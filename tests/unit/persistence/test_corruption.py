"""What happens when the stored payload is not a capture record.

The store owns its table, so these situations are outside its trust boundary —
something else wrote into the database, or a payload was written by a build
whose contracts have since moved on. The requirement is not that the store
repair any of it, but that it says so in its own vocabulary: a caller must
never have to catch a Pydantic ``ValidationError`` to find out that a row is
unreadable.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.persistence import (
    CaptureRecordCorruptError,
    CaptureRecordStoreError,
    SqliteCaptureRecordStore,
)
from tests.unit.persistence.builders import make_record


def plant(database: Path, capture_id: str, payload: object) -> None:
    """Write a payload straight into the table, bypassing the store."""
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO capture_records (id, payload) VALUES (?, ?)",
            (capture_id, payload),
        )
    connection.close()


def test_invalid_json_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_broken", "{not json at all")

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_broken")


def test_empty_payload_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_empty", "")

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_empty")


def test_valid_json_that_is_not_a_capture_record_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_shapeless", json.dumps({"id": "cap_shapeless"}))

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_shapeless")


def test_json_that_is_not_even_an_object_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    plant(database, "cap_list", json.dumps([1, 2, 3]))

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_list")


def test_an_unsupported_schema_version_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """A payload from a future contract is refused, not read with today's rules."""
    future = json.loads(make_record(id="cap_future").model_dump_json())
    future["schema_version"] = "0.2"
    plant(database, "cap_future", json.dumps(future))

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_future")


def test_an_unknown_field_in_the_payload_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """``extra="forbid"`` reaches the database boundary too."""
    extended = json.loads(make_record(id="cap_extra").model_dump_json())
    extended["summary"] = "derived data does not belong here"
    plant(database, "cap_extra", json.dumps(extended))

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_extra")


def test_a_key_that_disagrees_with_the_embedded_id_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """A valid record filed under the wrong key is not silently handed back."""
    plant(database, "cap_key", make_record(id="cap_other").model_dump_json())

    with pytest.raises(CaptureRecordCorruptError, match="cap_other"):
        store.get("cap_key")


def test_a_non_text_payload_is_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """SQLite's TEXT affinity leaves a blob a blob, so the column can hold one."""
    plant(database, "cap_blob", b"\x00\x01\x02not text")

    with pytest.raises(CaptureRecordCorruptError, match="not text"):
        store.get("cap_blob")


def test_a_blob_holding_valid_json_is_still_a_typed_corruption(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """The payload this store writes is text; anything else was not written by it."""
    plant(database, "cap_json_blob", make_record(id="cap_json_blob").model_dump_json().encode())

    with pytest.raises(CaptureRecordCorruptError, match="not text"):
        store.get("cap_json_blob")


def test_corruption_is_a_store_error_and_not_a_validation_error(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """Pydantic does not leak across the port, in type or in identity."""
    plant(database, "cap_broken", "{not json at all")

    with pytest.raises(CaptureRecordStoreError) as raised:
        store.get("cap_broken")

    assert not isinstance(raised.value, ValidationError)
    assert not isinstance(raised.value, sqlite3.Error)


def test_corruption_preserves_the_underlying_cause(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """The original error stays reachable for a human debugging the database."""
    plant(database, "cap_broken", "{not json at all")

    with pytest.raises(CaptureRecordCorruptError) as raised:
        store.get("cap_broken")

    assert isinstance(raised.value.__cause__, ValidationError)


def test_a_corrupt_row_does_not_stop_the_others_being_read(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    good = make_record(id="cap_good")
    store.create(good)
    plant(database, "cap_bad", "{not json at all")

    with pytest.raises(CaptureRecordCorruptError):
        store.get("cap_bad")

    assert store.get("cap_good") == good


def test_a_corrupt_row_can_be_replaced(store: SqliteCaptureRecordStore, database: Path) -> None:
    """Replace overwrites the payload without reading it first."""
    plant(database, "cap_persist_01", "{not json at all")
    repaired = make_record()

    store.replace(repaired)

    assert store.get(repaired.id) == repaired
