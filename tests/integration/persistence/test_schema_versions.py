"""Old and new capture records in one real database.

The SQLite table stores whole ``CaptureRecord`` JSON in ``(id, payload)``, so
adding fields to the contract needs no DDL and no migration. What it does need
is proof: that a payload written by a 0.1 build still loads, that rewriting it
does not quietly turn it into something a 0.1 reader would reject, and that an
unsupported future version is still refused rather than guessed at.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from core.contracts import (
    CaptureContext,
    CaptureIntent,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    IntentAction,
)
from core.persistence import CaptureRecordCorruptError, SqliteCaptureRecordStore

CAPTURED_AT = datetime(2026, 4, 5, 6, 7, 8, 90000, tzinfo=UTC)
RECEIVED_AT = CAPTURED_AT + timedelta(minutes=30)

#: Exactly the document a 0.1 build wrote: no context, intent, or title.
LEGACY_PAYLOAD: dict[str, Any] = {
    "schema_version": "0.1",
    "id": "cap_legacy_01",
    "status": "stored",
    "received_at": "2026-04-05T06:37:08.090000Z",
    "updated_at": None,
    "source": {"type": "api", "provider": "cli", "url": None},
    "payload_type": "text",
    "raw_object": {
        "id": "e" * 64,
        "mime_type": "text/plain",
        "sha256": "e" * 64,
        "ref": "sha256:" + "e" * 64,
    },
    "error": None,
}


class LegacyReader(BaseModel):
    """A stand-in for a build that predates 0.2, extras forbidden as ever."""

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


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "captures.sqlite3"


@pytest.fixture
def store(database: Path) -> SqliteCaptureRecordStore:
    return SqliteCaptureRecordStore(database)


def plant(database: Path, capture_id: str, payload: str) -> None:
    """Write a payload straight into the table, as an older build would have."""
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO capture_records (id, payload) VALUES (?, ?)",
            (capture_id, payload),
        )
    connection.close()


def raw_payload(database: Path, capture_id: str) -> str:
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "SELECT payload FROM capture_records WHERE id = ?", (capture_id,)
        )
        (payload,) = cursor.fetchone()
    connection.close()
    return str(payload)


def make_current(**overrides: Any) -> CaptureRecord:
    fields: dict[str, Any] = {
        "id": "cap_current_01",
        "status": CaptureStatus.STORED,
        "received_at": RECEIVED_AT,
        "source": CaptureSource(type=CaptureSourceType.BROWSER, provider="chrome"),
        "payload_type": CapturePayloadType.TEXT,
        "context": CaptureContext(
            captured_at=CAPTURED_AT, device="laptop", application="browser-extension"
        ),
        "intent": CaptureIntent(
            action=IntentAction.ANALYZE, collection="reading", tags=["architecture", "你好"]
        ),
        "title": "A note — 🌍",
    }
    return CaptureRecord(**(fields | overrides))


def test_the_table_schema_did_not_change(store: SqliteCaptureRecordStore, database: Path) -> None:
    """Whole-record JSON absorbed the contract change; no column was added."""
    with sqlite3.connect(database) as connection:
        described = connection.execute("PRAGMA table_info(capture_records)").fetchall()
    connection.close()

    assert [str(column[1]) for column in described] == ["id", "payload"]


def test_metadata_survives_a_real_round_trip(store: SqliteCaptureRecordStore) -> None:
    record = make_current()
    store.create(record)

    restored = store.get(record.id)

    assert restored == record
    assert restored.context is not None
    assert restored.context.captured_at == CAPTURED_AT
    assert restored.context.device == "laptop"
    assert restored.intent is not None
    assert restored.intent.tags == ["architecture", "你好"]
    assert restored.title == "A note — 🌍"


def test_metadata_survives_a_separate_store_instance(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    record = make_current()
    store.create(record)

    assert SqliteCaptureRecordStore(database).get(record.id) == record


def test_a_legacy_payload_still_loads(store: SqliteCaptureRecordStore, database: Path) -> None:
    plant(database, "cap_legacy_01", json.dumps(LEGACY_PAYLOAD))

    legacy = store.get("cap_legacy_01")

    assert legacy.schema_version == "0.1"
    assert legacy.context is None
    assert legacy.intent is None
    assert legacy.title is None
    assert legacy.source.provider == "cli"


def test_replacing_a_legacy_record_keeps_it_readable_by_a_legacy_reader(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """The compatibility guarantee that actually matters, through the real path."""
    plant(database, "cap_legacy_01", json.dumps(LEGACY_PAYLOAD))
    legacy = store.get("cap_legacy_01")

    store.replace(legacy)

    written = raw_payload(database, "cap_legacy_01")
    for field in ("context", "intent", "title"):
        assert field not in json.loads(written)
    assert LegacyReader.model_validate_json(written).schema_version == "0.1"


def test_a_rewritten_legacy_record_is_byte_identical_in_shape(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_legacy_01", json.dumps(LEGACY_PAYLOAD))

    store.replace(store.get("cap_legacy_01"))

    assert json.loads(raw_payload(database, "cap_legacy_01")) == LEGACY_PAYLOAD


def test_legacy_and_current_records_coexist(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_legacy_01", json.dumps(LEGACY_PAYLOAD))
    current = make_current()
    store.create(current)

    assert store.get("cap_legacy_01").schema_version == "0.1"
    assert store.get(current.id).schema_version == "0.2"


def test_a_legacy_payload_claiming_new_fields_is_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """A 0.1 document with 0.2 data is wrong about its own shape."""
    plant(
        database,
        "cap_liar",
        json.dumps(LEGACY_PAYLOAD | {"id": "cap_liar", "title": "smuggled"}),
    )

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_liar")


def test_a_current_payload_without_context_is_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(
        database,
        "cap_no_context",
        json.dumps(LEGACY_PAYLOAD | {"id": "cap_no_context", "schema_version": "0.2"}),
    )

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_no_context")


def test_an_unsupported_future_version_is_still_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """0.2 exists now; 0.3 does not, and is refused rather than guessed at."""
    payload = json.loads(make_current(id="cap_future").model_dump_json())
    plant(database, "cap_future", json.dumps(payload | {"schema_version": "0.3"}))

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_future")
