"""Capture intake: the first orchestration boundary in the system.

Everything before this phase was a component. Contracts describe, the raw store
persists bytes, processors normalize, renderers project, and the capture record
store persists snapshots — but nothing called them in order. Intake is the first
code that owns a sequence, and the sequence is the whole of it::

    CaptureEnvelope(TEXT | html-backed WEBPAGE)
        -> CaptureRecord(RECEIVED)   created first, with the capture metadata
        -> RawObjectStore            the immutable original
        -> CaptureRecord(STORED)     replaced with the reference to those bytes

The receipt comes first on purpose. If the raw write fails, a durable
``RECEIVED`` record says a capture was accepted and is incomplete; if the bytes
were written first and the process died, there would be orphaned bytes and no
evidence anyone ever asked for them.

Intake orchestrates and nothing else: it does not process, route, render,
create a ``ContentObject``, or decide a lifecycle policy beyond the two states
it writes.

Phase 2 PR 1 widened exactly one thing here: *which submitted field becomes the
raw original*. A ``TEXT`` capture stores ``payload.text``, an HTML-backed
``WEBPAGE`` capture stores ``payload.html``, and both store the exact UTF-8
encoding of that string and nothing else. The lifecycle, the ordering, the
metadata, and the text path's bytes are untouched — intake still does not parse,
extract, or look inside the material it stores, and the HTML never appears
anywhere on the ``CaptureRecord``.
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

#: The encoding inline capture material is written with — the submitted text of
#: a ``TEXT`` capture, and since Phase 2 the submitted HTML of a ``WEBPAGE``
#: one. It is not a preference: the processors decode stored originals as
#: strict UTF-8, so these must name the same encoding for a capture to survive
#: its own pipeline. A second encoding would be a decision at both ends, not a
#: default at one. The name is kept from Phase 0F because the constant is
#: exported and the value it names has not changed.
TEXT_ENCODING: Final = "utf-8"

#: The payload fields that carry submitted material a ``CaptureRecord`` could
#: store as its one raw original. A ``WEBPAGE`` envelope naming more than one of
#: them is refused rather than resolved: see :meth:`CaptureIntake._webpage_html`.
MATERIAL_PAYLOAD_FIELDS: Final = ("text", "html", "file_ref")


def utc_now() -> datetime:
    """The default clock: the current time, timezone-aware, in UTC."""
    return datetime.now(UTC)


class CaptureIntake:
    """Accepts a capture envelope and leaves a durably stored capture behind.

    The two stores are injected as their ports, so intake depends on
    ``RawObjectStore`` and ``CaptureRecordStore`` and on no backend. The clock
    is injected as a plain callable for the same reason and no more: a test
    needs to control time, which a function already does. There is no clock
    class, no service hierarchy, no container, no registry, and no router.
    Routing is the *processing* layer's job and stays there: intake decides only
    whether it can turn a payload into bytes, which is a property of the
    envelope and needs no registry to answer.
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
        2. create a ``RECEIVED`` record — the receipt, written first, and
           already carrying the envelope's capture-time metadata;
        3. store the exact UTF-8 bytes of the submitted material;
        4. replace the receipt with a ``STORED`` record carrying the reference.

        The metadata is durable from step 2, not step 4: a capture stranded by
        a failure in between still knows when, where, and why it was taken.
        Only the content itself is deferred to the raw store, and nothing about
        the envelope is ever written into those bytes.

        The envelope is read once, before anything becomes durable. From the
        moment ``create`` succeeds, the ``RECEIVED`` snapshot is the authority
        on every capture fact carried into ``STORED``: the two snapshots
        describe one capture, and re-reading a caller-held object across a
        store call is how they would come to disagree.

        Nothing is rolled back if a later step fails, and nothing is retried.
        The raw object store has no delete and shares no transaction with the
        record store, so a failure after step 2 leaves a truthful ``RECEIVED``
        record rather than a fabricated ``FAILED`` one.
        """
        data = self._materialize(envelope)
        mime_type = envelope.payload.mime_type

        # Read from the envelope once, here, and never again. Everything below
        # descends from this snapshot instead of re-reading the caller's object.
        context = envelope.context.model_copy(deep=True)

        received = CaptureRecord(
            id=envelope.id,
            status=CaptureStatus.RECEIVED,
            received_at=self._now(),
            updated_at=None,
            source=envelope.source.model_copy(deep=True),
            payload_type=envelope.payload.type,
            raw_object=None,
            error=None,
            # Capture-time facts, durable from the very first snapshot. Each
            # snapshot gets its own deep copies, so neither the envelope the
            # caller still holds nor the other snapshot shares a mutable
            # ``CaptureIntent.tags`` list with this one.
            context=context,
            intent=None if envelope.intent is None else envelope.intent.model_copy(deep=True),
            title=envelope.payload.title,
        )
        self._record_store.create(received)

        raw_object = self._raw_store.store_bytes(data, mime_type=mime_type)

        # ``received`` is now durable, which makes it the authority on every
        # capture fact — not the envelope. The raw store ran in between, and a
        # caller (or a store that was handed the envelope elsewhere) may have
        # mutated it since; re-reading it here would let ``stored`` silently
        # disagree with the snapshot already on record. Only status, the
        # timestamp, and the reference to the bytes are new.
        #
        # ``context`` is the very object ``received`` carries, kept in hand
        # because the field is optional on the model and required only from
        # schema 0.2 — copying it from the local says the same thing as
        # ``received.context`` without pretending the None case is reachable.
        stored = CaptureRecord(
            id=received.id,
            status=CaptureStatus.STORED,
            received_at=received.received_at,
            updated_at=self._now(),
            source=received.source.model_copy(deep=True),
            payload_type=received.payload_type,
            raw_object=raw_object,
            error=None,
            context=context.model_copy(deep=True),
            intent=None if received.intent is None else received.intent.model_copy(deep=True),
            title=received.title,
        )
        self._record_store.replace(stored)
        return stored

    @staticmethod
    def _materialize(envelope: CaptureEnvelope) -> bytes:
        """Turn the envelope's payload into the exact bytes to store.

        Encoding is the only transformation, for every supported payload type.
        Nothing is trimmed, Unicode normalized, BOM-prefixed, or line-ending
        rewritten, and no encoding is detected or attempted other than UTF-8 —
        what the caller submitted is what a future processor reads back.

        Two payload types are supported, and each names exactly one submitted
        field as the material::

            TEXT                     payload.text
            WEBPAGE (HTML-backed)    payload.html

        The refusals come in two kinds, and they are different things. An
        envelope naming a capability this build does not have is *valid* — a
        later phase may accept it unchanged — and raises
        :class:`~core.intake.errors.UnsupportedCapturePayloadError`. An envelope
        contradicting its own contract raises
        :class:`~core.intake.errors.InvalidCaptureEnvelopeError`, which no phase
        will accept. Both are raised before the clock is read or a store is
        touched, so a refused envelope leaves nothing behind.
        """
        match envelope.payload.type:
            case CapturePayloadType.TEXT:
                return CaptureIntake._text(envelope).encode(TEXT_ENCODING)
            case CapturePayloadType.WEBPAGE:
                return CaptureIntake._webpage_html(envelope).encode(TEXT_ENCODING)
            case _:
                raise UnsupportedCapturePayloadError(
                    f"capture {envelope.id!r} carries a {envelope.payload.type.value} payload; "
                    f"this build accepts inline {CapturePayloadType.TEXT.value} and "
                    f"html-backed {CapturePayloadType.WEBPAGE.value} captures only"
                )

    @staticmethod
    def _text(envelope: CaptureEnvelope) -> str:
        """The submitted text of a ``TEXT`` capture.

        A ``TEXT`` payload with no text is an envelope contradicting its own
        contract. A well-formed ``CapturePayload`` cannot reach this state — the
        contract's own validator requires text for a text payload — but Phase 0A
        documents that an assignment rejected by a model-level validator has
        already been written, so a caller can hold an envelope that no longer
        satisfies its own invariants.
        """
        text = envelope.payload.text
        if text is None:
            raise InvalidCaptureEnvelopeError(
                f"capture {envelope.id!r} declares a text payload but carries no text"
            )
        return text

    @staticmethod
    def _webpage_html(envelope: CaptureEnvelope) -> str:
        """The submitted HTML of a ``WEBPAGE`` capture, or a refusal.

        Phase 2 PR 1 supports exactly one materialization of a webpage: the
        HTML-backed form, where ``payload.html`` is the whole of the submitted
        material. That restriction is not squeamishness, it is arithmetic. A
        ``CaptureRecord`` holds **one** raw original reference, so an envelope
        carrying HTML *and* ``text`` or ``file_ref`` offers more material than
        this record can store. Silently picking the HTML would durably discard
        something the client submitted and report success, which is the one
        outcome worth refusing outright — so an ambiguous webpage is refused,
        and nothing is dropped.

        The canonical contract deliberately stays wider than this: it allows a
        webpage envelope backed by ``text``, and it always will. What is
        narrowed here is only what *this build* promises to ingest, which is why
        the refusal is an unsupported-capability error rather than a validation
        one — a later phase that can represent several materializations will
        accept these identical envelopes unchanged.

        No field is read for its value, and none is echoed: the refusals name
        the payload type and the field *names* only, so no submitted markup can
        reach an error message or a log through here.
        """
        payload = envelope.payload
        if payload.html is None:
            if payload.text is None:
                raise InvalidCaptureEnvelopeError(
                    f"capture {envelope.id!r} declares a webpage payload "
                    f"but carries neither html nor text"
                )
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a webpage payload backed by text; "
                f"this build ingests html-backed webpage captures only"
            )
        alongside = [
            name
            for name in MATERIAL_PAYLOAD_FIELDS
            if name != "html" and getattr(payload, name) is not None
        ]
        if alongside:
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a webpage payload with html and "
                f"{' and '.join(alongside)}; a capture stores one raw original, and this "
                f"build will not choose between submitted representations — resubmit with "
                f"html alone"
            )
        return payload.html
