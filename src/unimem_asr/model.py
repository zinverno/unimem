"""Explicit model preparation, and strictly local verification afterwards."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

from unimem_asr.policy import ENGINE_VERSION, MODEL_FILES, MODEL_ID, MODEL_REVISION, AsrError


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_engine() -> None:
    try:
        if importlib.metadata.version("faster-whisper") != ENGINE_VERSION:
            raise AsrError("model_unavailable")
        for package in ("av", "ctranslate2", "onnxruntime"):
            importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        raise AsrError("model_unavailable") from None


def verify_model(directory: Path) -> dict[str, str]:
    try:
        manifest = json.loads((directory / "unimem-model.json").read_text())
        if manifest["model"] != MODEL_ID or manifest["revision"] != MODEL_REVISION:
            raise ValueError
        hashes = {name: digest(directory / name) for name in MODEL_FILES}
        if hashes != manifest["sha256"]:
            raise ValueError
        return hashes
    except (OSError, ValueError, KeyError, TypeError):
        raise AsrError("model_unavailable") from None


def prepare_model(directory: Path) -> dict[str, str]:
    """The only network operation in this package, called by explicit CLI action."""
    from huggingface_hub import snapshot_download

    snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=directory,
        allow_patterns=list(MODEL_FILES),
        token=False,
    )
    hashes = {name: digest(directory / name) for name in MODEL_FILES}
    (directory / "unimem-model.json").write_text(
        json.dumps(
            {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "sha256": hashes,
            },
            indent=2,
        )
        + "\n"
    )
    return hashes
