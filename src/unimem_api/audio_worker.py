"""One ASR child, with an independent real execution and memory budget (Linux)."""

import logging
import os
import resource
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal

from core.persistence import CaptureRecordNotFoundError
from core.processing.errors import ProcessingError
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.audio_operations import AudioOperationStore
from unimem_asr.input import inspect_input
from unimem_asr.policy import EXECUTION_SECONDS, MAX_RSS_BYTES, MEMORY_BYTES, AsrError
from unimem_asr.result import Transcript
from unimem_asr.service import AudioCaptureService

Recognizer = Callable[[BinaryIO, str, Literal["auto", "ru", "en"], Path], Transcript]


def execute_audio(
    data_dir: Path, operation_id: str, model_dir: Path, recognize: Recognizer | None = None
) -> None:
    if recognize is None:
        from unimem_asr.engine import transcribe

        recognize = transcribe
    store, service = AudioOperationStore(data_dir / "unimem.sqlite3"), AudioCaptureService(data_dir)
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
        with service.raw_store.open(raw_object_ref(parse_raw_ref(op.request.file_ref))) as stream:
            _, mime, _ = inspect_input(stream, op.request.declared_mime)
        content = service.capture(
            capture_id=op.reserved_capture_id,
            operation_id=op.request.operation_id,
            file_ref=op.request.file_ref,
            mime_type=mime,
            captured_at=op.request.captured_at,
            recognize=lambda stream: recognize(
                stream, op.request.declared_mime, op.request.language, model_dir
            ),
        )
        store.finish(op, "complete", capture_id=content.source.capture_id, content_id=content.id)
    except AsrError as exc:
        store.reconcile(op, service, error_code=exc.code, incomplete_state="failed")
    except MemoryError:
        store.reconcile(op, service, error_code="budget_exceeded", incomplete_state="failed")
    except (ProcessingError, ValueError):
        store.reconcile(op, service, error_code="invalid_result", incomplete_state="failed")
    except Exception:
        store.reconcile(op, service, error_code="execution_unavailable")


def resident_bytes(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except FileNotFoundError:
        return 0


class AudioWorker:
    def __init__(
        self,
        data_dir: Path,
        store: AudioOperationStore,
        lease_fd: int,
        model_dir: Path,
        *,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.data_dir, self.store, self.lease_fd = data_dir, store, lease_fd
        self.model_dir = model_dir
        self.command = command or (sys.executable, "-m", "unimem_api.audio_worker")
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="audio-transcription", daemon=True)

    def start(self) -> None:
        self.store.recover(AudioCaptureService(self.data_dir))
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join()

    def _run(self) -> None:
        service = AudioCaptureService(self.data_dir)
        while not self.stopping.is_set():
            try:
                op = self.store.claim()
                if op is None:
                    self.stopping.wait(0.2)
                    continue
                budget = False
                env = os.environ | {
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "OPENBLAS_NUM_THREADS": "2",
                    "OMP_NUM_THREADS": "2",
                }
                with subprocess.Popen(
                    [
                        *self.command,
                        str(self.data_dir),
                        op.request.operation_id,
                        str(self.model_dir),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(self.lease_fd,),
                    env=env,
                ) as child:
                    deadline = monotonic() + EXECUTION_SECONDS
                    while child.poll() is None:
                        budget = (
                            monotonic() >= deadline or resident_bytes(child.pid) > MAX_RSS_BYTES
                        )
                        if self.stopping.wait(0.1) or budget:
                            child.kill()
                            child.wait(timeout=5)
                            break
                budget = budget or child.returncode in (-signal.SIGALRM, -signal.SIGXCPU)
                self.store.reconcile(
                    op,
                    service,
                    error_code=("budget_exceeded" if budget else "execution_interrupted"),
                    incomplete_state="failed" if budget else "interrupted",
                )
            except Exception:
                with suppress(Exception):
                    self.store.recover(service)
                logging.getLogger(__name__).error("Audio worker stopped; restart required.")
                self.stopping.set()


def main() -> int:
    # Kernel default SIGALRM terminates even wedged native code and orphan workers.
    signal.alarm(EXECUTION_SECONDS)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (EXECUTION_SECONDS, EXECUTION_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        execute_audio(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
