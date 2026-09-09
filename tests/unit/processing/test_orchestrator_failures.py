"""What the orchestrator does when something goes wrong.

One distinction runs through all of it, and it is the reason this file is
longer than the happy path. A ``ProcessingError`` is a verdict *about the
capture* — these bytes are not UTF-8, this record has no raw object, this
output belongs to another capture — and trying again changes nothing, so the
capture is durably ``failed``. Anything else says something about *the run* —
the store was unreachable, a bug was hit, the process was interrupted — and the
record stays ``processing``, which is true, rather than being given a terminal
status it has not earned.

That is Phase 0F's rule against fabricating terminal state from infrastructure
failure, one layer up.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.persistence import CaptureRecordPersistenceError
from core.processing import (
    AmbiguousProcessorError,
    NoProcessorError,
    ProcessingError,
    ProcessingInputError,
    ProcessingOrchestrator,
    ProcessingOutputError,
    ProcessorRouter,
    TextDecodingError,
)
from core.storage import RawObjectNotFoundError
from tests.unit.processing.builders import make_capture, make_content
from tests.unit.processing.doubles import (
    FakeClock,
    RecordingCaptureRecordStore,
    RecordingContentObjectStore,
    SpyProcessor,
)

CAPTURE_ID = "cap_text_01"
PROCESSING_AT = datetime(2026, 3, 4, 5, 10, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 3, 4, 5, 10, 30, tzinfo=UTC)

#: Deterministic failures: the capture itself cannot be normalized.
PROCESSING_FAILURES: list[ProcessingError] = [
    ProcessingInputError("capture has no raw object"),
    TextDecodingError("raw object is not valid utf-8"),
]

#: Failures that say nothing about the capture.
INFRASTRUCTURE_FAILURES: list[Exception] = [
    RawObjectNotFoundError("raw object is not present in this store"),
    RuntimeError("something unforeseen"),
]


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def stored() -> CaptureRecord:
    return make_capture(status=CaptureStatus.STORED, title="A note", raw_object=None)


@pytest.fixture
def record_store(journal: list[str], stored: CaptureRecord) -> RecordingCaptureRecordStore:
    return RecordingCaptureRecordStore(journal, records=[stored])


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(PROCESSING_AT, FINISHED_AT)


def orchestrate(
    processors: list[SpyProcessor],
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
    content_store: RecordingContentObjectStore | None = None,
) -> ProcessingOrchestrator:
    return ProcessingOrchestrator(
        ProcessorRouter(processors),
        record_store,
        content_store
        if content_store is not None
        else RecordingContentObjectStore(record_store.journal),
        now=clock,
    )


def test_no_matching_processor_leaves_the_capture_stored(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, journal: list[str]
) -> None:
    """A capture nothing handles is a wiring problem, not a processing attempt."""
    orchestrator = orchestrate([SpyProcessor(supported=False)], record_store, clock)

    with pytest.raises(NoProcessorError):
        orchestrator.process(CAPTURE_ID)

    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.STORED
    assert journal == ["records.get"]
    assert clock.reads == []


def test_several_matching_processors_leave_the_capture_stored(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, journal: list[str]
) -> None:
    content = make_content(CAPTURE_ID)
    orchestrator = orchestrate(
        [SpyProcessor(name="a", content=content), SpyProcessor(name="b", content=content)],
        record_store,
        clock,
    )

    with pytest.raises(AmbiguousProcessorError):
        orchestrator.process(CAPTURE_ID)

    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.STORED
    assert journal == ["records.get"]
    assert clock.reads == []


def test_a_failure_to_persist_processing_stops_before_the_processor(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """Nothing runs against a capture whose ``processing`` state is not durable."""
    store = RecordingCaptureRecordStore(
        journal,
        records=[stored],
        fail_replace_at={1: CaptureRecordPersistenceError("database went away")},
    )
    processor = SpyProcessor(content=make_content(CAPTURE_ID), journal=journal)

    with pytest.raises(CaptureRecordPersistenceError):
        orchestrate([processor], store, clock).process(CAPTURE_ID)

    assert processor.received == []
    assert store.stored(CAPTURE_ID).status is CaptureStatus.STORED
    assert journal == ["records.get", "records.replace(processing)"]


@pytest.mark.parametrize("failure", PROCESSING_FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_processing_failure_is_recorded_and_re_raised(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, failure: ProcessingError
) -> None:
    orchestrator = orchestrate([SpyProcessor(raises=failure)], record_store, clock)

    with pytest.raises(ProcessingError) as raised:
        orchestrator.process(CAPTURE_ID)

    assert raised.value is failure
    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.FAILED


@pytest.mark.parametrize("failure", PROCESSING_FAILURES, ids=lambda exc: type(exc).__name__)
def test_the_failed_record_carries_the_error_message(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, failure: ProcessingError
) -> None:
    with pytest.raises(ProcessingError):
        orchestrate([SpyProcessor(raises=failure)], record_store, clock).process(CAPTURE_ID)

    assert record_store.stored(CAPTURE_ID).error == str(failure)


def test_the_failed_record_preserves_every_capture_fact(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, stored: CaptureRecord
) -> None:
    failure = ProcessingInputError("capture has no raw object")

    with pytest.raises(ProcessingError):
        orchestrate([SpyProcessor(raises=failure)], record_store, clock).process(CAPTURE_ID)

    failed = record_store.stored(CAPTURE_ID)
    assert failed.schema_version == stored.schema_version
    assert failed.id == stored.id
    assert failed.received_at == stored.received_at
    assert failed.source == stored.source
    assert failed.raw_object == stored.raw_object
    assert failed.context == stored.context
    assert failed.intent == stored.intent
    assert failed.title == stored.title
    assert failed.updated_at == FINISHED_AT


def test_a_processing_failure_reads_the_clock_exactly_twice(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    with pytest.raises(ProcessingError):
        orchestrate([SpyProcessor(raises=ProcessingInputError("no"))], record_store, clock).process(
            CAPTURE_ID
        )

    assert clock.reads == [PROCESSING_AT, FINISHED_AT]


def test_content_for_another_capture_is_a_processing_output_error(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """Valid content, wrong capture: recording this complete would misattribute it."""
    foreign = make_content("cap_someone_else")
    orchestrator = orchestrate([SpyProcessor(content=foreign)], record_store, clock)

    with pytest.raises(ProcessingOutputError, match="cap_someone_else"):
        orchestrator.process(CAPTURE_ID)


def test_mismatched_content_fails_the_capture(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """It is a deterministic processing failure, so it is durably recorded."""
    orchestrator = orchestrate(
        [SpyProcessor(content=make_content("cap_someone_else"))], record_store, clock
    )

    with pytest.raises(ProcessingOutputError) as raised:
        orchestrator.process(CAPTURE_ID)

    failed = record_store.stored(CAPTURE_ID)
    assert failed.status is CaptureStatus.FAILED
    assert failed.error == str(raised.value)


def persistence_error_with_backend_cause() -> tuple[CaptureRecordPersistenceError, Exception]:
    """A store error that already names the backend failure underneath it.

    This is what the real SQLite adapter raises: it converts an ``sqlite3``
    error into its own typed error and chains the original, so a human
    debugging the database can still see what actually went wrong.
    """
    backend = sqlite3.OperationalError("database is locked")
    error = CaptureRecordPersistenceError("the capture record database rejected a write")
    error.__cause__ = backend
    return error, backend


def test_a_failure_to_persist_failed_surfaces_the_persistence_error(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """``failed`` was not recorded, so the caller must hear about that."""
    persistence_error, _ = persistence_error_with_backend_cause()
    store = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )
    processing_error = ProcessingInputError("capture has no raw object")

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(raises=processing_error)], store, clock).process(CAPTURE_ID)

    assert raised.value is persistence_error
    assert store.stored(CAPTURE_ID).status is CaptureStatus.PROCESSING


def test_the_persistence_error_keeps_its_own_backend_cause(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """Re-chaining it from the processing error would destroy this diagnostic.

    Phase 0E's persistence boundary converts backend failures into typed store
    errors and chains the original underneath. A ``raise ... from
    processing_error`` here would overwrite that ``__cause__``, and the reason
    the database actually refused the write would be gone.
    """
    persistence_error, backend = persistence_error_with_backend_cause()
    store = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )
    processing_error = ProcessingInputError("capture has no raw object")

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(raises=processing_error)], store, clock).process(CAPTURE_ID)

    assert raised.value.__cause__ is backend
    assert raised.value.__cause__ is not processing_error


def test_the_original_processing_error_is_still_reachable(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """Python links the two on its own, so nothing has to be wrapped."""
    persistence_error, _ = persistence_error_with_backend_cause()
    store = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )
    processing_error = ProcessingInputError("capture has no raw object")

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(raises=processing_error)], store, clock).process(CAPTURE_ID)

    assert raised.value.__context__ is processing_error
    assert not isinstance(raised.value, ProcessingError)


def test_a_store_error_without_a_cause_gains_none(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """No cause is invented either: the processing error stays the context."""
    persistence_error = CaptureRecordPersistenceError("database went away")
    store = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )
    processing_error = ProcessingInputError("capture has no raw object")

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(raises=processing_error)], store, clock).process(CAPTURE_ID)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is processing_error


@pytest.mark.parametrize("failure", INFRASTRUCTURE_FAILURES, ids=lambda exc: type(exc).__name__)
def test_an_infrastructure_failure_propagates_unchanged(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, failure: Exception
) -> None:
    orchestrator = orchestrate([SpyProcessor(raises=failure)], record_store, clock)

    with pytest.raises(type(failure)) as raised:
        orchestrator.process(CAPTURE_ID)

    assert raised.value is failure
    assert not isinstance(raised.value, ProcessingError)


@pytest.mark.parametrize("failure", INFRASTRUCTURE_FAILURES, ids=lambda exc: type(exc).__name__)
def test_an_infrastructure_failure_leaves_the_capture_processing(
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
    journal: list[str],
    failure: Exception,
) -> None:
    """No fabricated ``failed``, and no rollback to ``stored`` either."""
    with pytest.raises(type(failure)):
        orchestrate([SpyProcessor(raises=failure, journal=journal)], record_store, clock).process(
            CAPTURE_ID
        )

    left = record_store.stored(CAPTURE_ID)
    assert left.status is CaptureStatus.PROCESSING
    assert left.error is None
    assert journal == ["records.get", "records.replace(processing)", "processor.process"]
    assert clock.reads == [PROCESSING_AT]


def test_a_failure_to_persist_complete_leaves_the_capture_processing(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """The normalization worked; the lifecycle completion did not, and says so."""
    persistence_error = CaptureRecordPersistenceError("database went away")
    store = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )
    processor = SpyProcessor(content=make_content(CAPTURE_ID), journal=journal)

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([processor], store, clock).process(CAPTURE_ID)

    assert raised.value is persistence_error
    assert store.stored(CAPTURE_ID).status is CaptureStatus.PROCESSING
    assert journal == [
        "records.get",
        "records.replace(processing)",
        "processor.process",
        "content.create",
        "records.replace(complete)",
    ]


def test_nothing_ever_rolls_back_to_stored(
    record_store: RecordingCaptureRecordStore, clock: FakeClock
) -> None:
    """Once ``processing`` is durable, the capture never returns to ``stored``."""
    with pytest.raises(RuntimeError):
        orchestrate([SpyProcessor(raises=RuntimeError("boom"))], record_store, clock).process(
            CAPTURE_ID
        )

    assert record_store.stored(CAPTURE_ID).status is not CaptureStatus.STORED


def test_the_processing_snapshot_is_the_authority_for_what_follows(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    """A processor that edits the record it was handed cannot change the lineage.

    The record handed to a processor is a validated snapshot, not a handle on
    the store — but nothing stops a processor from editing its own copy, and
    the completion record must descend from what was durably written rather
    than from whatever the processor left behind.
    """

    def vandalize(capture: CaptureRecord) -> None:
        capture.title = "rewritten by the processor"
        capture.status = CaptureStatus.FAILED

    store = RecordingCaptureRecordStore(journal, records=[stored])
    processor = SpyProcessor(content=make_content(CAPTURE_ID), mutate=vandalize)

    orchestrate([processor], store, clock).process(CAPTURE_ID)

    complete = store.stored(CAPTURE_ID)
    assert complete.status is CaptureStatus.COMPLETE
    assert complete.title == "A note"


def test_snapshots_do_not_share_mutable_nested_state(
    record_store: RecordingCaptureRecordStore, clock: FakeClock, stored: CaptureRecord
) -> None:
    """No ``intent.tags`` list is shared between the capture's snapshots."""
    orchestrate([SpyProcessor(content=make_content(CAPTURE_ID))], record_store, clock).process(
        CAPTURE_ID
    )

    processing, complete = record_store.replaced
    assert processing.context is not complete.context
    assert processing.source is not complete.source
    assert processing.intent is None or processing.intent is not complete.intent


def test_store_errors_are_never_wrapped_as_processing_errors(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    store = RecordingCaptureRecordStore(
        journal,
        records=[stored],
        fail_replace_at={1: CaptureRecordPersistenceError("database went away")},
    )

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(content=make_content(CAPTURE_ID))], store, clock).process(
            CAPTURE_ID
        )

    assert not isinstance(raised.value, ProcessingError)


def test_a_content_object_is_not_returned_when_completion_fails(
    journal: list[str], stored: CaptureRecord, clock: FakeClock
) -> None:
    store = RecordingCaptureRecordStore(
        journal,
        records=[stored],
        fail_replace_at={2: CaptureRecordPersistenceError("database went away")},
    )
    returned: ContentObject | None = None

    with pytest.raises(CaptureRecordPersistenceError):
        returned = orchestrate(
            [SpyProcessor(content=make_content(CAPTURE_ID))], store, clock
        ).process(CAPTURE_ID)

    assert returned is None
