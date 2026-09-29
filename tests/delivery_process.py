"""Synthetic provider and real server/worker processes for B1 acceptance only."""

import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

import uvicorn

from tests.api_auth import TEST_TOKEN
from tests.youtube_fixtures import artifact
from unimem_api.security import ApiSecurity
from unimem_api.worker import execute_operation
from unimem_youtube.artifact import CaptionArtifact
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.operations import OperationStore, State, YoutubeOperation


def worker(mode: str, data: Path, operation_id: str) -> None:
    signal.alarm(3 if mode == "blocked" else 15)

    def acquire(url: str, languages: tuple[str, ...]) -> CaptionArtifact:
        with (data / "acquisitions").open("a") as file:
            file.write(operation_id + "\n")
        if mode == "blocked":
            while True:
                time.sleep(0.05)
        if mode in {"captions_unavailable", "language_unavailable", "timeout"}:
            raise AcquisitionError(mode, "secret upstream detail")
        return artifact()

    if mode == "crash-after-content":
        finish = OperationStore.finish

        def crash(
            self: OperationStore,
            op: YoutubeOperation,
            state: State,
            *,
            capture_id: str | None = None,
            content_id: str | None = None,
            error_code: str | None = None,
        ) -> None:
            if state == "complete":
                os._exit(23)
            finish(
                self, op, state, capture_id=capture_id, content_id=content_id, error_code=error_code
            )

        with patch.object(OperationStore, "finish", crash):
            execute_operation(data, operation_id, acquire=acquire)
        return
    execute_operation(data, operation_id, acquire=acquire)


def main() -> None:
    kind, mode, directory, last = sys.argv[1:]
    data = Path(directory)
    if kind == "worker":
        worker(mode, data, last)
        return
    import unimem_api.wiring as wiring

    # Only this synthetic test executable bypasses the optional dependency gate;
    # its worker has an explicit provider instead of claiming YouTube is available.
    wiring.require_youtube = lambda: None
    app = wiring.build_local_app(
        data,
        security=ApiSecurity(
            TEST_TOKEN, port=int(last), json_bytes=2048, upload_bytes=4096, body_seconds=1
        ),
        youtube=True,
        worker_command=(sys.executable, "-m", "tests.delivery_process", "worker", mode),
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(last),
        access_log=False,
        log_level="error",
        proxy_headers=False,
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
