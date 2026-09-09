"""Builders for the capture records the store is handed.

The default record is deliberately *full*: every field a snapshot must carry
through a database round trip — an aware timestamp, both timestamps, enums, a
nested source, a raw object reference, an error string — is populated, so a
round-trip test asserts fidelity rather than the survival of three fields.
"""

from datetime import UTC, datetime
from typing import Any

from core.contracts import (
    Asset,
    AssetRole,
    CaptureContext,
    CaptureIntent,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentSource,
    ContentType,
    DerivedContent,
    IntentAction,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
    RawObjectRef,
    Segment,
    SegmentType,
)

CAPTURED_AT = datetime(2026, 7, 8, 9, 0, 0, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 7, 8, 9, 10, 11, 120000, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 8, 9, 12, 13, tzinfo=UTC)

#: A well-formed digest. The store never computes one; it is data like any other.
RAW_SHA256 = "d" * 64

#: Text no ASCII escape survives readably, in a field that reaches the database.
UNICODE_ERROR = "не удалось — 失败 🚫 <>&'\"\\ \n\ttab"


def make_raw_object(**overrides: Any) -> RawObjectRef:
    """A reference to a stored raw original."""
    fields: dict[str, Any] = {
        "id": RAW_SHA256,
        "mime_type": "text/plain",
        "sha256": RAW_SHA256,
        "ref": f"sha256:{RAW_SHA256}",
    }
    return RawObjectRef(**(fields | overrides))


def make_record(**overrides: Any) -> CaptureRecord:
    """A fully populated capture record, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "cap_persist_01",
        "status": CaptureStatus.STORED,
        "received_at": RECEIVED_AT,
        "updated_at": UPDATED_AT,
        "source": CaptureSource(
            type=CaptureSourceType.BROWSER,
            provider="chrome",
            url="https://example.com/notes?q=1&r=2",
        ),
        "payload_type": CapturePayloadType.WEBPAGE,
        "raw_object": make_raw_object(),
        "error": UNICODE_ERROR,
        "context": CaptureContext(
            captured_at=CAPTURED_AT, device="laptop", application="browser-extension"
        ),
        "intent": CaptureIntent(
            action=IntentAction.ANALYZE, collection="reading", tags=["arch", "\u4f60\u597d"]
        ),
        "title": "A note \u2014 \U0001f30d",
    }
    return CaptureRecord(**(fields | overrides))


def make_minimal_record(**overrides: Any) -> CaptureRecord:
    """A record with every optional field left out."""
    fields: dict[str, Any] = {
        "id": "cap_minimal_01",
        "status": CaptureStatus.RECEIVED,
        "received_at": RECEIVED_AT,
        "source": CaptureSource(type=CaptureSourceType.API),
        "payload_type": CapturePayloadType.TEXT,
        "context": CaptureContext(captured_at=CAPTURED_AT),
    }
    return CaptureRecord(**(fields | overrides))


def make_content(
    content_id: str = "con_persist_01",
    capture_id: str = "cap_persist_01",
    **overrides: Any,
) -> ContentObject:
    """A fully populated canonical content object.

    Deliberately *full*: every field the persistence layer must carry through
    a round trip is populated, so a fidelity test asserts fidelity rather than
    the survival of three fields.
    """
    fields: dict[str, Any] = {
        "id": content_id,
        "type": ContentType.TEXT,
        "source": ContentSource(
            capture_id=capture_id, provider="chrome", url="https://example.com/notes?q=1&r=2"
        ),
        "original": OriginalReference(
            asset_id="ast_original", mime_type="text/plain", sha256=RAW_SHA256
        ),
        "title": "A note \u2014 \U0001f30d",
        "metadata": {"language": "en", "counters": {"words": 6}, "draft": False},
        "segments": [
            Segment(
                id="seg_01",
                type=SegmentType.TEXT,
                text="\u041f\u0440\u0438\u0432\u0435\u0442\r\n\tindented   ",
                provenance=Provenance(
                    capture_id=capture_id,
                    source_type=ProvenanceSourceType.ORIGINAL,
                    asset_id="ast_original",
                    processor="text",
                    processor_version="0.2",
                ),
                position=0,
            )
        ],
        "assets": [
            Asset(
                id="ast_original",
                role=AssetRole.ORIGINAL,
                mime_type="text/plain",
                ref=f"sha256:{RAW_SHA256}",
                sha256=RAW_SHA256,
            )
        ],
        "derived": DerivedContent(summary="A note.", topics=["architecture", "\u4f60\u597d"]),
        "processing": [
            ProcessingRecord(
                processor="text",
                processor_version="0.2",
                started_at=RECEIVED_AT,
                completed_at=UPDATED_AT,
                status=ProcessingStatus.COMPLETE,
            )
        ],
    }
    return ContentObject(**(fields | overrides))
