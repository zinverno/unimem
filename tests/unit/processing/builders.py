"""Builders for the capture records a processor is handed.

Capture persistence does not exist yet, so tests construct ``CaptureRecord``
directly — which is also the point: a processor takes a capture record and a
store, and knows nothing about where either came from.
"""

from datetime import UTC, datetime
from typing import Any

from core.contracts import (
    CaptureContext,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentSource,
    ContentType,
    OriginalReference,
    Provenance,
    ProvenanceSourceType,
    Segment,
    SegmentType,
)
from tests.unit.processing.doubles import InMemoryRawObjectStore

RECEIVED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

#: When the user captured, as distinct from when intake received it.
CAPTURED_AT = datetime(2026, 3, 4, 5, 0, 0, tzinfo=UTC)


def make_capture(**overrides: Any) -> CaptureRecord:
    """A stored text capture with no raw object, plus optional overrides."""
    fields: dict[str, Any] = {
        "id": "cap_text_01",
        "status": CaptureStatus.STORED,
        "received_at": RECEIVED_AT,
        "source": CaptureSource(
            type=CaptureSourceType.API,
            provider="cli",
            url="https://example.com/notes",
        ),
        "payload_type": CapturePayloadType.TEXT,
        "context": CaptureContext(captured_at=CAPTURED_AT, device="laptop"),
    }
    return CaptureRecord(**(fields | overrides))


def store_and_capture(
    store: InMemoryRawObjectStore,
    data: bytes,
    *,
    mime_type: str | None = "text/plain",
    **overrides: Any,
) -> CaptureRecord:
    """Put bytes in the store and build the capture that points at them."""
    raw_object = store.store_bytes(data, mime_type=mime_type)
    store.accesses.clear()
    return make_capture(raw_object=raw_object, **overrides)


def make_content(capture_id: str = "cap_text_01", **overrides: Any) -> ContentObject:
    """A minimal valid content object attributed to one capture.

    Orchestration never inspects content beyond the capture it is attributed
    to, so this is deliberately the smallest thing the contract accepts.
    """
    fields: dict[str, Any] = {
        "id": "con_01",
        "type": ContentType.TEXT,
        "source": ContentSource(capture_id=capture_id),
        "original": OriginalReference(),
        "segments": [
            Segment(
                id="seg_01",
                type=SegmentType.TEXT,
                text="normalized text",
                provenance=Provenance(
                    capture_id=capture_id,
                    source_type=ProvenanceSourceType.ORIGINAL,
                ),
            )
        ],
    }
    return ContentObject(**(fields | overrides))
