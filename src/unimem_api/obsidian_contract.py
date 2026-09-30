"""Version 1 outbound Markdown delivery. Independent of capture modality."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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


class Delivery(Contract):
    protocol_version: Literal["1"] = "1"
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


class DeliveryRequest(Contract):
    destination_id: DeliveryId
    source_capture_id: SourceId


class ClaimRequest(Contract):
    receiver_id: DeliveryId
    claim_id: DeliveryId


class AckRequest(ClaimRequest):
    markdown_sha256: Digest


class FailureRequest(ClaimRequest):
    error_code: FailureCode


class DeliveryError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
