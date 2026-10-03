"""Image operations and package delivery: deterministic, no OCR engine required."""

import hashlib
import io
import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.contracts import CaptureStatus
from core.processing.image_recognition import (
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
    ImageOcrResult,
)
from core.storage.raw import parse_raw_ref, raw_object_ref
from tests.api_auth import AUTH_HEADERS, TEST_SECURITY
from unimem_api.image_export import ImageMarkdownRenderer, image_attachment
from unimem_api.image_operations import ImageOperationStore, ImageRequest
from unimem_api.image_service import (
    ImageCaptureService,
    ImageError,
    inspect_image,
    ocr_status,
    validate_pixels,
)
from unimem_api.image_worker import execute_image
from unimem_api.obsidian_contract import (
    AckRequest,
    Attachment,
    ClaimRequest,
    DeliveryError,
    ImageDelivery,
    PackageAckRequest,
    package_digest,
)
from unimem_api.obsidian_store import ObsidianStore
from unimem_api.wiring import build_local_app


def png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    # Pixel-limit fixtures need only their header: no huge test allocation.
    pixels = b"\0" + b"\xff\xff\xff" * 4
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels * 3))
        + chunk(b"IEND", b"")
    )


class FakeOcr:
    def __init__(self, outcome: str) -> None:
        self.outcome, self.calls = outcome, 0

    def recognize_image(self, stream: Any, **kwargs: Any) -> ImageOcrResult:
        self.calls += 1
        if self.outcome == "error":
            raise ImageOcrExecutionError("private diagnostic")
        if self.outcome == "skipped":
            raise ImageOcrLimitExceeded("private", reason="encoded_pixel_limit", limit=1)
        return ImageOcrResult(text=self.outcome, engine="fake", engine_version="1", settings={})


def staged(
    tmp: Path, mode: Literal["original", "ocr"] = "original"
) -> tuple[ImageCaptureService, ImageOperationStore, ImageRequest]:
    service = ImageCaptureService(tmp)
    raw = service.raw_store.store_bytes(png(), mime_type="image/png")
    assert raw.ref is not None
    request = ImageRequest(
        operation_id=str(uuid4()), file_ref=raw.ref, mode=mode, captured_at=datetime.now(UTC)
    )
    return service, ImageOperationStore(tmp / "unimem.sqlite3"), request


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("", None),
        ("application/octet-stream", None),
        ("image/png", None),
        ("image/jpeg", "mime_mismatch"),
        ("text/plain", "unsupported_format"),
    ],
)
def test_signature_mime_and_input_budgets(declared: str, expected: str | None) -> None:
    if expected:
        with pytest.raises(ImageError, match=expected):
            inspect_image(io.BytesIO(png()), declared)
    else:
        data, mime, header = inspect_image(io.BytesIO(png()), declared)
        assert data == png()
        assert mime == "image/png"
        assert header.width == 4
    for data, code in [
        (b"", "input_size_limit"),
        (b"GIF89a", "unsupported_format"),
        (png()[:20], "invalid_image"),
        (png(10000, 10000), "pixel_limit"),
        (png() + b"x" * (16 * 1024 * 1024), "input_size_limit"),
    ]:
        with pytest.raises(ImageError, match=code):
            inspect_image(io.BytesIO(data), "")


def test_real_decode_valid_corrupt_animation_and_jpeg() -> None:
    image = pytest.importorskip("PIL.Image")
    data, _, header = inspect_image(io.BytesIO(png()), "")
    validate_pixels(data, header)
    with pytest.raises(ImageError, match="invalid_image"):
        validate_pixels(data[:45], header)
    for fmt in ["PNG", "JPEG"]:
        out = io.BytesIO()
        image.new("RGB", (10, 20), "white").save(out, format=fmt)
        data, _, header = inspect_image(io.BytesIO(out.getvalue()), "")
        validate_pixels(data, header)
    out = io.BytesIO()
    image.new("RGB", (10, 20), "white").save(
        out, format="PNG", save_all=True, append_images=[image.new("RGB", (10, 20), "black")]
    )
    data, _, header = inspect_image(io.BytesIO(out.getvalue()), "")
    with pytest.raises(ImageError, match="unsupported_format"):
        validate_pixels(data, header)


@pytest.mark.parametrize(
    ("mode", "outcome", "status"),
    [
        ("original", "error", "not_requested"),
        ("ocr", 'Русский text\n```\n![[injected]]\n<img src="https://evil">', "text"),
        ("ocr", " \n", "empty"),
        ("ocr", "skipped", "skipped"),
        ("ocr", "error", "error"),
    ],
)
def test_processing_truth_replay_and_original_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["original", "ocr"],
    outcome: str,
    status: str,
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    service, store, request = staged(tmp_path, mode)
    adapter = FakeOcr(outcome)
    op, created = store.register(request)
    assert created
    assert not store.register(request)[1]
    assert store.claim() is not None
    execute_image(tmp_path, request.operation_id, adapter)
    observed = store.get(request.operation_id)
    assert adapter.calls == (mode == "ocr")
    if status == "error":
        assert observed.state == "failed"
        assert observed.error_code == "ocr_error"
        assert service.record_store.get(op.reserved_capture_id).status is CaptureStatus.PROCESSING
    else:
        assert observed.state == "complete"
        content = service.read(op.reserved_capture_id)
        assert ocr_status(content) == status
        attachment, _ = image_attachment(content, service.raw_store)
        rendered = ImageMarkdownRenderer().render(
            content, service.record_store.get(op.reserved_capture_id), attachment
        )
        assert f"![Исходное изображение](./{attachment.relative_name})" in rendered
        if status == "text":
            assert "````text\n" in rendered
        assert store.register(request)[0] == observed
        execute_image(tmp_path, request.operation_id, adapter)
        assert adapter.calls == (mode == "ocr")
    with service.raw_store.open(raw_object_ref(parse_raw_ref(request.file_ref))) as stream:
        assert stream.read() == png()


def test_recovery_preserves_uncertainty_and_completed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    service, store, request = staged(tmp_path)
    store.register(request)
    op = store.claim()
    assert op
    content = service.capture(op, None)
    store.recover(service)
    assert store.get(request.operation_id).content_id == content.id
    second = request.model_copy(update={"operation_id": str(uuid4())})
    store.register(second)
    store.claim()
    store.recover(service)
    assert store.get(second.operation_id).state == "interrupted"
    assert store.claim() is None


def test_v2_isolation_immutable_snapshot_old_ack_and_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    _, operations, request = staged(tmp_path)
    operations.register(request)
    operations.claim()
    execute_image(tmp_path, request.operation_id)
    capture = operations.get(request.operation_id).capture_id
    app = build_local_app(tmp_path, security=TEST_SECURITY)
    store = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3")
    dest, token = store.create_destination("Test")
    other, other_token = store.create_destination("Other")
    client = TestClient(app, base_url="http://127.0.0.1:8765", headers=AUTH_HEADERS)
    headers = {"Authorization": f"Bearer {token}"}
    owner = ClaimRequest(receiver_id=str(uuid4()), claim_id=str(uuid4()))
    body = {"destination_id": dest.destination_id, "source_capture_id": capture}
    assert (
        client.post("/v2/deliveries", json=body).json()["error"]["code"]
        == "receiver_upgrade_required"
    )
    assert client.post("/v1/deliveries", json=body).status_code == 409
    assert (
        client.post(
            "/v2/receiver/capabilities",
            headers=headers,
            json={"receiver_id": owner.receiver_id, "enabled": True},
        ).status_code
        == 200
    )
    r = client.post("/v2/deliveries", json=body)
    assert r.status_code == 202
    d = store.get(dest.destination_id, r.json()["delivery_id"])
    assert isinstance(d, ImageDelivery)
    assert d.package_sha256 == package_digest(d)
    monkeypatch.setattr(ImageMarkdownRenderer, "render", lambda *_: "changed")
    assert client.post("/v2/deliveries", json=body).json() == r.json()
    assert store.get(dest.destination_id, d.delivery_id) == d
    path = f"/v2/receiver/deliveries/{d.delivery_id}"
    binary = path + "/assets/" + d.attachments[0].asset_id
    assert client.get(binary).status_code == 401
    assert client.get(binary, headers={"Authorization": f"Bearer {other_token}"}).status_code == 404
    assert client.get(path + "/assets/" + str(uuid4()), headers=headers).status_code == 404
    assert client.get(binary, headers=headers).content == png()
    assert client.get("/v1/receiver/deliveries/next", headers=headers).json() is None
    assert client.get(path.replace("/v2/", "/v1/"), headers=headers).status_code == 409
    assert client.post(path + "/claim", headers=headers, json=owner.model_dump()).status_code == 200
    old_ack = AckRequest(**owner.model_dump(), markdown_sha256=d.markdown_sha256)
    assert client.post(path + "/ack", headers=headers, json=old_ack.model_dump()).status_code == 422
    assert (
        client.post(
            path.replace("/v2/", "/v1/") + "/ack", headers=headers, json=old_ack.model_dump()
        ).status_code
        == 409
    )
    with pytest.raises(DeliveryError, match="protocol_mismatch"):
        store.finish(dest.destination_id, d.delivery_id, old_ack)
    ack = PackageAckRequest(**owner.model_dump(), package_sha256=d.package_sha256)
    assert (
        client.post(path + "/ack", headers=headers, json=ack.model_dump()).json()["state"]
        == "imported"
    )
    assert (
        client.post(path + "/ack", headers=headers, json=ack.model_dump()).json()["state"]
        == "imported"
    )
    text, _ = store.register(
        dest.destination_id, "text-capture", "text-content", "still v1", "markdown", "0.1"
    )
    assert (
        client.get("/v1/receiver/deliveries/next", headers=headers).json()["delivery_id"]
        == text.delivery_id
    )
    assert store.next(other.destination_id, "2") is None


def test_manifest_bounds_and_fixture() -> None:
    fixture = json.loads(Path("fixtures/obsidian-delivery-v2.json").read_text())
    d = ImageDelivery.model_validate(fixture["delivery"])
    assert package_digest(d) == d.package_sha256
    assert hashlib.sha256(d.markdown.encode()).hexdigest() == d.markdown_sha256
    assert hashlib.sha256(png()).hexdigest() == d.attachments[0].sha256
    changes_list: list[dict[str, Any]] = [
        {"relative_name": "../x.png"},
        {"mime_type": "image/svg+xml"},
        {"size_bytes": 16 * 1024 * 1024 + 1},
        {"size_bytes": True},
        {"relative_name": "unimem-" + "a" * 64 + ".jpg"},
    ]
    for changes in changes_list:
        with pytest.raises(ValidationError):
            Attachment.model_validate(d.attachments[0].model_dump() | changes)


def test_http_acceptance_replay_disabled_ocr_and_result_without_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    _, store, request = staged(tmp_path)
    client = TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY),
        base_url="http://127.0.0.1:8765",
        headers=AUTH_HEADERS,
    )
    body = request.model_dump(mode="json")
    path = "/v1/image/operations"
    assert client.post(path, json=body | {"mode": "ocr"}).status_code == 503
    assert client.post(path, json=body).status_code == 202
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body | {"mode": "ocr"}).status_code == 409
    result_path = path + "/" + request.operation_id
    assert client.get(result_path + "/result").status_code == 409
    store.claim()
    execute_image(tmp_path, request.operation_id)
    assert client.get(result_path).json()["state"] == "complete"
    monkeypatch.setattr(
        "unimem_api.image_service.validate_pixels", lambda *_: pytest.fail("read must not decode")
    )
    result = client.get(result_path + "/result").json()
    assert result["ocr_status"] == "not_requested"
    assert client.get(result_path + "/original").content == png()
    assert client.post(path, json=body).status_code == 200
    assert (
        client.get(
            result_path + "/original", headers={"Authorization": "Bearer " + "b" * 43}
        ).status_code
        == 401
    )


def test_decoder_missing_and_budget_failures_are_truthful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    data, _, header = inspect_image(io.BytesIO(png()), "")
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "PIL", None)
        with pytest.raises(ImageError, match="image_decoder_unavailable"):
            validate_pixels(data, header)
    service, store, request = staged(tmp_path)
    store.register(request)
    store.claim()

    def exhausted(*args: object) -> None:
        raise MemoryError

    monkeypatch.setattr("unimem_api.image_service.validate_pixels", exhausted)
    execute_image(tmp_path, request.operation_id)
    assert store.get(request.operation_id).error_code == "budget_exceeded"
    assert service.raw_store.exists(raw_object_ref(parse_raw_ref(request.file_ref)))


@pytest.mark.parametrize("stop", [False, True])
def test_real_worker_budget_or_unload_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: bool
) -> None:
    import sys
    import time

    from unimem_api.image_worker import ImageWorker
    from unimem_api.worker import ServerLease

    _, store, request = staged(tmp_path)
    store.register(request)
    monkeypatch.setattr("unimem_api.image_worker.IMAGE_SECONDS", 0.3)
    with ServerLease(tmp_path) as lease:
        worker = ImageWorker(
            tmp_path,
            store,
            lease.fd,
            ocr_enabled=False,
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
        )
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = store.get(request.operation_id).state
                if (stop and state == "running") or state in {"failed", "interrupted"}:
                    break
                time.sleep(0.02)
        finally:
            worker.stop()
    observed = store.get(request.operation_id)
    assert observed.state == ("interrupted" if stop else "failed")
    assert observed.error_code == ("execution_interrupted" if stop else "budget_exceeded")
