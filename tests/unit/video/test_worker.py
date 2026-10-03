"""Real processes and real shared slot; no ASR/vision weights or network."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.unit.asr.test_worker import CHILD, wait_file
from tests.unit.video.test_pipeline import staged
from unimem_api.worker import ServerLease
from unimem_delivery.heavy import heavy_slot
from unimem_video import worker


@pytest.mark.parametrize("reason", ["timeout", "memory", "shutdown"])
def test_kill_reap_release_and_no_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    service, store, request = staged(tmp_path, speech=False, describe=False)
    store.register(request)
    monkeypatch.setattr(worker, "TOTAL_SECONDS", 1 if reason == "timeout" else 30)
    with ServerLease(tmp_path) as lease:
        runner = worker.VideoWorker(tmp_path, store, lease.fd, None, None, command=CHILD)
        runner.start()
        try:
            wait_file(tmp_path / "child-pid")
            pid = int((tmp_path / "child-pid").read_text())
            if reason == "memory":
                monkeypatch.setattr(worker, "MAX_RSS_BYTES", 1)
            if reason != "shutdown":
                deadline = time.monotonic() + 5
                while store.get(request.operation_id).state == "running":
                    assert time.monotonic() < deadline
                    time.sleep(0.03)
        finally:
            runner.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert store.get(request.operation_id).state == (
        "interrupted" if reason == "shutdown" else "failed"
    )
    assert store.claim() is None
    store.recover(service)
    with heavy_slot(tmp_path, threading.Event()) as slot:
        assert slot is not None


def test_actual_video_child_completes_visual_only_and_records_resources(tmp_path: Path) -> None:
    _, store, request = staged(tmp_path, speech=False, describe=False, audio=False)
    store.register(request)
    with ServerLease(tmp_path) as lease:
        runner = worker.VideoWorker(tmp_path, store, lease.fd, None, None)
        runner.start()
        try:
            deadline = time.monotonic() + 20
            while store.get(request.operation_id).state in {"queued", "running"}:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            runner.stop()
    observed = store.get(request.operation_id)
    assert observed.state == "complete", observed.error_code
    facts = observed.model_dump()
    assert facts["peak_process_tree_rss_bytes"] > 0
    assert 0 < facts["execution_seconds"] < 20


@pytest.mark.parametrize("death", [signal.SIGTERM, signal.SIGKILL])
def test_kernel_parent_death_fence_stops_inference_descendant(
    tmp_path: Path, death: signal.Signals
) -> None:
    child_path = tmp_path / "descendant.py"
    pid_path = tmp_path / "descendant.pid"
    child_path.write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "from unimem_video.worker import parent_death\n"
        "parent_death(int(sys.argv[1]))\n"
        f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    # This surrogate owner starts the same kernel fence that video and PCM children use.
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,subprocess,sys,time; "
            "subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid())]); time.sleep(60)",
            str(child_path),
        ]
    )
    try:
        wait_file(pid_path)
        pid = int(pid_path.read_text())
        owner.send_signal(death)
        owner.wait(timeout=5)
        deadline = time.monotonic() + 5
        while Path(f"/proc/{pid}/stat").exists():
            # Reaping of a reparented orphan belongs to host init; a zombie has no
            # running inference, memory or open lease/slot descriptors.
            if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)


def test_waiting_for_other_heavy_work_does_not_deadlock_shutdown(tmp_path: Path) -> None:
    _, store, request = staged(tmp_path, speech=False, describe=False)
    store.register(request)
    with heavy_slot(tmp_path, threading.Event()), ServerLease(tmp_path) as lease:
        runner = worker.VideoWorker(tmp_path, store, lease.fd, None, None)
        runner.start()
        deadline = time.monotonic() + 5
        while store.get(request.operation_id).state != "running":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        runner.stop()
    assert store.get(request.operation_id).state == "interrupted"


@pytest.mark.parametrize("death", [signal.SIGTERM, signal.SIGKILL])
def test_api_process_death_stops_child_tree_and_recovery_never_reexecutes(
    tmp_path: Path, death: signal.Signals
) -> None:
    """Real uvicorn and kernel fences; deliberately synthetic busy inference."""
    service, store, request = staged(tmp_path, speech=False, describe=False)
    store.register(request)
    child = tmp_path / "busy.py"
    child.write_text(
        "import os,sys,subprocess,time\nfrom pathlib import Path\n"
        "from unimem_video.worker import parent_death\n"
        "parent_death(int(sys.argv[-1]))\n"
        "root=Path(sys.argv[1])\n"
        "if sys.argv[2]=='descendant':\n"
        " (root/'descendant-pid').write_text(str(os.getpid()))\n"
        "else:\n"
        " subprocess.Popen([sys.executable,__file__,str(root),'descendant',str(os.getpid())])\n"
        " (root/'child-pid').write_text(str(os.getpid()))\n"
        "while True: time.sleep(.05)\n"
    )
    server = tmp_path / "server.py"
    server.write_text(
        "from pathlib import Path\nimport sys,uvicorn\n"
        "from unimem_api.wiring import build_local_app\n"
        "from unimem_api.security import ApiSecurity\n"
        f"app=build_local_app(Path({str(tmp_path)!r}),security=ApiSecurity('a'*43),video_notes=True,"
        f"video_worker_command=(sys.executable,{str(child)!r}))\n"
        "uvicorn.run(app,host='127.0.0.1',port=0,log_level='error')\n"
    )
    owner = subprocess.Popen([sys.executable, str(server)])
    try:
        wait_file(tmp_path / "child-pid")
        wait_file(tmp_path / "descendant-pid")
        pids = [int((tmp_path / name).read_text()) for name in ("child-pid", "descendant-pid")]
        owner.send_signal(death)
        owner.wait(timeout=10)
        deadline = time.monotonic() + 5
        for pid in pids:
            while Path(f"/proc/{pid}/stat").exists():
                if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
        store.recover(service)
        assert store.get(request.operation_id).state == "interrupted"
        assert store.claim() is None
        with ServerLease(tmp_path), heavy_slot(tmp_path, threading.Event()) as slot:
            assert slot is not None
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
