"""The local PDF page recognizer: PDFium for pixels, Tesseract for words.

The one concrete implementation of :class:`~core.processing.ocr.PdfPageOcr`, and
the only module in this repository that touches a native rendering library or
starts a subprocess. It lives outside ``core`` because that is what makes the
canonical policy in :mod:`core.processing.pdf_ocr` testable on a machine with
neither installed.

**Everything is local.** No network call, no cloud OCR service, no model
download, no telemetry, and no external resource fetching of any kind. PDFium is
told not to initialize forms, so no JavaScript, XFA, or form-field interaction is
ever set up; annotations and form fields are not rendered, which keeps this from
quietly becoming the build that started drawing document features nobody asked it
to read. A PDF that references a remote image gets a page without it.

**Nothing a client sent becomes an instruction.** The engine is invoked with a
fixed argument list and ``shell=False``: no shell string is constructed, and no
filename, URL, document metadata, capture title, or request field appears in
``argv``, as an option, as an output path, or as a configuration path. The page
image travels in on the child's stdin and the recognized text comes back on its
stdout. There is no temporary file, so there is no temporary path to collide,
leak, or be guessed.

**PDFium is not thread-safe across documents, and this module takes that
seriously.** A single process-wide lock guards *every* call into the native
library, including opening and closing documents and pages, because destruction
is a native call too and a bitmap released while another thread is rendering is
the same bug as two threads rendering at once. The lock is deliberately *not*
held while Tesseract runs: the subprocess is where the wall-clock time goes, and
holding a native-library lock across it would serialize an entire multi-worker
server behind one OCR run for no safety benefit. Nothing here assumes the HTTP
server is single-threaded — Uvicorn runs synchronous route handlers on a thread
pool, so two captures really can be inside this module at once.

The lock is a native-library mutual-exclusion boundary and nothing else. It is
not capture ownership, not idempotency, not a worker lease, and not a queue; it
says only "one thread inside PDFium at a time".

**Resources are released explicitly, including on failure.** Every page, bitmap,
and image is closed in a ``finally``, and the document is closed in an outer
``finally``, so nothing waits on a garbage collector to run a finalizer at an
unpredictable moment on an unpredictable thread. Exactly one page raster exists
at a time: the bitmap for page *n* is freed before page *n+1* is rendered, so a
fifty-page scan costs one page of pixels rather than fifty.
"""

import io
import math
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from typing import BinaryIO, Final

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from pydantic import JsonValue
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

from core.processing.errors import ProcessingInputError
from core.processing.ocr import PdfOcrExecutionError, PdfOcrResult, RecognizedPage
from unimem_ocr.policy import (
    DEFAULT_LIMITS,
    PAGE_IMAGE_FORMAT,
    RECOGNITION_LANGUAGES,
    RENDER_DPI,
    RENDER_FILL_COLOR,
    RENDER_SCALE,
    TESSERACT_EXECUTABLE,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
    OcrLimits,
)

#: The process-wide boundary around every call into PDFium.
#:
#: Module level, so it is shared by every adapter instance in the process — two
#: independently constructed adapters serving two concurrent captures must not
#: each have their own lock, which would be no lock at all. It is a plain
#: ``threading.Lock`` and is never held across a subprocess, an I/O wait, or a
#: call back into ``core``.
_PDFIUM_LOCK: Final = threading.Lock()

#: What this adapter calls itself on a content object.
ENGINE_NAME: Final = "tesseract"

#: What this adapter calls its rasterizer on a content object.
RASTERIZER_NAME: Final = "pypdfium2"

#: The PDFium load-failure codes that are genuinely verdicts about the *document*.
#:
#: Document loading is the one PDFium API that reports a distinguishable reason:
#: ``PdfiumError.err_code`` is populated there and is ``None`` everywhere else.
#: That reason is what decides the classification — **not** the fact that the
#: failure happened during loading, and never the exception's message text, which
#: is prose from another library and not a stable signal.
#:
#: Three codes, each an honest statement about the submitted bytes:
#:
#: * ``FPDF_ERR_FORMAT`` — the data is not a PDF this renderer can parse. The
#:   document is malformed or truncated.
#: * ``FPDF_ERR_PASSWORD`` — the document needs a password. This build reads
#:   unencrypted PDFs and attempts none.
#: * ``FPDF_ERR_SECURITY`` — the document uses a security scheme this build
#:   cannot read. A capability limit, stated as one rather than as a crash.
#:
#: Everything else is deliberately **absent**, and the absences are the point:
#:
#: * ``FPDF_ERR_UNKNOWN`` — PDFium itself declined to say what went wrong, so
#:   neither can this adapter.
#: * ``FPDF_ERR_FILE`` — an access failure while reading. That describes the
#:   stream or the machine, not the document's contents.
#: * ``FPDF_ERR_PAGE`` — "page not found or content error". It belongs to the
#:   page-loading APIs, and what it would mean *at document load* is not
#:   something this adapter can justify without guessing. Guessing here would
#:   durably fail somebody's capture.
#: * ``FPDF_ERR_SUCCESS``, a missing ``err_code``, and any code this build does
#:   not recognize — including one a future PDFium adds — are unproven by
#:   construction.
#:
#: An unproven load failure becomes a
#: :class:`~core.processing.ocr.PdfOcrExecutionError`, which leaves the capture
#: non-terminal. That is the safe direction to be wrong in: a retry against a
#: repaired deployment costs a request, while a wrong ``FAILED`` is a permanent
#: claim that someone's document is unreadable.
_LOAD_INPUT_ERROR_CODES: Final[frozenset[int]] = frozenset(
    {
        pdfium_raw.FPDF_ERR_FORMAT,
        pdfium_raw.FPDF_ERR_PASSWORD,
        pdfium_raw.FPDF_ERR_SECURITY,
    }
)


class TesseractPdfPageOcr:
    """Rasterizes PDF pages with PDFium and recognizes them with local Tesseract."""

    def __init__(
        self,
        *,
        engine_version: str,
        executable: str = TESSERACT_EXECUTABLE,
        limits: OcrLimits = DEFAULT_LIMITS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Take the engine's reported version, where to find it, and its bounds.

        ``engine_version`` is required rather than defaulted because it is
        recorded on every content object this adapter contributes to, and a
        placeholder would travel into durable canonical content and stay there.
        :func:`unimem_ocr.build_tesseract_ocr` obtains it by asking the installed
        executable during startup validation, which is the only honest source.

        ``executable`` and ``limits`` are deployment configuration: they are
        supplied here, at composition time, and nothing reads them from an
        environment variable, a file, or a request.

        ``monotonic`` is the clock the per-document budget is measured on. It is
        a parameter for the same reason the orchestrator's clock is — a test
        needs to control time — and it is monotonic rather than wall-clock so
        that a system clock adjustment mid-document cannot hand a page a
        negative or enormous timeout.
        """
        self._engine_version = engine_version
        self._executable = executable
        self._limits = limits
        self._monotonic = monotonic

    def recognize_missing_pages(
        self, stream: BinaryIO, *, embedded_pages: frozenset[int]
    ) -> PdfOcrResult:
        """Rasterize and recognize every page not already covered by embedded text.

        The sequence, and where each safeguard sits in it::

            open the document                      (PDFium, under the lock)
            count its pages                        (PDFium, under the lock)
            refuse a document over the page limit  -- before any rasterization
            for each page not excluded:
                refuse to begin if the budget is spent
                take the lock, then refuse again if waiting for it spent the
                    budget                         -- before the page is opened
                check the computed pixel count     -- before any allocation
                render, encode to PNG, free        (PDFium, under the lock)
                recognize                          (subprocess, lock released)
            close the document                     (PDFium, under the lock)

        Excluded pages are skipped entirely: not opened, not measured, not
        rendered, and not recognized. That is not an optimization, it is the
        policy — the caller has the document's own text for those pages, and
        recognizing them anyway would spend a scanned document's page budget
        producing text that would be discarded.

        Raises :class:`~core.processing.errors.ProcessingInputError` for a
        document PDFium cannot open and for either explicit resource limit, and
        :class:`~core.processing.ocr.PdfOcrExecutionError` for a failure of the
        run: a missing or unusable engine, a nonzero exit, a page timeout, an
        exhausted document budget, undecodable output, or a rendering failure in
        a document that had already opened successfully.
        """
        deadline = self._monotonic() + self._limits.document_budget_seconds
        document = self._open(stream)
        try:
            page_count = self._page_count(document)
            if page_count > self._limits.max_pages:
                # Refused whole, before a single page is touched. Recognizing the
                # first fifty pages of a two-hundred-page scan and saying nothing
                # about the rest would be the silent truncation this limit exists
                # to prevent.
                raise ProcessingInputError(
                    f"the document has {page_count} pages; this build recognizes documents of "
                    f"at most {self._limits.max_pages} pages"
                )

            recognized: list[RecognizedPage] = []
            for number in range(1, page_count + 1):
                if number in embedded_pages:
                    continue
                if self._monotonic() >= deadline:
                    raise PdfOcrExecutionError(
                        f"the {self._limits.document_budget_seconds:.0f}s recognition budget "
                        f"for this document was exhausted before page {number} was begun"
                    )
                image = self._render(document, number, deadline=deadline)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise PdfOcrExecutionError(
                        f"the {self._limits.document_budget_seconds:.0f}s recognition budget "
                        f"for this document was exhausted while preparing page {number}"
                    )
                text = self._recognize(
                    image,
                    page=number,
                    timeout=min(remaining, self._limits.page_timeout_seconds),
                )
                recognized.append(RecognizedPage(page=number, text=text))
        finally:
            self._close(document)

        return PdfOcrResult(
            page_count=page_count,
            pages=tuple(recognized),
            engine=ENGINE_NAME,
            engine_version=self._engine_version,
            rasterizer=RASTERIZER_NAME,
            rasterizer_version=str(PYPDFIUM_INFO.version),
            settings=self.settings(),
        )

    def settings(self) -> Mapping[str, JsonValue]:
        """The effective recognition configuration, as recorded on the content.

        Every value that shaped what the engine saw and how it was asked to read
        it, plus the limits that were in force. It is a description of this run,
        not a schema: it lands in ``ContentObject.metadata``, which is free-form
        JSON, so adding a setting later does not change any contract.
        """
        return {
            "languages": TESSERACT_LANGUAGE_ARGUMENT,
            "language_order": list(RECOGNITION_LANGUAGES),
            "render_dpi": RENDER_DPI,
            "render_background": "opaque_white",
            "render_annotations": False,
            "render_form_fields": False,
            "page_image_format": PAGE_IMAGE_FORMAT,
            "oem": TESSERACT_OEM,
            "psm": TESSERACT_PSM,
            "orientation_detection": False,
            "deskew": False,
            "retries": 0,
            "pdfium_version": str(PDFIUM_INFO.version),
            "max_pages": self._limits.max_pages,
            "max_raster_pixels": self._limits.max_raster_pixels,
            "page_timeout_seconds": self._limits.page_timeout_seconds,
            "document_budget_seconds": self._limits.document_budget_seconds,
        }

    # --- PDFium, always under the lock --------------------------------------

    def _open(self, stream: BinaryIO) -> pdfium.PdfDocument:
        """Load the document from the stream the caller owns.

        ``autoclose=False``: the handle belongs to the caller's ``with`` block
        and this adapter does not get to close someone else's stream.

        **Where the failure happened does not decide what it means.** A
        ``PdfiumError`` raised here is classified by its ``err_code`` against
        :data:`_LOAD_INPUT_ERROR_CODES` — the allowlist of reasons that are
        actually statements about the submitted bytes. A recognized input reason
        becomes a :class:`~core.processing.errors.ProcessingInputError`; an
        unknown reason, an access failure, a missing code, or a code this build
        does not recognize becomes a
        :class:`~core.processing.ocr.PdfOcrExecutionError` and leaves the capture
        non-terminal, because "the renderer failed while opening this" is not
        evidence that the document is bad.

        The code is the only thing consulted. The exception's message is never
        parsed: it is another library's prose, it is not a stable interface, and
        it can name byte offsets and local paths.

        Nothing broader is caught. A ``TypeError`` from a defect in this module is
        a bug, not a verdict about someone's scan.
        """
        with _PDFIUM_LOCK:
            try:
                return pdfium.PdfDocument(stream, autoclose=False)
            except pdfium.PdfiumError as exc:
                code = getattr(exc, "err_code", None)
                if code in _LOAD_INPUT_ERROR_CODES:
                    raise ProcessingInputError(
                        "the document could not be opened for rasterization; it appears to be "
                        "malformed, truncated, password-protected, or protected by an "
                        "encryption scheme this build cannot read. This build recognizes "
                        "unencrypted PDFs only and does not attempt passwords"
                    ) from exc
                raise PdfOcrExecutionError(
                    f"the rasterizer failed to open an otherwise-accepted document and "
                    f"reported load error code {code!r}, which is not one of the reasons "
                    f"that would make this a verdict about the document"
                ) from exc

    def _page_count(self, document: pdfium.PdfDocument) -> int:
        with _PDFIUM_LOCK:
            return len(document)

    def _close(self, document: pdfium.PdfDocument) -> None:
        """Release the document, under the lock, whatever else went wrong.

        Closing is a native call, so it belongs inside the boundary exactly as
        opening does. Doing it explicitly — rather than letting the helper's
        finalizer run whenever the collector next fires — is what keeps
        destruction on this thread and inside this lock.
        """
        with _PDFIUM_LOCK:
            document.close()

    def _render(self, document: pdfium.PdfDocument, number: int, *, deadline: float) -> bytes:
        """Rasterize one page and return it as PNG bytes.

        ``deadline`` is the same absolute monotonic instant the caller measured the
        document budget against, and it is rechecked **immediately after the
        process-wide lock is acquired** — before the page is opened, before a
        bitmap is allocated, and before anything is encoded. The caller's check
        happens before this method is entered, and between those two moments this
        thread can block for an unbounded time waiting for another capture to
        finish inside PDFium. Without the second check a document whose budget
        expired entirely in that queue would still rasterize a page and then be
        refused on the far side of the work, which is the opposite of a budget.

        This is a *narrow* check and deliberately not preemption: a native render
        that has already begun is not interrupted, and nothing here is a queue, a
        worker lease, or an HTTP deadline.

        **The buffer lifetime, exactly.** ``PdfBitmap.to_pil()`` builds the image
        with ``Image.frombuffer`` over the bitmap's own memory, so for the formats
        this adapter renders the two share pixels rather than copying them. The
        sequence below therefore matters and is not incidental::

            page.render(...)          -> bitmap   (native memory allocated)
            bitmap.to_pil()           -> image    (a view over that memory)
            image.save(buffer, "PNG")            reads the shared memory
            buffer.getvalue()         -> bytes   an independent copy
            image.close() / bitmap.close() / page.close()

        Both reads of the shared memory — the encode and the copy out of the
        ``BytesIO`` — complete while the bitmap is still valid, because Python
        evaluates a ``return`` expression *before* unwinding the enclosing
        ``with`` blocks. The value handed back is a plain ``bytes`` object that
        owns nothing native, so nothing downstream can read freed memory: by the
        time this returns, the image, the bitmap and the page are closed and the
        PNG stands alone. The Tesseract subprocess that consumes it runs later,
        outside the lock, against those independent bytes.

        Everything is released in reverse order of acquisition on failure too —
        a conversion failure in ``to_pil``, an encoding failure in ``save``, or a
        raster-size refusal all unwind the same way.

        Page rotation is the renderer's business: the ``/Rotate`` a PDF declares
        is honoured by PDFium, and no additional rotation is applied and no
        orientation is guessed. ``draw_annots=False`` and ``may_draw_forms=False``
        are stated explicitly rather than left to a default, because "we do not
        render annotations or form fields" is a documented boundary and a library
        default is not a promise.

        A ``PdfiumError`` here is a
        :class:`~core.processing.ocr.PdfOcrExecutionError`: the document already
        opened, so a failure to produce pixels for one of its pages is this run
        not delivering rather than a proven verdict about the input, and calling
        it malformed input would durably fail a capture on the strength of a
        guess.
        """
        with _PDFIUM_LOCK:
            # First thing inside the boundary, and outside the PDFium try-block so
            # that no native handle exists yet to release and no ``PdfiumError``
            # translation can swallow it.
            if self._monotonic() >= deadline:
                raise PdfOcrExecutionError(
                    f"the {self._limits.document_budget_seconds:.0f}s recognition budget "
                    f"for this document was exhausted while waiting for the rasterizer "
                    f"lock before page {number} was opened"
                )
            try:
                with closing(document[number - 1]) as page:
                    self._check_raster_size(number, *page.get_size())
                    with (
                        closing(
                            page.render(
                                scale=RENDER_SCALE,
                                rotation=0,
                                draw_annots=False,
                                may_draw_forms=False,
                                fill_color=RENDER_FILL_COLOR,
                            )
                        ) as bitmap,
                        closing(bitmap.to_pil()) as image,
                    ):
                        buffer = io.BytesIO()
                        image.save(buffer, format=PAGE_IMAGE_FORMAT)
                        return buffer.getvalue()
            except pdfium.PdfiumError as exc:
                raise PdfOcrExecutionError(
                    f"rasterizing page {number} of an already-opened document failed"
                ) from exc

    def _check_raster_size(self, number: int, width: float, height: float) -> None:
        """Refuse a page whose raster would be too large, before allocating it.

        The dimensions are computed the way the renderer computes them — the
        page's declared size in canvas units times the scale factor, rounded up —
        so this is a prediction of the allocation rather than a guess at it.
        Refusal, not downscaling: a page silently rendered at a lower resolution
        would produce recognized text of a quality nothing in the result explains.
        """
        pixels = math.ceil(width * RENDER_SCALE) * math.ceil(height * RENDER_SCALE)
        if pixels > self._limits.max_raster_pixels:
            raise ProcessingInputError(
                f"page {number} would rasterize to {pixels} pixels at {RENDER_DPI} DPI, over "
                f"this build's limit of {self._limits.max_raster_pixels}"
            )

    # --- Tesseract, always with the lock released ---------------------------

    def _recognize(self, image: bytes, *, page: int, timeout: float) -> str:
        """Run the engine over one page image and return exactly what it printed.

        The argument list is fixed and built entirely from this package's own
        constants. ``stdin`` and ``stdout`` are Tesseract's own literals for
        "read the image from standard input" and "write the text to standard
        output", so no path is named on either side. ``--dpi`` tells the engine
        the resolution the page was rendered at, which it cannot infer from a PNG
        with no resolution metadata and would otherwise warn about and guess.

        Standard error is captured separately and never mixed into the returned
        text, and it is never returned to a client: Tesseract writes warnings
        about the image and, on some builds, paths from this machine. Standard
        output is decoded as UTF-8 **strictly** and handed back unchanged —
        every byte the engine produced, whitespace and line endings included.

        A timeout kills and reaps the child before the exception leaves this
        method: :func:`subprocess.run` sends ``SIGKILL`` and then waits on the
        process, so no orphan is left holding the pipe.
        """
        arguments = [
            self._executable,
            "stdin",
            "stdout",
            "-l",
            TESSERACT_LANGUAGE_ARGUMENT,
            "--oem",
            str(TESSERACT_OEM),
            "--psm",
            str(TESSERACT_PSM),
            "--dpi",
            str(RENDER_DPI),
        ]
        try:
            completed = subprocess.run(
                arguments,
                input=image,
                capture_output=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfOcrExecutionError(
                f"the OCR engine did not finish page {page} within {timeout:.1f}s and was killed"
            ) from exc
        except OSError as exc:
            raise PdfOcrExecutionError(
                f"the OCR engine {self._executable!r} could not be run while recognizing "
                f"page {page} ({exc.strerror or exc})"
            ) from exc

        if completed.returncode != 0:
            # Not treated as a verdict about the page. A nonzero exit can mean a
            # corrupt image, a missing data file, a build that crashed, or an
            # out-of-memory kill, and this adapter cannot tell those apart — so
            # it reports that recognition did not happen rather than claiming the
            # page is unreadable. The child's stderr is deliberately *not* copied
            # into this message: it is text from another program, it can name
            # paths on this machine, and the status code plus the page number are
            # what a reader of this error can act on.
            raise PdfOcrExecutionError(
                f"the OCR engine exited with status {completed.returncode} on page {page}"
            )
        try:
            return completed.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PdfOcrExecutionError(
                f"the OCR engine's output for page {page} was not valid UTF-8"
            ) from exc
