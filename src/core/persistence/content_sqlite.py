"""A file-backed SQLite content object store.

Schema, in full::

    CREATE TABLE IF NOT EXISTS content_objects (
        id         TEXT PRIMARY KEY,
        capture_id TEXT NOT NULL UNIQUE,
        payload    TEXT NOT NULL
    )

``payload`` is the whole content object serialized through its own Pydantic
contract. The two key columns are the only things lifted out of it, and each
buys something the payload alone cannot give: ``id`` is how an object is
addressed, and ``capture_id`` is how a capture's canonical content is found —
and, being ``UNIQUE``, is what makes "one canonical object per capture" a rule
the database enforces rather than a convention orchestration hopes for.

Nothing else is duplicated out of the payload. No type, title, digest, or
timestamp column: a duplicated column is a second definition of the contract to
keep in step with the first, and nothing here queries or sorts on one.

This adapter owns only its own table. It may share a database file with
:class:`~core.persistence.sqlite.SqliteCaptureRecordStore`, which leaves
``capture_records`` untouched — but sharing a file is a deployment convenience,
never a shared transaction: the two are separate ports and nothing here reaches
across.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from core.contracts import ContentObject
from core.persistence.errors import (
    ContentObjectAlreadyExistsError,
    ContentObjectCorruptError,
    ContentObjectNotFoundError,
    ContentObjectPersistenceError,
    ContentObjectStoreError,
)

_TABLE: Final = "content_objects"

_CREATE_TABLE: Final = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id         TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE,
    payload    TEXT NOT NULL
)
"""

#: The content id is taken.
_PRIMARY_KEY_CONFLICT: Final = "SQLITE_CONSTRAINT_PRIMARYKEY"

#: The capture already has canonical content. ``capture_id`` is the only
#: ``UNIQUE`` column in this table, so this name identifies it unambiguously.
_UNIQUE_CONFLICT: Final = "SQLITE_CONSTRAINT_UNIQUE"

_INSERT: Final = f"INSERT INTO {_TABLE} (id, capture_id, payload) VALUES (?, ?, ?)"
_SELECT_BY_ID: Final = f"SELECT id, capture_id, payload FROM {_TABLE} WHERE id = ?"
_SELECT_BY_CAPTURE: Final = f"SELECT id, capture_id, payload FROM {_TABLE} WHERE capture_id = ?"


class SqliteContentObjectStore:
    """Stores canonical content objects in a SQLite file.

    Construction opens the database once to create the table if it is missing,
    so a fresh store can point at a file that does not exist yet. The parent
    directory is *not* created: a path into a directory that is not there is a
    configuration mistake, and it surfaces as
    ``ContentObjectPersistenceError`` rather than being silently repaired.

    Each operation opens its own connection and closes it before returning.
    Nothing is pooled, cached, or kept alive between calls, so the store owns
    no resource a caller has to release, holds no lock between operations, and
    is not bound to the thread that constructed it.

    Durability is whatever SQLite's default rollback journal gives: each write
    is one committed transaction. There is no WAL tuning, retry loop, pool, or
    cross-process coordination here beyond that.
    """

    def __init__(self, database: Path | str) -> None:
        self._database = Path(database)
        with self._connect() as connection:
            try:
                with connection:
                    connection.execute(_CREATE_TABLE)
            except sqlite3.Error as exc:
                raise self._failed("create its table") from exc

    def create(self, content: ContentObject) -> None:
        """Insert one canonical content object, failing if either key is taken.

        The object is serialized before the transaction opens, and the stored
        text is a copy: later mutation of the object that was handed in cannot
        reach the database.

        The two uniqueness rules are reported apart, because they mean
        different things — a taken content id is an id collision, while a
        taken capture id means this capture already normalized into something.
        Any *other* constraint failure came from something this store did not
        put in the table, and calling it a duplicate would be a lie.
        """
        capture_id = content.source.capture_id
        payload = content.model_dump_json()
        with self._connect() as connection:
            try:
                with connection:
                    connection.execute(_INSERT, (content.id, capture_id, payload))
            except sqlite3.IntegrityError as exc:
                raise self._duplicate(exc, content.id, capture_id) from exc
            except sqlite3.Error as exc:
                raise self._failed(f"store content object {content.id!r}") from exc

    def get(self, content_id: str) -> ContentObject:
        """Return a newly validated content object, addressed by its own id."""
        row = self._fetch(_SELECT_BY_ID, content_id, f"read content object {content_id!r}")
        if row is None:
            raise ContentObjectNotFoundError(f"content object {content_id!r} is not stored")
        return _decode(row)

    def get_for_capture(self, capture_id: str) -> ContentObject:
        """Return the canonical content stored for one capture."""
        row = self._fetch(
            _SELECT_BY_CAPTURE, capture_id, f"read the content object of capture {capture_id!r}"
        )
        if row is None:
            raise ContentObjectNotFoundError(f"capture {capture_id!r} has no stored content object")
        return _decode(row)

    def _fetch(self, statement: str, key: str, action: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            try:
                row: tuple[object, ...] | None = connection.execute(statement, (key,)).fetchone()
            except sqlite3.Error as exc:
                raise self._failed(action) from exc
        return row

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

    def _duplicate(
        self, exc: sqlite3.IntegrityError, content_id: str, capture_id: str
    ) -> ContentObjectStoreError:
        """Classify a constraint failure, naming which rule was hit.

        Either the expected duplicate — under whichever of the two uniqueness
        rules was violated — or, for a constraint this store did not write, the
        catch-all backend failure.
        """
        if exc.sqlite_errorname == _PRIMARY_KEY_CONFLICT:
            return ContentObjectAlreadyExistsError(
                f"content object {content_id!r} is already stored"
            )
        if exc.sqlite_errorname == _UNIQUE_CONFLICT:
            return ContentObjectAlreadyExistsError(
                f"capture {capture_id!r} already has a stored content object"
            )
        return self._failed(f"store content object {content_id!r}")

    def _failed(self, action: str) -> ContentObjectPersistenceError:
        """The one error every unexpected backend failure becomes."""
        return ContentObjectPersistenceError(
            f"the content object database at {self._database} could not {action}"
        )


def _decode(row: tuple[object, ...]) -> ContentObject:
    """Rebuild a content object from its row, or say why it cannot be.

    Everything that can go wrong — a non-text payload, malformed JSON, a
    payload that is not a valid content object, an unsupported
    ``schema_version``, an embedded id or capture id that disagrees with the
    columns the row was filed under — means the stored data does not honour
    this store's contract. All of it surfaces as one corruption error rather
    than as a Pydantic ``ValidationError`` leaking through the port.

    Both key columns are checked, not just the primary key. A row whose payload
    claims a different capture would otherwise let ``get_for_capture`` answer
    with content that belongs to someone else.
    """
    content_id, capture_id, payload = str(row[0]), str(row[1]), row[2]
    if not isinstance(payload, str):
        raise ContentObjectCorruptError(
            f"the snapshot stored for content object {content_id!r} is not text"
        )
    try:
        content = ContentObject.model_validate_json(payload)
    except ValidationError as exc:
        raise ContentObjectCorruptError(
            f"the snapshot stored for content object {content_id!r} is not a valid content object"
        ) from exc
    if content.id != content_id:
        raise ContentObjectCorruptError(
            f"the snapshot stored for content object {content_id!r} carries id {content.id!r}"
        )
    if content.source.capture_id != capture_id:
        raise ContentObjectCorruptError(
            f"content object {content_id!r} is filed under capture {capture_id!r} "
            f"but carries capture {content.source.capture_id!r}"
        )
    return content
