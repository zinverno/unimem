"""Behaviour of the UTF-8 text processor."""

import hashlib
from datetime import UTC, datetime

import pytest

from core.contracts import (
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    ContentObject,
    ContentType,
    ProcessingStatus,
    ProvenanceSourceType,
    RawObjectRef,
    SegmentType,
)
from core.processing import (
    DEFAULT_TEXT_MIME_TYPE,
    ProcessingError,
    ProcessingInputError,
    TextDecodingError,
    TextProcessor,
)
from core.storage import RawObjectNotFoundError, build_raw_ref
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore

ASCII = "The canonical object is not Markdown."
CYRILLIC = "Привет, мир — ٱلسَّلَامُ عَلَيْكُمْ — 你好 🌍"
LAYOUT = "line 1\r\nline 2\n\n\theading  \n   trailing spaces   \n"


def missing_reference() -> RawObjectRef:
    """A well-formed reference to bytes no store holds."""
    digest = hashlib.sha256(b"never stored").hexdigest()
    return RawObjectRef(id=digest, mime_type="text/plain", sha256=digest, ref=build_raw_ref(digest))


# --- capability ------------------------------------------------------------


def test_supports_text_captures(processor: TextProcessor) -> None:
    assert processor.supports(make_capture()) is True


@pytest.mark.parametrize(
    "payload_type",
    [payload for payload in CapturePayloadType if payload is not CapturePayloadType.TEXT],
)
def test_supports_no_other_payload_type(
    processor: TextProcessor, payload_type: CapturePayloadType
) -> None:
    assert processor.supports(make_capture(payload_type=payload_type)) is False


def test_supports_reads_no_storage(processor: TextProcessor, store: InMemoryRawObjectStore) -> None:
    """A capability question is answered from the record, never from the bytes."""
    capture = store_and_capture(store, ASCII.encode())

    assert processor.supports(capture) is True
    assert processor.supports(make_capture(payload_type=CapturePayloadType.IMAGE)) is False
    assert store.accesses == []


def test_supports_does_not_need_a_raw_object(processor: TextProcessor) -> None:
    """A capture this processor handles but cannot read is still one it handles."""
    assert processor.supports(make_capture(raw_object=None)) is True


def test_processor_identity_is_stable_and_non_blank() -> None:
    assert TextProcessor.name.strip()
    assert TextProcessor.version.strip()
    assert TextProcessor(InMemoryRawObjectStore()).name == TextProcessor.name


# --- decoding --------------------------------------------------------------


@pytest.mark.parametrize("text", [ASCII, CYRILLIC, LAYOUT, "hello\nworld", "x"])
def test_decoded_text_reaches_the_segment_unchanged(
    processor: TextProcessor, store: InMemoryRawObjectStore, text: str
) -> None:
    """No trimming, no line-ending rewriting, no Unicode normalization."""
    capture = store_and_capture(store, text.encode())

    content = processor.process(capture)

    assert content.segments[0].text == text


def test_invalid_utf8_is_rejected_explicitly(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = store_and_capture(store, b"valid \xff\xfe not utf-8")

    with pytest.raises(TextDecodingError):
        processor.process(capture)


def test_latin1_bytes_are_not_silently_reinterpreted(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Text that decodes under another encoding is still not UTF-8."""
    capture = store_and_capture(store, "café".encode("latin-1"))

    with pytest.raises(TextDecodingError):
        processor.process(capture)


def test_empty_raw_object_is_rejected(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = store_and_capture(store, b"")

    with pytest.raises(ProcessingInputError, match="empty"):
        processor.process(capture)


@pytest.mark.parametrize("blank", ["   ", "\n\n", "\t", " \r\n \r\n"])
def test_whitespace_only_text_is_rejected(
    processor: TextProcessor, store: InMemoryRawObjectStore, blank: str
) -> None:
    capture = store_and_capture(store, blank.encode())

    with pytest.raises(ProcessingInputError, match="whitespace"):
        processor.process(capture)


# --- input and storage failures -------------------------------------------


def test_capture_without_a_raw_object_is_rejected(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    with pytest.raises(ProcessingInputError, match="no raw object"):
        processor.process(make_capture(raw_object=None))
    assert store.accesses == []


def test_raw_object_without_a_storage_reference_is_rejected(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Nothing could be asked for it, and no asset could carry it."""
    digest = hashlib.sha256(ASCII.encode()).hexdigest()
    capture = make_capture(raw_object=RawObjectRef(id=digest, sha256=digest, ref=None))

    with pytest.raises(ProcessingInputError, match="storage reference"):
        processor.process(capture)
    assert store.accesses == []


def test_storage_errors_keep_their_own_type(processor: TextProcessor) -> None:
    """A store that cannot produce the bytes is not a capture that cannot process."""
    capture = make_capture(raw_object=missing_reference())

    with pytest.raises(RawObjectNotFoundError) as raised:
        processor.process(capture)
    assert not isinstance(raised.value, ProcessingError)


# --- content object --------------------------------------------------------


@pytest.fixture
def content(processor: TextProcessor, store: InMemoryRawObjectStore) -> ContentObject:
    """The content object produced from a plain ASCII text capture."""
    return processor.process(store_and_capture(store, ASCII.encode()))


def test_content_is_canonical_text(content: ContentObject) -> None:
    assert content.type is ContentType.TEXT


def test_content_traces_back_to_its_capture(content: ContentObject) -> None:
    assert content.source.capture_id == make_capture().id


def test_capture_source_context_is_propagated(content: ContentObject) -> None:
    assert content.source.provider == "cli"
    assert content.source.url == "https://example.com/notes"


def test_absent_source_context_is_not_fabricated(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = store_and_capture(
        store,
        ASCII.encode(),
        source=CaptureSource(type=CaptureSourceType.FILESYSTEM),
    )

    content = processor.process(capture)

    assert content.source.provider is None
    assert content.source.url is None
    assert content.title is None


def test_original_reference_preserves_digest_and_mime(content: ContentObject) -> None:
    assert content.original.sha256 == hashlib.sha256(ASCII.encode()).hexdigest()
    assert content.original.mime_type == "text/plain"


def test_original_asset_carries_a_storage_neutral_reference(content: ContentObject) -> None:
    digest = hashlib.sha256(ASCII.encode()).hexdigest()
    asset = content.assets[0]

    assert len(content.assets) == 1
    assert asset.role.value == "original"
    assert content.original.asset_id == asset.id
    assert asset.ref == build_raw_ref(digest)
    assert asset.sha256 == digest


def test_asset_identity_is_not_raw_object_identity(content: ContentObject) -> None:
    """An asset records a pointer to an original; it is not the original.

    The raw object's identity travels on ``ref`` and ``sha256``. Reusing it as
    the asset id would make the digest name a record inside one content object
    as well as the bytes themselves.
    """
    digest = hashlib.sha256(ASCII.encode()).hexdigest()
    asset = content.assets[0]

    assert asset.id != digest
    assert asset.id != asset.sha256
    assert asset.id != asset.ref
    assert asset.id != content.id
    assert asset.id != content.segments[0].id


def test_the_original_can_be_read_back_from_the_content_object_alone(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """The whole point of keeping the asset: the bytes stay retrievable."""
    payload = CYRILLIC.encode()
    content = processor.process(store_and_capture(store, payload))
    asset = content.assets[0]

    # Raw object identity is the digest, so that is what rebuilds the handle.
    # The asset id names the asset record and has no place in a RawObjectRef.
    assert asset.sha256 is not None
    recovered = RawObjectRef(
        id=asset.sha256, mime_type=asset.mime_type, sha256=asset.sha256, ref=asset.ref
    )
    assert store.read_bytes(recovered) == payload


def test_missing_mime_falls_back_only_on_the_asset(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """A text capture is processed without a declared MIME type, and stays honest."""
    content = processor.process(store_and_capture(store, ASCII.encode(), mime_type=None))

    assert content.assets[0].mime_type == DEFAULT_TEXT_MIME_TYPE
    assert content.original.mime_type is None


def test_generic_mime_is_carried_through(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    content = processor.process(
        store_and_capture(store, ASCII.encode(), mime_type="application/octet-stream")
    )

    assert content.original.mime_type == "application/octet-stream"
    assert content.assets[0].mime_type == "application/octet-stream"


def test_derived_content_stays_empty(content: ContentObject) -> None:
    assert content.derived.summary is None
    assert content.derived.topics == []
    assert content.derived.entities == []


def test_content_revalidates_against_its_own_invariants(content: ContentObject) -> None:
    assert ContentObject.model_validate(content.model_dump()) == content


def test_content_survives_a_json_round_trip(content: ContentObject) -> None:
    assert ContentObject.model_validate_json(content.model_dump_json()) == content


# --- segment and provenance ------------------------------------------------


def test_one_capture_yields_one_segment(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Chunking is a retrieval concern; paragraphs are not split here."""
    many_paragraphs = "# Heading\n\nfirst para\n\nsecond para\n\n- a\n- b\n"
    content = processor.process(store_and_capture(store, many_paragraphs.encode()))

    assert len(content.segments) == 1
    assert content.segments[0].text == many_paragraphs


def test_segment_is_positioned_canonical_text(content: ContentObject) -> None:
    segment = content.segments[0]

    assert segment.type is SegmentType.TEXT
    assert segment.position == 0
    assert segment.text == ASCII


def test_segment_provenance_points_at_the_capture_and_the_original(
    content: ContentObject,
) -> None:
    provenance = content.segments[0].provenance

    assert provenance.capture_id == content.source.capture_id
    assert provenance.source_type is ProvenanceSourceType.ORIGINAL
    assert provenance.asset_id == content.assets[0].id


def test_segment_provenance_records_the_processor(content: ContentObject) -> None:
    provenance = content.segments[0].provenance

    assert provenance.processor == TextProcessor.name
    assert provenance.processor_version == TextProcessor.version


# --- processing record -----------------------------------------------------


def test_one_successful_processing_record_is_recorded(content: ContentObject) -> None:
    assert len(content.processing) == 1
    record = content.processing[0]

    assert record.status is ProcessingStatus.COMPLETE
    assert record.warnings == []
    assert record.errors == []


def test_processing_record_identity_matches_provenance(content: ContentObject) -> None:
    record = content.processing[0]
    provenance = content.segments[0].provenance

    assert record.processor == provenance.processor
    assert record.processor_version == provenance.processor_version


def test_processing_record_timestamps_are_real_and_ordered(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = store_and_capture(store, ASCII.encode())

    before = datetime.now(UTC)
    record = processor.process(capture).processing[0]
    after = datetime.now(UTC)

    assert record.completed_at is not None
    assert record.started_at.tzinfo is not None
    assert record.completed_at.tzinfo is not None
    assert before <= record.started_at <= record.completed_at <= after


# --- identifiers -----------------------------------------------------------


def test_minted_ids_are_opaque_and_not_the_content_digest(content: ContentObject) -> None:
    digest = hashlib.sha256(ASCII.encode()).hexdigest()
    minted = [content.id, content.segments[0].id, content.assets[0].id]

    assert all(identifier.strip() for identifier in minted)
    assert digest not in minted
    assert len(set(minted)) == len(minted)


def test_two_captures_of_identical_bytes_are_two_content_objects(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Raw deduplication is byte identity; it never collapses captures."""
    first = processor.process(store_and_capture(store, ASCII.encode(), id="cap_a"))
    second = processor.process(store_and_capture(store, ASCII.encode(), id="cap_b"))

    assert first.id != second.id
    assert first.segments[0].id != second.segments[0].id
    assert first.original.sha256 == second.original.sha256


def test_reprocessing_the_same_bytes_mints_a_new_asset_id(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Asset ids are per-run; the reference and digest are per-bytes."""
    capture = store_and_capture(store, ASCII.encode())
    first = processor.process(capture)
    second = processor.process(capture)

    assert first.assets[0].id != second.assets[0].id
    assert first.original.asset_id != second.original.asset_id
    assert first.segments[0].provenance.asset_id != second.segments[0].provenance.asset_id
    assert first.assets[0].ref == second.assets[0].ref
    assert first.assets[0].sha256 == second.assets[0].sha256


# --- what processing must not touch ---------------------------------------


def test_the_capture_record_is_not_mutated(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = store_and_capture(store, ASCII.encode())
    before = capture.model_dump()

    processor.process(capture)

    assert capture.model_dump() == before


def test_the_capture_lifecycle_is_not_advanced(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """Any status a capture arrives with is processed, and left alone."""
    for status in ("received", "stored", "queued", "processing"):
        capture = store_and_capture(store, ASCII.encode(), status=status)
        assert processor.process(capture).type is ContentType.TEXT
        assert capture.status.value == status


def test_the_stored_bytes_are_not_modified(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    payload = LAYOUT.encode()
    capture = store_and_capture(store, payload)

    processor.process(capture)

    assert isinstance(capture.raw_object, RawObjectRef)
    assert store.read_bytes(capture.raw_object) == payload


def test_processing_reads_the_original_rather_than_the_capture_payload(
    processor: TextProcessor, store: InMemoryRawObjectStore
) -> None:
    """The stored original is the source of truth, whatever else exists."""
    stored = "what was actually stored"
    capture: CaptureRecord = store_and_capture(store, stored.encode())

    content = processor.process(capture)

    assert content.segments[0].text == stored
    assert store.accesses == ["read_bytes"]


class TestTitle:
    """The capture's submitted title becomes the content object's title.

    Phase 0G made ``CaptureRecord.title`` durable; version 0.2 of this
    processor is the first thing that reads it. Plain text has no competing
    extracted title — no heading to parse, no ``<title>`` element, no document
    properties — so when the capture carried one it simply *is* the title, and
    when it did not, nothing is invented.
    """

    def test_the_capture_title_is_copied_exactly(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"some notes", title="  A Submitted Title  ")

        assert processor.process(capture).title == "  A Submitted Title  "

    def test_a_unicode_title_survives(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"some notes", title="Заметка — 你好 🌍")

        assert processor.process(capture).title == "Заметка — 你好 🌍"

    def test_no_title_stays_no_title(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"some notes", title=None)

        assert processor.process(capture).title is None

    def test_the_title_is_not_taken_from_the_first_line(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        """No heading parsing, no first-line heuristic, no filename or URL."""
        capture = store_and_capture(store, b"# Looks Like A Heading\n\nbody text", title=None)

        content = processor.process(capture)

        assert content.title is None
        assert content.segments[0].text == "# Looks Like A Heading\n\nbody text"

    def test_the_title_does_not_leak_into_the_segment_text(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"body text", title="A Submitted Title")

        assert processor.process(capture).segments[0].text == "body text"


class TestProcessorVersion:
    """0.2: the output changed for an unchanged input, so the version changed."""

    def test_the_processor_version_is_the_current_one(self) -> None:
        assert TextProcessor.name == "text"
        assert TextProcessor.version == "0.2"

    def test_provenance_records_the_current_version(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"some notes")

        provenance = processor.process(capture).segments[0].provenance

        assert provenance.processor == "text"
        assert provenance.processor_version == "0.2"

    def test_the_processing_record_records_the_current_version(
        self, store: InMemoryRawObjectStore, processor: TextProcessor
    ) -> None:
        capture = store_and_capture(store, b"some notes")

        (record,) = processor.process(capture).processing

        assert record.processor == "text"
        assert record.processor_version == "0.2"
