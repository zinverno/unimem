"""``PdfProcessor`` — page-aware canonical text from an immutable PDF.

The properties worth holding onto, and what each is protecting:

* **What the parser returned reaches the segment untouched.** No strip, no
  Unicode normalization, no whitespace collapse. A memory system that quietly
  edits what it stored is worse than one that stores nothing.
* **Canonical reading order and physical page number are different facts.** A
  blank page makes a gap in one and not the other, and both are recorded.
* **A PDF with no embedded text fails.** It does not become a ``COMPLETE``
  content object with no segments, because that would claim the document was
  remembered when it was not.
* **The original stays byte-exact and retrievable**, so a build with OCR can
  read exactly these bytes later.
"""

import hashlib
import io
import unicodedata

import pytest

from core.contracts import (
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentObject,
    ContentType,
    ProcessingStatus,
    ProvenanceSourceType,
    RawObjectRef,
    SegmentType,
)
from core.processing import (
    PDF_MIME_TYPE,
    PdfProcessor,
    TextProcessor,
    WebpageProcessor,
    extract_pages,
)
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectNotFoundError, build_raw_ref
from tests import pdfs
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore


@pytest.fixture
def pdf_processor(store: InMemoryRawObjectStore) -> PdfProcessor:
    return PdfProcessor(store)


def pdf_capture(store: InMemoryRawObjectStore, data: bytes, **overrides: object) -> CaptureRecord:
    """Stage PDF bytes and build the document capture that points at them."""
    fields: dict[str, object] = {
        "id": "cap_pdf_01",
        "payload_type": CapturePayloadType.DOCUMENT,
    }
    return store_and_capture(store, data, mime_type=PDF_MIME_TYPE, **(fields | overrides))


class TestSupports:
    """A pure capability check that claims one MIME type, not a whole payload type."""

    def test_a_pdf_document_is_claimed(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert pdf_processor.supports(pdf_capture(store, pdfs.one_page_pdf()))

    def test_a_text_capture_is_not_claimed(self, pdf_processor: PdfProcessor) -> None:
        assert not pdf_processor.supports(make_capture())

    def test_a_webpage_capture_is_not_claimed(self, pdf_processor: PdfProcessor) -> None:
        assert not pdf_processor.supports(make_capture(payload_type=CapturePayloadType.WEBPAGE))

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/epub+zip",
            "text/plain",
            "application/octet-stream",
            None,
        ],
        ids=["docx", "epub", "text", "octets", "none"],
    )
    def test_a_document_of_another_mime_type_is_not_claimed(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore, mime_type: str | None
    ) -> None:
        """A future DOCX processor must be able to join without an ambiguity."""
        capture = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type=mime_type,
            payload_type=CapturePayloadType.DOCUMENT,
        )

        assert not pdf_processor.supports(capture)

    def test_a_document_with_no_raw_object_is_not_claimed(
        self, pdf_processor: PdfProcessor
    ) -> None:
        assert not pdf_processor.supports(make_capture(payload_type=CapturePayloadType.DOCUMENT))

    def test_supports_touches_no_storage(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The router asks every processor; asking must never cost a read."""
        capture = pdf_capture(store, pdfs.one_page_pdf())
        store.accesses.clear()

        pdf_processor.supports(capture)

        assert store.accesses == []

    def test_a_pdf_whose_bytes_are_absent_is_still_claimed(
        self, pdf_processor: PdfProcessor
    ) -> None:
        """Routing is a property of the record, not of what the store holds."""
        missing = RawObjectRef(
            id="c" * 64,
            mime_type=PDF_MIME_TYPE,
            sha256="c" * 64,
            ref=build_raw_ref("c" * 64),
        )

        assert pdf_processor.supports(
            make_capture(payload_type=CapturePayloadType.DOCUMENT, raw_object=missing)
        )


class TestProcessInputRequirements:
    def test_a_capture_with_no_raw_object_is_refused(self, pdf_processor: PdfProcessor) -> None:
        capture = make_capture(payload_type=CapturePayloadType.DOCUMENT)

        with pytest.raises(ProcessingInputError, match="no raw object"):
            pdf_processor.process(capture)

    def test_a_reference_without_a_ref_is_refused(self, pdf_processor: PdfProcessor) -> None:
        capture = make_capture(
            payload_type=CapturePayloadType.DOCUMENT,
            raw_object=RawObjectRef(id="d" * 64, mime_type=PDF_MIME_TYPE, sha256="d" * 64),
        )

        with pytest.raises(ProcessingInputError, match="without a storage reference"):
            pdf_processor.process(capture)

    def test_a_non_pdf_original_is_refused_even_when_called_directly(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``process`` re-checks what ``supports`` asked; it is callable unrouted."""
        capture = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type="application/epub+zip",
            payload_type=CapturePayloadType.DOCUMENT,
        )

        with pytest.raises(ProcessingInputError, match="not declared application/pdf"):
            pdf_processor.process(capture)

    def test_a_storage_failure_propagates_with_its_own_type(
        self, pdf_processor: PdfProcessor
    ) -> None:
        """The store's failure is not flattened into "this document is bad"."""
        missing = RawObjectRef(
            id="e" * 64,
            mime_type=PDF_MIME_TYPE,
            sha256="e" * 64,
            ref=build_raw_ref("e" * 64),
        )
        capture = make_capture(payload_type=CapturePayloadType.DOCUMENT, raw_object=missing)

        with pytest.raises(RawObjectNotFoundError):
            pdf_processor.process(capture)


class TestOnePageDocument:
    @pytest.fixture
    def content(self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore) -> ContentObject:
        return pdf_processor.process(pdf_capture(store, pdfs.one_page_pdf()))

    def test_the_content_object_is_a_document(self, content: ContentObject) -> None:
        assert content.type is ContentType.DOCUMENT

    def test_it_is_attributed_to_the_capture(self, content: ContentObject) -> None:
        assert content.source.capture_id == "cap_pdf_01"

    def test_the_capture_source_facts_are_carried_over(self, content: ContentObject) -> None:
        assert content.source.provider == "cli"
        assert content.source.url == "https://example.com/notes"

    def test_there_is_one_segment(self, content: ContentObject) -> None:
        assert len(content.segments) == 1

    def test_the_segment_is_text(self, content: ContentObject) -> None:
        assert content.segments[0].type is SegmentType.TEXT

    def test_the_segment_carries_the_page_text(self, content: ContentObject) -> None:
        assert content.segments[0].text == pdfs.TWO_PAGE_TEXT_EXTRACTED

    def test_the_segment_names_page_one(self, content: ContentObject) -> None:
        assert content.segments[0].spatial is not None
        assert content.segments[0].spatial.page == 1

    def test_the_segment_is_first_in_reading_order(self, content: ContentObject) -> None:
        assert content.segments[0].position == 0

    def test_there_is_one_complete_processing_record(self, content: ContentObject) -> None:
        assert len(content.processing) == 1
        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_the_processing_record_names_this_processor(self, content: ContentObject) -> None:
        assert content.processing[0].processor == "pdf"
        assert content.processing[0].processor_version == "0.1"


class TestPageSegmentation:
    def test_pages_arrive_in_physical_order(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf()))

        assert [segment.text for segment in content.segments] == [
            pdfs.TWO_PAGE_TEXT_EXTRACTED,
            pdfs.TWO_PAGE_SECOND_EXTRACTED,
        ]

    def test_page_numbers_are_one_based(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A PDF's first page is page 1, as everyone who reads one already knows."""
        content = pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf()))

        assert [segment.spatial.page for segment in content.segments] == [1, 2]  # type: ignore[union-attr]

    def test_a_blank_page_emits_no_segment(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = pdf_processor.process(pdf_capture(store, pdfs.blank_middle_pdf()))

        assert len(content.segments) == 2

    def test_a_blank_page_leaves_a_gap_in_page_numbers(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Physical location is a fact about the document, and it is preserved."""
        content = pdf_processor.process(pdf_capture(store, pdfs.blank_middle_pdf()))

        assert [segment.spatial.page for segment in content.segments] == [1, 3]  # type: ignore[union-attr]

    def test_a_blank_page_leaves_no_gap_in_reading_order(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Canonical position is a fact about the content, and it stays contiguous."""
        content = pdf_processor.process(pdf_capture(store, pdfs.blank_middle_pdf()))

        assert [segment.position for segment in content.segments] == [0, 1]

    @pytest.mark.parametrize(
        "pages",
        [[[], ["only page two"]], [["only page one"], []], [[], ["middle"], []]],
        ids=["leading", "trailing", "surrounded"],
    )
    def test_positions_are_contiguous_wherever_the_blank_pages_are(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore, pages: list[list[str]]
    ) -> None:
        content = pdf_processor.process(pdf_capture(store, pdfs.build_pdf(pages)))

        assert [segment.position for segment in content.segments] == list(
            range(len(content.segments))
        )


class TestTheParserTextIsNotEdited:
    def test_the_text_is_not_stripped(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The extractor emits a trailing newline, and the segment keeps it."""
        content = pdf_processor.process(pdf_capture(store, pdfs.one_page_pdf()))

        text = content.segments[0].text
        assert text is not None
        assert text.endswith("\n")
        assert text != text.strip()

    def test_unicode_is_not_normalized(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A combining ring and a precomposed Å stay two different strings.

        Under NFC they would collapse into one, and a document that deliberately
        wrote both would come back saying something it did not say.
        """
        content = pdf_processor.process(pdf_capture(store, pdfs.awkward_text_pdf()))

        text = content.segments[0].text
        assert text is not None
        assert text == pdfs.AWKWARD_TEXT + "\n"
        assert text != unicodedata.normalize("NFC", text)
        assert text != unicodedata.normalize("NFD", text)

    def test_astral_and_cjk_characters_survive(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = pdf_processor.process(pdf_capture(store, pdfs.awkward_text_pdf()))

        text = content.segments[0].text
        assert text is not None
        assert "\U0001f30d" in text
        assert "你好" in text


class TestTheOriginal:
    @pytest.fixture
    def content(self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore) -> ContentObject:
        return pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf()))

    def test_there_is_exactly_one_asset_and_it_is_the_original(
        self, content: ContentObject
    ) -> None:
        assert [asset.role for asset in content.assets] == [AssetRole.ORIGINAL]

    def test_the_asset_reference_is_the_staged_raw_reference(self, content: ContentObject) -> None:
        digest = content.original.sha256
        assert digest is not None
        assert content.assets[0].ref == build_raw_ref(digest)

    def test_the_asset_digest_is_preserved(self, content: ContentObject) -> None:
        assert content.assets[0].sha256 == hashlib.sha256(pdfs.two_page_pdf()).hexdigest()

    def test_the_asset_mime_type_is_pdf(self, content: ContentObject) -> None:
        assert content.assets[0].mime_type == PDF_MIME_TYPE

    def test_the_original_reference_points_at_that_asset(self, content: ContentObject) -> None:
        assert content.original.asset_id == content.assets[0].id

    def test_the_original_reference_carries_the_declared_mime_type(
        self, content: ContentObject
    ) -> None:
        assert content.original.mime_type == PDF_MIME_TYPE

    def test_the_exact_pdf_is_still_retrievable_after_processing(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        data = pdfs.two_page_pdf()
        capture = pdf_capture(store, data)

        pdf_processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == data


class TestProvenance:
    @pytest.fixture
    def content(self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore) -> ContentObject:
        return pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf()))

    def test_every_segment_is_provenanced_to_the_original(self, content: ContentObject) -> None:
        """``ORIGINAL``, not ``OCR``: this text was embedded in the document."""
        assert {segment.provenance.source_type for segment in content.segments} == {
            ProvenanceSourceType.ORIGINAL
        }

    def test_no_segment_claims_ocr(self, content: ContentObject) -> None:
        assert ProvenanceSourceType.OCR not in {
            segment.provenance.source_type for segment in content.segments
        }

    def test_every_segment_points_at_the_original_asset(self, content: ContentObject) -> None:
        assert {segment.provenance.asset_id for segment in content.segments} == {
            content.assets[0].id
        }

    def test_every_segment_names_the_processor_and_version(self, content: ContentObject) -> None:
        assert {
            (segment.provenance.processor, segment.provenance.processor_version)
            for segment in content.segments
        } == {("pdf", "0.1")}

    def test_every_segment_is_provenanced_to_this_capture(self, content: ContentObject) -> None:
        assert {segment.provenance.capture_id for segment in content.segments} == {"cap_pdf_01"}


class TestTitlePrecedence:
    def test_the_submitted_title_wins(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(
            store, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE), title="What I called it"
        )

        assert pdf_processor.process(capture).title == "What I called it"

    def test_the_pdf_metadata_title_is_used_when_the_capture_has_none(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))

        assert pdf_processor.process(capture).title == pdfs.METADATA_TITLE

    def test_a_blank_metadata_title_is_treated_as_absent(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf(title=pdfs.BLANK_METADATA_TITLE))

        assert pdf_processor.process(capture).title is None

    def test_a_document_with_no_metadata_title_has_no_title(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf())).title is None

    def test_the_first_page_text_never_becomes_the_title(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No heading detection, no first-line fallback: a name the document never had."""
        content = pdf_processor.process(pdf_capture(store, pdfs.two_page_pdf()))

        assert content.title is None
        assert content.segments[0].text is not None
        assert content.title != content.segments[0].text.strip()

    def test_the_reference_and_digest_never_become_the_title(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf())

        content = pdf_processor.process(capture)

        assert capture.raw_object is not None
        assert content.title not in {capture.raw_object.ref, capture.raw_object.sha256}

    def test_the_metadata_title_does_not_mutate_the_capture_record(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``CaptureRecord.title`` means what the submitter said, always."""
        capture = pdf_capture(store, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))
        before = capture.model_dump_json()

        pdf_processor.process(capture)

        assert capture.title is None
        assert capture.model_dump_json() == before

    def test_processing_mutates_nothing_on_the_capture_record(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf(), title="Submitted")
        before = capture.model_dump_json()

        pdf_processor.process(capture)

        assert capture.model_dump_json() == before


class TestDocumentsThisBuildRefuses:
    def test_a_malformed_pdf_is_a_processing_input_error(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError, match="malformed or truncated"):
            pdf_processor.process(pdf_capture(store, pdfs.corrupt_pdf()))

    def test_bytes_that_are_not_a_pdf_at_all_are_refused(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            pdf_processor.process(pdf_capture(store, b"this is a text file, mislabelled"))

    def test_an_empty_file_is_refused(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            pdf_processor.process(pdf_capture(store, b""))

    def test_a_password_protected_pdf_is_refused(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError, match="encrypted"):
            pdf_processor.process(pdf_capture(store, pdfs.encrypted_pdf()))

    def test_an_empty_password_pdf_is_refused_too(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The parser would open this one silently. "Unencrypted" must mean one thing.

        Without an explicit ``is_encrypted`` check first, a document locked with
        an empty user password would sail through while a real-password one
        failed — so the supported contract would depend on how the document
        happened to be locked rather than on whether it was.
        """
        with pytest.raises(ProcessingInputError, match="encrypted"):
            pdf_processor.process(pdf_capture(store, pdfs.encrypt_pdf(pdfs.one_page_pdf(), "")))

    def test_no_password_is_ever_attempted(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The refusal names encryption, not a wrong password."""
        with pytest.raises(ProcessingInputError) as raised:
            pdf_processor.process(pdf_capture(store, pdfs.encrypted_pdf("s3cret")))

        assert "password" in str(raised.value)
        assert "s3cret" not in str(raised.value)

    def test_a_textless_pdf_is_refused(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A scan. It parses perfectly and there is nothing in it to remember."""
        with pytest.raises(ProcessingInputError, match="no extractable embedded text"):
            pdf_processor.process(pdf_capture(store, pdfs.textless_pdf()))

    def test_a_textless_pdf_never_becomes_an_empty_complete_document(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            pdf_processor.process(pdf_capture(store, pdfs.textless_pdf()))

    def test_the_textless_refusal_says_ocr_is_not_available(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The client is told the capability boundary, not just "failed"."""
        with pytest.raises(ProcessingInputError) as raised:
            pdf_processor.process(pdf_capture(store, pdfs.textless_pdf()))

        assert "OCR" in str(raised.value)

    def test_a_refused_document_leaves_its_bytes_untouched(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Nothing submitted is lost by refusing; an OCR build can read these later."""
        data = pdfs.textless_pdf()
        capture = pdf_capture(store, data)

        with pytest.raises(ProcessingInputError):
            pdf_processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == data

    def test_the_parser_message_does_not_reach_the_error(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Byte offsets and internal parser state are not a client's problem."""
        with pytest.raises(ProcessingInputError) as raised:
            pdf_processor.process(pdf_capture(store, pdfs.corrupt_pdf()))

        message = str(raised.value)
        assert "EOF" not in message
        assert "Stream has ended" not in message


class TestIdentity:
    def test_reprocessing_mints_fresh_ids(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Ids are opaque and never derived from the digest.

        Two captures of the identical PDF are two content objects holding two
        asset records; if ids came from the bytes they would collide.
        """
        capture = pdf_capture(store, pdfs.two_page_pdf())

        first = pdf_processor.process(capture)
        second = pdf_processor.process(capture)

        assert first.id != second.id
        assert [asset.id for asset in first.assets] != [asset.id for asset in second.assets]
        assert [segment.id for segment in first.segments] != [
            segment.id for segment in second.segments
        ]

    def test_no_id_is_the_digest(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf())
        content = pdf_processor.process(capture)

        assert capture.raw_object is not None
        digest = capture.raw_object.sha256
        identifiers = (
            {content.id}
            | {asset.id for asset in content.assets}
            | {segment.id for segment in content.segments}
        )
        assert digest not in identifiers

    def test_the_content_and_reference_still_agree_across_reprocessing(
        self, pdf_processor: PdfProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.two_page_pdf())

        first = pdf_processor.process(capture)
        second = pdf_processor.process(capture)

        assert first.assets[0].ref == second.assets[0].ref
        assert first.assets[0].sha256 == second.assets[0].sha256


class TestTheProcessorsDoNotOverlap:
    """Each claims its own thing, so the router never has to pick."""

    def test_the_other_processors_refuse_a_pdf_document(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.one_page_pdf())

        assert not TextProcessor(store).supports(capture)
        assert not WebpageProcessor(store).supports(capture)


class TestExtractPagesOnItsOwn:
    """The extraction function, with no capture and no content object near it."""

    def test_it_returns_page_numbers_and_text(self) -> None:
        """Blank pages are absent from the result, and the numbers say which."""
        pages, title = extract_pages(io.BytesIO(pdfs.blank_middle_pdf()))

        assert [number for number, _ in pages] == [1, 3]
        assert title is None

    def test_it_returns_the_metadata_title(self) -> None:
        _, title = extract_pages(io.BytesIO(pdfs.one_page_pdf(title=pdfs.METADATA_TITLE)))

        assert title == pdfs.METADATA_TITLE

    def test_it_opens_nothing_but_the_stream(self) -> None:
        """A stream is the whole input: there is no path and no URL to follow."""
        pages, _ = extract_pages(io.BytesIO(pdfs.two_page_pdf()))

        assert len(pages) == 2

    def test_it_refuses_an_encrypted_document_without_touching_a_page(self) -> None:
        with pytest.raises(ProcessingInputError, match="encrypted"):
            extract_pages(io.BytesIO(pdfs.encrypted_pdf()))
