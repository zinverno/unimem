"""Behaviour of the capture envelope and the capture lifecycle record."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from core.contracts import (
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    IntentAction,
    RawObjectRef,
)
from tests.unit.contracts.builders import CAPTURED_AT


def test_valid_envelope_carries_what_was_captured(envelope: CaptureEnvelope) -> None:
    assert envelope.schema_version == "0.1"
    assert envelope.source.type is CaptureSourceType.BROWSER
    assert envelope.payload.type is CapturePayloadType.WEBPAGE
    assert envelope.context.captured_at == CAPTURED_AT
    assert envelope.intent is not None
    assert envelope.intent.action is IntentAction.SAVE


def test_envelope_round_trips_through_json(envelope: CaptureEnvelope) -> None:
    encoded = json.dumps(envelope.model_dump(mode="json"))
    assert CaptureEnvelope.model_validate(json.loads(encoded)) == envelope


def test_envelope_intent_is_optional(envelope_data: dict[str, Any]) -> None:
    del envelope_data["intent"]
    assert CaptureEnvelope.model_validate(envelope_data).intent is None


@pytest.mark.parametrize(
    "extra_field",
    ["summary", "ocr_text", "transcript", "embedding", "entities", "topics", "unexpected"],
)
def test_envelope_rejects_unknown_fields(envelope_data: dict[str, Any], extra_field: str) -> None:
    """An envelope describes input; derived analysis cannot be smuggled in."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CaptureEnvelope.model_validate(envelope_data | {extra_field: "nope"})


def test_text_payload_without_text_is_rejected() -> None:
    with pytest.raises(ValidationError, match="text payload requires text"):
        CapturePayload.model_validate({"type": "text", "title": "no body"})


def test_text_payload_with_blank_text_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        CapturePayload.model_validate({"type": "text", "text": "   "})


def test_file_payload_without_file_ref_is_rejected() -> None:
    with pytest.raises(ValidationError, match="file payload requires file_ref"):
        CapturePayload.model_validate({"type": "file", "mime_type": "application/zip"})


def test_webpage_payload_requires_html_or_text() -> None:
    with pytest.raises(ValidationError, match="webpage payload requires html or text"):
        CapturePayload.model_validate({"type": "webpage", "title": "empty"})

    assert CapturePayload.model_validate({"type": "webpage", "text": "extracted"}).html is None


@pytest.mark.parametrize("payload_type", ["image", "video"])
def test_binary_payloads_require_a_file_reference(payload_type: str) -> None:
    with pytest.raises(ValidationError, match="requires file_ref"):
        CapturePayload.model_validate({"type": payload_type})

    assert CapturePayload.model_validate({"type": payload_type, "file_ref": "blob:1"})


def test_document_payload_accepts_text_or_file_ref() -> None:
    assert CapturePayload.model_validate({"type": "document", "text": "pasted"})
    assert CapturePayload.model_validate({"type": "document", "file_ref": "blob:1"})
    with pytest.raises(ValidationError, match="document payload requires file_ref or text"):
        CapturePayload.model_validate({"type": "document"})


def test_unknown_payload_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CapturePayload.model_validate({"type": "hologram", "text": "x"})


def test_url_payload_requires_a_source_url(envelope_data: dict[str, Any]) -> None:
    envelope_data["payload"] = {"type": "url"}
    envelope_data["source"] = {"type": "browser"}
    with pytest.raises(ValidationError, match=r"url payload requires source\.url"):
        CaptureEnvelope.model_validate(envelope_data)

    envelope_data["source"] = {"type": "browser", "url": "https://example.com/a"}
    assert CaptureEnvelope.model_validate(envelope_data)


def test_naive_captured_at_is_rejected(envelope_data: dict[str, Any]) -> None:
    envelope_data["context"]["captured_at"] = "2026-01-02T03:04:05"
    with pytest.raises(ValidationError, match="should have timezone info"):
        CaptureEnvelope.model_validate(envelope_data)


def test_empty_id_is_rejected(envelope_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CaptureEnvelope.model_validate(envelope_data | {"id": ""})


def test_blank_id_is_rejected(envelope_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        CaptureEnvelope.model_validate(envelope_data | {"id": "   "})


def test_opaque_ids_are_not_forced_into_a_format(envelope_data: dict[str, Any]) -> None:
    """Phase 0A has not decided how ids are minted, so any non-blank string works."""
    assert CaptureEnvelope.model_validate(envelope_data | {"id": "cap/2026/01/02#7"})


def test_assignment_is_validated(envelope: CaptureEnvelope) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        envelope.id = "   "


def test_capture_record_round_trips_through_json() -> None:
    record = CaptureRecord(
        id="cap_01",
        status=CaptureStatus.STORED,
        received_at=CAPTURED_AT,
        updated_at=CAPTURED_AT + timedelta(seconds=2),
        source=CaptureSource(type=CaptureSourceType.UPLOAD),
        payload_type=CapturePayloadType.DOCUMENT,
        raw_object=RawObjectRef(
            id="raw_01",
            mime_type="application/pdf",
            sha256="a" * 64,
            ref="object://raw/raw_01",
        ),
    )
    encoded = json.dumps(record.model_dump(mode="json"))
    assert CaptureRecord.model_validate(json.loads(encoded)) == record


def test_capture_record_rejects_updated_before_received() -> None:
    with pytest.raises(ValidationError, match="updated_at must not be earlier"):
        CaptureRecord.model_validate(
            {
                "id": "cap_01",
                "status": "failed",
                "received_at": CAPTURED_AT.isoformat(),
                "updated_at": (CAPTURED_AT - timedelta(seconds=1)).isoformat(),
                "source": {"type": "api"},
                "payload_type": "text",
            }
        )


def test_capture_record_failure_does_not_require_an_error_message() -> None:
    """A failure may be known before its cause is."""
    record = CaptureRecord(
        id="cap_01",
        status=CaptureStatus.FAILED,
        received_at=datetime.now(tz=UTC),
        source=CaptureSource(type=CaptureSourceType.API),
        payload_type=CapturePayloadType.TEXT,
    )
    assert record.error is None


def test_raw_object_ref_rejects_a_malformed_digest() -> None:
    with pytest.raises(ValidationError):
        RawObjectRef.model_validate({"id": "raw_01", "sha256": "not-a-digest"})
