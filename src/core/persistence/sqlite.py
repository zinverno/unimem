"""A file-backed SQLite capture record store.

Schema, in full::

    CREATE TABLE IF NOT EXISTS capture_records (
        id      TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    )

Two columns and nothing else. ``payload`` is the whole record serialized
through its own Pydantic contract, and ``id`` is the primary key only so that
SQLite enforces "one snapshot per capture id" for us. No status, timestamp,
source, or digest column is duplicated out of the payload: a duplicated column
is a second definition of the contract that has to be kept in step with the
first, and nothing in this phase queries or sorts, so nothing would use one.

The database file is a backend detail. It is passed to this adapter and never
appears in a domain contract.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from core.contracts import CaptureRecord
from core.persistence.errors import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordCorruptError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
)

_TABLE: Final = "capture_records"

_CREATE_TABLE: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id      TEXT PRIMARY KEY,
    payload TEXT NOT NULL
)
"""

#: The one constraint failure that means "this capture id is taken". Any other
#: constraint failure came from something this store did not put in the table,
#: and calling it a duplicate would be a lie.
_PRIMARY_KEY_CONFLICT: Final = "SQLITE_CONSTRAINT_PRIMARYKEY"

_INSERT: Final = f"INSERT INTO {_TABLE} (id, payload) VALUES (?, ?)"
_SELECT: Final = f"SELECT payload FROM {_TABLE} WHERE id = ?"
_UPDATE: Final = f"UPDATE {_TABLE} SET payload = ? WHERE id = ?"


class SqliteCaptureRecordStore:
    """Stores capture record snapshots in a SQLite file.

    Construction opens the database once to create the table if it is missing,
    so a fresh store can point at a file that does not exist yet. The parent
    directory is *not* created: a path into a directory that is not there is a
    configuration mistake, and it surfaces as
    ``CaptureRecordPersistenceError`` rather than being silently repaired.

    Each operation opens its own connection and closes it before returning.
    Nothing is pooled, cached, or kept alive between calls, so the store owns
    no resource a caller has to release, holds no lock between operations, and
    is not bound to the thread that constructed it. Two instances pointing at
    one file are therefore interchangeable and see the same records — which is
    what makes this a database rather than an in-process cache with a file
    behind it.

    Durability is whatever SQLite's default rollback journal gives: each write
    is one committed transaction, and a failed write leaves the previous
    snapshot intact. There is no WAL tuning, retry loop, pool, or cross-process
    coordination here beyond that.
    """

    def __init__(self, database: Path | str) -> None:
        self._database = Path(database)
        with self._connect() as connection:
            try:
                with connection:
                    connection.execute(_CREATE_TABLE)
            except sqlite3.Error as exc:
                raise self._failed("create its table") from exc

    def create(self, record: CaptureRecord) -> None:
        """Insert one snapshot, failing if the id is taken.

        The record is serialized before the transaction opens, and the stored
        text is a copy: later mutation of the object that was handed in cannot
        reach the database.

        A primary-key conflict — and only a primary-key conflict — is the
        duplicate id. Any other constraint failure is a backend failure, so
        the expected outcome and the unexpected one stay distinguishable in
        both directions.
        """
        payload = record.model_dump_json()
        with self._connect() as connection:
            try:
                with connection:
                    connection.execute(_INSERT, (record.id, payload))
            except sqlite3.IntegrityError as exc:
                if exc.sqlite_errorname != _PRIMARY_KEY_CONFLICT:
                    raise self._failed(f"store capture record {record.id!r}") from exc
                raise CaptureRecordAlreadyExistsError(
                    f"capture record {record.id!r} is already stored"
                ) from exc
            except sqlite3.Error as exc:
                raise self._failed(f"store capture record {record.id!r}") from exc

    def get(self, capture_id: str) -> CaptureRecord:
        """Return a newly validated record reconstructed from its snapshot."""
        with self._connect() as connection:
            try:
                row: tuple[object, ...] | None = connection.execute(
                    _SELECT, (capture_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise self._failed(f"read capture record {capture_id!r}") from exc
        if row is None:
            raise CaptureRecordNotFoundError(f"capture record {capture_id!r} is not stored")
        return _decode(capture_id, row[0])

    def replace(self, record: CaptureRecord) -> None:
        """Overwrite the snapshot stored under ``record.id``.

        One ``UPDATE`` matching one primary key: the old snapshot is either
        wholly replaced or wholly untouched. A statement matching no row
        changes nothing and raises, so replace can never create.
        """
        payload = record.model_dump_json()
        with self._connect() as connection:
            try:
                with connection:
                    replaced = connection.execute(_UPDATE, (payload, record.id)).rowcount
            except sqlite3.Error as exc:
                raise self._failed(f"replace capture record {record.id!r}") from exc
            if replaced == 0:
                raise CaptureRecordNotFoundError(
                    f"capture record {record.id!r} is not stored, so it cannot be replaced"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection for one operation and close it afterwards."""
        try:
            connection = sqlite3.connect(self._database)
        except sqlite3.Error as exc:
            raise self._failed("be opened") from exc
        try:
            yield connection
        finally:
            connection.close()

    def _failed(self, action: str) -> CaptureRecordPersistenceError:
        """The one error every unexpected backend failure becomes."""
        return CaptureRecordPersistenceError(
            f"the capture record database at {self._database} could not {action}"
        )


def _decode(capture_id: str, payload: object) -> CaptureRecord:
    """Rebuild a record from its stored payload, or say why it cannot be.

    Everything that can go wrong here — a non-text column value, malformed
    JSON, a payload that is not a valid record, an unsupported
    ``schema_version``, an embedded id that disagrees with the key it was
    filed under — means the stored data does not honour this store's contract.
    All of it surfaces as one corruption error rather than as a Pydantic
    ``ValidationError`` leaking through the port.
    """
    if not isinstance(payload, str):
        raise CaptureRecordCorruptError(
            f"the snapshot stored for capture record {capture_id!r} is not text"
        )
    try:
        record = CaptureRecord.model_validate_json(payload)
    except ValidationError as exc:
        raise CaptureRecordCorruptError(
            f"the snapshot stored for capture record {capture_id!r} is not a valid capture record"
        ) from exc
    if record.id != capture_id:
        raise CaptureRecordCorruptError(
            f"the snapshot stored for capture record {capture_id!r} carries id {record.id!r}"
        )
    return record
