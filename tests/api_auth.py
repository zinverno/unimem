"""Explicit credentials for legacy API regression harnesses, never production defaults."""

import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient as StarletteTestClient
from starlette.types import ASGIApp

from unimem_api.security import ApiSecurity

TEST_TOKEN = "a" * 43
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
TEST_SECURITY = ApiSecurity(TEST_TOKEN)


class AuthenticatedClient(StarletteTestClient):
    __test__ = False

    def __init__(self, app: ASGIApp, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "http://127.0.0.1:8765")
        kwargs["headers"] = AUTH_HEADERS | kwargs.get("headers", {})
        super().__init__(app, **kwargs)


def token_path(directory: Path) -> Path:
    path = directory / "test-api.token"
    if not path.exists():
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
            file.write(TEST_TOKEN + "\n")
    return path
