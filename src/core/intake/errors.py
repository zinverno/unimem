"""Errors raised by capture intake.

The hierarchy is deliberately tiny, and every member of it exists because a
caller *does something different* about it:

* the envelope is fine and this phase is not yet ready for it — wait for a later
  phase, or resubmit in a shape this build ingests;
* the envelope contradicts its own contract — fix the submission, because no
  phase will accept it as it stands;
* the envelope refers to staged material that is not in the raw store — stage
  the bytes and resubmit the identical envelope.

The third joined in Phase 3 PR 1, when an envelope first became able to name
material it did not carry. It is a genuinely new answer rather than a shade of
the other two: nothing about the envelope is wrong, and nothing about the
capability is missing.

Everything else that can go wrong belongs to a store, and those errors keep
their own types: a caller deciding whether to retry needs to know that the
*raw store* failed, not merely that "intake failed".
"""


class CaptureIntakeError(Exception):
    """Base class for every error intake raises on its own behalf."""


class UnsupportedCapturePayloadError(CaptureIntakeError):
    """This build does not handle envelopes of this payload type or shape.

    Raised for several related things, all of which are *valid* envelopes naming
    a capability that does not exist yet:

    * a payload type this build cannot materialize at all — it fetches no URLs,
      and it reads no images, video, or arbitrary files;
    * a ``WEBPAGE`` payload whose material this build will not reduce to one
      raw original. Phase 2 PR 1 ingests the HTML-backed form only, so a webpage
      backed by ``text`` instead, or one carrying ``html`` *alongside* ``text``
      or ``file_ref``, is refused rather than resolved. A capture stores one raw
      original; choosing between submitted representations would durably discard
      one of them and call it success;
    * a ``DOCUMENT`` payload outside the one form Phase 3 PR 1 ingests — a
      staged ``application/pdf`` raw object and nothing else. A document backed
      by ``text``, one carrying ``file_ref`` alongside ``text`` or ``html``, one
      declaring no MIME type, and one declaring a MIME type other than
      ``application/pdf`` are all refused for the same reason the webpage cases
      are: this build will not choose between submitted representations, and it
      will not claim to parse a format it has no processor for;
    * a ``DOCUMENT`` ``file_ref`` that is not a UniMem raw object reference.
      This build resolves ``sha256:<digest>`` and nothing else — not a
      filesystem path, a ``file://`` URL, an HTTP URL, or a bucket key. The
      envelope is valid and a later phase may well understand more reference
      schemes; this one understands the store's own.

    In every case the envelope is perfectly good — a later phase will accept the
    identical envelope unchanged — which is exactly what separates this from
    :class:`InvalidCaptureEnvelopeError`. Whether the referenced bytes are
    actually *there* is a different question again, and
    :class:`CaptureMaterialUnavailableError` is its answer.
    """


class InvalidCaptureEnvelopeError(CaptureIntakeError):
    """The envelope is internally inconsistent and cannot be materialized.

    Raised for a ``TEXT`` payload that carries no text, for a ``WEBPAGE``
    payload that carries neither ``html`` nor ``text``, and for a ``DOCUMENT``
    payload that carries neither ``file_ref`` nor ``text``. All three types
    *are* supported, so this is not an unsupported-payload error: the envelope
    contradicts its own contract, and no future phase will accept it as it
    stands.

    A well-formed ``CapturePayload`` cannot reach any of those states — the
    contract's own validator requires text for a text payload, html-or-text for
    a webpage one, and file_ref-or-text for a document. They are reachable
    because Phase 0A documents that an
    assignment rejected by a model-level validator has already been written, so
    a caller can hold an envelope that no longer satisfies its own invariants.
    Intake refuses it in its own vocabulary rather than failing somewhere
    further in.
    """


class CaptureMaterialUnavailableError(CaptureIntakeError):
    """The envelope names staged material this store cannot currently produce.

    Raised for one thing only: a ``file_ref`` that *is* a well-formed UniMem raw
    object reference, and that names no object the raw store holds. The envelope
    is valid, the reference is understood, and the capability exists — the bytes
    it points at simply are not there.

    It is deliberately neither of the other two. It is not an unsupported
    payload: this build handles exactly this shape, and resubmitting the
    identical envelope after staging the bytes succeeds. It is not an invalid
    envelope: nothing in it contradicts the contract, and no later phase would
    reject it for its shape.

    It is equally not a storage failure. The raw store answered the question it
    was asked — *is this object here?* — correctly and completely. Reporting a
    truthful "no" as a
    :class:`~core.storage.errors.RawObjectStoreError` would tell a caller the
    store is unavailable and to try again later, when the thing to do is stage
    the material first.

    The reference itself is never echoed. A caller may submit anything as a
    ``file_ref``, including a local path, and a refusal is not a reason to
    repeat it back into a message or a log.
    """
