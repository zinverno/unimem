import io
import json
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.contracts import CaptureStatus, ProvenanceSourceType
from unimem_asr.export import AudioMarkdownRenderer
from unimem_asr.input import inspect_input
from unimem_asr.model import verify_model
from unimem_asr.policy import AsrError
from unimem_asr.result import Cue, Transcript
from unimem_asr.service import AudioCaptureService


def wav_bytes(seconds: int = 1) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(bytes(32000 * seconds))
    return stream.getvalue()


def transcript(**changes: Any) -> Transcript:
    fields: dict[str, Any] = {
        "container": "wav",
        "codec": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "duration": 1.0,
        "selected_language": "ru",
        "detected_language": "en",
        "language_probability": 0.6,
        "outcome": "transcribed",
        "segments": [Cue(text="Recognized test", start=0.0, end=0.9)],
        "model_sha256": {"model.bin": "a" * 64},
        "versions": {"faster-whisper": "1.2.1"},
        "elapsed_seconds": 0.5,
        "peak_rss_bytes": 1000,
    }
    return Transcript.model_validate(fields | changes)


@pytest.mark.parametrize("mime", ["", "application/octet-stream", "audio/wav", "audio/x-wav"])
def test_content_detection_and_declared_aliases(mime: str) -> None:
    data = wav_bytes()
    assert inspect_input(io.BytesIO(data), mime) == ("wav", "audio/wav", len(data))


@pytest.mark.parametrize(
    ("data", "mime", "error"),
    [
        (b"", "", "input_size_limit"),
        (b"#EXTM3U\nhttps://example.test/private", "audio/mpeg", "unsupported_format"),
        (wav_bytes(), "audio/ogg", "mime_mismatch"),
        (wav_bytes(), "audio/anything", "mime_mismatch"),
    ],
)
def test_refuse_input(data: bytes, mime: str, error: str) -> None:
    with pytest.raises(AsrError, match=error):
        inspect_input(io.BytesIO(data), mime)


def test_size_checked_before_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unimem_asr.input.MAX_BYTES", 10)
    with pytest.raises(AsrError, match="input_size_limit"):
        inspect_input(io.BytesIO(wav_bytes()), "")


@pytest.mark.parametrize(
    "changes",
    [
        {"duration": float("inf")},
        {"duration": 121.0},
        {"duration": 0.0},
        {"segments": [{"text": 3, "start": 0.0, "end": 1.0}]},
        {"segments": [{"text": " ", "start": 0.0, "end": 1.0}]},
        {"segments": [{"text": "test", "start": -1.0, "end": 0.1}]},
        {"segments": [{"text": "test", "start": 0.5, "end": 0.1}]},
        {"segments": [{"text": "test", "start": 0.0, "end": float("nan")}]},
        {"segments": [{"text": "test", "start": 0.0, "end": 1.2}]},
        {
            "segments": [
                {"text": "first", "start": 0.5, "end": 0.9},
                {"text": "second", "start": 0.1, "end": 0.2},
            ]
        },
        {"segments": []},
        {"outcome": "no_speech"},
    ],
)
def test_invalid_engine_observations_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        transcript(**changes)


def test_text_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unimem_asr.result.MAX_TEXT_BYTES", 3)
    with pytest.raises(ValueError, match="budget"):
        transcript()


@pytest.mark.parametrize("no_speech", [False, True])
def test_original_first_persistence_and_offline_render(tmp_path: Path, no_speech: bool) -> None:
    service = AudioCaptureService(tmp_path)
    data = wav_bytes()
    raw = service.raw_store.store_bytes(data)
    assert raw.ref is not None
    calls = 0

    def recognize(stream: Any) -> Transcript:
        nonlocal calls
        calls += 1
        assert stream.read() == data
        return transcript(outcome="no_speech", segments=[]) if no_speech else transcript()

    content = service.capture(
        capture_id="audio-capture",
        operation_id="audio-operation",
        file_ref=raw.ref,
        mime_type="audio/wav",
        captured_at=datetime.now(UTC),
        recognize=recognize,
    )
    assert service.record_store.get("audio-capture").status is CaptureStatus.COMPLETE
    assert content.original.sha256 == raw.sha256
    assert service.raw_store.read_bytes(raw) == data
    for segment in content.segments:
        assert segment.provenance.source_type is ProvenanceSourceType.TRANSCRIPT
        assert segment.provenance.asset_id == content.original.asset_id
    first = AudioMarkdownRenderer().render(content)
    assert (
        AudioMarkdownRenderer().render(AudioCaptureService(tmp_path).read("audio-capture")) == first
    )
    assert "![[" not in first
    assert str(tmp_path) not in first
    assert "Автоматическая расшифровка; возможны ошибки" in first
    assert ("не доказывает" in first) is no_speech
    assert calls == 1
    # Re-read actual persisted content in a fresh process with native imports and
    # Python networking forbidden, including the ordinary CLI renderer.
    script = """
import importlib.abc, sys, socket
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'faster_whisper','av','numpy','onnxruntime','huggingface_hub'}
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Absent())
def forbidden(*a, **k): raise AssertionError('Network is forbidden')
socket.socket.connect = forbidden
from unimem_asr.__main__ import main
sys.argv = ['render', 'render', '--data-dir', sys.argv[1], '--capture-id', 'audio-capture',
            '--output', sys.argv[2]]
main()
"""
    output = tmp_path / "offline.md"
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(output)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.read_text() == first


def test_model_absent_and_tampered_are_local_failures(tmp_path: Path) -> None:
    with pytest.raises(AsrError, match="model_unavailable"):
        verify_model(tmp_path)
    (tmp_path / "unimem-model.json").write_text(json.dumps({"model": "wrong"}))
    with pytest.raises(AsrError, match="model_unavailable"):
        verify_model(tmp_path)


def test_import_base_and_render_need_no_asr_or_network(tmp_path: Path) -> None:
    script = """
import importlib.abc, sys, socket
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'faster_whisper','av','numpy','onnxruntime','huggingface_hub'}
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Absent())
def forbidden(*a, **k): raise AssertionError('Network is forbidden')
socket.socket.connect = forbidden
from unimem_api.wiring import build_local_app
from unimem_asr.export import AudioMarkdownRenderer
from unimem_asr.model import verify_model
from unimem_asr.service import AudioCaptureService
print('optional boundary passed')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "optional boundary passed"
