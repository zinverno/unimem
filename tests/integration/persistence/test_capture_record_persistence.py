"""The SQLite store against a real database file.

These tests look inside the backend on purpose: the table, its columns, and the
text in them are implementation detail, and verifying them is exactly what an
integration test for this backend is for. Nothing here appears in the port, and
no production caller ever sees a table name.

They also cover the claim that makes this persistence rather than an in-process
cache: a second store object over the same file sees the same records, and a
record outlives every object that touched it.
"""

import sqlite3
from pathlib import Path

import pytest

from core.contracts import CaptureRecord, CaptureStatus
from core.persistence import (
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    SqliteCaptureRecordStore,
)
from tests.unit.persistence.builders import UPDATED_AT, make_raw_object, make_record


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "captures.sqlite3"


def rows(database: Path) -> list[tuple[str, str]]:
    """Every stored row, read without going through the store."""
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT id, payload FROM capture_records ORDER BY id"
        ).fetchall()
    connection.close()
    return [(str(key), str(payload)) for key, payload in stored]


def columns(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        described = connection.execute("PRAGMA table_info(capture_records)").fetchall()
    connection.close()
    return [str(column[1]) for column in described]


def test_the_database_file_is_created_on_construction(database: Path) -> None:
    assert not database.exists()

    SqliteCaptureRecordStore(database)

    assert database.is_file()


def test_the_schema_is_one_table_of_two_columns(database: Path) -> None:
    """A key and a payload. No status, timestamp, source, or digest column."""
    SqliteCaptureRecordStore(database)

    assert columns(database) == ["id", "payload"]


def test_the_stored_payload_is_the_contract_serialization(database: Path) -> None:
    """Not a rendering, not a hand-built field mapping — the record's own JSON."""
    store = SqliteCaptureRecordStore(database)
    record = make_record()

    store.create(record)

    assert rows(database) == [(record.id, record.model_dump_json())]


def test_the_stored_payload_re_validates_into_the_record(database: Path) -> None:
    store = SqliteCaptureRecordStore(database)
    record = make_record()
    store.create(record)

    ((_, payload),) = rows(database)

    assert CaptureRecord.model_validate_json(payload) == record


def test_constructing_twice_over_one_file_is_harmless(database: Path) -> None:
    """``CREATE TABLE IF NOT EXISTS`` — a second store does not reset the first."""
    first = SqliteCaptureRecordStore(database)
    first.create(make_record())

    second = SqliteCaptureRecordStore(database)

    assert second.get("cap_persist_01") == make_record()


def test_two_stores_over_one_file_see_the_same_records(database: Path) -> None:
    writer = SqliteCaptureRecordStore(database)
    reader = SqliteCaptureRecordStore(database)
    record = make_record()

    writer.create(record)

    assert reader.get(record.id) == record


def test_a_replace_through_one_store_is_visible_through_another(database: Path) -> None:
    writer = SqliteCaptureRecordStore(database)
    reader = SqliteCaptureRecordStore(database)
    writer.create(make_record(status=CaptureStatus.STORED))

    writer.replace(make_record(status=CaptureStatus.COMPLETE, updated_at=UPDATED_AT))

    restored = reader.get("cap_persist_01")
    assert restored.status is CaptureStatus.COMPLETE
    assert restored.updated_at == UPDATED_AT


def test_a_duplicate_is_refused_across_store_instances(database: Path) -> None:
    """The primary key is in the file, not in either object's memory."""
    first = SqliteCaptureRecordStore(database)
    second = SqliteCaptureRecordStore(database)
    first.create(make_record())

    with pytest.raises(Exception, match="already stored"):
        second.create(make_record(status=CaptureStatus.FAILED))

    assert first.get("cap_persist_01").status is CaptureStatus.STORED


def test_records_outlive_the_store_object(database: Path) -> None:
    """Nothing is held open, cached, or flushed on close: the write is committed."""
    record = make_record()
    SqliteCaptureRecordStore(database).create(record)

    assert SqliteCaptureRecordStore(database).get(record.id) == record


def test_a_write_is_committed_immediately(database: Path) -> None:
    """A separate connection sees the row without the store being disposed of."""
    store = SqliteCaptureRecordStore(database)

    store.create(make_record())

    assert len(rows(database)) == 1


def test_the_file_holds_many_records(database: Path) -> None:
    store = SqliteCaptureRecordStore(database)
    for index in range(25):
        store.create(make_record(id=f"cap_{index:02d}", raw_object=make_raw_object()))

    reopened = SqliteCaptureRecordStore(database)

    assert len(rows(database)) == 25
    assert reopened.get("cap_07").id == "cap_07"


def test_an_aborted_write_leaves_the_previous_snapshot_intact(database: Path) -> None:
    """A transaction that fails mid-statement rolls back; the old record stands.

    The trigger is a way to make a write fail deterministically. Something
    installing triggers on this store's table is outside its trust boundary —
    the point being asserted is the transaction guarantee, not the trigger.
    """
    store = SqliteCaptureRecordStore(database)
    original = make_record(status=CaptureStatus.STORED, error=None)
    store.create(original)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER refuse_update BEFORE UPDATE ON capture_records "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    connection.close()

    with pytest.raises(CaptureRecordPersistenceError):
        store.replace(make_record(status=CaptureStatus.FAILED, error="clobbered"))

    assert store.get(original.id) == original
    assert rows(database) == [(original.id, original.model_dump_json())]


def test_an_aborted_create_leaves_the_table_as_it_was(database: Path) -> None:
    store = SqliteCaptureRecordStore(database)
    store.create(make_record(id="cap_first"))
    # A constraint failure that is not a primary-key conflict is not a duplicate.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER refuse_insert BEFORE INSERT ON capture_records "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    connection.close()

    with pytest.raises(CaptureRecordPersistenceError):
        store.create(make_record(id="cap_second"))

    assert [key for key, _ in rows(database)] == ["cap_first"]
    with pytest.raises(CaptureRecordNotFoundError):
        store.get("cap_second")
