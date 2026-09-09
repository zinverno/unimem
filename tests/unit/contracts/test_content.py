"""Behaviour of the canonical content object."""

import json
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

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
    ProvenanceSourceType,
    Segment,
    SegmentType,
    SpatialLocation,
    TemporalLocation,
)
from tests.unit.contracts.builders import (
    CAPTURED_AT,
    make_asset,
    make_content_object,
    make_provenance,
    make_segment,
)


def test_content_object_round_trips_through_json(content_object: ContentObject) -> None:
    encoded = json.dumps(content_object.model_dump(mode="json"))
    assert ContentObject.model_validate(json.loads(encoded)) == content_object


def test_rich_content_object_round_trips_without_loss() -> None:
    """A video object with several segments and assets survives serialization."""
    original = make_content_object(
        id="con_video",
        type=ContentType.VIDEO,
        source=ContentSource(capture_id="cap_01", provider="upload"),
        original=OriginalReference(asset_id="ast_video", mime_type="video/mp4", sha256="c" * 64),
        title="Design review",
        metadata={"duration_seconds": 612.5, "language": "en"},
        assets=[
            make_asset(id="ast_video", role=AssetRole.ORIGINAL, mime_type="video/mp4"),
            make_asset(id="ast_audio", role=AssetRole.AUDIO, mime_type="audio/wav"),
            make_asset(id="ast_frame", role=AssetRole.KEYFRAME, mime_type="image/png"),
        ],
        segments=[
            make_segment(
                id="seg_speech",
                type=SegmentType.TRANSCRIPT,
                text="the canonical object is not Markdown",
                temporal=TemporalLocation(start=302.1, end=309.4),
                provenance=make_provenance(
                    source_type=ProvenanceSourceType.TRANSCRIPT,
                    asset_id="ast_audio",
                    processor="whisperish",
                    processor_version="2.1",
                ),
                position=0,
            ),
            make_segment(
                id="seg_ocr",
                type=SegmentType.OCR,
                text="ctxalloc",
                spatial=SpatialLocation(x=120.0, y=400.0, width=180.0, height=35.0),
                provenance=make_provenance(
                    source_type=ProvenanceSourceType.OCR, asset_id="ast_frame"
                ),
                position=1,
            ),
            make_segment(
                id="seg_scene",
                type=SegmentType.VISUAL,
                text=None,
                metadata={"shot": 4},
                provenance=make_provenance(
                    source_type=ProvenanceSourceType.VISION, asset_id="ast_frame"
                ),
                position=2,
            ),
        ],
        derived=DerivedContent(
            summary="A design review of the ingestion layer.",
            topics=["architecture", "ingestion"],
            entities=[Entity(name="ctxalloc", type="project", metadata={"mentions": 3})],
        ),
        processing=[
            ProcessingRecord(
                processor="video-pipeline",
                processor_version="0.2.0",
                started_at=CAPTURED_AT,
                completed_at=CAPTURED_AT + timedelta(minutes=3),
                status=ProcessingStatus.PARTIAL,
                warnings=["1 keyframe unreadable"],
            )
        ],
    )
    encoded = json.dumps(original.model_dump(mode="json"))
    restored = ContentObject.model_validate(json.loads(encoded))
    assert restored == original
    assert [segment.id for segment in restored.segments] == ["seg_speech", "seg_ocr", "seg_scene"]
    assert restored.segments[0].temporal == TemporalLocation(start=302.1, end=309.4)
    assert restored.derived.entities[0].name == "ctxalloc"


def test_content_object_rejects_unknown_fields(content_object: ContentObject) -> None:
    data = content_object.model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContentObject.model_validate(data | {"markdown": "# canonical?"})


def test_content_object_has_no_embedding_or_chunk_fields() -> None:
    """Vector concerns belong to a later retrieval phase."""
    assert "embedding" not in ContentObject.model_fields
    assert "chunks" not in ContentObject.model_fields
    assert set(DerivedContent.model_fields) == {"summary", "topics", "entities"}


def test_segments_must_trace_back_to_the_capture_of_this_object() -> None:
    with pytest.raises(ValidationError, match="has provenance from capture"):
        make_content_object(
            segments=[make_segment(provenance=make_provenance(capture_id="cap_other"))]
        )


def test_segment_asset_references_must_resolve() -> None:
    with pytest.raises(ValidationError, match="references unknown asset"):
        make_content_object(
            segments=[make_segment(provenance=make_provenance(asset_id="ast_missing"))]
        )


def test_segment_may_reference_the_original_asset() -> None:
    content = make_content_object(
        assets=[],
        original=OriginalReference(asset_id="ast_01"),
        segments=[make_segment(provenance=make_provenance(asset_id="ast_01"))],
    )
    assert content.segments[0].provenance.asset_id == "ast_01"


def test_duplicate_segment_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate segment id"):
        make_content_object(segments=[make_segment(), make_segment()])


def test_duplicate_asset_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate asset id"):
        make_content_object(assets=[make_asset(), make_asset(role=AssetRole.THUMBNAIL)])


def test_content_object_may_have_no_segments_yet() -> None:
    content = make_content_object(segments=[], assets=[])
    assert content.segments == []
    assert content.derived == DerivedContent()
    assert content.processing == []


def test_content_metadata_must_be_json_serializable() -> None:
    with pytest.raises(ValidationError):
        make_content_object(metadata={"captured": {1, 2}})


def test_content_metadata_survives_json_round_trip() -> None:
    metadata: dict[str, Any] = {"word_count": 812, "tags": ["a", "b"], "nested": {"ok": None}}
    content = make_content_object(metadata=metadata)
    assert ContentObject.model_validate(content.model_dump(mode="json")).metadata == metadata


def test_entity_requires_a_name() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        Entity.model_validate({"name": "  "})


def test_content_object_is_independent_of_capture_payload_details() -> None:
    """The canonical object references its origin, it does not embed the raw capture."""
    assert "payload" not in ContentObject.model_fields
    assert set(ContentSource.model_fields) == {"capture_id", "provider", "url"}


def test_assignment_is_validated(content_object: ContentObject) -> None:
    orphan = Segment(
        id="seg_orphan",
        type=SegmentType.TEXT,
        text="from elsewhere",
        provenance=make_provenance(capture_id="cap_other"),
    )
    with pytest.raises(ValidationError, match="has provenance from capture"):
        content_object.segments = [orphan]


def test_asset_list_accepts_every_role() -> None:
    assets = [
        Asset(id=f"ast_{role.value}", role=role, mime_type="application/octet-stream", ref=role)
        for role in AssetRole
    ]
    content = make_content_object(assets=assets, segments=[], original=OriginalReference())
    assert len(content.assets) == len(AssetRole)
