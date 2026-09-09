"""Text processing over the real local raw object store.

The unit tests prove the processor talks to the port. These prove the whole
vertical slice works against a real filesystem backend: bytes in, canonical
content out, originals untouched.
"""

import hashlib
from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentType,
    ProcessingStatus,
    ProvenanceSourceType,
    RawObjectRef,
    SegmentType,
)
from core.processing import ProcessorRouter, TextProcessor
from core.storage import LocalRawObjectStore, RawObjectNotFoundError, build_raw_ref
from tests.unit.processing.builders import CAPTURED_AT, RECEIVED_AT

GREETING = "Привет, мир"
NOTES = f"# Notes\r\n\r\n{GREETING}\n\n\tindented line   \n"


def capture_for(raw_object: RawObjectRef, *, capture_id: str = "cap_int_01") -> CaptureRecord:
    """The capture record a future capture service would have written."""
    return CaptureRecord(
        id=capture_id,
        status=CaptureStatus.STORED,
        received_at=RECEIVED_AT,
        source=CaptureSource(
            type=CaptureSourceType.FILESYSTEM,
            provider="watcher",
            url="file:///notes.txt",
        ),
        payload_type=CapturePayloadType.TEXT,
        raw_object=raw_object,
        context=CaptureContext(captured_at=CAPTURED_AT),
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalRawObjectStore:
    return LocalRawObjectStore(tmp_path / "objects")


def test_bytes_become_a_canonical_content_object(store: LocalRawObjectStore) -> None:
    """raw bytes -> RawObjectStore -> CaptureRecord -> TextProcessor -> ContentObject."""
    raw_object = store.store_bytes(NOTES.encode(), mime_type="text/plain")
    capture = capture_for(raw_object)

    content = TextProcessor(store).process(capture)

    assert content.type is ContentType.TEXT
    assert content.source.capture_id == capture.id
    assert content.source.provider == "watcher"
    assert content.source.url == "file:///notes.txt"
    assert content.original.sha256 == hashlib.sha256(NOTES.encode()).hexdigest()

    segment = content.segments[0]
    assert len(content.segments) == 1
    assert segment.type is SegmentType.TEXT
    assert segment.text == NOTES
    assert segment.position == 0
    assert segment.provenance.source_type is ProvenanceSourceType.ORIGINAL
    assert segment.provenance.processor == TextProcessor.name

    assert len(content.processing) == 1
    assert content.processing[0].status is ProcessingStatus.COMPLETE
    assert ContentObject.model_validate_json(content.model_dump_json()) == content


def test_routing_the_capture_reaches_the_text_processor(store: LocalRawObjectStore) -> None:
    router = ProcessorRouter([TextProcessor(store)])
    capture = capture_for(store.store_bytes(b"routed through the router"))

    assert router.process(capture).segments[0].text == "routed through the router"


def test_the_original_is_retrievable_from_the_content_object(store: LocalRawObjectStore) -> None:
    payload = NOTES.encode()
    content = TextProcessor(store).process(capture_for(store.store_bytes(payload)))
    asset = content.assets[0]

    digest = hashlib.sha256(payload).hexdigest()
    assert asset.sha256 == digest
    recovered = RawObjectRef(
        id=asset.sha256, mime_type=asset.mime_type, sha256=asset.sha256, ref=asset.ref
    )
    assert store.read_bytes(recovered) == payload
    assert asset.ref == build_raw_ref(digest)
    assert asset.id != digest


def test_processing_leaves_the_stored_original_byte_identical(
    store: LocalRawObjectStore, tmp_path: Path
) -> None:
    payload = NOTES.encode()
    raw_object = store.store_bytes(payload, mime_type="text/plain")
    root = tmp_path / "objects"
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    TextProcessor(store).process(capture_for(raw_object))

    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before
    assert store.read_bytes(raw_object) == payload


def test_two_captures_of_one_stored_original_yield_two_content_objects(
    store: LocalRawObjectStore,
) -> None:
    """Exact byte deduplication belongs to storage, not to captures or content."""
    processor = TextProcessor(store)
    payload = b"identical bytes captured twice"
    first = processor.process(capture_for(store.store_bytes(payload), capture_id="cap_a"))
    second = processor.process(capture_for(store.store_bytes(payload), capture_id="cap_b"))

    assert first.id != second.id
    assert first.source.capture_id != second.source.capture_id
    assert first.original.sha256 == second.original.sha256
    assert first.assets[0].ref == second.assets[0].ref

    # One stored original, one digest — but an asset is a record inside one
    # content object, so each run mints its own.
    assert first.original.asset_id != second.original.asset_id
    assert first.assets[0].id != second.assets[0].id


def test_a_capture_whose_original_was_never_stored_fails_as_a_storage_error(
    store: LocalRawObjectStore,
) -> None:
    digest = hashlib.sha256(b"absent").hexdigest()
    capture = capture_for(RawObjectRef(id=digest, sha256=digest, ref=build_raw_ref(digest)))

    with pytest.raises(RawObjectNotFoundError):
        TextProcessor(store).process(capture)
