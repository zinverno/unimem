"""The canonical content object.

:class:`ContentObject` is the normalized representation every processor
produces and every later stage reads. Markdown, JSON views, and anything else
are derived from it; none of them are a source of truth.
"""

from collections.abc import Iterable
from typing import Self

from pydantic import Field, model_validator

from core.contracts.asset import Asset
from core.contracts.base import (
    SCHEMA_VERSION,
    DomainModel,
    Identifier,
    JsonMapping,
    NonBlankStr,
    SchemaVersion,
    Sha256,
)
from core.contracts.enums import ContentType
from core.contracts.processing import ProcessingRecord
from core.contracts.segment import Segment


def _reject_duplicates(ids: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ValueError(f"duplicate {label} id {identifier!r}")
        seen.add(identifier)


class ContentSource(DomainModel):
    """The capture this content object was normalized from."""

    capture_id: Identifier
    provider: NonBlankStr | None = None
    url: NonBlankStr | None = None


class OriginalReference(DomainModel):
    """A pointer to the immutable raw original this content came from."""

    asset_id: Identifier | None = None
    mime_type: NonBlankStr | None = None
    sha256: Sha256 | None = None


class Entity(DomainModel):
    """Something named that was recognised in the content.

    Intentionally minimal: entity modelling belongs to a later phase.
    """

    name: NonBlankStr
    type: NonBlankStr | None = None
    metadata: JsonMapping = Field(default_factory=dict)


class DerivedContent(DomainModel):
    """Analysis results derived from the segments.

    Always reproducible from the content object plus its processors; never an
    input to the system.
    """

    summary: NonBlankStr | None = None
    topics: list[str] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)


class ContentObject(DomainModel):
    """The canonical normalized representation of captured content."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    type: ContentType
    source: ContentSource
    original: OriginalReference
    title: NonBlankStr | None = None
    metadata: JsonMapping = Field(default_factory=dict)
    segments: list[Segment] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    derived: DerivedContent = Field(default_factory=DerivedContent)
    processing: list[ProcessingRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> Self:
        _reject_duplicates((segment.id for segment in self.segments), "segment")
        _reject_duplicates((asset.id for asset in self.assets), "asset")

        known_assets = {asset.id for asset in self.assets}
        if self.original.asset_id is not None:
            known_assets.add(self.original.asset_id)

        for segment in self.segments:
            if segment.provenance.capture_id != self.source.capture_id:
                raise ValueError(
                    f"segment {segment.id!r} has provenance from capture "
                    f"{segment.provenance.capture_id!r}, "
                    f"but this content object comes from {self.source.capture_id!r}"
                )
            asset_id = segment.provenance.asset_id
            if asset_id is not None and asset_id not in known_assets:
                raise ValueError(f"segment {segment.id!r} references unknown asset {asset_id!r}")
        return self
