"""Conformance to the ``Renderer`` port.

The bindings below are the real assertion: mypy checks every renderer against
the protocol at type-check time, which is where structural conformance is
decided. The runtime tests then pin the identity values, because those travel
outside the process — a caller records what rendered a string.
"""

import inspect

import pytest

from core.contracts import ContentObject
from core.rendering import JsonRenderer, MarkdownRenderer, Renderer

#: Type-checked conformance: assigning to ``Renderer`` fails mypy if a renderer
#: is missing an attribute, gets one's type wrong, or has a different
#: ``render`` signature.
RENDERERS: tuple[Renderer, ...] = (JsonRenderer(), MarkdownRenderer())


def render_through_the_port(renderer: Renderer, content: ContentObject) -> str:
    """Call a renderer knowing nothing about it but the protocol."""
    return renderer.render(content)


@pytest.mark.parametrize("renderer", RENDERERS, ids=lambda renderer: renderer.name)
def test_renderer_satisfies_the_port(renderer: Renderer, content: ContentObject) -> None:
    assert isinstance(render_through_the_port(renderer, content), str)


@pytest.mark.parametrize("renderer", RENDERERS, ids=lambda renderer: renderer.name)
def test_identity_is_three_non_empty_strings(renderer: Renderer) -> None:
    for value in (renderer.name, renderer.version, renderer.media_type):
        assert isinstance(value, str)
        assert value


def test_identities_are_the_documented_ones() -> None:
    """``name/version/media_type`` are a stable, externally visible identity."""
    assert [(r.name, r.version, r.media_type) for r in RENDERERS] == [
        ("json", "0.1", "application/json"),
        ("markdown", "0.1", "text/markdown"),
    ]


@pytest.mark.parametrize("renderer", RENDERERS, ids=lambda renderer: renderer.name)
def test_render_takes_only_the_content_object(renderer: Renderer) -> None:
    """No options argument, no keyword configuration, no rendering context."""
    parameters = inspect.signature(renderer.render).parameters

    assert list(parameters) == ["content"]


@pytest.mark.parametrize("renderer", RENDERERS, ids=lambda renderer: renderer.name)
def test_rendering_is_pure_over_the_port(renderer: Renderer, content: ContentObject) -> None:
    """Same object in, same string out, and the object comes back untouched."""
    before = content.model_copy(deep=True)

    first = render_through_the_port(renderer, content)
    second = render_through_the_port(renderer, content)

    assert first == second
    assert content == before
