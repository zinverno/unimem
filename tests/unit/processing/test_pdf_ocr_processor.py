"""``PdfOcrProcessor`` — embedded text first, recognition only for what is left.

Every test here runs against :class:`~tests.unit.processing.doubles.FakePdfPageOcr`,
so there is no rasterizer, no imaging library, no Tesseract, and no subprocess in
the process. That is the reason the OCR port exists: the *policy* — which pages
are recognized, what the resulting segments mean, what happens when nothing can
be read — is canonical behaviour and must be provable on a machine that cannot
perform OCR at all. The real engine is exercised separately, in
``tests/integration/ocr``.

The properties worth holding onto:

* **A page with embedded text is never rasterized and never recognized**, and
  its segment is indistinguishable from the one the default build produces.
* **Extraction and recognition stay tellable apart forever**: ``TEXT`` +
  ``ORIGINAL`` against ``OCR`` + ``OCR``, on every segment.
* **What the engine returned reaches the segment untouched.** The fake proves
  that, because it returns strings chosen to be hostile to any tidying.
* **A document nothing could be read from fails.** It does not become a
  ``COMPLETE`` content object with no segments.
* **A wrong answer from the engine is an execution failure**, not a verdict about
  the document, and it stays distinguishable from a storage failure.
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
    EMBEDDED_TEXT_PAGES_KEY,
    OCR_ATTEMPTED_PAGES_KEY,
    OCR_METADATA_KEY,
    OCR_PAGES_WITHOUT_TEXT_KEY,
    PAGE_COUNT_KEY,
    PDF_MIME_TYPE,
    PdfOcrProcessor,
    PdfProcessor,
)
from core.processing.errors import ProcessingError, ProcessingInputError
from core.processing.ocr import PdfOcrExecutionError, PdfOcrResult, RecognizedPage
from core.storage import RawObjectNotFoundError, build_raw_ref
from tests import pdfs
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import FakePdfPageOcr, InMemoryRawObjectStore

#: A recognized string that no well-meaning normalizer may tidy: leading and
#: trailing whitespace, a combining ring beside the precomposed character it
#: resembles, a plausible misreading nothing may "correct", and a hyphenated line
#: break nothing may rejoin.
RECOGNIZED = "  Scånned page\nwith an obvi0us OCR err-\nor in it.  \n"

#: A result that is only whitespace. Recognition succeeded and produced nothing
#: usable, which is different from having failed.
WHITESPACE_ONLY = "   \n\t\n "


def pdf_capture(store: InMemoryRawObjectStore, data: bytes, **overrides: object) -> CaptureRecord:
    """Stage PDF bytes and build the document capture that points at them."""
    fields: dict[str, object] = {
        "id": "cap_pdf_ocr_01",
        "payload_type": CapturePayloadType.DOCUMENT,
    }
    return store_and_capture(store, data, mime_type=PDF_MIME_TYPE, **(fields | overrides))


def build(store: InMemoryRawObjectStore, ocr: FakePdfPageOcr) -> PdfOcrProcessor:
    return PdfOcrProcessor(store, ocr)


class TestSupports:
    """The same claim ``PdfProcessor`` makes, which is what makes them alternatives."""

    @pytest.fixture
    def processor(self, store: InMemoryRawObjectStore) -> PdfOcrProcessor:
        return build(store, FakePdfPageOcr())

    def test_a_pdf_document_is_claimed(
        self, processor: PdfOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert processor.supports(pdf_capture(store, pdfs.one_page_pdf()))

    def test_a_text_capture_is_not_claimed(self, processor: PdfOcrProcessor) -> None:
        assert not processor.supports(make_capture())

    def test_a_webpage_capture_is_not_claimed(self, processor: PdfOcrProcessor) -> None:
        assert not processor.supports(make_capture(payload_type=CapturePayloadType.WEBPAGE))

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/epub+zip",
            "image/png",
            "image/tiff",
            "application/pdf; charset=binary",
            "APPLICATION/PDF",
        ],
    )
    def test_a_document_of_another_mime_type_is_not_claimed(
        self, processor: PdfOcrProcessor, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        """Notably ``image/*``: this is a PDF-page recognizer, not an image OCR build."""
        capture = pdf_capture(store, pdfs.one_page_pdf())
        other = capture.model_copy(
            update={"raw_object": capture.raw_object and capture.raw_object.model_copy()}
        )
        assert other.raw_object is not None
        other.raw_object.mime_type = mime_type

        assert not processor.supports(other)

    def test_a_document_with_no_raw_object_is_not_claimed(self, processor: PdfOcrProcessor) -> None:
        assert not processor.supports(make_capture(payload_type=CapturePayloadType.DOCUMENT))

    def test_supports_touches_no_storage(
        self, processor: PdfOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.textless_pdf())
        store.accesses.clear()

        processor.supports(capture)

        assert store.accesses == []

    def test_supports_never_consults_the_recognizer(self, store: InMemoryRawObjectStore) -> None:
        """Routing must not sniff whether a document happens to be a scan."""
        ocr = FakePdfPageOcr(page_count=1)
        capture = pdf_capture(store, pdfs.textless_pdf())

        build(store, ocr).supports(capture)

        assert ocr.calls == []

    def test_it_claims_exactly_what_the_default_pdf_processor_claims(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Stated as an equivalence, because the whole registration rule rests on it."""
        default = PdfProcessor(store)
        recognizing = build(store, FakePdfPageOcr())
        captures = [
            pdf_capture(store, pdfs.one_page_pdf()),
            pdf_capture(store, pdfs.textless_pdf()),
            make_capture(),
            make_capture(payload_type=CapturePayloadType.WEBPAGE),
            make_capture(payload_type=CapturePayloadType.DOCUMENT),
        ]

        for capture in captures:
            assert default.supports(capture) == recognizing.supports(capture)


class TestProcessInputRequirements:
    @pytest.fixture
    def ocr(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(page_count=1)

    def test_a_capture_with_no_raw_object_is_refused(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        with pytest.raises(ProcessingInputError, match="no raw object"):
            build(store, ocr).process(make_capture(payload_type=CapturePayloadType.DOCUMENT))

        assert ocr.calls == []

    def test_a_reference_without_a_ref_is_refused(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        capture = make_capture(
            payload_type=CapturePayloadType.DOCUMENT,
            raw_object=RawObjectRef(id="raw_01", mime_type=PDF_MIME_TYPE),
        )

        with pytest.raises(ProcessingInputError, match="without a storage reference"):
            build(store, ocr).process(capture)

        assert ocr.calls == []

    def test_a_non_pdf_original_is_refused_even_when_called_directly(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        """``process`` is callable without routing, so it re-checks the format."""
        capture = store_and_capture(
            store,
            pdfs.one_page_pdf(),
            mime_type="application/epub+zip",
            payload_type=CapturePayloadType.DOCUMENT,
        )

        with pytest.raises(ProcessingInputError, match="reads PDF documents only"):
            build(store, ocr).process(capture)

        assert ocr.calls == []

    def test_a_storage_failure_propagates_with_its_own_type(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        digest = "f" * 64
        capture = make_capture(
            payload_type=CapturePayloadType.DOCUMENT,
            raw_object=RawObjectRef(
                id=digest, mime_type=PDF_MIME_TYPE, sha256=digest, ref=build_raw_ref(digest)
            ),
        )

        with pytest.raises(RawObjectNotFoundError):
            build(store, ocr).process(capture)


class TestAnEmbeddedTextDocument:
    """A perfectly ordinary text PDF, in a deployment that could have recognized it."""

    @pytest.fixture
    def ocr(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(page_count=2)

    @pytest.fixture
    def content(self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr) -> ContentObject:
        return build(store, ocr).process(pdf_capture(store, pdfs.two_page_pdf()))

    def test_every_page_is_excluded_from_recognition(
        self, content: ContentObject, ocr: FakePdfPageOcr
    ) -> None:
        assert ocr.excluded == frozenset({1, 2})

    def test_both_segments_are_text_from_the_original(self, content: ContentObject) -> None:
        assert [segment.type for segment in content.segments] == [SegmentType.TEXT] * 2
        assert [segment.provenance.source_type for segment in content.segments] == [
            ProvenanceSourceType.ORIGINAL
        ] * 2

    def test_the_text_is_exactly_what_the_parser_returned(self, content: ContentObject) -> None:
        assert [segment.text for segment in content.segments] == [
            pdfs.TWO_PAGE_TEXT_EXTRACTED,
            pdfs.TWO_PAGE_SECOND_EXTRACTED,
        ]

    def test_no_segment_carries_ocr_metadata(self, content: ContentObject) -> None:
        assert [segment.metadata for segment in content.segments] == [{}, {}]

    def test_the_metadata_records_that_nothing_was_recognized(self, content: ContentObject) -> None:
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded[EMBEDDED_TEXT_PAGES_KEY] == [1, 2]
        assert recorded[OCR_ATTEMPTED_PAGES_KEY] == []
        assert recorded[OCR_PAGES_WITHOUT_TEXT_KEY] == []

    def test_the_result_matches_the_default_build_page_for_page(
        self, content: ContentObject, store: InMemoryRawObjectStore
    ) -> None:
        """Enabling OCR changes nothing about pages that already worked.

        Compared field by field rather than object by object, because the ids,
        the timestamps, and the processor name are all legitimately different.
        """
        default = PdfProcessor(store).process(pdf_capture(store, pdfs.two_page_pdf()))

        assert [
            (segment.text, segment.spatial, segment.position, segment.type)
            for segment in content.segments
        ] == [
            (segment.text, segment.spatial, segment.position, segment.type)
            for segment in default.segments
        ]


class TestAScannedDocument:
    """Nothing extractable anywhere: the document the default build refuses."""

    @pytest.fixture
    def ocr(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(page_count=2, texts={1: "First scanned page.\n", 2: RECOGNIZED})

    @pytest.fixture
    def content(self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr) -> ContentObject:
        return build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    def test_nothing_is_excluded_from_recognition(
        self, content: ContentObject, ocr: FakePdfPageOcr
    ) -> None:
        assert ocr.excluded == frozenset()

    def test_the_content_object_is_still_a_document(self, content: ContentObject) -> None:
        assert content.type is ContentType.DOCUMENT

    def test_it_is_attributed_to_the_capture(self, content: ContentObject) -> None:
        assert content.source.capture_id == "cap_pdf_ocr_01"

    def test_the_capture_source_facts_are_carried_over(self, content: ContentObject) -> None:
        assert content.source.provider == "cli"
        assert content.source.url == "https://example.com/notes"

    def test_both_pages_become_ocr_segments(self, content: ContentObject) -> None:
        assert [segment.type for segment in content.segments] == [SegmentType.OCR] * 2

    def test_every_segment_is_provenanced_as_recognized(self, content: ContentObject) -> None:
        assert [segment.provenance.source_type for segment in content.segments] == [
            ProvenanceSourceType.OCR
        ] * 2

    def test_the_recognized_strings_are_stored_exactly(self, content: ContentObject) -> None:
        assert [segment.text for segment in content.segments] == [
            "First scanned page.\n",
            RECOGNIZED,
        ]

    def test_the_recognized_text_is_not_stripped(self, content: ContentObject) -> None:
        text = content.segments[1].text
        assert text is not None
        assert text.startswith("  ")
        assert text.endswith("  \n")

    def test_the_recognized_text_is_not_unicode_normalized(self, content: ContentObject) -> None:
        text = content.segments[1].text
        assert text is not None
        assert "å" in text
        assert text != unicodedata.normalize("NFC", text)

    def test_a_plausible_misreading_is_not_corrected(self, content: ContentObject) -> None:
        text = content.segments[1].text
        assert text is not None
        assert "obvi0us" in text

    def test_a_hyphenated_line_break_is_not_rejoined(self, content: ContentObject) -> None:
        text = content.segments[1].text
        assert text is not None
        assert "err-\nor" in text

    def test_every_segment_names_the_engine(self, content: ContentObject) -> None:
        for segment in content.segments:
            assert segment.metadata[OCR_METADATA_KEY] == {
                "engine": "fake-ocr",
                "engine_version": "9.9.9",
            }

    def test_the_processing_record_names_this_processor(self, content: ContentObject) -> None:
        record = content.processing[0]
        assert (record.processor, record.processor_version) == ("pdf-ocr", "0.1")
        assert record.status is ProcessingStatus.COMPLETE

    def test_the_recognizer_was_handed_the_original_bytes_from_the_start(
        self, content: ContentObject, ocr: FakePdfPageOcr
    ) -> None:
        """A fresh stream, not one a parser had already walked through."""
        assert ocr.received_bytes == pdfs.textless_pdf()


class TestAMixedDocument:
    """Embedded text on page one, a scan on page two, a blank sheet on page three."""

    DOCUMENT = pdfs.build_pdf([pdfs.TWO_PAGE_TEXT, [], []])

    @pytest.fixture
    def ocr(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(page_count=3, texts={2: "Recognized page two.\n", 3: ""})

    @pytest.fixture
    def content(self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr) -> ContentObject:
        return build(store, ocr).process(pdf_capture(store, self.DOCUMENT))

    def test_only_the_text_page_is_excluded(
        self, content: ContentObject, ocr: FakePdfPageOcr
    ) -> None:
        assert ocr.excluded == frozenset({1})

    def test_there_are_two_segments(self, content: ContentObject) -> None:
        """Three physical pages, one of which recognized to nothing."""
        assert len(content.segments) == 2

    def test_the_embedded_page_keeps_its_extracted_text(self, content: ContentObject) -> None:
        assert content.segments[0].text == pdfs.TWO_PAGE_TEXT_EXTRACTED

    def test_the_two_sources_are_distinguishable(self, content: ContentObject) -> None:
        assert [(segment.type, segment.provenance.source_type) for segment in content.segments] == [
            (SegmentType.TEXT, ProvenanceSourceType.ORIGINAL),
            (SegmentType.OCR, ProvenanceSourceType.OCR),
        ]

    def test_physical_page_numbers_are_preserved(self, content: ContentObject) -> None:
        assert [segment.spatial and segment.spatial.page for segment in content.segments] == [1, 2]

    def test_reading_order_is_contiguous(self, content: ContentObject) -> None:
        """A page that produced nothing leaves a gap in pages and none in position."""
        assert [segment.position for segment in content.segments] == [0, 1]

    def test_the_text_page_is_not_duplicated_as_a_recognition(self, content: ContentObject) -> None:
        texts = [segment.text for segment in content.segments]
        assert texts.count(pdfs.TWO_PAGE_TEXT_EXTRACTED) == 1

    def test_the_metadata_separates_the_three_kinds_of_page(self, content: ContentObject) -> None:
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded[PAGE_COUNT_KEY] == 3
        assert recorded[EMBEDDED_TEXT_PAGES_KEY] == [1]
        assert recorded[OCR_ATTEMPTED_PAGES_KEY] == [2, 3]
        assert recorded[OCR_PAGES_WITHOUT_TEXT_KEY] == [3]

    def test_the_metadata_records_what_actually_ran(self, content: ContentObject) -> None:
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded["engine"] == "fake-ocr"
        assert recorded["engine_version"] == "9.9.9"
        assert recorded["rasterizer"] == "fake-raster"
        assert recorded["rasterizer_version"] == "1.2.3"
        assert recorded["settings"] == {"languages": "fake+fake"}

    def test_the_metadata_invents_no_confidence_score(self, content: ContentObject) -> None:
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert not [key for key in recorded if "confidence" in key or "score" in key]

    def test_the_content_metadata_round_trips_through_json(self, content: ContentObject) -> None:
        """Metadata is ``JsonValue`` space, and this is the proof it stayed there."""
        assert ContentObject.model_validate_json(content.model_dump_json()) == content


class TestPagesThatRecognizeToNothing:
    def test_a_whitespace_only_result_emits_no_segment(self, store: InMemoryRawObjectStore) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "Real text.\n", 2: WHITESPACE_ONLY})

        content = build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

        assert len(content.segments) == 1
        assert content.segments[0].spatial is not None
        assert content.segments[0].spatial.page == 1

    def test_a_whitespace_only_result_is_recorded_as_yielding_nothing(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "Real text.\n", 2: WHITESPACE_ONLY})

        content = build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded[OCR_PAGES_WITHOUT_TEXT_KEY] == [2]

    def test_the_metadata_key_does_not_call_those_pages_blank(self) -> None:
        """An empty result is an observation about the engine, not about the page."""
        assert "blank" not in OCR_PAGES_WITHOUT_TEXT_KEY
        assert "empty" not in OCR_PAGES_WITHOUT_TEXT_KEY

    def test_a_document_nothing_could_be_read_from_is_refused(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "", 2: WHITESPACE_ONLY})

        with pytest.raises(ProcessingInputError, match="yielded no text"):
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    def test_that_refusal_is_a_processing_error_and_not_an_execution_failure(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """It is a verdict about the document, so ``FAILED`` is the honest outcome."""
        ocr = FakePdfPageOcr(page_count=1, texts={1: ""})

        with pytest.raises(ProcessingError) as raised:
            build(store, ocr).process(pdf_capture(store, pdfs.build_pdf([[]])))

        assert not isinstance(raised.value, PdfOcrExecutionError)

    def test_a_document_with_no_pages_at_all_is_refused(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = FakePdfPageOcr(page_count=0)

        with pytest.raises(ProcessingInputError, match="yielded no text"):
            build(store, ocr).process(pdf_capture(store, pdfs.build_pdf([[]])))


class TestTheOriginal:
    DOCUMENT = pdfs.textless_pdf()

    @pytest.fixture
    def content(self, store: InMemoryRawObjectStore) -> ContentObject:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})
        return build(store, ocr).process(pdf_capture(store, self.DOCUMENT))

    def test_there_is_exactly_one_asset_and_it_is_the_original(
        self, content: ContentObject
    ) -> None:
        assert len(content.assets) == 1
        assert content.assets[0].role is AssetRole.ORIGINAL

    def test_no_page_image_is_ever_an_asset(self, content: ContentObject) -> None:
        """Page rasters are temporary computation, so there is nothing to reference."""
        assert [asset.mime_type for asset in content.assets] == [PDF_MIME_TYPE]

    def test_the_asset_reference_is_the_staged_raw_reference(self, content: ContentObject) -> None:
        digest = hashlib.sha256(self.DOCUMENT).hexdigest()
        assert content.assets[0].ref == build_raw_ref(digest)

    def test_the_asset_digest_is_preserved(self, content: ContentObject) -> None:
        assert content.assets[0].sha256 == hashlib.sha256(self.DOCUMENT).hexdigest()

    def test_the_original_reference_points_at_that_asset(self, content: ContentObject) -> None:
        assert content.original.asset_id == content.assets[0].id

    def test_the_original_reference_carries_the_declared_facts(
        self, content: ContentObject
    ) -> None:
        assert content.original.mime_type == PDF_MIME_TYPE
        assert content.original.sha256 == hashlib.sha256(self.DOCUMENT).hexdigest()

    def test_every_segment_points_at_the_original_asset(self, content: ContentObject) -> None:
        assert {segment.provenance.asset_id for segment in content.segments} == {
            content.assets[0].id
        }

    def test_every_segment_names_the_processor_and_version(self, content: ContentObject) -> None:
        for segment in content.segments:
            assert segment.provenance.processor == "pdf-ocr"
            assert segment.provenance.processor_version == "0.1"

    def test_the_exact_pdf_is_still_retrievable_after_processing(
        self, content: ContentObject, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, self.DOCUMENT)
        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == self.DOCUMENT


class TestFreshIdentities:
    def test_every_id_is_minted_fresh(self, store: InMemoryRawObjectStore) -> None:
        """Two captures of the same scan are two content objects, not one."""
        first = build(store, FakePdfPageOcr(page_count=1, texts={1: "x\n"})).process(
            pdf_capture(store, pdfs.textless_pdf(), id="cap_a")
        )
        second = build(store, FakePdfPageOcr(page_count=1, texts={1: "x\n"})).process(
            pdf_capture(store, pdfs.textless_pdf(), id="cap_b")
        )

        assert first.id != second.id
        assert first.assets[0].id != second.assets[0].id
        assert {segment.id for segment in first.segments}.isdisjoint(
            segment.id for segment in second.segments
        )

    def test_no_id_is_derived_from_the_digest(self, store: InMemoryRawObjectStore) -> None:
        document = pdfs.textless_pdf()
        digest = hashlib.sha256(document).hexdigest()

        content = build(store, FakePdfPageOcr(page_count=1, texts={1: "x\n"})).process(
            pdf_capture(store, document)
        )

        assert digest not in {content.id, content.assets[0].id}
        assert digest not in {segment.id for segment in content.segments}


class TestTitlePrecedence:
    def test_the_submitted_title_wins(self, store: InMemoryRawObjectStore) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})
        capture = pdf_capture(
            store, pdfs.build_pdf([[], []], title=pdfs.METADATA_TITLE), title="What I called it"
        )

        assert build(store, ocr).process(capture).title == "What I called it"

    def test_the_metadata_title_is_used_when_the_capture_has_none(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})
        capture = pdf_capture(store, pdfs.build_pdf([[], []], title=pdfs.METADATA_TITLE))

        assert build(store, ocr).process(capture).title == pdfs.METADATA_TITLE

    def test_a_blank_metadata_title_is_treated_as_absent(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})
        capture = pdf_capture(store, pdfs.build_pdf([[], []], title=pdfs.BLANK_METADATA_TITLE))

        assert build(store, ocr).process(capture).title is None

    def test_recognized_text_never_becomes_the_title(self, store: InMemoryRawObjectStore) -> None:
        """The one new source a recognizing build could have invented, and did not."""
        ocr = FakePdfPageOcr(page_count=1, texts={1: "ANNUAL REPORT 2019\n"})

        content = build(store, ocr).process(pdf_capture(store, pdfs.build_pdf([[]])))

        assert content.title is None
        assert content.segments[0].text == "ANNUAL REPORT 2019\n"

    def test_the_reference_and_digest_never_become_the_title(
        self, store: InMemoryRawObjectStore
    ) -> None:
        document = pdfs.textless_pdf()
        ocr = FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})

        content = build(store, ocr).process(pdf_capture(store, document))

        assert content.title is None
        assert hashlib.sha256(document).hexdigest() not in (content.title or "")

    def test_processing_mutates_nothing_on_the_capture_record(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.build_pdf([[], []], title=pdfs.METADATA_TITLE))
        before = capture.model_dump_json()

        build(store, FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})).process(capture)

        assert capture.model_dump_json() == before
        assert capture.title is None


class TestDocumentsThisBuildRefusesBeforeRecognizing:
    """Extraction's verdicts are final. Recognition is not a repair pass."""

    @pytest.fixture
    def ocr(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(page_count=1, texts={1: "the recognizer must not be asked\n"})

    def test_an_encrypted_pdf_is_refused_and_never_rasterized(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        with pytest.raises(ProcessingInputError, match="encrypted"):
            build(store, ocr).process(pdf_capture(store, pdfs.encrypted_pdf()))

        assert ocr.calls == []

    def test_an_empty_password_pdf_is_refused_too(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        with pytest.raises(ProcessingInputError, match="encrypted"):
            build(store, ocr).process(pdf_capture(store, pdfs.encrypted_pdf("")))

        assert ocr.calls == []

    def test_a_malformed_pdf_is_refused_and_never_rasterized(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        with pytest.raises(ProcessingInputError, match="malformed or truncated"):
            build(store, ocr).process(pdf_capture(store, pdfs.corrupt_pdf()))

        assert ocr.calls == []

    def test_bytes_that_are_not_a_pdf_at_all_are_refused(
        self, store: InMemoryRawObjectStore, ocr: FakePdfPageOcr
    ) -> None:
        with pytest.raises(ProcessingInputError):
            build(store, ocr).process(pdf_capture(store, b"this is a text file"))

        assert ocr.calls == []


class TestAWrongAnswerFromTheRecognizer:
    """Delegated to :func:`~core.processing.ocr.validate_ocr_result`, and enforced here."""

    def malformed(self, pages: tuple[RecognizedPage, ...], page_count: int) -> FakePdfPageOcr:
        return FakePdfPageOcr(
            result=PdfOcrResult(
                page_count=page_count,
                pages=pages,
                engine="fake-ocr",
                engine_version="9.9.9",
                rasterizer="fake-raster",
                rasterizer_version="1.2.3",
                settings={},
            )
        )

    def test_a_duplicated_page_is_rejected(self, store: InMemoryRawObjectStore) -> None:
        ocr = self.malformed(
            (
                RecognizedPage(page=1, text="a"),
                RecognizedPage(page=1, text="b"),
            ),
            2,
        )

        with pytest.raises(PdfOcrExecutionError, match="more than once"):
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    def test_a_missing_page_is_rejected(self, store: InMemoryRawObjectStore) -> None:
        ocr = self.malformed((RecognizedPage(page=1, text="a"),), 2)

        with pytest.raises(PdfOcrExecutionError, match="no result for page"):
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    def test_a_page_outside_the_document_is_rejected(self, store: InMemoryRawObjectStore) -> None:
        ocr = self.malformed(
            (
                RecognizedPage(page=1, text="a"),
                RecognizedPage(page=2, text="b"),
                RecognizedPage(page=3, text="c"),
            ),
            2,
        )

        with pytest.raises(PdfOcrExecutionError, match="says has 2 pages"):
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    def test_a_page_the_processor_excluded_is_rejected(self, store: InMemoryRawObjectStore) -> None:
        """An engine that recognized a page it was told to skip is out of control."""
        ocr = self.malformed(
            (
                RecognizedPage(page=1, text="ignored the exclusion"),
                RecognizedPage(page=2, text="b"),
            ),
            3,
        )
        document = pdfs.build_pdf([pdfs.TWO_PAGE_TEXT, [], []])

        with pytest.raises(PdfOcrExecutionError, match="excluded"):
            build(store, ocr).process(pdf_capture(store, document))

    def test_a_page_count_that_contradicts_the_parser_is_rejected(
        self, store: InMemoryRawObjectStore
    ) -> None:
        ocr = self.malformed((), 1)

        with pytest.raises(PdfOcrExecutionError, match="embedded text was extracted from"):
            build(store, ocr).process(pdf_capture(store, pdfs.two_page_pdf()))

    def test_a_malformed_answer_never_becomes_a_blank_document(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """The failure mode this guards: a dropped page read as an empty one."""
        ocr = self.malformed((), 2)

        with pytest.raises(PdfOcrExecutionError):
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

    @pytest.mark.parametrize(
        ("pages", "page_count"),
        [
            ((RecognizedPage(page=1, text="a"),), 2),
            ((RecognizedPage(page=1, text="a"), RecognizedPage(page=1, text="b")), 2),
            ((), 2),
        ],
        ids=["missing", "duplicate", "empty"],
    )
    def test_no_malformed_answer_is_a_processing_error(
        self,
        store: InMemoryRawObjectStore,
        pages: tuple[RecognizedPage, ...],
        page_count: int,
    ) -> None:
        with pytest.raises(PdfOcrExecutionError) as raised:
            build(store, self.malformed(pages, page_count)).process(
                pdf_capture(store, pdfs.textless_pdf())
            )

        assert not isinstance(raised.value, ProcessingError)


class TestFailuresStayDistinguishable:
    """The three ways this can go wrong must not collapse into one another."""

    def test_an_execution_failure_propagates_unchanged(self, store: InMemoryRawObjectStore) -> None:
        failure = PdfOcrExecutionError("the engine exited with status 1")
        ocr = FakePdfPageOcr(raises=failure)

        with pytest.raises(PdfOcrExecutionError) as raised:
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

        assert raised.value is failure

    def test_an_input_verdict_from_the_recognizer_propagates_unchanged(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """A page or pixel limit refusal is a fact about the document."""
        refusal = ProcessingInputError("the document has 900 pages")
        ocr = FakePdfPageOcr(raises=refusal)

        with pytest.raises(ProcessingInputError) as raised:
            build(store, ocr).process(pdf_capture(store, pdfs.textless_pdf()))

        assert raised.value is refusal

    def test_a_storage_failure_is_neither(self, store: InMemoryRawObjectStore) -> None:
        digest = "e" * 64
        capture = make_capture(
            payload_type=CapturePayloadType.DOCUMENT,
            raw_object=RawObjectRef(
                id=digest, mime_type=PDF_MIME_TYPE, sha256=digest, ref=build_raw_ref(digest)
            ),
        )

        with pytest.raises(RawObjectNotFoundError) as raised:
            build(store, FakePdfPageOcr(page_count=1)).process(capture)

        assert not isinstance(raised.value, ProcessingError | PdfOcrExecutionError)

    def test_no_content_is_returned_when_recognition_fails(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Stated explicitly: a failed page is not a partial document."""
        ocr = FakePdfPageOcr(raises=PdfOcrExecutionError("page 2 timed out"))
        capture = pdf_capture(store, pdfs.build_pdf([pdfs.TWO_PAGE_TEXT, []]))

        with pytest.raises(PdfOcrExecutionError):
            build(store, ocr).process(capture)


class TestTheOriginalIsReadTwiceAndOnlyRead:
    def test_the_store_is_opened_once_for_extraction_and_once_for_recognition(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = pdf_capture(store, pdfs.textless_pdf())
        store.accesses.clear()

        build(store, FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})).process(capture)

        assert store.accesses == ["open", "open"]

    def test_nothing_is_written_back_to_the_store(self, store: InMemoryRawObjectStore) -> None:
        capture = pdf_capture(store, pdfs.textless_pdf())
        store.accesses.clear()

        build(store, FakePdfPageOcr(page_count=2, texts={1: "a\n", 2: "b\n"})).process(capture)

        assert "store_bytes" not in store.accesses
        assert "store_stream" not in store.accesses


def test_the_recognizer_reads_a_stream_and_is_given_no_path(
    store: InMemoryRawObjectStore,
) -> None:
    """The port takes a stream so that a path is not expressible across it.

    Asserted against the real store double rather than by reading the signature:
    what the processor hands over is an open file object, and the only thing the
    recognizer can do with it is read.
    """
    received: list[object] = []

    class StreamOnly:
        def recognize_missing_pages(
            self, stream: io.IOBase, *, embedded_pages: frozenset[int]
        ) -> PdfOcrResult:
            received.append(stream)
            assert hasattr(stream, "read")
            return PdfOcrResult(
                page_count=1,
                pages=(RecognizedPage(page=1, text="read from a stream\n"),),
                engine="stream-only",
                engine_version="1",
                rasterizer="stream-only",
                rasterizer_version="1",
                settings={},
            )

    content = PdfOcrProcessor(store, StreamOnly()).process(  # type: ignore[arg-type]
        pdf_capture(store, pdfs.build_pdf([[]]))
    )

    assert len(received) == 1
    assert content.segments[0].text == "read from a stream\n"
