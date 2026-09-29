import builtins
from pathlib import Path
from typing import Any, Never

import pytest
from fastapi.testclient import TestClient

from tests.api_auth import AUTH_HEADERS, TEST_SECURITY
from tests.youtube_fixtures import URL, artifact
from unimem_api.__main__ import main, parse_args
from unimem_api.operations_http import install_operation_routes
from unimem_api.wiring import build_local_app
from unimem_api.worker import execute_operation
from unimem_youtube.errors import ExportError
from unimem_youtube.export import CaptionMarkdownRenderer
from unimem_youtube.operations import OperationStore, YoutubeRequest
from unimem_youtube.service import YoutubeCaptureService

ROUTE = "/v1/youtube/operations"


def test_all_data_routes_require_auth_before_body_parsing(tmp_path: Path) -> None:
    app = build_local_app(tmp_path, security=TEST_SECURITY)
    install_operation_routes(
        app, OperationStore(tmp_path / "unimem.sqlite3"), YoutubeCaptureService(tmp_path)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for method, path in (
            ("POST", "/v1/captures"),
            ("POST", "/v1/uploads"),
            ("GET", "/v1/captures/secret"),
            ("GET", "/v1/captures/secret/content"),
            ("GET", "/v1/content/secret"),
            ("POST", ROUTE),
            ("GET", ROUTE + "/secret"),
            ("GET", ROUTE + "/secret/content"),
            ("GET", ROUTE + "/secret/markdown"),
            ("GET", "/openapi.json"),
            ("GET", "/docs"),
        ):
            assert client.request(method, path, content=b"invalid").status_code == 401
        assert client.get("/health").json() == {"status": "ok"}


def test_validation_capacity_no_acquisition_on_reads_and_export_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_local_app(tmp_path, security=TEST_SECURITY)
    store = OperationStore(tmp_path / "unimem.sqlite3", capacity=1)
    service = YoutubeCaptureService(tmp_path)
    install_operation_routes(app, store, service)
    request = {"operation_id": "known", "url": URL, "languages": ["ru", "en"]}
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=AUTH_HEADERS) as client:
        invalid_requests: tuple[dict[str, object], ...] = (
            {"url": "http://youtube.com.evil.test/watch?v=abcdefghijk"},
            {"languages": []},
            {"output_dir": "/not-an-http-choice"},
            {"operation_id": "../unsafe"},
        )
        for invalid in invalid_requests:
            assert client.post(ROUTE, json=request | invalid).status_code == 422
        assert client.get(ROUTE + "/unknown").status_code == 404
        accepted = client.post(ROUTE, json=request)
        assert accepted.status_code == 202
        assert accepted.json()["capture_id"] is None
        assert store.get("known").state == "queued"
        assert client.get(ROUTE + "/known/content").status_code == 409
        assert client.get(ROUTE + "/known/markdown").status_code == 409
        assert client.post(ROUTE, json=request | {"operation_id": "full"}).status_code == 429
        assert client.post(ROUTE, json=request).status_code == 200
        assert store.claim() is not None
        execute_operation(tmp_path, "known", acquire=lambda *_: artifact())
        done = client.get(ROUTE + "/known").json()
        assert done["state"] == "complete"
        assert client.get(ROUTE + "/known/content").json()["id"] == done["content_id"]

        def fail(self: CaptionMarkdownRenderer, content: object) -> Never:
            raise ExportError("sensitive content should not escape")

        with monkeypatch.context() as patch:
            patch.setattr(CaptionMarkdownRenderer, "render", fail)
            response = client.get(ROUTE + "/known/markdown")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "markdown_unavailable"
            assert "sensitive" not in response.text
        assert client.get(ROUTE + "/known").json() == done
        assert client.get(ROUTE + "/known/markdown").status_code == 200
        assert service.read(done["capture_id"]).id == done["content_id"]


def test_default_api_and_cli_start_without_retrieval_but_opt_in_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = builtins.__import__

    def without_retrieval(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith(("unimem_youtube.retrieval", "youtube_transcript_api", "requests")):
            raise ImportError("optional library deliberately absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_retrieval)
    with TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY),
        base_url="http://127.0.0.1:8765",
        headers=AUTH_HEADERS,
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.post(ROUTE, json={}).status_code == 404
    with pytest.raises(ValueError, match="optional youtube extra"):
        build_local_app(tmp_path, security=TEST_SECURITY, youtube=True)
    assert main(["--data-dir", str(tmp_path), "--init-token"]) == 0
    with pytest.raises(SystemExit, match="optional youtube extra"):
        main(["--data-dir", str(tmp_path), "--youtube"])


def test_explicit_secret_commands_never_print_except_show(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--data-dir", str(tmp_path)]
    with pytest.raises(SystemExit, match="Credential unavailable"):
        main(args)
    assert main([*args, "--init-token"]) == 0
    assert capsys.readouterr().out == ""
    assert main([*args, "--show-token"]) == 0
    first = capsys.readouterr().out.strip()
    assert len(first) == 43
    assert main([*args, "--rotate-token"]) == 0
    assert capsys.readouterr().out == ""
    assert main([*args, "--show-token"]) == 0
    assert capsys.readouterr().out.strip() != first
    with pytest.raises(SystemExit):
        parse_args([*args, "--host", "0.0.0.0"])


def test_history_limit_retains_replay_receipts(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3", history_limit=1)
    request = YoutubeRequest(operation_id="one", url=URL)
    store.register(request)
    running = store.claim()
    assert running is not None
    store.finish(running, "failed", error_code="timeout")
    assert store.register(request)[1] is False
    app = build_local_app(tmp_path, security=TEST_SECURITY)
    install_operation_routes(app, store, YoutubeCaptureService(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=AUTH_HEADERS) as client:
        assert client.post(ROUTE, json={"operation_id": "two", "url": URL}).status_code == 507
