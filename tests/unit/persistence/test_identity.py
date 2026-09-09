"""Capture identity is the record's own id, and nothing else.

ADR-003 drew this line for raw storage: a SHA-256 addresses bytes, and two
captures of the same bytes are two captures. Persistence is where that line
could quietly be crossed — by keying on a digest, by treating a repeated URL as
a repeat submission, or by "helpfully" collapsing two identical-looking
records. It is not crossed here, and these tests are what says so.
"""

import pytest

from core.contracts import CaptureSource, CaptureSourceType, CaptureStatus
from core.persistence import CaptureRecordAlreadyExistsError, SqliteCaptureRecordStore
from tests.unit.persistence.builders import RAW_SHA256, make_raw_object, make_record


def test_two_captures_of_one_raw_object_coexist(store: SqliteCaptureRecordStore) -> None:
    """The same bytes, captured twice, are two independent records."""
    raw_object = make_raw_object()
    first = make_record(id="cap_first", raw_object=raw_object)
    second = make_record(id="cap_second", raw_object=raw_object)

    store.create(first)
    store.create(second)

    assert store.get("cap_first") == first
    assert store.get("cap_second") == second


def test_a_repeated_digest_is_not_a_duplicate(store: SqliteCaptureRecordStore) -> None:
    """Creating a second capture over the same SHA-256 is not an id collision."""
    store.create(make_record(id="cap_first", raw_object=make_raw_object()))

    store.create(make_record(id="cap_second", raw_object=make_raw_object()))

    assert store.get("cap_second").raw_object is not None


def test_records_sharing_a_digest_stay_independent(store: SqliteCaptureRecordStore) -> None:
    """Replacing one leaves the other exactly as it was."""
    first = make_record(id="cap_first", status=CaptureStatus.STORED)
    second = make_record(id="cap_second", status=CaptureStatus.STORED)
    store.create(first)
    store.create(second)

    store.replace(make_record(id="cap_first", status=CaptureStatus.FAILED))

    assert store.get("cap_second") == second
    assert store.get("cap_first").status is CaptureStatus.FAILED


def test_the_digest_is_not_a_key(store: SqliteCaptureRecordStore) -> None:
    """A record is not retrievable by the digest of the bytes it references."""
    store.create(make_record(id="cap_first", raw_object=make_raw_object()))

    with pytest.raises(Exception, match="not stored"):
        store.get(RAW_SHA256)


def test_identical_captures_under_two_ids_both_persist(
    store: SqliteCaptureRecordStore,
) -> None:
    """Same source, same URL, same payload type, same digest — two records."""
    fields = {
        "source": CaptureSource(
            type=CaptureSourceType.BROWSER, provider="chrome", url="https://example.com/same"
        ),
        "raw_object": make_raw_object(),
    }
    store.create(make_record(id="cap_a", **fields))
    store.create(make_record(id="cap_b", **fields))

    assert store.get("cap_a").id == "cap_a"
    assert store.get("cap_b").id == "cap_b"


def test_duplicate_detection_is_the_id_and_only_the_id(
    store: SqliteCaptureRecordStore,
) -> None:
    """A record with no raw object at all still collides on its id."""
    store.create(make_record(raw_object=None))

    with pytest.raises(CaptureRecordAlreadyExistsError):
        store.create(make_record(raw_object=None, status=CaptureStatus.FAILED))
