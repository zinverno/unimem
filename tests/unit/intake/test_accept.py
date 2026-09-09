"""What accepting a text capture does, and in what order.

The order is the substance of this phase: the receipt is durable before any
byte is stored, and the reference to those bytes is durable after. Everything
here is asserted through the ports.
"""

import inspect

import pytest

from core.contracts import (
    CaptureEnvelope,
    CapturePayloadType,
    CaptureRecord,
    CaptureSourceType,
    CaptureStatus,
)
from core.intake import CaptureIntake, utc_now
from core.persistence import CaptureRecordStore
from core.storage import RawObjectStore
from tests.unit.intake.builders import RECEIVED_AT, TEXT, UPDATED_AT, make_envelope
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore


def test_accept_returns_a_stored_capture_record(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert isinstance(accepted, CaptureRecord)
    assert accepted.status is CaptureStatus.STORED


def test_the_capture_record_keeps_the_envelope_id(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """The submitter named the capture event; intake does not rename it."""
    accepted = intake.accept(envelope)

    assert accepted.id == envelope.id


def test_source_and_payload_type_are_carried_over(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.source == envelope.source
    assert accepted.source.type is CaptureSourceType.API
    assert accepted.source.provider == "cli"
    assert accepted.source.url == "https://example.com/notes"
    assert accepted.payload_type is CapturePayloadType.TEXT


def test_the_stored_record_references_the_raw_object(
    intake: CaptureIntake, envelope: CaptureEnvelope, raw_store: FakeRawObjectStore
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.raw_object is not None
    assert raw_store.read_bytes(accepted.raw_object) == TEXT.encode("utf-8")


def test_the_stored_record_carries_no_error(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    assert intake.accept(envelope).error is None


def test_the_receipt_is_durable_before_any_byte_is_stored(
    intake: CaptureIntake, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """The whole point of the ordering: no orphaned bytes without a receipt."""
    intake.accept(envelope)

    assert journal == ["records.create", "raw.store_bytes", "records.replace"]


def test_the_first_record_is_received_with_nothing_filled_in(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    intake.accept(envelope)

    (received,) = record_store.created
    assert received.status is CaptureStatus.RECEIVED
    assert received.raw_object is None
    assert received.updated_at is None
    assert received.error is None
    assert received.id == envelope.id


def test_the_stored_record_replaces_the_receipt(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)

    (replaced,) = record_store.replaced
    assert replaced == accepted
    assert len(record_store.created) == 1


def test_what_is_persisted_equals_what_is_returned(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)

    assert record_store.get(envelope.id) == accepted


def test_the_returned_record_revalidates_on_its_own(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """It is a fully valid contract instance, not a half-built one."""
    accepted = intake.accept(envelope)

    assert CaptureRecord.model_validate(accepted.model_dump()) == accepted
    assert CaptureRecord.model_validate_json(accepted.model_dump_json()) == accepted


def test_timestamps_come_from_the_injected_clock(
    intake: CaptureIntake, envelope: CaptureEnvelope, clock: FakeClock
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.received_at == RECEIVED_AT
    assert accepted.updated_at == UPDATED_AT
    assert clock.reads == [RECEIVED_AT, UPDATED_AT]


def test_received_at_is_the_intake_clock_not_the_capture_time(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """``received_at`` is when UniMem accepted it, not when the user captured it."""
    accepted = intake.accept(envelope)

    assert accepted.received_at != envelope.context.captured_at
    assert accepted.received_at == RECEIVED_AT


def test_updated_at_is_never_earlier_than_received_at(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.updated_at is not None
    assert accepted.updated_at >= accepted.received_at


def test_the_clock_is_read_exactly_twice(
    intake: CaptureIntake, envelope: CaptureEnvelope, clock: FakeClock
) -> None:
    intake.accept(envelope)

    assert len(clock.reads) == 2


def test_the_default_clock_is_timezone_aware_utc() -> None:
    now = utc_now()

    offset = now.utcoffset()
    assert now.tzinfo is not None
    assert offset is not None
    assert offset.total_seconds() == 0


def test_the_default_clock_is_used_when_none_is_injected(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore
) -> None:
    accepted = CaptureIntake(raw_store, record_store).accept(make_envelope())

    assert accepted.received_at.tzinfo is not None
    assert accepted.updated_at is not None


def test_accept_takes_only_the_envelope() -> None:
    """No options, no context object, no per-call configuration."""
    parameters = inspect.signature(CaptureIntake.accept).parameters

    assert list(parameters) == ["self", "envelope"]


def test_intake_depends_only_on_the_two_ports(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore, clock: FakeClock
) -> None:
    """Bound through the protocols — mypy checks this too, which is the point."""
    raw_port: RawObjectStore = raw_store
    record_port: CaptureRecordStore = record_store

    accepted = CaptureIntake(raw_port, record_port, now=clock).accept(make_envelope())

    assert accepted.status is CaptureStatus.STORED


def test_the_envelope_is_not_modified(intake: CaptureIntake, envelope: CaptureEnvelope) -> None:
    before = envelope.model_copy(deep=True)

    intake.accept(envelope)

    assert envelope == before


def test_the_returned_record_does_not_share_state_with_the_envelope(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """Nested models are handed over as copies, not aliased into the result."""
    accepted = intake.accept(envelope)

    accepted.source.provider = "changed"

    assert envelope.source.provider == "cli"


def test_the_receipt_is_not_mutated_into_the_stored_record(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    """Two snapshots are built; the first is never edited in place."""
    accepted = intake.accept(envelope)

    (received,) = record_store.created
    assert received is not accepted
    assert received.status is CaptureStatus.RECEIVED
    assert received.raw_object is None
    assert received.updated_at is None


def test_the_two_snapshots_do_not_share_nested_state(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)
    (received,) = record_store.created

    accepted.source.provider = "changed"

    assert received.source.provider == "cli"


@pytest.mark.parametrize("status", [CaptureStatus.RECEIVED, CaptureStatus.STORED])
def test_only_two_lifecycle_states_are_ever_written(
    intake: CaptureIntake,
    envelope: CaptureEnvelope,
    record_store: FakeCaptureRecordStore,
    status: CaptureStatus,
) -> None:
    """No queued, processing, complete, partial, or failed comes out of intake."""
    intake.accept(envelope)

    written = [record.status for record in record_store.created + record_store.replaced]
    assert set(written) == {CaptureStatus.RECEIVED, CaptureStatus.STORED}
    assert status in written
