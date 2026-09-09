"""Builders for the content objects the renderers are handed.

Rendering is a function of a ``ContentObject`` and nothing else, so these build
one directly. The default object is deliberately *full*: every field a renderer
could project — metadata, segments with and without text, assets, derived
analysis, processing history — is populated, so a test that asserts something
is absent from a rendering is asserting a decision rather than an empty object.
"""

from datetime import UTC, datetime
from typing import Any

from core.contracts import (
    Asset,
    AssetRole,
    ContentObject,
    ContentSource,
    ContentType,
    DerivedContent,
    Entity,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
    Segment,
    SegmentType,
    SpatialLocation,
)

CAPTURE_ID = "cap_render_01"
STARTED_AT = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 5, 6, 7, 8, 10, tzinfo=UTC)

#: A well-formed digest. Renderers never compute one; it is data like any other.
ORIGINAL_SHA256 = "b" * 64

#: Text with characters no ASCII escape survives readably, plus an emoji.
UNICODE_TEXT = "Привет, мир — 你好 🌍"

#: Line endings, tabs, and edge whitespace a projection must not tidy up.
WHITESPACE_TEXT = "line 1\r\n\tline 2  \n\n   trailing   "


def make_provenance(**overrides: Any) -> Provenance:
    """Provenance pointing at the original asset of the default object."""
    fields: dict[str, Any] = {
        "capture_id": CAPTURE_ID,
        "source_type": ProvenanceSourceType.ORIGINAL,
        "asset_id": "ast_original",
        "processor": "text",
        "processor_version": "0.1",
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


def make_textless_segment(**overrides: Any) -> Segment:
    """A segment whose meaning is not text, so it has no textual projection."""
    fields: dict[str, Any] = {
        "id": "seg_visual",
        "type": SegmentType.VISUAL,
        "spatial": SpatialLocation(page=1, x=10.0, y=20.0, width=100.0, height=40.0),
        "provenance": make_provenance(source_type=ProvenanceSourceType.VISION),
    }
    return Segment(**(fields | overrides))


def make_content_object(**overrides: Any) -> ContentObject:
    """A fully populated content object, with optional field overrides."""
    fields: dict[str, Any] = {
        "id": "con_render_01",
        "type": ContentType.WEB,
        "source": ContentSource(
            capture_id=CAPTURE_ID,
            provider="chrome",
            url="https://example.com/architecture",
        ),
        "original": OriginalReference(
            asset_id="ast_original",
            mime_type="text/html",
            sha256=ORIGINAL_SHA256,
        ),
        "title": "Architecture",
        "metadata": {"language": "en", "counters": {"words": 6, "links": 2}, "draft": False},
        "segments": [
            make_segment(),
            make_textless_segment(),
            make_segment(id="seg_02", text=UNICODE_TEXT, position=1),
        ],
        "assets": [
            Asset(
                id="ast_original",
                role=AssetRole.ORIGINAL,
                mime_type="text/html",
                ref=f"sha256:{ORIGINAL_SHA256}",
                sha256=ORIGINAL_SHA256,
                metadata={"bytes": 1024},
            ),
            Asset(
                id="ast_thumb",
                role=AssetRole.THUMBNAIL,
                mime_type="image/png",
                ref="sha256:" + "c" * 64,
            ),
        ],
        "derived": DerivedContent(
            summary="A note about canonical objects.",
            topics=["architecture", "contracts"],
            entities=[Entity(name="UniMem", type="project", metadata={"confidence": 0.9})],
        ),
        "processing": [
            ProcessingRecord(
                processor="text",
                processor_version="0.1",
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                status=ProcessingStatus.COMPLETE,
                warnings=["one segment carried no text"],
            )
        ],
    }
    return ContentObject(**(fields | overrides))
