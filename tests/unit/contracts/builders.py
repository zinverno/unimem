"""Builders for valid contract instances.

Tests start from a known-good object and change one thing, so a failure points
at the rule under test rather than at incidental setup.
"""

from datetime import UTC, datetime
from typing import Any

from core.contracts import (
    Asset,
    AssetRole,
    CaptureContext,
    CaptureEnvelope,
    CaptureIntent,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    ContentObject,
    ContentSource,
    ContentType,
    IntentAction,
    OriginalReference,
    Provenance,
    ProvenanceSourceType,
    Segment,
    SegmentType,
)

CAPTURED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def make_envelope(**overrides: Any) -> CaptureEnvelope:
    """A valid webpage capture envelope, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "cap_01",
        "source": CaptureSource(
            type=CaptureSourceType.BROWSER,
            provider="chrome",
            url="https://example.com/architecture",
        ),
        "payload": CapturePayload(
            type=CapturePayloadType.WEBPAGE,
            mime_type="text/html",
            html="<h1>Architecture</h1>",
            title="Architecture",
        ),
        "context": CaptureContext(
            captured_at=CAPTURED_AT,
            device="laptop",
            application="browser-extension",
        ),
        "intent": CaptureIntent(action=IntentAction.SAVE, collection="reading", tags=["arch"]),
    }
    return CaptureEnvelope(**(fields | overrides))


def make_provenance(**overrides: Any) -> Provenance:
    """A valid provenance, with optional field overrides."""
    fields: dict[str, Any] = {
        "capture_id": "cap_01",
        "source_type": ProvenanceSourceType.HTML,
    }
    return Provenance(**(fields | overrides))


def make_segment(**overrides: Any) -> Segment:
    """A valid text segment, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "seg_01",
        "type": SegmentType.TEXT,
        "text": "The canonical object is not Markdown.",
        "provenance": make_provenance(),
        "position": 0,
    }
    return Segment(**(fields | overrides))


def make_asset(**overrides: Any) -> Asset:
    """A valid asset, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "ast_01",
        "role": AssetRole.ORIGINAL,
        "mime_type": "text/html",
        "ref": "object://captures/cap_01/original",
    }
    return Asset(**(fields | overrides))


def make_content_object(**overrides: Any) -> ContentObject:
    """A valid content object, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "con_01",
        "type": ContentType.WEB,
        "source": ContentSource(capture_id="cap_01", url="https://example.com/architecture"),
        "original": OriginalReference(asset_id="ast_01", mime_type="text/html"),
        "title": "Architecture",
        "segments": [make_segment()],
        "assets": [make_asset()],
    }
    return ContentObject(**(fields | overrides))
