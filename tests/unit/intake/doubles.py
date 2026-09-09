"""Fake ports for the intake tests.

These exist to prove that intake talks to the *protocols* and to nothing
underneath them: they share no code with ``LocalRawObjectStore`` or
``SqliteCaptureRecordStore``, keep everything in dictionaries, and record what
happened so a test can assert both the order of the calls and — just as often —
that a call never happened.

Both stores write into one shared ``journal``, because the sequence that
matters spans them: the receipt must be durable before any byte is stored.
"""

import hashlib
import io
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import BinaryIO

from core.contracts import CaptureRecord, RawObjectRef
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
)
from core.storage import RawObjectNotFoundError, build_raw_ref
from core.storage.raw import ReadableBinaryStream


class FakeClock:
    """A clock that hands out prepared instants, one per call.

    Running past the end is an error rather than a repeat: a test that says
    "intake reads the clock twice" should fail if it starts reading three
    times.
    """

    def __init__(self, *instants: datetime) -> None:
        self._instants = list(instants)
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        if len(self.reads) >= len(self._instants):
            raise AssertionError("the clock was read more times than the test prepared for")
        instant = self._instants[len(self.reads)]
        self.reads.append(instant)
        return instant


class FakeRawObjectStore:
    """A ``RawObjectStore``-compatible store backed by a dictionary.

    Content addressing and exact deduplication are reproduced because intake
    depends on them; nothing else about a real backend is.
    """

    def __init__(self, journal: list[str] | None = None, *, fail_with: Exception | None = None):
        self.journal = journal if journal is not None else []
        self.writes: list[tuple[bytes, str | None]] = []
        self._objects: dict[str, bytes] = {}
        self._fail_with = fail_with

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        self.journal.append("raw.store_bytes")
        if self._fail_with is not None:
            raise self._fail_with
        self.writes.append((data, mime_type))
        digest = hashlib.sha256(data).hexdigest()
        reference = build_raw_ref(digest)
        self._objects[reference] = data
        return RawObjectRef(id=digest, mime_type=mime_type, sha256=digest, ref=reference)

    def store_stream(
        self, stream: ReadableBinaryStream, *, mime_type: str | None = None
    ) -> RawObjectRef:
        self.journal.append("raw.store_stream")
        chunks: list[bytes] = []
        while chunk := stream.read(4096):
            chunks.append(chunk)
        return self.store_bytes(b"".join(chunks), mime_type=mime_type)

    @contextmanager
    def open(self, raw_object: RawObjectRef) -> Iterator[BinaryIO]:
        self.journal.append("raw.open")
        yield io.BytesIO(self.read_bytes(raw_object))

    def read_bytes(self, raw_object: RawObjectRef) -> bytes:
        try:
            return self._objects[raw_object.ref or ""]
        except KeyError:
            raise RawObjectNotFoundError(f"raw object {raw_object.id!r} is not here") from None

    def exists(self, raw_object: RawObjectRef) -> bool:
        return (raw_object.ref or "") in self._objects


class FakeCaptureRecordStore:
    """A ``CaptureRecordStore``-compatible store backed by a dictionary.

    Snapshot semantics are reproduced through the contract's own JSON, so a
    record handed in cannot be changed afterwards by mutating the caller's
    object — the same guarantee the real adapter makes.
    """

    def __init__(
        self,
        journal: list[str] | None = None,
        *,
        fail_replace_with: Exception | None = None,
    ) -> None:
        self.journal = journal if journal is not None else []
        self.created: list[CaptureRecord] = []
        self.replaced: list[CaptureRecord] = []
        self._snapshots: dict[str, str] = {}
        self._fail_replace_with = fail_replace_with

    def create(self, record: CaptureRecord) -> None:
        self.journal.append("records.create")
        if record.id in self._snapshots:
            raise CaptureRecordAlreadyExistsError(f"capture record {record.id!r} already stored")
        self.created.append(record)
        self._snapshots[record.id] = record.model_dump_json()

    def get(self, capture_id: str) -> CaptureRecord:
        try:
            payload = self._snapshots[capture_id]
        except KeyError:
            raise CaptureRecordNotFoundError(
                f"capture record {capture_id!r} is not stored"
            ) from None
        return CaptureRecord.model_validate_json(payload)

    def replace(self, record: CaptureRecord) -> None:
        self.journal.append("records.replace")
        if self._fail_replace_with is not None:
            raise self._fail_replace_with
        if record.id not in self._snapshots:
            raise CaptureRecordNotFoundError(f"capture record {record.id!r} is not stored")
        self.replaced.append(record)
        self._snapshots[record.id] = record.model_dump_json()
