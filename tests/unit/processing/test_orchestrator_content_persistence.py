"""Canonical content becomes durable before the capture is called complete.

That ordering is the whole of Phase 0I. ``complete`` is a claim that a capture
normalized into something that still exists, so it must not be written until
that something is stored — and a failure to store it is a statement about the
run, not a verdict that the captured bytes are unprocessable.
"""

from datetime import UTC, datetime

import pytest

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.persistence import (
    CaptureRecordPersistenceError,
    ContentObjectAlreadyExistsError,
    ContentObjectPersistenceError,
    ContentObjectStore,
)
from core.processing import (
    ProcessingInputError,
    ProcessingOrchestrator,
    ProcessingOutputError,
    ProcessorRouter,
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


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def stored() -> CaptureRecord:
    return make_capture(status=CaptureStatus.STORED, title="A note", raw_object=None)


@pytest.fixture
def content() -> ContentObject:
    return make_content(CAPTURE_ID)


@pytest.fixture
def record_store(journal: list[str], stored: CaptureRecord) -> RecordingCaptureRecordStore:
    return RecordingCaptureRecordStore(journal, records=[stored])


@pytest.fixture
def content_store(journal: list[str]) -> RecordingContentObjectStore:
    return RecordingContentObjectStore(journal)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(PROCESSING_AT, FINISHED_AT)


def orchestrate(
    processors: list[SpyProcessor],
    record_store: RecordingCaptureRecordStore,
    content_store: ContentObjectStore,
    clock: FakeClock,
) -> ProcessingOrchestrator:
    return ProcessingOrchestrator(
        ProcessorRouter(processors), record_store, content_store, now=clock
    )


def test_the_whole_sequence_happens_in_order(
    journal: list[str],
    stored: CaptureRecord,
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    """The shared journal is the assertion: content lands between the two writes."""
    processor = SpyProcessor(content=content, journal=journal)

    orchestrate([processor], record_store, content_store, clock).process(CAPTURE_ID)

    assert journal == [
        "records.get",
        "records.replace(processing)",
        "processor.process",
        "content.create",
        "records.replace(complete)",
    ]


def test_content_is_durable_before_complete_is(
    journal: list[str],
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    orchestrate(
        [SpyProcessor(content=content, journal=journal)], record_store, content_store, clock
    ).process(CAPTURE_ID)

    assert journal.index("content.create") < journal.index("records.replace(complete)")


def test_the_stored_content_is_what_the_processor_produced(
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    returned = orchestrate(
        [SpyProcessor(content=content)], record_store, content_store, clock
    ).process(CAPTURE_ID)

    assert content_store.get(content.id) == returned
    assert content_store.get_for_capture(CAPTURE_ID) == returned


def test_the_completion_clock_is_read_only_after_content_is_stored(
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
) -> None:
    """The completion timestamp must describe a capture whose content exists."""
    reads_at_create: list[int] = []

    class ObservingContentStore(RecordingContentObjectStore):
        def create(self, content: ContentObject) -> None:
            reads_at_create.append(len(clock.reads))
            super().create(content)

    orchestrate([SpyProcessor(content=content)], record_store, ObservingContentStore(), clock)
    orchestrate(
        [SpyProcessor(content=content)], record_store, ObservingContentStore(), clock
    ).process(CAPTURE_ID)

    assert reads_at_create == [1]
    assert clock.reads == [PROCESSING_AT, FINISHED_AT]


def test_the_processor_still_runs_exactly_once(
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    processor = SpyProcessor(content=content)

    orchestrate([processor], record_store, content_store, clock).process(CAPTURE_ID)

    assert len(processor.received) == 1
    assert len(content_store.created) == 1


def test_content_is_not_stored_before_the_processor_succeeds(
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    failure = ProcessingInputError("capture has no raw object")

    with pytest.raises(ProcessingInputError):
        orchestrate([SpyProcessor(raises=failure)], record_store, content_store, clock).process(
            CAPTURE_ID
        )

    assert content_store.created == []
    assert content_store.stored_ids() == []


def test_a_processing_failure_still_marks_the_capture_failed(
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    """Phase 0H's verdict semantics are unchanged by the new store."""
    failure = ProcessingInputError("capture has no raw object")

    with pytest.raises(ProcessingInputError) as raised:
        orchestrate([SpyProcessor(raises=failure)], record_store, content_store, clock).process(
            CAPTURE_ID
        )

    assert raised.value is failure
    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.FAILED
    assert content_store.created == []


def test_content_for_another_capture_never_reaches_the_store(
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    """The association check runs first, so misattributed content is not stored."""
    foreign = make_content("cap_someone_else")

    with pytest.raises(ProcessingOutputError):
        orchestrate([SpyProcessor(content=foreign)], record_store, content_store, clock).process(
            CAPTURE_ID
        )

    assert content_store.created == []
    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.FAILED


def test_an_infrastructure_failure_before_content_leaves_processing(
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    failure = RawObjectNotFoundError("raw object is not present in this store")

    with pytest.raises(RawObjectNotFoundError):
        orchestrate([SpyProcessor(raises=failure)], record_store, content_store, clock).process(
            CAPTURE_ID
        )

    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.PROCESSING
    assert content_store.created == []


CONTENT_STORE_FAILURES: list[Exception] = [
    ContentObjectAlreadyExistsError("capture 'cap_text_01' already has a stored content object"),
    ContentObjectPersistenceError("the content object database could not store it"),
]


@pytest.mark.parametrize("failure", CONTENT_STORE_FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_content_store_failure_propagates_unchanged(
    journal: list[str],
    stored: CaptureRecord,
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
    failure: Exception,
) -> None:
    failing = RecordingContentObjectStore(journal, fail_create_with=failure)

    with pytest.raises(type(failure)) as raised:
        orchestrate([SpyProcessor(content=content)], record_store, failing, clock).process(
            CAPTURE_ID
        )

    assert raised.value is failure


@pytest.mark.parametrize("failure", CONTENT_STORE_FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_content_store_failure_leaves_the_capture_processing(
    journal: list[str],
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
    failure: Exception,
) -> None:
    """Not being able to store the result is not a verdict on the capture."""
    failing = RecordingContentObjectStore(journal, fail_create_with=failure)

    with pytest.raises(type(failure)):
        orchestrate([SpyProcessor(content=content)], record_store, failing, clock).process(
            CAPTURE_ID
        )

    left = record_store.stored(CAPTURE_ID)
    # Not `failed` — the capture was normalized fine — and not rolled back to
    # `stored`, which would erase the evidence that a run was started.
    assert left.status is CaptureStatus.PROCESSING
    assert left.error is None


@pytest.mark.parametrize("failure", CONTENT_STORE_FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_content_store_failure_does_not_read_the_completion_clock(
    journal: list[str],
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
    failure: Exception,
) -> None:
    failing = RecordingContentObjectStore(journal, fail_create_with=failure)

    with pytest.raises(type(failure)):
        orchestrate(
            [SpyProcessor(content=content, journal=journal)], record_store, failing, clock
        ).process(CAPTURE_ID)

    assert clock.reads == [PROCESSING_AT]
    assert journal == [
        "records.get",
        "records.replace(processing)",
        "processor.process",
        "content.create",
    ]


def test_a_duplicate_capture_content_is_not_idempotent_success(
    journal: list[str],
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
) -> None:
    """Nothing loads and returns the existing object, and nothing marks complete.

    A capture that already has canonical content means something believes it
    needs normalizing again — a concurrent worker, a retry, drifted lifecycle
    state. Which of those it is cannot be decided here.
    """
    already = ContentObjectAlreadyExistsError("capture already has a stored content object")
    failing = RecordingContentObjectStore(journal, fail_create_with=already)
    returned: ContentObject | None = None

    with pytest.raises(ContentObjectAlreadyExistsError):
        returned = orchestrate(
            [SpyProcessor(content=content)], record_store, failing, clock
        ).process(CAPTURE_ID)

    assert returned is None
    assert record_store.stored(CAPTURE_ID).status is CaptureStatus.PROCESSING


def test_a_complete_write_failure_leaves_the_content_durable(
    journal: list[str],
    stored: CaptureRecord,
    content: ContentObject,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    """The new cross-store gap, stated honestly: content exists, capture does not say so.

    Nothing is deleted and nothing rolls back. ``get_for_capture`` is what makes
    the state findable by a later reconciliation pass.
    """
    persistence_error = CaptureRecordPersistenceError("database went away")
    failing_records = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: persistence_error}
    )

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        orchestrate([SpyProcessor(content=content)], failing_records, content_store, clock).process(
            CAPTURE_ID
        )

    assert raised.value is persistence_error
    assert content_store.get_for_capture(CAPTURE_ID) == content
    assert failing_records.stored(CAPTURE_ID).status is CaptureStatus.PROCESSING


def test_a_complete_write_failure_returns_nothing(
    journal: list[str],
    stored: CaptureRecord,
    content: ContentObject,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    failing_records = RecordingCaptureRecordStore(
        journal, records=[stored], fail_replace_at={2: CaptureRecordPersistenceError("gone")}
    )
    returned: ContentObject | None = None

    with pytest.raises(CaptureRecordPersistenceError):
        returned = orchestrate(
            [SpyProcessor(content=content)], failing_records, content_store, clock
        ).process(CAPTURE_ID)

    assert returned is None


def test_complete_is_never_durable_without_content_first(
    journal: list[str],
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    clock: FakeClock,
) -> None:
    """Across every content-store failure, no capture ever reaches ``complete``."""
    for failure in CONTENT_STORE_FAILURES:
        records = RecordingCaptureRecordStore(
            [], records=[make_capture(status=CaptureStatus.STORED, raw_object=None)]
        )
        failing = RecordingContentObjectStore(fail_create_with=failure)
        with pytest.raises(type(failure)):
            orchestrate(
                [SpyProcessor(content=content)],
                records,
                failing,
                FakeClock(PROCESSING_AT, FINISHED_AT),
            ).process(CAPTURE_ID)

        assert records.stored(CAPTURE_ID).status is not CaptureStatus.COMPLETE


def test_the_returned_object_is_the_processors_own(
    content: ContentObject,
    record_store: RecordingCaptureRecordStore,
    content_store: RecordingContentObjectStore,
    clock: FakeClock,
) -> None:
    """Orchestration stores it and hands it back; it never edits it."""
    before = content.model_copy(deep=True)

    returned = orchestrate(
        [SpyProcessor(content=content)], record_store, content_store, clock
    ).process(CAPTURE_ID)

    assert returned is content
    assert content == before
