"""Destination isolation, immutable acceptance, fencing and restart receipts."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from tests.api_auth import AUTH_HEADERS, TEST_SECURITY, TEST_TOKEN
from tests.youtube_fixtures import artifact
from unimem_api.__main__ import main
from unimem_api.obsidian_contract import (
    MAX_MARKDOWN_BYTES,
    AckRequest,
    AnyDelivery,
    ClaimRequest,
    Delivery,
    DeliveryError,
    FailureRequest,
)
from unimem_api.obsidian_store import ObsidianStore
from unimem_api.security import ApiSecurity, LocalApiSecurity
from unimem_api.wiring import build_local_app
from unimem_youtube.export import CaptionMarkdownRenderer
from unimem_youtube.service import YoutubeCaptureService


def setup(tmp: Path) -> tuple[TestClient, ObsidianStore, str, str, str]:
    app = build_local_app(tmp, security=TEST_SECURITY)
    store = ObsidianStore(tmp / "obsidian-delivery.sqlite3")
    dest, token = store.create_destination("Main Obsidian")
    service = YoutubeCaptureService(tmp)
    result = service.capture(
        "https://www.youtube.com/watch?v=abcdefghijk", ("ru",), acquire=lambda *_: artifact()
    )
    return (
        TestClient(app, base_url="http://127.0.0.1:8765", headers=AUTH_HEADERS),
        store,
        dest.destination_id,
        token,
        result.source.capture_id,
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def claim() -> ClaimRequest:
    return ClaimRequest(receiver_id=str(uuid4()), claim_id=str(uuid4()))


def expire(store: ObsidianStore, d: AnyDelivery) -> None:
    old = d.model_copy(update={"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    with store.connection() as db:
        db.execute(
            "UPDATE obsidian_deliveries SET payload=? WHERE id=?",
            (old.model_dump_json(), d.delivery_id),
        )


def test_browser_submit_is_durable_idempotent_immutable_and_capture_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store, dest, token, capture = setup(tmp_path)
    with client:
        before = client.get(f"/v1/captures/{capture}").json()
        request = {"destination_id": dest, "source_capture_id": capture}
        assert (
            client.get(f"/v1/destinations/{dest}/captures/{capture}/delivery").json()["delivery"]
            is None
        )
        with ThreadPoolExecutor(4) as pool:
            replies = list(
                pool.map(lambda _: client.post("/v1/deliveries", json=request), range(4))
            )
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 202]
        delivery_id = replies[0].json()["delivery_id"]
        assert {r.json()["delivery_id"] for r in replies} == {delivery_id}
        assert "markdown" not in replies[0].json()
        fresh = ObsidianStore(store.database).get(dest, delivery_id)
        assert fresh.state == "pending"
        assert fresh.markdown_sha256 == hashlib.sha256(fresh.markdown.encode()).hexdigest()
        monkeypatch.setattr(CaptionMarkdownRenderer, "render", lambda *_: "changed renderer")
        assert client.post("/v1/deliveries", json=request).json() == replies[0].json()
        assert store.get(dest, delivery_id) == fresh
        assert client.get(f"/v1/captures/{capture}").json() == before
        receiver = client.get("/v1/receiver/deliveries/next", headers=auth(token)).json()
        assert receiver == fresh.model_dump(mode="json")


def test_auth_scopes_destination_isolation_and_safe_errors(tmp_path: Path) -> None:
    client, store, dest, token, capture = setup(tmp_path)
    other, other_token = store.create_destination("Other")
    with client:
        accepted = client.post(
            "/v1/deliveries", json={"destination_id": dest, "source_capture_id": capture}
        ).json()
        path = f"/v1/receiver/deliveries/{accepted['delivery_id']}"
        for method, route in (
            ("POST", "/v1/captures"),
            ("POST", "/v1/uploads"),
            ("POST", "/v1/youtube/operations"),
            ("POST", "/v1/deliveries"),
            ("GET", "/v1/destinations"),
            ("GET", f"/v1/captures/{capture}"),
            ("GET", "/docs"),
        ):
            assert (
                client.request(method, route, headers=auth(token), content=b"not json").status_code
                == 401
            )
        for route in ("/v1/receiver/destination", "/v1/receiver/deliveries/next", path):
            assert client.get(route).status_code == 401
        for suffix in ("claim", "ack", "fail"):
            assert client.post(path + "/" + suffix, json={}).status_code == 401
            assert (
                client.post(
                    path + "/" + suffix,
                    headers=auth(other_token),
                    json={
                        **claim().model_dump(),
                        **(
                            {"markdown_sha256": accepted["markdown_sha256"]}
                            if suffix == "ack"
                            else {"error_code": "write_failed"}
                            if suffix == "fail"
                            else {}
                        ),
                    },
                ).status_code
                == 404
            )
        assert client.get(path, headers=auth(other_token)).status_code == 404
        assert client.get("/v1/receiver/deliveries/next", headers=auth(other_token)).json() is None
        assert (
            client.get("/v1/receiver/destination", headers=auth(other_token)).json()[
                "destination_id"
            ]
            == other.destination_id
        )
        assert (
            client.get(
                path, headers=auth(token) | {"Origin": "moz-extension://" + str(uuid4())}
            ).status_code
            == 401
        )
        assert client.get(path + "?token=" + token, headers=auth(token)).status_code == 400
        for response in (
            client.get("/v1/destinations"),
            client.get(path, headers=auth(token)),
            client.get("/openapi.json"),
        ):
            assert token not in response.text
            assert other_token not in response.text
            assert TEST_TOKEN not in response.text
        assert token.encode() not in store.database.read_bytes()


def test_claim_lease_restart_ack_loss_and_second_installation(tmp_path: Path) -> None:
    client, store, dest, token, capture = setup(tmp_path)
    with client:
        d = client.post(
            "/v1/deliveries", json={"destination_id": dest, "source_capture_id": capture}
        ).json()
        path = f"/v1/receiver/deliveries/{d['delivery_id']}"
        owner = claim()
        claimed = client.post(path + "/claim", headers=auth(token), json=owner.model_dump())
        assert claimed.status_code == 200
        assert (
            client.post(path + "/claim", headers=auth(token), json=owner.model_dump()).json()
            == claimed.json()
        )
        assert client.get("/v1/receiver/deliveries/next", headers=auth(token)).json() is None
        rival = claim()
        assert (
            client.post(path + "/claim", headers=auth(token), json=rival.model_dump()).json()[
                "error"
            ]["code"]
            == "receiver_mismatch"
        )
        replacement = ClaimRequest(receiver_id=owner.receiver_id, claim_id=str(uuid4()))
        assert (
            client.post(path + "/claim", headers=auth(token), json=replacement.model_dump()).json()[
                "error"
            ]["code"]
            == "lease_active"
        )
        fresh = ObsidianStore(store.database)
        saved = fresh.get(dest, d["delivery_id"])
        expire(fresh, saved)
        ack = AckRequest(**owner.model_dump(), markdown_sha256=saved.markdown_sha256)
        with pytest.raises(DeliveryError, match="lease_expired"):
            fresh.finish(dest, saved.delivery_id, ack)
        assert fresh.next(dest) is not None
        with pytest.raises(DeliveryError, match="receiver_mismatch"):
            fresh.claim(dest, saved.delivery_id, rival)
        fresh.claim(dest, saved.delivery_id, replacement)
        with pytest.raises(DeliveryError, match="claim_mismatch"):
            fresh.finish(dest, saved.delivery_id, ack)
        ack = AckRequest(**replacement.model_dump(), markdown_sha256=saved.markdown_sha256)
        with pytest.raises(DeliveryError, match="digest_mismatch"):
            fresh.finish(
                dest, saved.delivery_id, ack.model_copy(update={"markdown_sha256": "0" * 64})
            )
        imported = fresh.finish(dest, saved.delivery_id, ack)  # Discard first response.
        assert imported.state == "imported"
        assert ObsidianStore(store.database).finish(dest, saved.delivery_id, ack) == imported
        assert fresh.next(dest) is None
        assert client.get(path, headers=auth(token)).json()["state"] == "imported"


@pytest.mark.parametrize(
    ("code", "state"),
    [("file_exists", "conflict"), ("write_ambiguous", "ambiguous"), ("digest_mismatch", "failed")],
)
def test_safe_failure_receipts(tmp_path: Path, code: Any, state: str) -> None:
    client, store, dest, token, capture = setup(tmp_path)
    d, _ = store.register(dest, capture, "content", "Markdown", "markdown", "0.1")
    owner = claim()
    store.claim(dest, d.delivery_id, owner)
    fail = FailureRequest(**owner.model_dump(), error_code=code)
    assert store.finish(dest, d.delivery_id, fail).state == state
    assert store.finish(dest, d.delivery_id, fail).state == state
    with pytest.raises(DeliveryError, match="delivery_terminal"):
        store.finish(
            dest, d.delivery_id, AckRequest(**owner.model_dump(), markdown_sha256=d.markdown_sha256)
        )
    with client:
        r = client.post(
            f"/v1/receiver/deliveries/{d.delivery_id}/fail",
            headers=auth(token),
            json={**owner.model_dump(), "error_code": "secret local path"},
        )
        assert r.status_code == 422
        assert "secret local path" not in r.text


def test_size_capacity_persistence_failure_and_operator_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--data-dir", str(tmp_path), "--create-destination", "Main Obsidian"]) == 0
    issued = json.loads(capsys.readouterr().out)
    store = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3", limit=1)
    dest = issued["destination_id"]
    assert store.authenticate(issued["receiver_token"]) == dest
    with pytest.raises(DeliveryError, match="markdown_too_large"):
        store.register(dest, "big", "content", "я" * MAX_MARKDOWN_BYTES, "markdown", "0.1")
    d, _ = store.register(dest, "ok", "content", "x" * MAX_MARKDOWN_BYTES, "markdown", "0.1")
    assert store.register(dest, "ok", "content", "changed", "markdown", "0.1")[0] == d
    with pytest.raises(DeliveryError, match="delivery_history_full"):
        store.register(dest, "full", "content", "text", "markdown", "0.1")
    assert store.database.stat().st_mode & 0o077 == 0
    store.database = tmp_path / "missing" / "db"
    with pytest.raises(DeliveryError, match="delivery_storage_unavailable"):
        store.register(dest, "absent", "content", "text", "markdown", "0.1")


def test_shared_wire_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).parents[3] / "fixtures/obsidian-delivery-v1.json").read_text()
    )
    d = Delivery.model_validate(fixture["delivery"])
    assert hashlib.sha256(d.markdown.encode()).hexdigest() == d.markdown_sha256
    for state in fixture["states"]:
        Delivery.model_validate(fixture["delivery"] | {"state": state})
    for code in fixture["failure_codes"]:
        FailureRequest(
            receiver_id=fixture["receiver_id"], claim_id=fixture["claim_id"], error_code=code
        )
    for value in fixture["invalid_ids"]:
        with pytest.raises(ValidationError):
            ClaimRequest(receiver_id=value, claim_id=fixture["claim_id"])


def test_http_size_and_commit_failure_never_accept_or_change_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store, dest, _, capture = setup(tmp_path)
    body = {"destination_id": dest, "source_capture_id": capture}
    with client:
        before = client.get(f"/v1/captures/{capture}").json()
        with monkeypatch.context() as patch:
            patch.setattr(
                CaptionMarkdownRenderer, "render", lambda *_: "x" * (MAX_MARKDOWN_BYTES + 1)
            )
            assert client.post("/v1/deliveries", json=body).status_code == 413
        assert store.find(dest, capture) is None
        with store.connection() as db:
            db.execute(
                "CREATE TRIGGER refuse BEFORE INSERT ON obsidian_deliveries "
                "BEGIN SELECT RAISE(ABORT, 'private backend diagnostic'); END"
            )
        response = client.post("/v1/deliveries", json=body)
        assert response.status_code == 503
        assert "private backend" not in response.text
        assert store.find(dest, capture) is None
        assert client.get(f"/v1/captures/{capture}").json() == before


def test_concurrent_receivers_only_one_installation_can_claim(tmp_path: Path) -> None:
    _, store, dest, _, capture = setup(tmp_path)
    d, _ = store.register(dest, capture, "content", "text", "markdown", "0.1")

    def attempt(_: int) -> str:
        try:
            store.claim(dest, d.delivery_id, claim())
            return "claimed"
        except DeliveryError as exc:
            return exc.code

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert results.count("claimed") == 1
    assert results.count("receiver_mismatch") == 3


def test_receiver_credential_io_reserves_ingress_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ObsidianStore(tmp_path / "db")
    entered, release = Event(), Event()

    def slow_auth(token: str) -> str:
        entered.set()
        assert release.wait(5)
        return str(uuid4())

    monkeypatch.setattr(store, "authenticate", slow_auth)

    async def scenario() -> None:
        replies: list[Message] = []

        async def application(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            return {"type": "http.request", "body": b""}

        async def send(message: Message) -> None:
            replies.append(message)

        middleware = LocalApiSecurity(
            application, ApiSecurity(TEST_TOKEN, concurrent_requests=1), store
        )
        scope: Scope = {
            "type": "http",
            "path": "/v1/receiver/destination",
            "method": "GET",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"authorization", f"Bearer {TEST_TOKEN}".encode()),
            ],
        }
        first = asyncio.create_task(middleware(scope, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await middleware(scope, receive, send)
            assert replies[0]["status"] == 429
        finally:
            release.set()
            await first
        assert middleware.active == 0
        await middleware(scope, receive, send)
        assert [r["status"] for r in replies if r["type"] == "http.response.start"] == [
            429,
            200,
            200,
        ]

    asyncio.run(scenario())
