"""Manual real-engine smoke. Local authorized input + prepared weights; never CI fixtures.

Run separately per file, using a fresh data directory and operation ID. Outputs
only identifiers/measurements; the Markdown stays in that local data directory.
Python socket connections are denied in the child, including model-library calls.
"""

import argparse
import hashlib
import json
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from unimem_api.audio_operations import AudioOperationStore, AudioRequest
from unimem_api.audio_worker import AudioWorker
from unimem_api.worker import ServerLease
from unimem_asr.export import AudioMarkdownRenderer
from unimem_asr.input import inspect_input
from unimem_asr.service import AudioCaptureService


def deny_network(event: str, arguments: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("Network forbidden in offline ASR smoke")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--language", choices=("auto", "ru", "en"), default="auto")
    parser.add_argument("--mime", default="")
    args = parser.parse_args()
    data = args.data_dir.resolve()
    service = AudioCaptureService(data)
    with args.input.open("rb") as stream:
        inspect_input(stream, args.mime)
        raw = service.raw_store.store_stream(stream)
    assert raw.ref is not None
    store = AudioOperationStore(data / "unimem.sqlite3")
    operation_id = str(uuid4())
    store.register(
        AudioRequest(
            operation_id=operation_id,
            file_ref=raw.ref,
            declared_mime=args.mime,
            language=args.language,
            captured_at=datetime.now(UTC),
        )
    )
    with ServerLease(data) as lease:
        worker = AudioWorker(
            data,
            store,
            lease.fd,
            args.model_dir,
            command=(sys.executable, str(Path(__file__).resolve()), "_worker"),
        )
        started = time.monotonic()
        worker.start()
        try:
            while store.get(operation_id).state in {"queued", "running"}:
                if worker.stopping.wait(0.1) or time.monotonic() - started > 310:
                    raise RuntimeError("Worker did not finish")
        finally:
            worker.stop()
    op = store.get(operation_id)
    if op.state != "complete" or op.capture_id is None:
        raise SystemExit(f"{op.state}: {op.error_code}")
    content = service.read(op.capture_id)
    markdown = AudioMarkdownRenderer().render(content).encode()
    (data / "transcript.md").write_bytes(markdown)
    report = {
        "operation_id": operation_id,
        "capture_id": op.capture_id,
        "content_id": op.content_id,
        "original_sha256": raw.sha256,
        "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
        "markdown_bytes": len(markdown),
        "worker_wall_seconds": time.monotonic() - started,
        "measurements": content.metadata["audio_transcription"],
        "offline_check": (
            "Python socket connect/getaddrinfo/sendto denied; HF offline; local weights"
        ),
    }
    (data / "smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        sys.argv.pop(1)
        sys.addaudithook(deny_network)
        # Verify the guard itself before executing the actual bounded worker.
        with socket.socket() as check:
            try:
                check.connect(("127.0.0.1", 9))
            except RuntimeError:
                pass
            else:
                raise RuntimeError("Offline guard inactive")
        from unimem_api.audio_worker import main as worker_main

        raise SystemExit(worker_main())
    main()
