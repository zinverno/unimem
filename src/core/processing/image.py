"""Normalization of staged still-image captures.

The sixth processor, and the first whose canonical result carries no segments at
all. Text, HTML, PDF and DOCX are all *carriers* for words: the bytes are a
container, the words are what was meant, and a normalization that found no words
found nothing. A still image is not like that. The pixels are the artifact, and
an image that has been staged immutably, content-addressed, described
structurally, and tied to its capture has been remembered completely. What is
missing from such a result is an *interpretation* of it, which is optional
enrichment this build does not perform and never claimed to. See `ADR-019
<../../docs/ADR/ADR-019-still-image-ingestion.md>`_.

**So a valid image normalizes to a ``COMPLETE`` content object with zero
segments, and that is the honest answer rather than a shortcut.** Nothing is
invented to fill the gap: no text, no caption, no tags, no empty ``VISUAL``
segment, and no ``OCR`` or ``VISION`` provenance for a recognizer that did not
run. The refusal
:class:`~core.processing.pdf.PdfProcessor` makes for a textless PDF is correct
for documents and is untouched by this module; it simply does not extend to
material whose content is what it looks like.

**What it reads is the header, and only the header.** A PNG's mandatory ``IHDR``
chunk is read whole — signature, length, type, data and CRC, 33 bytes, with the
CRC verified and the structural fields checked against the combinations the
specification permits. A JPEG's dimensions are taken from the first supported
``SOF`` marker, reached by a marker walk with explicit finite bounds that stops
before the entropy-coded scan data begins. Those two numbers, plus which format
the header turned out to be, are the whole of what this processor observes.

**Nothing is decoded, and nothing is decompressed.** No ``IDAT``, no ancillary
chunk, no zlib inflate, no Huffman or arithmetic decoding, no inverse DCT, no
colour conversion, and no pixel buffer of any size is ever allocated. The
CRC-32 this module computes is a checksum over 33 bytes it is already holding —
a hash function that happens to live in the ``zlib`` module, not an invocation
of inflate. `core` therefore acquires no imaging library, no rasterizer, and no
new runtime dependency: the parsers below are ordinary bounded reads over
``bytes``.

**EXIF, XMP and ICC are not parsed.** Not for orientation, not for a timestamp,
not for anything. They stay in the immutable original, which remains exactly as
submitted and remains retrievable through the content object's asset, so a later
phase can decide about them deliberately rather than by omission. One
consequence is worth stating plainly: the dimensions recorded here are the
*encoded* ones, and an image whose metadata asks for a rotation has display
dimensions this build does not compute and does not claim.

**Nothing is executed, fetched, or opened.** The only input is the byte stream
the raw object store hands over. There is no network, no subprocess, and no
filesystem access anywhere in this module.
"""

import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO, Final

from core.contracts import (
    Asset,
    AssetRole,
    CaptureRecord,
    ContentObject,
    ContentSource,
    ContentType,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    RawObjectRef,
)
from core.contracts.base import JsonMapping
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectStore

#: The two still-image formats this processor reads, matched exactly against the
#: MIME type the submitter declared and intake recorded on the raw reference.
#:
#: Two, and the reason is the dependency boundary rather than taste. ``core`` may
#: not import an imaging library, so the structural reader is the hand-written
#: header parser below, and these are the formats such a parser reads correctly
#: and cheaply. WebP encodes its dimensions three different ways, GIF is an
#: animation container, TIFF needs IFD traversal, HEIC and AVIF need an ISO-BMFF
#: parser, and SVG is an active XML document rather than a raster still. Those
#: are deferred decisions, not permanent verdicts.
PNG_MIME_TYPE: Final = "image/png"
JPEG_MIME_TYPE: Final = "image/jpeg"
IMAGE_MIME_TYPES: Final[tuple[str, ...]] = (PNG_MIME_TYPE, JPEG_MIME_TYPE)

#: The supported formats as they appear in a refusal message.
_IMAGE_MIME_TYPES_PHRASE: Final = " and ".join(IMAGE_MIME_TYPES)

#: The format token recorded in metadata. Deliberately not the MIME type: the
#: declared type already travels on the asset, and this names what the *header*
#: was observed to be.
PNG_FORMAT: Final = "png"
JPEG_FORMAT: Final = "jpeg"

#: The metadata key, on the content object, under which this processor records
#: what it observed. One namespaced key rather than a scattering of top-level
#: ones, for the reason :mod:`core.processing.pdf_ocr` already gives:
#: ``ContentObject.metadata`` is shared free-form space.
IMAGE_METADATA_KEY: Final = "image"

#: Which format the header turned out to be.
ENCODED_FORMAT_KEY: Final = "encoded_format"

#: The pixel dimensions the header declares.
#:
#: Named ``encoded_`` rather than ``width``/``height`` because that is what was
#: measured. This build reads no orientation tag and performs no rotation, so
#: for an image that carries one these are not the dimensions a viewer would
#: see. Naming the key after the observation rather than after the conclusion is
#: the same discipline :mod:`core.processing.pdf_ocr` applies when it records
#: ``ocr_pages_without_text`` rather than "blank pages".
ENCODED_WIDTH_KEY: Final = "encoded_width"
ENCODED_HEIGHT_KEY: Final = "encoded_height"

#: The eight bytes every PNG begins with.
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"

#: The type of the chunk the specification requires to come first.
_IHDR_TYPE: Final = b"IHDR"

#: The size of ``IHDR``'s data field: width, height, bit depth, colour type,
#: compression method, filter method, interlace method.
_IHDR_DATA_SIZE: Final = 13

#: Signature (8) + chunk length (4) + chunk type (4) + chunk data (13) + CRC (4).
#:
#: The CRC is part of the required prefix, and reading only 29 bytes would not
#: do. A file whose bytes stop immediately after the ``IHDR`` data carries an
#: incomplete mandatory chunk, and a 29-byte contract would accept it and record
#: its dimensions as though the header were whole. The chunk is not present
#: until its CRC is.
PNG_HEADER_SIZE: Final = 33

#: The bit depths the PNG specification permits for each colour type. A pair
#: outside this mapping is not a PNG this build accepts — and checking it costs
#: nothing, because these are bytes the parser has already read.
_PNG_LEGAL_BIT_DEPTHS: Final[dict[int, frozenset[int]]] = {
    0: frozenset({1, 2, 4, 8, 16}),  # greyscale
    2: frozenset({8, 16}),  # truecolour
    3: frozenset({1, 2, 4, 8}),  # indexed
    4: frozenset({8, 16}),  # greyscale with alpha
    6: frozenset({8, 16}),  # truecolour with alpha
}

#: The two bytes every JPEG begins with: ``SOI``.
_JPEG_SOI: Final = b"\xff\xd8"

#: Every marker is introduced by this byte, and repeats of it are fill.
_MARKER_INTRODUCER: Final = 0xFF

#: Markers that stand alone and carry no length: ``TEM``, and the eight restart
#: markers.
_STANDALONE_MARKERS: Final[frozenset[int]] = frozenset({0x01}) | frozenset(range(0xD0, 0xD8))

#: Start of scan. Entropy-coded image data begins here and this build does not
#: enter it.
_MARKER_SOS: Final = 0xDA

#: End of image.
_MARKER_EOI: Final = 0xD9

#: The frame headers that carry the dimensions this processor needs.
#:
#: The markers interleaved in that numeric range are deliberately absent, and
#: they are the whole reason this is a set rather than a range check: ``C4`` is
#: ``DHT``, ``C8`` is the reserved ``JPG``, and ``CC`` is ``DAC``. None of the
#: three is a frame header, and reading one as though it were would take four
#: bytes of a Huffman table for a pair of dimensions.
_SOF_MARKERS: Final[frozenset[int]] = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

#: The structural bytes at the start of an ``SOF`` payload: sample precision (1),
#: height (2), width (2), component count (1).
_SOF_STRUCTURE_SIZE: Final = 6

#: How many marker segments the walk may examine before giving up, and how many
#: bytes of the file it may consume looking for a frame header.
#:
#: These make the walk provably finite over a crafted file, and that is all they
#: are for. Both are generous by orders of magnitude — a real JPEG's ``SOF``
#: follows its ``APPn``, ``DQT`` and ``DHT`` segments within a few kilobytes — and
#: exceeding one means *this build declined to keep looking*, not that the file
#: is globally invalid JPEG.
MAX_JPEG_MARKER_SEGMENTS: Final = 256
MAX_JPEG_SCAN_BYTES: Final = 1_048_576


@dataclass(frozen=True)
class ImageHeader:
    """What a supported image's header says about itself.

    Three values, all read directly out of header bytes, all deterministic. This
    is the whole of what this build observes about an image, and deliberately so:
    it is not a descriptor, a profile, or a place for a later phase to hang
    inferred facts.
    """

    encoded_format: str
    width: int
    height: int


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content and asset ids
    are explicitly *not* derived from the image's digest: that address identifies
    bytes, and two captures of the identical photograph are two different content
    objects holding two different asset records.
    """
    return str(uuid.uuid4())


def _read_exactly(stream: BinaryIO, size: int) -> bytes:
    """Read up to ``size`` bytes, looping until they arrive or the source ends.

    A single ``read(size)`` may legitimately return fewer bytes than asked for
    without being at the end of the data, so the caller cannot use a short result
    to mean "truncated". This loop makes a short return mean exactly one thing.
    """
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_png_header(stream: BinaryIO) -> ImageHeader:
    """Read and validate a PNG's signature and complete ``IHDR`` chunk.

    A module-level function because header validation is worth reasoning about —
    and testing — with no capture, no store, and no content object anywhere near
    it. It reads :data:`PNG_HEADER_SIZE` bytes and nothing else; it opens no path
    and fetches no URL.

    The semantics, in full:

    * **Exactly 33 bytes are read, and all 33 are required.** Signature, chunk
      length, chunk type, chunk data, chunk CRC. A file that stops short of the
      CRC carries an incomplete mandatory chunk and is refused rather than
      accepted on the strength of a header that is not all there.
    * **The signature must be exact**, the declared chunk length must be 13, and
      the chunk type must be ``IHDR`` — the specification makes that chunk
      mandatory and first.
    * **The CRC is verified**, over the chunk type followed by the chunk data, as
      the specification defines it and therefore *not* over the length field.
      This catches a header that is complete but corrupt.
    * **The structural fields are checked**: compression method 0, filter method
      0, interlace method 0 or 1, and a bit depth legal for the colour type.
    * **Both dimensions must be nonzero**, which the specification also requires.

    Nothing beyond those 33 bytes is read. No chunk is walked, no ``IDAT`` is
    touched, no ancillary chunk is inspected, and nothing is decompressed — so
    compressed-ancillary-chunk and chunk-flood concerns are disposed of by
    construction rather than by a limit.

    This verifies the header contract above. It is explicitly *not* a claim that
    the rest of the file is a well-formed PNG pixel stream: nothing here decodes
    one, and a build that wants that guarantee needs a decoder this one does not
    have.

    Raises :class:`~core.processing.errors.ProcessingInputError` for anything
    that fails a check above. The messages name what was wrong structurally and
    carry no byte offsets, no parser state, and no submitted string.
    """
    header = _read_exactly(stream, PNG_HEADER_SIZE)
    if len(header) < PNG_HEADER_SIZE:
        raise ProcessingInputError(
            "the image is too short to carry a complete PNG header; a PNG signature and "
            "its mandatory IHDR chunk, including the chunk's CRC, need 33 bytes"
        )

    if header[:8] != _PNG_SIGNATURE:
        raise ProcessingInputError(
            "the image does not begin with the PNG signature; the bytes are not a PNG, or "
            "are not the format this capture declared"
        )

    declared_length = int.from_bytes(header[8:12], "big")
    chunk_type = header[12:16]
    if chunk_type != _IHDR_TYPE:
        raise ProcessingInputError(
            "the PNG's first chunk is not IHDR; the specification requires the image header "
            "chunk to come first, and this build reads no other layout"
        )
    if declared_length != _IHDR_DATA_SIZE:
        raise ProcessingInputError(
            f"the PNG's IHDR chunk declares a length of {declared_length} rather than the "
            f"required {_IHDR_DATA_SIZE}; the image header is malformed"
        )

    data = header[16:29]
    expected_crc = int.from_bytes(header[29:33], "big")
    # A checksum over 33 bytes already in hand — the chunk type and its data, as
    # the specification defines the CRC's input. This is header integrity, not
    # decompression: `zlib.crc32` is a hash function that happens to live in that
    # module, and nothing here inflates anything.
    if zlib.crc32(chunk_type + data) != expected_crc:
        raise ProcessingInputError(
            "the PNG's IHDR chunk fails its own CRC check; the image header is corrupt"
        )

    width = int.from_bytes(data[0:4], "big")
    height = int.from_bytes(data[4:8], "big")
    if width == 0 or height == 0:
        raise ProcessingInputError(
            "the PNG's IHDR chunk declares a zero width or height; the specification "
            "requires both to be nonzero"
        )

    bit_depth = data[8]
    colour_type = data[9]
    compression = data[10]
    filter_method = data[11]
    interlace = data[12]
    if compression != 0:
        raise ProcessingInputError(
            "the PNG declares a compression method the specification does not define; "
            "only method 0 exists"
        )
    if filter_method != 0:
        raise ProcessingInputError(
            "the PNG declares a filter method the specification does not define; "
            "only method 0 exists"
        )
    if interlace not in (0, 1):
        raise ProcessingInputError(
            "the PNG declares an interlace method the specification does not define; "
            "only 0 (none) and 1 (Adam7) exist"
        )
    legal_depths = _PNG_LEGAL_BIT_DEPTHS.get(colour_type)
    if legal_depths is None:
        raise ProcessingInputError(
            f"the PNG declares colour type {colour_type}, which the specification does not "
            f"define; only 0, 2, 3, 4 and 6 exist"
        )
    if bit_depth not in legal_depths:
        raise ProcessingInputError(
            f"the PNG declares bit depth {bit_depth} for colour type {colour_type}, which "
            f"the specification does not permit for that colour type"
        )

    # Bit depth, colour type, compression, filter and interlace gate acceptance
    # and stop here. They are not promoted into canonical metadata: the required
    # vocabulary is the format and the two dimensions, and a field is not worth
    # recording merely because the parser happened to look at it.
    return ImageHeader(encoded_format=PNG_FORMAT, width=width, height=height)


def read_jpeg_header(stream: BinaryIO) -> ImageHeader:
    """Read a JPEG's dimensions from the first supported frame header.

    The walk, in full:

    * **``SOI`` is required** as the first two bytes.
    * **Markers are read in order.** Each is introduced by one or more ``FF``
      fill bytes followed by the marker code. ``TEM`` and the restart markers
      stand alone; every other marker carries a big-endian length that includes
      its own two bytes.
    * **The first supported ``SOF`` ends the walk successfully.** Its payload
      begins with sample precision, height, width and component count, and both
      dimensions must be nonzero.
    * **``SOS`` or ``EOI`` ends it unsuccessfully.** Entropy-coded scan data
      begins at ``SOS``, and a file whose structural region ends without a frame
      header is one this build cannot read the dimensions of.
    * **Malformation is refused**: a length below 2, a segment reaching past the
      end of the data, a byte where a marker introducer was required, or any
      truncation.

    Two explicit bounds keep the walk finite over a crafted file:
    :data:`MAX_JPEG_MARKER_SEGMENTS` segments examined, and
    :data:`MAX_JPEG_SCAN_BYTES` bytes consumed. Exceeding either is a refusal
    that says this build stopped looking — it is not a verdict that the file is
    invalid JPEG, and the message says so.

    Nothing is decoded. Entropy-coded data is never entered, no Huffman or
    arithmetic decoding happens, no quantization table is applied, and ``APPn``
    payloads — which is where EXIF, XMP and ICC live — are skipped by length
    without their contents being read.

    Raises :class:`~core.processing.errors.ProcessingInputError` for anything
    that fails a rule above.
    """
    # Bounded by construction: at most the scan budget is ever held, so the walk
    # cannot consume more of the file than the limit allows however the segments
    # are laid out.
    data = _read_exactly(stream, MAX_JPEG_SCAN_BYTES)
    exhausted = len(data) < MAX_JPEG_SCAN_BYTES

    def _ran_out() -> ProcessingInputError:
        """Whichever of the two reasons the walk could not continue."""
        if exhausted:
            return ProcessingInputError(
                "the image ends before its JPEG frame header; it appears to be truncated"
            )
        return ProcessingInputError(
            "no JPEG frame header was found within the structural inspection limit this "
            "build applies; the file was not examined further and is not being called "
            "invalid"
        )

    if data[:2] != _JPEG_SOI:
        raise ProcessingInputError(
            "the image does not begin with a JPEG start-of-image marker; the bytes are not "
            "a JPEG, or are not the format this capture declared"
        )

    offset = 2
    examined = 0
    while True:
        if offset >= len(data):
            raise _ran_out()
        if data[offset] != _MARKER_INTRODUCER:
            raise ProcessingInputError(
                "the JPEG's marker structure is malformed; a marker introducer was expected "
                "and something else was found"
            )
        # Repeats of the introducer are fill and carry no meaning.
        while offset < len(data) and data[offset] == _MARKER_INTRODUCER:
            offset += 1
        if offset >= len(data):
            raise _ran_out()

        marker = data[offset]
        offset += 1

        examined += 1
        if examined > MAX_JPEG_MARKER_SEGMENTS:
            raise ProcessingInputError(
                "no JPEG frame header was found within the number of marker segments this "
                "build examines; the file was not examined further and is not being called "
                "invalid"
            )

        if marker in _STANDALONE_MARKERS:
            continue
        if marker == 0x00:
            # A stuffed byte, which is only meaningful inside entropy-coded data
            # — and the walk never gets there, because it stops at SOS.
            raise ProcessingInputError(
                "the JPEG's marker structure is malformed; a stuffed byte appeared outside "
                "any entropy-coded scan"
            )
        if marker in (_MARKER_SOS, _MARKER_EOI):
            raise ProcessingInputError(
                "the JPEG carries no frame header before its scan data ends; this build "
                "reads dimensions from a baseline or progressive frame header and found none"
            )

        if offset + 2 > len(data):
            raise _ran_out()
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2:
            raise ProcessingInputError(
                "the JPEG declares a marker segment shorter than its own length field; "
                "the marker structure is malformed"
            )
        payload_start = offset + 2
        payload_end = offset + segment_length

        if marker in _SOF_MARKERS:
            if segment_length - 2 < _SOF_STRUCTURE_SIZE:
                raise ProcessingInputError(
                    "the JPEG's frame header is too short to carry its own dimensions; "
                    "the marker structure is malformed"
                )
            if payload_start + _SOF_STRUCTURE_SIZE > len(data):
                raise _ran_out()
            height = int.from_bytes(data[payload_start + 1 : payload_start + 3], "big")
            width = int.from_bytes(data[payload_start + 3 : payload_start + 5], "big")
            if width == 0 or height == 0:
                raise ProcessingInputError(
                    "the JPEG's frame header declares a zero width or height; neither is a "
                    "dimension an image can have"
                )
            return ImageHeader(encoded_format=JPEG_FORMAT, width=width, height=height)

        # Every other marker is skipped by its declared length without its
        # payload being read. APPn segments — EXIF, XMP, ICC — are skipped here,
        # deliberately and without being looked at.
        if payload_end > len(data):
            raise _ran_out()
        offset = payload_end


#: Which reader runs for which declared MIME type. The declaration decides, and
#: the reader then verifies the bytes are consistent with it: a PNG declared
#: ``image/jpeg`` fails the ``SOI`` check, and a JPEG declared ``image/png``
#: fails the signature check. Neither is re-examined to discover what it "really"
#: is, and neither is rerouted — refusing a contradiction is not inferring a
#: format.
_HEADER_READERS: Final[dict[str, Callable[[BinaryIO], ImageHeader]]] = {
    PNG_MIME_TYPE: read_png_header,
    JPEG_MIME_TYPE: read_jpeg_header,
}


def _original_asset(raw_object: RawObjectRef, ref: str, mime_type: str) -> Asset:
    """Describe the immutable original image as an asset of the content object.

    This matters more here than anywhere yet. For a document the asset is where
    the layout and the fonts the extractor did not read still live; for an image
    the asset is where *the entire content* lives, because this build derives
    nothing from the pixels at all. The content object without its original would
    be a record of three integers.

    The asset id is minted fresh, like every other id here. An asset is a record
    *within one content object* that points at an original; it is not the
    original. The raw object's identity travels on ``ref`` and ``sha256``.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=mime_type,
        ref=ref,
        sha256=raw_object.sha256,
    )


class ImageProcessor:
    """Normalizes staged PNG and JPEG captures into canonical content."""

    name = "image"
    #: The first version of these semantics. ``version`` describes *what this
    #: processor produces*, so any change to the rules above — the supported
    #: formats, what the header contract requires, the metadata vocabulary, the
    #: decision to emit no segments — changes it, and the new value travels
    #: automatically onto every ``ProcessingRecord`` emitted afterwards.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore) -> None:
        """Take the store the immutable original will be read through.

        The dependency is the :class:`~core.storage.raw.RawObjectStore` port, so
        the processor works against any backend and reaches into none of them. It
        is also the *only* dependency: no imaging library, no rasterizer, no OCR
        engine, no vision model, no HTTP client, no subprocess, and no filesystem
        access of its own.
        """
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged PNG and JPEG images and nothing else. Pure; touches no storage.

        Three facts, all read from the capture record: it is an ``image``, it has
        a raw original, and that original is declared one of
        :data:`IMAGE_MIME_TYPES`.

        Deliberately *not* "all images". Claiming the payload type alone would
        claim WebP, HEIC, and every other format the day one arrives, and the
        router's exactly-one-match rule would then report an ambiguity that is
        really a design mistake here.

        Whether the bytes really carry a readable header is a ``process``
        question, not a routing one: answering it here would mean reading storage
        to route, and an image capture whose original cannot currently be read is
        still an image capture this processor handles.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.IMAGE
            and raw_object is not None
            and raw_object.mime_type in IMAGE_MIME_TYPES
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Read the original's header and build canonical content around it.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not altered, its declared MIME type is not
        corrected, and the raw original is not modified. Lifecycle is
        orchestration's business, and a declaration this build disagrees with is
        a refusal rather than something to quietly fix.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError` — a missing object, a
        malformed reference — and that propagates unchanged, exactly as it does
        from the other processors. Those describe the state of the store rather
        than of the capture, and a caller deciding whether to retry needs to tell
        them apart from an image that will never process.
        """
        started_at = datetime.now(UTC)

        # All three preconditions are properties of the capture record itself, so
        # they are settled before any I/O. The MIME check repeats what
        # ``supports`` already asked, because ``process`` is callable directly and
        # must not read a PDF as though it were an image just because nobody
        # routed the capture first.
        raw_object = capture.raw_object
        if raw_object is None:
            raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
        if raw_object.ref is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references raw object {raw_object.id!r} "
                f"without a storage reference"
            )
        mime_type = raw_object.mime_type
        if mime_type is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references a raw object declaring no mime_type; "
                f"this processor reads {_IMAGE_MIME_TYPES_PHRASE} and does not infer a "
                f"format from the bytes"
            )
        reader = _HEADER_READERS.get(mime_type)
        if reader is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references a raw object that is not declared one of "
                f"{_IMAGE_MIME_TYPES_PHRASE}; this processor reads those still images only"
            )

        # Streamed rather than read whole. The parsers consume a bounded prefix
        # and stop, so the rest of the image is never brought into memory — which
        # is what makes reading a very large file cost the same as reading a
        # small one here.
        with self._raw_store.open(raw_object) as handle:
            header = reader(handle)

        original = _original_asset(raw_object, raw_object.ref, mime_type)
        metadata: JsonMapping = {
            IMAGE_METADATA_KEY: {
                ENCODED_FORMAT_KEY: header.encoded_format,
                ENCODED_WIDTH_KEY: header.width,
                ENCODED_HEIGHT_KEY: header.height,
            }
        }
        completed_at = datetime.now(UTC)

        return ContentObject(
            id=_new_id(),
            type=ContentType.IMAGE,
            source=ContentSource(
                capture_id=capture.id,
                provider=capture.source.provider,
                url=capture.source.url,
            ),
            original=OriginalReference(
                asset_id=original.id,
                mime_type=mime_type,
                sha256=raw_object.sha256,
            ),
            # The submitted capture title, exactly as given, or none. There is no
            # second source and there is deliberately no fallback: not the
            # uploaded filename, which is untrusted client text this build never
            # even records; not the ``file_ref`` or the digest, which name bytes
            # rather than a picture; and nothing read out of the image, because
            # this build reads nothing out of the image.
            title=capture.title,
            metadata=metadata,
            # Empty, and this is the decision the whole phase turns on. An image
            # nothing has interpreted has no segments, because a segment is
            # something a processor read or recognized and this one did neither.
            # A placeholder ``VISUAL`` segment here would carry no fact the asset
            # does not already carry and would be indistinguishable downstream
            # from one a vision model actually produced. See ADR-019.
            segments=[],
            assets=[original],
            processing=[
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started_at,
                    completed_at=completed_at,
                    # ``COMPLETE`` describes *this processor's* run, and
                    # ``image@0.1`` says what that run is: the original is held
                    # and its structure described, nothing is interpreted. It did
                    # that, entirely. It is not a claim that anything was
                    # understood, and ``PARTIAL`` would be a different falsehood —
                    # nothing was attempted and missed.
                    status=ProcessingStatus.COMPLETE,
                )
            ],
        )
