"""Explicit, resumable preparation of exactly one pinned CPU profile."""

import argparse
import shutil
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from unimem_vision.policy import (
    ARCHIVE,
    ARCHIVE_SHA,
    ARCHIVE_SIZE,
    COMPONENTS,
    MODEL,
    PROJECTOR,
    REVISION,
    WEIGHTS,
    VisionError,
)
from unimem_vision.profile import verify_file, verify_profile


def download(url: str, target: Path, size: int, sha: str) -> None:
    if target.exists():
        verify_file(target, size, sha)
        return
    deadline = time.monotonic() + 3600
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".vision-download-") as output:
        part = Path(output.name)
        with opener.open(url, timeout=30) as source:
            total = 0
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > size or time.monotonic() > deadline:
                    raise VisionError("vision_download_limit")
                output.write(block)
        output.flush()
        verify_file(part, size, sha)
        # Create-only: a concurrent preparer or an existing damaged file is never overwritten.
        target.hardlink_to(part)


def prepare(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in (WEIGHTS, PROJECTOR):
        spec = COMPONENTS[name]
        download(
            f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}",
            root / name,
            int(spec["size"]),
            str(spec["sha256"]),
        )
    archive = root / ARCHIVE
    download(
        f"https://github.com/ggml-org/llama.cpp/releases/download/b11146/{ARCHIVE}",
        archive,
        ARCHIVE_SIZE,
        ARCHIVE_SHA,
    )
    (root / "cpu").mkdir(exist_ok=True)
    with tarfile.open(archive) as bundle:
        for name, spec in COMPONENTS.items():
            if not name.startswith("cpu/"):
                continue
            target = root / name
            if not target.exists():
                # No extractall, paths or executables supplied by the archive are trusted.
                member = bundle.getmember("llama-b11146/" + target.name)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise VisionError("vision_profile_invalid")
                with stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
            verify_file(target, spec["size"], spec["sha256"])
            target.chmod(0o700 if target.name == "llama-mtmd-cli" else 0o600)
    verify_profile(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--download", action="store_true", help="explicitly fetch the listed files")
    args = parser.parse_args()
    print(f"{MODEL}@{REVISION}; Apache-2.0. llama.cpp b11146; MIT.")
    for name in (WEIGHTS, PROJECTOR):
        print(f"{name}: {COMPONENTS[name]['size']} bytes, sha256 {COMPONENTS[name]['sha256']}")
    print(f"{ARCHIVE}: {ARCHIVE_SIZE} bytes, sha256 {ARCHIVE_SHA}")
    print("Total download: 1569461525 bytes. CPU only; Linux x86_64 AVX2/FMA/F16C + bubblewrap.")
    if args.download:
        try:
            prepare(args.profile.resolve())
        except (OSError, ValueError, tarfile.TarError, VisionError) as exc:
            raise SystemExit(
                f"Preparation failed ({type(exc).__name__}); no inference enabled."
            ) from None
        print("Local files verified. Inference uses no network.")
    else:
        print("Nothing downloaded. Review this plan, then repeat with --download.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
