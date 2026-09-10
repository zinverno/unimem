"""Errors raised by capture intake.

The hierarchy is deliberately tiny, and it draws exactly one distinction:
whether the envelope is fine and this phase is not yet ready for it, or the
envelope itself is inconsistent. A caller does different things with those —
wait for a later phase, or fix the submission.

Everything else that can go wrong belongs to a store, and those errors keep
their own types: a caller deciding whether to retry needs to know that the
*raw store* failed, not merely that "intake failed".
"""


class CaptureIntakeError(Exception):
    """Base class for every error intake raises on its own behalf."""


class UnsupportedCapturePayloadError(CaptureIntakeError):
    """This build does not handle envelopes of this payload type or shape.

    Raised for two related things, both of which are *valid* envelopes naming a
    capability that does not exist yet:

    * a payload type this build cannot materialize at all — it reads no files,
      resolves no ``file_ref``, and fetches no URLs;
    * a ``WEBPAGE`` payload whose material this build will not reduce to one
      raw original. Phase 2 PR 1 ingests the HTML-backed form only, so a webpage
      backed by ``text`` instead, or one carrying ``html`` *alongside* ``text``
      or ``file_ref``, is refused rather than resolved. A capture stores one raw
      original; choosing between submitted representations would durably discard
      one of them and call it success.

    In every case the envelope is perfectly good — a later phase will accept the
    identical envelope unchanged — which is exactly what separates this from
    :class:`InvalidCaptureEnvelopeError`.
    """


class InvalidCaptureEnvelopeError(CaptureIntakeError):
    """The envelope is internally inconsistent and cannot be materialized.

    Raised for a ``TEXT`` payload that carries no text, and for a ``WEBPAGE``
    payload that carries neither ``html`` nor ``text``. Both types *are*
    supported, so this is not an unsupported-payload error: the envelope
    contradicts its own contract, and no future phase will accept it as it
    stands.

    A well-formed ``CapturePayload`` cannot reach either state — the contract's
    own validator requires text for a text payload and html-or-text for a
    webpage one. They are reachable because Phase 0A documents that an
    assignment rejected by a model-level validator has already been written, so
    a caller can hold an envelope that no longer satisfies its own invariants.
    Intake refuses it in its own vocabulary rather than failing somewhere
    further in.
    """
