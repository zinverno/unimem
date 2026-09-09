"""Persistence: durably storing capture record snapshots.

Phase 0E answers one question — how is a valid ``CaptureRecord`` durably
stored and retrieved without coupling domain code to a database?

::

    CaptureRecord -> CaptureRecordStore (port) -> SqliteCaptureRecordStore -> SQLite file

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
listing, query, or filter — and capture identity stays the record's own opaque
``id``, never a raw object's SHA-256, so two captures of identical bytes remain
two records.
"""

from core.persistence.base import CaptureRecordStore
from core.persistence.errors import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordCorruptError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    CaptureRecordStoreError,
)
from core.persistence.sqlite import SqliteCaptureRecordStore

__all__ = [
    "CaptureRecordAlreadyExistsError",
    "CaptureRecordCorruptError",
    "CaptureRecordNotFoundError",
    "CaptureRecordPersistenceError",
    "CaptureRecordStore",
    "CaptureRecordStoreError",
    "SqliteCaptureRecordStore",
]
