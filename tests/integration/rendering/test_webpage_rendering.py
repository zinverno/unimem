"""Rendering a web content object, which needs no new renderer.

Phase 0D's projections are typed against ``ContentObject``, not against a
modality. A ``web`` object is a content object with a title and one text
segment, so ``MarkdownRenderer`` and ``JsonRenderer`` already handle it by their
existing generic semantics.

This file is the focused compatibility check that says so. It is deliberately
small, and deliberately does **not** ask for anything new: there is no HTML
renderer, no derived Markdown is persisted anywhere, and the renderers are
unchanged by Phase 2.
"""

import json
from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    ContentObject,
    ContentType,
)
from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import (
    ProcessingOrchestrator,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from core.rendering import JsonRenderer, MarkdownRenderer
from core.storage import LocalRawObjectStore
from tests.unit.processing.builders import CAPTURED_AT

PAGE = (
    "<!doctype html><html><head><title>Example &amp; Test</title></head>"
    "<body><h1>Hello</h1><p>First paragraph.</p><p>Second paragraph.</p></body></html>"
)


@pytest.fixture
def content(tmp_path: Path) -> ContentObject:
    """A real web content object, produced by the real pipeline."""
    database = tmp_path / "unimem.sqlite3"
    raw_store = LocalRawObjectStore(tmp_path / "raw")
    record_store = SqliteCaptureRecordStore(database)
    content_store = SqliteContentObjectStore(database)
    intake = CaptureIntake(raw_store, record_store)
    orchestrator = ProcessingOrchestrator(
        ProcessorRouter([TextProcessor(raw_store), WebpageProcessor(raw_store)]),
        record_store,
        content_store,
    )
    intake.accept(
        CaptureEnvelope(
            id="cap_render_web",
            source=CaptureSource(
                type=CaptureSourceType.BROWSER, provider="chromium", url="https://example.com/a"
            ),
            payload=CapturePayload(
                type=CapturePayloadType.WEBPAGE, mime_type="text/html", html=PAGE
            ),
            context=CaptureContext(captured_at=CAPTURED_AT),
        )
    )
    return orchestrator.process("cap_render_web")


class TestMarkdown:
    def test_it_renders_without_any_change_to_the_renderer(self, content: ContentObject) -> None:
        rendered = MarkdownRenderer().render(content)

        assert rendered == "# Example & Test\n\nHello\n\nFirst paragraph.\n\nSecond paragraph."

    def test_the_extracted_title_becomes_the_heading(self, content: ContentObject) -> None:
        assert MarkdownRenderer().render(content).startswith("# Example & Test")

    def test_no_markup_reaches_the_rendering(self, content: ContentObject) -> None:
        """The segment is already text; the renderer has nothing to strip."""
        rendered = MarkdownRenderer().render(content)

        for fragment in ("<p>", "<h1>", "<!doctype", "&amp;"):
            assert fragment not in rendered

    def test_the_renderer_is_unchanged(self) -> None:
        assert MarkdownRenderer.name == "markdown"
        assert MarkdownRenderer.version == "0.1"

    def test_nothing_derived_is_persisted_on_the_object(self, content: ContentObject) -> None:
        """Markdown is derived on demand and stored nowhere."""
        assert "markdown" not in content.model_dump_json()
        assert content.metadata == {}


class TestJson:
    def test_it_projects_a_web_object(self, content: ContentObject) -> None:
        projected = json.loads(JsonRenderer().render(content))

        assert projected["type"] == ContentType.WEB.value
        assert projected["source"]["url"] == "https://example.com/a"

    def test_it_is_full_fidelity(self, content: ContentObject) -> None:
        projected = json.loads(JsonRenderer().render(content))

        assert projected == json.loads(content.model_dump_json())
