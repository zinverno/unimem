"""Intake against the real backends.

The unit tests prove intake talks to the ports. This one proves the ports it
talks to are satisfied by the backends the system actually ships: a local
content-addressed raw store on a real filesystem, and a SQLite capture record
store in a real file. Nothing is faked, and the assertions are the ones a
caller cares about — the bytes come back exactly, and the record is still there
after every object that wrote it is gone.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.contracts import CaptureRecord, CaptureStatus, IntentAction, RawObjectRef
from core.intake import CaptureIntake
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    SqliteCaptureRecordStore,
)
from core.storage import LocalRawObjectStore, RawObjectWriteError
from tests.unit.intake.builders import (
    AWKWARD_TEXT,
    CAPTURED_AT,
    TEXT,
    make_envelope,
    make_payload,
)

RECEIVED_AT = datetime(2026, 8, 9, 10, 11, 12, 130000, tzinfo=UTC)
UPDATED_AT = datetime(2026, 8, 9, 10, 11, 13, tzinfo=UTC)


class SteppedClock:
    """Hands out prepared instants so timestamps are assertable."""

    def __init__(self, *instants: datetime) -> None:
        self._instants = list(instants)
        self._reads = 0

    def __call__(self) -> datetime:
        instant = self._instants[min(self._reads, len(self._instants) - 1)]
        self._reads += 1
        return instant


class FailingRawObjectStore(LocalRawObjectStore):
    """A real local store that refuses to write, to strand a receipt."""

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        raise RawObjectWriteError("disk gave up")


@pytest.fixture
def raw_root(tmp_path: Path) -> Path:
    return tmp_path / "objects"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "captures.sqlite3"


@pytest.fixture
def intake(raw_root: Path, database: Path) -> CaptureIntake:
    return CaptureIntake(
        LocalRawObjectStore(raw_root),
        SqliteCaptureRecordStore(database),
        now=SteppedClock(RECEIVED_AT, UPDATED_AT),
    )


def test_a_text_capture_becomes_a_stored_record_and_real_bytes(
    intake: CaptureIntake, raw_root: Path, database: Path
) -> None:
    accepted = intake.accept(make_envelope())

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert persisted == accepted
    assert persisted.status is CaptureStatus.STORED
    assert persisted.raw_object is not None
    assert LocalRawObjectStore(raw_root).read_bytes(persisted.raw_object) == TEXT.encode("utf-8")


def test_the_record_outlives_every_object_that_wrote_it(
    intake: CaptureIntake, database: Path
) -> None:
    accepted = intake.accept(make_envelope())
    del intake

    assert SqliteCaptureRecordStore(database).get(accepted.id) == accepted


def test_awkward_text_survives_the_whole_round_trip(
    intake: CaptureIntake, raw_root: Path, database: Path
) -> None:
    """Through encoding, a content-addressed file, SQLite, and back."""
    accepted = intake.accept(make_envelope(payload=make_payload(text=AWKWARD_TEXT)))

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert persisted.raw_object is not None
    data = LocalRawObjectStore(raw_root).read_bytes(persisted.raw_object)
    assert data == AWKWARD_TEXT.encode("utf-8")
    assert data.decode("utf-8") == AWKWARD_TEXT


def test_the_stored_reference_is_content_addressed(intake: CaptureIntake, raw_root: Path) -> None:
    accepted = intake.accept(make_envelope())

    digest = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert accepted.raw_object is not None
    assert accepted.raw_object.sha256 == digest
    assert accepted.raw_object.ref == f"sha256:{digest}"
    assert LocalRawObjectStore(raw_root).exists(accepted.raw_object)


def test_the_persisted_record_revalidates(intake: CaptureIntake, database: Path) -> None:
    accepted = intake.accept(make_envelope())

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert CaptureRecord.model_validate(persisted.model_dump()) == accepted


def test_timestamps_survive_the_database(intake: CaptureIntake, database: Path) -> None:
    accepted = intake.accept(make_envelope())

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert persisted.received_at == RECEIVED_AT
    assert persisted.updated_at == UPDATED_AT


def test_two_captures_of_the_same_text_share_one_stored_object(
    raw_root: Path, database: Path
) -> None:
    """Raw storage deduplicates the bytes; the captures stay two records."""
    raw_store = LocalRawObjectStore(raw_root)
    record_store = SqliteCaptureRecordStore(database)
    first = CaptureIntake(
        raw_store, record_store, now=SteppedClock(RECEIVED_AT, UPDATED_AT)
    ).accept(make_envelope(id="cap_a"))
    second = CaptureIntake(
        raw_store, record_store, now=SteppedClock(RECEIVED_AT, UPDATED_AT)
    ).accept(make_envelope(id="cap_b"))

    assert first.raw_object is not None
    assert second.raw_object is not None
    assert first.raw_object.ref == second.raw_object.ref
    assert record_store.get("cap_a").id == "cap_a"
    assert record_store.get("cap_b").id == "cap_b"
    assert len(finalized(raw_root)) == 1


def test_a_duplicate_capture_id_is_refused_by_the_real_database(
    raw_root: Path, database: Path
) -> None:
    raw_store = LocalRawObjectStore(raw_root)
    record_store = SqliteCaptureRecordStore(database)
    first = CaptureIntake(
        raw_store, record_store, now=SteppedClock(RECEIVED_AT, UPDATED_AT)
    ).accept(make_envelope())

    with pytest.raises(CaptureRecordAlreadyExistsError):
        CaptureIntake(raw_store, record_store, now=SteppedClock(RECEIVED_AT)).accept(
            make_envelope(payload=make_payload(text="a different note"))
        )

    assert record_store.get(first.id) == first
    assert len(finalized(raw_root)) == 1


def finalized(root: Path) -> list[Path]:
    """Every finalized raw object under a store root."""
    objects = root / "sha256"
    return sorted(path for path in objects.rglob("*") if path.is_file())


def test_capture_metadata_survives_the_whole_stack(intake: CaptureIntake, database: Path) -> None:
    """Envelope context, intent, and title, through intake and real SQLite."""
    accepted = intake.accept(make_envelope())

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert persisted == accepted
    assert persisted.schema_version == "0.2"
    assert persisted.context is not None
    assert persisted.context.captured_at == CAPTURED_AT
    assert persisted.context.device == "laptop"
    assert persisted.context.application == "terminal"
    assert persisted.intent is not None
    assert persisted.intent.action is IntentAction.SAVE
    assert persisted.intent.collection == "reading"
    assert persisted.intent.tags == ["architecture"]
    assert persisted.title == "A note"


def test_the_metadata_is_durable_before_the_bytes_are(raw_root: Path, database: Path) -> None:
    """A raw-store failure strands a receipt that still knows its capture facts."""
    record_store = SqliteCaptureRecordStore(database)
    failing = FailingRawObjectStore(raw_root)

    with pytest.raises(RawObjectWriteError):
        CaptureIntake(failing, record_store, now=SteppedClock(RECEIVED_AT, UPDATED_AT)).accept(
            make_envelope()
        )

    stranded = SqliteCaptureRecordStore(database).get("cap_intake_01")
    assert stranded.status is CaptureStatus.RECEIVED
    assert stranded.raw_object is None
    assert stranded.context is not None
    assert stranded.context.captured_at == CAPTURED_AT
    assert stranded.title == "A note"


def test_captured_at_and_received_at_stay_distinct_in_the_database(
    intake: CaptureIntake, database: Path
) -> None:
    accepted = intake.accept(make_envelope())

    persisted = SqliteCaptureRecordStore(database).get(accepted.id)
    assert persisted.received_at == RECEIVED_AT
    assert persisted.context is not None
    assert persisted.context.captured_at == CAPTURED_AT
    assert persisted.context.captured_at < persisted.received_at


def test_the_raw_bytes_hold_the_text_and_nothing_else(
    intake: CaptureIntake, raw_root: Path
) -> None:
    accepted = intake.accept(make_envelope())

    assert accepted.raw_object is not None
    data = LocalRawObjectStore(raw_root).read_bytes(accepted.raw_object)
    assert data == TEXT.encode("utf-8")
    for absent in (b"A note", b"laptop", b"terminal", b"reading", b"architecture"):
        assert absent not in data
