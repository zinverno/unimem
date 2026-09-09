"""The local store against a real filesystem.

These tests look inside the backend directory on purpose: the physical layout
is an implementation detail, and verifying it is exactly what an integration
test for this backend is for. The production API never exposes paths.
"""

import hashlib
import string
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.contracts import RawObjectRef
from core.storage import LocalRawObjectStore

PAYLOAD = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64


def finalized(root: Path) -> list[Path]:
    return sorted(path for path in (root / "sha256").rglob("*") if path.is_file())


def staging(root: Path) -> list[Path]:
    staging_dir = root / "staging"
    return sorted(staging_dir.iterdir()) if staging_dir.is_dir() else []


def test_root_may_start_nonexistent_and_is_created(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "nested" / "store"
    assert not root.exists()

    store = LocalRawObjectStore(root)
    reference = store.store_bytes(PAYLOAD)

    assert root.is_dir()
    assert store.read_bytes(reference) == PAYLOAD


def test_layout_is_derived_only_from_the_digest(tmp_path: Path) -> None:
    store = LocalRawObjectStore(tmp_path)
    reference = store.store_bytes(PAYLOAD, mime_type="image/png")
    digest = hashlib.sha256(PAYLOAD).hexdigest()

    stored = finalized(tmp_path)
    assert len(stored) == 1
    assert stored[0].relative_to(tmp_path) == Path("sha256") / digest[:2] / digest[2:4] / digest
    assert reference.sha256 == digest


def test_no_user_controlled_text_appears_in_the_physical_layout(tmp_path: Path) -> None:
    """Only the store's own names and hex digests may appear on disk."""
    store = LocalRawObjectStore(tmp_path)
    store.store_bytes(PAYLOAD, mime_type="image/png")
    store.store_bytes(b"another", mime_type="video/mp4; name=holiday-video.mp4")

    permitted = set(string.hexdigits.lower())
    for path in (tmp_path / "sha256").rglob("*"):
        name = path.name
        assert set(name) <= permitted, f"unexpected name on disk: {name}"
    every_path = " ".join(str(path) for path in tmp_path.rglob("*"))
    assert "png" not in every_path
    assert "holiday" not in every_path
    assert "mp4" not in every_path


def test_logical_reference_never_reveals_the_local_root(tmp_path: Path) -> None:
    store = LocalRawObjectStore(tmp_path)
    reference = store.store_bytes(PAYLOAD, mime_type="image/png")

    serialized = reference.model_dump_json()
    assert str(tmp_path) not in serialized
    assert (reference.ref or "").split(":")[0] == "sha256"
    for fragment in ("/", "\\", "file://", "sha256/"):
        assert fragment not in (reference.ref or "")


def test_a_store_reopened_on_the_same_root_sees_earlier_objects(tmp_path: Path) -> None:
    reference = LocalRawObjectStore(tmp_path).store_bytes(PAYLOAD)

    reopened = LocalRawObjectStore(tmp_path)
    assert reopened.exists(reference)
    assert reopened.read_bytes(reference) == PAYLOAD


def test_two_stores_on_one_root_deduplicate(tmp_path: Path) -> None:
    first = LocalRawObjectStore(tmp_path).store_bytes(PAYLOAD)
    second = LocalRawObjectStore(tmp_path).store_bytes(PAYLOAD)

    assert first.ref == second.ref
    assert len(finalized(tmp_path)) == 1


def test_concurrent_identical_writers_converge(tmp_path: Path) -> None:
    """Both writers succeed, agree on identity, and leave exactly one object."""
    store = LocalRawObjectStore(tmp_path)
    payload = PAYLOAD * 32

    writers = 4
    ready = threading.Barrier(writers)

    def write() -> RawObjectRef:
        ready.wait(timeout=10)  # make the writers contend rather than queue
        return store.store_bytes(payload)

    with ThreadPoolExecutor(max_workers=writers) as pool:
        futures = [pool.submit(write) for _ in range(writers)]
        references = [future.result() for future in futures]

    assert len({reference.ref for reference in references}) == 1
    assert len({reference.id for reference in references}) == 1
    assert len(finalized(tmp_path)) == 1
    assert staging(tmp_path) == []
    assert store.read_bytes(references[0]) == payload


def test_concurrent_distinct_writers_all_land(tmp_path: Path) -> None:
    store = LocalRawObjectStore(tmp_path)
    payloads = [f"object-{index}".encode() * 1000 for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        references = list(pool.map(store.store_bytes, payloads))

    assert len({reference.ref for reference in references}) == len(payloads)
    assert len(finalized(tmp_path)) == len(payloads)
    assert staging(tmp_path) == []
    for payload, reference in zip(payloads, references, strict=True):
        assert store.read_bytes(reference) == payload


def test_stored_object_survives_an_unrelated_reference_error(tmp_path: Path) -> None:
    store = LocalRawObjectStore(tmp_path)
    reference = store.store_bytes(PAYLOAD)

    assert not store.exists(RawObjectRef(id="other", ref="sha256:" + "0" * 64))
    assert store.read_bytes(reference) == PAYLOAD
    assert len(finalized(tmp_path)) == 1
