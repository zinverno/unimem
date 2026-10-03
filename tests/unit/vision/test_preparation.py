"""Explicit preparation is the only network-capable vision command."""

import hashlib
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import unimem_vision.__main__ as preparation
import unimem_vision.profile as profile
from unimem_vision.policy import ARCHIVE, PROJECTOR, REVISION, WEIGHTS, VisionError


def test_plan_has_sizes_and_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vision", "--profile", str(tmp_path / "missing")])
    monkeypatch.setattr(preparation, "prepare", lambda _: pytest.fail("implicit download"))
    assert preparation.main() == 0
    printed = capsys.readouterr().out
    assert "1569461525" in printed
    assert "Q4_K_M" in printed
    assert "Q8_0" in printed
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize(
    ("data", "size", "sha", "code"),
    [
        (b"abcd", 3, hashlib.sha256(b"abc").hexdigest(), "vision_download_limit"),
        (b"ab", 3, hashlib.sha256(b"abc").hexdigest(), "vision_profile_invalid"),
        (b"bad", 3, hashlib.sha256(b"abc").hexdigest(), "vision_profile_invalid"),
        (b"abc", 3, hashlib.sha256(b"abc").hexdigest(), None),
    ],
)
def test_download_is_bounded_verified_and_create_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    size: int,
    sha: str,
    code: str | None,
) -> None:
    class Opener:
        def open(self, url: str, timeout: int) -> io.BytesIO:
            assert url == "https://fixed.test/weights"
            assert timeout == 30
            return io.BytesIO(data)

    monkeypatch.setattr("unimem_vision.__main__.urllib.request.build_opener", lambda *_: Opener())
    target = tmp_path / "weights"
    if code:
        with pytest.raises(VisionError, match=code):
            preparation.download("https://fixed.test/weights", target, size, sha)
        assert not target.exists()
    else:
        preparation.download("https://fixed.test/weights", target, size, sha)
        assert target.read_bytes() == data
        monkeypatch.setattr(
            "unimem_vision.__main__.urllib.request.build_opener",
            lambda *_: pytest.fail("unneeded download"),
        )
        preparation.download("https://fixed.test/weights", target, size, sha)
    assert not list(tmp_path.glob(".vision-download-*"))


def test_probe_is_bounded_and_does_not_load_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert args[-2:] == ["/runtime/llama-mtmd-cli", "--version"]
        assert "--unshare-all" in args
        assert "-p" not in args
        assert kwargs["timeout"] == 10
        assert kwargs["env"] == {}
        return subprocess.CompletedProcess(args, 0, b"build 11146, commit 7fe450e19", b"")

    monkeypatch.setattr("unimem_vision.profile.subprocess.run", run)
    profile.probe_sandbox(tmp_path)
    monkeypatch.setattr(
        "unimem_vision.profile.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"private error"),
    )
    with pytest.raises(VisionError, match="vision_sandbox_unavailable"):
        profile.probe_sandbox(tmp_path)


def test_readiness_requires_decoder_but_disabled_profile_imports_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile, "verify_profile", lambda _: None)
    monkeypatch.setattr(profile, "probe_sandbox", lambda _: None)
    attempted = []

    def missing(name: str) -> None:
        attempted.append(name)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(profile, "import_module", missing)
    assert profile.readiness(None)["code"] == "vision_disabled"
    assert not attempted
    assert profile.readiness(tmp_path)["code"] == "image_decoder_unavailable"
    assert attempted == ["PIL.Image"]


def test_preparation_extracts_only_verified_cpu_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tarfile

    weights, projector, archive = WEIGHTS, PROJECTOR, ARCHIVE
    payloads = {weights: b"weights", projector: b"projector", "cpu/llama-mtmd-cli": b"runtime"}
    specs = {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in payloads.items()
    }
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as bundle:
        for name, data in [("llama-b11146/llama-mtmd-cli", b"runtime"), ("../../unwanted", b"bad")]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    urls = []

    def download(url: str, target: Path, size: int, sha: str) -> None:
        urls.append(url)
        if not target.exists():
            target.write_bytes(
                packed.getvalue() if target.name == archive else payloads[target.name]
            )

    verified = []
    monkeypatch.setattr(preparation, "download", download)
    monkeypatch.setattr(preparation, "COMPONENTS", specs)
    monkeypatch.setattr(preparation, "verify_profile", lambda root: verified.append(root))
    root = tmp_path / "profile"
    preparation.prepare(root)
    preparation.prepare(root)
    assert verified == [root, root]
    assert (root / "cpu/llama-mtmd-cli").read_bytes() == b"runtime"
    assert (root / "cpu/llama-mtmd-cli").stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "unwanted").exists()
    assert len(set(urls)) == 3
    assert all(REVISION in url for url in urls if "huggingface" in url)
    (root / "cpu/llama-mtmd-cli").write_bytes(b"broken!")
    with pytest.raises(VisionError, match="vision_profile_invalid"):
        preparation.prepare(root)


def test_explicit_main_reports_failure_without_leaking_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vision", "--profile", str(tmp_path), "--download"])
    called = []
    monkeypatch.setattr(preparation, "prepare", lambda root: called.append(root))
    assert preparation.main() == 0
    assert called == [tmp_path]
    assert "verified" in capsys.readouterr().out

    def fail(_: Path) -> None:
        raise VisionError("private diagnostic")

    monkeypatch.setattr(preparation, "prepare", fail)
    with pytest.raises(SystemExit, match="Preparation failed") as error:
        preparation.main()
    assert "private" not in str(error.value)
