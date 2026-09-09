"""The renderer port.

A renderer answers one question: how does an existing canonical
:class:`~core.contracts.content.ContentObject` become an external, readable
representation? It is a **projection**, not a second source of truth — the
content object is the truth, and every rendered string is derived from it and
reproducible from it.

Renderers are pure functions of a content object. A renderer reads no store,
opens no file, makes no network call, consults no clock, reads no environment,
and mutates nothing — not the object it is handed, not anything else. The same
validated ``ContentObject`` rendered by the same renderer version always
produces the same string.

The API is synchronous and takes no options. There is deliberately no registry,
no discovery, no router, no template engine, and no configuration: an
application that renders picks a renderer and calls it. See
`ADR-005 <../../docs/ADR/ADR-005-derived-representations.md>`_.
"""

from typing import Protocol

from core.contracts import ContentObject


class Renderer(Protocol):
    """Derives one external representation from a canonical content object.

    ``name``, ``version``, and ``media_type`` are the renderer's stable
    identity. They describe *rendering semantics*, not deployment: they are not
    the package version and not a commit SHA. ``version`` changes when the
    output for an unchanged content object changes, which is what makes
    "reproducible from the object plus the renderer version" a checkable claim.
    """

    name: str
    version: str
    media_type: str

    def render(self, content: ContentObject) -> str:
        """Return the derived representation of ``content``.

        Takes a validated content object and returns a string. It does not
        persist, write, or transmit anything: where the result goes is the
        caller's decision, and no such caller exists yet.
        """
        ...
