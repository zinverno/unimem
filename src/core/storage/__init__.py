"""Storage ports and backends.

Phase 0B provides one thing: immutable, content-addressed storage for raw
originals. Canonical contracts stay storage-neutral — nothing here leaks into
:mod:`core.contracts`.
"""

from core.storage.errors import (
    InvalidRawObjectRefError,
    RawObjectNotFoundError,
    RawObjectStoreError,
    RawObjectWriteError,
)
from core.storage.local import LocalRawObjectStore
from core.storage.raw import (
    RAW_REF_SCHEME,
    RawObjectStore,
    build_raw_ref,
    parse_raw_ref,
    raw_object_ref,
    resolve_digest,
)

__all__ = [
    "RAW_REF_SCHEME",
    "InvalidRawObjectRefError",
    "LocalRawObjectStore",
    "RawObjectNotFoundError",
    "RawObjectStore",
    "RawObjectStoreError",
    "RawObjectWriteError",
    "build_raw_ref",
    "parse_raw_ref",
    "raw_object_ref",
    "resolve_digest",
]
