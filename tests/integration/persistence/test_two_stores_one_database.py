"""Both persistence adapters against one real database file.

Sharing a file is a deployment convenience: each adapter owns its own table,
neither touches the other's, and nothing opens a transaction across them. These
tests look inside the database on purpose — the tables are implementation
detail, and verifying them is exactly what an integration test for these
backends is for.
"""

import sqlite3
from pathlib import Path

import pytest

from core.persistence import (
    ContentObjectNotFoundError,
    SqliteCaptureRecordStore,
    SqliteContentObjectStore,
)
from tests.unit.persistence.builders import make_content, make_record


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "unimem.sqlite3"


def columns(database: Path, table: str) -> list[tuple[str, str, int]]:
    """Each column as (name, declared type, not-null flag)."""
    with sqlite3.connect(database) as connection:
        described = connection.execute(f"PRAGMA table_info({table})").fetchall()
    connection.close()
    return [(str(row[1]), str(row[2]), int(row[3])) for row in described]


def indexes(database: Path, table: str) -> list[tuple[str, int]]:
    """Each index as (origin, unique flag) — origin ``u`` is a UNIQUE column."""
    with sqlite3.connect(database) as connection:
        described = connection.execute(f"PRAGMA index_list({table})").fetchall()
    connection.close()
    return [(str(row[3]), int(row[2])) for row in described]


def tables(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        found = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    connection.close()
    return [str(row[0]) for row in found]


def test_the_content_table_has_exactly_its_intended_shape(database: Path) -> None:
    SqliteContentObjectStore(database)

    assert columns(database, "content_objects") == [
        ("id", "TEXT", 0),
        ("capture_id", "TEXT", 1),
        ("payload", "TEXT", 1),
    ]


def test_the_capture_id_column_is_unique(database: Path) -> None:
    """One canonical object per capture, enforced by the database."""
    SqliteContentObjectStore(database)

    assert ("u", 1) in indexes(database, "content_objects")


def test_the_capture_record_table_is_unchanged(database: Path) -> None:
    """Phase 0I adds a table; it does not migrate the existing one."""
    SqliteCaptureRecordStore(database)
    SqliteContentObjectStore(database)

    assert columns(database, "capture_records") == [
        ("id", "TEXT", 0),
        ("payload", "TEXT", 1),
    ]


def test_the_two_stores_create_only_their_own_tables(database: Path) -> None:
    SqliteCaptureRecordStore(database)
    SqliteContentObjectStore(database)

    assert [name for name in tables(database) if not name.startswith("sqlite_")] == [
        "capture_records",
        "content_objects",
    ]


def test_either_store_may_be_constructed_first(database: Path) -> None:
    SqliteContentObjectStore(database)
    SqliteCaptureRecordStore(database)

    assert "capture_records" in tables(database)
    assert "content_objects" in tables(database)


def test_records_and_content_coexist_in_one_file(database: Path) -> None:
    record = make_record(id="cap_persist_01")
    content = make_content("con_persist_01", "cap_persist_01")

    SqliteCaptureRecordStore(database).create(record)
    SqliteContentObjectStore(database).create(content)

    assert SqliteCaptureRecordStore(database).get("cap_persist_01") == record
    assert SqliteContentObjectStore(database).get("con_persist_01") == content


def test_content_outlives_the_store_object(database: Path) -> None:
    """Nothing is held open or cached: the write is committed."""
    content = make_content()
    SqliteContentObjectStore(database).create(content)

    assert SqliteContentObjectStore(database).get(content.id) == content


def test_two_content_store_instances_see_the_same_objects(database: Path) -> None:
    writer = SqliteContentObjectStore(database)
    reader = SqliteContentObjectStore(database)
    content = make_content()

    writer.create(content)

    assert reader.get_for_capture(content.source.capture_id) == content


def test_the_capture_uniqueness_rule_holds_across_instances(database: Path) -> None:
    """The constraint is in the file, not in either object's memory."""
    SqliteContentObjectStore(database).create(make_content("con_a", "cap_shared"))

    with pytest.raises(Exception, match="already has a stored content object"):
        SqliteContentObjectStore(database).create(make_content("con_b", "cap_shared"))

    assert SqliteContentObjectStore(database).get_for_capture("cap_shared").id == "con_a"


def test_a_capture_record_without_content_is_simply_not_found(database: Path) -> None:
    """A stored capture that has not been processed has no canonical content."""
    SqliteCaptureRecordStore(database).create(make_record(id="cap_unprocessed"))

    with pytest.raises(ContentObjectNotFoundError):
        SqliteContentObjectStore(database).get_for_capture("cap_unprocessed")
