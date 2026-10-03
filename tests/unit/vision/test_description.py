"""Deterministic vision contracts. These do not claim model quality or native acceptance."""

import hashlib
import io
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import unimem_vision.engine as engine
import unimem_vision.profile as profile
from core.contracts import CaptureStatus, ProvenanceSourceType, SegmentType
from tests.api_auth import AUTH_HEADERS, TEST_SECURITY
from tests.unit.api.test_image_delivery import staged
from unimem_api.image_export import image_attachment, image_renderer
from unimem_api.image_operations import ImageOperationStore
from unimem_api.image_worker import execute_image
from unimem_api.obsidian_contract import ImageDelivery
from unimem_api.obsidian_store import ObsidianStore
from unimem_api.wiring import build_local_app
from unimem_delivery.heavy import heavy_slot
from unimem_delivery.operations import OperationError
from unimem_vision.engine import Answer, Description, command, parse_answer, prepare_input
from unimem_vision.policy import OUTPUT_TOKENS, VisionError

DIAGNOSTICS = "eval time = 1250.00 ms / 70 runs\n"
TEXT = 'Белый прямоугольник.\n```\n![[other]] <img src="https://evil">'


def result(text: str = TEXT, status: str = "described") -> Description:
    answer = Answer.model_validate({"status": status, "description": text})
    return Description(
        answer=answer,
        raw_response=answer.model_dump_json(),
        preprocessing={
            "sha256": "a" * 64,
            "width": 64,
            "height": 64,
        },
        metrics={"completion": "ended_before_token_limit"},
    )


def test_generated_provenance_durable_replay_offline_read_and_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    service, store, old_request = staged(tmp_path)
    request = old_request.model_copy(update={"mode": "describe"})
    op, created = store.register(request)
    assert created
    assert store.claim() is not None
    calls = []

    def recognize(stream: Any) -> Description:
        calls.append(stream.read())
        return result()

    execute_image(tmp_path, request.operation_id, describe=recognize)
    execute_image(tmp_path, request.operation_id, describe=recognize)
    completed = store.get(request.operation_id)
    assert completed.state == "complete"
    assert len(calls) == 1
    content = service.read(op.reserved_capture_id)
    assert len(content.segments) == 1
    assert content.segments[0].type is SegmentType.VISUAL
    assert content.segments[0].provenance.source_type is ProvenanceSourceType.VISION
    assert content.original is not None
    assert content.segments[0].provenance.asset_id == content.original.asset_id
    assert content.original.sha256 == hashlib.sha256(calls[0]).hexdigest()
    attachment, _ = image_attachment(content, service.raw_store)
    renderer = image_renderer(content)
    assert renderer.version == "1.1"
    markdown = renderer.render(
        content, service.record_store.get(op.reserved_capture_id), attachment
    )
    assert TEXT in markdown
    assert "````text\n" + TEXT + "\n````" in markdown
    assert "## OCR-текст" not in markdown
    assert "Отдельное OCR не выполнялось" in markdown
    assert not store.register(request)[1]
    with pytest.raises(OperationError, match="operation_conflict"):
        store.register(old_request)
    # Model/profile not configured. New API must still read/render/send persisted content.
    client = TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY),
        headers=AUTH_HEADERS,
        base_url="http://127.0.0.1:8765",
    )
    saved = client.get(f"/v1/image/operations/{request.operation_id}/result").json()
    assert saved["markdown"] == markdown
    assert saved["ocr_status"] == "not_requested"
    assert saved["description"]["answer"]["description"] == TEXT
    original = client.get(f"/v1/image/operations/{request.operation_id}/original")
    assert original.content == calls[0]
    assert len(calls) == 1
    delivery_store = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3")
    destination, token = delivery_store.create_destination("Vision test")
    receiver = {"Authorization": f"Bearer {token}"}
    from uuid import uuid4

    client.post(
        "/v2/receiver/capabilities",
        headers=receiver,
        json={"receiver_id": str(uuid4()), "enabled": True},
    )
    body = {
        "destination_id": destination.destination_id,
        "source_capture_id": content.source.capture_id,
    }
    response = client.post("/v2/deliveries", json=body)
    assert response.status_code == 202
    delivered = delivery_store.get(destination.destination_id, response.json()["delivery_id"])
    assert isinstance(delivered, ImageDelivery)
    assert delivered.markdown == markdown
    assert delivered.export_version == "1.1"
    assert len(delivered.attachments) == 1
    assert delivered.attachments[0].sha256 == content.original.sha256
    monkeypatch.setattr(
        "unimem_api.obsidian_v2_http.image_renderer", lambda *_: pytest.fail("rerender")
    )
    assert client.post("/v2/deliveries", json=body).json() == response.json()
    assert len(calls) == 1
    # Recovery consults the canonical result; it never invokes recognition.
    ImageOperationStore(tmp_path / "unimem.sqlite3").recover(service)
    assert store.get(request.operation_id).content_id == content.id


@pytest.mark.parametrize(
    "code",
    [
        "vision_refused",
        "vision_invalid_output",
        "vision_output_limit",
        "vision_budget_exceeded",
        "vision_profile_invalid",
        "vision_execution_failed",
    ],
)
def test_failure_preserves_original_without_fake_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    monkeypatch.setattr("unimem_api.image_service.validate_pixels", lambda *_: None)
    service, store, request = staged(tmp_path)
    op, _ = store.register(request.model_copy(update={"mode": "describe"}))
    store.claim()

    def fail(_: Any) -> Description:
        raise VisionError(code)

    execute_image(tmp_path, request.operation_id, describe=fail)
    observed = store.get(request.operation_id)
    assert observed.state == "failed"
    assert observed.error_code == code
    capture = service.record_store.get(op.reserved_capture_id)
    assert capture.status is CaptureStatus.PROCESSING
    assert capture.raw_object is not None
    with service.raw_store.open(capture.raw_object) as stream:
        assert stream.read()
    assert observed.content_id is None


def test_missing_capability_replay_and_interrupted(tmp_path: Path) -> None:
    service, store, request = staged(tmp_path)
    describe_request = request.model_copy(update={"mode": "describe"})
    client = TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY),
        headers=AUTH_HEADERS,
        base_url="http://127.0.0.1:8765",
    )
    capabilities = client.get("/v1/image/capabilities").json()
    assert capabilities["description"]["ready"] is False
    assert (
        client.post(
            "/v1/image/operations", json=describe_request.model_dump(mode="json")
        ).status_code
        == 422
    )
    op, _ = store.register(describe_request)
    assert (
        client.post(
            "/v1/image/operations", json=describe_request.model_dump(mode="json")
        ).status_code
        == 200
    )
    store.claim()
    store.recover(service)
    assert store.get(op.request.operation_id).state == "interrupted"
    assert store.claim() is None


@pytest.mark.parametrize(
    ("payload", "diagnostics", "code"),
    [
        (b"", DIAGNOSTICS, "vision_invalid_output"),
        (b"\xff", DIAGNOSTICS, "vision_invalid_output"),
        (b"PROMPT\n{}", DIAGNOSTICS, "vision_invalid_output"),
        (b'{"status":"described","description":" "}', DIAGNOSTICS, "vision_invalid_output"),
        (b'{"status":"refused","description":"No"}', DIAGNOSTICS, "vision_refused"),
        (
            b'{"status":"described","description":"ok","command":"x"}',
            DIAGNOSTICS,
            "vision_invalid_output",
        ),
        (b'{"status":"described","description":"ok"}', "", "vision_completion_unverified"),
        (
            b'{"status":"described","description":"ok"}',
            f"eval time = 20 ms / {OUTPUT_TOKENS} runs",
            "vision_output_limit",
        ),
        (b"x" * 20000, DIAGNOSTICS, "vision_invalid_output"),
    ],
)
def test_output_is_not_blind_stdout(payload: bytes, diagnostics: str, code: str) -> None:
    with pytest.raises(VisionError, match=code):
        parse_answer(payload, diagnostics)


def test_unclear_is_honest_result_and_exact_text() -> None:
    raw = result("  Детали неразличимы.\n", "unclear").raw_response
    answer, saved, timing = parse_answer(
        raw.encode(), "prompt eval time = 100 ms / 80 tokens\n" + DIAGNOSTICS
    )
    assert answer.description == "  Детали неразличимы.\n"
    assert saved == raw
    assert timing["decoded_tokens"] == 70


def test_preprocessing_orientation_metadata_alpha_and_original(tmp_path: Path) -> None:
    image = pytest.importorskip("PIL.Image")
    source = image.new("RGB", (1600, 800), "red")
    exif = image.Exif()
    exif[274] = 6
    exif[270] = "SECRET expected answer, do not forward"
    stream = io.BytesIO()
    source.save(stream, format="JPEG", exif=exif)
    original = stream.getvalue()
    target = tmp_path / "input.png"
    facts = prepare_input(io.BytesIO(original), target)
    assert facts["orientation_applied"] is True
    assert facts["oriented_width"] == 800
    assert facts["oriented_height"] == 1600
    assert facts["width"] * facts["height"] <= 262144  # type: ignore[operator]
    with image.open(target) as prepared:
        assert prepared.height > prepared.width
        assert not prepared.getexif()
        assert not prepared.info
    assert stream.getvalue() == original
    assert b"SECRET" not in target.read_bytes()
    alpha = io.BytesIO()
    image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(alpha, format="PNG")
    prepare_input(io.BytesIO(alpha.getvalue()), target)
    with image.open(target) as white:
        assert white.getpixel((0, 0)) == (255, 255, 255)
    narrow = io.BytesIO()
    image.new("RGB", (2000, 20)).save(narrow, format="PNG")
    with pytest.raises(VisionError, match="vision_input_too_narrow"):
        prepare_input(io.BytesIO(narrow.getvalue()), target)


def test_fixed_command_has_no_source_metadata_network_or_user_paths(tmp_path: Path) -> None:
    args = command(tmp_path, tmp_path / "derived.png")
    assert args[:3] == ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent"]
    assert "--offline" in args
    assert "--clearenv" in args
    assert args[args.index("--image") + 1] == "/input.png"
    assert args[args.index("-t") + 1] == "2"
    assert args[args.index("-c") + 1] == "2048"
    assert "--server" not in args
    assert not any("vault" in value for value in args if value.startswith("/"))


def test_local_file_verification_and_no_hidden_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile, "probe_sandbox", lambda _: None)
    monkeypatch.setattr(profile, "import_module", lambda _: None)
    monkeypatch.setattr("unimem_vision.profile.platform.system", lambda: "Linux")
    monkeypatch.setattr("unimem_vision.profile.platform.machine", lambda: "x86_64")
    real_is_file, real_read_text = Path.is_file, Path.read_text
    monkeypatch.setattr(
        Path, "is_file", lambda p: True if str(p) == "/usr/bin/bwrap" else real_is_file(p)
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **k: (
            "flags: avx2 fma f16c" if str(p) == "/proc/cpuinfo" else real_read_text(p, *a, **k)
        ),
    )
    monkeypatch.setattr(
        profile,
        "COMPONENTS",
        {"weights": {"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}},
    )
    assert profile.readiness(tmp_path)["code"] == "vision_profile_missing"
    (tmp_path / "weights").write_bytes(b"bad")
    assert profile.readiness(tmp_path)["code"] == "vision_profile_invalid"
    (tmp_path / "weights").write_bytes(b"abc")
    assert profile.readiness(tmp_path)["ready"] is True
    (tmp_path / "weights").unlink()
    (tmp_path / "weights").symlink_to("/etc/passwd")
    assert profile.readiness(tmp_path)["code"] == "vision_profile_invalid"
    assert profile.readiness(None)["code"] == "vision_disabled"


def test_base_import_does_not_load_optional_engines() -> None:
    script = (
        "import unimem_api.wiring, sys; assert not any(n in sys.modules for n in "
        "('unimem_vision.engine', 'faster_whisper', 'llama_cpp'))"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_heavy_slot_stop_and_reuse(tmp_path: Path) -> None:
    stopping = threading.Event()
    results: list[int | None] = []
    with heavy_slot(tmp_path, stopping) as first:
        assert first is not None

        def blocked() -> None:
            with heavy_slot(tmp_path, stopping) as second:
                results.append(second)

        waiter = threading.Thread(target=blocked)
        waiter.start()
        assert not results
        stopping.set()
        waiter.join(timeout=2)
        assert results == [None]
    with heavy_slot(tmp_path, threading.Event()) as acquired:
        assert acquired is not None
    with heavy_slot(tmp_path, threading.Event(), required=False) as bypass:
        assert bypass == -1
    # A body failure must not be mistaken for lock contention or swallowed.
    with pytest.raises(BlockingIOError), heavy_slot(tmp_path, threading.Event()):
        raise BlockingIOError("body failed")


def test_actual_child_timeout_is_reaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "verify_profile", lambda _: None)
    monkeypatch.setattr(engine, "prepare_input", lambda *_: {})
    monkeypatch.setattr(engine, "SECONDS", 0.2)
    pidfile = tmp_path / "pid"
    script = (
        f"import os,time,pathlib; pathlib.Path({str(pidfile)!r})"
        ".write_text(str(os.getpid())); time.sleep(60)"
    )
    monkeypatch.setattr(engine, "command", lambda *_: [sys.executable, "-c", script])
    with pytest.raises(VisionError, match="vision_budget_exceeded"):
        engine.describe(io.BytesIO(), tmp_path)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_successful_child_separates_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "verify_profile", lambda _: None)
    monkeypatch.setattr(engine, "prepare_input", lambda *_: {})
    raw = result("Видимый прямоугольник.").raw_response
    diagnostic = "main: loading model:\nmtmd batch encoding done in 10 ms\n" + DIAGNOSTICS
    script = (
        f"import sys,time; sys.stderr.write({diagnostic!r}); "
        f"sys.stderr.flush(); time.sleep(.2); print({raw!r})"
    )
    monkeypatch.setattr(engine, "command", lambda *_: [sys.executable, "-c", script])
    description = engine.describe(io.BytesIO(), tmp_path)
    assert description.raw_response == raw
    assert "main:" not in description.answer.description
    assert description.metrics["vision_encoding_seconds"] == 0.01


def test_heavy_slot_survives_parent_descriptor_close(tmp_path: Path) -> None:
    import fcntl

    # The child retains the same open-file description through pass_fds, as both
    # workers do. A restarted API cannot overlap it after its parent exits.
    with heavy_slot(tmp_path, threading.Event()) as slot:
        assert slot is not None
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"], pass_fds=(slot,)
        )
    try:
        with (tmp_path / "heavy-worker.lock").open("a+b") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            child.terminate()
            child.wait(timeout=5)
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
