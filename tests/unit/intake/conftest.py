"""Fixtures shared by the intake tests."""

import pytest

from core.contracts import CaptureEnvelope
from core.intake import CaptureIntake
from tests.unit.intake.builders import RECEIVED_AT, UPDATED_AT, make_envelope
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore


@pytest.fixture
def journal() -> list[str]:
    """The shared record of what both stores were asked to do, in order."""
    return []


@pytest.fixture
def raw_store(journal: list[str]) -> FakeRawObjectStore:
    return FakeRawObjectStore(journal)


@pytest.fixture
def record_store(journal: list[str]) -> FakeCaptureRecordStore:
    return FakeCaptureRecordStore(journal)


@pytest.fixture
def clock() -> FakeClock:
    """Two instants: the receipt, then the moment the bytes landed."""
    return FakeClock(RECEIVED_AT, UPDATED_AT)


@pytest.fixture
def intake(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore, clock: FakeClock
) -> CaptureIntake:
    return CaptureIntake(raw_store, record_store, now=clock)


@pytest.fixture
def envelope() -> CaptureEnvelope:
    return make_envelope()
