"""Deterministic PNG and JPEG fixtures, built byte by byte.

Every image the tests use is generated here, from the format's own structure
written out in full, and three properties follow from that which a checked-in
binary could not offer:

* **They are deterministic.** The same call produces the same bytes on every
  machine and every run, so a test can assert on a SHA-256 and mean it. No
  encoder version, timestamp, or compression choice varies.
* **They are readable.** Why a fixture has a bad CRC, or an illegal colour type,
  or a frame header that never arrives, is visible in the source rather than
  hidden inside an opaque file.
* **They are honest.** Nothing here is generated *by* the code under test. No
  imaging library is used — there is none installed in the default build, which
  is the point — and the bytes are assembled with :mod:`struct` and
  :func:`zlib.crc32`, neither of which knows what an image is.

This is a fixture generator, not an image library, and it deliberately does not
encode pixels. The processor under test reads headers and stops, so what a valid
fixture needs is a *well-formed header* followed by whatever a real file would
carry — and for the PNG fixtures that trailing data is a token ``IDAT``/``IEND``
tail whose contents are never read. A fixture that needs real pixel data is a
sign the test wants a different processor, not a bigger builder.
"""

import struct
import zlib
from dataclasses import dataclass
from typing import Final

#: The eight bytes every PNG begins with.
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"

#: A colour type and bit depth the specification permits, used wherever a
#: fixture needs to be valid and the particular combination is beside the point.
DEFAULT_COLOUR_TYPE: Final = 6
DEFAULT_BIT_DEPTH: Final = 8

#: Dimensions distinct from each other, so a test that transposes width and
#: height fails instead of passing by symmetry.
DEFAULT_WIDTH: Final = 7
DEFAULT_HEIGHT: Final = 3

#: A capture title that is not a filename and could not have been read out of an
#: image, so a test can tell which source a title came from.
SUBMITTED_TITLE: Final = "A photograph of the harbour at dusk"


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """One PNG chunk: length, type, data, and the CRC over type plus data."""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data))
    )


def ihdr_data(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    colour_type: int = DEFAULT_COLOUR_TYPE,
    compression: int = 0,
    filter_method: int = 0,
    interlace: int = 0,
) -> bytes:
    """The 13 bytes of an ``IHDR`` chunk's data field."""
    return struct.pack(
        ">IIBBBBB", width, height, bit_depth, colour_type, compression, filter_method, interlace
    )


def png(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    colour_type: int = DEFAULT_COLOUR_TYPE,
    compression: int = 0,
    filter_method: int = 0,
    interlace: int = 0,
    signature: bytes = PNG_SIGNATURE,
    chunk_type: bytes = b"IHDR",
    declared_length: int | None = None,
    crc: int | None = None,
    tail: bytes | None = None,
) -> bytes:
    """A PNG whose header says exactly what the keywords ask for.

    Every structural field is a parameter precisely because the tests need to
    make each one wrong on purpose. ``declared_length`` and ``crc`` default to
    the correct values and can be overridden to lie; ``tail`` defaults to a
    token ``IDAT``/``IEND`` pair so the fixture looks like a whole file, and the
    processor must not need it.
    """
    data = ihdr_data(
        width=width,
        height=height,
        bit_depth=bit_depth,
        colour_type=colour_type,
        compression=compression,
        filter_method=filter_method,
        interlace=interlace,
    )
    length = len(data) if declared_length is None else declared_length
    checksum = zlib.crc32(chunk_type + data) if crc is None else crc
    header = signature + struct.pack(">I", length) + chunk_type + data + struct.pack(">I", checksum)
    return header + (_default_tail() if tail is None else tail)


def _default_tail() -> bytes:
    """What follows the header in these fixtures, and is never read.

    A syntactically well-formed ``IDAT``/``IEND`` pair, so that a fixture is a
    plausible whole file rather than a bare header — which is what makes "the
    parser stops after 33 bytes" a claim a test can actually check.
    """
    return _chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + _chunk(b"IEND", b"")


#: The two bytes every JPEG begins with.
JPEG_SOI: Final = b"\xff\xd8"

#: A minimal JFIF ``APP0`` segment, the thing a real JPEG carries before its
#: frame header and the thing the marker walk has to skip to get there.
JFIF_APP0: Final = (
    b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
)

#: Token entropy-coded scan data and an end-of-image marker. The walk must stop
#: before this, so its contents are arbitrary and must stay unread.
JPEG_SCAN_TAIL: Final = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x9a\xbc\xde\xff\xd9"


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    """One ordinary marker segment: ``FF``, the marker, a length, the payload."""
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def sof_segment(
    *,
    marker: int = 0xC0,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    components: int = 3,
    precision: int = 8,
) -> bytes:
    """A frame header declaring the given dimensions.

    The payload is the structural prefix the parser reads — precision, height,
    width, component count — followed by the per-component bytes a real frame
    header carries and this build never looks at.
    """
    payload = struct.pack(">BHHB", precision, height, width, components)
    payload += bytes([1, 0x11, 0]) * components
    return jpeg_segment(marker, payload)


def jpeg(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    marker: int = 0xC0,
    leading: bytes = JFIF_APP0,
    soi: bytes = JPEG_SOI,
    tail: bytes = JPEG_SCAN_TAIL,
) -> bytes:
    """A JPEG whose first frame header says exactly what the keywords ask for."""
    return soi + leading + sof_segment(marker=marker, width=width, height=height) + tail


#: A start-of-scan segment: where entropy-coded data begins, and where the walk
#: must stop whether or not it has found what it was looking for.
SOS_SEGMENT: Final = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"


def jpeg_without_frame_header(*, terminator: bytes = SOS_SEGMENT) -> bytes:
    """A structurally fine JPEG whose scan begins before any frame header."""
    return JPEG_SOI + JFIF_APP0 + terminator + b"\xff\xd9"


def jpeg_with_many_markers(count: int) -> bytes:
    """A JPEG that hides its frame header behind ``count`` skippable segments.

    Each filler is a tiny ``APP1`` carrying nothing worth reading — which is also
    what makes it a fair test of the segment bound rather than of the byte bound.
    """
    filler = jpeg_segment(0xE1, b"\x00\x00") * count
    return JPEG_SOI + filler + sof_segment() + JPEG_SCAN_TAIL


#: The largest payload one marker segment can carry. The length field is 16 bits
#: and counts itself, so no single segment reaches even 64 KiB — which is why
#: pushing a frame header past a megabyte takes seventeen of them and not one.
MAX_SEGMENT_PAYLOAD: Final = 0xFFFF - 2


def jpeg_with_bulky_segments(at_least: int) -> bytes:
    """A JPEG whose frame header sits behind ``at_least`` bytes of skippable segments.

    The filler is a run of maximum-size ``APP1`` segments, because that is the
    only way a real JPEG can push its frame header a long way into the file: a
    segment's length field is two bytes, so one segment cannot do it. Seventeen
    are enough to clear a mebibyte, which is comfortably inside the segment
    bound — so this exercises the byte budget and not the segment count.
    """
    per_segment = len(jpeg_segment(0xE1, b"\x00" * MAX_SEGMENT_PAYLOAD))
    count = at_least // per_segment + 1
    filler = jpeg_segment(0xE1, b"\x00" * MAX_SEGMENT_PAYLOAD) * count
    return JPEG_SOI + filler + sof_segment() + JPEG_SCAN_TAIL


def jpeg_with_truncated_frame_header(*, declared_payload: int = 64) -> bytes:
    """A JPEG whose frame header declares more bytes than the file contains.

    The six structural bytes — precision, height, width, component count — are
    all present and perfectly readable, and the dimensions they carry are valid.
    What is missing is the rest of the segment the header declared. A parser that
    returns as soon as it can read those six bytes accepts this file; one that
    requires the declared segment to be present refuses it as truncated, which is
    what it is.
    """
    structure = struct.pack(">BHHB", 8, DEFAULT_HEIGHT, DEFAULT_WIDTH, 3)
    segment = bytes([0xFF, 0xC0]) + struct.pack(">H", declared_payload + 2) + structure
    return JPEG_SOI + JFIF_APP0 + segment + b"\x00\x00\x00\x00"


def jpeg_with_labelled_regions(*, marker_payload: int = 512) -> tuple[bytes, range, range, range]:
    """A JPEG plus the byte ranges of its ``APPn`` payload, its frame header and its scan.

    Returned together because the interesting assertions about this parser are
    about *which bytes it touched*, and a test cannot check that against ranges
    it had to recompute by hand.
    """
    app = jpeg_segment(0xE1, b"Exif\x00\x00" + b"\xa5" * (marker_payload - 6))
    sof = sof_segment()

    app_payload = range(len(JPEG_SOI) + 4, len(JPEG_SOI) + len(app))
    frame_header = range(len(JPEG_SOI) + len(app), len(JPEG_SOI) + len(app) + len(sof))
    scan = range(frame_header.stop, frame_header.stop + len(JPEG_SCAN_TAIL))

    return JPEG_SOI + app + sof + JPEG_SCAN_TAIL, app_payload, frame_header, scan


def png_with_animation_control(*, frames: int = 3) -> bytes:
    """A PNG whose IHDR is followed by an ``acTL`` chunk — an APNG, in other words.

    It exists to pin down what Phase 4A does *not* claim. The parser reads the
    33-byte prefix and stops, so this datastream is structurally accepted exactly
    like any other PNG: the build records that it found a valid ``IHDR``, and
    says nothing at all about whether the frames behind it are one or many.
    """
    actl = _chunk(b"acTL", struct.pack(">II", frames, 0))
    return png(tail=actl + _default_tail())


# --- A real, readable image, built without an imaging library -----------------
#
# Everything above is a header fixture: a well-formed prefix followed by bytes
# nobody reads, which is all a header-only parser needs. The native OCR test
# needs the opposite — an image whose *pixels* a real engine can look at and read
# words out of.
#
# It is still built here, byte by byte, and for a sharper reason than before.
# Direct-image OCR must work on a machine where the optional `[ocr]` extra is not
# installed, so a fixture generated with Pillow at run time would be a circular
# proof: the test would need the very package the test exists to show is
# unnecessary. `zlib` supplies both the `IDAT` compression and the chunk CRCs and
# knows nothing about images, so what follows depends on nothing but the standard
# library. Nothing is downloaded, and no font file is committed.
#
# **The glyphs are drawn as strokes rather than scaled-up bitmaps**, which is not
# a flourish. An upscaled 5x7 grid produces letters whose strokes are as wide as
# a sixth of the glyph and whose corners are perfectly square — unlike any
# typeface an LSTM recognizer was trained on — and in practice Tesseract read
# such a fixture as Cyrillic, because blocky Latin capitals and their Cyrillic
# lookalikes become indistinguishable. Strokes of a proportional width read
# reliably instead.

#: The word the native fixture draws, and the token the test looks for.
#:
#: Chosen for two properties rather than for flavour. Every letter is one with no
#: Cyrillic lookalike — the recognition policy is `eng+rus`, and a word built from
#: A, E, O, P, C, H, T, M, K, X invites the engine to answer in the wrong script
#: with no way to call it wrong. And it is long enough that a partial misread
#: fails the test rather than passing on a coincidence.
NATIVE_TOKEN: Final = "WINDFALL"

#: Cap height in pixels for the native fixture.
#:
#: Large, because a recognizer reading small synthetic text is a test of luck. The
#: token recognizes exactly at every cap height from 70 to 200 and every stroke
#: ratio from 0.12 to 0.18 against Tesseract 5.3.4 at the production policy, so
#: this value sits in the middle of a range that works rather than on the edge of
#: one that happens to.
_CAP_HEIGHT: Final = 110

#: Stroke width as a fraction of cap height. Real typefaces sit near here.
_STROKE_RATIO: Final = 0.15

#: Glyph advance and inter-glyph gap, as fractions of cap height.
_GLYPH_ADVANCE: Final = 0.78
_GLYPH_GAP: Final = 0.22

#: Blank margin around the text, as a fraction of cap height. Layout analysis
#: wants margins, and text touching the edge is the most common reason a
#: synthetic fixture recognizes to nothing.
_MARGIN_RATIO: Final = 0.55


#: One horizontal bar of a glyph, in unit-box coordinates.
@dataclass(frozen=True, slots=True)
class _Horizontal:
    y: float
    start: float
    stop: float


#: One vertical bar of a glyph, in unit-box coordinates.
@dataclass(frozen=True, slots=True)
class _Vertical:
    x: float
    start: float
    stop: float


#: One diagonal of a glyph, in unit-box coordinates.
@dataclass(frozen=True, slots=True)
class _Diagonal:
    x0: float
    y0: float
    x1: float
    y1: float


_Stroke = _Horizontal | _Vertical | _Diagonal

#: Each glyph as strokes inside a unit box. Only the letters
#: :data:`NATIVE_TOKEN` needs, plus a space, because this is a fixture generator
#: and not a font.
_STROKES: Final[dict[str, tuple[_Stroke, ...]]] = {
    "W": (
        _Diagonal(0.0, 0.0, 0.22, 1.0),
        _Diagonal(0.22, 1.0, 0.5, 0.25),
        _Diagonal(0.5, 0.25, 0.78, 1.0),
        _Diagonal(0.78, 1.0, 1.0, 0.0),
    ),
    "I": (_Vertical(0.5, 0.0, 1.0), _Horizontal(0.0, 0.18, 0.82), _Horizontal(1.0, 0.18, 0.82)),
    "N": (_Vertical(0.0, 0.0, 1.0), _Vertical(1.0, 0.0, 1.0), _Diagonal(0.0, 0.0, 1.0, 1.0)),
    "D": (
        _Vertical(0.0, 0.0, 1.0),
        _Horizontal(0.0, 0.0, 0.75),
        _Horizontal(1.0, 0.0, 0.75),
        _Vertical(0.9, 0.1, 0.9),
    ),
    "F": (_Vertical(0.0, 0.0, 1.0), _Horizontal(0.0, 0.0, 0.85), _Horizontal(0.45, 0.0, 0.65)),
    "A": (
        _Diagonal(0.5, 0.0, 0.0, 1.0),
        _Diagonal(0.5, 0.0, 1.0, 1.0),
        _Horizontal(0.68, 0.2, 0.8),
    ),
    "L": (_Vertical(0.0, 0.0, 1.0), _Horizontal(1.0, 0.0, 0.85)),
    " ": (),
}


def render_text_png(text: str = NATIVE_TOKEN) -> bytes:
    """A real greyscale PNG with ``text`` drawn on it in black on white.

    Colour type 0, bit depth 8: one byte per pixel, so the scanline layout is
    something this function writes directly rather than packs. Each row carries
    the PNG "None" filter byte, and the whole image is deflated in one go.

    The result is a genuine PNG that any decoder reads, produced without a decoder
    or an encoder being installed anywhere.
    """
    glyphs = [character.upper() for character in text]
    stroke = max(3, round(_CAP_HEIGHT * _STROKE_RATIO))
    advance = round(_CAP_HEIGHT * _GLYPH_ADVANCE)
    gap = round(_CAP_HEIGHT * _GLYPH_GAP)
    margin = round(_CAP_HEIGHT * _MARGIN_RATIO)

    width = margin * 2 + len(glyphs) * advance + max(len(glyphs) - 1, 0) * gap
    height = margin * 2 + _CAP_HEIGHT
    canvas = _Canvas(width, height, stroke)

    left = margin
    for character in glyphs:
        for shape in _STROKES[character]:
            _draw(canvas, shape, left=left, top=margin, advance=advance, stroke=stroke)
        left += advance + gap

    return _greyscale_png(canvas.pixels, width, height)


class _Canvas:
    """A one-byte-per-pixel white field that strokes are painted black onto."""

    def __init__(self, width: int, height: int, stroke: int) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(b"\xff" * (width * height))
        self._radius = stroke // 2

    def blob(self, x: int, y: int) -> None:
        """Paint one nib of the pen, clipped to the canvas."""
        for dy in range(-self._radius, self._radius + 1):
            row = y + dy
            if not 0 <= row < self.height:
                continue
            start = max(x - self._radius, 0)
            stop = min(x + self._radius + 1, self.width)
            if start < stop:
                offset = row * self.width
                self.pixels[offset + start : offset + stop] = b"\x00" * (stop - start)


def _draw(
    canvas: _Canvas, shape: _Stroke, *, left: int, top: int, advance: int, stroke: int
) -> None:
    """Paint one stroke of one glyph onto the canvas."""
    inset = stroke // 2
    if isinstance(shape, _Horizontal):
        y = top + round(shape.y * (_CAP_HEIGHT - stroke)) + inset
        for x in range(left + round(shape.start * advance), left + round(shape.stop * advance) + 1):
            canvas.blob(x, y)
    elif isinstance(shape, _Vertical):
        x = left + round(shape.x * (advance - stroke)) + inset
        for y in range(
            top + round(shape.start * _CAP_HEIGHT), top + round(shape.stop * _CAP_HEIGHT) + 1
        ):
            canvas.blob(x, y)
    else:
        steps = _CAP_HEIGHT * 2
        for step in range(steps + 1):
            position = step / steps
            canvas.blob(
                left
                + round((shape.x0 + (shape.x1 - shape.x0) * position) * (advance - stroke))
                + inset,
                top
                + round((shape.y0 + (shape.y1 - shape.y0) * position) * (_CAP_HEIGHT - stroke))
                + inset,
            )


def _greyscale_png(pixels: bytearray, width: int, height: int) -> bytes:
    """Wrap raw 8-bit greyscale pixels as a complete PNG datastream."""
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # the "None" filter, per the PNG specification
        scanlines += pixels[y * width : (y + 1) * width]
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr_data(width=width, height=height, bit_depth=8, colour_type=0))
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + _chunk(b"IEND", b"")
    )
