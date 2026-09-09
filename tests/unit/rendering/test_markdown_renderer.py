"""Behaviour of the lossy, readable Markdown projection."""

import pytest

from core.contracts import ContentObject
from core.rendering import BLOCK_SEPARATOR, MarkdownRenderer
from tests.unit.rendering.builders import (
    ORIGINAL_SHA256,
    UNICODE_TEXT,
    WHITESPACE_TEXT,
    make_content_object,
    make_segment,
    make_textless_segment,
)


def one_segment(text: str) -> ContentObject:
    """A content object carrying exactly one text segment and no title."""
    return make_content_object(title=None, segments=[make_segment(text=text)])


# --- identity --------------------------------------------------------------


def test_renderer_identity(markdown_renderer: MarkdownRenderer) -> None:
    assert (
        markdown_renderer.name,
        markdown_renderer.version,
        markdown_renderer.media_type,
    ) == ("markdown", "0.1", "text/markdown")


def test_block_separator_is_one_blank_line() -> None:
    assert BLOCK_SEPARATOR == "\n\n"


# --- the shape of the projection -------------------------------------------


def test_single_text_segment_renders_as_its_text(
    markdown_renderer: MarkdownRenderer,
) -> None:
    assert markdown_renderer.render(one_segment("hello")) == "hello"


def test_title_becomes_a_single_heading(markdown_renderer: MarkdownRenderer) -> None:
    content = make_content_object(title="Note", segments=[make_segment(text="hello")])

    assert markdown_renderer.render(content) == "# Note\n\nhello"


def test_no_title_means_no_heading_at_all(markdown_renderer: MarkdownRenderer) -> None:
    """Nothing is invented from the content id, the capture, or the text."""
    content = one_segment("hello")

    rendered = markdown_renderer.render(content)

    assert rendered == "hello"
    assert not rendered.startswith("#")
    assert content.id not in rendered
    assert content.source.capture_id not in rendered


def test_multiple_text_segments_keep_list_order(
    markdown_renderer: MarkdownRenderer,
) -> None:
    content = make_content_object(
        title=None,
        segments=[make_segment(id="seg_a", text="a"), make_segment(id="seg_b", text="b")],
    )

    assert markdown_renderer.render(content) == "a\n\nb"


def test_position_does_not_reorder_segments(markdown_renderer: MarkdownRenderer) -> None:
    """``position`` is optional metadata; list order is the canonical order."""
    content = make_content_object(
        title=None,
        segments=[
            make_segment(id="seg_a", text="first", position=90),
            make_segment(id="seg_b", text="second", position=10),
            make_segment(id="seg_c", text="third", position=None),
        ],
    )

    assert markdown_renderer.render(content) == "first\n\nsecond\n\nthird"


def test_blocks_are_separated_by_exactly_one_blank_line(
    markdown_renderer: MarkdownRenderer,
) -> None:
    content = make_content_object(
        title="Note",
        segments=[make_segment(id="seg_a", text="a"), make_segment(id="seg_b", text="b")],
    )

    assert markdown_renderer.render(content) == BLOCK_SEPARATOR.join(["# Note", "a", "b"])


# --- text is passed through untouched --------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "line 1\r\nline 2",
        "\ttabbed\tcolumns",
        "   leading and trailing   ",
        "keeps\n\n\n\nblank lines",
        UNICODE_TEXT,
        "é is not é",
        "*not* escaped [either](x) <b>or</b> & \\backslash",
        WHITESPACE_TEXT,
    ],
)
def test_segment_text_is_reproduced_exactly(markdown_renderer: MarkdownRenderer, text: str) -> None:
    assert markdown_renderer.render(one_segment(text)) == text


def test_no_trailing_newline_is_appended(markdown_renderer: MarkdownRenderer) -> None:
    assert markdown_renderer.render(one_segment("hello")) == "hello"


def test_a_segments_own_trailing_newline_is_kept(
    markdown_renderer: MarkdownRenderer,
) -> None:
    """The output ends exactly where the last block ends — no more, no less."""
    assert markdown_renderer.render(one_segment("hello\n")) == "hello\n"


# --- what is left out ------------------------------------------------------


def test_segments_without_text_are_omitted(markdown_renderer: MarkdownRenderer) -> None:
    content = make_content_object(
        title=None,
        segments=[
            make_segment(id="seg_a", text="a"),
            make_textless_segment(),
            make_segment(id="seg_b", text="b"),
        ],
    )

    assert markdown_renderer.render(content) == "a\n\nb"


def test_empty_projection_is_the_empty_string(markdown_renderer: MarkdownRenderer) -> None:
    content = make_content_object(title=None, segments=[make_textless_segment()])

    assert markdown_renderer.render(content) == ""


def test_no_title_and_no_segments_is_the_empty_string(
    markdown_renderer: MarkdownRenderer,
) -> None:
    content = make_content_object(title=None, segments=[])

    assert markdown_renderer.render(content) == ""


def test_machine_state_is_not_projected(
    markdown_renderer: MarkdownRenderer, content: ContentObject
) -> None:
    """Ids, digests, asset refs, provenance and processing stay out of Markdown."""
    rendered = markdown_renderer.render(content)

    absent = (
        content.id,
        content.source.capture_id,
        content.schema_version,
        ORIGINAL_SHA256,
        "ast_original",
        "seg_01",
        "sha256:",
        "provenance",
        "processing",
        "text/html",
        "https://example.com/architecture",
    )
    for value in absent:
        assert value not in rendered


def test_derived_analysis_is_not_projected(markdown_renderer: MarkdownRenderer) -> None:
    """``derived`` is analysis output; it is not part of the readable text."""
    content = make_content_object(
        title="Note",
        segments=[make_segment(text="body")],
        derived={
            "summary": "SUMMARY-MARKER",
            "topics": ["TOPIC-MARKER"],
            "entities": [{"name": "ENTITY-MARKER"}],
        },
    )

    rendered = markdown_renderer.render(content)

    assert rendered == "# Note\n\nbody"
    for marker in ("SUMMARY-MARKER", "TOPIC-MARKER", "ENTITY-MARKER"):
        assert marker not in rendered


def test_metadata_is_not_projected(markdown_renderer: MarkdownRenderer) -> None:
    content = make_content_object(
        title="Note",
        segments=[make_segment(text="body", metadata={"SEGMENT-KEY": "SEGMENT-VALUE"})],
        metadata={"OBJECT-KEY": "OBJECT-VALUE"},
    )

    rendered = markdown_renderer.render(content)

    assert rendered == "# Note\n\nbody"
    for marker in ("OBJECT-KEY", "OBJECT-VALUE", "SEGMENT-KEY", "SEGMENT-VALUE"):
        assert marker not in rendered


def test_output_has_no_frontmatter(
    markdown_renderer: MarkdownRenderer, content: ContentObject
) -> None:
    rendered = markdown_renderer.render(content)

    assert not rendered.startswith("---")
    assert "---" not in rendered


def test_output_is_not_json(markdown_renderer: MarkdownRenderer, content: ContentObject) -> None:
    rendered = markdown_renderer.render(content)

    assert not rendered.lstrip().startswith("{")
    assert '"segments"' not in rendered


# --- purity ----------------------------------------------------------------


def test_repeated_renders_are_identical(
    markdown_renderer: MarkdownRenderer, content: ContentObject
) -> None:
    assert markdown_renderer.render(content) == markdown_renderer.render(content)


def test_separate_renderer_instances_agree(content: ContentObject) -> None:
    assert MarkdownRenderer().render(content) == MarkdownRenderer().render(content)


def test_equal_objects_render_identically(markdown_renderer: MarkdownRenderer) -> None:
    assert markdown_renderer.render(make_content_object()) == markdown_renderer.render(
        make_content_object()
    )


def test_rendering_does_not_mutate_the_content_object(
    markdown_renderer: MarkdownRenderer, content: ContentObject
) -> None:
    before = content.model_copy(deep=True)

    markdown_renderer.render(content)

    assert content == before
    assert content.model_dump(mode="json") == before.model_dump(mode="json")
