"""Explicit local secret issuance; never served over HTTP."""

import os
import secrets
import stat
import tempfile
from pathlib import Path

from unimem_api.security import TOKEN_PATTERN


def read_token(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError
            token = file.read(128).strip()
            if not TOKEN_PATTERN.fullmatch(token):
                raise ValueError
            return token
    except (OSError, ValueError, UnicodeError):
        raise ValueError(
            "Credential unavailable; initialize it explicitly and use owner-only permissions."
        ) from None


def issue_token(path: Path, *, rotate: bool = False) -> None:
    """Create exclusively, or explicitly replace; no token in the result/logs."""
    if rotate:
        read_token(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            file.write(secrets.token_urlsafe(32) + "\n")
            file.flush()
            os.fsync(file.fileno())
        if rotate:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    except OSError:
        raise ValueError(
            "Could not issue credential; initialization never overwrites an existing file."
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
