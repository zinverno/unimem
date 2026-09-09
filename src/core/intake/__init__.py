"""Intake: turning a submitted capture envelope into a stored capture.

Phase 0F answers one question — how does a valid inline-text
``CaptureEnvelope`` become an immutable raw object plus a durably registered
``CaptureRecord``?

::

    CaptureEnvelope(TEXT)
        |
    CaptureIntake ------> CaptureRecordStore   CaptureRecord(RECEIVED)
        |
        +---------------> RawObjectStore       the immutable original
        |
        +---------------> CaptureRecordStore   CaptureRecord(STORED)

This is the first orchestration in the system: the first code that calls the
components in an order and owns what happens when one of them fails. It stops
the moment the ``STORED`` record is durable. No processor runs, no
``ContentObject`` is built, nothing is rendered or exported, and there is no
HTTP, CLI, or browser surface.

The capture record's id *is* the envelope's id: a capture is an event the
submitter named, and intake does not rename it. A raw SHA-256 addresses bytes
and never becomes capture identity, so two envelopes with different ids and
identical text yield two capture records pointing at one deduplicated raw
object.

A duplicate capture id is an error, not a quietly successful retry: Phase 0F
has no idempotency keys, no replay, and no resume.
"""

from core.intake.errors import (
    CaptureIntakeError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.intake.service import TEXT_ENCODING, CaptureIntake, utc_now

__all__ = [
    "TEXT_ENCODING",
    "CaptureIntake",
    "CaptureIntakeError",
    "InvalidCaptureEnvelopeError",
    "UnsupportedCapturePayloadError",
    "utc_now",
]
