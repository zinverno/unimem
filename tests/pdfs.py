"""Deterministic PDF fixtures, built byte by byte.

Every PDF the tests use is generated here, from PDF syntax written out in full,
and three properties follow from that which a checked-in binary could not offer:

* **They are deterministic.** The same call produces the same bytes on every
  machine and every run, so a test can assert on a SHA-256 and mean it. No
  producer string, timestamp, object id, or compression level varies.
* **They are readable.** What the text of page two is, and why page three is
  blank, is visible in the source rather than hidden inside an opaque file.
* **They are honest.** Nothing here is generated *by* the code under test, and
  ``pypdf`` is used to write only the one fixture that needs a feature this
  builder does not implement (encryption).

This is a fixture generator, not a PDF library. It writes uncompressed content
streams, one standard Type 1 font, and a classic cross-reference table — the
smallest thing a conforming reader accepts. It does not implement compression,
object streams, incremental updates, outlines, forms, annotations, or images,
and it should not grow them: a fixture that needs a feature this cannot express
is a sign the test wants a different fixture, not a bigger builder.

Text is written in the font's WinAnsi encoding, and a page may instead carry an
explicit ``/ToUnicode`` mapping so that characters outside that encoding — a
combining mark, an emoji — can be placed in a document and read back exactly.
That is the mechanism real PDFs use for the same purpose.
"""

import io
from typing import Final

from pypdf import PdfWriter

#: Every page is US Letter. Page geometry is irrelevant to text extraction here,
#: and one size keeps the fixtures comparable.
_MEDIA_BOX: Final = "[0 0 612 792]"

#: Where text starts on a page, and the leading between lines. Any values a
#: reader accepts would do; these are ordinary ones.
_TEXT_ORIGIN: Final = "72 720 Td"
_LEADING: Final = "14 TL"


def _escape(text: str) -> str:
    """Escape a string for a PDF literal string object."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str], codes: list[bytes] | None) -> bytes:
    """The page-drawing operators for one page, or nothing for a blank page.

    ``codes`` is the WinAnsi (or ``/ToUnicode``-mapped) byte string for each
    line, defaulting to the WinAnsi encoding of ``lines``.
    """
    if not lines:
        return b""
    encoded = codes if codes is not None else [line.encode("cp1252") for line in lines]
    parts = ["BT", "/F1 12 Tf", _TEXT_ORIGIN, _LEADING]
    body = b"\n".join(part.encode("ascii") for part in parts)
    for raw in encoded:
        body += b"\n(" + _escape(raw.decode("latin-1")).encode("latin-1") + b") Tj T*"
    return body + b"\nET"


def _to_unicode_cmap(mapping: dict[int, str]) -> str:
    """A minimal ``/ToUnicode`` CMap mapping single byte codes to UTF-16BE text.

    This is the standard way a PDF says "the glyph I drew for byte 0x41 means
    this Unicode string". It is what lets a fixture contain a combining mark or
    an astral-plane character and have a reader hand back exactly that.
    """
    entries = "\n".join(
        f"<{code:02X}> <{text.encode('utf-16-be').hex().upper()}>" for code, text in mapping.items()
    )
    return (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CMapName /UniMem-Fixture def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        f"{len(mapping)} beginbfchar\n{entries}\nendbfchar\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
    )


class _Body:
    """An accumulating PDF body that remembers where each object landed."""

    def __init__(self) -> None:
        self._out = bytearray(b"%PDF-1.4\n")
        self._offsets: dict[int, int] = {}

    def add(self, number: int, body: str) -> None:
        self._offsets[number] = len(self._out)
        self._out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")

    def add_stream(self, number: int, data: bytes, extra: str = "") -> None:
        self._offsets[number] = len(self._out)
        self._out += f"{number} 0 obj\n<< /Length {len(data)}{extra} >>\nstream\n".encode("latin-1")
        self._out += data + b"\nendstream\nendobj\n"

    def finish(self, root: int, info: int | None) -> bytes:
        """Append the cross-reference table and trailer, and return the file."""
        size = max(self._offsets) + 1
        start = len(self._out)
        self._out += f"xref\n0 {size}\n".encode("latin-1")
        self._out += b"0000000000 65535 f \n"
        for number in range(1, size):
            self._out += f"{self._offsets[number]:010d} 00000 n \n".encode("latin-1")
        trailer = f"<< /Size {size} /Root {root} 0 R"
        if info is not None:
            trailer += f" /Info {info} 0 R"
        self._out += f"trailer\n{trailer} >>\nstartxref\n{start}\n%%EOF\n".encode("latin-1")
        return bytes(self._out)


def build_pdf(
    pages: list[list[str]],
    *,
    title: str | None = None,
    to_unicode: dict[int, str] | None = None,
    page_codes: dict[int, list[bytes]] | None = None,
) -> bytes:
    """Build a PDF whose pages carry exactly the given lines.

    ``pages`` is one list of lines per physical page, in order; an empty list is
    a page that carries no text at all. ``title`` becomes the document
    information dictionary's ``/Title`` and is omitted entirely when ``None``.

    ``to_unicode`` and ``page_codes`` are the escape hatch for text WinAnsi
    cannot express: ``page_codes`` supplies the literal byte codes to draw on a
    given 1-based page, and ``to_unicode`` maps those codes to the Unicode text
    a reader should hand back.
    """
    body = _Body()
    count = len(pages)
    page_ids = [3 + 2 * index for index in range(count)]
    content_ids = [4 + 2 * index for index in range(count)]
    font_id = 3 + 2 * count
    cmap_id = font_id + 1
    info_id = cmap_id + 1

    body.add(1, "<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{identifier} 0 R" for identifier in page_ids)
    body.add(2, f"<< /Type /Pages /Kids [{kids}] /Count {count} >>")
    for index, lines in enumerate(pages):
        body.add(
            page_ids[index],
            f"<< /Type /Page /Parent 2 0 R /MediaBox {_MEDIA_BOX} "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_ids[index]} 0 R >>",
        )
        codes = None if page_codes is None else page_codes.get(index + 1)
        body.add_stream(content_ids[index], _content_stream(lines, codes))

    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding" + (
        f" /ToUnicode {cmap_id} 0 R >>" if to_unicode else " >>"
    )
    body.add(font_id, font)
    if to_unicode:
        body.add_stream(cmap_id, _to_unicode_cmap(to_unicode).encode("latin-1"))
    if title is not None:
        body.add(info_id if to_unicode else cmap_id, f"<< /Title ({_escape(title)}) >>")
    info = (info_id if to_unicode else cmap_id) if title is not None else None
    return body.finish(root=1, info=info)


def encrypt_pdf(data: bytes, password: str) -> bytes:
    """Re-emit a PDF with standard encryption applied.

    ``pypdf`` writes this one rather than the builder above, because encryption
    is the single fixture property that cannot be expressed by writing plain
    objects: it needs a key derivation and every string and stream in the file
    encrypted with it. Using the same library the processor reads with is fine
    here — the fixture is *input*, and what is under test is that the processor
    refuses it.

    ``password`` may be empty, which produces a document a reader opens without
    ever asking anyone for anything. That case matters more than the other: it
    is the one where "encrypted" could silently come to mean "readable".
    """
    writer = PdfWriter(clone_from=io.BytesIO(data))
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# --- The fixtures themselves -------------------------------------------------

#: Two text-bearing pages. The baseline document for everything about ordering,
#: page numbers, and segment shape.
TWO_PAGE_TEXT: Final = ["The canonical object is not Markdown.", "It is a ContentObject."]
TWO_PAGE_SECOND: Final = ["A second page, with its own text.", "And a second line on it."]

#: A blank physical page between two text-bearing ones. This is the fixture that
#: separates *canonical reading order* from *physical page number*: it must
#: produce two segments at positions 0 and 1, on pages 1 and 3.
BLANK_MIDDLE_PAGES: Final = [TWO_PAGE_TEXT, [], TWO_PAGE_SECOND]

#: What ``pypdf`` returns for a page built from ``TWO_PAGE_TEXT``. Written out
#: rather than computed, including the trailing newline the extractor emits,
#: because "the parser's string reaches the segment untouched" is only worth
#: asserting against a literal.
TWO_PAGE_TEXT_EXTRACTED: Final = "The canonical object is not Markdown.\nIt is a ContentObject.\n"
TWO_PAGE_SECOND_EXTRACTED: Final = "A second page, with its own text.\nAnd a second line on it.\n"

#: A document title that is not a filename, not a heading, and not the first
#: line of the text — so a test can tell which source a title came from.
METADATA_TITLE: Final = "A PDF that names itself"

#: A ``/Title`` made only of whitespace. It exists, it is a string, and it says
#: nothing, which is exactly the case "nonblank" has to rule out.
BLANK_METADATA_TITLE: Final = "   \t "

#: Text no well-meaning normalizer may tidy: a combining ring next to the
#: precomposed character it looks like, an em dash, CJK, and an astral-plane
#: emoji. Placed through a ``/ToUnicode`` map, which is how a real PDF carries
#: characters its font encoding cannot name.
AWKWARD_CODES: Final = {
    0x01: "Å",
    0x02: " vs ",
    0x03: "Å",
    0x04: " — ",
    0x05: "你好",
    0x06: " \U0001f30d",
}
AWKWARD_TEXT: Final = "Å vs Å — 你好 \U0001f30d"


def two_page_pdf(*, title: str | None = None) -> bytes:
    """The baseline two-page text-bearing document."""
    return build_pdf([TWO_PAGE_TEXT, TWO_PAGE_SECOND], title=title)


def blank_middle_pdf(*, title: str | None = None) -> bytes:
    """Text, blank, text — three physical pages, two of them with content."""
    return build_pdf(BLANK_MIDDLE_PAGES, title=title)


def one_page_pdf(*, title: str | None = None) -> bytes:
    """A single text-bearing page."""
    return build_pdf([TWO_PAGE_TEXT], title=title)


def textless_pdf() -> bytes:
    """A structurally valid PDF whose pages carry no text at all.

    This is what a scan looks like to a text extractor: the file is perfectly
    well-formed and the parser is perfectly happy, and there is simply nothing
    in it a text segment could be made from.
    """
    return build_pdf([[], []])


def awkward_text_pdf() -> bytes:
    """A page whose text is deliberately hostile to normalization."""
    return build_pdf(
        [["placeholder"]],
        page_codes={1: [bytes(AWKWARD_CODES)]},
        to_unicode=AWKWARD_CODES,
    )


def corrupt_pdf() -> bytes:
    """Bytes that announce themselves as a PDF and are not one."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog\nthis file stops mid-object"


def encrypted_pdf(password: str = "unimem") -> bytes:
    """A text-bearing document that a reader cannot open without a password."""
    return encrypt_pdf(one_page_pdf(), password)
