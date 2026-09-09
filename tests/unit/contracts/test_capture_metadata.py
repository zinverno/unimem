"""Durable capture metadata, and what schema version 0.2 means for it.

Two things are being pinned here. That a capture record can finally hold the
facts a submitter supplied — when, where, why, and under what title — and that
the version bump those fields arrived with is honest in both directions: a 0.2
record must carry ``context``, and a 0.1 record must neither claim the new
fields nor grow them on the way back out.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from core.contracts import (
    SCHEMA_VERSION,
    CaptureContext,
    CaptureIntent,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    IntentAction,
    RawObjectRef,
)

CAPTURED_AT = datetime(2026, 4, 5, 6, 7, 8, 90000, tzinfo=UTC)
RECEIVED_AT = CAPTURED_AT + timedelta(minutes=30)

#: The three fields schema version 0.2 introduced.
METADATA_FIELDS = ("context", "intent", "title")


class LegacyCaptureRecordV01(BaseModel):
    """A stand-in for a reader built against schema version 0.1.

    It has exactly the fields 0.1 defined and, like every real contract in this
    system, ``extra="forbid"``. That is the whole point: an old reader does not
    ignore an unknown key, it refuses the document — so ``"context": null`` is
    just as fatal to it as a populated ``context``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    id: str
    status: str
    received_at: datetime
    updated_at: datetime | None = None
    source: dict[str, Any]
    payload_type: str
    raw_object: dict[str, Any] | None = None
    error: str | None = None


def record_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "cap_meta_01",
        "status": CaptureStatus.STORED,
        "received_at": RECEIVED_AT,
        "source": CaptureSource(type=CaptureSourceType.BROWSER, provider="chrome"),
        "payload_type": CapturePayloadType.TEXT,
    }
    return fields | overrides


def make_context(**overrides: Any) -> CaptureContext:
    fields: dict[str, Any] = {
        "captured_at": CAPTURED_AT,
        "device": "laptop",
        "application": "browser-extension",
    }
    return CaptureContext(**(fields | overrides))


def make_intent(**overrides: Any) -> CaptureIntent:
    fields: dict[str, Any] = {
        "action": IntentAction.ANALYZE,
        "collection": "reading",
        "tags": ["architecture", "你好"],
    }
    return CaptureIntent(**(fields | overrides))


def test_a_current_record_carries_the_capture_metadata() -> None:
    record = CaptureRecord(
        **record_fields(), context=make_context(), intent=make_intent(), title="A note"
    )

    assert record.schema_version == SCHEMA_VERSION
    assert record.context == make_context()
    assert record.intent == make_intent()
    assert record.title == "A note"


def test_context_is_required_at_the_current_version() -> None:
    """A capture that cannot say when it was taken is not worth much later."""
    with pytest.raises(ValidationError, match="context is required"):
        CaptureRecord(**record_fields())


def test_intent_and_title_stay_optional() -> None:
    record = CaptureRecord(**record_fields(), context=make_context())

    assert record.intent is None
    assert record.title is None


def test_context_fields_survive_a_json_round_trip() -> None:
    record = CaptureRecord(**record_fields(), context=make_context())

    restored = CaptureRecord.model_validate_json(record.model_dump_json())

    assert restored.context is not None
    assert restored.context.captured_at == CAPTURED_AT
    assert restored.context.captured_at.tzinfo is not None
    assert restored.context.captured_at.microsecond == CAPTURED_AT.microsecond
    assert restored.context.device == "laptop"
    assert restored.context.application == "browser-extension"


def test_intent_fields_survive_a_json_round_trip() -> None:
    record = CaptureRecord(**record_fields(), context=make_context(), intent=make_intent())

    restored = CaptureRecord.model_validate_json(record.model_dump_json())

    assert restored.intent is not None
    assert restored.intent.action is IntentAction.ANALYZE
    assert restored.intent.collection == "reading"
    assert restored.intent.tags == ["architecture", "你好"]


def test_title_survives_exactly_and_stays_optional() -> None:
    titled = CaptureRecord(**record_fields(), context=make_context(), title="  Spaced — Title  ")
    untitled = CaptureRecord(**record_fields(), context=make_context())

    assert CaptureRecord.model_validate_json(titled.model_dump_json()).title == (
        "  Spaced — Title  "
    )
    assert CaptureRecord.model_validate_json(untitled.model_dump_json()).title is None


def test_a_blank_title_is_rejected() -> None:
    """``title`` is a real title or nothing; it is never an empty string."""
    with pytest.raises(ValidationError, match="must not be blank"):
        CaptureRecord(**record_fields(), context=make_context(), title="   ")


def test_captured_at_and_received_at_are_different_facts() -> None:
    """One is the user's clock, the other the system's. Neither replaces the other."""
    record = CaptureRecord(**record_fields(), context=make_context())

    assert record.received_at == RECEIVED_AT
    assert record.context is not None
    assert record.context.captured_at == CAPTURED_AT
    assert record.received_at != record.context.captured_at


def test_a_full_record_round_trips_intact() -> None:
    record = CaptureRecord(
        **record_fields(),
        updated_at=RECEIVED_AT + timedelta(seconds=2),
        raw_object=RawObjectRef(id="d" * 64, sha256="d" * 64, ref="sha256:" + "d" * 64),
        context=make_context(),
        intent=make_intent(),
        title="A note",
    )

    assert CaptureRecord.model_validate_json(record.model_dump_json()) == record


def test_a_legacy_record_without_the_new_fields_validates() -> None:
    """Exactly the document a 0.1 build wrote."""
    legacy = CaptureRecord.model_validate(
        {
            "schema_version": "0.1",
            "id": "cap_legacy_01",
            "status": "stored",
            "received_at": RECEIVED_AT.isoformat(),
            "source": {"type": "api"},
            "payload_type": "text",
        }
    )

    assert legacy.schema_version == "0.1"
    assert legacy.context is None
    assert legacy.intent is None
    assert legacy.title is None


@pytest.mark.parametrize("field", METADATA_FIELDS)
def test_a_legacy_record_may_not_claim_a_new_field(field: str) -> None:
    """0.1 predates these fields, so a 0.1 document carrying one is lying."""
    values: dict[str, Any] = {
        "context": make_context(),
        "intent": make_intent(),
        "title": "A note",
    }

    with pytest.raises(ValidationError, match=f"0.1 has no .*{field}"):
        CaptureRecord(**record_fields(), schema_version="0.1", **{field: values[field]})


def test_a_legacy_record_serializes_without_the_new_keys() -> None:
    """Not even as nulls: an old reader forbids extras and would reject them."""
    legacy = CaptureRecord(**record_fields(), schema_version="0.1")

    dumped = legacy.model_dump(mode="json")
    encoded = json.loads(legacy.model_dump_json())

    for field in METADATA_FIELDS:
        assert field not in dumped
        assert field not in encoded


def test_a_legacy_record_stays_readable_by_a_legacy_reader() -> None:
    """The real assertion: a 0.1 reader accepts what this build writes back."""
    legacy = CaptureRecord(**record_fields(), schema_version="0.1")

    reread = LegacyCaptureRecordV01.model_validate_json(legacy.model_dump_json())

    assert reread.schema_version == "0.1"
    assert reread.id == "cap_meta_01"


def test_a_legacy_record_round_trips_through_this_build_unchanged() -> None:
    original = {
        "schema_version": "0.1",
        "id": "cap_legacy_01",
        "status": "stored",
        "received_at": RECEIVED_AT.isoformat().replace("+00:00", "Z"),
        "updated_at": None,
        "source": {"type": "api", "provider": None, "url": None},
        "payload_type": "text",
        "raw_object": None,
        "error": None,
    }

    reserialized = CaptureRecord.model_validate(original).model_dump(mode="json")

    assert reserialized == original


def test_a_current_record_does_serialize_the_new_keys() -> None:
    record = CaptureRecord(**record_fields(), context=make_context())

    dumped = record.model_dump(mode="json")

    for field in METADATA_FIELDS:
        assert field in dumped
    assert dumped["context"]["captured_at"].startswith("2026-04-05T06:07:08")


def test_an_unknown_version_is_still_rejected() -> None:
    data = CaptureRecord(**record_fields(), context=make_context()).model_dump(mode="json")

    with pytest.raises(ValidationError, match="schema_version"):
        CaptureRecord.model_validate(data | {"schema_version": "0.3"})


def test_the_record_never_holds_the_captured_content() -> None:
    """Metadata became durable; content did not. It lives in the raw store."""
    record = CaptureRecord(**record_fields(), context=make_context(), title="A note")

    content_fields = {"text", "html", "payload", "file_ref", "body"}
    assert not set(CaptureRecord.model_fields) & content_fields
    assert "payload" not in record.model_dump(mode="json")
