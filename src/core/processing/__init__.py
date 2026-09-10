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
    "BLOCK_TAGS",
    "DEFAULT_HTML_MIME_TYPE",
    "DEFAULT_TEXT_MIME_TYPE",
    "HTML_ENCODING",
    "IGNORED_TAGS",
    "STARTING_STATUS",
    "TEXT_ENCODING",
    "AmbiguousProcessorError",
    "HtmlTextExtractor",
    "InvalidCaptureProcessingStateError",
    "NoProcessorError",
    "ProcessingError",
    "ProcessingInputError",
    "ProcessingOrchestrator",
    "ProcessingOutputError",
    "Processor",
    "ProcessorRouter",
    "ProcessorRoutingError",
    "TextDecodingError",
    "TextProcessor",
    "WebpageProcessor",
    "extract",
    "utc_now",
]
