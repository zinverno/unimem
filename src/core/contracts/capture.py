"""Capture contracts: what entered the system, and its lifecycle record.

A :class:`CaptureEnvelope` describes *input only*. It never carries analysis
results — no summary, OCR text, transcript, embeddings, entities, topics, or
generated tags. Those belong to :mod:`core.contracts.content`, and unknown
fields are rejected so they cannot sneak in.
"""

from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from core.contracts.base import (
    SCHEMA_VERSION,
    DomainModel,
    Identifier,
    NonBlankStr,
    SchemaVersion,
    Sha256,
)
from core.contracts.enums import (
    CapturePayloadType,
    CaptureSourceType,
    CaptureStatus,
    IntentAction,
)


class CaptureSource(DomainModel):
    """Where the capture came from."""

    type: CaptureSourceType
    provider: NonBlankStr | None = None
    url: NonBlankStr | None = None


class CapturePayload(DomainModel):
    """The captured material itself, or a reference to it.

    ``file_ref`` is an opaque handle to bytes held outside this contract; it is
    not required to be a filesystem path.
    """

    type: CapturePayloadType
    mime_type: NonBlankStr | None = None
    text: NonBlankStr | None = None
    html: NonBlankStr | None = None
    file_ref: NonBlankStr | None = None
    title: NonBlankStr | None = None

    @model_validator(mode="after")
    def _check_payload_is_usable(self) -> Self:
        match self.type:
            case CapturePayloadType.TEXT:
                if self.text is None:
                    raise ValueError("text payload requires text")
            case CapturePayloadType.WEBPAGE:
                if self.html is None and self.text is None:
                    raise ValueError("webpage payload requires html or text")
            case CapturePayloadType.DOCUMENT:
                if self.file_ref is None and self.text is None:
                    raise ValueError("document payload requires file_ref or text")
            case CapturePayloadType.IMAGE | CapturePayloadType.VIDEO | CapturePayloadType.FILE:
                if self.file_ref is None:
                    raise ValueError(f"{self.type.value} payload requires file_ref")
            case CapturePayloadType.URL:
                pass  # checked against the source url on the envelope
        return self


class CaptureContext(DomainModel):
    """The circumstances of the capture."""

    captured_at: AwareDatetime
    device: NonBlankStr | None = None
    application: NonBlankStr | None = None


class CaptureIntent(DomainModel):
    """What the user asked for when capturing."""

    action: IntentAction | None = None
    collection: NonBlankStr | None = None
    tags: list[str] = Field(default_factory=list)


class CaptureEnvelope(DomainModel):
    """What entered the system and where it came from."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    source: CaptureSource
    payload: CapturePayload
    context: CaptureContext
    intent: CaptureIntent | None = None

    @model_validator(mode="after")
    def _check_url_payload(self) -> Self:
        if self.payload.type is CapturePayloadType.URL and self.source.url is None:
            raise ValueError("url payload requires source.url")
        return self


class RawObjectRef(DomainModel):
    """A reference to the stored raw original.

    Storage-neutral by design: ``ref`` is an opaque handle whose meaning is
    owned by whatever object store is introduced later.
    """

    id: Identifier
    mime_type: NonBlankStr | None = None
    sha256: Sha256 | None = None
    ref: NonBlankStr | None = None


class CaptureRecord(DomainModel):
    """The internal lifecycle record created after accepting an envelope.

    Phase 0A defines the contract only; nothing persists it.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    status: CaptureStatus
    received_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    source: CaptureSource
    payload_type: CapturePayloadType
    raw_object: RawObjectRef | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _check_timestamps(self) -> Self:
        if self.updated_at is not None and self.updated_at < self.received_at:
            raise ValueError("updated_at must not be earlier than received_at")
        return self
