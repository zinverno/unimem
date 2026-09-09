"""Test doubles for the processing layer.

The store double exists to prove a processor talks to the *port* and not to a
particular backend: it shares no code with ``LocalRawObjectStore``, keeps its
objects in a dict, and records every access so a test can assert that something
did *not* touch storage.
"""

import hashlib
import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from core.contracts import CaptureRecord, ContentObject, RawObjectRef
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
