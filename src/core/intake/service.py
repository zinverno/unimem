"""Capture intake: the first orchestration boundary in the system.

Everything before this phase was a component. Contracts describe, the raw store
persists bytes, processors normalize, renderers project, and the capture record
store persists snapshots — but nothing called them in order. Intake is the first
code that owns a sequence, and the sequence is the whole of it::

    CaptureEnvelope(TEXT)
        -> CaptureRecord(RECEIVED)   created first, so the capture is on record
        -> RawObjectStore            the immutable original
        -> CaptureRecord(STORED)     replaced with the reference to those bytes

The receipt comes first on purpose. If the raw write fails, a durable
``RECEIVED`` record says a capture was accepted and is incomplete; if the bytes
were written first and the process died, there would be orphaned bytes and no
evidence anyone ever asked for them.

Intake orchestrates and nothing else: it does not process, route, render,
create a ``ContentObject``, or decide a lifecycle policy beyond the two states
it writes.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from core.contracts import (
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureStatus,
)
from core.intake.errors import (
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.persistence import CaptureRecordStore
from core.storage import RawObjectStore

#: The encoding inline capture text is written with. It is not a preference:
#: Phase 0C's ``TextProcessor`` decodes stored originals as strict UTF-8, so
#: these two must name the same encoding for a capture to survive its own
#: pipeline. A second encoding would be a decision at both ends, not a default
#: at one.
TEXT_ENCODING: Final = "utf-8"


def utc_now() -> datetime:
    """The default clock: the current time, timezone-aware, in UTC."""
    return datetime.now(UTC)


class CaptureIntake:
    """Accepts a capture envelope and leaves a durably stored capture behind.

    The two stores are injected as their ports, so intake depends on
    ``RawObjectStore`` and ``CaptureRecordStore`` and on no backend. The clock
    is injected as a plain callable for the same reason and no more: a test
    needs to control time, which a function already does. There is no clock
    class, no service hierarchy, no container, no registry, and no router —
    Phase 0F supports one payload type, so there is nothing to route.
    """

    def __init__(
        self,
        raw_store: RawObjectStore,
        record_store: CaptureRecordStore,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._raw_store = raw_store
        self._record_store = record_store
        self._now = now

    def accept(self, envelope: CaptureEnvelope) -> CaptureRecord:
        """Register a capture, store its bytes, and return the stored snapshot.

        The order is the contract:

        1. refuse anything it cannot materialize, before any side effect;
        2. create a ``RECEIVED`` record — the receipt, written first;
        3. store the exact UTF-8 bytes of the text;
        4. replace the receipt with a ``STORED`` record carrying the reference.

        Nothing is rolled back if a later step fails, and nothing is retried.
        The raw object store has no delete and shares no transaction with the
        record store, so a failure after step 2 leaves a truthful ``RECEIVED``
        record rather than a fabricated ``FAILED`` one.
        """
        data = self._materialize(envelope)

        received = CaptureRecord(
            id=envelope.id,
            status=CaptureStatus.RECEIVED,
            received_at=self._now(),
            updated_at=None,
            source=envelope.source.model_copy(deep=True),
            payload_type=envelope.payload.type,
            raw_object=None,
            error=None,
        )
        self._record_store.create(received)

        raw_object = self._raw_store.store_bytes(data, mime_type=envelope.payload.mime_type)

        stored = CaptureRecord(
            id=received.id,
            status=CaptureStatus.STORED,
            received_at=received.received_at,
            updated_at=self._now(),
            source=received.source.model_copy(deep=True),
            payload_type=received.payload_type,
            raw_object=raw_object,
            error=None,
        )
        self._record_store.replace(stored)
        return stored

    @staticmethod
    def _materialize(envelope: CaptureEnvelope) -> bytes:
        """Turn the envelope's payload into the exact bytes to store.

        Encoding is the only transformation. The text is not trimmed, Unicode
        normalized, BOM-prefixed, or line-ending rewritten, and no encoding is
        detected or attempted other than UTF-8 — what the caller submitted is
        what a future processor reads back.

        The two refusals are different things. A non-``TEXT`` payload is a
        valid envelope naming a capability this phase does not have, and a
        later phase will accept it unchanged. A ``TEXT`` payload with no text
        is an envelope contradicting its own contract, which no phase will
        accept. Both are raised before the clock is read or a store is
        touched, so a refused envelope leaves nothing behind.
        """
        if envelope.payload.type is not CapturePayloadType.TEXT:
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a {envelope.payload.type.value} payload; "
                f"this phase accepts inline {CapturePayloadType.TEXT.value} only"
            )
        text = envelope.payload.text
        if text is None:
            raise InvalidCaptureEnvelopeError(
                f"capture {envelope.id!r} declares a text payload but carries no text"
            )
        return text.encode(TEXT_ENCODING)
