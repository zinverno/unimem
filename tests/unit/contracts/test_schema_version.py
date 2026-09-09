"""Schema versioning and enum wire-format stability."""

from datetime import UTC, datetime
from enum import StrEnum

import pytest
from pydantic import BaseModel, ValidationError

from core.contracts import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    AssetRole,
    CaptureContext,
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentType,
    IntentAction,
    ProcessingStatus,
    ProvenanceSourceType,
    SegmentType,
)
from tests.unit.contracts.builders import make_content_object, make_envelope


def make_capture_record() -> CaptureRecord:
    """A minimal valid capture record at the current schema version."""
    return CaptureRecord(
        id="cap_01",
        status=CaptureStatus.RECEIVED,
        received_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        source=CaptureSource(type=CaptureSourceType.API),
        payload_type=CapturePayloadType.TEXT,
        context=CaptureContext(captured_at=datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC)),
    )


VERSIONED_MODELS: list[type[BaseModel]] = [CaptureEnvelope, CaptureRecord, ContentObject]


def test_current_schema_version_is_supported() -> None:
    assert SCHEMA_VERSION == "0.2"
    assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_every_readable_version_is_listed() -> None:
    """0.1 stays supported for exactly as long as this build can read it."""
    assert frozenset({"0.1", "0.2"}) == SUPPORTED_SCHEMA_VERSIONS


@pytest.mark.parametrize("model", VERSIONED_MODELS)
def test_versioned_models_default_to_the_current_schema_version(model: type[BaseModel]) -> None:
    field = model.model_fields["schema_version"]
    assert field.default == SCHEMA_VERSION
    assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_schema_version_is_always_serialized() -> None:
    for instance in (make_envelope(), make_content_object(), make_capture_record()):
        assert instance.model_dump(mode="json")["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize("version", ["0.3", "1.0", "0.1.0", "", "latest", "0.2.0"])
def test_unknown_schema_version_is_rejected(version: str) -> None:
    data = make_content_object().model_dump(mode="json")
    with pytest.raises(ValidationError, match="schema_version"):
        ContentObject.model_validate(data | {"schema_version": version})


def test_unknown_schema_version_is_rejected_on_envelopes() -> None:
    data = make_envelope().model_dump(mode="json")
    with pytest.raises(ValidationError, match="schema_version"):
        CaptureEnvelope.model_validate(data | {"schema_version": "0.3"})


def test_a_legacy_envelope_is_still_readable() -> None:
    """0.2 changed no envelope field, so a 0.1 envelope validates unchanged."""
    data = make_envelope().model_dump(mode="json")

    legacy = CaptureEnvelope.model_validate(data | {"schema_version": "0.1"})

    assert legacy.schema_version == "0.1"
    assert legacy.model_dump(mode="json") == data | {"schema_version": "0.1"}


def test_a_legacy_content_object_is_still_readable() -> None:
    """0.2 changed no content-object field either."""
    data = make_content_object().model_dump(mode="json")

    legacy = ContentObject.model_validate(data | {"schema_version": "0.1"})

    assert legacy.schema_version == "0.1"
    assert legacy.model_dump(mode="json") == data | {"schema_version": "0.1"}


#: The exact wire values of every closed set in the domain. These are part of
#: the serialized format: changing one is a schema change, not a refactor.
ENUM_VALUES: list[tuple[type[StrEnum], list[str]]] = [
    (CaptureSourceType, ["browser", "filesystem", "upload", "api"]),
    (CapturePayloadType, ["text", "webpage", "image", "document", "video", "file", "url"]),
    (
        CaptureStatus,
        ["received", "stored", "queued", "processing", "complete", "partial", "failed"],
    ),
    (ContentType, ["text", "web", "image", "document", "video"]),
    (SegmentType, ["text", "section", "transcript", "ocr", "visual"]),
    (ProvenanceSourceType, ["original", "html", "ocr", "transcript", "vision", "processor"]),
    (AssetRole, ["original", "image", "keyframe", "thumbnail", "audio", "attachment"]),
    (ProcessingStatus, ["complete", "partial", "failed"]),
    (IntentAction, ["save", "analyze"]),
]


@pytest.mark.parametrize(
    ("enum_type", "expected"), ENUM_VALUES, ids=lambda arg: getattr(arg, "__name__", "")
)
def test_enum_values_are_stable_and_lowercase(
    enum_type: type[StrEnum], expected: list[str]
) -> None:
    values = [member.value for member in enum_type]
    assert values == expected
    assert all(value == value.lower() for value in values)


def test_enums_serialize_as_their_string_values() -> None:
    data = make_content_object().model_dump(mode="json")
    assert data["type"] == "web"
    assert data["segments"][0]["type"] == "text"
    assert data["segments"][0]["provenance"]["source_type"] == "html"
    assert data["assets"][0]["role"] == "original"

    envelope = make_envelope().model_dump(mode="json")
    assert envelope["source"]["type"] == "browser"
    assert envelope["payload"]["type"] == "webpage"
    assert envelope["intent"]["action"] == "save"


def test_enum_members_are_their_wire_values() -> None:
    """Members are plain strings, so JSON output needs no custom encoder."""
    assert isinstance(SegmentType.OCR, str)
    assert str(SegmentType.OCR) == "ocr"
    assert str(AssetRole.THUMBNAIL) == "thumbnail"
