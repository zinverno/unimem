"""One bounded, offline llama.cpp child with no access to application files or tools."""

import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from unimem_images import inspect_image, validate_pixels
from unimem_vision.policy import (
    COMPONENTS,
    DIAGNOSTIC_BYTES,
    MAX_RSS,
    MODEL,
    OUTPUT_BYTES,
    OUTPUT_TOKENS,
    PARAMETERS,
    PROJECTOR,
    PROMPT,
    PROMPT_VERSION,
    REVISION,
    RUNTIME,
    SCHEMA,
    SECONDS,
    SYSTEM,
    WEIGHTS,
    VisionError,
)
from unimem_vision.profile import verify_profile


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    status: Literal["described", "unclear", "refused"]
    description: str = Field(min_length=1, max_length=8000)


class Description(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    answer: Answer
    raw_response: str
    preprocessing: dict[str, JsonValue]
    metrics: dict[str, JsonValue]


def prepare_input(stream: BinaryIO, destination: Path) -> dict[str, JsonValue]:
    data, _, header = inspect_image(stream, "")
    validate_pixels(data, header)
    from PIL import Image, ImageOps, __version__

    with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as source:
        orientation = source.getexif().get(274, 1)
        oriented = ImageOps.exif_transpose(source)
        # Flatten transparency on white, never crop. Copy pixels into a new image:
        # filenames, EXIF and other source metadata never reach the runtime.
        rgba = oriented.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
        scale = min(1, 768 / max(image.size), math.sqrt(262144 / (image.width * image.height)))
        width, height = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        if min(width, height) < 28:
            raise VisionError("vision_input_too_narrow")
        if (width, height) != image.size:
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        image.save(destination, format="PNG")
    return {
        "version": "oriented-fit-white/1",
        "pillow_version": __version__,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "encoded_width": header.width,
        "encoded_height": header.height,
        "oriented_width": oriented.width,
        "oriented_height": oriented.height,
        "orientation_applied": orientation in (2, 3, 4, 5, 6, 7, 8),
        "width": width,
        "height": height,
        "scale": scale,
        "cropped": False,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "warning": "Reduced input; small text and fine details may be lost.",
    }


def command(profile: Path, image: Path) -> list[str]:
    return [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/usr/lib",
        "/usr/lib",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib",
        "/lib64",
        "--ro-bind",
        str(profile / "cpu"),
        "/runtime",
        "--ro-bind",
        str(profile / WEIGHTS),
        "/model.gguf",
        "--ro-bind",
        str(profile / PROJECTOR),
        "/mmproj.gguf",
        "--ro-bind",
        str(image),
        "/input.png",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "OMP_NUM_THREADS",
        "2",
        "--setenv",
        "OPENBLAS_NUM_THREADS",
        "2",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--",
        "/runtime/llama-mtmd-cli",
        "-m",
        "/model.gguf",
        "--mmproj",
        "/mmproj.gguf",
        "--image",
        "/input.png",
        "-sys",
        SYSTEM,
        "-p",
        PROMPT,
        "--json-schema",
        json.dumps(SCHEMA),
        "-t",
        "2",
        "-tb",
        "2",
        "-c",
        "2048",
        "-n",
        str(OUTPUT_TOKENS),
        "-b",
        "128",
        "-ub",
        "128",
        "--temp",
        "0",
        "--seed",
        "42",
        "--image-min-tokens",
        "64",
        "--image-max-tokens",
        "256",
        "--no-mmproj-offload",
        "--device",
        "none",
        "-ngl",
        "0",
        "--no-op-offload",
        "--fit",
        "off",
        "--offline",
        "--no-warmup",
        "--log-colors",
        "off",
        "--perf",
        "--verbosity",
        "4",
    ]


def resident_tree(pid: int) -> int:
    try:
        root = Path(f"/proc/{pid}")
        rss = int((root / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        children = (root / f"task/{pid}/children").read_text().split()
        return rss + sum(resident_tree(int(child)) for child in children)
    except (FileNotFoundError, ProcessLookupError):
        return 0


def parse_answer(stdout: bytes, diagnostics: str) -> tuple[Answer, str, dict[str, JsonValue]]:
    counts = re.findall(r"(?<!prompt )eval time =\s*([\d.]+) ms /\s*(\d+) runs", diagnostics)
    if len(counts) != 1 or int(counts[0][1]) < 1:
        raise VisionError("vision_completion_unverified")
    milliseconds, count = counts[0]
    # The pinned CLI does not expose finish_reason. Every emitted non-EOG token
    # is decoded; conservatively reject the boundary, even if JSON looks complete.
    if int(count) >= OUTPUT_TOKENS - 1:
        raise VisionError("vision_output_limit")
    if not stdout or len(stdout) > OUTPUT_BYTES:
        raise VisionError("vision_invalid_output")
    try:
        raw = stdout.decode("utf-8").strip()

        def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
            obj = dict(pairs)
            if len(obj) != len(pairs):
                raise ValueError("duplicate key")
            return obj

        answer = Answer.model_validate(json.loads(raw, object_pairs_hook=unique))
    except (ValueError, UnicodeError):
        raise VisionError("vision_invalid_output") from None
    if not answer.description.strip() or any(
        ord(c) < 32 and c not in "\n\t" for c in answer.description
    ):
        raise VisionError("vision_invalid_output")
    if answer.status == "refused":
        raise VisionError("vision_refused")
    return (
        answer,
        raw,
        {"generation_seconds": float(milliseconds) / 1000, "decoded_tokens": int(count)},
    )


def describe(stream: BinaryIO, profile: Path) -> Description:
    started = time.monotonic()
    verify_profile(profile)
    verified = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="unimem-vision-") as directory:
        work = Path(directory)
        preprocessing = prepare_input(stream, work / "input.png")
        prepared = time.monotonic()
        peak, ready = 0, None
        with (work / "answer").open("w+b") as output, (work / "diagnostics").open("w+b") as logs:
            # The parent image worker owns this process group. Bubblewrap's
            # private PID namespace + die-with-parent also terminate descendants
            # when the worker disappears. No shell and no user command arguments.
            with subprocess.Popen(
                command(profile.resolve(), work / "input.png"),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=logs,
                env={"LANG": "C.UTF-8"},
            ) as child:
                try:
                    while child.poll() is None:
                        elapsed = time.monotonic() - prepared
                        peak = max(peak, resident_tree(child.pid))
                        if elapsed > SECONDS or peak > MAX_RSS:
                            raise VisionError("vision_budget_exceeded")
                        if output.tell() > OUTPUT_BYTES or logs.tell() > DIAGNOSTIC_BYTES:
                            raise VisionError("vision_output_limit")
                        if (
                            ready is None
                            and b"main: loading model:" in (work / "diagnostics").read_bytes()
                        ):
                            ready = elapsed
                        time.sleep(0.1)
                finally:
                    if child.poll() is None:
                        child.kill()  # PID namespace tears down every inference descendant.
                    child.wait(timeout=5)
                if child.returncode != 0:
                    raise VisionError("vision_execution_failed")
            output.seek(0)
            logs.seek(0)
            diagnostic_bytes = logs.read(DIAGNOSTIC_BYTES + 1)
            if len(diagnostic_bytes) > DIAGNOSTIC_BYTES:
                raise VisionError("vision_output_limit")
            diagnostic = diagnostic_bytes.decode("utf-8", errors="replace")
            answer, raw, timing = parse_answer(output.read(OUTPUT_BYTES + 1), diagnostic)
        encode = re.findall(r"mtmd batch encoding done in (\d+) ms", diagnostic)
        if ready is None or not encode:
            raise VisionError("vision_completion_unverified")
        return Description(
            answer=answer,
            raw_response=raw,
            preprocessing=preprocessing,
            metrics={
                **timing,
                "profile_verification_seconds": verified - started,
                "preprocessing_seconds": prepared - verified,
                "model_ready_seconds": ready,
                "vision_encoding_seconds": sum(map(int, encode)) / 1000,
                "runtime_seconds": time.monotonic() - prepared,
                "total_seconds": time.monotonic() - started,
                "peak_process_tree_rss": peak,
                "completion": "ended_before_token_limit",
            },
        )


def provenance() -> dict[str, JsonValue]:
    return {
        "model": MODEL,
        "revision": REVISION,
        "runtime": RUNTIME,
        "components": {k: str(v["sha256"]) for k, v in COMPONENTS.items()},
        "parameters": dict(PARAMETERS),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256((SYSTEM + "\n" + PROMPT).encode()).hexdigest(),
        "limitations": "Model interpretation, not verified fact or OCR. Fine details may be lost.",
    }
