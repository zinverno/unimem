"""Behaviour of the local raw object store."""

import hashlib
from pathlib import Path
from typing import BinaryIO

import pytest

from core.contracts import RawObjectRef
from core.storage import (
    InvalidRawObjectRefError,
    LocalRawObjectStore,
    RawObjectNotFoundError,
    RawObjectStore,
    RawObjectWriteError,
    build_raw_ref,
)
from tests.unit.storage.streams import FailingStream, NonSeekableStream, RecordingStream

BINARY = b"\x00\x01\x02\xff\xfe" + b"payload" + b"\x00" * 16


@pytest.fixture
def store(tmp_path: Path) -> LocalRawObjectStore:
    """A store rooted at a directory that does not exist yet."""
    return LocalRawObjectStore(tmp_path / "objects")


def stored_files(root: Path) -> list[Path]:
    """Every finalized object under a store root."""
    objects = root / "sha256"
    return sorted(path for path in objects.rglob("*") if path.is_file())


def test_local_store_satisfies_the_port(store: LocalRawObjectStore) -> None:
    """Structural conformance, checked by the type checker as well as at runtime."""
    port: RawObjectStore = store
    assert port.exists(port.store_bytes(b"port"))


def test_store_bytes_returns_a_content_addressed_reference(store: LocalRawObjectStore) -> None:
    reference = store.store_bytes(BINARY, mime_type="application/octet-stream")
    digest = hashlib.sha256(BINARY).hexdigest()

    assert reference.sha256 == digest
    assert reference.id == digest
    assert reference.ref == f"sha256:{digest}"
    assert reference.mime_type == "application/octet-stream"


def test_round_trip_is_byte_exact(store: LocalRawObjectStore) -> None:
    reference = store.store_bytes(BINARY)
    assert store.read_bytes(reference) == BINARY


def test_open_yields_the_exact_content(store: LocalRawObjectStore) -> None:
    reference = store.store_bytes(BINARY)
    with store.open(reference) as handle:
        assert handle.read() == BINARY


def test_zero_byte_object_is_supported(store: LocalRawObjectStore) -> None:
    reference = store.store_bytes(b"")
    assert reference.sha256 == hashlib.sha256(b"").hexdigest()
    assert store.read_bytes(reference) == b""
    assert store.exists(reference)


def test_payload_with_null_bytes_is_supported(store: LocalRawObjectStore) -> None:
    data = b"\x00" * 1024 + b"\n\r\x1a" + b"\x00"
    assert store.read_bytes(store.store_bytes(data)) == data


def test_identical_bytes_store_once(store: LocalRawObjectStore, tmp_path: Path) -> None:
    first = store.store_bytes(BINARY)
    second = store.store_bytes(BINARY)

    assert (first.id, first.sha256, first.ref) == (second.id, second.sha256, second.ref)
    assert len(stored_files(tmp_path / "objects")) == 1


def test_mime_type_does_not_affect_identity(store: LocalRawObjectStore, tmp_path: Path) -> None:
    as_png = store.store_bytes(BINARY, mime_type="image/png")
    as_octets = store.store_bytes(BINARY, mime_type="application/octet-stream")

    assert (as_png.id, as_png.sha256, as_png.ref) == (as_octets.id, as_octets.sha256, as_octets.ref)
    assert as_png.mime_type == "image/png"
    assert as_octets.mime_type == "application/octet-stream"
    assert len(stored_files(tmp_path / "objects")) == 1


def test_different_bytes_get_different_identities(
    store: LocalRawObjectStore, tmp_path: Path
) -> None:
    first = store.store_bytes(b"one")
    second = store.store_bytes(b"two")

    assert first.sha256 != second.sha256
    assert first.ref != second.ref
    assert len(stored_files(tmp_path / "objects")) == 2


def test_store_stream_accepts_a_non_seekable_source(store: LocalRawObjectStore) -> None:
    reference = store.store_stream(NonSeekableStream(BINARY))
    assert reference.sha256 == hashlib.sha256(BINARY).hexdigest()
    assert store.read_bytes(reference) == BINARY


def test_stream_is_consumed_in_bounded_chunks(store: LocalRawObjectStore) -> None:
    """A source that refuses an unbounded read must still store successfully."""
    payload = b"chunk me" * 400_000  # comfortably larger than one chunk
    stream = RecordingStream(payload)

    reference = store.store_stream(stream)

    assert store.read_bytes(reference) == payload
    assert len(stream.requested_sizes) > 1
    assert max(stream.requested_sizes) <= 1024 * 1024


def test_stream_is_read_from_its_current_position(store: LocalRawObjectStore) -> None:
    """The store must not rewind what the caller handed it."""
    stream = NonSeekableStream(b"skipped:" + BINARY)
    assert stream.read(len(b"skipped:")) == b"skipped:"

    reference = store.store_stream(stream)
    assert store.read_bytes(reference) == BINARY


def test_missing_object_raises_not_found(store: LocalRawObjectStore) -> None:
    absent = RawObjectRef(id="raw_absent", ref=build_raw_ref(hashlib.sha256(b"absent").hexdigest()))
    with pytest.raises(RawObjectNotFoundError):
        store.read_bytes(absent)
    with pytest.raises(RawObjectNotFoundError), store.open(absent):
        pass


def test_exists_reports_presence_and_absence(store: LocalRawObjectStore) -> None:
    present = store.store_bytes(BINARY)
    absent = RawObjectRef(id="raw_absent", ref=build_raw_ref(hashlib.sha256(b"absent").hexdigest()))

    assert store.exists(present)
    assert not store.exists(absent)


@pytest.mark.parametrize(
    "ref",
    ["sha1:" + "a" * 40, "sha256:not-a-digest", "sha256:../../etc/passwd", "file:///etc/passwd"],
)
def test_invalid_references_are_rejected_by_every_read_path(
    store: LocalRawObjectStore, ref: str
) -> None:
    reference = RawObjectRef(id="raw_01", ref=ref)

    with pytest.raises(InvalidRawObjectRefError):
        store.exists(reference)
    with pytest.raises(InvalidRawObjectRefError):
        store.read_bytes(reference)


def test_reference_hash_disagreement_is_rejected(store: LocalRawObjectStore) -> None:
    stored = store.store_bytes(BINARY)
    tampered = RawObjectRef(
        id=stored.id,
        ref=stored.ref,
        sha256=hashlib.sha256(b"different").hexdigest(),
    )
    with pytest.raises(InvalidRawObjectRefError, match="but sha256"):
        store.read_bytes(tampered)


def test_failed_stream_leaves_no_finalized_object(
    store: LocalRawObjectStore, tmp_path: Path
) -> None:
    root = tmp_path / "objects"
    with (
        pytest.raises(RawObjectWriteError, match="failed while reading"),
        FailingStream(b"x" * 4096, chunks_before_failure=2) as stream,
    ):
        store.store_stream(stream)

    assert stored_files(root) == []
    assert list((root / "staging").iterdir()) == []


def test_a_failed_write_does_not_disturb_existing_objects(
    store: LocalRawObjectStore, tmp_path: Path
) -> None:
    existing = store.store_bytes(BINARY)

    with (
        pytest.raises(RawObjectWriteError),
        FailingStream(b"x" * 4096, chunks_before_failure=1) as stream,
    ):
        store.store_stream(stream)

    assert store.read_bytes(existing) == BINARY
    assert len(stored_files(tmp_path / "objects")) == 1


def test_repeated_storage_never_rewrites_the_finalized_object(
    store: LocalRawObjectStore, tmp_path: Path
) -> None:
    reference = store.store_bytes(BINARY)
    path = stored_files(tmp_path / "objects")[0]
    before = path.stat()

    store.store_bytes(BINARY, mime_type="image/png")
    store.store_stream(NonSeekableStream(BINARY))

    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
    )
    assert store.read_bytes(reference) == BINARY


def test_store_has_no_mutation_api(store: LocalRawObjectStore) -> None:
    """Raw originals are immutable: there is nothing to update or delete."""
    for forbidden in ("update", "overwrite", "delete", "remove", "mutate", "write"):
        assert not hasattr(store, forbidden)


def test_unusable_staging_location_raises_a_write_error(tmp_path: Path) -> None:
    """An OS failure while staging surfaces as a storage error, not an OSError."""
    root = tmp_path / "objects"
    root.mkdir(parents=True)
    (root / "staging").write_bytes(b"not a directory")

    store = LocalRawObjectStore(root)
    with pytest.raises(RawObjectWriteError, match="could not stage"):
        store.store_bytes(BINARY)


def test_unusable_object_location_raises_a_write_error(tmp_path: Path) -> None:
    """A blocked shard directory is a write error, never a silent deduplication."""
    root = tmp_path / "objects"
    digest = hashlib.sha256(BINARY).hexdigest()
    shard = root / "sha256" / digest[:2]
    shard.parent.mkdir(parents=True)
    shard.write_bytes(b"not a directory")

    store = LocalRawObjectStore(root)
    with pytest.raises(RawObjectWriteError, match="could not prepare"):
        store.store_bytes(BINARY)
    assert list((root / "staging").iterdir()) == []


def test_finalization_failure_leaves_no_object_and_no_staging(
    store: LocalRawObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If publishing the staged object fails, nothing is left behind."""

    def failing_link(source: object, target: object) -> None:
        raise OSError("link failed")

    monkeypatch.setattr("core.storage.local.os.link", failing_link)

    with pytest.raises(RawObjectWriteError, match="could not finalize"):
        store.store_bytes(BINARY)

    root = tmp_path / "objects"
    assert stored_files(root) == []
    assert list((root / "staging").iterdir()) == []


def test_object_that_vanishes_after_the_existence_check_raises_not_found(
    store: LocalRawObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read losing its race against the filesystem still gets a typed error."""
    reference = store.store_bytes(BINARY)

    def failing_open(self: Path, *args: object, **kwargs: object) -> BinaryIO:
        raise OSError("vanished")

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(RawObjectNotFoundError), store.open(reference):
        pass
