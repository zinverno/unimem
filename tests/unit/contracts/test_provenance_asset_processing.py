"""Behaviour of provenance, assets, and processing records."""

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from core.contracts import (
    Asset,
    AssetRole,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
)
from tests.unit.contracts.builders import CAPTURED_AT, make_asset, make_provenance


def test_provenance_requires_a_capture_id() -> None:
    with pytest.raises(ValidationError, match="capture_id"):
        Provenance.model_validate({"source_type": "ocr"})


def test_provenance_round_trips_through_json() -> None:
    provenance = make_provenance(
        source_type=ProvenanceSourceType.OCR,
        asset_id="ast_02",
        processor="tesseract",
        processor_version="5.3.4",
    )
    encoded = json.dumps(provenance.model_dump(mode="json"))
    assert Provenance.model_validate(json.loads(encoded)) == provenance


def test_provenance_does_not_track_models_or_prompts() -> None:
    """Model/provider tracking belongs to a later AI processing phase."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Provenance.model_validate(
            {"capture_id": "cap_01", "source_type": "vision", "model": "some-model"}
        )


def test_asset_reference_is_storage_neutral() -> None:
    for ref in ["object://bucket/key", "s3://b/k", "/var/data/x", "urn:x:1"]:
        assert make_asset(ref=ref).ref == ref


def test_asset_round_trips_through_json() -> None:
    asset = make_asset(role=AssetRole.KEYFRAME, mime_type="image/png", sha256="b" * 64)
    encoded = json.dumps(asset.model_dump(mode="json"))
    assert Asset.model_validate(json.loads(encoded)) == asset


def test_asset_requires_a_reference_and_mime_type() -> None:
    with pytest.raises(ValidationError):
        Asset.model_validate({"id": "ast_01", "role": "image"})


def test_asset_rejects_a_malformed_digest() -> None:
    with pytest.raises(ValidationError, match="should match pattern"):
        make_asset(sha256="B" * 64)


def test_processing_record_round_trips_through_json() -> None:
    record = ProcessingRecord(
        processor="html-normalizer",
        processor_version="0.3.1",
        started_at=CAPTURED_AT,
        completed_at=CAPTURED_AT + timedelta(seconds=4),
        status=ProcessingStatus.PARTIAL,
        warnings=["3 images skipped"],
    )
    encoded = json.dumps(record.model_dump(mode="json"))
    assert ProcessingRecord.model_validate(json.loads(encoded)) == record


def test_processing_record_completed_before_started_is_rejected() -> None:
    with pytest.raises(ValidationError, match="completed_at must not be earlier than started_at"):
        ProcessingRecord.model_validate(
            {
                "processor": "html-normalizer",
                "processor_version": "0.3.1",
                "started_at": CAPTURED_AT.isoformat(),
                "completed_at": (CAPTURED_AT - timedelta(seconds=1)).isoformat(),
                "status": "complete",
            }
        )


def test_processing_record_may_still_be_running() -> None:
    record = ProcessingRecord(
        processor="transcriber",
        processor_version="1.0.0",
        started_at=CAPTURED_AT,
        status=ProcessingStatus.PARTIAL,
    )
    assert record.completed_at is None
    assert record.warnings == []


def test_failed_processing_record_does_not_require_errors() -> None:
    record = ProcessingRecord(
        processor="transcriber",
        processor_version="1.0.0",
        started_at=CAPTURED_AT,
        completed_at=CAPTURED_AT,
        status=ProcessingStatus.FAILED,
    )
    assert record.errors == []


def test_processing_record_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValidationError, match="should have timezone info"):
        ProcessingRecord.model_validate(
            {
                "processor": "p",
                "processor_version": "1",
                "started_at": "2026-01-02T03:04:05",
                "status": "complete",
            }
        )
