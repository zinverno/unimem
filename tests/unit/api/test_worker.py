import subprocess
import sys
import time
from pathlib import Path
from typing import Never

import pytest
from fastapi.testclient import TestClient

from tests.api_auth import AUTH_HEADERS, TEST_SECURITY
from tests.youtube_fixtures import URL
from unimem_api import wiring, worker
from unimem_youtube.operations import OperationStore, YoutubeRequest


@pytest.mark.parametrize("mode", ["success", "blocked"])
def test_lifespan_runs_and_stops_the_owned_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(wiring, "require_youtube", lambda: None)
    app = wiring.build_local_app(
        tmp_path,
        security=TEST_SECURITY,
        youtube=True,
        worker_command=(sys.executable, "-m", "tests.delivery_process", "worker", mode),
    )
    store = OperationStore(tmp_path / "unimem.sqlite3")
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=AUTH_HEADERS) as client:
        assert (
            client.post(
                "/v1/youtube/operations",
                json={
                    "operation_id": "lifespan",
                    "url": URL,
                    "languages": ["ru"],
                },
            ).status_code
            == 202
        )
        deadline = time.monotonic() + 10
        while (
            store.get("lifespan").state != "complete"
            if mode == "success"
            else not (tmp_path / "acquisitions").exists()
        ):
            assert time.monotonic() < deadline
            time.sleep(0.03)
    assert store.get("lifespan").state == ("complete" if mode == "success" else "interrupted")
    assert (tmp_path / "acquisitions").read_text().splitlines() == ["lifespan"]


def test_spawn_failure_stops_worker_with_safe_durable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    store.register(YoutubeRequest(operation_id="spawn", url=URL))

    def fail(*args: object, **kwargs: object) -> Never:
        raise OSError("secret process details")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with worker.ServerLease(tmp_path) as lease:
        coordinator = worker.DeliveryWorker(tmp_path, store, lease.fd)
        coordinator.start()
        assert coordinator.stopping.wait(5)
        coordinator.stop()
    assert store.get("spawn").state == "interrupted"
    assert "restart required" in caplog.text
    assert "secret" not in caplog.text


def test_worker_budget_terminates_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    store.register(YoutubeRequest(operation_id="deadline", url=URL))
    monkeypatch.setattr(worker, "EXECUTION_SECONDS", 0.2)
    with worker.ServerLease(tmp_path) as lease:
        coordinator = worker.DeliveryWorker(
            tmp_path, store, lease.fd, command=(sys.executable, "-c", "import time; time.sleep(30)")
        )
        coordinator.start()
        deadline = time.monotonic() + 5
        while store.get("deadline").state != "interrupted":
            assert time.monotonic() < deadline
            time.sleep(0.03)
        coordinator.stop()
    assert store.claim() is None
