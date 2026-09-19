"""Completed-capture replay: answering a resubmission the client already made.

This module exists because of one concrete thing a real browser connector does.
The extension mints a capture id, POSTs the envelope, and the response is lost
on the way back. The capture may be durably ``COMPLETE`` on the server, and the
client has no way to know. Resubmitting with a fresh id would duplicate the
user's capture; resubmitting with the same id used to be a flat ``409``. Neither
is an answer, so the client could only look and hope.

The narrow guarantee here closes exactly that gap, and nothing wider::

    POST (same id, same request)
        -> intake refuses: the id is taken
        -> is the durable capture COMPLETE and demonstrably this same request?
            yes -> 200 with the content it already produced
            no  -> the duplicate conflict stands, unchanged

**The capture id is the replay identity.** There is no ``Idempotency-Key``
header, request token, nonce, fingerprint table, or second store. The client
already mints one opaque id per capture *before* the POST, so a replay is
literally a resubmission of that id, and the ``CaptureRecord`` primary key
remains the only authority on who won creation. Nothing here writes.

**Same id does not mean same request.** A capture id is a claim, not proof, so
a replay is granted only when the incoming envelope is provably the same
request as the durable capture — every observable semantic fact matching, and
the exact submitted bytes verified through the raw object's SHA-256. Anything
that differs, and anything the durable record simply cannot speak for, is a
conflict. This is why replay proves rather than assumes: returning someone
else's content for a colliding id would be far worse than a spurious 409.

**Completed only.** A duplicate of a capture that is ``RECEIVED``, ``STORED``,
``PROCESSING``, ``FAILED``, or anything else is still a conflict. Nothing here
resumes, retries, reprocesses, marks, polls, waits, or reconciles: a capture
stranded by a failure keeps whatever truthful state core left it in, and
``GET /v1/captures/{id}`` stays the way a client observes it.

**No concurrency claim.** Two simultaneous submissions of one id do not
converge here. Whichever loses creation sees whatever is durable at that
instant, and if the winner is still processing that is a ``409`` — correct, and
not what this module is for. The guarantee is only: *once a capture is durably
complete, an equivalent resubmission can be answered with its result.*

**Defined per materialization, and narrowly.** Phase 1 defined equivalence for
an inline ``TEXT`` payload; Phase 5A-2 adds staged ``AUDIO`` and ``VIDEO``, where
the submitted material is a content-addressed digest the client named and
"the same bytes" is therefore a comparison rather than a re-read. Every other
payload type — ``WEBPAGE``, ``DOCUMENT``, ``IMAGE``, ``FILE``, ``URL`` — has no
rule here and is not replayable, and their behaviour is unchanged by this phase.
A modality joins that table only with its own proof, which is why the default is
refusal rather than a generic field-by-field comparison.

Media replay additionally requires the *incoming* envelope to declare schema
0.3, and that limit is stated rather than worked around. Intake records every
capture at the current schema version and nothing durably keeps the version the
request arrived with, so a legacy 0.1 or 0.2 ``VIDEO`` resubmission cannot be
proven equivalent to anything. It keeps the conflict it would always have had.

The policy lives here, in the delivery layer, and not in ``core``. Core's
contract is unchanged: intake still refuses a duplicate id, and this module is
one read-only question asked afterwards by the code that owns HTTP semantics.
"""

import hashlib
from typing import Final

from core.contracts import (
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureStatus,
)
from core.intake import TEXT_ENCODING
from core.persistence import (
    CaptureRecordNotFoundError,
    CaptureRecordStore,
    ContentObjectNotFoundError,
    ContentObjectStore,
)
from core.storage import InvalidRawObjectRefError, build_raw_ref, parse_raw_ref
from unimem_api.models import CaptureAcceptedResponse

#: The payload types a media replay is defined for.
_MEDIA_PAYLOAD_TYPES: Final = (CapturePayloadType.AUDIO, CapturePayloadType.VIDEO)

#: The one schema version a completed media replay may be granted at.
#:
#: Private to this module, and written out rather than borrowed, because the two
#: constants it could plausibly reuse both mean something else:
#:
#: * **not** :data:`~core.contracts.AUDIO_SCHEMA_VERSION`. That names the version
#:   that introduced the ``audio`` modality, and this rule governs ``VIDEO`` just
#:   as much — ``VIDEO`` has been valid since 0.1 and has nothing to do with when
#:   ``audio`` arrived. Tying a delivery rule for video to audio's history would
#:   be a coincidence of values standing in for a reason.
#: * **not** :data:`~core.contracts.SCHEMA_VERSION`. That tracks whatever is
#:   current, so a future bump to 0.4 would silently extend replay equivalence to
#:   a version whose proof obligations nobody has reviewed. Widening what may be
#:   replayed must be a deliberate edit here, in a diff someone reads.
#:
#: The rule itself is unchanged: intake records every capture at the current
#: version and nothing durably retains the version a request arrived with, so a
#: resubmission at any other version cannot be proven equivalent.
_MEDIA_REPLAY_SCHEMA_VERSION: Final = "0.3"


class CaptureReplayIntegrityError(Exception):
    """A capture says ``COMPLETE`` but its canonical content is not there.

    Phase 0I established the invariant this violates: a ``COMPLETE`` capture
    *has* durable canonical content, because the orchestrator writes the
    content first and the ``COMPLETE`` snapshot second. So this is not a
    client's problem to fix and not a duplicate to refuse — it is the server
    contradicting itself, and the only honest answer is a 500.

    It lives in the delivery layer because the delivery layer is where the
    question is asked. Reusing a core persistence error to get a convenient
    status code would be inventing a domain failure that did not happen: no
    store failed here, and each answered exactly what it was asked.
    """


def is_equivalent_text_replay(envelope: CaptureEnvelope, record: CaptureRecord) -> bool:
    """Is this envelope demonstrably the same request the record was built from?

    Equivalence is defined only for the materialization this build actually
    supports — an inline ``TEXT`` payload — and it is proven from durable facts,
    never assumed from the id.

    Every observable semantic fact must match:

    * the capture id and the schema version;
    * ``source``, ``context``, and ``intent``, compared as the validated domain
      models they already are, so the comparison is the contract's own notion of
      equality rather than a hand-maintained field list that could fall behind
      it. Two ``captured_at`` values naming the same instant in different UTC
      offsets are therefore equal — they are the same fact, differently written
      — while a different instant is not;
    * the submitted ``title`` and ``mime_type``;
    * and the exact submitted text, verified by hashing
      ``payload.text.encode("utf-8")`` — the very transformation intake applies
      — and comparing it to the raw object's stored digest. Nothing is trimmed,
      case-folded, Unicode-normalized, BOM-stripped, or line-ending rewritten
      first, so a leading space, a ``\\r\\n``, a combining mark, or a BOM makes
      two texts different, exactly as the pipeline already treats them.

    ``html`` and ``file_ref`` must both be absent. They are not refused because
    they are forbidden — a ``TEXT`` payload may legally carry them — but because
    a text ``CaptureRecord`` does not represent them. If one is populated, the
    durable capture cannot say whether the original request carried it too, and
    an unprovable equivalence must not be reported as a replay.

    Server-generated lifecycle facts — ``received_at``, ``updated_at``,
    processing timestamps — are deliberately not compared. They describe what
    the server did, not what the client asked for, and a client resending an
    identical request has no way to reproduce them.
    """
    payload = envelope.payload

    if envelope.id != record.id:
        return False
    if envelope.schema_version != record.schema_version:
        return False

    # Both sides must be the inline-text case this predicate is defined for.
    if payload.type is not CapturePayloadType.TEXT:
        return False
    if record.payload_type is not CapturePayloadType.TEXT:
        return False

    # Payload facts the durable text record cannot represent, so cannot vouch for.
    if payload.html is not None or payload.file_ref is not None:
        return False

    if envelope.source != record.source:
        return False
    if envelope.context != record.context:
        return False
    if envelope.intent != record.intent:
        return False
    if payload.title != record.title:
        return False

    raw_object = record.raw_object
    if raw_object is None or raw_object.sha256 is None:
        return False
    if payload.mime_type != raw_object.mime_type:
        return False

    text = payload.text
    if text is None:
        return False
    return hashlib.sha256(text.encode(TEXT_ENCODING)).hexdigest() == raw_object.sha256


def is_equivalent_media_replay(envelope: CaptureEnvelope, record: CaptureRecord) -> bool:
    """Is this media envelope demonstrably the same request the record was built from?

    The staged-original counterpart of :func:`is_equivalent_text_replay`, for
    ``AUDIO`` and ``VIDEO`` only. Everything it proves, it proves from durable
    facts on the ``CaptureRecord`` and from the envelope in hand. **Nothing is
    read, probed, opened, or reprocessed**: the raw object store is not touched,
    no :class:`~core.processing.media_probe.MediaProbe` is called, and the
    content is not rebuilt. A replay that had to run the engine again would not
    be a replay.

    That is possible because media is staged. The submitted material *is* a
    content-addressed digest the client named, so proving "these are the same
    bytes" is a string comparison against the digest the record already carries
    — where the text path has to hash the submitted string to reach the same
    certainty. Neither needs the bytes themselves.

    **The incoming envelope must declare schema 0.3, and this is explicit rather
    than incidental.** Intake writes every record at the *current* schema version
    and nothing durably retains the version the request arrived with, so for a
    legacy 0.1 or 0.2 ``VIDEO`` submission there is no stored fact that could
    prove the resubmission matches the original request. Rather than invent one —
    an ``ingress_schema_version`` column would be persistence added purely to
    serve a replay — such a duplicate keeps the conflict it would have received
    anyway. A legacy ``VIDEO`` capture is still accepted and processed the first
    time; only its exact resubmission is a ``409``. That is not a regression: a
    duplicate was always a conflict.

    Every observable semantic fact must match:

    * the capture id, and both sides at schema 0.3;
    * the payload type, which must be one the record also carries, and must be
      ``AUDIO`` or ``VIDEO`` — this predicate says nothing about any other kind;
    * ``source``, ``context``, and ``intent``, compared as the validated domain
      models they already are, so equality is the contract's own notion rather
      than a hand-maintained field list;
    * the submitted ``title`` and the declared ``mime_type``;
    * and the exact submitted material, proven by the ``file_ref``'s digest
      matching **every** durable spelling of the ``RawObjectRef``'s digest — its
      ``id``, its ``sha256``, and the digest inside its ``ref``. A record whose
      own three spellings disagree is internally inconsistent and is never
      granted a replay.

    The payload must be ``file_ref``-backed and carry nothing alongside it.
    ``text`` or ``html`` beside the reference is material the media record cannot
    represent, so it cannot vouch for whether the original request carried it
    either — and an unprovable equivalence must not be reported as a replay.

    Returns ``False`` for anything else, including a reference this build cannot
    parse. Being unable to prove equivalence and having disproven it lead to the
    same place: the duplicate conflict stands.
    """
    payload = envelope.payload

    if envelope.id != record.id:
        return False
    # Both sides at the current version. The record's own version is checked too,
    # so a record written by some earlier build cannot be replayed against a 0.3
    # request that it never saw.
    if envelope.schema_version != _MEDIA_REPLAY_SCHEMA_VERSION:
        return False
    if record.schema_version != _MEDIA_REPLAY_SCHEMA_VERSION:
        return False

    # Both sides must be the staged-media case this predicate is defined for.
    if payload.type not in _MEDIA_PAYLOAD_TYPES:
        return False
    if record.payload_type is not payload.type:
        return False

    # Material the durable media record cannot represent, so cannot vouch for.
    if payload.text is not None or payload.html is not None:
        return False

    if envelope.source != record.source:
        return False
    if envelope.context != record.context:
        return False
    if envelope.intent != record.intent:
        return False
    if payload.title != record.title:
        return False

    raw_object = record.raw_object
    if raw_object is None or raw_object.sha256 is None or raw_object.ref is None:
        return False
    if payload.mime_type != raw_object.mime_type:
        return False

    file_ref = payload.file_ref
    if file_ref is None:
        return False
    try:
        digest = parse_raw_ref(file_ref)
    except InvalidRawObjectRefError:
        # A reference this build cannot parse is one it cannot compare. Not an
        # error here: intake would refuse it on its own terms, and a replay that
        # cannot be proven is simply not granted.
        return False
    # **All three** durable spellings of the digest must agree with the submitted
    # one. A raw object carries its content digest in ``id``, in ``sha256``, and
    # inside ``ref``; they are three writings of a single fact, and a record where
    # any of them disagrees is internally inconsistent. Such a record cannot
    # vouch for which bytes the capture was built from, so it is never granted a
    # replay — checking two of the three would let the odd one out pass unnoticed.
    return (
        digest == raw_object.id
        and digest == raw_object.sha256
        and raw_object.ref == build_raw_ref(digest)
    )


def _is_equivalent_replay(envelope: CaptureEnvelope, record: CaptureRecord) -> bool:
    """Dispatch to the equivalence rule for the payload type in hand.

    One small table rather than a chain of predicates each returning ``False``
    for the others' types. A payload type with no rule here — ``WEBPAGE``,
    ``DOCUMENT``, ``IMAGE``, ``FILE``, ``URL`` — is **not replayable**, and
    falls through to the duplicate conflict exactly as it always has. Adding a
    modality to this table is a deliberate act with its own proof obligations,
    which is why the default is refusal rather than a generic comparison.
    """
    match envelope.payload.type:
        case CapturePayloadType.TEXT:
            return is_equivalent_text_replay(envelope, record)
        case CapturePayloadType.AUDIO | CapturePayloadType.VIDEO:
            return is_equivalent_media_replay(envelope, record)
        case _:
            return False


def resolve_completed_replay(
    envelope: CaptureEnvelope,
    record_store: CaptureRecordStore,
    content_store: ContentObjectStore,
) -> CaptureAcceptedResponse | None:
    """Resolve a duplicate capture id as a completed replay, or decline to.

    Returns the existing success for a proven replay, and ``None`` for every
    duplicate that is not one — leaving the caller to let the conflict stand.
    Four things must hold, checked in this order because each is cheaper and
    more decisive than the next:

    1. the existing record loads;
    2. its status is ``COMPLETE``;
    3. the incoming request is equivalent to it;
    4. its canonical content loads.

    The order is part of the contract, not an optimization. A capture that is
    still ``PROCESSING`` is answered without the content store being asked
    anything at all, so a duplicate arriving mid-run cannot be mistaken for a
    capture whose content is missing.

    Only "no such record" is swallowed — the vanishingly rare race where
    creation was refused and the record is gone by the time this reads. A store
    that cannot answer, or answers with a snapshot it cannot validate, raises
    as it always has and is mapped as it always was: those are the server's
    failures, and reporting one as a plain duplicate conflict would hide it.

    **Every call here is a read.** No record is written, no status advanced, no
    timestamp touched, no content created, no bytes re-stored, and the
    processor is not run. A replay returns what the first submission produced,
    which is the only thing that makes it a replay rather than a second
    capture.
    """
    try:
        record = record_store.get(envelope.id)
    except CaptureRecordNotFoundError:
        return None

    if record.status is not CaptureStatus.COMPLETE:
        return None
    if not _is_equivalent_replay(envelope, record):
        return None

    try:
        content = content_store.get_for_capture(record.id)
    except ContentObjectNotFoundError as missing:
        raise CaptureReplayIntegrityError(
            f"capture {record.id!r} is complete but has no canonical content"
        ) from missing

    return CaptureAcceptedResponse(capture_id=record.id, content_id=content.id)
