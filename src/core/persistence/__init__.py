"""Persistence: durably storing capture records and canonical content.

Phase 0E answered one question — how is a valid ``CaptureRecord`` durably
stored and retrieved without coupling domain code to a database? Phase 0I asks
the same question of the other thing worth keeping, the canonical
``ContentObject``, and answers it the same way.

::

    CaptureRecord -> CaptureRecordStore (port) -> SqliteCaptureRecordStore -> SQLite file
    ContentObject -> ContentObjectStore (port) -> SqliteContentObjectStore -> SQLite file

Two ports, two tables, two error hierarchies — deliberately siblings rather
than one store with more methods. A capture record is a mutable lifecycle
snapshot replaced as a capture advances; a content object is written once and
never rewritten. Their failures mean different things to a caller, and merging
them would hide that.

The port is three synchronous methods over whole validated snapshots.
``create`` and ``replace`` are separate on purpose: there is no ``save`` or
``upsert`` that guesses which one a caller meant. A store persists exactly what
it is handed — it mints no ids, sets no timestamps, chooses no status, and
enforces no lifecycle rule, because whether a capture may move from ``stored``
to ``processing`` is orchestration's decision and orchestration does not exist
yet.

Persistence is snapshot-based, not live-object tracking: what is stored is a
serialized copy, and what ``get`` returns is a newly validated object. There is
no session, no dirty tracking, and no unit of work. Nor is there a delete,
listing, query, or filter on either port — and identity stays layered: a
capture record is keyed by its own opaque ``id`` and a content object by its
own, never by a raw object's SHA-256, so two captures of identical bytes remain
two records with two content objects.

Canonical content additionally carries the id of the capture it normalized,
under a uniqueness rule: one capture has at most one stored content object. It
has no ``replace``, because the system produces one normalization result per
capture and superseding it is a decision reprocessing will have to make
explicitly.

Sharing one database file between the two SQLite adapters is a deployment
convenience and never a shared transaction. Neither reaches into the other, and
nothing here exposes a connection or a unit of work to a caller.
"""

from core.persistence.base import CaptureRecordStore
from core.persistence.content_base import ContentObjectStore
from core.persistence.content_sqlite import SqliteContentObjectStore
from core.persistence.errors import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordCorruptError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    CaptureRecordStoreError,
    ContentObjectAlreadyExistsError,
    ContentObjectCorruptError,
    ContentObjectNotFoundError,
    ContentObjectPersistenceError,
    ContentObjectStoreError,
)
from core.persistence.sqlite import SqliteCaptureRecordStore

__all__ = [
    "CaptureRecordAlreadyExistsError",
    "CaptureRecordCorruptError",
    "CaptureRecordNotFoundError",
    "CaptureRecordPersistenceError",
    "CaptureRecordStore",
    "CaptureRecordStoreError",
    "ContentObjectAlreadyExistsError",
    "ContentObjectCorruptError",
    "ContentObjectNotFoundError",
    "ContentObjectPersistenceError",
    "ContentObjectStore",
    "ContentObjectStoreError",
    "SqliteCaptureRecordStore",
    "SqliteContentObjectStore",
]
