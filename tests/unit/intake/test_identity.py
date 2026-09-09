"""Capture identity at the intake boundary.

This is where a raw SHA-256 is most tempting and most wrong. ADR-003 drew the
line between "these bytes" and "this capture event"; intake is the first code
that holds both at once, so it is the first place the line could quietly be
crossed.
"""

from core.contracts import CaptureEnvelope, CaptureStatus
from core.intake import CaptureIntake
from tests.unit.intake.builders import TEXT, make_envelope, make_payload
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeRawObjectStore


def test_the_capture_id_is_the_envelope_id_verbatim(
    intake: CaptureIntake, record_store: FakeCaptureRecordStore
) -> None:
    """Not minted, not prefixed, not normalized."""
    accepted = intake.accept(make_envelope(id="cap_submitter_chose_this"))

    assert accepted.id == "cap_submitter_chose_this"
    assert record_store.get("cap_submitter_chose_this").id == "cap_submitter_chose_this"


def test_the_capture_id_is_not_the_raw_digest(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    accepted = intake.accept(envelope)

    assert accepted.raw_object is not None
    assert accepted.id != accepted.raw_object.sha256
    assert accepted.id != accepted.raw_object.ref
    assert accepted.id != accepted.raw_object.id


def test_the_capture_id_is_not_derived_from_the_content(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    """Changing the text changes the digest and leaves the capture id alone."""
    accepted = intake.accept(make_envelope(id="cap_fixed", payload=make_payload(text="one")))
    digest = accepted.raw_object.sha256 if accepted.raw_object else None

    other = CaptureIntake(raw_store, FakeCaptureRecordStore(), now=lambda: accepted.received_at)
    second = other.accept(make_envelope(id="cap_fixed", payload=make_payload(text="two")))

    assert second.id == accepted.id == "cap_fixed"
    assert second.raw_object is not None
    assert second.raw_object.sha256 != digest


def test_two_envelopes_with_the_same_text_are_two_captures(
    intake: CaptureIntake,
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
) -> None:
    """One raw object, two capture records — the ADR-003 layering, end to end."""
    first = intake.accept(make_envelope(id="cap_a", payload=make_payload(text=TEXT)))
    second = CaptureIntake(raw_store, record_store, now=lambda: first.received_at).accept(
        make_envelope(id="cap_b", payload=make_payload(text=TEXT))
    )

    assert first.id != second.id
    assert record_store.get("cap_a").id == "cap_a"
    assert record_store.get("cap_b").id == "cap_b"


def test_captures_of_identical_text_share_one_raw_object(
    intake: CaptureIntake,
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
) -> None:
    """Raw deduplication is exact and untouched; it does not collapse captures."""
    first = intake.accept(make_envelope(id="cap_a", payload=make_payload(text=TEXT)))
    second = CaptureIntake(raw_store, record_store, now=lambda: first.received_at).accept(
        make_envelope(id="cap_b", payload=make_payload(text=TEXT))
    )

    assert first.raw_object is not None
    assert second.raw_object is not None
    assert first.raw_object.sha256 == second.raw_object.sha256
    assert first.raw_object.ref == second.raw_object.ref
    assert raw_store.read_bytes(second.raw_object) == TEXT.encode("utf-8")


def test_both_captures_stay_independently_retrievable(
    intake: CaptureIntake,
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
) -> None:
    first = intake.accept(make_envelope(id="cap_a", payload=make_payload(text=TEXT)))
    CaptureIntake(raw_store, record_store, now=lambda: first.received_at).accept(
        make_envelope(id="cap_b", payload=make_payload(text=TEXT), source=first.source)
    )

    for capture_id in ("cap_a", "cap_b"):
        assert record_store.get(capture_id).status is CaptureStatus.STORED


def test_different_text_yields_different_raw_objects(
    intake: CaptureIntake,
    raw_store: FakeRawObjectStore,
    record_store: FakeCaptureRecordStore,
) -> None:
    first = intake.accept(make_envelope(id="cap_a", payload=make_payload(text="alpha")))
    second = CaptureIntake(raw_store, record_store, now=lambda: first.received_at).accept(
        make_envelope(id="cap_b", payload=make_payload(text="beta"))
    )

    assert first.raw_object is not None
    assert second.raw_object is not None
    assert first.raw_object.sha256 != second.raw_object.sha256
