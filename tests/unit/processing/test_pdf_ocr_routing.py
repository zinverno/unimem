"""Two implementations of one claim, and the router that refuses to choose.

``PdfProcessor`` and ``PdfOcrProcessor`` both claim ``DOCUMENT`` +
``application/pdf``. That is not an oversight to be patched over with precedence
— it is the design: a deployment picks one, and the router's exactly-one-match
rule is what turns a mistaken double registration into a loud error instead of a
silent preference. These tests use the genuine processor sets the composition root
registers, in the order it registers them.
"""

import pytest

from core.contracts import CapturePayloadType, CaptureRecord
from core.processing import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    AmbiguousProcessorError,
    DocxProcessor,
    NoProcessorError,
    PdfOcrProcessor,
    PdfProcessor,
    Processor,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from tests import docxs, pdfs
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import FakePdfPageOcr, InMemoryRawObjectStore


@pytest.fixture
def default_processors(store: InMemoryRawObjectStore) -> list[Processor]:
    """What ``build_local_app`` registers without ``--pdf-ocr``."""
    return [
        TextProcessor(store),
        WebpageProcessor(store),
        PdfProcessor(store),
        DocxProcessor(store),
    ]


@pytest.fixture
def ocr_processors(store: InMemoryRawObjectStore) -> list[Processor]:
    """What it registers *with* ``--pdf-ocr``: the same list, one substitution."""
    return [
        TextProcessor(store),
        WebpageProcessor(store),
        PdfOcrProcessor(store, FakePdfPageOcr()),
        DocxProcessor(store),
    ]


def pdf_capture(store: InMemoryRawObjectStore) -> CaptureRecord:
    return store_and_capture(
        store,
        pdfs.textless_pdf(),
        mime_type=PDF_MIME_TYPE,
        payload_type=CapturePayloadType.DOCUMENT,
    )


def docx_capture(store: InMemoryRawObjectStore) -> CaptureRecord:
    return store_and_capture(
        store,
        docxs.paragraphs_docx(docxs.PARAGRAPH_A),
        mime_type=DOCX_MIME_TYPE,
        payload_type=CapturePayloadType.DOCUMENT,
    )


def test_the_ocr_set_still_has_exactly_one_claimant_for_every_kind(
    ocr_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    captures = [
        make_capture(),
        make_capture(payload_type=CapturePayloadType.WEBPAGE),
        pdf_capture(store),
        docx_capture(store),
    ]

    for capture in captures:
        assert sum(processor.supports(capture) for processor in ocr_processors) == 1


def test_a_pdf_reaches_the_recognizing_processor_in_the_ocr_set(
    ocr_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    selected = ProcessorRouter(ocr_processors).select(pdf_capture(store))

    assert isinstance(selected, PdfOcrProcessor)
    assert (selected.name, selected.version) == ("pdf-ocr", "0.1")


def test_the_other_three_processors_are_untouched_by_the_substitution(
    default_processors: list[Processor],
    ocr_processors: list[Processor],
    store: InMemoryRawObjectStore,
) -> None:
    """Only the PDF slot differs; text, webpage, and DOCX route identically."""
    default = ProcessorRouter(default_processors)
    recognizing = ProcessorRouter(ocr_processors)
    unchanged = [
        make_capture(),
        make_capture(payload_type=CapturePayloadType.WEBPAGE),
        docx_capture(store),
    ]

    for capture in unchanged:
        assert type(default.select(capture)) is type(recognizing.select(capture))


def test_registering_both_pdf_processors_is_ambiguous(
    default_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """The reason mutual exclusion lives in composition rather than in the router."""
    router = ProcessorRouter([*default_processors, PdfOcrProcessor(store, FakePdfPageOcr())])

    with pytest.raises(AmbiguousProcessorError) as raised:
        router.select(pdf_capture(store))

    assert "pdf@0.1" in str(raised.value)
    assert "pdf-ocr@0.1" in str(raised.value)


def test_the_ambiguity_does_not_depend_on_registration_order(
    default_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """Order is not precedence, so reversing the list changes nothing."""
    both = [*default_processors, PdfOcrProcessor(store, FakePdfPageOcr())]

    for candidates in (both, list(reversed(both))):
        with pytest.raises(AmbiguousProcessorError):
            ProcessorRouter(candidates).select(pdf_capture(store))


def test_a_second_recognizing_processor_is_ambiguous_too(
    ocr_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    router = ProcessorRouter([*ocr_processors, PdfOcrProcessor(store, FakePdfPageOcr())])

    with pytest.raises(AmbiguousProcessorError):
        router.select(pdf_capture(store))


def test_the_ocr_processor_is_not_a_fallback_for_other_document_formats(
    ocr_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """It claims one MIME type, so an unknown format is still a loud error."""
    capture = store_and_capture(
        store,
        pdfs.textless_pdf(),
        mime_type="application/epub+zip",
        payload_type=CapturePayloadType.DOCUMENT,
    )

    with pytest.raises(NoProcessorError):
        ProcessorRouter(ocr_processors).select(capture)


def test_routing_in_the_ocr_set_reads_no_storage(
    ocr_processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    capture = pdf_capture(store)
    store.accesses.clear()

    ProcessorRouter(ocr_processors).select(capture)

    assert store.accesses == []


def test_routing_never_runs_the_recognizer(store: InMemoryRawObjectStore) -> None:
    ocr = FakePdfPageOcr(page_count=2)
    router = ProcessorRouter(
        [
            TextProcessor(store),
            WebpageProcessor(store),
            PdfOcrProcessor(store, ocr),
            DocxProcessor(store),
        ]
    )

    router.select(pdf_capture(store))

    assert ocr.calls == []
