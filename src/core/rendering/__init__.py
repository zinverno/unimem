"""Rendering: deriving external representations from canonical content.

Phase 0D answers one question — how does an existing
:class:`~core.contracts.content.ContentObject` become something outside the
system can read?

::

    ContentObject -> JsonRenderer     -> JSON string
    ContentObject -> MarkdownRenderer -> Markdown string

The content object stays the source of truth. :class:`JsonRenderer` is the
full-fidelity projection and round-trips back into a ``ContentObject``;
:class:`MarkdownRenderer` is a deliberately lossy readable projection. Neither
is ever read back as domain state.

Renderers are pure: no storage, no filesystem, no network, no clock, no
mutation. Nothing here persists, writes, or exports anything, and there is no
registry, router, or discovery — a caller picks a renderer and calls it.
"""

from core.rendering.base import Renderer
from core.rendering.json import JsonRenderer
from core.rendering.markdown import BLOCK_SEPARATOR, MarkdownRenderer

__all__ = [
    "BLOCK_SEPARATOR",
    "JsonRenderer",
    "MarkdownRenderer",
    "Renderer",
]
