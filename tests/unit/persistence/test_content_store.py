"""What ``create``, ``get``, and ``get_for_capture`` mean.

Canonical content is written once and never rewritten, so the semantics here
are narrower than the capture record store's: there is no ``replace``, and the
two uniqueness rules — one object per id, one object per capture — are what the
store exists to enforce.
"""

import inspect
from pathlib import Path

import pytest

from core.contracts import ContentObject, ContentSource
from core.persistence import (
    ContentObjectAlreadyExistsError,
    ContentObjectNotFoundError,
    ContentObjectStore,
    ContentObjectStoreError,
    SqliteContentObjectStore,
)
from tests.unit.persistence.builders import RAW_SHA256, make_content


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """A database file that does not exist yet."""
    return tmp_path / "content.sqlite3"


@pytest.fixture
def store(database: Path) -> SqliteContentObjectStore:
    return SqliteContentObjectStore(database)


@pytest.fixture
def content() -> ContentObject:
    return make_content()


def test_the_sqlite_store_satisfies_the_port(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    """Structural conformance, checked by the type checker as well as at runtime."""
    port: ContentObjectStore = store
    port.create(content)

    assert port.get(content.id) == content
    assert port.get_for_capture(content.source.capture_id) == content


def test_the_port_is_exactly_three_methods() -> None:
    """No ``replace``, ``save``, ``upsert``, ``delete``, ``list``, or ``search``."""
    methods = {
        name
        for name, _ in inspect.getmembers(ContentObjectStore, inspect.isfunction)
        if not name.startswith("_")
    }

    assert methods == {"create", "get", "get_for_capture"}


def test_create_then_get_round_trips(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    assert store.get(content.id) == content


def test_every_field_survives_the_round_trip(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    restored = store.get(content.id)

    assert restored.schema_version == "0.2"
    assert restored.title == content.title
    assert restored.metadata == content.metadata
    assert restored.segments[0].text == content.segments[0].text
    assert restored.segments[0].provenance == content.segments[0].provenance
    assert restored.assets[0].sha256 == RAW_SHA256
    assert restored.derived == content.derived
    assert restored.processing == content.processing
    assert restored.original == content.original


def test_get_returns_a_freshly_built_object_each_time(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    first = store.get(content.id)
    second = store.get(content.id)

    assert first == second
    assert first is not second
    assert first is not content


def test_get_for_capture_returns_the_captures_content(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    assert store.get_for_capture(content.source.capture_id) == content


def test_get_for_capture_finds_the_right_one_among_several(
    store: SqliteContentObjectStore,
) -> None:
    store.create(make_content("con_a", "cap_a"))
    store.create(make_content("con_b", "cap_b"))

    assert store.get_for_capture("cap_b").id == "con_b"
    assert store.get_for_capture("cap_a").id == "con_a"


def test_an_unknown_content_id_is_not_found(store: SqliteContentObjectStore) -> None:
    with pytest.raises(ContentObjectNotFoundError):
        store.get("con_never_stored")


def test_an_unknown_capture_id_is_not_found(store: SqliteContentObjectStore) -> None:
    """The ordinary answer for a capture that has not been processed."""
    with pytest.raises(ContentObjectNotFoundError):
        store.get_for_capture("cap_never_processed")


def test_a_duplicate_content_id_raises(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    with pytest.raises(ContentObjectAlreadyExistsError, match="con_persist_01"):
        store.create(make_content("con_persist_01", "cap_other"))


def test_a_second_object_for_one_capture_raises(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    """One capture, one canonical object: the rule the database enforces."""
    store.create(content)

    with pytest.raises(ContentObjectAlreadyExistsError, match="cap_persist_01"):
        store.create(make_content("con_different", "cap_persist_01"))


def test_the_two_uniqueness_rules_are_reported_apart(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    """A taken id and a capture that already normalized are different problems."""
    store.create(content)

    with pytest.raises(ContentObjectAlreadyExistsError) as by_id:
        store.create(make_content("con_persist_01", "cap_other"))
    with pytest.raises(ContentObjectAlreadyExistsError) as by_capture:
        store.create(make_content("con_other", "cap_persist_01"))

    assert "content object" in str(by_id.value)
    assert "already has a stored content object" in str(by_capture.value)


def test_a_refused_duplicate_does_not_overwrite(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    with pytest.raises(ContentObjectAlreadyExistsError):
        store.create(make_content("con_persist_01", "cap_other", title="clobbered"))

    assert store.get("con_persist_01") == content


def test_a_refused_capture_duplicate_does_not_overwrite(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    with pytest.raises(ContentObjectAlreadyExistsError):
        store.create(make_content("con_other", "cap_persist_01", title="clobbered"))

    assert store.get_for_capture("cap_persist_01") == content
    with pytest.raises(ContentObjectNotFoundError):
        store.get("con_other")


def test_two_captures_of_identical_content_coexist(store: SqliteContentObjectStore) -> None:
    """Same raw digest, same text: two captures, two canonical objects."""
    first = make_content("con_a", "cap_a")
    second = make_content("con_b", "cap_b")
    store.create(first)
    store.create(second)

    assert store.get("con_a").assets[0].sha256 == store.get("con_b").assets[0].sha256
    assert store.get("con_a").segments[0].text == store.get("con_b").segments[0].text
    assert store.get("con_a").id != store.get("con_b").id


def test_the_content_id_is_not_the_capture_id_or_the_digest(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    restored = store.get(content.id)
    assert restored.id != restored.source.capture_id
    assert restored.id != RAW_SHA256
    assert restored.id not in {asset.sha256 for asset in restored.assets}


def test_create_does_not_mutate_the_object_it_is_given(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    before = content.model_copy(deep=True)

    store.create(content)

    assert content == before


def test_mutating_the_object_after_create_does_not_change_the_snapshot(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    """Persistence is a snapshot, not a live handle on the caller's object."""
    store.create(content)

    content.title = "changed my mind"
    content.metadata["injected"] = True

    restored = store.get(content.id)
    assert restored.title == "A note — 🌍"
    assert "injected" not in restored.metadata


def test_mutating_a_retrieved_object_does_not_change_the_snapshot(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    """There is no write-back API at all, so nothing could carry it through."""
    store.create(content)
    retrieved = store.get(content.id)

    retrieved.title = "changed"
    retrieved.segments[0].text = "rewritten"

    assert store.get(content.id).title == "A note — 🌍"
    assert store.get(content.id).segments[0].text == content.segments[0].text


def test_every_error_shares_one_base_class(store: SqliteContentObjectStore) -> None:
    with pytest.raises(ContentObjectStoreError):
        store.get("con_never_stored")


def test_content_errors_are_not_capture_record_errors(
    store: SqliteContentObjectStore,
) -> None:
    """Sibling hierarchies: a caller can tell which store failed."""
    from core.persistence import CaptureRecordStoreError

    with pytest.raises(ContentObjectStoreError) as raised:
        store.get("con_never_stored")

    assert not isinstance(raised.value, CaptureRecordStoreError)


def test_a_legacy_content_object_round_trips(store: SqliteContentObjectStore) -> None:
    """0.2 changed no ``ContentObject`` field, so a 0.1 object stores and loads."""
    data = make_content("con_legacy", "cap_legacy").model_dump(mode="json")
    legacy = ContentObject.model_validate(data | {"schema_version": "0.1"})

    store.create(legacy)

    restored = store.get("con_legacy")
    assert restored.schema_version == "0.1"
    assert restored == legacy


def test_a_current_content_object_stays_current(
    store: SqliteContentObjectStore, content: ContentObject
) -> None:
    store.create(content)

    assert store.get(content.id).schema_version == "0.2"


def test_content_without_a_provider_or_url_round_trips(
    store: SqliteContentObjectStore,
) -> None:
    minimal = make_content(
        "con_minimal", "cap_minimal", source=ContentSource(capture_id="cap_minimal")
    )
    store.create(minimal)

    restored = store.get(minimal.id)
    assert restored.source.provider is None
    assert restored.source.url is None
    assert restored == minimal
