import json
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tests.api_auth import AUTH_HEADERS, TEST_TOKEN
from tests.youtube_fixtures import URL
from unimem_api.worker import ServerLease

ROUTE = "/v1/youtube/operations"


@contextmanager
def server(
    data: Path, mode: str = "success", *, port: int = 0
) -> Iterator[tuple[httpx2.Client, subprocess.Popen[str]]]:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.delivery_process", "server", mode, str(data), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with httpx2.Client(
        base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10
    ) as client:
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, process.communicate()[1]
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.05)
            yield client, process
        finally:
            if process.poll() is None:
                process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            assert TEST_TOKEN not in stdout + stderr
            assert "secret upstream detail" not in stdout + stderr


def body(operation_id: str) -> dict[str, Any]:
    return {"operation_id": operation_id, "url": URL, "languages": ["ru", "en"]}


def wait_for(client: httpx2.Client, operation_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 12
    while True:
        response = client.get(f"{ROUTE}/{operation_id}")
        if response.status_code == 200:
            found: dict[str, Any] = response.json()
            if found["state"] in states:
                return found
        assert time.monotonic() < deadline, response.text
        time.sleep(0.05)


def count_captures(data: Path) -> int:
    with sqlite3.connect(data / "unimem.sqlite3") as db:
        count = int(db.execute("SELECT count(*) FROM capture_records").fetchone()[0])
    db.close()
    return count


def test_lost_response_disconnect_replay_restart_and_offline_result(tmp_path: Path) -> None:
    with server(tmp_path) as (client, _):
        payload = json.dumps(body("known-id")).encode()
        # Send a real HTTP request, then close before reading any response.
        port = client.base_url.port
        assert port is not None
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            headers = (
                f"POST {ROUTE} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {TEST_TOKEN}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            )
            connection.sendall(headers.encode() + payload)
        complete = wait_for(client, "known-id", {"complete"})
        assert complete["capture_id"]
        assert complete["content_id"]
        replay = client.post(ROUTE, json=body("known-id"))
        assert replay.status_code == 200
        assert replay.json() == complete
        assert (
            client.post(ROUTE, json=body("known-id") | {"languages": ["en", "ru"]}).status_code
            == 409
        )
        text = client.get(f"{ROUTE}/known-id/markdown").text
        content = client.get(f"{ROUTE}/known-id/content").json()
        assert content["id"] == complete["content_id"]
        assert "Привет" in text
    with server(tmp_path, "timeout") as (client, _):
        assert client.get(f"{ROUTE}/known-id").json() == complete
        assert client.get(f"{ROUTE}/known-id/markdown").text == text
        assert client.post(ROUTE, json=body("known-id")).status_code == 200
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["known-id"]
    assert count_captures(tmp_path) == 1


def test_content_commit_before_operation_commit_is_reconciled(tmp_path: Path) -> None:
    with server(tmp_path, "crash-after-content") as (client, _):
        assert client.post(ROUTE, json=body("crash")).status_code == 202
        result = wait_for(client, "crash", {"complete"})
        assert result["result_available"] is True
        assert client.get(f"{ROUTE}/crash/markdown").status_code == 200
    assert count_captures(tmp_path) == 1
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["crash"]


@pytest.mark.parametrize("kill", [False, True])
def test_running_interrupted_queued_survives_and_single_process_is_enforced(
    tmp_path: Path, kill: bool
) -> None:
    with server(tmp_path, "blocked") as (client, process):
        assert client.post(ROUTE, json=body("running")).status_code == 202
        wait_for(client, "running", {"running"})
        deadline = time.monotonic() + 10
        while not (tmp_path / "acquisitions").exists():
            assert time.monotonic() < deadline
            time.sleep(0.03)
        assert client.post(ROUTE, json=body("queued")).status_code == 202
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.delivery_process",
                "server",
                "success",
                str(tmp_path),
                "8764",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert second.returncode != 0
        assert "Another API process" in second.stderr
        if kill:
            process.kill()
            process.wait(timeout=5)
            with pytest.raises(RuntimeError, match="Another API process"), ServerLease(tmp_path):
                pytest.fail("The orphan still owns the lease")
            # The orphan holds the inherited lease until its bounded lifetime ends.
            time.sleep(3.2)
    with server(tmp_path) as (client, _):
        interrupted = wait_for(client, "running", {"interrupted"})
        assert interrupted["capture_id"] is None
        assert interrupted["result_available"] is False
        assert interrupted["next_action"]
        assert client.post(ROUTE, json=body("running")).json()["state"] == "interrupted"
        wait_for(client, "queued", {"complete"})
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["running", "queued"]
    assert count_captures(tmp_path) == 1


@pytest.mark.parametrize("code", ["captions_unavailable", "language_unavailable", "timeout"])
def test_safe_acquisition_errors_are_durable_and_not_retried(tmp_path: Path, code: str) -> None:
    with server(tmp_path, code) as (client, _):
        assert client.post(ROUTE, json=body("failure")).status_code == 202
        failed = wait_for(client, "failure", {"failed"})
        assert failed["error_code"] == code
        assert failed["capture_id"] is None
        assert client.get(f"{ROUTE}/failure/markdown").status_code == 409
        assert client.post(ROUTE, json=body("failure")).json() == failed
    assert count_captures(tmp_path) == 0
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["failure"]


def test_concurrent_tcp_delivery_starts_only_one_capture(tmp_path: Path) -> None:
    with server(tmp_path) as (client, _):
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: client.post(ROUTE, json=body("same")), range(6)))
        assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 200, 202]
        wait_for(client, "same", {"complete"})
    assert count_captures(tmp_path) == 1
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["same"]


def test_tcp_security_and_streaming_limits_before_parsing(tmp_path: Path) -> None:
    with server(tmp_path) as (client, _):
        assert (
            client.post(ROUTE, json=body("unauthorized"), headers={"Authorization": ""}).status_code
            == 401
        )
        assert client.get("/health", headers={"Host": "rebound.example"}).status_code == 400
        assert (
            client.post(
                ROUTE, json=body("web"), headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.post(ROUTE, content=b"x" * 2049).status_code == 413
        assert client.post(ROUTE, content=iter([b"x" * 1024] * 3)).status_code == 413
        assert client.post("/v1/uploads", files={"file": ("large", b"x" * 4097)}).status_code == 413
        boundary = b'--upload\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n'
        assert (
            client.post(
                "/v1/uploads",
                content=iter([boundary, b"x" * 5000, b"\r\n--upload--\r\n"]),
                headers={"Content-Type": "multipart/form-data; boundary=upload"},
            ).status_code
            == 413
        )
        port = client.base_url.port
        assert port is not None
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall(
                (
                    f"POST {ROUTE} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Authorization: Bearer {TEST_TOKEN}\r\nContent-Length: 10\r\n"
                    "Content-Type: application/json\r\nConnection: close\r\n\r\n{"
                ).encode()
            )
            # No remaining body is sent; the socket must settle within its budget.
            assert b"400" in connection.recv(4096).split(b"\r\n", 1)[0]
    assert not (tmp_path / "acquisitions").exists()
    assert count_captures(tmp_path) == 0
