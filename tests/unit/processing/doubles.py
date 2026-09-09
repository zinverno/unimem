"""Test doubles for the processing layer.

The raw store double exists to prove a processor talks to the *port* and not to
a particular backend: it shares no code with ``LocalRawObjectStore``, keeps its
objects in a dict, and records every access so a test can assert that something
did *not* touch storage.

The orchestration doubles below add the other half. They all write into one
shared ``journal``, because in Phase 0H the thing under test is an *order* that
spans a record store, a router, and a processor: routing before any state is
written, the ``processing`` record durable before the processor runs, and
``complete`` durable before the caller is handed anything.
"""

import hashlib
import io
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import BinaryIO

from core.contracts import CaptureRecord, ContentObject, RawObjectRef
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
    ContentObjectAlreadyExistsError,
    ContentObjectNotFoundError,
)
from core.storage import RawObjectNotFoundError, build_raw_ref
from core.storage.raw import ReadableBinaryStream


class InMemoryRawObjectStore:
    """A ``RawObjectStore``-compatible store backed by a dictionary.

    Objects are keyed by their logical reference, so nothing here knows or
    cares how a real backend lays bytes out.
    """

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.accesses: list[str] = []

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        self.accesses.append("store_bytes")
        return self._put(data, mime_type)

    def store_stream(
        self, stream: ReadableBinaryStream, *, mime_type: str | None = None
    ) -> RawObjectRef:
        self.accesses.append("store_stream")
        chunks: list[bytes] = []
        while chunk := stream.read(4096):
            chunks.append(chunk)
        return self._put(b"".join(chunks), mime_type)

    @contextmanager
    def open(self, raw_object: RawObjectRef) -> Iterator[BinaryIO]:
        self.accesses.append("open")
        yield io.BytesIO(self._lookup(raw_object))

    def read_bytes(self, raw_object: RawObjectRef) -> bytes:
        self.accesses.append("read_bytes")
        return self._lookup(raw_object)

    def exists(self, raw_object: RawObjectRef) -> bool:
        self.accesses.append("exists")
        return raw_object.ref in self._objects

    def _put(self, data: bytes, mime_type: str | None) -> RawObjectRef:
        digest = hashlib.sha256(data).hexdigest()
        reference = build_raw_ref(digest)
        self._objects[reference] = data
        return RawObjectRef(id=digest, mime_type=mime_type, sha256=digest, ref=reference)

    def _lookup(self, raw_object: RawObjectRef) -> bytes:
        try:
            return self._objects[raw_object.ref or ""]
        except KeyError:
            raise RawObjectNotFoundError(
                f"raw object {raw_object.id!r} is not present in this store"
            ) from None


class StubProcessor:
    """A processor with a fixed answer to ``supports``, recording every call."""

    def __init__(
        self,
        name: str,
        *,
        supported: bool,
        version: str = "0.1",
        content: ContentObject | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self._supported = supported
        self._content = content
        self.supports_calls: list[str] = []
        self.process_calls: list[str] = []

    def supports(self, capture: CaptureRecord) -> bool:
        self.supports_calls.append(capture.id)
        return self._supported

    def process(self, capture: CaptureRecord) -> ContentObject:
        self.process_calls.append(capture.id)
        if self._content is None:
            raise AssertionError(f"processor {self.name!r} was not expected to run")
        return self._content


class FakeClock:
    """A clock that hands out prepared instants, one per call.

    Running past the end is an error rather than a repeat: this phase makes
    exact claims about how many times the orchestration clock is read, and a
    test that says "twice" should fail if it starts being read three times.
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


class RecordingCaptureRecordStore:
    """A ``CaptureRecordStore``-compatible store that journals and can fail.

    Snapshots go in as their own contract JSON, so what comes back out of
    ``get`` is a fresh object and a caller mutating one cannot reach the store
    — the same guarantee the real adapter makes.
    """

    def __init__(
        self,
        journal: list[str] | None = None,
        *,
        records: Iterable[CaptureRecord] = (),
        fail_replace_at: Mapping[int, Exception] | None = None,
    ) -> None:
        self.journal = journal if journal is not None else []
        self.snapshots: dict[str, str] = {record.id: record.model_dump_json() for record in records}
        self.replaced: list[CaptureRecord] = []
        self._fail_replace_at = dict(fail_replace_at or {})
        self._replace_calls = 0

    def create(self, record: CaptureRecord) -> None:
        self.journal.append("records.create")
        if record.id in self.snapshots:
            raise CaptureRecordAlreadyExistsError(f"capture record {record.id!r} already stored")
        self.snapshots[record.id] = record.model_dump_json()

    def get(self, capture_id: str) -> CaptureRecord:
        self.journal.append("records.get")
        try:
            payload = self.snapshots[capture_id]
        except KeyError:
            raise CaptureRecordNotFoundError(
                f"capture record {capture_id!r} is not stored"
            ) from None
        return CaptureRecord.model_validate_json(payload)

    def replace(self, record: CaptureRecord) -> None:
        self._replace_calls += 1
        self.journal.append(f"records.replace({record.status.value})")
        failure = self._fail_replace_at.get(self._replace_calls)
        if failure is not None:
            raise failure
        if record.id not in self.snapshots:
            raise CaptureRecordNotFoundError(f"capture record {record.id!r} is not stored")
        self.replaced.append(record)
        self.snapshots[record.id] = record.model_dump_json()

    def stored(self, capture_id: str) -> CaptureRecord:
        """Read a snapshot without journalling it, for assertions."""
        return CaptureRecord.model_validate_json(self.snapshots[capture_id])


class SpyProcessor:
    """A processor that records what it was handed and does what it was told.

    It is a ``Processor`` by structure, so the router and the orchestrator
    reach it through the same protocol a real one satisfies.
    """

    def __init__(
        self,
        *,
        name: str = "spy",
        version: str = "0.1",
        supported: bool = True,
        content: ContentObject | None = None,
        raises: Exception | None = None,
        mutate: Callable[[CaptureRecord], None] | None = None,
        journal: list[str] | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.journal = journal if journal is not None else []
        self.received: list[CaptureRecord] = []
        self._supported = supported
        self._content = content
        self._raises = raises
        self._mutate = mutate

    def supports(self, capture: CaptureRecord) -> bool:
        return self._supported

    def process(self, capture: CaptureRecord) -> ContentObject:
        self.journal.append("processor.process")
        self.received.append(capture)
        if self._mutate is not None:
            self._mutate(capture)
        if self._raises is not None:
            raise self._raises
        if self._content is None:
            raise AssertionError(f"processor {self.name!r} was not given content to return")
        return self._content


class RecordingContentObjectStore:
    """A ``ContentObjectStore``-compatible store that journals and can fail.

    Snapshots go in as their own contract JSON, so a caller mutating the object
    it handed over cannot reach the store — the same guarantee the real adapter
    makes. Both uniqueness rules are reproduced, because orchestration depends
    on the second one: one capture, one canonical object.
    """

    def __init__(
        self, journal: list[str] | None = None, *, fail_create_with: Exception | None = None
    ) -> None:
        self.journal = journal if journal is not None else []
        self.created: list[ContentObject] = []
        self._by_id: dict[str, str] = {}
        self._by_capture: dict[str, str] = {}
        self._fail_create_with = fail_create_with

    def create(self, content: ContentObject) -> None:
        self.journal.append("content.create")
        if self._fail_create_with is not None:
            raise self._fail_create_with
        capture_id = content.source.capture_id
        if content.id in self._by_id:
            raise ContentObjectAlreadyExistsError(
                f"content object {content.id!r} is already stored"
            )
        if capture_id in self._by_capture:
            raise ContentObjectAlreadyExistsError(
                f"capture {capture_id!r} already has a stored content object"
            )
        self.created.append(content)
        self._by_id[content.id] = content.model_dump_json()
        self._by_capture[capture_id] = content.id

    def get(self, content_id: str) -> ContentObject:
        try:
            payload = self._by_id[content_id]
        except KeyError:
            raise ContentObjectNotFoundError(
                f"content object {content_id!r} is not stored"
            ) from None
        return ContentObject.model_validate_json(payload)

    def get_for_capture(self, capture_id: str) -> ContentObject:
        try:
            content_id = self._by_capture[capture_id]
        except KeyError:
            raise ContentObjectNotFoundError(
                f"capture {capture_id!r} has no stored content object"
            ) from None
        return self.get(content_id)

    def stored_ids(self) -> list[str]:
        """Every stored content id, without journalling the read."""
        return sorted(self._by_id)
