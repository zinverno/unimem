"""What intake does when something goes wrong, which is deliberately little.

Two rules run through all of it. Store errors keep their own types — intake
does not flatten a raw-store failure and a persistence failure into one
"intake failed", because a caller deciding whether to retry needs to know which
one broke. And nothing is fabricated: there is no invented ``FAILED`` status
and no rollback, because the two stores share no transaction and the raw store
has no delete.
"""

import pytest
from pydantic import ValidationError

from core.contracts import CaptureEnvelope, CapturePayloadType, CaptureStatus
from core.intake import (
    CaptureIntake,
    CaptureIntakeError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
)
from core.storage import RawObjectWriteError
from tests.unit.intake.builders import (
    RECEIVED_AT,
    TEXT,
    UPDATED_AT,
    make_envelope,
    make_payload,
    make_unsupported_envelope,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore

#: Payload types this build cannot materialize at all. ``WEBPAGE`` left this
#: list in Phase 2 PR 1 and ``DOCUMENT`` in Phase 3 PR 1, each when it became a
#: supported type; which *shapes* of them are supported is asked in
#: ``test_webpage_materialization.py`` and ``test_document_materialization.py``
#: instead.
UNSUPPORTED = [
    CapturePayloadType.IMAGE,
    CapturePayloadType.VIDEO,
    CapturePayloadType.FILE,
    CapturePayloadType.URL,
]


@pytest.mark.parametrize("payload_type", UNSUPPORTED, ids=lambda t: t.value)
def test_an_unsupported_payload_is_refused(
    intake: CaptureIntake, payload_type: CapturePayloadType
) -> None:
    with pytest.raises(UnsupportedCapturePayloadError, match=payload_type.value):
        intake.accept(make_unsupported_envelope(payload_type))


@pytest.mark.parametrize("payload_type", UNSUPPORTED, ids=lambda t: t.value)
def test_an_unsupported_payload_has_no_side_effects(
    intake: CaptureIntake,
    payload_type: CapturePayloadType,
    journal: list[str],
    clock: FakeClock,
) -> None:
    """No clock read, no byte written, no record created: nothing happened."""
    with pytest.raises(UnsupportedCapturePayloadError):
        intake.accept(make_unsupported_envelope(payload_type))

    assert journal == []
    assert clock.reads == []


def test_unsupported_payload_errors_share_one_base(intake: CaptureIntake) -> None:
    with pytest.raises(CaptureIntakeError):
        intake.accept(make_unsupported_envelope(CapturePayloadType.IMAGE))


def emptied_text_envelope() -> CaptureEnvelope:
    """An envelope whose text payload no longer carries any text.

    Phase 0A documents that an assignment rejected by a model-level validator
    has already been written, so a caller really can end up holding one of
    these. It is the only way to reach the state, and it is why the guard
    exists.
    """
    envelope = make_envelope()
    with pytest.raises(ValidationError):
        envelope.payload.text = None
    assert envelope.payload.text is None
    return envelope


def test_a_text_payload_emptied_after_construction_is_refused(
    intake: CaptureIntake, journal: list[str], clock: FakeClock
) -> None:
    """The envelope contradicts its own contract, so intake refuses it."""
    with pytest.raises(InvalidCaptureEnvelopeError, match="carries no text"):
        intake.accept(emptied_text_envelope())

    assert journal == []
    assert clock.reads == []


def test_an_inconsistent_envelope_is_not_an_unsupported_payload(
    intake: CaptureIntake,
) -> None:
    """``TEXT`` is supported. This envelope is broken, which is a different thing.

    The distinction is what a caller does next: an unsupported payload type
    will work unchanged in a later phase, while this envelope never will.
    """
    with pytest.raises(InvalidCaptureEnvelopeError) as raised:
        intake.accept(emptied_text_envelope())

    assert not isinstance(raised.value, UnsupportedCapturePayloadError)


def test_an_unsupported_payload_is_not_an_invalid_envelope(intake: CaptureIntake) -> None:
    """The envelope is perfectly valid; this phase simply cannot handle it."""
    with pytest.raises(UnsupportedCapturePayloadError) as raised:
        intake.accept(make_unsupported_envelope(CapturePayloadType.IMAGE))

    assert not isinstance(raised.value, InvalidCaptureEnvelopeError)


def test_an_inconsistent_envelope_error_shares_the_intake_base(
    intake: CaptureIntake,
) -> None:
    with pytest.raises(CaptureIntakeError):
        intake.accept(emptied_text_envelope())


def test_a_duplicate_capture_id_is_an_error_not_a_quiet_success(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore
) -> None:
    """No idempotency, no replay, no retry token in this phase."""
    CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
        make_envelope()
    )

    with pytest.raises(CaptureRecordAlreadyExistsError):
        CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
            make_envelope()
        )


def test_a_duplicate_fails_before_any_byte_is_stored(
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
    journal: list[str],
) -> None:
    CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
        make_envelope()
    )
    journal.clear()

    with pytest.raises(CaptureRecordAlreadyExistsError):
        CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT)).accept(
            make_envelope(payload=make_payload(text="different text entirely"))
        )

    assert journal == ["records.create"]
    assert len(raw_store.writes) == 1


def test_a_duplicate_does_not_overwrite_the_stored_record(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore
) -> None:
    first = CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
        make_envelope()
    )

    with pytest.raises(CaptureRecordAlreadyExistsError):
        CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT)).accept(
            make_envelope(payload=make_payload(text="different text entirely"))
        )

    assert record_store.get(first.id) == first


def test_a_raw_store_failure_keeps_its_own_type(
    record_store: FakeCaptureRecordStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """Not flattened into a generic intake error: the caller needs the difference."""
    failing = FakeRawObjectStore(journal, fail_with=RawObjectWriteError("disk gave up"))
    intake = CaptureIntake(failing, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT))

    with pytest.raises(RawObjectWriteError):
        intake.accept(envelope)


def test_a_raw_store_failure_leaves_a_durable_received_record(
    record_store: FakeCaptureRecordStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """The receipt is the point: the system knows a capture was accepted."""
    failing = FakeRawObjectStore(journal, fail_with=RawObjectWriteError("disk gave up"))

    with pytest.raises(RawObjectWriteError):
        CaptureIntake(failing, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
            envelope
        )

    stranded = record_store.get(envelope.id)
    assert stranded.status is CaptureStatus.RECEIVED
    assert stranded.raw_object is None
    assert stranded.updated_at is None


def test_a_raw_store_failure_does_not_reach_replace(
    record_store: FakeCaptureRecordStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    failing = FakeRawObjectStore(journal, fail_with=RawObjectWriteError("disk gave up"))

    with pytest.raises(RawObjectWriteError):
        CaptureIntake(failing, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
            envelope
        )

    assert journal == ["records.create", "raw.store_bytes"]
    assert record_store.replaced == []


def test_a_raw_store_failure_does_not_fabricate_a_failed_status(
    record_store: FakeCaptureRecordStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """``FAILED`` would be a lie: the capture is incomplete, not doomed."""
    failing = FakeRawObjectStore(journal, fail_with=RawObjectWriteError("disk gave up"))

    with pytest.raises(RawObjectWriteError):
        CaptureIntake(failing, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
            envelope
        )

    assert record_store.get(envelope.id).status is not CaptureStatus.FAILED
    assert record_store.get(envelope.id).error is None


def test_a_replace_failure_keeps_its_own_type(
    raw_store: FakeRawObjectStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    failing = FakeCaptureRecordStore(
        journal, fail_replace_with=CaptureRecordPersistenceError("database went away")
    )

    with pytest.raises(CaptureRecordPersistenceError):
        CaptureIntake(raw_store, failing, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(envelope)


def test_a_replace_failure_leaves_received_while_the_bytes_exist(
    raw_store: FakeRawObjectStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """The documented cross-store gap: the raw object outlives the failed update."""
    failing = FakeCaptureRecordStore(
        journal, fail_replace_with=CaptureRecordPersistenceError("database went away")
    )

    with pytest.raises(CaptureRecordPersistenceError):
        CaptureIntake(raw_store, failing, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(envelope)

    stranded = failing.get(envelope.id)
    assert stranded.status is CaptureStatus.RECEIVED
    assert stranded.raw_object is None
    assert len(raw_store.writes) == 1


def test_a_replace_failure_does_not_delete_the_raw_object(
    raw_store: FakeRawObjectStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """There is no rollback to perform: the raw store has no delete, by design."""
    failing = FakeCaptureRecordStore(
        journal, fail_replace_with=CaptureRecordPersistenceError("database went away")
    )

    with pytest.raises(CaptureRecordPersistenceError):
        CaptureIntake(raw_store, failing, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(envelope)

    assert raw_store.writes[0][0] == TEXT.encode("utf-8")
    assert journal == ["records.create", "raw.store_bytes", "records.replace"]


def test_a_missing_receipt_at_replace_time_keeps_its_own_type(
    raw_store: FakeRawObjectStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """Something else deleted the row mid-flight; intake does not re-create it."""
    failing = FakeCaptureRecordStore(
        journal, fail_replace_with=CaptureRecordNotFoundError("someone removed it")
    )

    with pytest.raises(CaptureRecordNotFoundError):
        CaptureIntake(raw_store, failing, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(envelope)
