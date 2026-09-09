"""Rendering the content object a real processor produced.

The unit tests build content objects directly. These prove the vertical slice:
bytes -> LocalRawObjectStore -> TextProcessor -> ContentObject -> renderers.
Nothing here reaches into how ``TextProcessor`` works — it is used only as a
source of a realistic canonical object, exactly as any other producer would be.
"""

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
)
from core.processing import TextProcessor
from core.rendering import JsonRenderer, MarkdownRenderer
from core.storage import LocalRawObjectStore
from tests.unit.processing.builders import CAPTURED_AT, RECEIVED_AT

GREETING = "Привет, мир — 你好 🌍"
NOTES = f"# Notes\r\n\r\n{GREETING}\n\n\tindented line   \n"


@pytest.fixture
def content(tmp_path: Path) -> ContentObject:
    """A canonical content object produced by the real text processor."""
    store = LocalRawObjectStore(tmp_path / "objects")
    raw_object = store.store_bytes(NOTES.encode(), mime_type="text/plain")
    capture = CaptureRecord(
        id="cap_render_int_01",
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
    return TextProcessor(store).process(capture)


def test_processed_content_round_trips_through_json(content: ContentObject) -> None:
    rendered = JsonRenderer().render(content)

    assert ContentObject.model_validate_json(rendered) == content


def test_processed_content_renders_the_stored_text_verbatim(content: ContentObject) -> None:
    """The processor kept the bytes intact; the projection keeps them intact too."""
    rendered = MarkdownRenderer().render(content)

    assert rendered == NOTES
    assert GREETING in rendered
    assert "\r\n" in rendered
    assert "\t" in rendered


def test_markdown_leaves_the_processors_bookkeeping_out(content: ContentObject) -> None:
    """Ids, digests, refs and the processing record exist — in JSON, not Markdown."""
    markdown = MarkdownRenderer().render(content)
    rendered_json = JsonRenderer().render(content)
    digest = content.original.sha256
    assert digest is not None

    for value in (content.id, content.source.capture_id, digest, "processing"):
        assert value not in markdown
        assert value in rendered_json


def test_rendering_touches_neither_the_object_nor_the_store(
    content: ContentObject, tmp_path: Path
) -> None:
    """A renderer is a function of the object alone."""
    before = content.model_copy(deep=True)
    stored_before = sorted(path.name for path in (tmp_path / "objects").rglob("*"))

    JsonRenderer().render(content)
    MarkdownRenderer().render(content)

    assert content == before
    assert sorted(path.name for path in (tmp_path / "objects").rglob("*")) == stored_before


def test_both_projections_are_reproducible(content: ContentObject) -> None:
    """Same object, same renderer version, same output — every time."""
    assert JsonRenderer().render(content) == JsonRenderer().render(content)
    assert MarkdownRenderer().render(content) == MarkdownRenderer().render(content)
