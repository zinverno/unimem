"""Assets: binary or external artifacts attached to a content object."""

from pydantic import Field

from core.contracts.base import DomainModel, Identifier, JsonMapping, NonBlankStr, Sha256
from core.contracts.enums import AssetRole


class Asset(DomainModel):
    """A binary or external artifact related to a content object.

    ``ref`` is an opaque, storage-neutral reference. It is deliberately *not* a
    filesystem path, URL, or bucket key in the contract: how assets are stored
    is decided outside this package.
    """

    id: Identifier
    role: AssetRole
    mime_type: NonBlankStr
    ref: NonBlankStr
    sha256: Sha256 | None = None
    metadata: JsonMapping = Field(default_factory=dict)
