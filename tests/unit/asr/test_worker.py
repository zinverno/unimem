import os
import sys
import time
from pathlib import Path

import pytest

from tests.api_auth import TEST_SECURITY, AuthenticatedClient
from tests.unit.asr.test_operations import request
from tests.unit.asr.test_transcription import wav_bytes
from unimem_api import audio_worker
from unimem_api.audio_operations import AudioOperationStore
from unimem_api.wiring import build_local_app
from unimem_api.worker import ServerLease
from unimem_asr.service import AudioCaptureService


def pending(directory: Path) -> AudioOperationStore:
    raw = AudioCaptureService(directory).raw_store.store_bytes(wav_bytes())
    assert raw.ref is not None
    store = AudioOperationStore(directory / "unimem.sqlite3")
    store.register(request(raw.ref))
    return store


# A real CPU-bound child. No native engine/model is needed for process ownership tests.
CHILD = (
    sys.executable,
    "-c",
    "import os,sys; from pathlib import Path; "
    "Path(sys.argv[1], 'child-pid').write_text(str(os.getpid())); "
    "exec('while True: pass')",
)


def wait_file(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.parametrize("limit", ["wall", "rss", "shutdown"])
def test_budget_or_shutdown_kills_actual_cpu_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    store = pending(tmp_path)
    monkeypatch.setattr(audio_worker, "EXECUTION_SECONDS", 1 if limit == "wall" else 30)
    with ServerLease(tmp_path) as lease:
        worker = audio_worker.AudioWorker(tmp_path, store, lease.fd, tmp_path, command=CHILD)
        worker.start()
        try:
            wait_file(tmp_path / "child-pid")
            pid = int((tmp_path / "child-pid").read_text())
            if limit == "rss":
                monkeypatch.setattr(audio_worker, "MAX_RSS_BYTES", 1)
            if limit != "shutdown":
                deadline = time.monotonic() + 5
                while store.get("audio-one").state == "running":
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
        finally:
            worker.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    op = store.get("audio-one")
    assert op.state == ("interrupted" if limit == "shutdown" else "failed")
    assert op.error_code == ("execution_interrupted" if limit == "shutdown" else "budget_exceeded")
    assert store.claim() is None
    assert audio_worker.resident_bytes(pid) == 0


def test_lifespan_keeps_api_responsive_and_one_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("unimem_asr.model.require_engine", lambda: None)
    monkeypatch.setattr("unimem_asr.model.verify_model", lambda _: {})
    app = build_local_app(
        tmp_path, security=TEST_SECURITY, audio_model=tmp_path, audio_worker_command=CHILD
    )
    store = pending(tmp_path)
    with AuthenticatedClient(app) as client:
        wait_file(tmp_path / "child-pid")
        pid = int((tmp_path / "child-pid").read_text())
        start = time.monotonic()
        assert client.get("/health").status_code == 200
        assert client.get("/v1/audio/operations/audio-one").json()["state"] == "running"
        second = request(store.get("audio-one").request.file_ref, operation_id="queued")
        assert (
            client.post("/v1/audio/operations", json=second.model_dump(mode="json")).status_code
            == 202
        )
        assert client.get("/v1/audio/operations/queued").json()["state"] == "queued"
        assert int((tmp_path / "child-pid").read_text()) == pid
        assert time.monotonic() - start < 5
    assert store.get("audio-one").state == "interrupted"
    assert store.get("queued").state == "queued"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawn_failure_is_safe_and_durable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = pending(tmp_path)
    with ServerLease(tmp_path) as lease:
        worker = audio_worker.AudioWorker(
            tmp_path, store, lease.fd, tmp_path, command=(str(tmp_path / "missing-executable"),)
        )
        worker.start()
        assert worker.stopping.wait(5)
        worker.stop()
    assert store.get("audio-one").state == "interrupted"
    assert "restart required" in caplog.text
    assert str(tmp_path) not in caplog.text
