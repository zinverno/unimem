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


def make_unsupported_envelope(
    payload_type: CapturePayloadType, **overrides: Any
) -> CaptureEnvelope:
    """A valid envelope of a payload type this phase does not accept."""
    payloads: dict[CapturePayloadType, dict[str, Any]] = {
        CapturePayloadType.WEBPAGE: {"html": "<p>hi</p>"},
        CapturePayloadType.IMAGE: {"file_ref": "blob://image"},
        CapturePayloadType.DOCUMENT: {"file_ref": "blob://document"},
        CapturePayloadType.VIDEO: {"file_ref": "blob://video"},
        CapturePayloadType.FILE: {"file_ref": "blob://file"},
        CapturePayloadType.URL: {},
    }
    payload = CapturePayload(type=payload_type, **payloads[payload_type])
    return make_envelope(payload=payload, **overrides)
