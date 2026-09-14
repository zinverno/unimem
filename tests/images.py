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
