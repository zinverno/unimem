"""Verify local components. Network exists only in the explicit preparation CLI."""

import hashlib
import platform
import subprocess
from importlib import import_module
from pathlib import Path

from unimem_vision.policy import COMPONENTS, MODEL, REVISION, VisionError


def verify_file(path: Path, size: int | str, sha256: int | str) -> None:
    try:
        if path.is_symlink() or path.stat().st_size != size:
            raise VisionError("vision_profile_invalid")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                raise VisionError("vision_profile_invalid")
    except OSError:
        raise VisionError("vision_profile_missing") from None


def verify_profile(root: Path) -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise VisionError("vision_platform_unsupported")
    if not Path("/usr/bin/bwrap").is_file():
        raise VisionError("vision_sandbox_unavailable")
    flags = Path("/proc/cpuinfo").read_text()
    if not all(flag in flags.split() for flag in ("avx2", "fma", "f16c")):
        raise VisionError("vision_platform_unsupported")
    for name, spec in COMPONENTS.items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise VisionError("vision_profile_invalid")
        verify_file(path, spec["size"], spec["sha256"])
    expected = {Path(name).name for name in COMPONENTS if name.startswith("cpu/")}
    if expected and {p.name for p in (root / "cpu").iterdir()} != expected:
        raise VisionError("vision_profile_invalid")


def probe_sandbox(root: Path) -> None:
    from unimem_vision.engine import command

    args = command(root.resolve(), (root / next(iter(COMPONENTS))).resolve())
    args = [*args[: args.index("--") + 1], "/runtime/llama-mtmd-cli", "--version"]
    try:
        result = subprocess.run(args, capture_output=True, timeout=10, env={}, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise VisionError("vision_sandbox_unavailable") from None
    if result.returncode or b"build 11146, commit 7fe450e19" not in result.stderr + result.stdout:
        raise VisionError("vision_sandbox_unavailable")


def readiness(root: Path | None) -> dict[str, str | bool]:
    # Called once on opt-in API startup; never downloads or loads a model.
    code = "vision_disabled"
    if root is not None:
        try:
            verify_profile(root)
            probe_sandbox(root)
            import_module("PIL.Image")
            import_module("PIL.ImageOps")
            code = "ready"
        except VisionError as exc:
            code = exc.code
        except ImportError:
            code = "image_decoder_unavailable"
    return {"ready": code == "ready", "code": code, "model": MODEL, "revision": REVISION}
