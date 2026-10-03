from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal

import pytest

from core.contracts import CaptureStatus
from tests.api_auth import TEST_SECURITY, AuthenticatedClient
from tests.unit.asr.test_transcription import transcript, wav_bytes
from unimem_api.audio_operations import AudioOperationStore, AudioRequest
from unimem_api.audio_worker import execute_audio
from unimem_api.obsidian_store import ObsidianStore
from unimem_api.wiring import build_local_app
from unimem_asr.policy import AsrError
from unimem_asr.result import Transcript
from unimem_asr.service import AudioCaptureService
from unimem_delivery.operations import OperationError


def audio_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AuthenticatedClient:
    monkeypatch.setattr("unimem_asr.model.require_engine", lambda: None)
    monkeypatch.setattr("unimem_asr.model.verify_model", lambda _: {})
    return AuthenticatedClient(
        build_local_app(tmp_path, security=TEST_SECURITY, audio_model=tmp_path)
    )


def request(file_ref: str, **changes: object) -> AudioRequest:
    return AudioRequest.model_validate(
        {
            "operation_id": "audio-one",
            "file_ref": file_ref,
            "captured_at": datetime.now(UTC),
            **changes,
        }
    )


def test_upload_acceptance_replay_processing_export_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = audio_app(tmp_path, monkeypatch)
    upload = client.post(
        "/v1/uploads", files={"file": ("../../voice.ogg", wav_bytes(), "audio/wav")}
    )
    assert upload.status_code == 200
    body = request(upload.json()["file_ref"]).model_dump(mode="json")
    created = client.post("/v1/audio/operations", json=body)
    assert created.status_code == 202
    assert created.json()["state"] == "queued"
    store = AudioOperationStore(tmp_path / "unimem.sqlite3")
    calls = 0

    def recognize(
        stream: BinaryIO, mime: str, language: Literal["auto", "ru", "en"], model: Path
    ) -> Transcript:
        nonlocal calls
        calls += 1
        assert stream.read() == wav_bytes()
        return transcript(selected_language=language)

    assert store.claim() is not None
    execute_audio(tmp_path, "audio-one", tmp_path, recognize)
    for _ in range(2):
        replay = client.post("/v1/audio/operations", json=body)
        assert replay.status_code == 200
        assert replay.json()["state"] == "complete"
        assert client.get("/v1/audio/operations/audio-one").json()["state"] == "complete"
        execute_audio(tmp_path, "audio-one", tmp_path, recognize)
    assert calls == 1
    conflict = client.post("/v1/audio/operations", json=body | {"language": "ru"})
    assert conflict.status_code == 409
    markdown = client.get("/v1/audio/operations/audio-one/markdown")
    assert markdown.status_code == 200
    delivery_store = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3")
    destination, _ = delivery_store.create_destination("Audio test")
    capture_id = replay.json()["capture_id"]
    delivery_body = {"source_capture_id": capture_id, "destination_id": destination.destination_id}
    sent = client.post("/v1/deliveries", json=delivery_body)
    assert sent.status_code == 202
    snapshot = delivery_store.get(destination.destination_id, sent.json()["delivery_id"])
    assert snapshot.markdown.encode() == markdown.content
    assert client.post("/v1/deliveries", json=delivery_body).status_code == 200
    assert calls == 1
    # A base server reopens and renders existing results without a model/decoder.
    base = AuthenticatedClient(build_local_app(tmp_path, security=TEST_SECURITY))
    assert base.get("/v1/audio/operations/audio-one/markdown").content == markdown.content
    assert base.post("/v1/audio/operations", json=body).json()["state"] == "complete"
    refused = base.post("/v1/audio/operations", json=body | {"operation_id": "new"})
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "asr_disabled"


def test_acceptance_requires_uploaded_matching_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = audio_app(tmp_path, monkeypatch)
    body = request("sha256:" + "f" * 64).model_dump(mode="json")
    assert client.post("/v1/audio/operations", json=body).status_code != 202
    assert client.get("/v1/audio/operations/audio-one").status_code == 404
    for source in ("https://example.test/a.wav", "/etc/passwd", "file:///tmp/a.wav"):
        assert (
            client.post("/v1/audio/operations", json=body | {"file_ref": source}).status_code == 422
        )
    raw = AudioCaptureService(tmp_path).raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    mismatch = request(raw.ref, declared_mime="audio/ogg").model_dump(mode="json")
    assert (
        client.post("/v1/audio/operations", json=mismatch).json()["error"]["code"]
        == "mime_mismatch"
    )
    assert client.get("/v1/audio/operations/audio-one").status_code == 404


@pytest.mark.parametrize(
    "error", ["decode_failed", "model_unavailable", "budget_exceeded", "invalid_result"]
)
def test_known_worker_failures_preserve_original(tmp_path: Path, error: str) -> None:
    service = AudioCaptureService(tmp_path)
    raw = service.raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    store = AudioOperationStore(tmp_path / "unimem.sqlite3")
    store.register(request(raw.ref))
    assert store.claim() is not None

    def fail(*args: object) -> Transcript:
        raise AsrError(error)

    execute_audio(tmp_path, "audio-one", tmp_path, fail)
    assert store.get("audio-one").state == "failed"
    assert store.get("audio-one").error_code == error
    assert service.raw_store.read_bytes(raw) == wav_bytes()
    assert store.claim() is None


def test_restart_never_reprocesses_uncertain_capture(tmp_path: Path) -> None:
    service = AudioCaptureService(tmp_path)
    raw = service.raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    store = AudioOperationStore(tmp_path / "unimem.sqlite3", capacity=1)
    store.register(request(raw.ref))
    with pytest.raises(OperationError, match="queue_full"):
        store.register(request(raw.ref, operation_id="second"))
    assert store.claim() is not None
    assert store.claim() is None
    store.recover(service)
    assert store.get("audio-one").state == "interrupted"
    assert store.claim() is None


def test_crash_after_content_commit_recovers_without_model(tmp_path: Path) -> None:
    service = AudioCaptureService(tmp_path)
    raw = service.raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    store = AudioOperationStore(tmp_path / "unimem.sqlite3")
    store.register(request(raw.ref))
    op = store.claim()
    assert op is not None
    content = service.capture(
        capture_id=op.reserved_capture_id,
        operation_id="audio-one",
        file_ref=raw.ref,
        mime_type="audio/wav",
        captured_at=datetime.now(UTC),
        recognize=lambda _: transcript(),
    )
    assert service.record_store.get(content.source.capture_id).status is CaptureStatus.COMPLETE
    store.recover(service)
    assert store.get("audio-one").content_id == content.id
    assert store.get("audio-one").state == "complete"


def test_default_api_does_not_enable_audio(tmp_path: Path) -> None:
    client = AuthenticatedClient(build_local_app(tmp_path, security=TEST_SECURITY))
    assert client.get("/v1/audio/operations/test").status_code == 404


def test_large_audio_result_exports_but_delivery_refuses_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unimem_asr.result import Cue

    client = audio_app(tmp_path, monkeypatch)
    service = AudioCaptureService(tmp_path)
    raw = service.raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    store = AudioOperationStore(tmp_path / "unimem.sqlite3")
    store.register(request(raw.ref))
    assert store.claim()
    assert client.get("/v1/audio/operations/audio-one/markdown").status_code == 409
    result = transcript(segments=[Cue(text="x" * 100_000, start=0.0, end=0.0) for _ in range(12)])
    execute_audio(tmp_path, "audio-one", tmp_path, lambda *_: result)
    op = store.get("audio-one")
    markdown = client.get("/v1/audio/operations/audio-one/markdown")
    assert markdown.status_code == 200
    assert len(markdown.content) > 1024**2
    destination, _ = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3").create_destination(
        "Large"
    )
    response = client.post(
        "/v1/deliveries",
        json={"source_capture_id": op.capture_id, "destination_id": destination.destination_id},
    )
    assert response.json()["error"]["code"] == "markdown_too_large"
    assert client.get("/v1/audio/operations/audio-one/markdown").content == markdown.content
    assert service.raw_store.read_bytes(raw) == wav_bytes()


@pytest.mark.parametrize("kind", [MemoryError, ValueError, RuntimeError])
def test_unexpected_failure_and_memory_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: type[Exception]
) -> None:
    from tests.unit.asr.test_worker import pending

    store = pending(tmp_path)
    assert store.claim()

    def fail(*args: object) -> Transcript:
        raise kind("sensitive runtime diagnostics")

    monkeypatch.setattr("unimem_asr.engine.transcribe", fail)
    execute_audio(tmp_path, "audio-one", tmp_path)
    op = store.get("audio-one")
    assert op.state == ("interrupted" if kind is RuntimeError else "failed")
    assert (
        op.error_code
        == {
            MemoryError: "budget_exceeded",
            ValueError: "invalid_result",
            RuntimeError: "execution_unavailable",
        }[kind]
    )
