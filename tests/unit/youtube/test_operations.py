import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Never

import pytest

from core.contracts import CaptureStatus
from tests.youtube_fixtures import URL, artifact
from unimem_api.worker import execute_operation
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.operations import OperationError, OperationStore, YoutubeRequest
from unimem_youtube.service import YoutubeCaptureService


def test_atomic_replay_conflict_queue_and_distinct_ids(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3", capacity=2)
    request = YoutubeRequest(operation_id="known-before-post", url=URL, languages=("ru", "en"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        registered = list(pool.map(lambda _: store.register(request), range(12)))
    assert sum(created for _, created in registered) == 1
    assert len({op.reserved_capture_id for op, _ in registered}) == 1
    equivalent = YoutubeRequest(
        operation_id=request.operation_id,
        url="https://youtu.be/abcdefghijk?t=12",
        languages=("ru", "en"),
    )
    assert store.register(equivalent)[1] is False
    with pytest.raises(OperationError, match="conflict"):
        store.register(request.model_copy(update={"languages": ("en", "ru")}))
    second, _ = store.register(request.model_copy(update={"operation_id": "intentional-second"}))
    assert second.reserved_capture_id != registered[0][0].reserved_capture_id
    with pytest.raises(OperationError, match="queue_full"):
        store.register(request.model_copy(update={"operation_id": "third"}))
    assert store.register(request)[1] is False


def test_restart_queued_then_finished_capture_resolves_without_acquisition(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    op, _ = store.register(YoutubeRequest(operation_id="restart", url=URL, languages=("ru",)))
    reopened = OperationStore(tmp_path / "unimem.sqlite3")
    service = YoutubeCaptureService(tmp_path)
    reopened.recover(service)
    assert reopened.get("restart").state == "queued"
    assert reopened.claim() is not None
    content = service.capture(
        URL, ("ru",), acquire=lambda *_: artifact(), capture_id=op.reserved_capture_id
    )
    # Simulated crash after canonical commit, before operation update.
    reopened.recover(YoutubeCaptureService(tmp_path))
    done = reopened.get("restart")
    assert done.state == "complete"
    assert done.content_id == content.id
    assert done.capture_id == content.source.capture_id
    assert reopened.claim() is None


def test_acquisition_failure_and_uncertain_running_are_not_retried(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    store.register(YoutubeRequest(operation_id="error", url=URL))
    assert store.claim() is not None
    calls = 0

    def fail(url: str, languages: tuple[str, ...]) -> Never:
        nonlocal calls
        calls += 1
        raise AcquisitionError("timeout", "sensitive upstream URL")

    execute_operation(tmp_path, "error", acquire=fail)
    assert calls == 1
    assert store.get("error").state == "failed"
    assert store.get("error").error_code == "timeout"
    assert "sensitive" not in store.get("error").model_dump_json()
    store.register(YoutubeRequest(operation_id="killed", url=URL))
    assert store.claim() is not None
    store.recover(YoutubeCaptureService(tmp_path))
    assert store.get("killed").state == "interrupted"
    assert store.claim() is None
    assert store.get("killed").capture_id is None


def test_existing_complete_capture_is_never_acquired_twice(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    request = YoutubeRequest(operation_id="existing", url=URL, languages=("ru",))
    op, _ = store.register(request)
    service = YoutubeCaptureService(tmp_path)
    content = service.capture(
        URL, ("ru",), acquire=lambda *_: artifact(), capture_id=op.reserved_capture_id
    )
    assert store.claim() is not None

    def forbidden(*args: object) -> Never:
        pytest.fail("Recovery must not acquire another caption artifact")

    execute_operation(tmp_path, "existing", acquire=forbidden)
    assert store.get("existing").content_id == content.id
    execute_operation(tmp_path, "existing", acquire=forbidden)
    assert store.get("existing").state == "complete"


def test_failed_pipeline_has_real_failed_capture(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    store.register(YoutubeRequest(operation_id="invalid", url=URL, languages=("ru",)))
    assert store.claim() is not None
    execute_operation(tmp_path, "invalid", acquire=lambda *_: artifact("not XML"))
    op = store.get("invalid")
    assert op.state == "failed"
    assert op.error_code == "processing_failed"
    assert op.capture_id is not None
    assert (
        YoutubeCaptureService(tmp_path).record_store.get(op.capture_id).status
        is CaptureStatus.FAILED
    )


@pytest.mark.parametrize("incomplete", [True, False])
def test_content_or_complete_record_alone_is_not_success(tmp_path: Path, incomplete: bool) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    op, _ = store.register(YoutubeRequest(operation_id="torn", url=URL, languages=("ru",)))
    assert store.claim() is not None
    service = YoutubeCaptureService(tmp_path)
    content = service.capture(
        URL, ("ru",), acquire=lambda *_: artifact(), capture_id=op.reserved_capture_id
    )
    if incomplete:
        record = service.record_store.get(op.reserved_capture_id)
        service.record_store.replace(record.model_copy(update={"status": CaptureStatus.PROCESSING}))
    else:
        with sqlite3.connect(tmp_path / "unimem.sqlite3") as db:
            db.execute("DELETE FROM content_objects")
        db.close()
    store.recover(service)
    found = store.get("torn")
    assert found.state == "interrupted"
    assert found.capture_id == op.reserved_capture_id
    assert found.content_id == (content.id if incomplete else None)
    assert store.claim() is None


def test_storage_and_corruption_errors_are_safe(tmp_path: Path) -> None:
    with pytest.raises(OperationError, match="operation_storage_unavailable"):
        OperationStore(tmp_path / "missing" / "db")
    store = OperationStore(tmp_path / "unimem.sqlite3")
    with pytest.raises(OperationError, match="operation_not_found"):
        store.get("not-here")
    op, _ = store.register(YoutubeRequest(operation_id="bad", url=URL))
    for payload in (
        "invalid JSON",
        op.model_copy(update={"state": "failed"}).model_dump_json(),
        op.model_dump_json().replace(URL, "https://invalid.example"),
    ):
        with sqlite3.connect(store.database) as db:
            db.execute("UPDATE youtube_operations SET payload=?", (payload,))
        db.close()
        with pytest.raises(OperationError, match="operation_corrupt"):
            store.get("bad")


def test_unexpected_provider_exception_does_not_escape_or_retry(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "unimem.sqlite3")
    store.register(YoutubeRequest(operation_id="unexpected", url=URL))
    assert store.claim() is not None

    def fail(*args: object) -> Never:
        raise RuntimeError("private diagnostic")

    execute_operation(tmp_path, "unexpected", acquire=fail)
    op = store.get("unexpected")
    assert op.state == "interrupted"
    assert op.error_code == "execution_unavailable"
    assert "private" not in op.model_dump_json()
