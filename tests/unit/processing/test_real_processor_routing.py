"""The four real processors in one router, and the routing that follows.

The router's exactly-one-match rule was a formality with one processor and real
work with two. With four — two of them claiming *MIME types* within the same
``DOCUMENT`` payload type — it is what keeps document formats apart from each
other, and Phase 3 PR 2 is where that stops being hypothetical: a PDF capture and
a DOCX capture are the same payload type, and only the declared format tells them
apart. These tests use the genuine processor set the composition root registers,
in the order it registers them, and assert that order never decides anything.
"""

from collections.abc import Callable

import pytest

from core.contracts import CapturePayloadType, CaptureRecord
from core.processing import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    AmbiguousProcessorError,
    DocxProcessor,
    NoProcessorError,
    PdfProcessor,
    Processor,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from tests import docxs, pdfs
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore


@pytest.fixture
def processors(store: InMemoryRawObjectStore) -> list[Processor]:
    """Exactly what ``build_local_app`` registers, in exactly that order."""
    return [
        TextProcessor(store),
        WebpageProcessor(store),
        PdfProcessor(store),
        DocxProcessor(store),
    ]


@pytest.fixture
def router(processors: list[Processor]) -> ProcessorRouter:
    return ProcessorRouter(processors)


def pdf_capture(store: InMemoryRawObjectStore, **overrides: object) -> CaptureRecord:
    fields: dict[str, object] = {"payload_type": CapturePayloadType.DOCUMENT}
    return store_and_capture(
        store, pdfs.one_page_pdf(), mime_type=PDF_MIME_TYPE, **(fields | overrides)
    )


def docx_capture(store: InMemoryRawObjectStore, **overrides: object) -> CaptureRecord:
    fields: dict[str, object] = {"payload_type": CapturePayloadType.DOCUMENT}
    return store_and_capture(
        store,
        docxs.paragraphs_docx(docxs.PARAGRAPH_A),
        mime_type=DOCX_MIME_TYPE,
        **(fields | overrides),
    )


def test_a_text_capture_reaches_the_text_processor(
    router: ProcessorRouter, processors: list[Processor]
) -> None:
    assert router.select(make_capture()) is processors[0]


def test_a_webpage_capture_reaches_the_webpage_processor(
    router: ProcessorRouter, processors: list[Processor]
) -> None:
    assert router.select(make_capture(payload_type=CapturePayloadType.WEBPAGE)) is processors[1]


def test_a_pdf_document_reaches_the_pdf_processor(
    router: ProcessorRouter, processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    assert router.select(pdf_capture(store)) is processors[2]


def test_a_docx_document_reaches_the_docx_processor(
    router: ProcessorRouter, processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    assert router.select(docx_capture(store)) is processors[3]


def test_the_two_document_processors_never_claim_each_other_s_captures(
    processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """Same payload type, disjoint claims. This is the whole routing design."""
    pdf, docx = processors[2], processors[3]

    assert pdf.supports(pdf_capture(store))
    assert not pdf.supports(docx_capture(store))
    assert docx.supports(docx_capture(store))
    assert not docx.supports(pdf_capture(store))


def test_exactly_one_processor_claims_each_kind(
    processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """No ambiguity anywhere: routing needs no tie-break and gets none."""
    captures = [
        make_capture(),
        make_capture(payload_type=CapturePayloadType.WEBPAGE),
        pdf_capture(store),
        docx_capture(store),
    ]

    for capture in captures:
        assert sum(processor.supports(capture) for processor in processors) == 1


def test_registration_order_is_not_precedence(
    processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """Reverse the list and every capture still reaches the same processor."""
    forwards = ProcessorRouter(processors)
    backwards = ProcessorRouter(list(reversed(processors)))
    captures = [
        make_capture(),
        make_capture(payload_type=CapturePayloadType.WEBPAGE),
        pdf_capture(store),
        docx_capture(store),
    ]

    for capture in captures:
        assert forwards.select(capture) is backwards.select(capture)


def test_there_is_no_fallback_processor(
    router: ProcessorRouter, store: InMemoryRawObjectStore
) -> None:
    """A document format nobody handles is a loud error, not a default."""
    capture = store_and_capture(
        store,
        pdfs.one_page_pdf(),
        mime_type="application/epub+zip",
        payload_type=CapturePayloadType.DOCUMENT,
    )

    with pytest.raises(NoProcessorError):
        router.select(capture)


def test_a_second_document_processor_claiming_the_same_mime_type_is_an_error(
    processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    """The rule that makes claiming a MIME type worth doing.

    If ``PdfProcessor`` claimed the whole ``document`` payload type instead,
    every DOCX capture would now be ambiguous — the day that processor arrived
    has arrived. Two processors claiming *the same format* is still an error,
    and should be.
    """
    router = ProcessorRouter([*processors, PdfProcessor(store)])

    with pytest.raises(AmbiguousProcessorError):
        router.select(pdf_capture(store))


def test_a_second_docx_processor_is_an_error_too(
    processors: list[Processor], store: InMemoryRawObjectStore
) -> None:
    router = ProcessorRouter([*processors, DocxProcessor(store)])

    with pytest.raises(AmbiguousProcessorError):
        router.select(docx_capture(store))


@pytest.mark.parametrize("build", [pdf_capture, docx_capture], ids=["pdf", "docx"])
def test_routing_reads_no_storage(
    router: ProcessorRouter,
    store: InMemoryRawObjectStore,
    build: Callable[[InMemoryRawObjectStore], CaptureRecord],
) -> None:
    """Every processor is asked about every capture; none of them may read bytes."""
    capture = build(store)
    store.accesses.clear()

    router.select(capture)

    assert store.accesses == []
