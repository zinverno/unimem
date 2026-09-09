"""The happy path through the capture lifecycle, and the order it happens in.

Phase 0H's substance is a sequence, so most of these assertions are about
*when* something happened relative to something else: routing before any state
is written, ``processing`` durable before the processor is allowed to do any
I/O, and ``complete`` durable before the caller is handed anything. The shared
journal is what makes that checkable.
"""

import inspect
from datetime import UTC, datetime

import pytest

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.persistence import CaptureRecordNotFoundError, CaptureRecordStore
from core.processing import (
    InvalidCaptureProcessingStateError,
    ProcessingError,
    ProcessingOrchestrator,
    ProcessorRouter,
    utc_now,
)
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
def journal() -> list[str]:
    """What the store, the router, and the processor were asked to do, in order."""
    return []


@pytest.fixture
def stored() -> CaptureRecord:
    """A durably stored capture, ready to be processed."""
    return make_capture(status=CaptureStatus.STORED, title="A note", raw_object=None)


@pytest.fixture
def content() -> ContentObject:
    return make_content(CAPTURE_ID)


@pytest.fixture
def record_store(journal: list[str], stored: CaptureRecord) -> RecordingCaptureRecordStore:
    return RecordingCaptureRecordStore(journal, records=[stored])


@pytest.fixture
def processor(journal: list[str], content: ContentObject) -> SpyProcessor:
    return SpyProcessor(content=content, journal=journal)


@pytest.fixture
def clock() -> FakeClock:
    """Two instants: entering processing, and finishing."""
    return FakeClock(PROCESSING_AT, FINISHED_AT)


@pytest.fixture
def orchestrator(
    processor: SpyProcessor, record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> ProcessingOrchestrator:
    return ProcessingOrchestrator(ProcessorRouter([processor]), record_store, now=clock)


def test_process_returns_the_content_object(
    orchestrator: ProcessingOrchestrator, content: ContentObject
) -> None:
    assert orchestrator.process(CAPTURE_ID) is content


def test_process_takes_a_capture_id_not_a_record() -> None:
    """A lifecycle decision must not run against a snapshot a caller was holding."""
    parameters = inspect.signature(ProcessingOrchestrator.process).parameters

    assert list(parameters) == ["self", "capture_id"]
    assert parameters["capture_id"].annotation is str


def test_the_record_is_loaded_from_the_store_itself(
    orchestrator: ProcessingOrchestrator, journal: list[str]
) -> None:
    orchestrator.process(CAPTURE_ID)

    assert journal[0] == "records.get"


def test_the_whole_sequence_happens_in_order(
    orchestrator: ProcessingOrchestrator, journal: list[str]
) -> None:
    """Routing, then a durable ``processing``, then the processor, then ``complete``."""
    orchestrator.process(CAPTURE_ID)

    assert journal == [
        "records.get",
        "records.replace(processing)",
        "processor.process",
        "records.replace(complete)",
    ]


def test_processing_is_durable_before_the_processor_runs(
    orchestrator: ProcessingOrchestrator, journal: list[str]
) -> None:
    orchestrator.process(CAPTURE_ID)

    assert journal.index("records.replace(processing)") < journal.index("processor.process")


def test_complete_is_durable_before_the_content_is_returned(
    orchestrator: ProcessingOrchestrator, record_store: RecordingCaptureRecordStore
) -> None:
    orchestrator.process(CAPTURE_ID)

    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.COMPLETE


def test_the_processor_is_handed_the_processing_snapshot(
    orchestrator: ProcessingOrchestrator, processor: SpyProcessor, stored: CaptureRecord
) -> None:
    """Not the ``stored`` one it descended from."""
    orchestrator.process(CAPTURE_ID)

    (handed,) = processor.received
    assert handed.status is CaptureStatus.PROCESSING
    assert handed.status is not stored.status


def test_the_selected_processor_runs_exactly_once(
    orchestrator: ProcessingOrchestrator, processor: SpyProcessor
) -> None:
    orchestrator.process(CAPTURE_ID)

    assert len(processor.received) == 1


def test_the_router_is_not_asked_a_second_time(
    record_store: RecordingCaptureRecordStore, content: ContentObject, clock: FakeClock
) -> None:
    """Selecting again after the state change could run a different processor."""
    asked: list[str] = []

    class CountingProcessor(SpyProcessor):
        def supports(self, capture: CaptureRecord) -> bool:
            asked.append(capture.status.value)
            return True

    processor = CountingProcessor(content=content)
    ProcessingOrchestrator(ProcessorRouter([processor]), record_store, now=clock).process(
        CAPTURE_ID
    )

    assert asked == ["stored"]


def test_the_processing_snapshot_preserves_every_capture_fact(
    orchestrator: ProcessingOrchestrator,
    record_store: RecordingCaptureRecordStore,
    stored: CaptureRecord,
) -> None:
    orchestrator.process(CAPTURE_ID)

    processing = record_store.replaced[0]
    assert processing.schema_version == stored.schema_version
    assert processing.id == stored.id
    assert processing.received_at == stored.received_at
    assert processing.source == stored.source
    assert processing.payload_type == stored.payload_type
    assert processing.raw_object == stored.raw_object
    assert processing.context == stored.context
    assert processing.intent == stored.intent
    assert processing.title == stored.title


def test_only_lifecycle_fields_differ_from_stored_to_processing(
    orchestrator: ProcessingOrchestrator,
    record_store: RecordingCaptureRecordStore,
    stored: CaptureRecord,
) -> None:
    orchestrator.process(CAPTURE_ID)

    before = stored.model_dump()
    after = record_store.replaced[0].model_dump()

    assert {name for name in before if before[name] != after[name]} == {
        "status",
        "updated_at",
    }


def test_only_lifecycle_fields_differ_from_processing_to_complete(
    orchestrator: ProcessingOrchestrator, record_store: RecordingCaptureRecordStore
) -> None:
    orchestrator.process(CAPTURE_ID)

    processing, complete = record_store.replaced
    before = processing.model_dump()
    after = complete.model_dump()

    assert {name for name in before if before[name] != after[name]} == {
        "status",
        "updated_at",
    }
    assert complete.status is CaptureStatus.COMPLETE
    assert complete.error is None


def test_the_lifecycle_timestamps_come_from_the_orchestration_clock(
    orchestrator: ProcessingOrchestrator,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
) -> None:
    orchestrator.process(CAPTURE_ID)

    processing, complete = record_store.replaced
    assert processing.updated_at == PROCESSING_AT
    assert complete.updated_at == FINISHED_AT
    assert clock.reads == [PROCESSING_AT, FINISHED_AT]


def test_received_at_is_never_restamped(
    orchestrator: ProcessingOrchestrator, record_store: RecordingCaptureRecordStore
) -> None:
    orchestrator.process(CAPTURE_ID)

    for snapshot in record_store.replaced:
        assert snapshot.received_at == RECEIVED_AT


def test_the_clock_is_read_exactly_twice(
    orchestrator: ProcessingOrchestrator, clock: FakeClock
) -> None:
    orchestrator.process(CAPTURE_ID)

    assert len(clock.reads) == 2


def test_the_default_clock_is_timezone_aware_utc() -> None:
    now = utc_now()
    offset = now.utcoffset()

    assert now.tzinfo is not None
    assert offset is not None
    assert offset.total_seconds() == 0


def test_the_default_clock_is_used_when_none_is_injected(
    record_store: RecordingCaptureRecordStore, processor: SpyProcessor
) -> None:
    ProcessingOrchestrator(ProcessorRouter([processor]), record_store).process(CAPTURE_ID)

    complete = record_store.stored(CAPTURE_ID)
    assert complete.status is CaptureStatus.COMPLETE
    assert complete.updated_at is not None


def test_the_orchestrator_depends_on_the_record_store_port(
    journal: list[str], stored: CaptureRecord, processor: SpyProcessor, clock: FakeClock
) -> None:
    """Bound through the protocol — mypy checks this too, which is the point."""
    store: CaptureRecordStore = RecordingCaptureRecordStore(journal, records=[stored])

    content = ProcessingOrchestrator(ProcessorRouter([processor]), store, now=clock).process(
        CAPTURE_ID
    )

    assert isinstance(content, ContentObject)


def test_a_missing_capture_propagates_the_store_error(
    journal: list[str], processor: SpyProcessor, clock: FakeClock
) -> None:
    """The persistence error keeps its own type; it is not a processing failure."""
    empty = RecordingCaptureRecordStore(journal)
    orchestrator = ProcessingOrchestrator(ProcessorRouter([processor]), empty, now=clock)

    with pytest.raises(CaptureRecordNotFoundError) as raised:
        orchestrator.process("cap_missing")

    assert not isinstance(raised.value, ProcessingError)
    assert journal == ["records.get"]
    assert clock.reads == []


NON_STARTING_STATUSES = [status for status in CaptureStatus if status is not CaptureStatus.STORED]


@pytest.mark.parametrize("status", NON_STARTING_STATUSES, ids=lambda s: s.value)
def test_only_a_stored_capture_may_begin_processing(
    journal: list[str], processor: SpyProcessor, clock: FakeClock, status: CaptureStatus
) -> None:
    record = make_capture(status=status, updated_at=PROCESSING_AT, raw_object=None)
    store = RecordingCaptureRecordStore(journal, records=[record])
    orchestrator = ProcessingOrchestrator(ProcessorRouter([processor]), store, now=clock)

    with pytest.raises(InvalidCaptureProcessingStateError, match=status.value):
        orchestrator.process(CAPTURE_ID)


@pytest.mark.parametrize("status", NON_STARTING_STATUSES, ids=lambda s: s.value)
def test_a_wrong_starting_state_has_no_side_effects(
    journal: list[str], processor: SpyProcessor, clock: FakeClock, status: CaptureStatus
) -> None:
    """No router, no processor, no clock, no write: the capture is untouched."""
    record = make_capture(status=status, updated_at=PROCESSING_AT, raw_object=None)
    store = RecordingCaptureRecordStore(journal, records=[record])
    orchestrator = ProcessingOrchestrator(ProcessorRouter([processor]), store, now=clock)

    with pytest.raises(InvalidCaptureProcessingStateError):
        orchestrator.process(CAPTURE_ID)

    assert journal == ["records.get"]
    assert clock.reads == []
    assert processor.received == []
    assert store.stored(CAPTURE_ID).status is status


def test_reprocessing_a_completed_capture_is_refused(
    journal: list[str], processor: SpyProcessor, clock: FakeClock
) -> None:
    """Reprocessing is a decision this phase does not make."""
    record = make_capture(status=CaptureStatus.COMPLETE, updated_at=PROCESSING_AT)
    store = RecordingCaptureRecordStore(journal, records=[record])

    with pytest.raises(InvalidCaptureProcessingStateError):
        ProcessingOrchestrator(ProcessorRouter([processor]), store, now=clock).process(CAPTURE_ID)
