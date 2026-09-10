"""Test doubles for the API tests.

Everything here implements a ``core`` port — ``RawObjectStore``,
``CaptureRecordStore``, ``ContentObjectStore``, ``Processor`` — and nothing
implements an HTTP concept. That is deliberate: ``create_app`` is handed real
:class:`~core.intake.CaptureIntake` and
:class:`~core.processing.ProcessingOrchestrator` instances built over *these*,
so every test runs the genuine orchestration and only the backends are fake.
Faking an orchestrator would test that the routes call a mock; faking the stores
tests that the routes call the system.

The failures are injected at the two places a real deployment fails: a store
that cannot serve, and a processor that cannot normalize.
"""

import hashlib
import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from core.contracts import (
    CaptureRecord,
    ContentObject,
    RawObjectRef,
)
from core.contracts.enums import CapturePayloadType
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
    ContentObjectAlreadyExistsError,
    ContentObjectNotFoundError,
)
from core.processing import Processor
from core.storage import RawObjectNotFoundError, build_raw_ref
from core.storage.raw import ReadableBinaryStream


class FakeRawObjectStore:
    """A dictionary-backed ``RawObjectStore``, content-addressed like the real one.

    ``fail_with`` makes every write raise, which is how a raw-store outage is
    reproduced without breaking a filesystem.
    """

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self._objects: dict[str, bytes] = {}
        self._fail_with = fail_with
        #: Every accepted write, in order. Counting writes is how "this request
        #: stored nothing" is checked: content addressing makes a second write
        #: of identical bytes invisible in ``_objects``, so the dictionary alone
        #: cannot tell a replay from a re-store.
        self.writes: list[bytes] = []

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        if self._fail_with is not None:
            raise self._fail_with
        self.writes.append(data)
        digest = hashlib.sha256(data).hexdigest()
        reference = build_raw_ref(digest)
        self._objects[reference] = data
        return RawObjectRef(id=digest, mime_type=mime_type, sha256=digest, ref=reference)

    def store_stream(
        self, stream: ReadableBinaryStream, *, mime_type: str | None = None
    ) -> RawObjectRef:
        chunks: list[bytes] = []
        while chunk := stream.read(4096):
            chunks.append(chunk)
        return self.store_bytes(b"".join(chunks), mime_type=mime_type)

    @contextmanager
    def open(self, raw_object: RawObjectRef) -> Iterator[BinaryIO]:
        yield io.BytesIO(self.read_bytes(raw_object))

    def read_bytes(self, raw_object: RawObjectRef) -> bytes:
        try:
            return self._objects[raw_object.ref or ""]
        except KeyError:
            raise RawObjectNotFoundError(f"raw object {raw_object.id!r} is not here") from None

    def exists(self, raw_object: RawObjectRef) -> bool:
        return (raw_object.ref or "") in self._objects


class FakeCaptureRecordStore:
    """A dictionary-backed ``CaptureRecordStore`` with injectable failures.

    Snapshots are held as the contract's own JSON, so a record handed in cannot
    change afterwards through the caller's object — the guarantee the real
    adapter makes.

    ``fail_replace_with`` fires on ``replace`` only, which is where a real outage
    strands a capture in a truthful non-terminal state: intake's receipt is
    already durable, so the capture stays ``received`` and nothing terminal is
    invented.
    """

    def __init__(
        self,
        *,
        fail_replace_with: Exception | None = None,
        fail_get_with: Exception | None = None,
    ) -> None:
        self._snapshots: dict[str, str] = {}
        self._fail_replace_with = fail_replace_with
        self._fail_get_with = fail_get_with

    def create(self, record: CaptureRecord) -> None:
        if record.id in self._snapshots:
            raise CaptureRecordAlreadyExistsError(f"capture record {record.id!r} is already stored")
        self._snapshots[record.id] = record.model_dump_json()

    def get(self, capture_id: str) -> CaptureRecord:
        if self._fail_get_with is not None:
            raise self._fail_get_with
        try:
            payload = self._snapshots[capture_id]
        except KeyError:
            raise CaptureRecordNotFoundError(
                f"capture record {capture_id!r} is not stored"
            ) from None
        return CaptureRecord.model_validate_json(payload)

    def get_or_none(self, capture_id: str) -> CaptureRecord | None:
        """A test-only convenience: "is anything stored under this id?"."""
        payload = self._snapshots.get(capture_id)
        return None if payload is None else CaptureRecord.model_validate_json(payload)

    def replace(self, record: CaptureRecord) -> None:
        if self._fail_replace_with is not None:
            raise self._fail_replace_with
        if record.id not in self._snapshots:
            raise CaptureRecordNotFoundError(f"capture record {record.id!r} is not stored")
        self._snapshots[record.id] = record.model_dump_json()


class FakeContentObjectStore:
    """A dictionary-backed ``ContentObjectStore``, with the capture uniqueness rule.

    ``fail_get_for_capture_with`` covers the read side: a store that holds
    something it cannot hand back, which is what a corrupt canonical snapshot
    looks like to a caller.
    """

    def __init__(
        self,
        *,
        fail_create_with: Exception | None = None,
        fail_get_for_capture_with: Exception | None = None,
    ) -> None:
        self._by_id: dict[str, str] = {}
        self._by_capture: dict[str, str] = {}
        self._fail_create_with = fail_create_with
        self._fail_get_for_capture_with = fail_get_for_capture_with
        #: Every capture id ``get_for_capture`` was asked about, in order. What
        #: this proves is a negative: that a duplicate of a capture which is not
        #: complete is answered without the content store being consulted.
        self.capture_lookups: list[str] = []

    def create(self, content: ContentObject) -> None:
        if self._fail_create_with is not None:
            raise self._fail_create_with
        if content.id in self._by_id:
            raise ContentObjectAlreadyExistsError(
                f"content object {content.id!r} is already stored"
            )
        capture_id = content.source.capture_id
        if capture_id in self._by_capture:
            raise ContentObjectAlreadyExistsError(
                f"capture {capture_id!r} already has a stored content object"
            )
        payload = content.model_dump_json()
        self._by_id[content.id] = payload
        self._by_capture[capture_id] = payload

    def get(self, content_id: str) -> ContentObject:
        try:
            return ContentObject.model_validate_json(self._by_id[content_id])
        except KeyError:
            raise ContentObjectNotFoundError(
                f"content object {content_id!r} is not stored"
            ) from None

    def get_for_capture(self, capture_id: str) -> ContentObject:
        self.capture_lookups.append(capture_id)
        if self._fail_get_for_capture_with is not None:
            raise self._fail_get_for_capture_with
        try:
            return ContentObject.model_validate_json(self._by_capture[capture_id])
        except KeyError:
            raise ContentObjectNotFoundError(
                f"capture {capture_id!r} has no stored content object"
            ) from None

    def stored_content_ids(self) -> set[str]:
        """A test-only view: which content objects exist at all.

        Counting them is how "the replay created nothing" is checked from
        outside the API, rather than from the API's own account of itself.
        """
        return set(self._by_id)

    def drop_for_capture(self, capture_id: str) -> None:
        """Test-only: delete a capture's content, leaving its record alone.

        This manufactures the one state Phase 0I says cannot happen — a
        ``COMPLETE`` capture with no canonical content — by breaking it the way
        reality would, rather than by injecting an error a store would not
        raise.
        """
        payload = self._by_capture.pop(capture_id)
        self._by_id.pop(ContentObject.model_validate_json(payload).id, None)

    def get_or_none(self, capture_id: str) -> ContentObject | None:
        """A test-only convenience: "did this capture produce content?"."""
        payload = self._by_capture.get(capture_id)
        return None if payload is None else ContentObject.model_validate_json(payload)


class FailingProcessor:
    """A ``Processor`` that claims text captures and always refuses them.

    The failure is supplied by the test, so one double covers every processing
    verdict the error table has to translate.
    """

    name = "failing"
    version = "0.1"

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def supports(self, capture: CaptureRecord) -> bool:
        return capture.payload_type is CapturePayloadType.TEXT

    def process(self, capture: CaptureRecord) -> ContentObject:
        raise self._failure


class RecordingProcessor:
    """Wraps a processor and notes when its ``process`` call entered and returned.

    Used to prove that ``201`` is written after the pipeline rather than
    alongside it: the journal records the way out of the processor, so a
    response produced before the orchestrator finished would be visible as a
    missing entry.
    """

    def __init__(self, inner: Processor) -> None:
        self._inner = inner
        self.name = inner.name
        self.version = inner.version
        self.journal: list[str] = []

    def supports(self, capture: CaptureRecord) -> bool:
        return self._inner.supports(capture)

    def process(self, capture: CaptureRecord) -> ContentObject:
        self.journal.append("process.enter")
        content = self._inner.process(capture)
        self.journal.append("process.return")
        return content
