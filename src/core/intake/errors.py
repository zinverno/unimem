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
    """This phase does not handle envelopes of this payload type.

    Raised when the payload is not ``CapturePayloadType.TEXT``: Phase 0F reads
    no files, resolves no ``file_ref``, and fetches no URLs. The envelope is
    perfectly valid — it just names a capability that does not exist yet, so a
    later phase will accept the identical envelope unchanged.
    """


class InvalidCaptureEnvelopeError(CaptureIntakeError):
    """The envelope is internally inconsistent and cannot be materialized.

    Raised for a ``TEXT`` payload that carries no text. ``TEXT`` *is*
    supported, so this is not an unsupported-payload error: the envelope
    contradicts its own contract, and no future phase will accept it as it
    stands.

    A well-formed ``CapturePayload`` cannot reach this state — the contract's
    own validator requires text for a text payload. It is reachable because
    Phase 0A documents that an assignment rejected by a model-level validator
    has already been written, so a caller can hold an envelope that no longer
    satisfies its own invariants. Intake refuses it in its own vocabulary
    rather than failing somewhere further in.
    """
