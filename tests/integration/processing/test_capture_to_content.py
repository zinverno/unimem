"""The whole path, with nothing faked.

An envelope goes into intake, its bytes land in a content-addressed directory,
its record lands in a SQLite file, the orchestrator picks it up by id, the real
text processor normalizes it, and the capture ends ``complete`` in the same
database. Every component here is the one the system actually ships.

This is also where the two limitations Phase 0H does not solve become visible:
the returned content object is not stored anywhere, and nothing stops a second
worker from starting the same capture.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CaptureIntent,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentType,
    IntentAction,
)
from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import (
    InvalidCaptureProcessingStateError,
    ProcessingOrchestrator,
    ProcessorRouter,
    TextProcessor,
)
from core.rendering import JsonRenderer, MarkdownRenderer
from core.storage import LocalRawObjectStore

CAPTURED_AT = datetime(2026, 5, 6, 7, 0, 0, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 5, 6, 7, 30, 0, tzinfo=UTC)
STORED_AT = datetime(2026, 5, 6, 7, 30, 1, tzinfo=UTC)
PROCESSING_AT = datetime(2026, 5, 6, 7, 31, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 5, 6, 7, 31, 2, tzinfo=UTC)

GREETING = "Привет, мир"
NOTES = f"# Architecture\r\n\r\n{GREETING} \u2014 \u4f60\u597d \U0001f30d\n\n\tindented line   \n"
TITLE = "Architecture \u2014 \U0001f30d"


class SteppedClock:
    """Hands out prepared instants, repeating the last one if asked again."""

    def __init__(self, *instants: datetime) -> None:
        self._instants = list(instants)
        self.reads = 0

    def __call__(self) -> datetime:
        instant = self._instants[min(self.reads, len(self._instants) - 1)]
        self.reads += 1
        return instant


@pytest.fixture
def raw_root(tmp_path: Path) -> Path:
    return tmp_path / "objects"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "captures.sqlite3"


@pytest.fixture
def raw_store(raw_root: Path) -> LocalRawObjectStore:
    return LocalRawObjectStore(raw_root)


@pytest.fixture
def record_store(database: Path) -> SqliteCaptureRecordStore:
    return SqliteCaptureRecordStore(database)


@pytest.fixture
def content_store(database: Path) -> SqliteContentObjectStore:
    """The canonical content store, sharing the capture database file."""
    return SqliteContentObjectStore(database)


def make_envelope(capture_id: str = "cap_e2e_01", *, title: str | None = TITLE) -> CaptureEnvelope:
    return CaptureEnvelope(
        id=capture_id,
        source=CaptureSource(
            type=CaptureSourceType.BROWSER, provider="chrome", url="https://example.com/notes"
        ),
        payload=CapturePayload(
            type=CapturePayloadType.TEXT, mime_type="text/plain", text=NOTES, title=title
        ),
        context=CaptureContext(
            captured_at=CAPTURED_AT, device="laptop", application="browser-extension"
        ),
        intent=CaptureIntent(action=IntentAction.SAVE, collection="reading", tags=["architecture"]),
    )


@pytest.fixture
def capture_id(raw_store: LocalRawObjectStore, record_store: SqliteCaptureRecordStore) -> str:
    """One capture, taken all the way to ``stored`` by the real intake."""
    accepted = CaptureIntake(
        raw_store, record_store, now=SteppedClock(RECEIVED_AT, STORED_AT)
    ).accept(make_envelope())
    return accepted.id


@pytest.fixture
def orchestrator(
    raw_store: LocalRawObjectStore,
    record_store: SqliteCaptureRecordStore,
    content_store: SqliteContentObjectStore,
) -> ProcessingOrchestrator:
    return ProcessingOrchestrator(
        ProcessorRouter([TextProcessor(raw_store)]),
        record_store,
        content_store,
        now=SteppedClock(PROCESSING_AT, COMPLETED_AT),
    )


def test_a_captured_note_becomes_canonical_content(
    orchestrator: ProcessingOrchestrator, capture_id: str
) -> None:
    content = orchestrator.process(capture_id)

    assert content.type is ContentType.TEXT
    assert content.source.capture_id == capture_id
    assert content.segments[0].text == NOTES


def test_the_original_text_survives_byte_for_byte(
    orchestrator: ProcessingOrchestrator, capture_id: str
) -> None:
    """Through UTF-8 encoding, a content-addressed file, SQLite, and back out."""
    content = orchestrator.process(capture_id)

    (segment,) = content.segments
    assert segment.text == NOTES
    assert "\r\n" in segment.text
    assert segment.text.endswith("   \n")


def test_the_submitted_title_reaches_the_content_object(
    orchestrator: ProcessingOrchestrator, capture_id: str
) -> None:
    assert orchestrator.process(capture_id).title == TITLE


def test_a_capture_without_a_title_yields_untitled_content(
    raw_store: LocalRawObjectStore,
    record_store: SqliteCaptureRecordStore,
    orchestrator: ProcessingOrchestrator,
) -> None:
    accepted = CaptureIntake(
        raw_store, record_store, now=SteppedClock(RECEIVED_AT, STORED_AT)
    ).accept(make_envelope("cap_untitled", title=None))

    assert orchestrator.process(accepted.id).title is None


def test_the_capture_ends_complete_in_the_database(
    orchestrator: ProcessingOrchestrator, capture_id: str, database: Path
) -> None:
    orchestrator.process(capture_id)

    persisted = SqliteCaptureRecordStore(database).get(capture_id)
    assert persisted.status is CaptureStatus.COMPLETE
    assert persisted.updated_at == COMPLETED_AT
    assert persisted.error is None


def test_the_completed_record_still_carries_every_capture_fact(
    orchestrator: ProcessingOrchestrator, capture_id: str, database: Path
) -> None:
    orchestrator.process(capture_id)

    persisted = SqliteCaptureRecordStore(database).get(capture_id)
    assert persisted.received_at == RECEIVED_AT
    assert persisted.context is not None
    assert persisted.context.captured_at == CAPTURED_AT
    assert persisted.context.device == "laptop"
    assert persisted.intent is not None
    assert persisted.intent.tags == ["architecture"]
    assert persisted.title == TITLE
    assert persisted.raw_object is not None
    assert persisted.source.url == "https://example.com/notes"


def test_the_content_object_reaches_the_original_bytes(
    orchestrator: ProcessingOrchestrator,
    capture_id: str,
    raw_store: LocalRawObjectStore,
    database: Path,
) -> None:
    content = orchestrator.process(capture_id)

    (original,) = content.assets
    raw_object = SqliteCaptureRecordStore(database).get(capture_id).raw_object
    assert raw_object is not None
    assert original.ref == raw_object.ref
    assert original.sha256 == raw_object.sha256
    assert raw_store.read_bytes(raw_object) == NOTES.encode("utf-8")


def test_the_processor_identity_is_recorded_on_the_output(
    orchestrator: ProcessingOrchestrator, capture_id: str
) -> None:
    content = orchestrator.process(capture_id)

    (record,) = content.processing
    assert (record.processor, record.processor_version) == ("text", "0.2")
    assert content.segments[0].provenance.processor_version == "0.2"


def test_the_same_capture_cannot_be_processed_twice(
    orchestrator: ProcessingOrchestrator, capture_id: str
) -> None:
    """Reprocessing is refused; it is a decision this phase does not make."""
    orchestrator.process(capture_id)

    with pytest.raises(InvalidCaptureProcessingStateError, match="complete"):
        orchestrator.process(capture_id)


def test_the_content_object_is_durable(
    orchestrator: ProcessingOrchestrator, capture_id: str, database: Path
) -> None:
    """The limitation Phase 0H had to name is now closed.

    ``complete`` used to mean only that normalization had happened in memory.
    It now means the canonical object is in the database, reachable both by its
    own id and by the capture it came from.
    """
    content = orchestrator.process(capture_id)

    reopened = SqliteContentObjectStore(database)
    assert reopened.get(content.id) == content
    assert reopened.get_for_capture(capture_id) == content


def test_the_content_object_never_reaches_the_raw_store(
    orchestrator: ProcessingOrchestrator, capture_id: str, raw_root: Path
) -> None:
    """Raw storage still holds the original bytes and nothing derived from them."""
    content = orchestrator.process(capture_id)

    stored_bytes = b"".join(path.read_bytes() for path in raw_root.rglob("*") if path.is_file())
    assert stored_bytes == NOTES.encode("utf-8")
    assert content.id.encode() not in stored_bytes


def test_the_content_is_reachable_both_ways(
    orchestrator: ProcessingOrchestrator, capture_id: str, content_store: SqliteContentObjectStore
) -> None:
    """By its own id, and by the capture it normalized from."""
    content = orchestrator.process(capture_id)

    assert content_store.get(content.id) == content
    assert content_store.get_for_capture(capture_id) == content
    assert content_store.get(content.id).id != capture_id


def test_everything_survives_a_restart(
    orchestrator: ProcessingOrchestrator,
    capture_id: str,
    database: Path,
    raw_root: Path,
) -> None:
    """Fresh adapter objects against the same files find the whole result.

    This is what durability means in practice: nothing is held in the objects
    that did the work.
    """
    content = orchestrator.process(capture_id)
    del orchestrator

    records = SqliteCaptureRecordStore(database)
    contents = SqliteContentObjectStore(database)
    originals = LocalRawObjectStore(raw_root)

    record = records.get(capture_id)
    restored = contents.get_for_capture(capture_id)

    assert record.status is CaptureStatus.COMPLETE
    assert restored == content
    assert restored.segments[0].text == NOTES
    assert restored.title == TITLE
    assert record.raw_object is not None
    assert originals.read_bytes(record.raw_object) == NOTES.encode("utf-8")


def test_the_restored_content_keeps_its_provenance_and_digest(
    orchestrator: ProcessingOrchestrator, capture_id: str, database: Path
) -> None:
    content = orchestrator.process(capture_id)

    restored = SqliteContentObjectStore(database).get(content.id)
    assert restored.source.capture_id == capture_id
    assert restored.segments[0].provenance.capture_id == capture_id
    assert restored.segments[0].provenance.processor_version == "0.2"
    assert restored.original.sha256 == content.original.sha256
    assert restored.assets[0].ref == content.assets[0].ref


def test_the_restored_content_still_renders(
    orchestrator: ProcessingOrchestrator, capture_id: str, database: Path
) -> None:
    """Rendering is unchanged, and reads the object persistence handed back.

    The stored JSON is the canonical contract, not a rendering — a renderer
    still derives its own output from the object, exactly as before.
    """
    content = orchestrator.process(capture_id)

    restored = SqliteContentObjectStore(database).get(content.id)
    assert JsonRenderer().render(restored) == JsonRenderer().render(content)
    assert MarkdownRenderer().render(restored) == MarkdownRenderer().render(content)
    assert NOTES in MarkdownRenderer().render(restored)


def test_a_second_processing_attempt_is_still_refused_after_restart(
    orchestrator: ProcessingOrchestrator,
    capture_id: str,
    raw_store: LocalRawObjectStore,
    database: Path,
) -> None:
    orchestrator.process(capture_id)

    fresh = ProcessingOrchestrator(
        ProcessorRouter([TextProcessor(raw_store)]),
        SqliteCaptureRecordStore(database),
        SqliteContentObjectStore(database),
        now=SteppedClock(PROCESSING_AT, COMPLETED_AT),
    )

    with pytest.raises(InvalidCaptureProcessingStateError, match="complete"):
        fresh.process(capture_id)
