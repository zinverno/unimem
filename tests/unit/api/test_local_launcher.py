"""The saved launcher preserves enrollment; diagnosis cannot import the service."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from unimem_api.worker import ServerLease
from unimem_local.__main__ import check_lease, cli_args, main, read_config


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'data_dir = "{tmp_path / "data"}"\n')
    return path


def test_setup_and_destination_repeat_without_changing_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config_file(tmp_path)
    args = ["--config", str(path)]
    assert main([*args, "setup"]) == 0
    capsys.readouterr()
    token = (tmp_path / "data/api.token").read_bytes()
    assert main([*args, "destination"]) == 0
    pairing = json.loads(capsys.readouterr().out)
    before = {p.name: p.read_bytes() for p in (tmp_path / "data").iterdir()}
    assert main([*args, "setup"]) == 0
    assert main([*args, "destination"]) == 0
    output = capsys.readouterr().out
    assert pairing["destination_id"] in output
    assert pairing["receiver_token"] not in output
    assert token.decode().strip() not in output
    assert {p.name: p.read_bytes() for p in (tmp_path / "data").iterdir()} == before


def test_diagnosis_is_read_only_and_imports_no_app_or_models(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    before = path.read_bytes()
    code = """
import sys
from unimem_local.__main__ import main
assert main(['--config', sys.argv[1], 'diagnose']) == 0
for name in sys.modules:
    assert not name.startswith(('unimem_api', 'unimem_asr', 'unimem_vision',
                                'faster_whisper', 'ctranslate2', 'av', 'PIL'))
"""
    subprocess.run([sys.executable, "-c", code, str(path)], check=True, capture_output=True)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == before


def test_lease_conflict_never_deletes_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config_file(tmp_path)
    assert main(["--config", str(path), "setup"]) == 0
    data = tmp_path / "data"
    with ServerLease(data):
        inode = (data / ".api-server.lock").stat().st_ino
        assert check_lease(data) == "owned"
        assert main(["--config", str(path), "start"]) == 1
        assert "owns this data directory" in capsys.readouterr().err
    assert (data / ".api-server.lock").stat().st_ino == inode
    assert check_lease(data) == "free"


def test_port_conflict_stops_before_service_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config_file(tmp_path)
    monkeypatch.setattr("unimem_local.__main__.port_available", lambda: False)
    assert main(["--config", str(path), "start"]) == 1
    assert "8765 is occupied" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "text", ['data_dir="relative"', 'data_dir="/x"\nyoutube="yes"', 'data_dir="/x"\ntoken="secret"']
)
def test_config_rejects_relative_paths_unknown_keys_and_wrong_types(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="Config"):
        read_config(path)


def test_config_maps_only_existing_cli_options() -> None:
    assert cli_args(
        {
            "data_dir": "/data",
            "youtube": True,
            "image_ocr": False,
            "audio_model": "/models/base",
            "video_notes": True,
        }
    ) == ["--data-dir", "/data", "--audio-model", "/models/base", "--youtube", "--video-notes"]


def test_first_setup_uses_persistent_xdg_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "persistent"))
    path = tmp_path / "settings/config.toml"
    assert main(["--config", str(path), "setup"]) == 0
    assert read_config(path)["data_dir"] == str(tmp_path / "persistent/unimem")
    assert os.stat(path).st_mode & 0o777 == 0o600
