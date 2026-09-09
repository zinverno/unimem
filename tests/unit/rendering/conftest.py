"""Fixtures shared by the rendering tests."""

import pytest

from core.contracts import ContentObject
from core.rendering import JsonRenderer, MarkdownRenderer
from tests.unit.rendering.builders import make_content_object


@pytest.fixture
def content() -> ContentObject:
    """A fully populated canonical content object."""
    return make_content_object()


@pytest.fixture
def json_renderer() -> JsonRenderer:
    return JsonRenderer()


@pytest.fixture
def markdown_renderer() -> MarkdownRenderer:
    return MarkdownRenderer()
