import io
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.asr.test_transcription import wav_bytes
from unimem_asr.engine import transcribe
from unimem_asr.policy import AsrError


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Deterministic adapter contract tests; these are explicitly NOT real ASR."""

    class Array:
        def reshape(self, n: int) -> "Array":
            return self

        def astype(self, dtype: object) -> "Array":
            return self

        def __truediv__(self, other: float) -> "Array":
            return self

    frame = SimpleNamespace(pts=0, samples=16000, to_ndarray=Array)
    codec = SimpleNamespace(name="pcm_s16le", sample_rate=16000, channels=1, thread_count=0)
    stream = SimpleNamespace(codec_context=codec)

    class Streams(list[Any]):
        @property
        def audio(self) -> list[Any]:
            return [stream]

    container = SimpleNamespace(
        streams=Streams([stream]), duration=1_000_000, decode=lambda _: iter([frame])
    )
    state = SimpleNamespace(
        container=container,
        codec=codec,
        frame=frame,
        speech=True,
        cues=[SimpleNamespace(text="Actual model output", start=0.0, end=0.9)],
        loads=[],
        calls=[],
        decode_error=False,
        model_error=False,
    )

    def open_audio(source: Any, **kwargs: Any) -> Any:
        assert hasattr(source, "read")
        assert kwargs["format"] == "wav"
        assert kwargs["options"]["protocol_whitelist"] == ""
        assert kwargs["options"]["err_detect"] == "explode"
        with pytest.raises(AsrError, match="decode_failed"):
            kwargs["io_open"]("file:/etc/passwd", 0, {})
        if state.decode_error:
            raise OSError("sensitive decoder text")
        return nullcontext(container)

    class Model:
        def __init__(self, directory: str, **kwargs: Any) -> None:
            state.loads.append((directory, kwargs))
            if state.model_error:
                raise RuntimeError("sensitive model path")

        def detect_language(self, **kwargs: Any) -> tuple[str, float, list[object]]:
            return "en", 0.8, []

        def transcribe(self, audio: object, **kwargs: Any) -> tuple[Any, None]:
            state.calls.append(kwargs)
            return iter(state.cues), None

    monkeypatch.setitem(
        sys.modules,
        "av",
        SimpleNamespace(
            open=open_audio,
            time_base=1_000_000,
            error=SimpleNamespace(FFmpegError=OSError),
            AudioResampler=lambda **_: SimpleNamespace(resample=lambda f: [] if f is None else [f]),
        ),
    )
    monkeypatch.setitem(
        sys.modules, "numpy", SimpleNamespace(concatenate=lambda _: Array(), float32=object())
    )
    monkeypatch.setitem(
        sys.modules, "onnxruntime", SimpleNamespace(disable_telemetry_events=lambda: None)
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.vad",
        SimpleNamespace(
            VadOptions=lambda **_: None,
            get_speech_timestamps=lambda *_: [1] if state.speech else [],
        ),
    )
    monkeypatch.setattr("unimem_asr.engine.require_engine", lambda: None)
    monkeypatch.setattr("unimem_asr.engine.verify_model", lambda _: {"model.bin": "a" * 64})
    monkeypatch.setattr("unimem_asr.engine.importlib.metadata.version", lambda _: "test-version")
    return state


def test_real_adapter_options_local_only_and_language_separation(
    native: SimpleNamespace, tmp_path: Path
) -> None:
    result = transcribe(io.BytesIO(wav_bytes()), "audio/wav", "ru", tmp_path)
    assert result.selected_language == "ru"
    assert result.detected_language == "en"
    assert native.calls[0]["language"] == "ru"
    assert native.loads[0] == (
        str(tmp_path),
        {
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 2,
            "num_workers": 1,
            "local_files_only": True,
        },
    )
    assert native.codec.thread_count == 1
    assert result.segments[0].text == "Actual model output"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("codec.name", "pcm_f32le", "unsupported_format"),
        ("codec.channels", 3, "unsupported_format"),
        ("codec.sample_rate", 96000, "unsupported_format"),
        ("container.duration", 121_000_000, "duration_limit"),
        ("frame.samples", 1_920_001, "duration_limit"),
        ("frame.samples", 0, "decode_failed"),
        ("decode_error", True, "decode_failed"),
        ("model_error", True, "model_unavailable"),
    ],
)
def test_decoder_and_model_failures(
    native: SimpleNamespace, tmp_path: Path, field: str, value: object, code: str
) -> None:
    names = field.split(".")
    owner = getattr(native, names[0]) if len(names) > 1 else native
    setattr(owner, names[-1], value)
    with pytest.raises(AsrError, match=code):
        transcribe(io.BytesIO(wav_bytes()), "", "auto", tmp_path)


def test_silence_is_mechanism_outcome_with_no_fake_language_or_cues(
    native: SimpleNamespace, tmp_path: Path
) -> None:
    native.speech = False
    result = transcribe(io.BytesIO(wav_bytes()), "", "auto", tmp_path)
    assert result.outcome == "no_speech"
    assert result.segments == []
    assert result.detected_language is None
    assert native.calls == []


def test_output_limit(
    native: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("unimem_asr.engine.MAX_TEXT_BYTES", 3)
    with pytest.raises(AsrError, match="output_limit"):
        transcribe(io.BytesIO(wav_bytes()), "", "auto", tmp_path)


@pytest.mark.parametrize("bad", [float("nan"), -1.0, 1.2])
def test_invalid_native_timeline(native: SimpleNamespace, tmp_path: Path, bad: float) -> None:
    native.cues[0].end = bad
    with pytest.raises(AsrError, match="invalid_result"):
        transcribe(io.BytesIO(wav_bytes()), "", "auto", tmp_path)
