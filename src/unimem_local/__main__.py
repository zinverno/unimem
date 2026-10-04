"""A saved TOML argument list for the existing CLI; diagnosis is stdlib-only."""

import argparse
import fcntl
import importlib.metadata
import json
import os
import socket
import stat
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast

FLAGS = ("youtube", "image_ocr", "video_notes", "pdf_ocr", "media")
PATHS = ("data_dir", "token_file", "audio_model", "image_description_profile")


def default_config() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "unimem/config.toml"
    )


def read_config(path: Path) -> dict[str, str | bool]:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError("Cannot read config; run unimem setup or check the TOML file.") from None
    if set(raw) - {*FLAGS, *PATHS} or "data_dir" not in raw:
        raise ValueError("Config requires data_dir and accepts only documented launch options.")
    for key, value in raw.items():
        if key in FLAGS:
            if type(value) is not bool:
                raise ValueError(f"Config {key} must be true or false.")
        elif not isinstance(value, str) or not Path(value).is_absolute() or "\x00" in value:
            raise ValueError(f"Config {key} must be an absolute path.")
    return cast(dict[str, str | bool], raw)


def cli_args(config: dict[str, str | bool]) -> list[str]:
    args = ["--data-dir", str(config["data_dir"])]
    for key in PATHS[1:]:
        if key in config:
            args.extend(["--" + key.replace("_", "-"), str(config[key])])
    for key in FLAGS:
        if config.get(key, False):
            args.append("--" + key.replace("_", "-"))
    return args


def setup(path: Path) -> None:
    if not path.exists():
        data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "unimem"
        if not data.is_absolute():
            raise ValueError("XDG_DATA_HOME must be absolute.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        text = (
            "# No secrets here. Absolute paths; optional components start disabled.\n"
            f"data_dir = {json.dumps(str(data))}\n"
            "youtube = false\nimage_ocr = false\nvideo_notes = false\n"
            '# audio_model = "/absolute/path/to/models/asr-base"\n'
            '# image_description_profile = "/absolute/path/to/models/vision"\n'
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
    config = read_config(path)
    from unimem_api.credentials import issue_token, read_token

    data_dir = Path(str(config["data_dir"]))
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = Path(str(config.get("token_file", data_dir / "api.token")))
    if not token.exists():
        issue_token(token)
    read_token(token)
    print("Setup ready. Existing config, credential and destinations are preserved.")
    print(f"Config: {path}")


def check_lease(data_dir: Path) -> str:
    """Observe the existing inode. Never create, truncate, or unlink a lock."""
    try:
        fd = os.open(data_dir / ".api-server.lock", os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "free"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "owned"
        return "free"
    finally:
        os.close(fd)


def port_available() -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8765))
        except OSError:
            return False
    return True


def diagnose(config: dict[str, str | bool]) -> None:
    """No application/model imports, subprocesses, HTTP writes, or SQLite opens."""
    data_dir = Path(str(config["data_dir"]))
    print(f"Data directory: {data_dir}")
    print(f"Process lease: {check_lease(data_dir)} (lock files must not be deleted)")
    print(f"Port 127.0.0.1:8765: {'free' if port_available() else 'occupied'}")
    token = Path(str(config.get("token_file", data_dir / "api.token")))
    try:
        info = token.lstat()
        secure = (
            stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
        )
    except OSError:
        secure = False
    print(
        f"Browser credential file: {'owner-only file present' if secure else 'missing or unsafe'}"
    )
    for key in FLAGS:
        print(f"{key}: {'enabled' if config.get(key) else 'disabled'}")
    for key in ("audio_model", "image_description_profile"):
        directory = config.get(key)
        state = "disabled" if not directory else "files present; not verified or loaded"
        if directory and not Path(str(directory)).is_dir():
            state = "directory missing; prepare explicitly (no automatic download)"
        print(f"{key}: {state}")
    for package in ("capture-core", "youtube-transcript-api", "faster-whisper", "av", "Pillow"):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "not installed (optional)"
        print(f"{package}: {version}")
    print("Use the browser connection check for authenticated runtime readiness and destinations.")


def destination(config: dict[str, str | bool]) -> None:
    """Explicit one-time receiver enrollment; reruns cannot mint another identity."""
    from unimem_api.obsidian_store import ObsidianStore
    from unimem_api.worker import ServerLease

    data_dir = Path(str(config["data_dir"]))
    with ServerLease(data_dir):
        store = ObsidianStore(data_dir / "obsidian-delivery.sqlite3")
        existing = store.destinations()
        if existing:
            print("Destinations preserved; use the previously issued receiver credential.")
            for row in existing:
                print(f"Destination ID: {row.destination_id}")
            return
        row, token = store.create_destination("Obsidian")
        # Only this explicit enrollment command reveals the receiver secret once.
        print(json.dumps({"destination_id": row.destination_id, "receiver_token": token}))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config())
    parser.add_argument(
        "command", choices=("setup", "start", "diagnose", "show-browser-token", "destination")
    )
    options = parser.parse_args(argv)
    try:
        if not options.config.is_absolute():
            raise ValueError("Config path must be absolute.")
        if options.command == "setup":
            setup(options.config)
            return 0
        config = read_config(options.config)
        if options.command == "diagnose":
            diagnose(config)
            return 0
        if options.command == "destination":
            destination(config)
            return 0
        if options.command == "start":
            if check_lease(Path(str(config["data_dir"]))) != "free":
                raise ValueError(
                    "Another API process/worker owns this data directory; stop it first."
                )
            if not port_available():
                raise ValueError("Port 127.0.0.1:8765 is occupied; stop the owning service first.")
        from unimem_api.__main__ import main as api_main

        args = cli_args(config)
        if options.command == "show-browser-token":
            args.append("--show-token")
        return api_main(args)
    except (OSError, ValueError, RuntimeError) as exc:
        # Never print an OS error that could contain paths or credential bytes.
        message = (
            str(exc) if isinstance(exc, ValueError | RuntimeError) else "Local file unavailable."
        )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
