"""Segments: the reusable unit of structured content.

A segment is a piece of a content object — a paragraph, a section, a
transcript cue, an OCR box — together with where it sits (in time, in space,
in reading order) and where it came from.
"""

from typing import Self

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from core.contracts.base import DomainModel, Identifier, JsonMapping, NonBlankStr
from core.contracts.enums import SegmentType
from core.contracts.provenance import Provenance

#: Segment types whose entire meaning is the text they carry.
_TEXT_REQUIRED_TYPES = frozenset(
    {SegmentType.TEXT, SegmentType.TRANSCRIPT, SegmentType.OCR},
)

_BOX_FIELDS = ("x", "y", "width", "height")


class TemporalLocation(DomainModel):
    """Position of a segment on a media timeline, in seconds."""

    start: NonNegativeFloat | None = None
    end: NonNegativeFloat | None = None

    @model_validator(mode="after")
    def _check_interval(self) -> Self:
        if self.start is None and self.end is None:
            raise ValueError("temporal location must set at least one of start or end")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must not be earlier than start")
        return self


class SpatialLocation(DomainModel):
    """Position of a segment within a document page, layout, or image.

    Coordinates are in the units of the referenced original (pixels for
    images, points for documents); the contract does not fix a unit, only the
    requirement that a bounding box is complete when present.
    """

    page: PositiveInt | None = None
    heading: NonBlankStr | None = None
    x: NonNegativeFloat | None = None
    y: NonNegativeFloat | None = None
    width: NonNegativeFloat | None = None
    height: NonNegativeFloat | None = None

    @model_validator(mode="after")
    def _check_location(self) -> Self:
        box = [getattr(self, name) for name in _BOX_FIELDS]
        if any(value is not None for value in box) and any(value is None for value in box):
            raise ValueError("a bounding box requires all of x, y, width and height")
        if self.page is None and self.heading is None and all(value is None for value in box):
            raise ValueError("spatial location must locate something")
        return self


class Segment(DomainModel):
    """A structured piece of a content object.

    Provenance is mandatory: a segment that cannot be traced back to a capture
    is not representable.
    """

    id: Identifier
    type: SegmentType
    text: NonBlankStr | None = None
    temporal: TemporalLocation | None = None
    spatial: SpatialLocation | None = None
    provenance: Provenance
    metadata: JsonMapping = Field(default_factory=dict)
    position: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _check_text(self) -> Self:
        if self.type in _TEXT_REQUIRED_TYPES and self.text is None:
            raise ValueError(f"segment of type {self.type.value!r} requires text")
        return self
