"""The PDF page recognition port.

One narrow boundary, for one job: hand a PDF stream to something that can look
at the pixels of the pages that carry no embedded text, and get back what it
read. It is deliberately *not* a vision framework, a provider registry, an
"engine" abstraction with pluggable models, or a general image-understanding
interface. There is one method, it takes a PDF, and it answers about pages.

**Why this exists at all.** ``core`` may not import a rasterizer, a native
library, ``subprocess``, or an OCR engine — recognition is an infrastructure
capability with system prerequisites, and the canonical policy that decides
*which* pages get recognized and *what* the resulting content means must stay
testable with none of that installed. So the policy lives in
:mod:`core.processing.pdf_ocr`, the machinery lives in a concrete adapter
outside ``core``, and this module is the typed seam between them.

**What crosses the seam is values, not objects.** The input is a binary stream
— never a caller-controlled path, because a path is a filesystem instruction
and this port must not be able to express one. What comes back is a small
frozen dataclass holding plain strings and integers. No bitmap, no image, no
file handle, no native object, and no temporary path ever reaches canonical
processing; whatever an adapter allocated is its own to release before it
returns.

**The result is validated before it is believed.** An adapter is
infrastructure, and infrastructure can be wrong in ways that would otherwise
turn into quiet content loss: a page reported twice, a page number outside the
document, a page the caller already said was covered by embedded text, or —
worst of all — a page simply missing from the answer, which would look exactly
like a page that legitimately recognized to nothing. :func:`validate_ocr_result`
rejects all of those as :class:`PdfOcrExecutionError`, which is an *execution*
failure and not a verdict about the document. A malformed provider answer must
never become a blank document.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from pydantic import JsonValue


class PdfOcrExecutionError(Exception):
    """Recognition could not be carried out, and nothing is known about the pages.

    Deliberately **not** a
    :class:`~core.processing.errors.ProcessingError`, and that is the whole
    point of the type. A ``ProcessingError`` is a verdict about the submitted
    capture — these bytes are encrypted, this document carries nothing readable
    — and the orchestrator durably marks such a capture ``failed`` because
    trying again would produce the same answer. This error says the opposite:
    the engine was missing, crashed, timed out, or answered inconsistently, so
    *nothing was learned about the document at all*. Marking the capture
    ``failed`` on the strength of it would record a judgement the system never
    made.

    Because it is outside the ``ProcessingError`` hierarchy it flows through
    :class:`~core.processing.service.ProcessingOrchestrator` untouched: the
    capture stays ``processing``, no content object is built or persisted, and
    the delivery layer answers a fixed 503. See
    :mod:`unimem_api.errors`.

    Its message is written for a server log rather than for a client. Nothing
    here is passed to the wire.
    """


@dataclass(frozen=True, slots=True)
class RecognizedPage:
    """What recognition read from one physical page.

    ``page`` is the 1-based *physical* page number in the PDF, which is the only
    page coordinate this system recognises — it is the same number
    :func:`core.processing.pdf.extract_pages` reports and the same one that
    reaches ``Segment.spatial.page``.

    ``text`` is exactly what the engine returned, including an **empty string**
    when recognition succeeded and found nothing. That case is a result, not an
    omission: a page that recognized to nothing has been looked at, and saying
    so is different from not mentioning the page. Nothing here is trimmed,
    normalized, or repaired — see :mod:`core.processing.pdf_ocr` for why.
    """

    page: int
    text: str


@dataclass(frozen=True, slots=True)
class PdfOcrResult:
    """Everything one recognition run reports back.

    ``page_count`` is the document's *actual* physical page count as the
    rasterizer counted it, not a count of results. It is what lets the caller
    tell "page 4 recognized to nothing" from "page 4 was never looked at", and
    it is checked against the embedded-text pages the caller supplied, because
    two readers disagreeing about how many pages a document has is a fact worth
    failing on rather than papering over.

    ``pages`` holds one :class:`RecognizedPage` for **every** page the caller
    did not exclude, in any order. Missing entries are an error, not an
    optimization.

    The four identity fields are what actually ran — the engine and rasterizer
    names and the versions reported by the installed software, not constants
    compiled in here. ``settings`` is the *effective* recognition
    configuration: the languages, the rasterization resolution, the engine
    modes, the limits in force. Both travel onto the canonical content object so
    that a segment can be re-read years later against the exact configuration
    that produced it. They are recorded and never interpreted: nothing in
    ``core`` branches on an engine name.
    """

    page_count: int
    pages: tuple[RecognizedPage, ...]
    engine: str
    engine_version: str
    rasterizer: str
    rasterizer_version: str
    settings: Mapping[str, JsonValue]


class PdfPageOcr(Protocol):
    """Recognizes the pages of a PDF that carry no embedded text.

    Synchronous, like every other port in ``core``, and for the same reason:
    the one thing calling it is a synchronous processor, and an awaitable
    surface for implementations that do not exist yet would buy nothing.
    """

    def recognize_missing_pages(
        self, stream: BinaryIO, *, embedded_pages: frozenset[int]
    ) -> PdfOcrResult:
        """Rasterize and recognize every page not named in ``embedded_pages``.

        ``stream`` is an open, rewound binary stream positioned at the start of
        a PDF. The implementation reads it and closes nothing it did not open;
        the caller owns the handle.

        ``embedded_pages`` is the set of 1-based physical page numbers the
        caller has *already* covered from the document's own embedded text.
        Those pages must not be rasterized, must not be recognized, and must
        not appear in the result — the caller has better text for them than any
        recognizer could produce, and spending time on them would be spending
        someone's page budget on an answer that will be thrown away.

        Raises :class:`~core.processing.errors.ProcessingInputError` when the
        *document* is the problem in a way the implementation can state as a
        fact about the input: it is encrypted, it is not readable as a PDF at
        all, or it exceeds an explicit, documented resource limit. Raises
        :class:`PdfOcrExecutionError` when the *run* is the problem: the engine
        is missing, it failed, it timed out, or it answered with something that
        is not a consistent description of this document.
        """
        ...


def validate_ocr_result(result: PdfOcrResult, *, embedded_pages: frozenset[int]) -> None:
    """Check that a result is a consistent description of the document, or raise.

    Every rule here exists because breaking it would corrupt canonical content
    silently rather than loudly:

    * **A sane page count.** Negative page counts, and an embedded page number
      the rasterizer says does not exist, mean the two readers are not looking
      at the same document.
    * **Positive, in-range page numbers.** A page ``0`` or ``-1`` cannot become
      a ``Segment.spatial.page``, and a page past the end names nothing.
    * **No duplicates.** Two results for one page is two answers where there
      can only be one, and picking either would be inventing a tie-break.
    * **No overlap with embedded pages.** The caller said those pages are
      covered. A result for one of them means the implementation ignored the
      exclusion, and its text would compete with text the document itself
      carries.
    * **Nothing missing.** Every page that is not embedded-covered must be
      accounted for. This is the rule that matters most: without it, an
      implementation that quietly dropped half a document would be
      indistinguishable from a document whose second half is blank, and the
      result would be a ``COMPLETE`` content object missing content nobody
      knows is missing.

    Raises :class:`PdfOcrExecutionError`, never
    :class:`~core.processing.errors.ProcessingInputError`: an implementation
    that answers inconsistently has told us nothing about the document, so the
    document must not be blamed for it.
    """
    if result.page_count < 0:
        raise PdfOcrExecutionError(f"the recognizer reported a page count of {result.page_count}")

    physical = frozenset(range(1, result.page_count + 1))
    if not embedded_pages <= physical:
        beyond = sorted(embedded_pages - physical)
        raise PdfOcrExecutionError(
            f"the recognizer reported {result.page_count} physical pages, but embedded text "
            f"was extracted from page(s) {beyond}"
        )

    reported: set[int] = set()
    for page in result.pages:
        if page.page < 1:
            raise PdfOcrExecutionError(
                f"the recognizer reported a result for page {page.page}, which is not a "
                f"physical page number"
            )
        if page.page > result.page_count:
            raise PdfOcrExecutionError(
                f"the recognizer reported a result for page {page.page} of a document it "
                f"says has {result.page_count} pages"
            )
        if page.page in embedded_pages:
            raise PdfOcrExecutionError(
                f"the recognizer reported a result for page {page.page}, which was excluded "
                f"as already covered by embedded text"
            )
        if page.page in reported:
            raise PdfOcrExecutionError(f"the recognizer reported page {page.page} more than once")
        reported.add(page.page)

    missing = sorted(physical - embedded_pages - reported)
    if missing:
        raise PdfOcrExecutionError(
            f"the recognizer returned no result for page(s) {missing}; a page that was not "
            f"looked at is not a page that recognized to nothing"
        )
