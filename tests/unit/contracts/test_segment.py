"""Behaviour of segments and their temporal/spatial locations."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from core.contracts import Segment, SegmentType, SpatialLocation, TemporalLocation
from tests.unit.contracts.builders import make_provenance, make_segment


def test_segment_requires_provenance() -> None:
    """Every segment must be traceable back to its origin."""
    with pytest.raises(ValidationError, match="provenance"):
        Segment.model_validate({"id": "seg_01", "type": "text", "text": "orphan"})


def test_segment_provenance_survives_a_json_round_trip() -> None:
    segment = make_segment(provenance=make_provenance(asset_id="ast_01", processor="html-reader"))
    restored = Segment.model_validate(json.loads(json.dumps(segment.model_dump(mode="json"))))
    assert restored == segment
    assert restored.provenance.capture_id == "cap_01"


def test_segment_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Segment.model_validate(
            {
                "id": "seg_01",
                "type": "text",
                "text": "hello",
                "provenance": make_provenance().model_dump(mode="json"),
                "embedding": [0.1, 0.2],
            }
        )


@pytest.mark.parametrize("segment_type", ["text", "transcript", "ocr"])
def test_text_bearing_segments_require_text(segment_type: str) -> None:
    with pytest.raises(ValidationError, match="requires text"):
        Segment.model_validate(
            {
                "id": "seg_01",
                "type": segment_type,
                "provenance": make_provenance().model_dump(mode="json"),
            }
        )


@pytest.mark.parametrize("segment_type", ["section", "visual"])
def test_structural_segments_may_carry_no_text(segment_type: str) -> None:
    segment = make_segment(type=SegmentType(segment_type), text=None)
    assert segment.text is None


def test_segment_position_must_not_be_negative() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        make_segment(position=-1)


def test_valid_temporal_location_is_accepted() -> None:
    segment = make_segment(
        type=SegmentType.TRANSCRIPT,
        text="the canonical object",
        temporal=TemporalLocation(start=302.1, end=309.4),
    )
    assert segment.temporal is not None
    assert segment.temporal.start == 302.1
    assert segment.temporal.end == 309.4


def test_temporal_location_accepts_a_single_bound() -> None:
    assert TemporalLocation(start=12.0).end is None
    assert TemporalLocation(end=12.0).start is None


def test_temporal_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end must not be earlier than start"):
        TemporalLocation.model_validate({"start": 10.0, "end": 9.9})


@pytest.mark.parametrize("bounds", [{"start": -1.0}, {"end": -0.5}, {"start": -2.0, "end": -1.0}])
def test_negative_temporal_values_are_rejected(bounds: dict[str, float]) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        TemporalLocation.model_validate(bounds)


def test_empty_temporal_location_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one of start or end"):
        TemporalLocation.model_validate({})


def test_page_located_segment_is_accepted() -> None:
    segment = make_segment(spatial=SpatialLocation(page=17))
    assert segment.spatial is not None
    assert segment.spatial.page == 17


def test_heading_located_segment_is_accepted() -> None:
    spatial = SpatialLocation(heading="Architecture")
    segment = make_segment(type=SegmentType.SECTION, spatial=spatial)
    assert segment.spatial is not None
    assert segment.spatial.heading == "Architecture"


def test_bounding_box_is_accepted() -> None:
    box = SpatialLocation(x=120.0, y=400.0, width=180.0, height=35.0)
    segment = make_segment(type=SegmentType.OCR, text="ctxalloc", spatial=box)
    assert segment.spatial == box


@pytest.mark.parametrize("page", [0, -3])
def test_invalid_page_number_is_rejected(page: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        SpatialLocation.model_validate({"page": page})


@pytest.mark.parametrize("dimension", ["x", "y", "width", "height"])
def test_negative_spatial_dimensions_are_rejected(dimension: str) -> None:
    box: dict[str, Any] = {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}
    box[dimension] = -1.0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        SpatialLocation.model_validate(box)


def test_partial_bounding_box_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires all of x, y, width and height"):
        SpatialLocation.model_validate({"x": 120.0, "y": 400.0})


def test_empty_spatial_location_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must locate something"):
        SpatialLocation.model_validate({})


def test_segment_metadata_must_be_json_serializable() -> None:
    with pytest.raises(ValidationError):
        make_segment(metadata={"reader": object()})


def test_segment_metadata_accepts_nested_json_values() -> None:
    metadata: dict[str, Any] = {"selector": {"path": ["main", 2], "exact": True, "score": 0.5}}
    segment = make_segment(metadata=metadata)
    assert json.loads(json.dumps(segment.model_dump(mode="json")))["metadata"] == metadata
