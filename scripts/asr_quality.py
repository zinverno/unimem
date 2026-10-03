"""Bounded, offline, two-model evaluation. See docs/asr-quality/PROTOCOL.md.

Run with the project's Python and optional ASR extra. This is research tooling;
it observes the production engine without adding small to its production allowlist.
"""

import argparse
import hashlib
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/asr-quality"


def words(text):
    return re.findall(r"[^\W_]+", text.lower().replace("\u0451", "\u0435"))


def score(reference, hypothesis):
    """Minimum word edit distance; deterministic S, D, I tie order."""
    ref, hyp = words(reference), words(hypothesis)
    table = [[(0, 0, j) for j in range(len(hyp) + 1)]]
    for i, expected in enumerate(ref, 1):
        row = [(0, i, 0)]
        for j, actual in enumerate(hyp, 1):
            s, d, ins = table[i - 1][j - 1]
            if expected == actual:
                row.append((s, d, ins))
            else:
                ps, pd, pi = table[i - 1][j]
                qs, qd, qi = row[j - 1]
                row.append(min([(s + 1, d, ins), (ps, pd + 1, pi), (qs, qd, qi + 1)], key=sum))
        table.append(row)
    s, d, ins = table[-1][-1]
    return {
        "N": len(ref),
        "S": s,
        "D": d,
        "I": ins,
        "WER": (s + d + ins) / len(ref) if ref else None,
    }


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(args):
    """Persist actual base outputs, then prove reread/render needs no audio or model."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from unimem_asr.export import AudioMarkdownRenderer
    from unimem_asr.result import Transcript
    from unimem_asr.service import AudioCaptureService

    assert not args.output.exists(), "preserve prior canonical evidence"
    service = AudioCaptureService(args.output)
    checks = []
    for path in sorted(args.results.glob("base-*.json")):
        saved = json.loads(path.read_text())
        if saved["language"] == "auto":
            continue
        transcript = Transcript.model_validate(saved["transcript"])
        source = args.audio / (saved["sample"] + ".wav")
        with source.open("rb") as stream:
            raw = service.raw_store.store_stream(stream)
        calls = []

        def recorded_result(stream, source=source, calls=calls, transcript=transcript):
            assert hashlib.sha256(stream.read()).hexdigest() == sha(source)
            calls.append(True)
            return transcript

        content = service.capture(
            capture_id=str(uuid4()),
            operation_id=str(uuid4()),
            file_ref=raw.ref,
            mime_type="audio/wav",
            captured_at=datetime.now(UTC),
            recognize=recorded_result,
        )
        assert calls == [True]
        assert [s.text for s in content.segments] == [
            s["text"].strip() for s in saved["raw_segments"] if s["text"].strip()
        ]
        assert [(s.temporal.start, s.temporal.end) for s in content.segments] == [
            (s.start, s.end) for s in transcript.segments
        ]
        renderer = AudioMarkdownRenderer()
        markdown = renderer.render(content)
        with (
            patch("unimem_asr.engine.transcribe", side_effect=AssertionError("ASR repeated")),
            patch("unimem_asr.model.verify_model", side_effect=AssertionError("model read")),
            patch.object(service.raw_store, "open", side_effect=AssertionError("audio read")),
        ):
            reread = service.read(content.source.capture_id)
            assert renderer.render(reread) == markdown
        checks.append(
            {
                "sample": saved["sample"],
                "original_sha256": raw.sha256,
                "raw_to_canonical_text_and_times_equal": True,
                "offline_reread_render_equal": True,
                "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            }
        )
    (args.output / "audit.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


def child(args):
    from unimem_asr import engine
    from unimem_asr.policy import EXECUTION_SECONDS, MEMORY_BYTES

    signal.alarm(EXECUTION_SECONDS)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (EXECUTION_SECONDS, EXECUTION_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    result = {
        "sample": args.sample,
        "candidate": args.candidate,
        "language": args.language,
        "raw_segments": [],
        "network_attempts": 0,
    }

    def no_network(event, _arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            result["network_attempts"] += 1
            raise RuntimeError("network forbidden during evaluation")

    sys.addaudithook(no_network)
    started = time.monotonic()
    phase = "imports"
    try:
        import av
        import faster_whisper
        import faster_whisper.vad as vad
        import numpy as np

        result["imports_seconds"] = time.monotonic() - started
        manifests = json.loads((EVIDENCE / "models.json").read_text())
        expected = manifests[args.candidate]
        path = args.audio / f"{args.sample}.wav"
        spec = next(
            s for s in json.loads((EVIDENCE / "samples.json").read_text()) if s["id"] == args.sample
        )
        assert sha(path) == spec["sha256"]
        with wave.open(str(path)) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            original = wav.readframes(wav.getnframes())

        def verify(directory):
            nonlocal phase
            phase = "verification"
            t = time.monotonic()
            manifest = json.loads((directory / "unimem-model.json").read_text())
            assert manifest == expected
            hashes = {name: sha(directory / name) for name in expected["sha256"]}
            assert hashes == expected["sha256"]
            result["verification_seconds"] = time.monotonic() - t
            return hashes

        original_open = av.open

        @contextmanager
        def observed_open(*a, **kw):
            nonlocal phase
            phase = "decode"
            t = time.monotonic()
            with original_open(*a, **kw) as container:
                yield container
            result["decode_seconds"] = time.monotonic() - t

        original_vad = vad.get_speech_timestamps

        def observed_vad(audio, options):
            expected_pcm = np.frombuffer(original, dtype="<i2").astype(np.float32) / 32768
            assert np.array_equal(audio, expected_pcm), "decode changed PCM or truncated edges"
            result["pcm"] = {
                "samples": len(audio),
                "rate": 16000,
                "channels": 1,
                "duration": len(audio) / 16000,
                "peak": float(np.max(np.abs(audio))),
                "rms": float(np.sqrt(np.mean(audio**2))),
                "clipped_samples": int(np.count_nonzero(np.abs(audio) >= 32767 / 32768)),
                "exact_original": True,
                "sha256": hashlib.sha256(audio.tobytes()).hexdigest(),
            }
            return original_vad(audio, options)

        class ObservedModel(faster_whisper.WhisperModel):
            def __init__(self, *a, **kw):
                nonlocal phase
                phase = "model_load"
                t = time.monotonic()
                try:
                    super().__init__(*a, **kw)
                finally:
                    result["model_load_seconds"] = time.monotonic() - t
                phase = "recognition"
                result["recognition_started"] = time.monotonic()

            def transcribe(self, *a, **kw):
                segments, info = super().transcribe(*a, **kw)

                def observe():
                    for segment in segments:
                        result["raw_segments"].append(
                            {"text": segment.text, "start": segment.start, "end": segment.end}
                        )
                        yield segment

                return observe(), info

        with (
            patch.object(engine, "verify_model", verify),
            patch.object(av, "open", observed_open),
            patch.object(vad, "get_speech_timestamps", observed_vad),
            patch.object(faster_whisper, "WhisperModel", ObservedModel),
            path.open("rb") as stream,
        ):
            transcript = engine.transcribe(stream, "audio/wav", args.language, args.model)
        result["transcript"] = transcript.model_dump(mode="json")
        assert [c.text for c in transcript.segments] == [
            s["text"].strip() for s in result["raw_segments"] if s["text"].strip()
        ]
        result["status"] = "ok"
    except Exception as exc:
        result.update(
            status="failed",
            phase=phase,
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )
    finally:
        end = time.monotonic()
        if "recognition_started" in result:
            result["recognition_seconds"] = end - result.pop("recognition_started")
        result["total_seconds"] = end - started
        result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def run(args):
    from unimem_api.audio_worker import resident_bytes
    from unimem_asr.policy import EXECUTION_SECONDS, MAX_RSS_BYTES

    samples = json.loads((EVIDENCE / "samples.json").read_text())
    spec = next(s for s in samples if s["id"] == args.sample)
    assert args.language == spec["language"] or (
        args.sample == "ru-negation" and args.language == "auto"
    )
    if spec["phase"] == "heldout":
        assert (EVIDENCE / "DECISION.md").is_file(), "record preliminary decision before heldout"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assert not args.output.exists(), "preserve previous runs; choose a new output"
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "OPENBLAS_NUM_THREADS": "2",
        "OMP_NUM_THREADS": "2",
        "TOKENIZERS_PARALLELISM": "false",
    }
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "child",
        "--sample",
        args.sample,
        "--candidate",
        args.candidate,
        "--language",
        args.language,
        "--audio",
        str(args.audio),
        "--model",
        str(args.model),
        "--output",
        str(args.output),
    ]
    start = time.monotonic()
    peak = 0
    killed = None
    with subprocess.Popen(command, env=env) as process:
        while process.poll() is None:
            peak = max(peak, resident_bytes(process.pid))
            if peak > MAX_RSS_BYTES or time.monotonic() - start > EXECUTION_SECONDS:
                killed = "rss" if peak > MAX_RSS_BYTES else "wall_time"
                process.kill()
                break
            time.sleep(0.025)
        returncode = process.wait()
    result = json.loads(args.output.read_text()) if args.output.exists() else {}
    result.update(
        sample=args.sample,
        candidate=args.candidate,
        language=args.language,
        process_seconds=time.monotonic() - start,
        monitored_peak_rss_bytes=peak,
        returncode=returncode,
        killed=killed,
    )
    if killed or returncode:
        result["status"] = "failed"
    result["hypothesis"] = " ".join(s["text"].strip() for s in result.get("raw_segments", []))
    result["score"] = (
        score(spec["reference"], result["hypothesis"]) if result.get("status") == "ok" else None
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in (
                    "sample",
                    "candidate",
                    "status",
                    "phase",
                    "score",
                    "process_seconds",
                    "peak_rss_bytes",
                    "killed",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "child", "audit"])
    parser.add_argument("--sample")
    parser.add_argument("--candidate", choices=["base", "small"])
    parser.add_argument("--language", choices=["ru", "en", "auto"])
    parser.add_argument("--model", type=Path)
    parser.add_argument("--results", type=Path)
    for name in ("audio", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    options = parser.parse_args()
    if options.mode != "audit" and not all(
        (options.sample, options.candidate, options.language, options.model)
    ):
        parser.error("run/child require --sample, --candidate, --language and --model")
    if options.mode == "audit" and options.results is None:
        parser.error("audit requires --results")
    {"child": child, "run": run, "audit": audit}[options.mode](options)
