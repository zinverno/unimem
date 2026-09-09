"""Processing: turning accepted captures into canonical content.

Phase 0C answers one question — how does a stored raw original become a
:class:`~core.contracts.content.ContentObject`? A :class:`Processor` normalizes
one modality, a :class:`ProcessorRouter` picks exactly one processor for a
capture, and :class:`TextProcessor` is the first real implementation.

Nothing here persists, renders, or calls out to a model.
"""

from core.processing.base import Processor
from core.processing.errors import (
    AmbiguousProcessorError,
    NoProcessorError,
    ProcessingError,
    ProcessingInputError,
    ProcessorRoutingError,
    TextDecodingError,
)
from core.processing.router import ProcessorRouter
from core.processing.text import DEFAULT_TEXT_MIME_TYPE, TEXT_ENCODING, TextProcessor

__all__ = [
    "DEFAULT_TEXT_MIME_TYPE",
    "TEXT_ENCODING",
    "AmbiguousProcessorError",
    "NoProcessorError",
    "ProcessingError",
    "ProcessingInputError",
    "Processor",
    "ProcessorRouter",
    "ProcessorRoutingError",
    "TextDecodingError",
    "TextProcessor",
]
