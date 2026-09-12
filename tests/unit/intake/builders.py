"""Builders for the envelopes intake is handed."""

from datetime import UTC, datetime
from typing import Any

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CaptureIntent,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    IntentAction,
)

#: When the *user* captured. Deliberately far from the intake clock below, so a
#: test can tell which one a timestamp came from.
CAPTURED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

#: The instants the fake intake clock hands out, in order.
RECEIVED_AT = datetime(2026, 8, 9, 10, 11, 12, 130000, tzinfo=UTC)
UPDATED_AT = datetime(2026, 8, 9, 10, 11, 13, tzinfo=UTC)

TEXT = "The canonical object is not Markdown."

#: A small but complete HTML document. Deliberately carries the things intake
#: must *not* touch: a doctype, indentation, entities, a ``<title>``, a script,
#: and a trailing newline.
HTML = (
    "<!doctype html>\n"
    "<html>\n"
    "  <head><title>A &amp; B</title></head>\n"
    "  <body>\n"
    "    <p>Hello &mdash; world.</p>\n"
    "    <script>var x = 1 < 2;</script>\n"
    "  </body>\n"
    "</html>\n"
)

#: Text with characters, line endings, and edge whitespace that no well-meaning
#: normalizer must be allowed to tidy up.
AWKWARD_TEXT = (
    "A\u030a vs \u00c5 \u2014 \u4f60\u597d \U0001f30d\r\n\tindented\n\ntrailing   \n\u00a0\ufeffend"
)


def make_payload(**overrides: Any) -> CapturePayload:
    fields: dict[str, Any] = {
        "type": CapturePayloadType.TEXT,
        "mime_type": "text/plain",
        "text": TEXT,
        "title": "A note",
    }
    return CapturePayload(**(fields | overrides))


def make_envelope(**overrides: Any) -> CaptureEnvelope:
    """A valid inline-text envelope, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "cap_intake_01",
        "source": CaptureSource(
            type=CaptureSourceType.API,
            provider="cli",
            url="https://example.com/notes",
        ),
        "payload": make_payload(),
        "context": CaptureContext(
            captured_at=CAPTURED_AT,
            device="laptop",
            application="terminal",
        ),
        "intent": CaptureIntent(
            action=IntentAction.SAVE,
            collection="reading",
            tags=["architecture"],
        ),
    }
    return CaptureEnvelope(**(fields | overrides))


def make_webpage_payload(**overrides: Any) -> CapturePayload:
    """The one webpage shape this build ingests: HTML and nothing else."""
    fields: dict[str, Any] = {
        "type": CapturePayloadType.WEBPAGE,
        "mime_type": "text/html",
        "html": HTML,
        "title": "A page",
    }
    return CapturePayload(**(fields | overrides))


def make_webpage_envelope(**overrides: Any) -> CaptureEnvelope:
    """A valid HTML-backed webpage envelope, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "cap_intake_web_01",
        "payload": make_webpage_payload(),
    }
    return make_envelope(**(fields | overrides))


#: A staged raw object reference of the form intake resolves. The digest is a
#: literal rather than a hash of anything, because what matters at the intake
#: boundary is only that the *shape* is right; whether the bytes exist is the
#: raw store's answer and the tests stage them there explicitly.
STAGED_DIGEST = "a" * 64
STAGED_FILE_REF = f"sha256:{STAGED_DIGEST}"

#: The MIME type the one supported document shape must declare.
PDF_MIME = "application/pdf"

#: The other document MIME type this build ingests, from Phase 3 PR 2.
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: The legacy binary Word format. A different format needing a different reader,
#: and one this build refuses rather than guesses at.
DOC_MIME = "application/msword"


def make_document_payload(**overrides: Any) -> CapturePayload:
    """The one document shape this build ingests: a staged PDF file_ref."""
    fields: dict[str, Any] = {
        "type": CapturePayloadType.DOCUMENT,
        "mime_type": PDF_MIME,
        "file_ref": STAGED_FILE_REF,
        "title": "A paper",
    }
    return CapturePayload(**(fields | overrides))


def make_document_envelope(**overrides: Any) -> CaptureEnvelope:
    """A valid staged-PDF document envelope, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "cap_intake_doc_01",
        "payload": make_document_payload(),
    }
    return make_envelope(**(fields | overrides))


def make_unsupported_envelope(
    payload_type: CapturePayloadType, **overrides: Any
) -> CaptureEnvelope:
    """A valid envelope of a payload type this build cannot materialize at all.

    ``WEBPAGE`` is deliberately absent: since Phase 2 PR 1 it is a supported
    type, and *which shapes of it* are supported is a separate question asked
    in ``test_webpage_materialization.py``. ``DOCUMENT`` left for the same
    reason in Phase 3 PR 1, and its shapes are asked about in
    ``test_document_materialization.py``.
    """
    payloads: dict[CapturePayloadType, dict[str, Any]] = {
        CapturePayloadType.IMAGE: {"file_ref": "blob://image"},
        CapturePayloadType.VIDEO: {"file_ref": "blob://video"},
        CapturePayloadType.FILE: {"file_ref": "blob://file"},
        CapturePayloadType.URL: {},
    }
    payload = CapturePayload(type=payload_type, **payloads[payload_type])
    return make_envelope(payload=payload, **overrides)
