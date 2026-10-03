"""Sequential stages in one heavy slot. Each native process has finite lifetime."""

import ctypes
import json
import logging
import os
import resource
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from core.persistence import CaptureRecordNotFoundError
from unimem_asr.policy import AsrError
from unimem_asr.result import Transcript
from unimem_delivery.heavy import heavy_slot
from unimem_video.decode import DecodedVideo
from unimem_video.operations import Stage, VideoOperation, VideoOperationStore, VideoRequest
from unimem_video.policy import (
    ASR_SECONDS,
    DECODE_SECONDS,
    FRAME_SECONDS,
    MAX_RSS_BYTES,
    MEMORY_BYTES,
    TOTAL_SECONDS,
    VideoError,
)
from unimem_video.service import Describer, Recognizer, VideoCaptureService


def parent_death(expected_parent: int) -> None:
    """Linux kernel fence, including API SIGKILL. Check the setup race as well."""
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise VideoError("process_fence_unavailable")
    if os.getppid() != expected_parent:
        raise VideoError("execution_interrupted")


def recognize_pcm(
    path: Path, decoded: DecodedVideo, request: VideoRequest, model: Path
) -> Transcript:
    assert decoded.audio is not None
    facts = path.with_suffix(".json")
    answer = path.with_suffix(".result.json")
    facts.write_text(json.dumps(decoded.audio))
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "unimem_video.worker",
            "pcm",
            str(path),
            str(model),
            request.language,
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as child:
        try:
            child.wait(timeout=ASR_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
            raise VideoError("asr_budget_exceeded") from None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    if child.returncode != 0 or not answer.is_file() or answer.stat().st_size > 4 * 1024**2:
        raise VideoError("asr_execution_failed")
    payload = json.loads(answer.read_text())
    if "error" in payload:
        raise VideoError(str(payload["error"]))
    return Transcript.model_validate(payload)


def execute_video(
    data_dir: Path,
    operation_id: str,
    recognize: Recognizer | None = None,
    describe: Describer | None = None,
    *,
    alarms: bool = False,
) -> None:
    store, service = VideoOperationStore(data_dir / "unimem.sqlite3"), VideoCaptureService(data_dir)
    op = cast(VideoOperation, store.get(operation_id))
    if op.state != "running":
        return
    try:
        service.record_store.get(op.reserved_capture_id)
    except CaptureRecordNotFoundError:
        pass
    else:
        store.reconcile(op, service)
        return

    def stage(value: Stage) -> None:
        store.stage(operation_id, value)
        if alarms:
            signal.alarm(
                DECODE_SECONDS
                if value == "extracting"
                else ASR_SECONDS + 10
                if value == "transcribing"
                else 30
                if value == "saving"
                else FRAME_SECONDS
            )

    try:
        content = service.capture(op, stage, recognize, describe)
        store.finish(op, "complete", capture_id=content.source.capture_id, content_id=content.id)
    except Exception as exc:
        from unimem_vision.policy import VisionError

        if isinstance(exc, (VideoError, AsrError, VisionError)):
            store.reconcile(op, service, error_code=exc.code, incomplete_state="failed")
        elif isinstance(exc, MemoryError):
            store.reconcile(op, service, error_code="budget_exceeded", incomplete_state="failed")
        else:
            store.reconcile(op, service, error_code="execution_unavailable")


class VideoWorker:
    def __init__(
        self,
        data_dir: Path,
        store: VideoOperationStore,
        lease_fd: int,
        model: Path | None,
        profile: Path | None,
        *,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.data_dir, self.store, self.lease_fd = data_dir.resolve(), store, lease_fd
        self.model, self.profile = model, profile
        self.command = command or (sys.executable, "-m", "unimem_video.worker")
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="video-notes", daemon=True)

    def start(self) -> None:
        self.store.recover(VideoCaptureService(self.data_dir))
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.thread.join()

    def _run(self) -> None:
        from unimem_vision.engine import resident_tree

        service = VideoCaptureService(self.data_dir)
        while not self.stopping.is_set():
            try:
                op = self.store.claim()
                if op is None:
                    self.stopping.wait(0.2)
                    continue
                # Sole owner: nested ASR/vision adapters never acquire this slot.
                with heavy_slot(self.data_dir, self.stopping) as slot:
                    if slot is None:
                        self.store.reconcile(op, service)
                        return
                    budget = False
                    began, peak = monotonic(), 0
                    with subprocess.Popen(
                        [
                            *self.command,
                            str(self.data_dir),
                            op.request.operation_id,
                            str(self.model.resolve()) if self.model else "",
                            str(self.profile.resolve()) if self.profile else "",
                            str(os.getpid()),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(self.lease_fd, slot),
                        start_new_session=True,
                        env=os.environ
                        | {
                            "HF_HUB_OFFLINE": "1",
                            "HF_HUB_DISABLE_TELEMETRY": "1",
                            "OPENBLAS_NUM_THREADS": "2",
                            "OMP_NUM_THREADS": "2",
                            "TOKENIZERS_PARALLELISM": "false",
                        },
                    ) as child:
                        deadline = monotonic() + TOTAL_SECONDS
                        try:
                            while child.poll() is None:
                                peak = max(peak, resident_tree(child.pid))
                                budget = monotonic() >= deadline or peak > MAX_RSS_BYTES
                                if self.stopping.wait(0.1) or budget:
                                    break
                        finally:
                            with suppress(ProcessLookupError):
                                os.killpg(child.pid, signal.SIGKILL)
                            child.wait(timeout=5)
                    budget = budget or child.returncode in (-signal.SIGALRM, -signal.SIGXCPU)
                    self.store.resources(op.request.operation_id, monotonic() - began, peak)
                    self.store.reconcile(
                        op,
                        service,
                        error_code="budget_exceeded" if budget else "execution_interrupted",
                        incomplete_state="failed" if budget else "interrupted",
                    )
            except Exception:
                with suppress(Exception):
                    self.store.recover(service)
                logging.getLogger(__name__).error("Video worker stopped; restart required.")
                self.stopping.set()


def main() -> int:
    try:
        parent_death(int(sys.argv[-1]))
        signal.alarm(ASR_SECONDS if sys.argv[1] == "pcm" else TOTAL_SECONDS)
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (TOTAL_SECONDS, TOTAL_SECONDS + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if sys.argv[1] == "pcm":
            import numpy as np

            from unimem_asr.engine import transcribe_pcm

            path = Path(sys.argv[2])
            if not 0 < path.stat().st_size <= 120 * 16000 * 2 or path.stat().st_size % 2:
                raise VideoError("duration_limit")
            facts = json.loads(path.with_suffix(".json").read_text())
            audio = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
            try:
                result = transcribe_pcm(
                    audio,
                    cast(Literal["auto", "ru", "en"], sys.argv[4]),
                    Path(sys.argv[3]),
                    container="mp4",
                    codec="aac",
                    sample_rate=facts["sample_rate"],
                    channels=facts["channels"],
                    duration=len(audio) / 16000,
                )
                path.with_suffix(".result.json").write_text(result.model_dump_json())
            except AsrError as exc:
                path.with_suffix(".result.json").write_text(json.dumps({"error": exc.code}))
        else:
            from unimem_vision.engine import describe

            execute_video(
                Path(sys.argv[1]),
                sys.argv[2],
                (
                    lambda path, decoded, request: recognize_pcm(
                        path, decoded, request, Path(sys.argv[3])
                    )
                )
                if sys.argv[3]
                else None,
                (lambda source: describe(source, Path(sys.argv[4]))) if sys.argv[4] else None,
                alarms=True,
            )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
