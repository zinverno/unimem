"""Strict v1 Markdown and v2 required-image delivery contracts."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
DeliveryId = Annotated[str, Field(pattern=UUID_PATTERN)]
SourceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_MARKDOWN_BYTES = 1024 * 1024
State = Literal["pending", "claimed", "imported", "conflict", "failed", "ambiguous"]
FailureCode = Literal[
    "path_rejected",
    "file_exists",
    "digest_mismatch",
    "write_failed",
    "write_ambiguous",
    "config_changed",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Destination(Contract):
    destination_id: DeliveryId
    display_name: str = Field(min_length=1, max_length=80)
    receiver_id: DeliveryId | None = None


class DeliveryFields(Contract):
    delivery_id: DeliveryId
    destination_id: DeliveryId
    source_capture_id: SourceId
    source_content_id: SourceId
    export_format: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    export_version: str = Field(pattern=r"^[0-9][0-9.]{0,15}$")
    markdown: str
    markdown_sha256: Digest
    suggested_filename: str = Field(pattern=r"^unimem-[0-9a-f]{64}\.md$")
    created_at: AwareDatetime
    state: State = "pending"
    lease_expires_at: AwareDatetime | None = None
    error_code: FailureCode | None = None


class Delivery(DeliveryFields):
    protocol_version: Literal["1"] = "1"


MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = MAX_MARKDOWN_BYTES + MAX_ATTACHMENT_BYTES


class Attachment(Contract):
    asset_id: SourceId
    mime_type: Literal["image/png", "image/jpeg"]
    size_bytes: int = Field(gt=0, le=MAX_ATTACHMENT_BYTES, strict=True)
    sha256: Digest
    relative_name: str = Field(pattern=r"^unimem-[0-9a-f]{64}\.(png|jpg)$")

    @model_validator(mode="after")
    def extension(self) -> "Attachment":
        suffix = ".png" if self.mime_type == "image/png" else ".jpg"
        if not self.relative_name.endswith(suffix):
            raise ValueError("attachment extension mismatch")
        return self


class ImageDelivery(DeliveryFields):
    protocol_version: Literal["2"] = "2"
    attachments: tuple[Attachment]
    package_sha256: Digest


def package_digest(d: ImageDelivery) -> str:
    a = d.attachments[0]
    fields = [
        "2",
        d.delivery_id,
        d.destination_id,
        d.source_capture_id,
        d.source_content_id,
        d.export_format,
        d.export_version,
        d.suggested_filename,
        d.markdown_sha256,
        a.asset_id,
        a.mime_type,
        a.size_bytes,
        a.sha256,
        a.relative_name,
    ]
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()


AnyDelivery = Delivery | ImageDelivery
DELIVERY_ADAPTER: TypeAdapter[AnyDelivery] = TypeAdapter(AnyDelivery)


class DeliveryRequest(Contract):
    destination_id: DeliveryId
    source_capture_id: SourceId


class ClaimRequest(Contract):
    receiver_id: DeliveryId
    claim_id: DeliveryId


class AckRequest(ClaimRequest):
    markdown_sha256: Digest


class PackageAckRequest(ClaimRequest):
    package_sha256: Digest


class AttachmentPermission(Contract):
    receiver_id: DeliveryId
    enabled: bool


class FailureRequest(ClaimRequest):
    error_code: FailureCode


class DeliveryError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
