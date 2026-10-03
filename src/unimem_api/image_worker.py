"""One bounded image child, using existing processors and OCR adapter."""

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
from typing import TYPE_CHECKING, BinaryIO

from unimem_delivery.heavy import heavy_slot

if TYPE_CHECKING:
    from unimem_vision.engine import Description

from core.persistence import CaptureRecordNotFoundError
from core.processing.image_recognition import ImageOcr, ImageOcrExecutionError
from unimem_api.image_operations import ImageOperationStore
from unimem_api.image_service import IMAGE_MEMORY, IMAGE_SECONDS, ImageCaptureService, ImageError


def execute_image(
    data_dir: Path,
    operation_id: str,
    ocr: ImageOcr | None = None,
    describe: Callable[[BinaryIO], "Description"] | None = None,
) -> None:
    store, service = ImageOperationStore(data_dir / "unimem.sqlite3"), ImageCaptureService(data_dir)
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
        content = service.capture(op, ocr, describe)
        store.finish(op, "complete", capture_id=content.source.capture_id, content_id=content.id)
    except ImageError as exc:
        store.reconcile(op, service, error_code=exc.code, incomplete_state="failed")
    except ImageOcrExecutionError:
        store.reconcile(op, service, error_code="ocr_error", incomplete_state="failed")
    except MemoryError:
        store.reconcile(op, service, error_code="budget_exceeded", incomplete_state="failed")
    except Exception as exc:
        from unimem_vision.policy import VisionError

        if isinstance(exc, VisionError):
            store.reconcile(op, service, error_code=exc.code, incomplete_state="failed")
            return
        store.reconcile(op, service, error_code="execution_unavailable")


class ImageWorker:
    def __init__(
        self,
        data_dir: Path,
        store: ImageOperationStore,
        lease_fd: int,
        *,
        ocr_enabled: bool,
        command: tuple[str, ...] | None = None,
        description_profile: Path | None = None,
    ) -> None:
        self.data_dir, self.store, self.lease_fd = data_dir.resolve(), store, lease_fd
        self.ocr_enabled = ocr_enabled
        self.description_profile = description_profile.resolve() if description_profile else None
        self.command = command or (sys.executable, "-m", "unimem_api.image_worker")
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="image-capture", daemon=True)

    def start(self) -> None:
        self.store.recover(ImageCaptureService(self.data_dir))
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join()

    def _run(self) -> None:
        service = ImageCaptureService(self.data_dir)
        while not self.stopping.is_set():
            try:
                op = self.store.claim()
                if op is None:
                    self.stopping.wait(0.2)
                    continue
                with heavy_slot(
                    self.data_dir, self.stopping, required=op.request.mode == "describe"
                ) as slot:
                    if slot is None:
                        self.store.reconcile(op, service)
                        return
                    budget = False
                    with subprocess.Popen(
                        [
                            *self.command,
                            str(self.data_dir),
                            op.request.operation_id,
                            "ocr" if self.ocr_enabled else "original",
                            str(self.description_profile) if self.description_profile else "",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(self.lease_fd,) + ((slot,) if slot >= 0 else ()),
                        start_new_session=True,
                    ) as child:
                        from unimem_vision.policy import WORKER_SECONDS

                        deadline = monotonic() + (
                            WORKER_SECONDS if op.request.mode == "describe" else IMAGE_SECONDS
                        )
                        while child.poll() is None:
                            budget = monotonic() >= deadline
                            if self.stopping.wait(0.1) or budget:
                                os.killpg(child.pid, signal.SIGKILL)
                                child.wait(timeout=5)
                                break
                        # A timed-out/crashed worker may have left its Tesseract child.
                        with suppress(ProcessLookupError):
                            os.killpg(child.pid, signal.SIGKILL)
                    budget = budget or child.returncode in (-signal.SIGALRM, -signal.SIGXCPU)
                    self.store.reconcile(
                        op,
                        service,
                        error_code="budget_exceeded" if budget else "execution_interrupted",
                        incomplete_state="failed" if budget else "interrupted",
                    )
            except Exception:
                with suppress(Exception):
                    self.store.recover(service)
                logging.getLogger(__name__).error("Image worker stopped; restart required.")
                self.stopping.set()


def main() -> int:
    op = ImageOperationStore(Path(sys.argv[1]) / "unimem.sqlite3").get(sys.argv[2])
    from unimem_vision.policy import MEMORY, WORKER_SECONDS

    seconds = WORKER_SECONDS if op.request.mode == "describe" else IMAGE_SECONDS
    memory = MEMORY if op.request.mode == "describe" else IMAGE_MEMORY
    signal.alarm(seconds)
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds + 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        ocr = None
        op = ImageOperationStore(Path(sys.argv[1]) / "unimem.sqlite3").get(sys.argv[2])
        if sys.argv[3] == "ocr" and op.request.mode == "ocr":
            from unimem_api.__main__ import build_image_ocr

            ocr = build_image_ocr()
        describe_image = None
        if op.request.mode == "describe" and len(sys.argv) > 4 and sys.argv[4]:
            from unimem_vision.engine import describe

            def describe_image(stream: BinaryIO) -> "Description":
                return describe(stream, Path(sys.argv[4]))

        execute_image(Path(sys.argv[1]), sys.argv[2], ocr, describe_image)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
