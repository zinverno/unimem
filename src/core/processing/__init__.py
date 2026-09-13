"""Processing: turning accepted captures into canonical content.

Phase 0C answers one question — how does a stored raw original become a
:class:`~core.contracts.content.ContentObject`? A :class:`Processor` normalizes
one modality, a :class:`ProcessorRouter` picks exactly one processor for a
capture, and :class:`TextProcessor` is the first real implementation.

Phase 2 PR 1 adds the second: :class:`WebpageProcessor`, which turns an
immutable original HTML document into a canonical ``web`` content object whose
single segment is a deterministic text extraction of the page. With two
processors registered, the router's exactly-one-match rule stops being a
formality — ``TEXT`` reaches ``TextProcessor`` and ``WEBPAGE`` reaches
``WebpageProcessor`` because each claims its own payload type, not because of
where either sits in a list.

Phase 3 PR 1 adds the third: :class:`PdfProcessor`, which turns a staged
immutable PDF into a canonical ``document`` content object with one text segment
per nonblank page, each carrying the physical page it came from. It is the first
processor to claim a *MIME type* rather than a payload type, which is what lets
a DOCX or EPUB processor join the list later without either of them having to
know the other exists.

Phase 3 PR 2 adds the fourth and collects on that promise: :class:`DocxProcessor`
turns a staged immutable OOXML ``.docx`` into a canonical ``document`` content
object whose segments are the main body's paragraphs and table rows, in body
order and with no page numbers — a DOCX has flow content, and which page a
paragraph lands on is a property of the renderer rather than of the document. It
shares no parsing code with :class:`PdfProcessor` and neither knows the other
exists; they simply claim disjoint MIME types, so the router's
exactly-one-match rule separates them with no precedence, no ordering, and no
generic ``DOCUMENT`` fallback.

Phase 3 PR 3 adds the fifth, and the first that a deployment has to ask for:
:class:`PdfOcrProcessor`. It reads the same staged ``application/pdf`` captures
:class:`PdfProcessor` reads, through the same public
:func:`~core.processing.pdf.extract_pages` helper, and differs in exactly one
respect — the pages that carry no embedded text are handed to a
:class:`~core.processing.ocr.PdfPageOcr` port and become ``OCR`` segments with
``OCR`` provenance instead of making the whole document a failure. Because both
claim the same MIME type they are *alternatives*: a deployment registers one or
the other, never both, and registering both is the router's ambiguity error
rather than a silent precedence. ``core`` still imports no rasterizer, no
imaging library, and no ``subprocess``; the engine lives behind the port in
:mod:`core.processing.ocr`, whose result types are validated before anything
believes them.

Phase 0H adds the step that calls them in order.
:class:`ProcessingOrchestrator` takes a capture id, loads the authoritative
record, insists it is ``stored``, routes it, marks it ``processing`` durably,
runs the one selected processor, and records ``complete`` — or, for a failure
that is a verdict about the capture rather than about the run, ``failed``::

    stored -> processing -> complete
                         -> failed        (ProcessingError only)

Processors themselves stay exactly as pure as ADR-004 made them: they are
handed a record, they return content or raise, and they never write a status.
Every lifecycle write in the system happens in the orchestrator, and nothing
here renders, calls out to a model, or persists a ``ContentObject`` — the
normalized object is returned to the caller and is not yet durable anywhere.
"""

from core.processing.base import Processor
from core.processing.docx import (
    BLOCK_METADATA_KEY,
    CELL_SEPARATOR,
    DOCX_MIME_TYPE,
    PARAGRAPH_BLOCK,
    TABLE_ROW_BLOCK,
    DocxBlock,
    DocxProcessor,
    extract_blocks,
)
from core.processing.errors import (
    AmbiguousProcessorError,
    InvalidCaptureProcessingStateError,
    NoProcessorError,
    ProcessingError,
    ProcessingInputError,
    ProcessingOutputError,
    ProcessorRoutingError,
    TextDecodingError,
)
from core.processing.ocr import (
    PdfOcrExecutionError,
    PdfOcrResult,
    PdfPageOcr,
    RecognizedPage,
    validate_ocr_result,
)
from core.processing.pdf import PDF_MIME_TYPE, PdfProcessor, extract_pages
from core.processing.pdf_ocr import (
    EMBEDDED_TEXT_PAGES_KEY,
    OCR_ATTEMPTED_PAGES_KEY,
    OCR_METADATA_KEY,
    OCR_PAGES_WITHOUT_TEXT_KEY,
    PAGE_COUNT_KEY,
    PdfOcrProcessor,
)
from core.processing.router import ProcessorRouter
from core.processing.service import STARTING_STATUS, ProcessingOrchestrator, utc_now
from core.processing.text import DEFAULT_TEXT_MIME_TYPE, TEXT_ENCODING, TextProcessor
from core.processing.webpage import (
    BLOCK_TAGS,
    DEFAULT_HTML_MIME_TYPE,
    HTML_ENCODING,
    IGNORED_TAGS,
    HtmlTextExtractor,
    WebpageProcessor,
    extract,
)

__all__ = [
    "BLOCK_METADATA_KEY",
    "BLOCK_TAGS",
    "CELL_SEPARATOR",
    "DEFAULT_HTML_MIME_TYPE",
    "DEFAULT_TEXT_MIME_TYPE",
    "DOCX_MIME_TYPE",
    "EMBEDDED_TEXT_PAGES_KEY",
    "HTML_ENCODING",
    "IGNORED_TAGS",
    "OCR_ATTEMPTED_PAGES_KEY",
    "OCR_METADATA_KEY",
    "OCR_PAGES_WITHOUT_TEXT_KEY",
    "PAGE_COUNT_KEY",
    "PARAGRAPH_BLOCK",
    "PDF_MIME_TYPE",
    "STARTING_STATUS",
    "TABLE_ROW_BLOCK",
    "TEXT_ENCODING",
    "AmbiguousProcessorError",
    "DocxBlock",
    "DocxProcessor",
    "HtmlTextExtractor",
    "InvalidCaptureProcessingStateError",
    "NoProcessorError",
    "PdfOcrExecutionError",
    "PdfOcrProcessor",
    "PdfOcrResult",
    "PdfPageOcr",
    "PdfProcessor",
    "ProcessingError",
    "ProcessingInputError",
    "ProcessingOrchestrator",
    "ProcessingOutputError",
    "Processor",
    "ProcessorRouter",
    "ProcessorRoutingError",
    "RecognizedPage",
    "TextDecodingError",
    "TextProcessor",
    "WebpageProcessor",
    "extract",
    "extract_blocks",
    "extract_pages",
    "utc_now",
    "validate_ocr_result",
]
