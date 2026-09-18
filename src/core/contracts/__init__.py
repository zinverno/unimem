"""Canonical domain contracts.

The objects in this package define what flows through the system. They are
plain Pydantic models with no dependency on storage, transport, or AI
providers.
"""

from core.contracts.asset import Asset
from core.contracts.base import (
    AUDIO_SCHEMA_VERSION,
    AUDIO_SCHEMA_VERSIONS,
    CAPTURE_METADATA_SCHEMA_VERSION,
    CAPTURE_METADATA_SCHEMA_VERSIONS,
    SCHEMA_VERSION,
    SCHEMA_VERSIONS_BEFORE_AUDIO,
    SCHEMA_VERSIONS_BEFORE_CAPTURE_METADATA,
    SUPPORTED_SCHEMA_VERSIONS,
    DomainModel,
    Identifier,
    JsonMapping,
    NonBlankStr,
    SchemaVersion,
    Sha256,
)
from core.contracts.capture import (
    CaptureContext,
    CaptureEnvelope,
    CaptureIntent,
    CapturePayload,
    CaptureRecord,
    CaptureSource,
    RawObjectRef,
)
from core.contracts.content import (
    ContentObject,
    ContentSource,
    DerivedContent,
    Entity,
    OriginalReference,
)
from core.contracts.enums import (
    AssetRole,
    CapturePayloadType,
    CaptureSourceType,
    CaptureStatus,
    ContentType,
    IntentAction,
    ProcessingStatus,
    ProvenanceSourceType,
    SegmentType,
)
from core.contracts.processing import ProcessingRecord
from core.contracts.provenance import Provenance
from core.contracts.segment import Segment, SpatialLocation, TemporalLocation

__all__ = [
    "AUDIO_SCHEMA_VERSION",
    "AUDIO_SCHEMA_VERSIONS",
    "CAPTURE_METADATA_SCHEMA_VERSION",
    "CAPTURE_METADATA_SCHEMA_VERSIONS",
    "SCHEMA_VERSION",
    "SCHEMA_VERSIONS_BEFORE_AUDIO",
    "SCHEMA_VERSIONS_BEFORE_CAPTURE_METADATA",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Asset",
    "AssetRole",
    "CaptureContext",
    "CaptureEnvelope",
    "CaptureIntent",
    "CapturePayload",
    "CapturePayloadType",
    "CaptureRecord",
    "CaptureSource",
    "CaptureSourceType",
    "CaptureStatus",
    "ContentObject",
    "ContentSource",
    "ContentType",
    "DerivedContent",
    "DomainModel",
    "Entity",
    "Identifier",
    "IntentAction",
    "JsonMapping",
    "NonBlankStr",
    "OriginalReference",
    "ProcessingRecord",
    "ProcessingStatus",
    "Provenance",
    "ProvenanceSourceType",
    "RawObjectRef",
    "SchemaVersion",
    "Segment",
    "SegmentType",
    "Sha256",
    "SpatialLocation",
    "TemporalLocation",
]
