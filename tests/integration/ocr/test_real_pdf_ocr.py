"""The real engine, the real renderer, and fixtures with no text layer to cheat with.

Nothing is faked here. PDFium rasterizes the pages, the installed Tesseract reads
them, and the documents are bitmaps generated on this machine from a system font —
:class:`TestTheFixturesCarryNoTextLayer` proves that through the very extraction
path the processor uses, because a fixture with a text layer would make every
assertion below meaningless.

What these tests assert is that recognizable content arrives, on the right page,
with the right provenance. They deliberately do **not** assert the engine's
whitespace byte for byte: Tesseract builds differ in their line breaking, and a
test that pinned it would fail on a correct result. That UniMem stores whatever the
engine returned *exactly* is proved separately, against a fake engine, in
``tests/unit/processing/test_pdf_ocr_processor.py`` and
``tests/unit/ocr/test_tesseract_adapter.py`` — and once more here, by comparing the
processor's segment against the adapter's own return value for the same page.

This module needs the optional extra, Tesseract, and both language data files.
Where they are absent it skips, unless ``UNIMEM_REQUIRE_PDF_OCR_INTEGRATION`` says
it may not — see :mod:`tests.ocr_support`.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentType,
    ProvenanceSourceType,
    SegmentType,
)
from core.processing import OCR_METADATA_KEY, PDF_MIME_TYPE, PdfOcrProcessor
from core.processing.errors import ProcessingInputError
from core.storage import LocalRawObjectStore
from tests import ocr_support, pdfs

ocr_support.require_local_ocr()

import io  # noqa: E402

from tests import ocr_fixtures  # noqa: E402 - needs Pillow, so only past the guard
from unimem_ocr import build_tesseract_ocr  # noqa: E402
from unimem_ocr.policy import RECOGNITION_LANGUAGES  # noqa: E402

#: A fixed instant; nothing here depends on the clock.
_RECEIVED_AT = datetime(2026, 6, 7, 8, 9, 10, tzinfo=UTC)


def normalized(text: str | None) -> str:
    """Collapse every run of whitespace, so a phrase check is about the words.

    Engine builds legitimately disagree about where a line ends and whether two
    spaces or one separate two words. They do not disagree about what the words
    are, and that is what these tests are for.
    """
    return " ".join((text or "").split())


def capture_for(store: LocalRawObjectStore, document: bytes, capture_id: str) -> CaptureRecord:
    """Stage real bytes in a real content-addressed store and name them."""
    raw_object = store.store_bytes(document, mime_type=PDF_MIME_TYPE)
    return CaptureRecord(
        id=capture_id,
        status=CaptureStatus.STORED,
        received_at=_RECEIVED_AT,
        source=CaptureSource(type=CaptureSourceType.UPLOAD, provider="pytest"),
        payload_type=CapturePayloadType.DOCUMENT,
        raw_object=raw_object,
        context=CaptureContext(captured_at=_RECEIVED_AT),
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalRawObjectStore:
    return LocalRawObjectStore(tmp_path / "raw")


@pytest.fixture(scope="module")
def recognizer() -> object:
    """The genuine adapter, built through the real startup gate."""
    return build_tesseract_ocr()


@pytest.fixture
def processor(store: LocalRawObjectStore, recognizer: object) -> PdfOcrProcessor:
    return PdfOcrProcessor(store, recognizer)  # type: ignore[arg-type]


class TestTheFixturesCarryNoTextLayer:
    """Rendering text to a PDF and keeping its text layer is not an OCR test."""

    @pytest.mark.parametrize(
        "document",
        [
            ocr_fixtures.english_scan(),
            ocr_fixtures.russian_scan(),
            ocr_fixtures.two_page_scan(),
            ocr_fixtures.blank_scan(),
        ],
        ids=["english", "russian", "two-page", "blank"],
    )
    def test_the_existing_extractor_finds_nothing_in_an_image_only_fixture(
        self, document: bytes
    ) -> None:
        assert ocr_fixtures.embedded_text_of(document) == []

    def test_the_mixed_fixture_has_exactly_one_page_of_embedded_text(self) -> None:
        assert ocr_fixtures.embedded_text_of(ocr_fixtures.mixed_document()) == [
            (1, ocr_fixtures.MIXED_EMBEDDED_TEXT)
        ]

    def test_the_image_only_pages_really_are_images(self) -> None:
        """A structurally valid PDF the parser is perfectly happy with."""
        pages, title = ocr_fixtures.embedded_text_of(ocr_fixtures.english_scan()), None

        assert pages == []
        assert title is None


class TestTheEngineReallyReads:
    """The adapter against the installed Tesseract, one language at a time."""

    def test_it_reads_english(self, recognizer: object) -> None:
        result = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(ocr_fixtures.english_scan()), embedded_pages=frozenset()
        )

        read = normalized(result.pages[0].text)
        for phrase in ocr_fixtures.ENGLISH_PHRASES:
            assert phrase in read

    def test_it_reads_russian(self, recognizer: object) -> None:
        result = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(ocr_fixtures.russian_scan()), embedded_pages=frozenset()
        )

        read = normalized(result.pages[0].text)
        for phrase in ocr_fixtures.RUSSIAN_PHRASES:
            assert phrase in read

    def test_both_languages_are_read_by_one_configuration(self, recognizer: object) -> None:
        """``eng+rus`` in one pass: no per-document language guess, no second run."""
        result = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(ocr_fixtures.two_page_scan()), embedded_pages=frozenset()
        )

        assert normalized(result.pages[0].text).startswith("UniMem")
        assert "распознавания" in normalized(result.pages[1].text)

    def test_it_reports_the_installed_versions(self, recognizer: object) -> None:
        result = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(ocr_fixtures.english_scan()), embedded_pages=frozenset()
        )

        assert result.engine == "tesseract"
        assert result.engine_version[0].isdigit()
        assert result.rasterizer == "pypdfium2"
        assert result.rasterizer_version[0].isdigit()
        assert result.settings["languages"] == "+".join(RECOGNITION_LANGUAGES)

    def test_a_blank_page_is_an_empty_result_rather_than_a_failure(
        self, recognizer: object
    ) -> None:
        result = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(ocr_fixtures.blank_scan()), embedded_pages=frozenset()
        )

        assert [page.text.strip() for page in result.pages] == ["", ""]
        assert result.page_count == 2


class TestTheProcessorOverTheRealEngine:
    def test_an_english_scan_becomes_a_document_content_object(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.english_scan(), "cap_real_english")
        )

        assert content.type is ContentType.DOCUMENT
        assert len(content.segments) == 1
        assert content.segments[0].type is SegmentType.OCR
        assert content.segments[0].provenance.source_type is ProvenanceSourceType.OCR
        for phrase in ocr_fixtures.ENGLISH_PHRASES:
            assert phrase in normalized(content.segments[0].text)

    def test_a_russian_scan_becomes_a_document_content_object(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.russian_scan(), "cap_real_russian")
        )

        for phrase in ocr_fixtures.RUSSIAN_PHRASES:
            assert phrase in normalized(content.segments[0].text)

    def test_the_segment_holds_exactly_what_the_adapter_returned(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore, recognizer: object
    ) -> None:
        """Real engine, exact string: recognition is deterministic for one image."""
        document = ocr_fixtures.english_scan()
        direct = recognizer.recognize_missing_pages(  # type: ignore[attr-defined]
            io.BytesIO(document), embedded_pages=frozenset()
        )

        content = processor.process(capture_for(store, document, "cap_real_exact"))

        assert content.segments[0].text == direct.pages[0].text

    def test_a_mixed_document_keeps_its_embedded_text_and_recognizes_the_scan(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.mixed_document(), "cap_real_mixed")
        )

        assert len(content.segments) == 2
        embedded, recognized = content.segments
        assert embedded.text == ocr_fixtures.MIXED_EMBEDDED_TEXT
        assert (embedded.type, embedded.provenance.source_type) == (
            SegmentType.TEXT,
            ProvenanceSourceType.ORIGINAL,
        )
        assert (recognized.type, recognized.provenance.source_type) == (
            SegmentType.OCR,
            ProvenanceSourceType.OCR,
        )
        for phrase in ocr_fixtures.MIXED_SCANNED_PHRASES:
            assert phrase in normalized(recognized.text)

    def test_the_mixed_document_records_which_pages_went_where(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.mixed_document(), "cap_real_mixed_meta")
        )

        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded["page_count"] == 3
        assert recorded["embedded_text_pages"] == [1]
        assert recorded["ocr_attempted_pages"] == [2, 3]
        assert recorded["ocr_pages_without_text"] == [3]

    def test_the_physical_pages_and_reading_order_are_both_right(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.mixed_document(), "cap_real_pages")
        )

        assert [segment.spatial and segment.spatial.page for segment in content.segments] == [1, 2]
        assert [segment.position for segment in content.segments] == [0, 1]

    def test_the_embedded_page_is_not_recognized_twice(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        """The scan's words must not also appear attributed to the text page."""
        content = processor.process(
            capture_for(store, ocr_fixtures.mixed_document(), "cap_real_nodupe")
        )

        assert "middle page" not in normalized(content.segments[0].text)

    def test_the_original_is_unchanged_and_still_retrievable(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        document = ocr_fixtures.english_scan()
        capture = capture_for(store, document, "cap_real_original")

        content = processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == document
        assert content.original.sha256 == hashlib.sha256(document).hexdigest()
        assert [asset.mime_type for asset in content.assets] == [PDF_MIME_TYPE]

    def test_the_engine_and_rasterizer_that_ran_are_recorded(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.english_scan(), "cap_real_engine")
        )

        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded["engine"] == "tesseract"
        assert recorded["rasterizer"] == "pypdfium2"
        settings = recorded["settings"]
        assert isinstance(settings, dict)
        assert settings["languages"] == "eng+rus"
        assert settings["render_dpi"] == 300
        assert settings["psm"] == 3
        assert settings["oem"] == 1

    def test_the_content_object_round_trips_through_json(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        content = processor.process(
            capture_for(store, ocr_fixtures.english_scan(), "cap_real_json")
        )

        assert ContentObject.model_validate_json(content.model_dump_json()) == content


class TestDocumentsTheRealStackRefuses:
    def test_a_blank_scan_is_refused_rather_than_completed_empty(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError, match="yielded no text"):
            processor.process(capture_for(store, ocr_fixtures.blank_scan(), "cap_real_blank"))

    def test_an_encrypted_pdf_is_refused_by_extraction_before_any_rendering(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError, match="encrypted"):
            processor.process(capture_for(store, pdfs.encrypted_pdf(), "cap_real_encrypted"))

    def test_an_empty_password_pdf_is_refused_too(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        """PDFium would open this one; the extraction step refuses it first."""
        with pytest.raises(ProcessingInputError, match="encrypted"):
            processor.process(capture_for(store, pdfs.encrypted_pdf(""), "cap_real_emptypw"))

    def test_malformed_input_is_refused(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError, match="malformed or truncated"):
            processor.process(capture_for(store, pdfs.corrupt_pdf(), "cap_real_corrupt"))

    def test_bytes_that_are_not_a_pdf_are_refused(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            processor.process(capture_for(store, b"a plain text file", "cap_real_text"))

    def test_an_ordinary_text_pdf_needs_no_recognition_at_all(
        self, processor: PdfOcrProcessor, store: LocalRawObjectStore
    ) -> None:
        """The OCR build must not change what a text PDF becomes."""
        content = processor.process(capture_for(store, pdfs.two_page_pdf(), "cap_real_textpdf"))

        assert [segment.type for segment in content.segments] == [SegmentType.TEXT] * 2
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded["ocr_attempted_pages"] == []
