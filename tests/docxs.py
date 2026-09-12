"""Deterministic DOCX fixtures, generated with ``python-docx``.

The PDF fixtures next door are written out byte by byte, and this module cannot
be. A DOCX is a ZIP of a dozen XML parts with a relationship graph between them,
and hand-writing a conforming one would be a Word-package implementation living
in a test directory — far more code than the thing it is testing, and wrong in
ways nobody would notice. So these are built with ``python-docx``, and the
tradeoff is stated rather than hidden: the writer and the reader come from the
same library, so a fixture proves what UniMem does with a *conforming* document
rather than how the reader behaves against Word's own output.

Two consequences follow, and both are worked around here rather than papered
over:

* **The bytes are not reproducible across runs.** A ZIP entry carries a
  modification time, and the writer stamps it from the clock, so the same
  document written twice a second apart is two different byte strings with two
  different SHA-256s. Every builder is therefore cached: within one test run a
  given fixture *is* one byte string, so "upload the same bytes twice" and "the
  stored original equals what was uploaded" are testable as written. Across runs
  the bytes differ, which is why no test in this repository asserts a literal
  DOCX digest.
* **The default template is not empty.** ``Document()`` starts from Word's
  own default template, which carries styles, a theme, a thumbnail, and empty
  core properties — but no body paragraphs, which is exactly what the textless
  fixtures need.

The corrupt and encrypted fixtures are the exception: those are written out
directly, because what they are testing is that the reader *refuses* them, and a
library that could produce them would be a library that could read them.
"""

import io
import zipfile
from functools import cache
from typing import Final

from docx import Document
from docx.document import Document as DocxDocument

#: A paragraph, a table row, and a second paragraph — the ordering fixture.
#: These strings are deliberately unlike each other so an assertion about order
#: reads as an assertion about order.
PARAGRAPH_A: Final = "The canonical object is not Markdown."
PARAGRAPH_B: Final = "It is a ContentObject."
PARAGRAPH_C: Final = "And a third paragraph, after the table."

#: A 2x2 table's cells, in row-major order.
TABLE_CELLS: Final = [["Format", "Pagination"], ["docx", "renderer-dependent"]]

#: What the two rows of :data:`TABLE_CELLS` flatten to.
TABLE_ROW_ONE: Final = "Format\tPagination"
TABLE_ROW_TWO: Final = "docx\trenderer-dependent"

#: Paragraph text with padding that must survive intact. A normalizer would
#: trim it; this build uses ``strip`` to decide blankness and never to rewrite.
PADDED_PARAGRAPH: Final = "   indented, and it stays indented   "

#: Cell text with padding, for the same reason, on the table path.
PADDED_CELLS: Final = [["  left  ", "\tright\t"]]
PADDED_ROW: Final = "  left  \t\tright\t"

#: A document title that is not a filename, not a heading, and not the first
#: paragraph — so a test can tell which source a title came from.
CORE_TITLE: Final = "A DOCX that names itself"

#: A core-properties title made only of whitespace. It exists, it is a string,
#: and it says nothing, which is exactly the case "nonblank" has to rule out.
BLANK_CORE_TITLE: Final = "   \t "

#: Text no well-meaning normalizer may tidy: a combining ring next to the
#: precomposed character it looks like, an em dash, CJK, and an astral-plane
#: emoji. XML carries all of these directly, so unlike the PDF fixture there is
#: no font encoding to work around. Written with escapes rather than as literal
#: characters, because the whole point is that two strings which *look* the same
#: are not, and a source file is exactly where that distinction gets lost.
AWKWARD_TEXT: Final = "\u00c5 vs A\u030a \u2014 \u4f60\u597d \U0001f30d"

#: Text placed in a header and a footer, which this build does not read.
HEADER_TEXT: Final = "A running header nobody captured"
FOOTER_TEXT: Final = "A running footer nobody captured"

#: A paragraph styled as a Word heading. This build stores it as text, and the
#: fixture exists so that staying text is something a test can insist on.
HEADING_TEXT: Final = "Chapter One"

#: The smallest PNG that parses: one opaque pixel, written out so that an
#: image-only document needs no image file and no image library.
ONE_PIXEL_PNG: Final = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc0000003010100b5cd4c1a0000000049454e"
    "44ae426082"
)


def _save(document: DocxDocument) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@cache
def paragraphs_docx(*paragraphs: str, title: str | None = None) -> bytes:
    """A document whose body is exactly these paragraphs, in this order.

    An empty string is a genuinely empty paragraph — the thing a person leaves
    behind by pressing Enter twice — and is how the blank-paragraph cases are
    built.
    """
    document = Document()
    if title is not None:
        document.core_properties.title = title
    for text in paragraphs:
        document.add_paragraph(text)
    return _save(document)


@cache
def paragraph_table_paragraph_docx(*, title: str | None = None) -> bytes:
    """Paragraph, 2x2 table, paragraph — the body-order fixture.

    This is the one that separates "extracted the tables" from "extracted the
    tables *where they were*": a processor that appended table rows after the
    paragraphs would pass every other assertion in the suite and fail this one.
    """
    document = Document()
    if title is not None:
        document.core_properties.title = title
    document.add_paragraph(PARAGRAPH_A)
    _add_table(document, TABLE_CELLS)
    document.add_paragraph(PARAGRAPH_B)
    return _save(document)


@cache
def two_tables_docx() -> bytes:
    """Two body tables with a paragraph between them.

    The second table's rows must report ``table_index`` 1 and their own
    ``row_index`` values, which is what makes the metadata a coordinate into the
    document rather than a running count of emitted segments.
    """
    document = Document()
    document.add_paragraph(PARAGRAPH_A)
    _add_table(document, TABLE_CELLS)
    document.add_paragraph(PARAGRAPH_B)
    _add_table(document, [["one", "two", "three"]])
    return _save(document)


@cache
def table_with_blank_rows_docx() -> bytes:
    """A table whose middle row is entirely blank.

    The blank row emits nothing and still counts, so the row after it reports
    ``row_index`` 2 — the same distinction the PDF fixtures draw between a
    physical page number and a canonical position.
    """
    document = Document()
    _add_table(document, [["first", "row"], ["", "   "], ["third", "row"]])
    return _save(document)


@cache
def padded_table_docx() -> bytes:
    """A one-row table whose cells carry padding that must survive the join."""
    document = Document()
    _add_table(document, PADDED_CELLS)
    return _save(document)


@cache
def merged_cell_docx() -> bytes:
    """A 2x2 table whose first row is one horizontally merged cell.

    A documented limitation rather than a target: ``row.cells`` reports a merged
    cell once per grid column it spans, so the merged text appears twice in the
    flattened row. The fixture exists so the limitation is pinned down by a test
    instead of discovered later.
    """
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "spanning"
    table.cell(1, 0).text = "left"
    table.cell(1, 1).text = "right"
    return _save(document)


@cache
def heading_docx() -> bytes:
    """A Word ``Heading 1`` followed by an ordinary paragraph."""
    document = Document()
    document.add_heading(HEADING_TEXT, level=1)
    document.add_paragraph(PARAGRAPH_A)
    return _save(document)


@cache
def header_footer_docx() -> bytes:
    """Body text, plus a header and a footer this build does not read."""
    document = Document()
    section = document.sections[0]
    section.header.paragraphs[0].text = HEADER_TEXT
    section.footer.paragraphs[0].text = FOOTER_TEXT
    document.add_paragraph(PARAGRAPH_A)
    return _save(document)


@cache
def header_only_docx() -> bytes:
    """A document whose only text is in its header. The body says nothing."""
    document = Document()
    document.sections[0].header.paragraphs[0].text = HEADER_TEXT
    return _save(document)


@cache
def textless_docx() -> bytes:
    """A structurally valid DOCX with no body content at all."""
    return _save(Document())


@cache
def blank_paragraphs_docx() -> bytes:
    """A body that is nothing but empty and whitespace-only paragraphs."""
    return paragraphs_docx("", "   ", "\t")


@cache
def image_only_docx() -> bytes:
    """A document whose body is one picture and no text.

    This is what a scanned page looks like to a text extractor, and the point of
    the fixture is that it *fails*: no OCR runs, no vision model is called, and
    no empty ``COMPLETE`` content object is produced.
    """
    document = Document()
    document.add_picture(io.BytesIO(ONE_PIXEL_PNG))
    return _save(document)


@cache
def awkward_text_docx() -> bytes:
    """A paragraph whose text is deliberately hostile to normalization."""
    return paragraphs_docx(AWKWARD_TEXT)


def _add_table(document: DocxDocument, cells: list[list[str]]) -> None:
    table = document.add_table(rows=len(cells), cols=len(cells[0]))
    for row_index, row in enumerate(cells):
        for column_index, text in enumerate(row):
            table.cell(row_index, column_index).text = text


# --- Documents the reader must refuse ----------------------------------------


def corrupt_docx() -> bytes:
    """Bytes that are not a ZIP archive, and so are not an OOXML package."""
    return b"PK\x03\x04 this announces itself as an archive and stops"


def not_a_word_package_docx() -> bytes:
    """A perfectly valid ZIP that is not an OPC package.

    The container layer is happy and the package layer finds nothing it needs —
    a different failure path from :func:`corrupt_docx`, and the same refusal.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "a zip, but not a document")
    return buffer.getvalue()


def truncated_body_docx() -> bytes:
    """A real DOCX package whose ``word/document.xml`` no longer parses.

    Container fine, package fine, XML broken — the third of the three layers a
    DOCX can fail at, and the one a ZIP-level check would sail straight past.
    """
    source = zipfile.ZipFile(io.BytesIO(paragraphs_docx(PARAGRAPH_A)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in source.namelist():
            blob = b"<w:document" if name == "word/document.xml" else source.read(name)
            archive.writestr(name, blob)
    return buffer.getvalue()


#: The eight-byte signature of a Microsoft compound file, which is the container
#: Office wraps a password-protected document in — see :func:`encrypted_docx`.
COMPOUND_FILE_SIGNATURE: Final = bytes.fromhex("d0cf11e0a1b11ae1")


def encrypted_docx() -> bytes:
    """The container an encrypted Word document arrives in, and not one more.

    **This is a deliberately limited fixture, and the limit is worth stating.**
    A genuine password-protected ``.docx`` is a compound file holding an
    ``EncryptionInfo`` stream and an AES-encrypted package, and producing one
    needs both a compound-file writer and an AES implementation — a second
    substantial dependency, added for a single test, to generate input this
    build's whole intent is to refuse. So what is generated here is the part
    that decides the outcome: an encrypted document is *not a ZIP*, and a reader
    of ZIP-based packages rejects it at the container layer before any
    decryption could be attempted. That refusal is what is under test.

    What is therefore **not** covered by any test in this repository: a real
    encrypted package, with real ``EncryptionInfo``, reaching the reader. The
    claim this build makes about those is narrow and follows from the format —
    they are compound files, they are not ZIPs, and nothing here tries a
    password — rather than from an executed example.
    """
    return COMPOUND_FILE_SIGNATURE + bytes(120)
