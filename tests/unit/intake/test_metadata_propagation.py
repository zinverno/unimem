"""Capture metadata surviving intake, durably and without aliasing.

Phase 0F accepted context, intent, and title and then dropped them, because
``CaptureRecord`` had nowhere to put them. Schema 0.2 gave them a home, and
these tests say they arrive there: in the *first* durable snapshot, before any
byte is written, copied rather than shared, and never in the raw bytes.
"""

import pytest

from core.contracts import (
    CaptureEnvelope,
    CaptureStatus,
    IntentAction,
)
from core.intake import CaptureIntake
from core.persistence import CaptureRecordPersistenceError
from core.storage import RawObjectWriteError
from tests.unit.intake.builders import (
    CAPTURED_AT,
    RECEIVED_AT,
    UPDATED_AT,
    make_envelope,
    make_payload,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore


def test_the_receipt_already_carries_the_metadata(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    """Durable from the RECEIVED snapshot, not only once the bytes land."""
    intake.accept(envelope)

    (received,) = record_store.created
    assert received.status is CaptureStatus.RECEIVED
    assert received.context == envelope.context
    assert received.intent == envelope.intent
    assert received.title == envelope.payload.title


def test_the_metadata_is_durable_before_the_raw_write(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    intake.accept(envelope)

    persisted_receipt = record_store.created[0]
    assert persisted_receipt.raw_object is None
    assert persisted_receipt.context is not None


def test_the_stored_snapshot_preserves_the_metadata_unchanged(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)

    (received,) = record_store.created
    assert accepted.context == received.context
    assert accepted.intent == received.intent
    assert accepted.title == received.title


def test_context_values_are_carried_over_exactly(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.context is not None
    assert accepted.context.captured_at == CAPTURED_AT
    assert accepted.context.device == "laptop"
    assert accepted.context.application == "terminal"


def test_intent_values_are_carried_over_exactly(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.intent is not None
    assert accepted.intent.action is IntentAction.SAVE
    assert accepted.intent.collection == "reading"
    assert accepted.intent.tags == ["architecture"]


def test_the_title_comes_from_the_payload(intake: CaptureIntake) -> None:
    accepted = intake.accept(make_envelope(payload=make_payload(title="A Specific Title")))

    assert accepted.title == "A Specific Title"


def test_an_absent_title_stays_absent(intake: CaptureIntake) -> None:
    """Nothing is fabricated from the id, the text, or the URL."""
    accepted = intake.accept(make_envelope(payload=make_payload(title=None)))

    assert accepted.title is None


def test_an_absent_intent_stays_absent(intake: CaptureIntake) -> None:
    accepted = intake.accept(make_envelope(intent=None))

    assert accepted.intent is None
    assert accepted.context is not None


def test_captured_at_never_becomes_received_at(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """Two different facts, and intake keeps both."""
    accepted = intake.accept(envelope)

    assert accepted.received_at == RECEIVED_AT
    assert accepted.context is not None
    assert accepted.context.captured_at == CAPTURED_AT
    assert accepted.received_at != accepted.context.captured_at


def test_the_record_is_at_the_current_schema_version(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    assert intake.accept(envelope).schema_version == "0.2"


def test_a_legacy_envelope_produces_a_current_record(intake: CaptureIntake) -> None:
    """A 0.1 envelope is still accepted, and its metadata still becomes durable."""
    data = make_envelope().model_dump(mode="json")
    legacy = CaptureEnvelope.model_validate(data | {"schema_version": "0.1"})

    accepted = intake.accept(legacy)

    assert legacy.schema_version == "0.1"
    assert accepted.schema_version == "0.2"
    assert accepted.context == legacy.context
    assert accepted.intent == legacy.intent
    assert accepted.title == legacy.payload.title


def test_the_envelope_is_not_rewritten(intake: CaptureIntake) -> None:
    data = make_envelope().model_dump(mode="json")
    legacy = CaptureEnvelope.model_validate(data | {"schema_version": "0.1"})
    before = legacy.model_copy(deep=True)

    intake.accept(legacy)

    assert legacy == before
    assert legacy.schema_version == "0.1"


def test_the_returned_record_does_not_share_context_with_the_envelope(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.context is not None
    accepted.context.device = "changed"

    assert envelope.context.device == "laptop"


def test_mutating_the_envelope_context_after_accept_changes_nothing(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)

    envelope.context.device = "changed after the fact"

    assert accepted.context is not None
    assert accepted.context.device == "laptop"
    persisted = record_store.get(envelope.id)
    assert persisted.context is not None
    assert persisted.context.device == "laptop"


def test_mutating_the_envelope_tags_after_accept_changes_nothing(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    """In-place mutation of a list never reaches a validator, so it must not alias."""
    accepted = intake.accept(envelope)
    assert envelope.intent is not None

    envelope.intent.tags.append("smuggled")

    assert accepted.intent is not None
    assert accepted.intent.tags == ["architecture"]
    persisted = record_store.get(envelope.id)
    assert persisted.intent is not None
    assert persisted.intent.tags == ["architecture"]


def test_the_two_snapshots_do_not_share_intent_tags(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)
    (received,) = record_store.created

    assert received.intent is not None
    assert accepted.intent is not None
    assert received.intent.tags is not accepted.intent.tags
    assert received.intent is not accepted.intent
    assert received.context is not accepted.context


def test_mutating_the_returned_tags_leaves_the_receipt_alone(
    intake: CaptureIntake, envelope: CaptureEnvelope, record_store: FakeCaptureRecordStore
) -> None:
    accepted = intake.accept(envelope)
    (received,) = record_store.created

    assert accepted.intent is not None
    accepted.intent.tags.append("later")

    assert received.intent is not None
    assert received.intent.tags == ["architecture"]
    assert envelope.intent is not None
    assert envelope.intent.tags == ["architecture"]


def test_a_raw_store_failure_leaves_the_metadata_durable(
    record_store: FakeCaptureRecordStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    """The stranded receipt still knows when, where, and why it was captured."""
    failing = FakeRawObjectStore(journal, fail_with=RawObjectWriteError("disk gave up"))

    with pytest.raises(RawObjectWriteError):
        CaptureIntake(failing, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
            envelope
        )

    stranded = record_store.get(envelope.id)
    assert stranded.status is CaptureStatus.RECEIVED
    assert stranded.context is not None
    assert stranded.context.captured_at == CAPTURED_AT
    assert stranded.intent is not None
    assert stranded.title == "A note"


def test_a_replace_failure_leaves_the_metadata_durable(
    raw_store: FakeRawObjectStore, envelope: CaptureEnvelope, journal: list[str]
) -> None:
    failing = FakeCaptureRecordStore(
        journal, fail_replace_with=CaptureRecordPersistenceError("database went away")
    )

    with pytest.raises(CaptureRecordPersistenceError):
        CaptureIntake(raw_store, failing, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(envelope)

    stranded = failing.get(envelope.id)
    assert stranded.status is CaptureStatus.RECEIVED
    assert stranded.context is not None
    assert stranded.intent is not None
    assert stranded.title == "A note"


def test_two_captures_of_one_text_keep_their_own_metadata(
    intake: CaptureIntake,
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
) -> None:
    """Same bytes, one raw object, two records — with different capture facts."""
    first = intake.accept(make_envelope(id="cap_a", payload=make_payload(title="From the browser")))
    second = CaptureIntake(raw_store, record_store, now=FakeClock(RECEIVED_AT, UPDATED_AT)).accept(
        make_envelope(
            id="cap_b",
            payload=make_payload(title="From the CLI"),
            intent=None,
        )
    )

    assert first.raw_object is not None
    assert second.raw_object is not None
    assert first.raw_object.sha256 == second.raw_object.sha256
    assert record_store.get("cap_a").title == "From the browser"
    assert record_store.get("cap_b").title == "From the CLI"
    assert record_store.get("cap_a").intent is not None
    assert record_store.get("cap_b").intent is None
