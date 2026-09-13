"""Image-only PDF fixtures, rendered locally and proven to carry no text.

Every fixture here is generated on the machine running the tests, from a system
font, with no network access and no committed binary. There is no font file in
this repository and none is downloaded.

**Why they are rendered rather than written.** A PDF fixture built the way
:mod:`tests.pdfs` builds one carries a *text* layer, which is exactly what a
recognition test must not have: rendering ordinary text into a PDF and then
reading it back with an OCR engine proves nothing if the parser could have read it
straight out of the file. So these fixtures go through a raster: the text is drawn
into a bitmap with Pillow, and the bitmap — and nothing else — is placed on the
PDF page. The result is what a scanner produces, and
:func:`tests.ocr_fixtures.embedded_text_of` is used by the tests to *prove* it,
through the same ``pypdf`` path the processor uses.

The phrases are chosen to be distinctive and unambiguous in print: ordinary words,
high contrast, generous size, at a resolution the engine is tuned for. They are
not chosen to be easy — the English page includes a pangram — but they are chosen
so that a correct result is recognizable and a wrong one is obvious.
"""

import io
from functools import cache
from pathlib import Path
from typing import BinaryIO, Final

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter

from core.processing.pdf import extract_pages
from tests import pdfs

#: Fonts that cover both Latin and Cyrillic, in preference order. All of them are
#: ordinary system packages; none is redistributed here.
FONT_CANDIDATES: Final = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

#: The page geometry every rendered fixture uses: US Letter at 300 DPI.
PAGE_DPI: Final = 300
PAGE_INCHES: Final = (8.5, 11.0)

#: Big enough that recognition is about the engine rather than about the render.
FONT_SIZE: Final = 64
LINE_SPACING: Final = 110
TEXT_ORIGIN: Final = (250, 300)

#: The English image-only page, and the words a correct reading must contain.
ENGLISH_LINES: Final = (
    "UniMem scanned acceptance page.",
    "The quick brown fox jumps over the lazy dog.",
)
ENGLISH_PHRASES: Final = (
    "UniMem",
    "scanned",
    "acceptance",
    "quick brown fox",
    "lazy dog",
)

#: The Russian image-only page, and the words a correct reading must contain.
RUSSIAN_LINES: Final = (
    "Отсканированная страница.",
    "Проверка распознавания текста.",
)
RUSSIAN_PHRASES: Final = (
    "Отсканированная",
    "страница",
    "Проверка",
    "распознавания",
    "текста",
)

#: The embedded-text page of the mixed document, and what ``pypdf`` reads from it.
MIXED_EMBEDDED_LINES: Final = ("This page carries its own embedded text.",)
MIXED_EMBEDDED_TEXT: Final = "This page carries its own embedded text.\n"

#: The scanned middle page of the mixed document.
MIXED_SCANNED_LINES: Final = ("The middle page is a scan.",)
MIXED_SCANNED_PHRASES: Final = ("middle page", "scan")


def font_path() -> str | None:
    """The first Cyrillic-capable system font that exists, or ``None``."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def require_font() -> str:
    """The font, or a refusal that obeys the required-mode policy."""
    from tests import ocr_support

    found = font_path()
    if found is None:
        ocr_support.unavailable(
            "no system font with Cyrillic coverage was found "
            f"(looked for {', '.join(FONT_CANDIDATES)})"
        )
    return found


def render_page(lines: tuple[str, ...]) -> Image.Image:
    """Draw ``lines`` as black text on an opaque white page-sized bitmap."""
    width = int(PAGE_INCHES[0] * PAGE_DPI)
    height = int(PAGE_INCHES[1] * PAGE_DPI)
    page = Image.new("RGB", (width, height), "white")
    pen = ImageDraw.Draw(page)
    face = ImageFont.truetype(require_font(), FONT_SIZE)
    x, y = TEXT_ORIGIN
    for line in lines:
        pen.text((x, y), line, fill="black", font=face)
        y += LINE_SPACING
    return page


def image_only_pdf(*pages: tuple[str, ...]) -> bytes:
    """A PDF whose every page is a single raster image and nothing else.

    Pillow's PDF writer places the bitmap and no text objects, no fonts, and no
    ``/ToUnicode`` maps, which is precisely why it is used here: there is nothing
    in the file for a text extractor to find.
    """
    rendered = [render_page(lines) for lines in pages]
    buffer = io.BytesIO()
    rendered[0].save(
        buffer,
        format="PDF",
        resolution=float(PAGE_DPI),
        save_all=True,
        append_images=rendered[1:],
    )
    return buffer.getvalue()


def merge(*documents: bytes, title: str | None = None) -> bytes:
    """Concatenate whole PDFs, preserving each page exactly as it was written.

    ``pypdf`` does the merge because the fixtures being merged come from two
    different producers — a rendered raster page from Pillow and a text page from
    :mod:`tests.pdfs` — and a mixed document is the only fixture that needs both.
    """
    writer = PdfWriter()
    for document in documents:
        writer.append(io.BytesIO(document))
    if title is not None:
        writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def embedded_text_of(document: bytes) -> list[tuple[int, str]]:
    """What the *existing* extraction path reads from these bytes.

    Deliberately the production helper, not a private reimplementation: "this
    fixture has no text layer" is only a meaningful claim if it is made by the code
    whose job is to find one.
    """
    stream: BinaryIO = io.BytesIO(document)
    pages, _ = extract_pages(stream)
    return pages


@cache
def english_scan() -> bytes:
    """One image-only page of English text."""
    return image_only_pdf(ENGLISH_LINES)


@cache
def russian_scan() -> bytes:
    """One image-only page of Russian text."""
    return image_only_pdf(RUSSIAN_LINES)


@cache
def two_page_scan() -> bytes:
    """Two image-only pages: English, then Russian."""
    return image_only_pdf(ENGLISH_LINES, RUSSIAN_LINES)


@cache
def mixed_document() -> bytes:
    """Embedded text, then a scan, then a genuinely empty page.

    The fixture the mixed-page policy is about: page one must reach a segment
    through the ordinary parser and never be rasterized, page two must be
    recognized, and page three must produce nothing without being called blank.
    """
    return merge(
        pdfs.build_pdf([list(MIXED_EMBEDDED_LINES)]),
        image_only_pdf(MIXED_SCANNED_LINES),
        pdfs.build_pdf([[]]),
        title=pdfs.METADATA_TITLE,
    )


@cache
def blank_scan() -> bytes:
    """Two image-only pages with nothing drawn on them: white paper."""
    return image_only_pdf((), ())
