"""Translating typed core failures into HTTP, at the delivery boundary only.

Core raises the errors it has always raised. Nothing here catches one and
re-raises it as another, collapses two hierarchies into one, widens a base
class, or teaches ``core`` what a status code is. This module is a lookup table
plus the handlers that consult it, and it is the only place in the system where
a domain failure acquires a number.

Two rules shape the table, and they are not the same rule:

* **A 4xx describes the request.** The client sent a capture id that is taken, a
  payload type this build cannot materialize, bytes that are not UTF-8. Core
  already says which, in a message built from what the client itself supplied,
  so that message is passed through: it is the difference between a client that
  can fix its submission and one that has to guess.
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
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.intake import InvalidCaptureEnvelopeError, UnsupportedCapturePayloadError
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
    InvalidCaptureProcessingStateError,
    NoProcessorError,
    ProcessingInputError,
    ProcessingOutputError,
    ProcessorRoutingError,
    TextDecodingError,
)
from core.storage import RawObjectStoreError
from unimem_api.models import ErrorBody, ErrorResponse


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
    (ProcessingInputError, _PROCESSING_FAILED),
    (TextDecodingError, _PROCESSING_FAILED),
    (ProcessingOutputError, _PROCESSING_FAILED),
    (ProcessorRoutingError, _ROUTING_MISCONFIGURED),
    (NoProcessorError, _ROUTING_MISCONFIGURED),
    (AmbiguousProcessorError, _ROUTING_MISCONFIGURED),
    (CaptureRecordCorruptError, _DATA_INTEGRITY),
    (ContentObjectCorruptError, _DATA_INTEGRITY),
    (CaptureRecordPersistenceError, _STORAGE_UNAVAILABLE),
    (ContentObjectPersistenceError, _STORAGE_UNAVAILABLE),
    (RawObjectStoreError, _STORAGE_UNAVAILABLE),
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
