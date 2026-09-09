"""Capture contracts: what entered the system, and its lifecycle record.

A :class:`CaptureEnvelope` describes *input only*. It never carries analysis
results — no summary, OCR text, transcript, embeddings, entities, topics, or
generated tags. Those belong to :mod:`core.contracts.content`, and unknown
fields are rejected so they cannot sneak in.
"""

from typing import Any, Final, Self

from pydantic import (
    AwareDatetime,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from core.contracts.base import (
    LEGACY_SCHEMA_VERSION,
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


#: The fields schema version 0.2 added to :class:`CaptureRecord`. A 0.1
#: document neither carries them nor may claim them, and one read by this build
#: is written back out without them.
CAPTURE_METADATA_FIELDS: Final = ("context", "intent", "title")


class CaptureRecord(DomainModel):
    """The internal lifecycle record created after accepting an envelope.

    It holds what the system knows *about* a capture. The captured material
    itself is never here: content lives in the raw object store, and
    ``raw_object`` is how this record reaches it.

    Since schema version 0.2 the record also carries the capture-time facts a
    submitter supplied — ``context``, ``intent``, and ``title`` — because they
    are properties of the capture event and there is nowhere else to keep them.
    ``context`` is required at 0.2: a capture that cannot say when it was taken
    is not worth much later, and the one field the client always has is
    ``captured_at``. ``intent`` and ``title`` are genuinely optional, and
    ``title`` means the title the submitter provided, never a generated one.

    ``received_at`` and ``context.captured_at`` are different facts —
    when the system accepted the capture, and when the user took it — and
    neither is ever substituted for the other.
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
    context: CaptureContext | None = None
    intent: CaptureIntent | None = None
    title: NonBlankStr | None = None

    @model_validator(mode="after")
    def _check_timestamps(self) -> Self:
        if self.updated_at is not None and self.updated_at < self.received_at:
            raise ValueError("updated_at must not be earlier than received_at")
        return self

    @model_validator(mode="after")
    def _check_metadata_matches_schema_version(self) -> Self:
        """Tie the 0.2 metadata fields to the version that introduced them.

        A 0.1 document predates them, so carrying one is not a lenient old
        record — it is a document whose version is wrong about its own shape,
        and reading it as 0.1 would then lose data on the way back out. A 0.2
        record, conversely, must carry ``context``.
        """
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            claimed = [name for name in CAPTURE_METADATA_FIELDS if getattr(self, name) is not None]
            if claimed:
                raise ValueError(
                    f"schema version {LEGACY_SCHEMA_VERSION} has no "
                    f"{', '.join(claimed)}; it was added in {SCHEMA_VERSION}"
                )
        elif self.context is None:
            raise ValueError(f"context is required from schema version {SCHEMA_VERSION}")
        return self

    @model_serializer(mode="wrap")
    def _serialize_for_its_own_schema_version(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Emit a 0.1 record as a 0.1 document, with no 0.2 keys at all.

        Every contract sets ``extra="forbid"``, so a reader built against 0.1
        rejects an unknown key rather than ignoring it — ``"context": null``
        would be as fatal to it as a populated one. A 0.1 record therefore goes
        back out shaped exactly as it came in, and stays readable by the build
        that wrote it.
        """
        data: dict[str, Any] = handler(self)
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            for name in CAPTURE_METADATA_FIELDS:
                data.pop(name, None)
        return data
