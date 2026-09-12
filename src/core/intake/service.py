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

Phase 3 PR 1 widens something genuinely different, and it is worth naming
precisely. A ``DOCUMENT`` capture's material never travelled inside the
envelope: a PDF is binary, JSON is not, and base64 in a ``text`` field would be
a lie about what was captured. The bytes are staged *before* the capture, as an
immutable content-addressed raw object, and the envelope carries only the
``file_ref`` that names them. So for a document the sequence loses a step
rather than gaining one::

    CaptureEnvelope(DOCUMENT, file_ref -> already-staged document)
        -> resolve and verify the reference   (read-only, before any side effect)
        -> CaptureRecord(RECEIVED)
        -> CaptureRecord(STORED)              pointing at those very bytes

**Intake writes no bytes on that path.** The original is already immutable and
already addressed by its own SHA-256; storing it a second time would be a second
copy of something that deduplicates to itself, and a pointless one. What intake
adds is the capture: the record that says somebody asked for those bytes to be
remembered.

The reference is settled *first*, entirely through reads, because the ordering
rule that makes ``RECEIVED`` worth writing cuts both ways. A receipt is durable
evidence that a capture was accepted; a capture whose material cannot be found
was never acceptable, and leaving a ``RECEIVED`` record behind for one would be
evidence of something that did not happen.

Only the raw store's own reference format is resolved. A ``file_ref`` is an
opaque handle by contract, and this build understands exactly one kind:
``sha256:<digest>``, as returned by the upload surface. Nothing here opens a
path, resolves a ``file://`` URL, or fetches an HTTP one — treating a
caller-supplied string as a filesystem path would move authority from the
upload boundary into core and hand every client a local-file-read primitive.

Phase 3 PR 2 widens one value and nothing else: the set of document MIME types a
``DOCUMENT`` capture may declare grows from ``application/pdf`` alone to that
plus the OOXML ``.docx`` type. The sequence above, the ordering guarantee, the
reference format, the no-second-write rule, and every message's shape are
untouched, and intake still does not open, sniff, unzip, or parse the material it
points at. That is the result worth noticing: adding a second binary document
format cost this module a tuple, because the acquisition boundary was designed
to be format-independent rather than PDF-shaped.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from core.contracts import (
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureStatus,
    RawObjectRef,
)
from core.intake.errors import (
    CaptureMaterialUnavailableError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.persistence import CaptureRecordStore
from core.storage import (
    RAW_REF_SCHEME,
    InvalidRawObjectRefError,
    RawObjectStore,
    parse_raw_ref,
    raw_object_ref,
)

#: The encoding inline capture material is written with — the submitted text of
#: a ``TEXT`` capture, and since Phase 2 the submitted HTML of a ``WEBPAGE``
#: one. It is not a preference: the processors decode stored originals as
#: strict UTF-8, so these must name the same encoding for a capture to survive
#: its own pipeline. A second encoding would be a decision at both ends, not a
#: default at one. The name is kept from Phase 0F because the constant is
#: exported and the value it names has not changed.
TEXT_ENCODING: Final = "utf-8"

#: The payload fields that carry submitted material a ``CaptureRecord`` could
#: store as its one raw original. A ``WEBPAGE`` or ``DOCUMENT`` envelope naming
#: more than one of them is refused rather than resolved: see
#: :meth:`CaptureIntake._webpage_html` and :meth:`CaptureIntake._staged_document`.
MATERIAL_PAYLOAD_FIELDS: Final = ("text", "html", "file_ref")

#: The document formats this build has a processor for, in the order they were
#: added. A ``DOCUMENT`` capture must declare one of them explicitly: intake does
#: not sniff bytes, read magic numbers, look inside a ZIP, or infer a format from
#: a filename it was never given or a URL it was handed, and a document whose
#: format is merely *probably* one of these is one this build declines to guess
#: at.
#:
#: Phase 3 PR 2 turned this from a single value into a set, which is as much
#: generalization as two formats earn. It is a membership test and a message,
#: not a registry: a plugin system for two entries would be architecture
#: standing in for a requirement, and the moment a format needs intake to treat
#: it *differently* — rather than merely to allow it — that difference is what
#: will say what the abstraction should be.
#:
#: These names are restated here rather than imported from
#: :mod:`core.processing`. Intake deliberately does not depend on the processing
#: layer: what it is deciding is which declarations *this deployment* accepts at
#: its boundary, which is a fact about the build rather than about any one
#: processor, and the wiring that registers the processors is what keeps the two
#: lists honest.
DOCUMENT_MIME_TYPES: Final[tuple[str, ...]] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

#: The supported document formats as they appear in a refusal message.
_DOCUMENT_MIME_TYPES_PHRASE: Final = " and ".join(DOCUMENT_MIME_TYPES)


@dataclass(frozen=True)
class _InlineMaterial:
    """Material that arrived inside the envelope, as the bytes to write."""

    data: bytes


@dataclass(frozen=True)
class _StagedMaterial:
    """Material that was already immutable before the capture existed.

    ``raw_object`` is the reference the ``CaptureRecord`` will carry: minted
    from the digest the submitted ``file_ref`` names, and carrying the MIME type
    the submitter declared. It is a *fresh* reference rather than the upload
    response's — intake mints what this capture records, and does not pass a
    caller's object through into durable state.
    """

    raw_object: RawObjectRef


#: What the envelope's payload resolves to before anything becomes durable.
_Material = _InlineMaterial | _StagedMaterial


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

        1. refuse anything it cannot materialize, before any side effect —
           including, for staged material, proving that the referenced raw
           object is actually there;
        2. create a ``RECEIVED`` record — the receipt, written first, and
           already carrying the envelope's capture-time metadata;
        3. obtain the reference to the immutable original: by writing the exact
           UTF-8 bytes of inline material, or — for a document — by taking the
           reference to bytes that were staged before this capture existed;
        4. replace the receipt with a ``STORED`` record carrying the reference.

        The metadata is durable from step 2, not step 4: a capture stranded by
        a failure in between still knows when, where, and why it was taken.
        Only the content itself is deferred to the raw store, and nothing about
        the envelope is ever written into those bytes.

        Step 3 writes nothing at all on the staged path, and step 1 is what
        earns that. The bytes are immutable and content-addressed already, so
        the only honest thing left to establish is that they exist — which is a
        read, and which happens before the clock is even looked at.

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
        material = self._materialize(envelope)
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

        # Inline material becomes bytes in the store; staged material already
        # is bytes in the store, and its reference was settled above. Either
        # way what comes out is the one ``RawObjectRef`` this capture records.
        raw_object = (
            material.raw_object
            if isinstance(material, _StagedMaterial)
            else self._raw_store.store_bytes(material.data, mime_type=mime_type)
        )

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

    def _materialize(self, envelope: CaptureEnvelope) -> _Material:
        """Settle what this capture's one raw original is, without writing it.

        Three payload types are supported, and each names exactly one submitted
        field as the material::

            TEXT                     payload.text     -> bytes to write
            WEBPAGE (HTML-backed)    payload.html     -> bytes to write
            DOCUMENT (staged PDF)    payload.file_ref -> bytes already stored

        For the two inline types, encoding is the only transformation. Nothing
        is trimmed, Unicode normalized, BOM-prefixed, or line-ending rewritten,
        and no encoding is detected or attempted other than UTF-8 — what the
        caller submitted is what a future processor reads back. For the staged
        type there is no transformation at all, because there is nothing to
        transform: the bytes were immutable before this call.

        The refusals come in three kinds, and they are different things. An
        envelope naming a capability this build does not have is *valid* — a
        later phase may accept it unchanged — and raises
        :class:`~core.intake.errors.UnsupportedCapturePayloadError`. An envelope
        contradicting its own contract raises
        :class:`~core.intake.errors.InvalidCaptureEnvelopeError`, which no phase
        will accept. An envelope whose staged material is simply not in the
        store raises
        :class:`~core.intake.errors.CaptureMaterialUnavailableError`, which the
        identical envelope survives once the bytes are staged. All three are
        raised before the clock is read or a record is written, so a refused
        envelope leaves nothing behind.

        The only store call this method may make is a read: the existence check
        on the document path. Reads are what make the ordering guarantee
        possible; a write here would be the side effect the guarantee exists to
        prevent.
        """
        match envelope.payload.type:
            case CapturePayloadType.TEXT:
                return _InlineMaterial(CaptureIntake._text(envelope).encode(TEXT_ENCODING))
            case CapturePayloadType.WEBPAGE:
                return _InlineMaterial(CaptureIntake._webpage_html(envelope).encode(TEXT_ENCODING))
            case CapturePayloadType.DOCUMENT:
                return self._staged_document(envelope)
            case _:
                raise UnsupportedCapturePayloadError(
                    f"capture {envelope.id!r} carries a {envelope.payload.type.value} payload; "
                    f"this build accepts inline {CapturePayloadType.TEXT.value} captures, "
                    f"html-backed {CapturePayloadType.WEBPAGE.value} captures, and "
                    f"staged {_DOCUMENT_MIME_TYPES_PHRASE} "
                    f"{CapturePayloadType.DOCUMENT.value} captures only"
                )

    def _staged_document(self, envelope: CaptureEnvelope) -> _StagedMaterial:
        """Resolve a ``DOCUMENT`` payload to the staged raw object it names.

        This build supports exactly one materialization of a document: a
        ``file_ref`` naming a raw object this store already holds, declared as
        one of :data:`DOCUMENT_MIME_TYPES`. Everything else about the shape is
        refused rather than resolved, for the two reasons the webpage path
        already established and one that is new to documents:

        * **A capture stores one raw original.** ``file_ref`` alongside ``text``
          or ``html`` offers more material than the record can hold, and picking
          one would durably discard something the client submitted while
          reporting success.
        * **The canonical contract stays wider than this build.** It permits a
          document backed by ``text``, and it always will; this implementation
          simply has no processor for that yet, so the refusal is an
          unsupported-capability error rather than a validation one.
        * **A format this build cannot parse must not be accepted as if it
          could.** A legacy ``.doc`` or an EPUB reaching intake would sail
          through to a router that has no processor for it, and the capture
          would strand mid-lifecycle for a reason nobody could act on. Refusing
          at the boundary — where the client is still holding the request — is
          the honest place to say "not yet". Which formats those are is the one
          thing Phase 3 PR 2 changed: the check is a membership test against
          :data:`DOCUMENT_MIME_TYPES` rather than an equality test against a
          single value, and the declared type still travels onto the capture's
          ``RawObjectRef`` exactly as submitted, which is what lets the router
          tell a PDF capture from a DOCX one without either of them being
          sniffed.

        The reference itself is checked in two separate steps, because they fail
        for different reasons and a caller does different things about them:

        1. **Is it a reference this build understands?** Only the raw store's
           own ``sha256:<digest>`` form is resolved. A filesystem path, a
           ``file://`` URL, an HTTP URL, or an S3 key is refused as an
           unsupported capability — and, importantly, is never *opened*,
           *resolved*, or *fetched*. This is the one line standing between an
           arbitrary caller-supplied string and a local-file-read primitive, and
           it is why the upload surface exists at all.
        2. **Are the bytes there?** A well-formed reference naming nothing is a
           :class:`~core.intake.errors.CaptureMaterialUnavailableError` — the
           envelope is right and the material has not been staged.

        No submitted value is echoed. The refusals name the payload type, the
        field *names*, and the MIME type this build supports; they never repeat
        the ``file_ref``, the declared MIME type, or any other client string
        back into a message that will be logged. A ``file_ref`` in particular
        may be an absolute path from someone's home directory, and a refusal is
        not a reason to publish it.
        """
        payload = envelope.payload
        if payload.file_ref is None:
            if payload.text is None:
                raise InvalidCaptureEnvelopeError(
                    f"capture {envelope.id!r} declares a document payload "
                    f"but carries neither file_ref nor text"
                )
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a document payload backed by text; "
                f"this build ingests documents backed by a staged file_ref only, declared "
                f"as one of {_DOCUMENT_MIME_TYPES_PHRASE}"
            )
        alongside = [
            name
            for name in MATERIAL_PAYLOAD_FIELDS
            if name != "file_ref" and getattr(payload, name) is not None
        ]
        if alongside:
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a document payload with file_ref and "
                f"{' and '.join(alongside)}; a capture stores one raw original, and this "
                f"build will not choose between submitted representations — resubmit with "
                f"file_ref alone"
            )
        if payload.mime_type is None:
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a document payload declaring no mime_type; "
                f"this build ingests {_DOCUMENT_MIME_TYPES_PHRASE} documents only, and does "
                f"not infer a document's format"
            )
        if payload.mime_type not in DOCUMENT_MIME_TYPES:
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a document payload declaring a mime_type "
                f"this build has no processor for; it ingests {_DOCUMENT_MIME_TYPES_PHRASE} "
                f"documents only"
            )

        try:
            digest = parse_raw_ref(payload.file_ref)
        except InvalidRawObjectRefError:
            # Deliberately unchained. The store's own message quotes the
            # reference it rejected, and a rejected reference is exactly the
            # kind of client string — a home directory path, a private URL —
            # that must not be carried into a message or a traceback that ends
            # up in a log.
            raise UnsupportedCapturePayloadError(
                f"capture {envelope.id!r} carries a document payload whose file_ref is not a "
                f"UniMem raw object reference; this build resolves references of the form "
                f"'{RAW_REF_SCHEME}:<64 lowercase hex characters>', as returned when the "
                f"bytes were staged, and reads no filesystem path or URL"
            ) from None

        # Minted here rather than taken from a caller: this is the reference the
        # capture record will carry, and the MIME type on it is the one the
        # submitter declared for *this capture*, not one the raw store inferred.
        raw_object = raw_object_ref(digest, mime_type=payload.mime_type)
        if not self._raw_store.exists(raw_object):
            raise CaptureMaterialUnavailableError(
                f"capture {envelope.id!r} refers to staged material that is not in the "
                f"raw object store; stage the bytes first, then resubmit this capture"
            )
        return _StagedMaterial(raw_object)

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
