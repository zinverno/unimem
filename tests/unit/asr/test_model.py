import hashlib
import importlib.metadata
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from unimem_asr.__main__ import main
from unimem_asr.model import require_engine, verify_model
from unimem_asr.policy import MODEL_FILES, MODEL_ID, MODEL_REVISION, AsrError


def test_model_preparation_is_explicit_pinned_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []

    def download(model: str, **kwargs: Any) -> None:
        calls.append(model)
        assert model == MODEL_ID
        assert kwargs == {
            "revision": MODEL_REVISION,
            "local_dir": tmp_path,
            "allow_patterns": list(MODEL_FILES),
            "token": False,
        }
        for name in MODEL_FILES:
            (tmp_path / name).write_bytes(b"fake weights for preparation contract only")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setattr("unimem_asr.__main__.require_engine", lambda: None)
    monkeypatch.setattr(sys, "argv", ["asr", "prepare", "--model-dir", str(tmp_path)])
    main()
    assert not calls
    assert not list(tmp_path.iterdir())
    assert MODEL_ID in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--download"])
    main()
    assert calls == [MODEL_ID]
    hashes = verify_model(tmp_path)
    assert set(hashes) == set(MODEL_FILES)
    assert hashes["model.bin"] == hashlib.sha256((tmp_path / "model.bin").read_bytes()).hexdigest()
    (tmp_path / "model.bin").write_bytes(b"tampered")
    with pytest.raises(AsrError, match="model_unavailable"):
        verify_model(tmp_path)
    assert calls == [MODEL_ID]


@pytest.mark.parametrize("version", [None, "wrong", "1.2.1"])
def test_optional_engine_version(monkeypatch: pytest.MonkeyPatch, version: str | None) -> None:
    def installed(package: str) -> str:
        if version is None:
            raise importlib.metadata.PackageNotFoundError(package)
        return version

    monkeypatch.setattr(importlib.metadata, "version", installed)
    if version == "1.2.1":
        require_engine()
    else:
        with pytest.raises(AsrError, match="model_unavailable"):
            require_engine()
