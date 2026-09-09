"""What ``create``, ``get``, and ``replace`` mean.

These are the semantics a caller is entitled to rely on, and every one of them
is asserted through the port rather than by looking at the database.
"""

import inspect
from pathlib import Path

import pytest

from core.contracts import CaptureRecord, CaptureStatus
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordNotFoundError,
    CaptureRecordStore,
    CaptureRecordStoreError,
    SqliteCaptureRecordStore,
)
from tests.unit.persistence.builders import UPDATED_AT, make_minimal_record, make_record


def test_sqlite_store_satisfies_the_port(
    store: SqliteCaptureRecordStore, record: CaptureRecord
) -> None:
    """Structural conformance, checked by the type checker as well as at runtime."""
    port: CaptureRecordStore = store
    port.create(record)

    assert port.get(record.id) == record


def test_the_port_is_exactly_three_methods() -> None:
    """No ``save``, no ``upsert``, no ``delete``, no ``list``, no ``search``."""
    methods = {
        name
        for name, _ in inspect.getmembers(CaptureRecordStore, inspect.isfunction)
        if not name.startswith("_")
    }

    assert methods == {"create", "get", "replace"}


def test_create_then_get_round_trips(store: SqliteCaptureRecordStore) -> None:
    record = make_record()
    store.create(record)

    assert store.get(record.id) == record


def test_a_minimal_record_round_trips(store: SqliteCaptureRecordStore) -> None:
    """Absent optional fields come back absent, not defaulted into place."""
    record = make_minimal_record()
    store.create(record)

    restored = store.get(record.id)

    assert restored == record
    assert restored.updated_at is None
    assert restored.raw_object is None
    assert restored.error is None


def test_get_returns_a_freshly_built_object_each_time(
    store: SqliteCaptureRecordStore, record: CaptureRecord
) -> None:
    """A record is reconstructed per call, not handed out from a cache."""
    store.create(record)

    first = store.get(record.id)
    second = store.get(record.id)

    assert first == second
    assert first is not second
    assert first is not record


def test_get_of_an_unknown_id_is_not_found(store: SqliteCaptureRecordStore) -> None:
    with pytest.raises(CaptureRecordNotFoundError):
        store.get("cap_never_stored")


def test_create_of_a_duplicate_id_raises(
    store: SqliteCaptureRecordStore, record: CaptureRecord
) -> None:
    store.create(record)

    with pytest.raises(CaptureRecordAlreadyExistsError):
        store.create(record)


def test_a_rejected_duplicate_does_not_overwrite(store: SqliteCaptureRecordStore) -> None:
    """The stored snapshot is the first one, untouched by the refused write."""
    first = make_record(status=CaptureStatus.STORED, error=None)
    store.create(first)
    intruder = make_record(id=first.id, status=CaptureStatus.FAILED, error="clobbered")

    with pytest.raises(CaptureRecordAlreadyExistsError):
        store.create(intruder)

    assert store.get(first.id) == first


def test_records_with_different_ids_coexist(store: SqliteCaptureRecordStore) -> None:
    one = make_record(id="cap_a")
    another = make_record(id="cap_b", status=CaptureStatus.FAILED)
    store.create(one)
    store.create(another)

    assert store.get("cap_a") == one
    assert store.get("cap_b") == another


def test_replace_overwrites_the_stored_snapshot(store: SqliteCaptureRecordStore) -> None:
    original = make_record(status=CaptureStatus.STORED, error=None)
    store.create(original)
    moved_on = make_record(
        id=original.id,
        status=CaptureStatus.FAILED,
        updated_at=UPDATED_AT,
        error="decoding failed",
    )

    store.replace(moved_on)

    assert store.get(original.id) == moved_on


def test_replace_stores_status_error_and_timestamps_exactly_as_supplied(
    store: SqliteCaptureRecordStore,
) -> None:
    """The store transcribes the caller's snapshot; it decides none of it."""
    store.create(make_minimal_record())
    supplied = make_minimal_record(
        status=CaptureStatus.PARTIAL,
        updated_at=UPDATED_AT,
        error="two of three segments",
    )

    store.replace(supplied)
    restored = store.get(supplied.id)

    assert restored.status is CaptureStatus.PARTIAL
    assert restored.updated_at == UPDATED_AT
    assert restored.error == "two of three segments"
    assert restored.received_at == supplied.received_at


def test_replace_may_clear_optional_fields(store: SqliteCaptureRecordStore) -> None:
    """Replace is a whole-snapshot swap, not a merge of the fields that are set."""
    store.create(make_record(error="transient", updated_at=UPDATED_AT))
    cleared = make_record(error=None, updated_at=None)

    store.replace(cleared)
    restored = store.get(cleared.id)

    assert restored.error is None
    assert restored.updated_at is None


def test_replace_of_an_unknown_id_is_not_found(store: SqliteCaptureRecordStore) -> None:
    with pytest.raises(CaptureRecordNotFoundError):
        store.replace(make_record(id="cap_never_stored"))


def test_replace_never_creates(store: SqliteCaptureRecordStore) -> None:
    """A refused replace leaves nothing behind — no implicit upsert."""
    missing = make_record(id="cap_never_stored")

    with pytest.raises(CaptureRecordNotFoundError):
        store.replace(missing)

    with pytest.raises(CaptureRecordNotFoundError):
        store.get(missing.id)


def test_replace_touches_only_its_own_record(store: SqliteCaptureRecordStore) -> None:
    neighbour = make_record(id="cap_neighbour")
    target = make_record(id="cap_target")
    store.create(neighbour)
    store.create(target)

    store.replace(make_record(id="cap_target", status=CaptureStatus.COMPLETE))

    assert store.get("cap_neighbour") == neighbour


def test_a_failed_replace_leaves_the_neighbour_alone(store: SqliteCaptureRecordStore) -> None:
    stored = make_record(id="cap_stored")
    store.create(stored)

    with pytest.raises(CaptureRecordNotFoundError):
        store.replace(make_record(id="cap_absent"))

    assert store.get("cap_stored") == stored


def test_every_error_shares_one_base_class(store: SqliteCaptureRecordStore) -> None:
    """A caller that wants to catch "the store failed" has one type to name."""
    with pytest.raises(CaptureRecordStoreError):
        store.get("cap_never_stored")


def test_the_store_accepts_any_lifecycle_state(store: SqliteCaptureRecordStore) -> None:
    """Persistence holds no transition policy: that is orchestration's job."""
    for index, status in enumerate(CaptureStatus):
        record = make_record(id=f"cap_{index}", status=status)
        store.create(record)

        assert store.get(record.id).status is status


def test_the_store_does_not_enforce_a_lifecycle_order(store: SqliteCaptureRecordStore) -> None:
    """``complete`` may be replaced by ``received``; nothing here has an opinion."""
    store.create(make_record(status=CaptureStatus.COMPLETE))

    store.replace(make_record(status=CaptureStatus.RECEIVED))

    assert store.get("cap_persist_01").status is CaptureStatus.RECEIVED


def test_the_database_path_stays_out_of_the_contracts(
    store: SqliteCaptureRecordStore, record: CaptureRecord, database: Path
) -> None:
    """Nothing in a returned record names a file, a directory, or a backend."""
    store.create(record)

    assert str(database) not in store.get(record.id).model_dump_json()
