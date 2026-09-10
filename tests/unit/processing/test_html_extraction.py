"""The HTML text extraction algorithm, specified test by test.

This is the file that defines what "canonical webpage text" means in UniMem, so
it is deliberately exhaustive and deliberately literal: every test asserts an
exact output string rather than "contains". An extraction whose result can only
be described loosely is one nobody can reproduce, and reproducibility from the
immutable original is the entire justification for deriving text at all.

Nothing here involves a capture, a store, or a content object. Extraction is a
pure function of an HTML string, and it is tested as one.

Two things are *not* claimed anywhere below, on purpose:

* **No CSS semantics.** There is no test that ``display: none`` hides text,
  because the extractor does not evaluate CSS and must not pretend to. The one
  test on the subject asserts the opposite — hidden-by-CSS text comes out — so
  the limitation is recorded rather than implied.
* **No article extraction.** There is no test that navigation is stripped or
  that "the main content" is found, because none of that is attempted.
"""

import pytest

from core.processing.webpage import BLOCK_TAGS, IGNORED_TAGS, HtmlTextExtractor, extract


def text_of(html: str) -> str:
    return extract(html)[0]


def title_of(html: str) -> str | None:
    return extract(html)[1]


class TestOrdinaryTextFlow:
    def test_a_single_paragraph(self) -> None:
        assert text_of("<p>Hello world.</p>") == "Hello world."

    def test_bare_text_with_no_markup_at_all(self) -> None:
        assert text_of("just words") == "just words"

    def test_two_paragraphs_are_separated_by_a_blank_line(self) -> None:
        assert text_of("<p>One.</p><p>Two.</p>") == "One.\n\nTwo."

    def test_inline_elements_do_not_break_a_line(self) -> None:
        assert text_of("<p>First <strong>bold</strong> last.</p>") == "First bold last."

    def test_adjacent_inline_elements_do_not_gain_a_space(self) -> None:
        """The markup says there is no space, so there is no space."""
        assert text_of("<p><em>a</em><em>b</em></p>") == "ab"

    def test_a_whitespace_only_text_node_keeps_its_one_space(self) -> None:
        """It is a space between two words, not indentation. Collapsed, not dropped."""
        assert text_of("<p><em>a</em> <em>b</em></p>") == "a b"

    def test_text_outside_any_element_is_still_content(self) -> None:
        assert text_of("before<p>inside</p>after") == "before\n\ninside\n\nafter"


class TestNestedBlocks:
    def test_nested_divs_do_not_multiply_blank_lines(self) -> None:
        assert text_of("<div><div><div>deep</div></div></div>") == "deep"

    def test_two_nested_blocks_with_text_are_separated_once(self) -> None:
        assert text_of("<div><p>a</p><p>b</p></div>") == "a\n\nb"

    def test_a_wrapper_around_a_paragraph_adds_nothing(self) -> None:
        assert text_of("<main><section><p>only</p></section></main>") == "only"

    def test_repeated_empty_blocks_between_text_collapse_to_one_break(self) -> None:
        assert text_of("<p>a</p><div></div><div></div><section></section><p>b</p>") == "a\n\nb"

    def test_leading_and_trailing_blocks_leave_no_blank_lines(self) -> None:
        assert text_of("<div></div><p>only</p><div></div>") == "only"

    @pytest.mark.parametrize("tag", sorted(BLOCK_TAGS))
    def test_every_block_tag_separates_text(self, tag: str) -> None:
        assert text_of(f"before<{tag}>inside</{tag}>after") == "before\n\ninside\n\nafter"


class TestHeadings:
    def test_a_heading_is_its_own_block(self) -> None:
        assert text_of("<h1>Title</h1><p>Body.</p>") == "Title\n\nBody."

    def test_every_heading_level_separates(self) -> None:
        html = "".join(f"<h{level}>H{level}</h{level}>" for level in range(1, 7))

        assert text_of(html) == "H1\n\nH2\n\nH3\n\nH4\n\nH5\n\nH6"

    def test_a_heading_is_not_marked_up_in_the_output(self) -> None:
        """The output is text. There is no ``#``, and this is not Markdown."""
        assert "#" not in text_of("<h1>Title</h1>")


class TestLists:
    def test_list_items_are_separate_blocks(self) -> None:
        assert text_of("<ul><li>a</li><li>b</li><li>c</li></ul>") == "a\n\nb\n\nc"

    def test_ordered_lists_are_not_numbered(self) -> None:
        """No bullet, marker, or number is generated: none was in the text."""
        assert text_of("<ol><li>first</li><li>second</li></ol>") == "first\n\nsecond"

    def test_a_nested_list_stays_flat(self) -> None:
        html = "<ul><li>outer<ul><li>inner</li></ul></li></ul>"

        assert text_of(html) == "outer\n\ninner"


class TestLineBreaks:
    def test_br_breaks_a_line_without_a_blank_one(self) -> None:
        assert text_of("<p>one<br>two</p>") == "one\ntwo"

    def test_a_self_closing_br_behaves_the_same(self) -> None:
        assert text_of("<p>one<br/>two</p>") == "one\ntwo"

    def test_consecutive_brs_collapse_to_one_blank_line(self) -> None:
        assert text_of("<p>one<br><br><br>two</p>") == "one\n\ntwo"

    def test_a_br_at_the_edge_of_a_block_leaves_nothing_behind(self) -> None:
        assert text_of("<p><br>one<br></p>") == "one"


class TestTables:
    def test_cells_and_rows_are_blocks(self) -> None:
        html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"

        assert text_of(html) == "a\n\nb\n\nc\n\nd"

    def test_header_cells_are_included(self) -> None:
        html = "<table><tr><th>Name</th></tr><tr><td>Ada</td></tr></table>"

        assert text_of(html) == "Name\n\nAda"

    def test_no_table_layout_is_reconstructed(self) -> None:
        """No pipes, no alignment, no grid. This is text, not a rendering."""
        assert "|" not in text_of("<table><tr><td>a</td><td>b</td></tr></table>")


class TestEntities:
    def test_named_entities_are_decoded(self) -> None:
        assert text_of("<p>a &amp; b</p>") == "a & b"

    def test_angle_bracket_entities_do_not_become_markup(self) -> None:
        assert text_of("<p>&lt;script&gt;</p>") == "<script>"

    def test_numeric_references_are_decoded(self) -> None:
        assert text_of("<p>&#233; &#x2014;</p>") == "é —"

    def test_a_doubly_escaped_ampersand_decodes_once(self) -> None:
        assert text_of("<p>&amp;amp;</p>") == "&amp;"

    def test_nbsp_is_preserved_and_not_collapsed(self) -> None:
        """A no-break space is a character the page asked for, not indentation."""
        assert text_of("<p>Second&nbsp;paragraph.</p>") == "Second\u00a0paragraph."

    def test_a_run_of_nbsp_is_preserved_in_full(self) -> None:
        assert text_of("<p>a&nbsp;&nbsp;&nbsp;b</p>") == "a\u00a0\u00a0\u00a0b"

    def test_nbsp_at_an_edge_is_not_trimmed_away(self) -> None:
        assert text_of("<p>&nbsp;edge&nbsp;</p>") == "\u00a0edge\u00a0"


class TestUnicode:
    def test_non_ascii_text_survives(self) -> None:
        assert text_of("<p>Привет 你好 \U0001f30d</p>") == "Привет 你好 \U0001f30d"

    def test_combining_marks_are_not_normalized(self) -> None:
        """No NFC, no NFD. Decomposed stays decomposed and is not equal to precomposed."""
        extracted = text_of("<p>Å</p>")

        assert extracted == "Å"
        assert extracted != "Å"

    def test_no_case_conversion(self) -> None:
        assert text_of("<p>MiXeD CaSe</p>") == "MiXeD CaSe"

    def test_unicode_whitespace_other_than_ascii_is_left_alone(self) -> None:
        """Only ASCII formatting whitespace is collapsed."""
        assert text_of("<p>a\u2003b</p>") == "a\u2003b"


class TestIgnoredElements:
    def test_script_content_is_not_text(self) -> None:
        assert text_of('<p>real</p><script>var secret = "not content";</script>') == "real"

    def test_script_markup_lookalikes_do_not_escape(self) -> None:
        assert text_of("<script>if (a < b) { x('</p>') }</script><p>real</p>") == "real"

    def test_style_content_is_not_text(self) -> None:
        assert text_of("<style>.x { display: none; }</style><p>real</p>") == "real"

    def test_noscript_content_is_not_text(self) -> None:
        assert text_of("<noscript>Enable JavaScript</noscript><p>real</p>") == "real"

    def test_template_content_is_not_text(self) -> None:
        assert text_of("<template><p>a template</p></template><p>real</p>") == "real"

    def test_svg_text_is_not_page_text(self) -> None:
        assert text_of("<svg><text>label</text></svg><p>real</p>") == "real"

    def test_an_svg_title_does_not_become_the_page_title(self) -> None:
        assert title_of("<svg><title>drawing</title></svg><p>real</p>") is None

    def test_nested_markup_inside_an_ignored_element_is_ignored_too(self) -> None:
        html = "<template><div><h1>heading</h1><p>para</p></div></template><p>real</p>"

        assert text_of(html) == "real"

    def test_ignored_elements_nest(self) -> None:
        html = "<template><noscript>inner</noscript>outer</template><p>real</p>"

        assert text_of(html) == "real"

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_every_ignored_tag_hides_its_content(self, tag: str) -> None:
        assert text_of(f"<{tag}>hidden</{tag}><p>real</p>") == "real"

    def test_an_ignored_element_does_not_swallow_what_follows(self) -> None:
        assert text_of("<p>a</p><script>x</script><p>b</p>") == "a\n\nb"


class TestAnIgnoredSubtreeNeverEatsWhatFollowsIt:
    """The regression class for the bug this file exists to make impossible.

    An ignored element is a *subtree*, not a switch. It must end at its own end
    tag and leave every following sibling untouched. Get that wrong and the
    failure is silent and awful: the page is stored, a segment is produced, the
    capture reports ``complete``, and everything after the first ``<script>``
    is gone with no error anywhere.

    The representative smoke page has exactly this shape — a paragraph, a
    ``<script>``, then another paragraph — so it is pinned here for every
    ignored tag rather than only for the one that happened to be in the fixture.
    """

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_visible_blocks_on_both_sides_survive(self, tag: str) -> None:
        html = f"<p>before</p><{tag}>hidden</{tag}><p>after</p>"

        assert text_of(html) == "before\n\nafter"

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_the_ignored_content_itself_is_gone(self, tag: str) -> None:
        html = f"<p>before</p><{tag}>hidden</{tag}><p>after</p>"

        assert "hidden" not in text_of(html)

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_inline_text_on_both_sides_survives(self, tag: str) -> None:
        """Not just blocks: an ignored element inside a run of text, too."""
        html = f"<p>before <{tag}>hidden</{tag}> after</p>"
        extracted = text_of(html)

        assert "before" in extracted
        assert "after" in extracted
        assert "hidden" not in extracted

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_a_nested_subtree_after_it_survives(self, tag: str) -> None:
        html = f"<{tag}>hidden</{tag}><main><section><p>after</p></section></main>"

        assert text_of(html) == "after"

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_several_of_them_interleaved_with_text(self, tag: str) -> None:
        html = f"<p>one</p><{tag}>x</{tag}><p>two</p><{tag}>y</{tag}><p>three</p>"

        assert text_of(html) == "one\n\ntwo\n\nthree"

    @pytest.mark.parametrize("tag", sorted(IGNORED_TAGS))
    def test_a_title_after_it_is_still_found(self, tag: str) -> None:
        """Title collection must survive an ignored subtree too."""
        html = f"<head><{tag}>hidden</{tag}><title>Real Title</title></head><body><p>b</p></body>"

        assert extract(html) == ("b", "Real Title")

    def test_the_representative_smoke_page_extracts_all_three_pieces(self) -> None:
        """The exact acceptance page, asserted exactly.

        This is the page the README documents and the real HTTP smoke posts, and
        it is pinned here so the extraction contract for it is checkable without
        a server, a store, or a capture.
        """
        html = (
            "<!doctype html>\n"
            "<html>\n"
            "<head>\n"
            "  <title>Example &amp; Test</title>\n"
            "  <style>.x { display: none; }</style>\n"
            "</head>\n"
            "<body>\n"
            "  <main>\n"
            "    <h1>Hello</h1>\n"
            "    <p>First <strong>paragraph</strong>.</p>\n"
            '    <script>window.secret = "not content"</script>\n'
            "    <p>Second&nbsp;paragraph.</p>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )

        text, title = extract(html)

        assert title == "Example & Test"
        assert text == "Hello\n\nFirst paragraph.\n\nSecond\u00a0paragraph."
        assert "window.secret" not in text
        assert "display: none" not in text


class TestCommentsAndDeclarations:
    def test_comments_are_not_content(self) -> None:
        assert text_of("<p>a<!-- hidden note -->b</p>") == "ab"

    def test_a_comment_between_blocks_changes_nothing(self) -> None:
        assert text_of("<p>a</p><!-- note --><p>b</p>") == "a\n\nb"

    def test_a_comment_containing_markup_stays_a_comment(self) -> None:
        assert text_of("<!-- <p>not real</p> --><p>real</p>") == "real"

    def test_a_doctype_is_not_content(self) -> None:
        assert text_of("<!doctype html><p>real</p>") == "real"

    def test_a_processing_instruction_is_not_content(self) -> None:
        assert text_of("<?xml version='1.0'?><p>real</p>") == "real"

    def test_a_conditional_comment_is_not_content(self) -> None:
        assert text_of("<!--[if IE]><p>old</p><![endif]--><p>real</p>") == "real"


class TestHeadIsNotBody:
    def test_meta_and_link_text_never_appears(self) -> None:
        html = (
            "<html><head>"
            '<meta name="description" content="a description">'
            '<meta name="keywords" content="a,b,c">'
            '<link rel="canonical" href="https://example.com/">'
            "</head><body><p>real</p></body></html>"
        )

        assert text_of(html) == "real"

    def test_stray_text_in_head_is_excluded(self) -> None:
        html = "<html><head>stray head text</head><body><p>real</p></body></html>"

        assert text_of(html) == "real"

    def test_json_ld_is_not_scraped(self) -> None:
        html = (
            '<head><script type="application/ld+json">{"name": "structured"}</script></head>'
            "<body><p>real</p></body>"
        )

        assert text_of(html) == "real"

    def test_opengraph_is_not_read(self) -> None:
        html = '<head><meta property="og:title" content="Social Title"></head><body><p>x</p></body>'

        assert text_of(html) == "x"
        assert title_of(html) is None

    def test_the_title_is_not_joined_into_the_body(self) -> None:
        html = "<html><head><title>The Title</title></head><body><p>The body.</p></body></html>"

        assert text_of(html) == "The body."

    def test_body_text_is_included(self) -> None:
        html = "<html><head><title>T</title></head><body><p>included</p></body></html>"

        assert text_of(html) == "included"


class TestSourceFormattingIsNotContent:
    def test_indentation_does_not_become_content(self) -> None:
        html = "<div>\n\t\t\t\t<p>\n\t\t\t\t\tindented source\n\t\t\t\t</p>\n\t\t\t</div>"

        assert text_of(html) == "indented source"

    def test_a_run_of_spaces_collapses_to_one(self) -> None:
        assert text_of("<p>a          b</p>") == "a b"

    def test_newlines_inside_a_paragraph_become_spaces(self) -> None:
        assert text_of("<p>wrapped\nacross\nlines</p>") == "wrapped across lines"

    def test_whitespace_around_a_block_boundary_is_trimmed(self) -> None:
        assert text_of("<p>   a   </p>\n\n   <p>   b   </p>") == "a\n\nb"

    def test_whitespace_spanning_two_text_nodes_collapses_once(self) -> None:
        assert text_of("<p>a <em> </em> b</p>") == "a b"

    def test_a_deeply_formatted_document_yields_compact_text(self) -> None:
        html = (
            "<!doctype html>\n<html>\n  <body>\n    <main>\n"
            "      <h1>\n        Title\n      </h1>\n"
            "      <p>\n        Body text.\n      </p>\n"
            "    </main>\n  </body>\n</html>\n"
        )

        assert text_of(html) == "Title\n\nBody text."

    def test_pre_whitespace_is_collapsed_like_everything_else(self) -> None:
        """A documented limitation, asserted so it cannot drift silently.

        ``pre`` is a block boundary and nothing more. Preserving its whitespace
        would be a second whitespace policy; the exact bytes remain in the raw
        original, which is where anyone who needs them should look.
        """
        assert text_of("<pre>line one\n    line two</pre>") == "line one line two"


class TestMalformedButParseable:
    def test_an_unclosed_paragraph_still_yields_its_text(self) -> None:
        assert text_of("<p>unclosed") == "unclosed"

    def test_an_unclosed_block_before_another_still_separates(self) -> None:
        assert text_of("<p>first<div>second") == "first\n\nsecond"

    def test_a_stray_end_tag_is_harmless(self) -> None:
        assert text_of("</p><p>real</p></div>") == "real"

    def test_crossed_tags_are_handled_without_raising(self) -> None:
        assert text_of("<b><i>crossed</b></i>") == "crossed"

    def test_an_unknown_element_is_treated_as_inline(self) -> None:
        assert text_of("<p>a <custom-tag>b</custom-tag> c</p>") == "a b c"

    def test_an_unclosed_ignored_element_hides_the_rest(self) -> None:
        """Deterministic, and the same thing a parser would conclude."""
        assert text_of("<p>before</p><script>never closed <p>after</p>") == "before"

    def test_a_stray_title_end_tag_is_harmless(self) -> None:
        """``</title>`` with nothing open closes nothing and invents no title."""
        assert extract("</title><p>real</p>") == ("real", None)

    def test_a_stray_head_end_tag_does_not_unbalance_the_document(self) -> None:
        """``</head>`` with nothing open must not make later body text vanish."""
        assert text_of("</head><p>real</p>") == "real"

    def test_a_stray_head_end_tag_after_a_real_head_still_leaves_body_text(self) -> None:
        html = "<head><title>T</title></head></head><body><p>real</p></body>"

        assert extract(html) == ("real", "T")

    def test_an_attribute_value_is_never_content(self) -> None:
        html = '<p title="tooltip text"><img alt="alt text">visible</p>'

        assert text_of(html) == "visible"

    def test_a_url_in_an_href_is_never_content(self) -> None:
        assert text_of('<p><a href="https://example.com/secret">link</a></p>') == "link"


class TestNoBrowserSemanticsAreClaimed:
    """The limitations, written down as tests so they cannot be assumed away."""

    def test_css_hidden_text_is_still_extracted(self) -> None:
        """No CSS engine. ``display: none`` is a stylesheet fact, not a text fact."""
        html = '<p style="display: none">hidden by css</p><p>visible</p>'

        assert text_of(html) == "hidden by css\n\nvisible"

    def test_the_hidden_attribute_does_not_hide_text_either(self) -> None:
        assert text_of("<p hidden>still extracted</p>") == "still extracted"

    def test_navigation_and_footers_are_not_stripped(self) -> None:
        """This is not Readability, and it does not guess at boilerplate."""
        html = "<nav>Home About</nav><main><p>Article.</p></main><footer>© 2026</footer>"

        assert text_of(html) == "Home About\n\nArticle.\n\n© 2026"

    def test_css_pseudo_element_content_is_not_generated(self) -> None:
        html = '<style>p::before { content: "GENERATED"; }</style><p>real</p>'

        assert text_of(html) == "real"

    def test_script_generated_content_does_not_appear(self) -> None:
        html = "<div id='x'></div><script>document.getElementById('x').textContent='dynamic'"
        html += "</script><p>real</p>"

        assert text_of(html) == "real"


class TestPagesWithNoText:
    """Extraction reports emptiness; deciding what it *means* is the processor's job."""

    @pytest.mark.parametrize(
        ("label", "html"),
        [
            ("empty string", ""),
            ("whitespace only", "   \n\t  "),
            ("empty body", "<html><body></body></html>"),
            ("blank body", "<html><body>   \n   </body></html>"),
            ("script only", "<script>var x = 1;</script>"),
            ("style only", "<style>body { color: red; }</style>"),
            ("noscript only", "<noscript>enable js</noscript>"),
            ("template only", "<template><p>held</p></template>"),
            ("svg only", "<svg><text>label</text></svg>"),
            ("comments only", "<!-- one --><!-- two -->"),
            ("doctype only", "<!doctype html>"),
            ("head only", "<html><head><title>T</title></head></html>"),
            ("empty blocks", "<div></div><p></p><section></section>"),
            ("brs only", "<p><br><br></p>"),
        ],
        ids=lambda value: value if isinstance(value, str) and "<" not in value else None,
    )
    def test_it_extracts_to_the_empty_string(self, label: str, html: str) -> None:
        assert text_of(html) == ""


class TestTitleExtraction:
    def test_a_title_is_found(self) -> None:
        assert title_of("<html><head><title>The Title</title></head></html>") == "The Title"

    def test_entities_in_a_title_are_decoded(self) -> None:
        assert title_of("<title>Example &amp; Test</title>") == "Example & Test"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert title_of("<title>\n    Padded Title\n  </title>") == "Padded Title"

    def test_internal_whitespace_is_collapsed(self) -> None:
        assert title_of("<title>Two    words\nwrapped</title>") == "Two words wrapped"

    def test_a_blank_title_is_none(self) -> None:
        assert title_of("<title>   \n  </title>") is None

    def test_an_empty_title_is_none(self) -> None:
        assert title_of("<title></title>") is None

    def test_no_title_element_is_none(self) -> None:
        assert title_of("<html><body><p>text</p></body></html>") is None

    def test_the_first_nonblank_title_wins(self) -> None:
        html = "<title>First</title><title>Second</title>"

        assert title_of(html) == "First"

    def test_a_blank_first_title_does_not_shadow_a_real_one(self) -> None:
        html = "<title>  </title><title>Real Title</title>"

        assert title_of(html) == "Real Title"

    def test_a_title_in_the_body_is_still_found(self) -> None:
        """Malformed, but unambiguous: the element is what is looked for."""
        assert title_of("<body><title>Moved</title><p>text</p></body>") == "Moved"

    def test_an_unclosed_title_is_still_read(self) -> None:
        assert title_of("<head><title>Unclosed") == "Unclosed"

    def test_a_title_never_becomes_body_text(self) -> None:
        assert text_of("<body><title>Moved</title><p>text</p></body>") == "text"

    def test_an_h1_is_never_the_title(self) -> None:
        html = "<html><head></head><body><h1>A Heading</h1><p>body</p></body></html>"

        assert title_of(html) is None
        assert text_of(html) == "A Heading\n\nbody"

    def test_the_first_line_of_text_is_never_the_title(self) -> None:
        assert title_of("<p>The first line of the page.</p>") is None

    def test_a_url_in_the_markup_is_never_the_title(self) -> None:
        html = '<head><link rel="canonical" href="https://example.com/a-page"></head><body>x</body>'

        assert title_of(html) is None

    def test_a_meta_title_is_not_a_title(self) -> None:
        html = '<head><meta name="title" content="Meta Title"></head><body>x</body>'

        assert title_of(html) is None

    def test_unicode_in_a_title_is_not_normalized(self) -> None:
        extracted = title_of("<title>Å</title>")

        assert extracted == "Å"
        assert extracted != "Å"

    def test_nbsp_in_a_title_is_preserved(self) -> None:
        assert title_of("<title>A&nbsp;Title</title>") == "A\u00a0Title"


class TestDeterminism:
    """The same input yields the same output. Every time, in either order."""

    DOCUMENT = (
        "<!doctype html><html><head><title>Doc</title><style>x{}</style></head>"
        "<body><nav>Menu</nav><main><h1>Head</h1><p>One &amp; two.</p>"
        "<script>var a=1</script><ul><li>i</li><li>ii</li></ul></main></body></html>"
    )

    def test_repeated_extraction_agrees(self) -> None:
        results = [extract(self.DOCUMENT) for _ in range(5)]

        assert len(set(results)) == 1

    def test_a_fresh_extractor_gives_the_same_answer(self) -> None:
        first = HtmlTextExtractor()
        first.feed(self.DOCUMENT)
        first.close()
        second = HtmlTextExtractor()
        second.feed(self.DOCUMENT)
        second.close()

        assert (first.text, first.title) == (second.text, second.title)

    def test_the_whole_document_extracts_exactly(self) -> None:
        assert extract(self.DOCUMENT) == ("Menu\n\nHead\n\nOne & two.\n\ni\n\nii", "Doc")

    def test_feeding_in_chunks_agrees_with_feeding_at_once(self) -> None:
        """``HTMLParser`` buffers across ``feed`` calls; nothing here defeats that."""
        chunked = HtmlTextExtractor()
        for index in range(0, len(self.DOCUMENT), 7):
            chunked.feed(self.DOCUMENT[index : index + 7])
        chunked.close()

        assert (chunked.text, chunked.title) == extract(self.DOCUMENT)


class TestExtractionIsPure:
    def test_the_input_string_is_returned_unchanged_by_nothing(self) -> None:
        """Extraction derives; it never claims to round-trip the markup."""
        html = "<p>Hello &amp; goodbye.</p>"

        assert text_of(html) == "Hello & goodbye."
        assert text_of(html) != html

    def test_no_markup_survives_into_the_text(self) -> None:
        html = "<div class='a'><p id='b'>content</p></div>"
        extracted = text_of(html)

        assert extracted == "content"
        for fragment in ("<div", "<p", "class", "id="):
            assert fragment not in extracted
