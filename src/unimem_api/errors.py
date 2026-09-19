"""Translating typed core failures into HTTP, at the delivery boundary only.

Core raises the errors it has always raised. Nothing here catches one and
re-raises it as another, collapses two hierarchies into one, widens a base
class, or teaches ``core`` what a status code is. This module is a lookup table
plus the handlers that consult it, and it is the only place in the system where
a domain failure acquires a number.

Two rules shape the table, and they are not the same rule:

* **A 4xx describes the request.** The client sent a capture id that is taken, a
  payload type this build cannot materialize, bytes that are not UTF-8, a
  ``file_ref`` naming material nobody staged. Core already says which, in a
  message built from what the client itself supplied, so that message is passed
  through: it is the difference between a client that can fix its submission and
  one that has to guess. Core's 4xx messages name *fields and capabilities*, not
  submitted values — a rejected ``file_ref`` is never repeated back — so passing
  them through echoes nothing a client sent.
* **A 5xx describes the server.** It is the server's business how it failed, and
  core's messages for those failures name a SQLite file, a staging directory, or
  the internal shape of a stored snapshot. Every 5xx therefore carries a fixed
  public message written here, and the underlying error's own text never reaches
  the wire.

What is deliberately *not* mapped matters as much as what is. A bare
``ProcessingError``, an ``Exception`` from a bug, anything a future processor
invents — none of them appear below, so none is quietly given a friendly status
code. They fall through to the ASGI server's ordinary 500, which is what an
unhandled bug should look like. ``BaseException`` is never caught.

Three rows are not ``ProcessingError``s and must never be mistaken for ones.
:class:`~core.processing.ocr.PdfOcrExecutionError` says a recognition *run*
failed — the engine was missing, crashed, timed out, or answered inconsistently —
so nothing is known about the document and the capture is deliberately left
non-terminal by the orchestrator.
:class:`~core.processing.image_recognition.ImageOcrExecutionError` says the same
about an image, and says slightly less: it means only that the run produced no
result this build can trust, and it deliberately does **not** claim the failure
is transient. Phase 4A validates a header and stops, so it never proves the
encoded pixel stream behind that header is decodable — an engine may therefore
have failed on this machine, or on these bytes, and nothing available can tell
those apart without parsing another program's prose.

Both map to a 503 with a fixed public message, like every other "the server could
not do this right now", and specifically not to the 422 a ``ProcessingInputError``
gets: a 422 would tell a client their document or their picture is the problem,
which is exactly the claim neither failure can support. Both sit outside the
``ProcessingError`` hierarchy in ``core`` so that no base class can adopt either
into the 422 row by accident, and they are separate types with separate rows
because the public sentence a client reads should name what was actually being
processed.

Phase 5A-2 adds the third.
:class:`~core.processing.media_probe.MediaProbeExecutionError` says a structural
*probe* produced **no trusted structural result** for audio or video, so UniMem
cannot reach a verdict about the submitted media. It is the same shape of claim
as the other two and the same 503, and it is careful about what it does not
assert: not that the bytes went unexamined — the failure may arrive before any
byte is read, after some, or after all of them — not that the cause is transient,
and not that a retry of the identical bytes would succeed.

The distinction it carries is the sharpest of the set. A container that
contradicts its declared type, or one lacking the stream its type requires, is a
*trusted* structural observation and therefore a deterministic verdict about the
submitted bytes: it arrives as a ``ProcessingInputError``, a 422, with the
capture durably ``failed``. Only the absence of a trustworthy result is this row.
Collapsing the two would either record a verdict this build has no evidence for,
or hide a real refusal behind an unavailability answer.

One core error is deliberately **absent** from this table and must stay absent.
:class:`~core.processing.image_recognition.ImageOcrLimitExceeded` never reaches
delivery: it is a control signal that
:class:`~core.processing.image_ocr.ImageOcrProcessor` consumes, and the capture it
belongs to succeeds with a ``201``. Giving it a status code here would be
answering a request that was never failed.

One row is not core's. :class:`~unimem_api.replay.CaptureReplayIntegrityError`
is raised by this package, when a capture that says ``COMPLETE`` turns out to
have no canonical content — a broken server invariant rather than a failed
store. It is declared where it is raised instead of being smuggled in as a core
persistence error for the sake of a convenient status code, and it lands on the
same fixed ``data_integrity_error`` message every other unreadable-stored-data
failure does, because it means the same thing to a client.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.intake import (
    CaptureMaterialUnavailableError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordCorruptError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    ContentObjectAlreadyExistsError,
    ContentObjectCorruptError,
    ContentObjectNotFoundError,
    ContentObjectPersistenceError,
)
from core.processing import (
    AmbiguousProcessorError,
    ImageOcrExecutionError,
    InvalidCaptureProcessingStateError,
    MediaProbeExecutionError,
    NoProcessorError,
    PdfOcrExecutionError,
    ProcessingInputError,
    ProcessingOutputError,
    ProcessorRoutingError,
    TextDecodingError,
)
from core.storage import RawObjectStoreError
from unimem_api.models import ErrorBody, ErrorResponse
from unimem_api.replay import CaptureReplayIntegrityError


@dataclass(frozen=True)
class HttpError:
    """One row of the translation table.

    ``message`` is the fixed public text for a 5xx, and ``None`` for a 4xx —
    where the core error's own message is passed through instead.
    """

    status_code: int
    code: str
    message: str | None = None


# --- The 4xx rows. No public message: core's own is passed through. ----------
_CAPTURE_ALREADY_EXISTS: Final = HttpError(409, "capture_already_exists")
_CONTENT_CONFLICT: Final = HttpError(409, "content_conflict")
_INVALID_CAPTURE_STATE: Final = HttpError(409, "invalid_capture_state")
_NOT_FOUND: Final = HttpError(404, "not_found")
_UNSUPPORTED_PAYLOAD: Final = HttpError(422, "unsupported_payload")
_INVALID_ENVELOPE: Final = HttpError(422, "invalid_capture_envelope")
_PROCESSING_FAILED: Final = HttpError(422, "processing_failed")
#: A ``file_ref`` this build understands, naming bytes that are not staged. It is
#: 4xx because it describes the request — the fix is to stage the material and
#: resubmit — and it is deliberately *not* the 503 a raw-store failure gets. The
#: store is fine and answered correctly; telling a client to retry later would
#: send them into a loop that cannot terminate.
_MATERIAL_UNAVAILABLE: Final = HttpError(422, "capture_material_unavailable")

# --- The 5xx rows. A fixed public message; core's text never leaves. ---------
# Each says what the client can act on — retry later, or stop and call someone —
# and nothing about how the server is put together. No path, file name, table,
# column, SQLite error name, or Python type appears in any of them.
_ROUTING_MISCONFIGURED: Final = HttpError(
    500,
    "processing_configuration_error",
    "the server is not configured to process this capture; "
    "the capture is stored and no content was produced",
)
_DATA_INTEGRITY: Final = HttpError(
    500,
    "data_integrity_error",
    "stored data for this capture could not be read back",
)
_STORAGE_UNAVAILABLE: Final = HttpError(
    503,
    "storage_unavailable",
    "the capture store is currently unavailable",
)
#: A recognition run that could not be carried out. 503 rather than 422 because
#: it says nothing about the submitted document: the engine is missing, it failed,
#: it timed out, or it answered with something inconsistent, and retrying later
#: against a repaired deployment is a sensible thing for a client to do. The
#: public text says the capability is unavailable and names nothing about this
#: machine — no engine version, no exit status, no language path, no subprocess
#: stderr, and no local file name, all of which the underlying error's own message
#: may carry for a server log.
_OCR_UNAVAILABLE: Final = HttpError(
    503,
    "ocr_unavailable",
    "text recognition for this document could not be completed; "
    "the capture is stored and no content was produced",
)
#: The image counterpart, and a separate row rather than a shared one. Its public
#: text names an image because that is what the client submitted, and a message
#: about "this document" would be wrong in a way a client cannot correct for.
#:
#: The same 503-not-422 reasoning applies with one extra turn of the screw: this
#: failure may well be deterministic for these exact bytes, because the engine may
#: have been unable to decode encoded data that Phase 4A's header check never
#: looked at. A retry against a repaired deployment *may* succeed and is not
#: guaranteed to — which is still a better answer than a 422 claiming the picture
#: is at fault, since this build genuinely cannot tell. The text says nothing
#: about this machine: no engine version, no exit status, no executable name, no
#: language path, no subprocess stderr, and no local file name, all of which the
#: underlying error's own message may carry for a server log.
_IMAGE_OCR_UNAVAILABLE: Final = HttpError(
    503,
    "image_ocr_unavailable",
    "text recognition for this image could not be completed; "
    "the capture is stored and no content was produced",
)

#: The media counterpart, and a third row rather than a shared one, for the same
#: reason the image row is separate from the document one: its public text names
#: media because that is what the client submitted.
#:
#: The 503-not-422 reasoning is at its clearest here. This build cannot probe a
#: container without an engine, so a failure may mean the engine is missing, or
#: crashed, or timed out, or answered inconsistently with its own contract. What
#: those share is not that the bytes went unexamined — some of them happen after
#: the file has been read in full — but that **no structural result this build
#: can trust came back**, leaving no basis for a verdict about the media.
#: Answering 422 would state a conclusion the server does not have.
#:
#: It is deliberately *not* the answer for a container that contradicts its
#: declaration or lacks the stream its type requires. Those are deterministic
#: verdicts about the bytes, they arrive as ``ProcessingInputError``, and they
#: are a 422 with the capture durably ``failed``. Keeping the two rows apart is
#: what keeps "no result this build can trust" distinguishable from "a trusted
#: result, and it does not meet the policy".
#:
#: The text names nothing about this machine: no engine, no version, no exit
#: status, no container alias the probe observed, no stream detail, no subprocess
#: output, no temporary file and no local path — all of which the underlying
#: error's own message may carry for a server log.
_MEDIA_PROBE_UNAVAILABLE: Final = HttpError(
    503,
    "media_probe_unavailable",
    "media structure probing could not be completed; "
    "the capture is stored and no content was produced",
)

#: Which core error becomes which HTTP response.
#:
#: Registration is by concrete type, and Starlette resolves a raised exception by
#: walking its ``__mro__`` for the nearest registered handler — so a type listed
#: here also covers its subclasses, and a type that is *not* listed is not
#: silently adopted by a base class that is. The two base classes that do appear
#: are here on purpose:
#:
#: * ``ProcessorRoutingError`` — ``NoProcessorError`` and
#:   ``AmbiguousProcessorError`` are the routing failures worth naming, and both
#:   mean the same thing to a client (this deployment is wired wrong). The base
#:   carries the row so neither can be forgotten, and both are listed under it
#:   anyway so the table reads as the contract rather than as an inheritance
#:   puzzle.
#: * ``RawObjectStoreError`` — a missing object, an unparseable reference, and a
#:   failed write all say the raw store cannot serve this capture right now.
#:
#: Order is documentation only; lookup is by type, never by position.
ERROR_MAPPINGS: Final[tuple[tuple[type[Exception], HttpError], ...]] = (
    (CaptureRecordAlreadyExistsError, _CAPTURE_ALREADY_EXISTS),
    (ContentObjectAlreadyExistsError, _CONTENT_CONFLICT),
    (InvalidCaptureProcessingStateError, _INVALID_CAPTURE_STATE),
    (CaptureRecordNotFoundError, _NOT_FOUND),
    (ContentObjectNotFoundError, _NOT_FOUND),
    (UnsupportedCapturePayloadError, _UNSUPPORTED_PAYLOAD),
    (InvalidCaptureEnvelopeError, _INVALID_ENVELOPE),
    (CaptureMaterialUnavailableError, _MATERIAL_UNAVAILABLE),
    (ProcessingInputError, _PROCESSING_FAILED),
    (TextDecodingError, _PROCESSING_FAILED),
    (ProcessingOutputError, _PROCESSING_FAILED),
    (ProcessorRoutingError, _ROUTING_MISCONFIGURED),
    (NoProcessorError, _ROUTING_MISCONFIGURED),
    (AmbiguousProcessorError, _ROUTING_MISCONFIGURED),
    (CaptureRecordCorruptError, _DATA_INTEGRITY),
    (ContentObjectCorruptError, _DATA_INTEGRITY),
    (CaptureReplayIntegrityError, _DATA_INTEGRITY),
    (CaptureRecordPersistenceError, _STORAGE_UNAVAILABLE),
    (ContentObjectPersistenceError, _STORAGE_UNAVAILABLE),
    (RawObjectStoreError, _STORAGE_UNAVAILABLE),
    (PdfOcrExecutionError, _OCR_UNAVAILABLE),
    (ImageOcrExecutionError, _IMAGE_OCR_UNAVAILABLE),
    (MediaProbeExecutionError, _MEDIA_PROBE_UNAVAILABLE),
)

#: The code for a body FastAPI/Pydantic rejected before any core code ran.
INVALID_REQUEST_CODE: Final = "invalid_request"


def error_response(mapping: HttpError, exc: Exception) -> JSONResponse:
    """Build the wire body for one mapped failure.

    A 5xx uses the table's fixed message. A 4xx uses ``str(exc)``, which core
    built from what the client itself submitted.
    """
    message = mapping.message if mapping.message is not None else str(exc)
    return _envelope(mapping.status_code, mapping.code, message)


def describe_validation_error(exc: RequestValidationError) -> str:
    """Summarize a rejected body by *where* it was wrong, not by what was in it.

    Only Pydantic's ``loc`` and ``msg`` are used. The submitted values are
    deliberately left out: echoing a rejected body back is how a payload a client
    did not mean to send ends up in someone's log a second time.
    """
    parts = [
        f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}" for error in exc.errors()
    ]
    detail = f" ({'; '.join(parts)})" if parts else ""
    return f"request body failed validation{detail}"


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _handler_for(mapping: HttpError) -> Callable[[Request, Exception], Response]:
    """Bind one table row into a Starlette exception handler."""

    def handle(request: Request, exc: Exception) -> Response:
        return error_response(mapping, exc)

    return handle


def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Wrap FastAPI's own body rejection in this API's error envelope.

    The status stays 422 — FastAPI's default, and a perfectly good answer for a
    malformed body — but the shape becomes the one every other failure uses, so a
    client parses one envelope rather than two. This runs *before* any route
    code, so no core service has been called and nothing is durable.
    """
    return _envelope(422, INVALID_REQUEST_CODE, describe_validation_error(exc))


#: Starlette types every handler as taking a bare ``Exception``, but it only ever
#: dispatches an exception matching the registered type. The cast records that
#: the wider signature is Starlette's, not a claim this module can make good on.
_ExceptionHandler = Callable[[Request, Exception], Response]


def install_error_handlers(app: FastAPI) -> None:
    """Register the translation table on an app. The only wiring this module does."""
    app.add_exception_handler(
        RequestValidationError,
        cast(_ExceptionHandler, _handle_request_validation_error),
    )
    for exception_type, mapping in ERROR_MAPPINGS:
        app.add_exception_handler(exception_type, _handler_for(mapping))
