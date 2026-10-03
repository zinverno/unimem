"""Real CPU ASR over a controlled stream. Called only inside the bounded worker."""

import importlib.metadata
import itertools
import resource
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal

from unimem_asr.input import inspect_input
from unimem_asr.model import require_engine, verify_model
from unimem_asr.policy import (
    CPU_THREADS,
    MAX_SAMPLES,
    MAX_SECONDS,
    MAX_SEGMENTS,
    MAX_TEXT_BYTES,
    AsrError,
)
from unimem_asr.result import Cue, Transcript


def deny_external(*args: object, **kwargs: object) -> None:
    raise AsrError("decode_failed")


def transcribe(
    stream: BinaryIO,
    declared: str,
    language: Literal["auto", "ru", "en"],
    model_dir: Path,
) -> Transcript:
    started = monotonic()
    require_engine()
    hashes = verify_model(model_dir)
    import av
    import numpy as np
    import onnxruntime
    from faster_whisper import WhisperModel
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    container_name, _, _ = inspect_input(stream, declared)
    try:
        # Forced demuxer excludes playlists. Custom IO refuses every nested open;
        # no URL/path from the caller ever reaches FFmpeg.
        with av.open(
            stream,
            mode="r",
            format=container_name,
            io_open=deny_external,
            options={"protocol_whitelist": "", "err_detect": "explode"},
        ) as container:
            if container.duration is not None and container.duration / av.time_base > MAX_SECONDS:
                raise AsrError("duration_limit")
            if len(container.streams) != 1 or len(container.streams.audio) != 1:
                raise AsrError("unsupported_format")
            audio_stream = container.streams.audio[0]
            codec = audio_stream.codec_context.name
            rate = audio_stream.codec_context.sample_rate
            channels = audio_stream.codec_context.channels
            codecs = {
                "wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8"},
                "mp3": {"mp3float", "mp3"},
                "ogg": {"opus"},
            }
            if (
                codec not in codecs[container_name]
                or not 8000 <= rate <= 48000
                or channels not in (1, 2)
            ):
                raise AsrError("unsupported_format")
            audio_stream.codec_context.thread_count = 1
            # Authoritative duration is the fully decoded, resampled sample count.
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            chunks = []
            samples = 0
            for frame in itertools.chain(container.decode(audio_stream), [None]):
                if frame is not None:
                    frame.pts = None
                for output in resampler.resample(frame):
                    samples += output.samples
                    if samples > MAX_SAMPLES:
                        raise AsrError("duration_limit")
                    chunks.append(output.to_ndarray().reshape(-1))
            if not samples:
                raise AsrError("decode_failed")
            audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    except (av.error.FFmpegError, OSError, ValueError):
        raise AsrError("decode_failed") from None
    try:
        model = WhisperModel(
            str(model_dir),
            device="cpu",
            compute_type="int8",
            cpu_threads=CPU_THREADS,
            num_workers=1,
            local_files_only=True,
        )
    except (RuntimeError, ValueError, OSError):
        raise AsrError("model_unavailable") from None
    onnxruntime.disable_telemetry_events()
    cues: list[Cue] = []
    detected = probability = None
    # Explicit VAD outcome; an empty result makes no claim of proven absence of speech.
    if get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500)):
        detected, probability, _ = model.detect_language(audio=audio)
        segments, _ = model.transcribe(
            audio,
            language=detected if language == "auto" else language,
            task="transcribe",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        size = 0
        for segment in segments:
            if not isinstance(segment.text, str):
                raise AsrError("invalid_result")
            if not segment.text.strip():
                continue
            size += len(segment.text.encode("utf-8"))
            if len(cues) >= MAX_SEGMENTS or size > MAX_TEXT_BYTES:
                raise AsrError("output_limit")
            cues.append(Cue(text=segment.text.strip(), start=segment.start, end=segment.end))
    try:
        return Transcript(
            container=container_name,
            codec=codec,
            sample_rate=rate,
            channels=channels,
            duration=samples / 16000,
            selected_language=language,
            detected_language=detected,
            language_probability=probability,
            outcome="transcribed" if cues else "no_speech",
            segments=cues,
            model_sha256=hashes,
            versions={
                p: importlib.metadata.version(p)
                for p in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "numpy")
            },
            elapsed_seconds=monotonic() - started,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        )
    except ValueError:
        raise AsrError("invalid_result") from None
