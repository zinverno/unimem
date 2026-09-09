"""What happens when a content row is not what this store wrote.

The store owns its table, so these situations are outside its trust boundary —
something else wrote into the database, or a row was written by a build whose
contracts have since moved on. The requirement is not that the store repair any
of it, but that it says so in its own vocabulary: a caller must never have to
catch a Pydantic ``ValidationError`` or an ``sqlite3`` exception to find out
that a row is unreadable.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.persistence import (
    ContentObjectCorruptError,
    ContentObjectNotFoundError,
    ContentObjectPersistenceError,
    ContentObjectStoreError,
    SqliteContentObjectStore,
)
from tests.unit.persistence.builders import make_content


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "content.sqlite3"


@pytest.fixture
def store(database: Path) -> SqliteContentObjectStore:
    return SqliteContentObjectStore(database)


def plant(database: Path, content_id: str, capture_id: str, payload: object) -> None:
    """Write a row straight into the table, bypassing the store."""
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO content_objects (id, capture_id, payload) VALUES (?, ?, ?)",
            (content_id, capture_id, payload),
        )
    connection.close()


def drop_the_table(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE content_objects")
    connection.close()


def test_invalid_json_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    plant(database, "con_broken", "cap_broken", "{not json at all")

    with pytest.raises(ContentObjectCorruptError):
        store.get("con_broken")


def test_a_non_text_payload_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """SQLite's TEXT affinity leaves a blob a blob, so the column can hold one."""
    plant(database, "con_blob", "cap_blob", b"\x00\x01\x02not text")

    with pytest.raises(ContentObjectCorruptError, match="not text"):
        store.get("con_blob")


def test_valid_json_that_is_not_a_content_object_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    plant(database, "con_shapeless", "cap_shapeless", json.dumps({"id": "con_shapeless"}))

    with pytest.raises(ContentObjectCorruptError):
        store.get("con_shapeless")


def test_an_unsupported_schema_version_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """A payload from a future contract is refused, not read with today's rules."""
    future = json.loads(make_content("con_future", "cap_future").model_dump_json())
    plant(database, "con_future", "cap_future", json.dumps(future | {"schema_version": "0.3"}))

    with pytest.raises(ContentObjectCorruptError):
        store.get("con_future")


def test_a_content_id_that_disagrees_with_the_row_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    plant(database, "con_key", "cap_key", make_content("con_other", "cap_key").model_dump_json())

    with pytest.raises(ContentObjectCorruptError, match="con_other"):
        store.get("con_key")


def test_a_capture_id_that_disagrees_with_the_row_is_a_typed_corruption(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """Otherwise ``get_for_capture`` could answer with someone else's content."""
    plant(
        database, "con_key", "cap_filed", make_content("con_key", "cap_embedded").model_dump_json()
    )

    with pytest.raises(ContentObjectCorruptError, match="cap_embedded"):
        store.get("con_key")


def test_the_capture_lookup_checks_both_keys_too(
    store: SqliteContentObjectStore, database: Path
) -> None:
    plant(
        database, "con_key", "cap_filed", make_content("con_key", "cap_embedded").model_dump_json()
    )

    with pytest.raises(ContentObjectCorruptError):
        store.get_for_capture("cap_filed")


def test_corruption_is_a_store_error_and_not_a_validation_error(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """Pydantic does not leak across the port, in type or in identity."""
    plant(database, "con_broken", "cap_broken", "{not json at all")

    with pytest.raises(ContentObjectStoreError) as raised:
        store.get("con_broken")

    assert not isinstance(raised.value, ValidationError)
    assert not isinstance(raised.value, sqlite3.Error)


def test_corruption_preserves_the_underlying_cause(
    store: SqliteContentObjectStore, database: Path
) -> None:
    plant(database, "con_broken", "cap_broken", "{not json at all")

    with pytest.raises(ContentObjectCorruptError) as raised:
        store.get("con_broken")

    assert isinstance(raised.value.__cause__, ValidationError)


def test_a_corrupt_row_does_not_stop_the_others_being_read(
    store: SqliteContentObjectStore, database: Path
) -> None:
    good = make_content("con_good", "cap_good")
    store.create(good)
    plant(database, "con_bad", "cap_bad", "{not json at all")

    with pytest.raises(ContentObjectCorruptError):
        store.get("con_bad")

    assert store.get("con_good") == good


def test_an_unopenable_database_fails_at_construction(tmp_path: Path) -> None:
    """The parent directory is not created for you; the path is a decision."""
    with pytest.raises(ContentObjectPersistenceError):
        SqliteContentObjectStore(tmp_path / "no" / "such" / "directory" / "content.sqlite3")


def test_a_database_that_is_not_a_database_fails_at_construction(tmp_path: Path) -> None:
    not_a_database = tmp_path / "content.sqlite3"
    not_a_database.write_bytes(b"this is not a SQLite file" * 64)

    with pytest.raises(ContentObjectPersistenceError):
        SqliteContentObjectStore(not_a_database)


def test_a_failing_write_is_a_typed_persistence_error(
    store: SqliteContentObjectStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(ContentObjectPersistenceError) as raised:
        store.create(make_content())

    assert isinstance(raised.value.__cause__, sqlite3.Error)


def test_a_failing_read_is_a_typed_persistence_error(
    store: SqliteContentObjectStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(ContentObjectPersistenceError) as raised:
        store.get("con_persist_01")

    assert isinstance(raised.value.__cause__, sqlite3.Error)


def test_a_failing_capture_lookup_is_a_typed_persistence_error(
    store: SqliteContentObjectStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(ContentObjectPersistenceError):
        store.get_for_capture("cap_persist_01")


def test_a_backend_failure_is_not_reported_as_missing_or_duplicate(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """The three outcomes stay distinguishable, which is the point of the types."""
    drop_the_table(database)

    with pytest.raises(ContentObjectPersistenceError) as raised:
        store.get("con_persist_01")

    assert not isinstance(raised.value, ContentObjectNotFoundError)
    assert not isinstance(raised.value, ContentObjectCorruptError)


def test_an_unrelated_constraint_failure_is_not_called_a_duplicate(
    store: SqliteContentObjectStore, database: Path
) -> None:
    """Only the two uniqueness rules mean "already exists"; a trigger does not.

    Something installing triggers on this store's table is outside its trust
    boundary — the point being asserted is that constraint classification is
    exact rather than "any IntegrityError is a duplicate".
    """
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER refuse_insert BEFORE INSERT ON content_objects "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    connection.close()

    with pytest.raises(ContentObjectPersistenceError) as raised:
        store.create(make_content())

    assert isinstance(raised.value.__cause__, sqlite3.IntegrityError)


def test_no_sqlite_exception_escapes_the_port(
    store: SqliteContentObjectStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(ContentObjectPersistenceError) as raised:
        store.create(make_content())

    assert not isinstance(raised.value, sqlite3.Error)
