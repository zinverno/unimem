"""``DocxProcessor`` — body-ordered canonical text from an immutable DOCX.

The properties worth holding onto, and what each is protecting:

* **Body order is the fact being preserved.** A table between two paragraphs
  produces segments between those two paragraphs' segments. A processor that
  appended the tables afterwards would pass almost everything else here.
* **What the reader returned reaches the segment untouched.** No strip, no
  Unicode normalization, no whitespace collapse — for paragraph text and for
  cell text alike. A memory system that quietly edits what it stored is worse
  than one that stores nothing.
* **There are no page numbers, and their absence is load-bearing.** A DOCX has
  flow content; a page is a property of whoever renders it. Every segment's
  ``spatial`` is ``None``, and no test here is allowed to want otherwise.
* **A DOCX with no body text fails.** It does not become a ``COMPLETE`` content
  object with no segments, because that would claim the document was remembered
  when it was not — and nothing reaches for OCR to avoid saying so.
* **The original stays byte-exact and retrievable**, so a richer build can read
  exactly these bytes later.
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
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    DocxProcessor,
    PdfProcessor,
    TextProcessor,
    WebpageProcessor,
    extract_blocks,
)
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectNotFoundError, build_raw_ref
from tests import docxs, pdfs
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore


@pytest.fixture
def docx_processor(store: InMemoryRawObjectStore) -> DocxProcessor:
    return DocxProcessor(store)


def docx_capture(store: InMemoryRawObjectStore, data: bytes, **overrides: object) -> CaptureRecord:
    """Stage DOCX bytes and build the document capture that points at them."""
    fields: dict[str, object] = {
        "id": "cap_docx_01",
        "payload_type": CapturePayloadType.DOCUMENT,
    }
    return store_and_capture(store, data, mime_type=DOCX_MIME_TYPE, **(fields | overrides))


class TestSupports:
    """A pure capability check that claims one MIME type, not a whole payload type."""

    def test_a_docx_document_is_claimed(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert docx_processor.supports(docx_capture(store, docxs.paragraphs_docx("hello")))

    def test_a_text_capture_is_not_claimed(self, docx_processor: DocxProcessor) -> None:
        assert not docx_processor.supports(make_capture())

    def test_a_webpage_capture_is_not_claimed(self, docx_processor: DocxProcessor) -> None:
        assert not docx_processor.supports(make_capture(payload_type=CapturePayloadType.WEBPAGE))

    def test_a_pdf_document_is_not_claimed(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The disjointness that lets both processors sit in one router."""
        capture = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type=PDF_MIME_TYPE,
            payload_type=CapturePayloadType.DOCUMENT,
        )

        assert not docx_processor.supports(capture)

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/msword",
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.oasis.opendocument.text",
            "application/zip",
            "application/epub+zip",
            DOCX_MIME_TYPE.upper(),
            f"{DOCX_MIME_TYPE}; charset=utf-8",
            None,
        ],
        ids=["doc", "docm", "odt", "zip", "epub", "uppercase", "parameterized", "none"],
    )
    def test_a_document_of_another_mime_type_is_not_claimed(
        self,
        docx_processor: DocxProcessor,
        store: InMemoryRawObjectStore,
        mime_type: str | None,
    ) -> None:
        """Exact match. ``application/zip`` in particular: a DOCX is a ZIP, and
        claiming the container would claim every other OOXML format with it."""
        capture = store_and_capture(
            store,
            docxs.paragraphs_docx("hello"),
            mime_type=mime_type,
            payload_type=CapturePayloadType.DOCUMENT,
        )

        assert not docx_processor.supports(capture)

    def test_a_document_with_no_raw_object_is_not_claimed(
        self, docx_processor: DocxProcessor
    ) -> None:
        assert not docx_processor.supports(make_capture(payload_type=CapturePayloadType.DOCUMENT))

    def test_supports_touches_no_storage(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The router asks every processor; asking must never cost a read."""
        capture = docx_capture(store, docxs.paragraphs_docx("hello"))
        store.accesses.clear()

        docx_processor.supports(capture)

        assert store.accesses == []

    def test_a_docx_whose_bytes_are_absent_is_still_claimed(
        self, docx_processor: DocxProcessor
    ) -> None:
        """Routing is a property of the record, not of what the store holds."""
        missing = RawObjectRef(
            id="c" * 64,
            mime_type=DOCX_MIME_TYPE,
            sha256="c" * 64,
            ref=build_raw_ref("c" * 64),
        )

        assert docx_processor.supports(
            make_capture(payload_type=CapturePayloadType.DOCUMENT, raw_object=missing)
        )


class TestProcessInputRequirements:
    def test_a_capture_with_no_raw_object_is_refused(self, docx_processor: DocxProcessor) -> None:
        capture = make_capture(payload_type=CapturePayloadType.DOCUMENT)

        with pytest.raises(ProcessingInputError, match="no raw object"):
            docx_processor.process(capture)

    def test_a_reference_without_a_ref_is_refused(self, docx_processor: DocxProcessor) -> None:
        capture = make_capture(
            payload_type=CapturePayloadType.DOCUMENT,
            raw_object=RawObjectRef(id="d" * 64, mime_type=DOCX_MIME_TYPE, sha256="d" * 64),
        )

        with pytest.raises(ProcessingInputError, match="without a storage reference"):
            docx_processor.process(capture)

    def test_a_pdf_original_is_refused_even_when_called_directly(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``process`` re-checks what ``supports`` asked; it is callable unrouted."""
        capture = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type=PDF_MIME_TYPE,
            payload_type=CapturePayloadType.DOCUMENT,
        )

        with pytest.raises(ProcessingInputError, match="not declared application/vnd"):
            docx_processor.process(capture)

    def test_a_storage_failure_propagates_with_its_own_type(
        self, docx_processor: DocxProcessor
    ) -> None:
        """The store's failure is not flattened into "this document is bad"."""
        missing = RawObjectRef(
            id="e" * 64,
            mime_type=DOCX_MIME_TYPE,
            sha256="e" * 64,
            ref=build_raw_ref("e" * 64),
        )
        capture = make_capture(payload_type=CapturePayloadType.DOCUMENT, raw_object=missing)

        with pytest.raises(RawObjectNotFoundError):
            docx_processor.process(capture)


class TestOneParagraphDocument:
    @pytest.fixture
    def content(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return docx_processor.process(docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A)))

    def test_the_content_object_is_a_document(self, content: ContentObject) -> None:
        assert content.type is ContentType.DOCUMENT

    def test_it_is_attributed_to_the_capture(self, content: ContentObject) -> None:
        assert content.source.capture_id == "cap_docx_01"

    def test_the_capture_source_facts_are_carried_over(self, content: ContentObject) -> None:
        assert content.source.provider == "cli"
        assert content.source.url == "https://example.com/notes"

    def test_there_is_one_segment(self, content: ContentObject) -> None:
        assert len(content.segments) == 1

    def test_the_segment_is_text(self, content: ContentObject) -> None:
        assert content.segments[0].type is SegmentType.TEXT

    def test_the_segment_carries_the_paragraph_text(self, content: ContentObject) -> None:
        assert content.segments[0].text == docxs.PARAGRAPH_A

    def test_the_segment_is_first_in_reading_order(self, content: ContentObject) -> None:
        assert content.segments[0].position == 0

    def test_the_segment_is_marked_as_a_paragraph(self, content: ContentObject) -> None:
        assert content.segments[0].metadata == {"docx_block": "paragraph"}

    def test_there_is_one_complete_processing_record(self, content: ContentObject) -> None:
        assert len(content.processing) == 1
        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_the_processing_record_names_this_processor(self, content: ContentObject) -> None:
        assert content.processing[0].processor == "docx"
        assert content.processing[0].processor_version == "0.1"


class TestParagraphExtraction:
    def test_paragraphs_arrive_in_body_order(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        data = docxs.paragraphs_docx(docxs.PARAGRAPH_A, docxs.PARAGRAPH_B, docxs.PARAGRAPH_C)

        content = docx_processor.process(docx_capture(store, data))

        assert [segment.text for segment in content.segments] == [
            docxs.PARAGRAPH_A,
            docxs.PARAGRAPH_B,
            docxs.PARAGRAPH_C,
        ]

    def test_a_blank_paragraph_emits_no_segment(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The thing a person leaves behind by pressing Enter twice."""
        data = docxs.paragraphs_docx(docxs.PARAGRAPH_A, "", docxs.PARAGRAPH_B)

        content = docx_processor.process(docx_capture(store, data))

        assert [segment.text for segment in content.segments] == [
            docxs.PARAGRAPH_A,
            docxs.PARAGRAPH_B,
        ]

    @pytest.mark.parametrize(
        "blank",
        ["", " ", "   ", "\t", "\n", "\u00a0"],
        ids=["empty", "space", "spaces", "tab", "newline", "no-break-space"],
    )
    def test_every_shape_of_blank_paragraph_is_omitted(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore, blank: str
    ) -> None:
        data = docxs.paragraphs_docx(docxs.PARAGRAPH_A, blank)

        content = docx_processor.process(docx_capture(store, data))

        assert len(content.segments) == 1

    def test_blank_paragraphs_leave_no_gap_in_reading_order(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        data = docxs.paragraphs_docx("", docxs.PARAGRAPH_A, "  ", docxs.PARAGRAPH_B, "")

        content = docx_processor.process(docx_capture(store, data))

        assert [segment.position for segment in content.segments] == [0, 1]

    def test_a_word_heading_is_still_a_paragraph_of_text(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No style interpretation: a ``Heading 1`` carries no special meaning here."""
        content = docx_processor.process(docx_capture(store, docxs.heading_docx()))

        assert content.segments[0].text == docxs.HEADING_TEXT
        assert content.segments[0].type is SegmentType.TEXT
        assert content.segments[0].metadata == {"docx_block": "paragraph"}

    def test_no_segment_is_a_section(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Turning heading styles into a hierarchy is a later question, not a guess."""
        content = docx_processor.process(docx_capture(store, docxs.heading_docx()))

        assert {segment.type for segment in content.segments} == {SegmentType.TEXT}

    def test_no_heading_reaches_the_spatial_location(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``SpatialLocation.heading`` is not a place to put a Word style name."""
        content = docx_processor.process(docx_capture(store, docxs.heading_docx()))

        assert all(segment.spatial is None for segment in content.segments)


class TestTableExtraction:
    @pytest.fixture
    def content(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return docx_processor.process(docx_capture(store, docxs.paragraph_table_paragraph_docx()))

    def test_body_order_is_preserved_across_paragraphs_and_tables(
        self, content: ContentObject
    ) -> None:
        """The assertion this whole PR exists to make."""
        assert [segment.text for segment in content.segments] == [
            docxs.PARAGRAPH_A,
            docxs.TABLE_ROW_ONE,
            docxs.TABLE_ROW_TWO,
            docxs.PARAGRAPH_B,
        ]

    def test_positions_are_one_contiguous_sequence(self, content: ContentObject) -> None:
        assert [segment.position for segment in content.segments] == [0, 1, 2, 3]

    def test_each_row_is_its_own_segment(self, content: ContentObject) -> None:
        assert len(content.segments) == 4

    def test_no_wrapper_segment_is_emitted_for_the_table(self, content: ContentObject) -> None:
        """A table is a container; a segment for it would be text nobody wrote."""
        assert [segment.metadata.get("docx_block") for segment in content.segments] == [
            "paragraph",
            "table_row",
            "table_row",
            "paragraph",
        ]

    def test_cells_are_joined_with_a_literal_tab(self, content: ContentObject) -> None:
        assert content.segments[1].text == "Format\tPagination"

    def test_a_row_segment_records_its_table_and_row(self, content: ContentObject) -> None:
        assert content.segments[1].metadata == {
            "docx_block": "table_row",
            "table_index": 0,
            "row_index": 0,
        }
        assert content.segments[2].metadata == {
            "docx_block": "table_row",
            "table_index": 0,
            "row_index": 1,
        }

    def test_table_indexes_count_body_tables(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = docx_processor.process(docx_capture(store, docxs.two_tables_docx()))

        assert [
            segment.metadata.get("table_index")
            for segment in content.segments
            if segment.metadata.get("docx_block") == "table_row"
        ] == [0, 0, 1]

    def test_a_blank_row_emits_nothing_and_still_counts(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``row_index`` is a coordinate into the document, not a count of output."""
        content = docx_processor.process(docx_capture(store, docxs.table_with_blank_rows_docx()))

        assert [segment.text for segment in content.segments] == ["first\trow", "third\trow"]
        assert [segment.metadata["row_index"] for segment in content.segments] == [0, 2]
        assert [segment.position for segment in content.segments] == [0, 1]

    def test_cell_text_is_not_stripped_before_joining(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = docx_processor.process(docx_capture(store, docxs.padded_table_docx()))

        assert content.segments[0].text == docxs.PADDED_ROW

    def test_a_merged_cell_repeats_across_the_columns_it_spans(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A documented limitation, pinned down rather than discovered later.

        ``row.cells`` reports a horizontally merged cell once per grid column,
        and this build takes that as given. Reconstructing a merge grid is real
        work with real ambiguities and no downstream requirement yet.
        """
        content = docx_processor.process(docx_capture(store, docxs.merged_cell_docx()))

        assert [segment.text for segment in content.segments] == [
            "spanning\tspanning",
            "left\tright",
        ]

    def test_table_rows_are_never_silently_dropped(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A processor that read only paragraphs would return two segments here."""
        content = docx_processor.process(docx_capture(store, docxs.two_tables_docx()))

        assert len(content.segments) == 5


class TestNoPageNumbers:
    """Pagination is renderer-dependent, so canonical content does not claim it."""

    @pytest.fixture
    def content(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return docx_processor.process(docx_capture(store, docxs.paragraph_table_paragraph_docx()))

    def test_no_segment_has_a_spatial_location_at_all(self, content: ContentObject) -> None:
        assert all(segment.spatial is None for segment in content.segments)

    def test_no_segment_carries_a_page_number(self, content: ContentObject) -> None:
        assert all(
            segment.spatial is None or segment.spatial.page is None for segment in content.segments
        )

    def test_no_page_number_hides_in_segment_metadata_either(self, content: ContentObject) -> None:
        """Not recorded anywhere under another name."""
        assert all("page" not in segment.metadata for segment in content.segments)


class TestTheReaderTextIsNotEdited:
    def test_paragraph_text_is_not_stripped(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        data = docxs.paragraphs_docx(docxs.PADDED_PARAGRAPH)

        content = docx_processor.process(docx_capture(store, data))

        text = content.segments[0].text
        assert text == docxs.PADDED_PARAGRAPH
        assert text != docxs.PADDED_PARAGRAPH.strip()

    def test_unicode_is_not_normalized(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A combining ring and a precomposed Å stay two different strings.

        Under NFC they would collapse into one, and a document that deliberately
        wrote both would come back saying something it did not say.
        """
        content = docx_processor.process(docx_capture(store, docxs.awkward_text_docx()))

        text = content.segments[0].text
        assert text is not None
        assert text == docxs.AWKWARD_TEXT
        assert text != unicodedata.normalize("NFC", text)
        assert text != unicodedata.normalize("NFD", text)

    def test_astral_and_cjk_characters_survive(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = docx_processor.process(docx_capture(store, docxs.awkward_text_docx()))

        text = content.segments[0].text
        assert text is not None
        assert "\U0001f30d" in text
        assert "你好" in text


class TestWhatIsOutsideTheBody:
    def test_header_and_footer_text_never_becomes_a_segment(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Out of scope here, and — crucially — not quietly mixed into the body."""
        content = docx_processor.process(docx_capture(store, docxs.header_footer_docx()))

        assert [segment.text for segment in content.segments] == [docxs.PARAGRAPH_A]
        texts = {segment.text for segment in content.segments}
        assert docxs.HEADER_TEXT not in texts
        assert docxs.FOOTER_TEXT not in texts

    def test_a_document_whose_only_text_is_a_header_is_refused(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Not an empty success: the body genuinely says nothing this build reads."""
        with pytest.raises(ProcessingInputError, match="no body text"):
            docx_processor.process(docx_capture(store, docxs.header_only_docx()))


class TestTheOriginal:
    @pytest.fixture
    def content(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return docx_processor.process(docx_capture(store, docxs.paragraph_table_paragraph_docx()))

    def test_there_is_exactly_one_asset_and_it_is_the_original(
        self, content: ContentObject
    ) -> None:
        assert [asset.role for asset in content.assets] == [AssetRole.ORIGINAL]

    def test_the_asset_reference_is_the_staged_raw_reference(self, content: ContentObject) -> None:
        digest = content.original.sha256
        assert digest is not None
        assert content.assets[0].ref == build_raw_ref(digest)

    def test_the_asset_digest_is_preserved(self, content: ContentObject) -> None:
        assert (
            content.assets[0].sha256
            == hashlib.sha256(docxs.paragraph_table_paragraph_docx()).hexdigest()
        )

    def test_the_asset_mime_type_is_docx(self, content: ContentObject) -> None:
        assert content.assets[0].mime_type == DOCX_MIME_TYPE

    def test_the_original_reference_points_at_that_asset(self, content: ContentObject) -> None:
        assert content.original.asset_id == content.assets[0].id

    def test_the_original_reference_carries_the_declared_mime_type(
        self, content: ContentObject
    ) -> None:
        assert content.original.mime_type == DOCX_MIME_TYPE

    def test_the_exact_docx_is_still_retrievable_after_processing(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        data = docxs.paragraph_table_paragraph_docx()
        capture = docx_capture(store, data)

        docx_processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == data


class TestProvenance:
    @pytest.fixture
    def content(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return docx_processor.process(docx_capture(store, docxs.paragraph_table_paragraph_docx()))

    def test_every_segment_is_provenanced_to_the_original(self, content: ContentObject) -> None:
        """``ORIGINAL``: this text was written in the document and read out of it."""
        assert {segment.provenance.source_type for segment in content.segments} == {
            ProvenanceSourceType.ORIGINAL
        }

    def test_a_table_row_is_provenanced_to_the_original_too(self, content: ContentObject) -> None:
        """Flattening rearranged structure; it did not author the words."""
        rows = [
            segment
            for segment in content.segments
            if segment.metadata.get("docx_block") == "table_row"
        ]
        assert {segment.provenance.source_type for segment in rows} == {
            ProvenanceSourceType.ORIGINAL
        }

    @pytest.mark.parametrize(
        "source_type",
        [ProvenanceSourceType.OCR, ProvenanceSourceType.HTML, ProvenanceSourceType.PROCESSOR],
        ids=["ocr", "html", "processor"],
    )
    def test_no_segment_claims_another_source(
        self, content: ContentObject, source_type: ProvenanceSourceType
    ) -> None:
        assert source_type not in {segment.provenance.source_type for segment in content.segments}

    def test_every_segment_points_at_the_original_asset(self, content: ContentObject) -> None:
        assert {segment.provenance.asset_id for segment in content.segments} == {
            content.assets[0].id
        }

    def test_every_segment_names_the_processor_and_version(self, content: ContentObject) -> None:
        assert {
            (segment.provenance.processor, segment.provenance.processor_version)
            for segment in content.segments
        } == {("docx", "0.1")}

    def test_every_segment_is_provenanced_to_this_capture(self, content: ContentObject) -> None:
        assert {segment.provenance.capture_id for segment in content.segments} == {"cap_docx_01"}


class TestTitlePrecedence:
    def test_the_submitted_title_wins(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(
            store,
            docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE),
            title="What I called it",
        )

        assert docx_processor.process(capture).title == "What I called it"

    def test_the_core_properties_title_is_used_when_the_capture_has_none(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(
            store, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE)
        )

        assert docx_processor.process(capture).title == docxs.CORE_TITLE

    def test_a_blank_core_title_is_treated_as_absent(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(
            store, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.BLANK_CORE_TITLE)
        )

        assert docx_processor.process(capture).title is None

    def test_a_document_with_no_core_title_has_no_title(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A))

        assert docx_processor.process(capture).title is None

    def test_the_first_paragraph_never_becomes_the_title(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = docx_processor.process(
            docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A))
        )

        assert content.title is None
        assert content.title != content.segments[0].text

    def test_a_heading_never_becomes_the_title(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No style is promoted to a name the document never carried."""
        content = docx_processor.process(docx_capture(store, docxs.heading_docx()))

        assert content.title is None
        assert content.title != docxs.HEADING_TEXT

    def test_the_reference_and_digest_never_become_the_title(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A))

        content = docx_processor.process(capture)

        assert capture.raw_object is not None
        assert content.title not in {capture.raw_object.ref, capture.raw_object.sha256}

    def test_the_core_title_does_not_mutate_the_capture_record(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``CaptureRecord.title`` means what the submitter said, always."""
        capture = docx_capture(
            store, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE)
        )
        before = capture.model_dump_json()

        docx_processor.process(capture)

        assert capture.title is None
        assert capture.model_dump_json() == before

    def test_processing_mutates_nothing_on_the_capture_record(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A), title="Submitted")
        before = capture.model_dump_json()

        docx_processor.process(capture)

        assert capture.model_dump_json() == before


class TestDocumentsThisBuildRefuses:
    @pytest.mark.parametrize(
        "data",
        [
            docxs.corrupt_docx(),
            docxs.not_a_word_package_docx(),
            docxs.truncated_body_docx(),
            b"this is a text file, mislabelled",
            b"",
        ],
        ids=["not-a-zip", "zip-but-not-a-package", "broken-xml", "plain-text", "empty"],
    )
    def test_an_unreadable_package_is_a_processing_input_error(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore, data: bytes
    ) -> None:
        with pytest.raises(ProcessingInputError, match="could not be read as a DOCX package"):
            docx_processor.process(docx_capture(store, data))

    def test_a_pdf_mislabelled_as_a_docx_is_refused(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Intake records a declaration; this is where a wrong one is caught."""
        with pytest.raises(ProcessingInputError, match="could not be read as a DOCX package"):
            docx_processor.process(docx_capture(store, pdfs.one_page_pdf()))

    def test_an_encrypted_package_is_refused(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Office wraps a password-protected document in a compound file, not a ZIP.

        See :func:`tests.docxs.encrypted_docx` for exactly how far this fixture
        goes and what it therefore does not prove.
        """
        with pytest.raises(ProcessingInputError, match="password-protected"):
            docx_processor.process(docx_capture(store, docxs.encrypted_docx()))

    def test_no_password_is_ever_attempted(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError) as raised:
            docx_processor.process(docx_capture(store, docxs.encrypted_docx()))

        assert "does not attempt passwords" in str(raised.value)

    @pytest.mark.parametrize(
        "data",
        [
            docxs.textless_docx(),
            docxs.blank_paragraphs_docx(),
            docxs.image_only_docx(),
            docxs.header_only_docx(),
        ],
        ids=["empty-body", "blank-paragraphs", "image-only", "header-only"],
    )
    def test_a_document_with_no_body_text_is_refused(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore, data: bytes
    ) -> None:
        """It parses perfectly and there is nothing in it to remember."""
        with pytest.raises(ProcessingInputError, match="no body text"):
            docx_processor.process(docx_capture(store, data))

    def test_a_textless_docx_never_becomes_an_empty_complete_document(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            docx_processor.process(docx_capture(store, docxs.textless_docx()))

    def test_an_image_only_document_triggers_no_ocr(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The refusal names the capability boundary rather than reaching past it."""
        with pytest.raises(ProcessingInputError) as raised:
            docx_processor.process(docx_capture(store, docxs.image_only_docx()))

        assert "does not perform OCR" in str(raised.value)

    def test_the_textless_refusal_names_what_is_not_read(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError) as raised:
            docx_processor.process(docx_capture(store, docxs.header_only_docx()))

        message = str(raised.value)
        assert "headers" in message
        assert "footers" in message

    def test_a_refused_document_leaves_its_bytes_untouched(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Nothing submitted is lost by refusing; a richer build can read these later."""
        data = docxs.image_only_docx()
        capture = docx_capture(store, data)

        with pytest.raises(ProcessingInputError):
            docx_processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == data

    def test_the_reader_message_does_not_reach_the_error(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Archive members, XML line numbers, and stream reprs are not a client's problem."""
        with pytest.raises(ProcessingInputError) as raised:
            docx_processor.process(docx_capture(store, docxs.truncated_body_docx()))

        message = str(raised.value)
        assert "word/document.xml" not in message
        assert "line 1" not in message
        assert "BytesIO" not in message


class TestIdentity:
    def test_reprocessing_mints_fresh_ids(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Ids are opaque and never derived from the digest."""
        capture = docx_capture(store, docxs.paragraph_table_paragraph_docx())

        first = docx_processor.process(capture)
        second = docx_processor.process(capture)

        assert first.id != second.id
        assert [asset.id for asset in first.assets] != [asset.id for asset in second.assets]
        assert [segment.id for segment in first.segments] != [
            segment.id for segment in second.segments
        ]

    def test_no_id_is_the_digest(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraph_table_paragraph_docx())
        content = docx_processor.process(capture)

        assert capture.raw_object is not None
        identifiers = (
            {content.id}
            | {asset.id for asset in content.assets}
            | {segment.id for segment in content.segments}
        )
        assert capture.raw_object.sha256 not in identifiers

    def test_the_content_and_reference_still_agree_across_reprocessing(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraph_table_paragraph_docx())

        first = docx_processor.process(capture)
        second = docx_processor.process(capture)

        assert first.assets[0].ref == second.assets[0].ref
        assert first.assets[0].sha256 == second.assets[0].sha256


class TestTheProcessorsDoNotOverlap:
    """Each claims its own thing, so the router never has to pick."""

    def test_the_other_processors_refuse_a_docx_document(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = docx_capture(store, docxs.paragraphs_docx(docxs.PARAGRAPH_A))

        assert not TextProcessor(store).supports(capture)
        assert not WebpageProcessor(store).supports(capture)
        assert not PdfProcessor(store).supports(capture)

    def test_the_docx_processor_refuses_everything_the_others_claim(
        self, docx_processor: DocxProcessor, store: InMemoryRawObjectStore
    ) -> None:
        pdf = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type=PDF_MIME_TYPE,
            payload_type=CapturePayloadType.DOCUMENT,
        )

        assert not docx_processor.supports(make_capture())
        assert not docx_processor.supports(make_capture(payload_type=CapturePayloadType.WEBPAGE))
        assert not docx_processor.supports(pdf)


class TestExtractBlocksOnItsOwn:
    """The extraction function, with no capture and no content object near it."""

    def test_it_returns_blocks_in_body_order(self) -> None:
        blocks, title = extract_blocks(io.BytesIO(docxs.paragraph_table_paragraph_docx()))

        assert [block.text for block in blocks] == [
            docxs.PARAGRAPH_A,
            docxs.TABLE_ROW_ONE,
            docxs.TABLE_ROW_TWO,
            docxs.PARAGRAPH_B,
        ]
        assert title is None

    def test_it_returns_the_core_properties_title(self) -> None:
        _, title = extract_blocks(
            io.BytesIO(docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE))
        )

        assert title == docxs.CORE_TITLE

    def test_it_reports_an_empty_body_rather_than_raising(self) -> None:
        """Whether an empty body is a failure is the *processor's* policy, not this one's."""
        blocks, title = extract_blocks(io.BytesIO(docxs.textless_docx()))

        assert blocks == []
        assert title is None

    def test_it_opens_nothing_but_the_stream(self) -> None:
        """A stream is the whole input: there is no path and no URL to follow."""
        blocks, _ = extract_blocks(io.BytesIO(docxs.paragraphs_docx(docxs.PARAGRAPH_A)))

        assert len(blocks) == 1

    def test_it_refuses_a_package_it_cannot_read(self) -> None:
        with pytest.raises(ProcessingInputError):
            extract_blocks(io.BytesIO(docxs.corrupt_docx()))
