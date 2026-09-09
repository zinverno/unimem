"""The human- and LLM-readable Markdown projection of a content object.

Markdown is **deliberately lossy**. It is a text projection for people and
language models to read: an optional title, followed by the text the content
object actually carries, in the order the object carries it. Nothing else.

It is not a serialization format and must not become one. Ids, digests, asset
references, provenance, processing history, metadata, topics, and entities are
all absent on purpose — adding them to make the output reversible would turn a
readable projection into a second, worse encoding of the canonical object, and
invite consumers to parse Markdown back into domain state. Consumers that need
machine state read the ``ContentObject`` or its JSON projection
(:mod:`core.rendering.json`). See
`ADR-005 <../../docs/ADR/ADR-005-derived-representations.md>`_.

Segment text is passed through untouched: no trimming, no Unicode
normalization, no line-ending rewriting, no escaping, no chunking, no
summarizing. This mirrors :mod:`core.processing.text`, which took the same care
to get the text into the segment unchanged; undoing that during rendering would
make the projection a poorer witness of the original than the object it came
from.
"""

from typing import Final

from core.contracts import ContentObject

#: What separates two rendered blocks: one blank line, exactly. Whatever
#: whitespace a block carries internally — or at its own edges — is the block's,
#: and is neither trimmed nor padded.
BLOCK_SEPARATOR: Final = "\n\n"


class MarkdownRenderer:
    """Renders a content object as readable Markdown text."""

    name = "markdown"
    version = "0.1"
    media_type = "text/markdown"

    def render(self, content: ContentObject) -> str:
        """Project the title and the textual segments into Markdown.

        The title, when there is one, becomes a single ``#`` heading. Nothing
        is invented when there is not: no heading is synthesized from the
        content id, the capture, or the first line of a segment, because a
        fabricated title is indistinguishable from a real one downstream.

        Segments are rendered in the order they are stored on the object. They
        are explicitly *not* sorted by ``position``: list order is the content
        object's canonical order, ``position`` is optional and free to be
        absent or duplicated, and sorting here would let a renderer disagree
        with the object about what the content says.

        Segments carrying no text are omitted — a bounding box or a timeline
        cue has no textual projection — and an object with no title and no
        textual segment renders as the empty string. No trailing newline is
        appended: the output ends exactly where the last block ends.

        The content object is only read; nothing on it is modified.
        """
        blocks: list[str] = []
        if content.title is not None:
            blocks.append(f"# {content.title}")
        blocks.extend(segment.text for segment in content.segments if segment.text is not None)
        return BLOCK_SEPARATOR.join(blocks)
