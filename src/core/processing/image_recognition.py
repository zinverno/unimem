"""The still-image recognition port.

One narrow boundary, for one job: hand a staged PNG or JPEG to something that
can read words out of its pixels, and get back what it read. It is a **sibling**
of :mod:`core.processing.ocr` and deliberately not a generalization of it — see
`ADR-020 <../../docs/ADR/ADR-020-opt-in-local-image-ocr.md>`_. The PDF port
accepts a document and answers about *pages*: it takes an ``embedded_pages``
exclusion, reports a ``page_count``, names a rasterizer, and is guarded by five
structural rules whose load-bearing one is that a *missing* page must not look
like a blank one. One image has none of that. There is one subject, nothing to
exclude, nothing to count, and nothing that rasterizes.

**Why this exists at all.** ``core`` may not import an imaging library, a
decoder, ``subprocess``, or an OCR engine, and the canonical policy that decides
what a recognition result *means* must stay testable with none of that
installed. So the policy lives in :mod:`core.processing.image_ocr`, the
machinery lives in an adapter outside ``core``, and this module is the typed seam
between them.

**What crosses the seam is values, not objects.** The input is a binary stream —
never a caller-controlled path, because a path is a filesystem instruction and
this port must not be able to express one. Alongside it travel the declared MIME
type and the encoded dimensions :mod:`core.processing.image` already validated,
so an implementation can enforce a pre-decode budget without parsing the header a
second time and becoming a second source of truth about the same bytes. What
comes back is a small frozen dataclass of plain strings. No bitmap, no image
object, no file handle, no native handle, and no temporary path ever reaches
canonical processing.

**Two failures, and they mean opposite things.** :class:`ImageOcrLimitExceeded`
says this deployment declined to spend the work; the capture still succeeds, with
the skip recorded. :class:`ImageOcrExecutionError` says no trusted result came
back; the capture is left non-terminal and nothing is persisted. They are
siblings rather than parent and child precisely so that no ``except`` clause and
no exception-handler registration can adopt one into the other's behaviour.

**Neither is a verdict about the image**, and this port cannot express one.
:class:`~core.processing.errors.ProcessingInputError` is deliberately absent from
its vocabulary: the PDF port may raise one because for a document "cannot open"
genuinely is a statement about the submitted bytes, whereas ADR-019 fixed that an
image which ingests successfully today must never become a failure when OCR is
enabled — and the header was validated before the adapter was handed anything.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO, Final, Literal, Protocol

from pydantic import JsonValue

#: The encoded pixel count exceeded this deployment's image OCR budget.
ENCODED_PIXEL_LIMIT: Final = "encoded_pixel_limit"

#: The encoded byte count exceeded what the adapter will hold for one invocation.
ENCODED_BYTE_LIMIT: Final = "encoded_byte_limit"

#: Every reason a recognition may be refused before it begins.
LimitReason = Literal["encoded_pixel_limit", "encoded_byte_limit"]


class ImageOcrExecutionError(Exception):
    """The OCR execution did not produce a trusted recognition result.

    That sentence is the whole meaning, and the things it deliberately does
    **not** say are as load-bearing as the things it does.

    It does **not** claim the failure is a transient infrastructure outage. It
    covers an executable that vanished after startup, a timeout, a crash or an
    out-of-memory kill, a nonzero exit, output that is not valid UTF-8, an
    adapter answering inconsistently with its own contract — **and** encoded
    image data beyond the structural boundary
    :mod:`core.processing.image` validates that the engine could not decode.
    Phase 4A reads a PNG signature and one complete ``IHDR``, or a JPEG ``SOI``
    and the first fully present frame header, and stops; it never enters an
    ``IDAT`` or an entropy-coded scan, so it does not prove the pixel stream
    behind that header is decodable at all. The last case may therefore be
    perfectly deterministic for the same bytes, and a retry would fail
    identically.

    No implementation of this port may distinguish those by parsing an engine's
    standard error. That is another program's prose, it is not a stable
    interface, and it can name paths on the machine — the same reason
    :mod:`unimem_ocr.tesseract` never parses a ``PdfiumError`` message.

    It does **not** claim anything about the image either. Deliberately **not** a
    :class:`~core.processing.errors.ProcessingError`, so it flows through
    :class:`~core.processing.service.ProcessingOrchestrator` untouched: the
    capture stays ``processing``, no content object is built or persisted, and
    the delivery layer answers a fixed 503. Marking the capture ``failed`` would
    record a judgement about a picture that the system never made, and recording
    a successful zero-segment object would claim a recognizer looked at pixels it
    never successfully opened.

    Its message is written for a server log rather than for a client. Nothing
    here is passed to the wire.
    """


class ImageOcrLimitExceeded(Exception):  # noqa: N818 - see the naming note below
    """Recognition was refused before it began, because of a deployment budget.

    This is a **control signal**, not a failure. The image is valid, it is
    already canonical content, and the only thing that did not happen is optional
    enrichment. :class:`~core.processing.image_ocr.ImageOcrProcessor` catches it
    and returns a successful capture with the skip recorded durably.

    **Its meaning is machine-readable.** :attr:`reason` and :attr:`limit` are
    attributes precisely because a processor branches on them, and control flow
    that depends on a message breaks the first time the message is reworded.
    Nothing may branch on ``str(exc)``.

    :attr:`reason` is a plain ``Literal`` and deliberately not a member of any
    canonical contract enum: this is a private arrangement between an adapter and
    a processor, it never appears on a contract field, and minting a durable
    wire-format value for it would be a commitment nothing asked for.

    Outside :class:`~core.processing.errors.ProcessingError` so the orchestrator
    never sees a verdict, and outside :class:`ImageOcrExecutionError` so neither
    can adopt the other's lifecycle behaviour by inheritance. They carry opposite
    control-flow meaning: one ends in a ``201``, the other in a ``503``.

    **On the name.** It does not end in ``Error``, which the linter's general rule
    prefers, and that is the point rather than an oversight: this is not an error.
    Nothing went wrong, the capture succeeds, and reading it as a failure is
    exactly the mistake the naming avoids. ADR-020 fixes the name, so the rule is
    suppressed here with a reason rather than the decision being renamed to suit
    it.
    """

    def __init__(self, message: str, *, reason: LimitReason, limit: int) -> None:
        super().__init__(message)
        #: Which bound was crossed.
        self.reason: LimitReason = reason
        #: The bound that was in force, so the caller records the number that
        #: actually applied rather than re-deriving it from a policy it does not
        #: own.
        self.limit = limit


@dataclass(frozen=True, slots=True)
class ImageOcrResult:
    """Everything one recognition of one image reports back.

    ``text`` is exactly what the engine returned, including an **empty string**
    when recognition succeeded and read nothing. That case is a result rather
    than an omission, and it is not a finding about the image: a faint
    photograph, an unsupported script, a rotated sign, a picture with no words in
    it, and a blank frame all land here and no implementation can tell them
    apart. Nothing is trimmed, normalized, or repaired — see
    :mod:`core.processing.image_ocr` for why.

    ``engine`` and ``engine_version`` are what actually ran, reported by the
    installed software rather than compiled in here, because they travel onto the
    canonical content object and a constant written in ``core`` would eventually
    be a lie. ``settings`` is the *effective* recognition configuration for this
    run. Both are recorded and never interpreted: nothing in ``core`` branches on
    an engine name.

    There is deliberately no ``rasterizer`` pair. Nothing rasterizes on this
    path, and carrying one across because the PDF result has one would be a
    fabricated fact wearing the shape of an observation.
    """

    text: str
    engine: str
    engine_version: str
    settings: Mapping[str, JsonValue]


class ImageOcr(Protocol):
    """Recognizes text in one still image.

    Synchronous, like every other port in ``core``, and for the same reason: the
    one thing calling it is a synchronous processor, and an awaitable surface for
    implementations that do not exist yet would buy nothing.
    """

    def recognize_image(
        self,
        stream: BinaryIO,
        *,
        mime_type: str,
        encoded_width: int,
        encoded_height: int,
    ) -> ImageOcrResult:
        """Recognize the image the stream carries, or refuse before reading it.

        ``stream`` is an open, rewound binary stream positioned at the start of
        the immutable original. The implementation reads it forward and closes
        nothing it did not open; the caller owns the handle. Nothing here
        requires the stream to be seekable.

        ``mime_type`` is the type the submitter declared, which the caller has
        already verified the leading bytes against. ``encoded_width`` and
        ``encoded_height`` are the dimensions the caller read out of the
        validated header. They arrive as parameters so that an implementation can
        enforce a pixel budget *before* the first byte is read, which is what
        ADR-019 requires of any path that decodes.

        Raises :class:`ImageOcrLimitExceeded` when a documented resource bound is
        crossed. The refusal must happen before any decode and before any
        recognition begins, and for a pixel-bound refusal before the stream is
        read at all.

        Raises :class:`ImageOcrExecutionError` when no trusted recognition result
        came back, for any of the reasons that error documents.

        It must raise rather than return an empty string on any failure. An empty
        ``text`` produced by a defect is indistinguishable from an empty ``text``
        produced by a wordless photograph, and ``core`` has no way to tell them
        apart — which is why that discipline lives here rather than in
        :func:`validate_image_ocr_result`.

        It must **not** raise :class:`~core.processing.errors.ProcessingInputError`.
        A verdict about the image is not this port's to reach.
        """
        ...


def _json_compatible(value: JsonValue) -> bool:
    """Whether a settings value survives a round trip through JSON.

    ``ContentObject.metadata`` is declared over ``JsonValue``, so a value that
    does not is rejected by the contract at the far end of this function anyway —
    just with a message about a canonical object rather than about the adapter
    that produced it. Checking here names the real culprit.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return True
    if isinstance(value, list):
        return all(_json_compatible(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_compatible(item) for key, item in value.items())
    return False


def validate_image_ocr_result(result: ImageOcrResult) -> None:
    """Check that a result is something canonical content can be built from, or raise.

    The check is short, and its shortness is a finding rather than an oversight.
    :func:`~core.processing.ocr.validate_ocr_result` is long because a document
    has a *structure* an adapter can contradict; one image has none, so there is
    no page to be missing, duplicated, or out of range. What is left is the
    handful of things that would otherwise become a permanent lie on a stored
    object:

    * **``text`` really is a string.** An adapter that returned ``None`` or bytes
      would fail much later, inside a contract validator, in a message about a
      segment rather than about the engine.
    * **``engine`` and ``engine_version`` are nonblank.** Both land in durable
      metadata, where ``JsonMapping`` imposes no non-blankness of its own, so an
      empty string here would become a content object claiming it was produced by
      an engine with no name.
    * **``settings`` is a JSON-compatible mapping with string keys.** It is
      recorded verbatim under ``ContentObject.metadata``, which must round-trip
      through JSON unchanged.

    Nothing is trimmed, coerced, or rewritten. ``strip()`` appears only to decide
    whether a name is blank, exactly as it appears in the processor only to
    decide whether recognized text is blank.

    Raises :class:`ImageOcrExecutionError`, never
    :class:`~core.processing.errors.ProcessingInputError`: an implementation that
    answers with something malformed has told us nothing about the image, so the
    image must not be blamed for it — and specifically must not be recorded as
    having been looked at and found wordless.
    """
    if not isinstance(result.text, str):
        raise ImageOcrExecutionError(
            f"the recognizer returned {type(result.text).__name__} where recognized text "
            f"was expected"
        )
    if not isinstance(result.engine, str) or not result.engine.strip():
        raise ImageOcrExecutionError("the recognizer reported no engine name")
    if not isinstance(result.engine_version, str) or not result.engine_version.strip():
        raise ImageOcrExecutionError("the recognizer reported no engine version")
    if not isinstance(result.settings, Mapping):
        raise ImageOcrExecutionError(
            f"the recognizer reported {type(result.settings).__name__} where a settings "
            f"mapping was expected"
        )
    for key, value in result.settings.items():
        if not isinstance(key, str):
            raise ImageOcrExecutionError(
                f"the recognizer reported a settings key of type {type(key).__name__}, which "
                f"cannot be recorded on a canonical content object"
            )
        if not _json_compatible(value):
            raise ImageOcrExecutionError(
                f"the recognizer reported a settings value for {key!r} that cannot be "
                f"recorded on a canonical content object"
            )
