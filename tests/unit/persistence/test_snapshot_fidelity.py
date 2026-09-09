"""What survives the round trip, and what a stored snapshot is.

Two claims are tested here. Fidelity: a record comes back field for field,
including aware datetimes, enums, nested models, and Unicode. Isolation:
storing is taking a copy, so nothing that happens to a Python object afterwards
reaches the database, and nothing done to a retrieved object reaches it either.
"""

from core.contracts import (
    CapturePayloadType,
    CaptureRecord,
    CaptureSourceType,
    CaptureStatus,
)
from core.persistence import SqliteCaptureRecordStore
from tests.unit.persistence.builders import (
    RAW_SHA256,
    RECEIVED_AT,
    UNICODE_ERROR,
    UPDATED_AT,
    make_record,
)


def test_every_field_survives_the_round_trip(store: SqliteCaptureRecordStore) -> None:
    record = make_record()
    store.create(record)

    restored = store.get(record.id)

    assert restored.schema_version == "0.1"
    assert restored.id == record.id
    assert restored.status is CaptureStatus.STORED
    assert restored.received_at == RECEIVED_AT
    assert restored.updated_at == UPDATED_AT
    assert restored.source.type is CaptureSourceType.BROWSER
    assert restored.source.provider == "chrome"
    assert restored.source.url == "https://example.com/notes?q=1&r=2"
    assert restored.payload_type is CapturePayloadType.WEBPAGE
    assert restored.error == UNICODE_ERROR


def test_the_raw_object_reference_survives_intact(store: SqliteCaptureRecordStore) -> None:
    record = make_record()
    store.create(record)

    raw_object = store.get(record.id).raw_object

    assert raw_object is not None
    assert raw_object.id == RAW_SHA256
    assert raw_object.sha256 == RAW_SHA256
    assert raw_object.ref == f"sha256:{RAW_SHA256}"
    assert raw_object.mime_type == "text/plain"


def test_datetimes_come_back_aware_and_exact(store: SqliteCaptureRecordStore) -> None:
    """Microseconds and the timezone offset both survive."""
    record = make_record()
    store.create(record)

    restored = store.get(record.id)

    assert restored.received_at.tzinfo is not None
    assert restored.received_at.utcoffset() == RECEIVED_AT.utcoffset()
    assert restored.received_at.microsecond == RECEIVED_AT.microsecond


def test_enums_come_back_as_enum_members(store: SqliteCaptureRecordStore) -> None:
    """Not the bare strings they serialize to."""
    record = make_record()
    store.create(record)

    restored = store.get(record.id)

    assert isinstance(restored.status, CaptureStatus)
    assert isinstance(restored.payload_type, CapturePayloadType)
    assert isinstance(restored.source.type, CaptureSourceType)


def test_unicode_survives_unmangled(store: SqliteCaptureRecordStore) -> None:
    """Cyrillic, CJK, an emoji, quoting characters, and whitespace, byte for byte."""
    record = make_record(error=UNICODE_ERROR)
    store.create(record)

    assert store.get(record.id).error == UNICODE_ERROR


def test_unicode_survives_in_the_key_as_well_as_the_payload(
    store: SqliteCaptureRecordStore,
) -> None:
    """Identifiers are opaque non-blank strings, so the key is text like any other."""
    record = make_record(id="капчур-🌍-01")
    store.create(record)

    assert store.get("капчур-🌍-01") == record


def test_the_stored_snapshot_is_a_valid_contract_document(
    store: SqliteCaptureRecordStore,
) -> None:
    """What comes back re-validates, and re-serializes to the same JSON."""
    record = make_record()
    store.create(record)

    restored = store.get(record.id)

    assert CaptureRecord.model_validate_json(restored.model_dump_json()) == record
    assert restored.model_dump_json() == record.model_dump_json()


def test_create_does_not_mutate_the_record_it_is_given(
    store: SqliteCaptureRecordStore,
) -> None:
    record = make_record()
    before = record.model_copy(deep=True)

    store.create(record)

    assert record == before


def test_replace_does_not_mutate_the_record_it_is_given(
    store: SqliteCaptureRecordStore,
) -> None:
    store.create(make_record())
    replacement = make_record(status=CaptureStatus.COMPLETE)
    before = replacement.model_copy(deep=True)

    store.replace(replacement)

    assert replacement == before


def test_mutating_the_record_after_create_does_not_change_the_snapshot(
    store: SqliteCaptureRecordStore,
) -> None:
    """Persistence is a snapshot, not a live handle on the caller's object."""
    record = make_record(status=CaptureStatus.STORED, error=None)
    store.create(record)

    record.status = CaptureStatus.FAILED
    record.error = "changed my mind"

    restored = store.get(record.id)
    assert restored.status is CaptureStatus.STORED
    assert restored.error is None


def test_mutating_a_retrieved_record_does_not_change_the_snapshot(
    store: SqliteCaptureRecordStore,
) -> None:
    """No dirty tracking, no session, no unit of work: only ``replace`` writes."""
    store.create(make_record(status=CaptureStatus.STORED))
    retrieved = store.get("cap_persist_01")

    retrieved.status = CaptureStatus.COMPLETE

    assert store.get("cap_persist_01").status is CaptureStatus.STORED


def test_an_explicit_replace_is_what_makes_a_change_durable(
    store: SqliteCaptureRecordStore,
) -> None:
    store.create(make_record(status=CaptureStatus.STORED))
    retrieved = store.get("cap_persist_01")
    retrieved.status = CaptureStatus.COMPLETE

    store.replace(retrieved)

    assert store.get("cap_persist_01").status is CaptureStatus.COMPLETE


def test_the_store_generates_nothing(store: SqliteCaptureRecordStore) -> None:
    """No id, no timestamp, no status, no error is minted on the way through."""
    record = make_record(updated_at=None, error=None)
    store.create(record)

    restored = store.get(record.id)

    assert restored.updated_at is None
    assert restored.error is None
    assert restored.received_at == RECEIVED_AT
    assert restored.id == record.id


def test_updated_at_is_untouched_by_a_replace(store: SqliteCaptureRecordStore) -> None:
    """``updated_at`` belongs to orchestration; persistence never advances it."""
    store.create(make_record(updated_at=None))

    store.replace(make_record(status=CaptureStatus.COMPLETE, updated_at=None))

    assert store.get("cap_persist_01").updated_at is None
