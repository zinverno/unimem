import asyncio
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from unimem_api.credentials import issue_token, read_token
from unimem_api.security import ApiSecurity, LocalApiSecurity

TOKEN = "a" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ORIGIN = "moz-extension://12345678-1234-1234-1234-123456789abc"


def client(**limits: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(LocalApiSecurity, policy=ApiSecurity(TOKEN, **limits))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route("/v1/test", methods=["GET", "POST"])
    async def read(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.post("/v1/uploads")
    async def upload(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_auth_host_origin_and_no_query_credentials() -> None:
    with client() as http:
        assert http.get("/health").json() == {"status": "ok"}
        assert http.get("/v1/test").status_code == 401
        assert http.get("/v1/test", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert http.get("/v1/test", headers=AUTH).status_code == 200
        for origin in (ORIGIN, "chrome-extension://" + "a" * 32):
            response = http.get("/v1/test", headers=AUTH | {"Origin": origin})
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            assert http.get("/v1/test", headers={"Origin": origin}).status_code == 401
        for origin in ("https://evil.test", "null", ORIGIN + "/", ORIGIN + ".evil.test"):
            assert http.get("/v1/test", headers=AUTH | {"Origin": origin}).status_code == 403
        for host in ("evil.test:8765", "127.0.0.1.evil.test", "127.0.0.1:9999"):
            assert http.get("/v1/test", headers=AUTH | {"Host": host}).status_code == 400
        response = http.get("/v1/test?token=" + TOKEN, headers=AUTH)
        assert response.status_code == 400
        assert TOKEN not in response.text


def test_preflight_does_not_grant_data_access() -> None:
    with client() as http:
        response = http.options(
            "/v1/test",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert http.post("/v1/test", headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize("path", ["/v1/test", "/v1/uploads"])
def test_body_limits_include_chunked_and_multipart(path: str) -> None:
    with client(json_bytes=20, upload_bytes=20) as http:
        assert http.post(path, content=b"a" * 21, headers=AUTH).status_code == 413
        assert http.post(path, content=iter([b"a" * 10] * 3), headers=AUTH).status_code == 413
        assert http.post(path, files={"file": ("x", b"a" * 21)}, headers=AUTH).status_code == 413
        assert http.post(path, content=b"123", headers=AUTH).status_code == 200


def test_request_rate_is_bounded() -> None:
    with client(requests_per_minute=2) as http:
        assert http.get("/v1/test", headers=AUTH).status_code == 200
        assert http.get("/v1/test", headers=AUTH).status_code == 200
        assert http.get("/v1/test", headers=AUTH).status_code == 429


def test_credential_issuance_rotation_and_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private" / "token"
    issue_token(path)
    first = read_token(path)
    assert len(first) == 43
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="Could not issue"):
        issue_token(path)
    assert read_token(path) == first
    issue_token(path, rotate=True)
    assert read_token(path) != first
    assert first not in repr(ApiSecurity(first))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="Credential unavailable"):
        read_token(path)
    link = tmp_path / "link"
    os.symlink(path, link)
    with pytest.raises(ValueError, match="Credential unavailable"):
        read_token(link)


def test_ingress_concurrency_bound_releases_after_completion() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        replies: list[Message] = []

        async def application(scope: Scope, receive: Receive, send: Send) -> None:
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            return {"type": "http.request", "body": b""}

        async def send(message: Message) -> None:
            replies.append(message)

        middleware = LocalApiSecurity(application, ApiSecurity(TOKEN, concurrent_requests=1))
        scope: Scope = {
            "type": "http",
            "path": "/v1/test",
            "method": "GET",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"authorization", f"Bearer {TOKEN}".encode()),
            ],
        }
        first = asyncio.create_task(middleware(scope, receive, send))
        await entered.wait()
        await middleware(scope, receive, send)
        assert replies[0]["status"] == 429
        release.set()
        await first
        await middleware(scope, receive, send)
        assert [r["status"] for r in replies if r["type"] == "http.response.start"] == [
            429,
            200,
            200,
        ]

    asyncio.run(scenario())


def test_invalid_configuration_and_framing_are_refused() -> None:
    with pytest.raises(ValueError, match="Invalid local API"):
        ApiSecurity("bad")
    with pytest.raises(ValueError, match="limits must be positive"):
        ApiSecurity(TOKEN, json_bytes=0)
    with client() as http:
        assert (
            http.post(
                "/v1/test",
                content=b"a",
                headers=AUTH
                | {
                    "Content-Length": "bad",
                },
            ).status_code
            == 400
        )
        assert (
            http.options(
                "/v1/test",
                headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "DELETE",
                },
            ).status_code
            == 403
        )


def test_token_missing_malformed_or_wrong_owner_is_not_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "token"
    with pytest.raises(ValueError, match="Credential unavailable"):
        read_token(path)
    issue_token(path)
    with monkeypatch.context() as patch:
        patch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
        with pytest.raises(ValueError, match="Credential unavailable"):
            read_token(path)
    path.write_text("not a credential")
    with pytest.raises(ValueError, match="Credential unavailable"):
        read_token(path)
