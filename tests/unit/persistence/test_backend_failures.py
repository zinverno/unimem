"""Backend failures that are neither a duplicate nor a missing record.

A caller of the port should never see ``sqlite3.OperationalError``,
``sqlite3.DatabaseError``, or ``OSError``: those name a backend this layer
exists to hide. They become ``CaptureRecordPersistenceError``, which is
deliberately *not* what a duplicate id or a missing record becomes.
"""

import sqlite3
from pathlib import Path

import pytest

from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    SqliteCaptureRecordStore,
)
from tests.unit.persistence.builders import make_record


def drop_the_table(database: Path) -> None:
    """Remove the table under a live store, the cheapest real backend failure."""
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE capture_records")
    connection.close()


def test_an_unopenable_database_fails_at_construction(tmp_path: Path) -> None:
    """The parent directory is not created for you; the path is a decision."""
    with pytest.raises(CaptureRecordPersistenceError):
        SqliteCaptureRecordStore(tmp_path / "no" / "such" / "directory" / "captures.sqlite3")


def test_a_database_that_is_not_a_database_fails_at_construction(tmp_path: Path) -> None:
    not_a_database = tmp_path / "captures.sqlite3"
    not_a_database.write_bytes(b"this is not a SQLite file" * 64)

    with pytest.raises(CaptureRecordPersistenceError):
        SqliteCaptureRecordStore(not_a_database)


def test_a_failing_read_is_a_typed_persistence_error(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError):
        store.get("cap_persist_01")


def test_a_failing_create_is_a_typed_persistence_error(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError):
        store.create(make_record())


def test_a_failing_replace_is_a_typed_persistence_error(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    store.create(make_record())
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError):
        store.replace(make_record())


def test_a_backend_failure_is_not_reported_as_missing_or_duplicate(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    """The three outcomes stay distinguishable, which is the point of the types."""
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        store.replace(make_record())

    assert not isinstance(raised.value, CaptureRecordNotFoundError)
    assert not isinstance(raised.value, CaptureRecordAlreadyExistsError)


def test_a_duplicate_is_not_reported_as_a_backend_failure(
    store: SqliteCaptureRecordStore,
) -> None:
    """The expected outcome keeps its own type rather than being swallowed."""
    store.create(make_record())

    with pytest.raises(CaptureRecordAlreadyExistsError) as raised:
        store.create(make_record())

    assert not isinstance(raised.value, CaptureRecordPersistenceError)


def test_no_sqlite_exception_escapes_the_port(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        store.get("cap_persist_01")

    assert not isinstance(raised.value, sqlite3.Error)


def test_backend_failures_preserve_the_underlying_cause(
    store: SqliteCaptureRecordStore, database: Path
) -> None:
    drop_the_table(database)

    with pytest.raises(CaptureRecordPersistenceError) as raised:
        store.create(make_record())

    assert isinstance(raised.value.__cause__, sqlite3.Error)


def test_a_duplicate_preserves_the_underlying_cause(store: SqliteCaptureRecordStore) -> None:
    store.create(make_record())

    with pytest.raises(CaptureRecordAlreadyExistsError) as raised:
        store.create(make_record())

    assert isinstance(raised.value.__cause__, sqlite3.IntegrityError)
