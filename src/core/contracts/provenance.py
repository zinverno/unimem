"""Provenance: where a piece of content came from.

Every :class:`~core.contracts.segment.Segment` carries provenance, so any text
in the system can be traced back to the capture (and, where relevant, the
asset) it originated from.
"""

from core.contracts.base import DomainModel, Identifier, NonBlankStr
from core.contracts.enums import ProvenanceSourceType


class Provenance(DomainModel):
    """The origin of a derived piece of content."""

    capture_id: Identifier
    source_type: ProvenanceSourceType
    asset_id: Identifier | None = None
    processor: NonBlankStr | None = None
    processor_version: NonBlankStr | None = None
