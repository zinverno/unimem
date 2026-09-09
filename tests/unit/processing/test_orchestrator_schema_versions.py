"""A legacy capture record still processes, and stays legacy while it does.

Schema 0.2 made ``context`` required, and a 0.1 record has none — there is no
honest value to put there, because ``received_at`` is not ``captured_at``. So a
0.1 capture is processed *as a 0.1 capture*: every lifecycle snapshot it
produces stays at 0.1, and the version is carried, never upgraded.

The content object is a different document with a different lifetime. It is
produced now, by this build, so it carries the current schema version.
"""

from datetime import UTC, datetime

import pytest

from core.contracts import (
    SCHEMA_VERSION,
    CaptureRecord,
    CaptureStatus,
    ContentObject,
)
from core.processing import ProcessingInputError, ProcessingOrchestrator, ProcessorRouter
from tests.unit.processing.builders import RECEIVED_AT, make_capture, make_content
from tests.unit.processing.doubles import (
    FakeClock,
    RecordingCaptureRecordStore,
    SpyProcessor,
)

CAPTURE_ID = "cap_text_01"
PROCESSING_AT = datetime(2026, 3, 4, 5, 10, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 3, 4, 5, 10, 30, tzinfo=UTC)


@pytest.fixture
def legacy() -> CaptureRecord:
    """A stored capture written by a build that predates schema 0.2."""
    record = make_capture(status=CaptureStatus.STORED, raw_object=None)
    data = record.model_dump(mode="json")
    for absent in ("context", "intent", "title"):
        data.pop(absent, None)
    return CaptureRecord.model_validate(data | {"schema_version": "0.1"})


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(PROCESSING_AT, FINISHED_AT)


@pytest.fixture
def record_store(legacy: CaptureRecord) -> RecordingCaptureRecordStore:
    return RecordingCaptureRecordStore(records=[legacy])


def test_the_fixture_really_is_a_legacy_record(legacy: CaptureRecord) -> None:
    assert legacy.schema_version == "0.1"
    assert legacy.context is None
    assert legacy.status is CaptureStatus.STORED


def test_a_legacy_capture_can_be_processed(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    orchestrator = ProcessingOrchestrator(
        ProcessorRouter([SpyProcessor(content=make_content(CAPTURE_ID))]),
        record_store,
        now=clock,
    )

    content = orchestrator.process(CAPTURE_ID)

    assert isinstance(content, ContentObject)
    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.COMPLETE


def test_its_lifecycle_snapshots_stay_at_the_legacy_version(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """Upgrading it would mean inventing a ``context`` that was never captured."""
    ProcessingOrchestrator(
        ProcessorRouter([SpyProcessor(content=make_content(CAPTURE_ID))]),
        record_store,
        now=clock,
    ).process(CAPTURE_ID)

    processing, complete = record_store.replaced
    assert processing.schema_version == "0.1"
    assert complete.schema_version == "0.1"
    assert processing.context is None
    assert complete.context is None


def test_its_snapshots_still_serialize_as_legacy_documents(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """The 0.1 compatibility guarantee survives a trip through the lifecycle."""
    ProcessingOrchestrator(
        ProcessorRouter([SpyProcessor(content=make_content(CAPTURE_ID))]),
        record_store,
        now=clock,
    ).process(CAPTURE_ID)

    for snapshot in record_store.replaced:
        dumped = snapshot.model_dump(mode="json")
        for absent in ("context", "intent", "title"):
            assert absent not in dumped


def test_a_legacy_failure_also_stays_at_the_legacy_version(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    failure = ProcessingInputError("capture has no raw object")
    orchestrator = ProcessingOrchestrator(
        ProcessorRouter([SpyProcessor(raises=failure)]), record_store, now=clock
    )

    with pytest.raises(ProcessingInputError):
        orchestrator.process(CAPTURE_ID)

    failed = record_store.stored(CAPTURE_ID)
    assert failed.status is CaptureStatus.FAILED
    assert failed.schema_version == "0.1"
    assert failed.received_at == RECEIVED_AT


def test_the_content_object_uses_the_current_schema_version(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """A new document produced now, not a rewrite of an old one."""
    content = ProcessingOrchestrator(
        ProcessorRouter([SpyProcessor(content=make_content(CAPTURE_ID))]),
        record_store,
        now=clock,
    ).process(CAPTURE_ID)

    assert content.schema_version == SCHEMA_VERSION
    assert content.schema_version == "0.2"
