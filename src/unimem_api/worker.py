"""One durable queue coordinator and one bounded child at a time (Linux)."""

import fcntl
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from time import monotonic

from core.persistence import CaptureRecordNotFoundError
from unimem_youtube.artifact import CaptionArtifact
from unimem_youtube.errors import AcquisitionError, CapturePipelineError
from unimem_youtube.operations import OperationStore
from unimem_youtube.service import YoutubeCaptureService, acquire_captions

EXECUTION_SECONDS = 75


class ServerLease:
    """An OS lease, shared with any running child until it exits.

    A killed parent cannot allow a second server to start alongside its orphan.
    Never unlink the lock file: that would let two processes lock different inodes.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / ".api-server.lock"
        self.fd = -1

    def __enter__(self) -> "ServerLease":
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = -1
            raise RuntimeError(
                "Another API process or its worker owns this data directory."
            ) from None
        return self

    def __exit__(self, *args: object) -> None:
        os.close(self.fd)
        self.fd = -1


def execute_operation(
    data_dir: Path,
    operation_id: str,
    *,
    acquire: Callable[[str, tuple[str, ...]], CaptionArtifact] = acquire_captions,
) -> None:
    store = OperationStore(data_dir / "unimem.sqlite3")
    service = YoutubeCaptureService(data_dir)
    op = store.get(operation_id)
    if op.state != "running":
        return
    try:
        service.record_store.get(op.reserved_capture_id)
    except CaptureRecordNotFoundError:
        pass
    else:
        store.reconcile(op, service)
        return
    try:
        content = service.capture(
            op.request.url, op.request.languages, acquire=acquire, capture_id=op.reserved_capture_id
        )
        store.finish(op, "complete", capture_id=content.source.capture_id, content_id=content.id)
    except AcquisitionError as exc:
        store.finish(op, "failed", error_code=exc.code)
    except CapturePipelineError as exc:
        # Inspect existing owners, never repair CaptureRecord or repeat work.
        store.reconcile(op, service, error_code=exc.code)
    except Exception:
        # The coordinator reconciles any unchanged running record after exit.
        # No upstream text, content or traceback is logged by this process.
        store.reconcile(op, service, error_code="execution_unavailable")


class DeliveryWorker:
    def __init__(
        self,
        data_dir: Path,
        store: OperationStore,
        lease_fd: int,
        *,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.data_dir, self.store, self.lease_fd = data_dir, store, lease_fd
        self.command = command or (sys.executable, "-m", "unimem_api.worker")
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="youtube-delivery", daemon=True)

    def start(self) -> None:
        self.store.recover(YoutubeCaptureService(self.data_dir))
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join()  # Child termination below never waits on retrieval.

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                op = self.store.claim()
                if op is None:
                    self.stopping.wait(0.2)
                    continue
                with subprocess.Popen(
                    [*self.command, str(self.data_dir), op.request.operation_id],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(self.lease_fd,),
                ) as child:
                    deadline = monotonic() + EXECUTION_SECONDS
                    while child.poll() is None:
                        if self.stopping.wait(0.1) or monotonic() >= deadline:
                            child.kill()
                            child.wait(timeout=5)
                            break
                self.store.recover(YoutubeCaptureService(self.data_dir))
            except Exception:
                # The child context has exited. Reconcile if storage permits;
                # otherwise retain the evidence for startup recovery.
                with suppress(Exception):
                    self.store.recover(YoutubeCaptureService(self.data_dir))
                logging.getLogger(__name__).error(
                    "YouTube worker stopped after an infrastructure failure; restart required."
                )
                self.stopping.set()


def main() -> int:
    # Also bounds an orphan after SIGKILL of its parent; no network retry.
    signal.alarm(EXECUTION_SECONDS)
    try:
        execute_operation(Path(sys.argv[1]), sys.argv[2])
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
