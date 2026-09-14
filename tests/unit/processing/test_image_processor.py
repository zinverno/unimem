"""The still-image processor: a complete capture that carries no segments.

Most of this file is about refusals, because a header parser is mostly a list of
things that must not be believed. But the assertion the phase turns on is the
quiet one at the top: a perfectly good PNG normalizes to a ``COMPLETE`` content
object with ``segments == []``, and nothing — no placeholder, no caption, no
empty ``VISUAL`` segment, no OCR provenance — is invented to fill that space.

The parser tests hand streams to :func:`read_png_header` and
:func:`read_jpeg_header` directly, with no capture, no store, and no content
object anywhere near them, for the same reason the PDF tests exercise
``extract_pages`` on its own: header validation is worth reasoning about as
header validation.
"""

import io
import struct
from typing import Any, BinaryIO

import pytest

from core.contracts import (
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentObject,
    ContentType,
    ProcessingStatus,
)
from core.processing import (
    DOCX_MIME_TYPE,
    ENCODED_FORMAT_KEY,
    ENCODED_HEIGHT_KEY,
    ENCODED_WIDTH_KEY,
    IMAGE_METADATA_KEY,
    JPEG_MIME_TYPE,
    MAX_JPEG_MARKER_SEGMENTS,
    MAX_JPEG_SCAN_BYTES,
    PDF_MIME_TYPE,
    PNG_HEADER_SIZE,
    PNG_MIME_TYPE,
    ImageProcessor,
    ProcessingInputError,
    read_jpeg_header,
    read_png_header,
)
from core.storage import RawObjectNotFoundError
from tests import images
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore

PNG_BYTES = images.png()
JPEG_BYTES = images.jpeg()


@pytest.fixture
def image_processor(store: InMemoryRawObjectStore) -> ImageProcessor:
    return ImageProcessor(store)


def image_capture(
    store: InMemoryRawObjectStore,
    data: bytes = PNG_BYTES,
    *,
    mime_type: str | None = PNG_MIME_TYPE,
    **overrides: object,
) -> CaptureRecord:
    fields: dict[str, object] = {"payload_type": CapturePayloadType.IMAGE}
    return store_and_capture(store, data, mime_type=mime_type, **(fields | overrides))


class CountingStream(io.BytesIO):
    """A stream that remembers how many bytes were taken out of it."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.consumed = 0

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk


# --- identity and routing ---------------------------------------------------


def test_processor_identity(image_processor: ImageProcessor) -> None:
    assert (image_processor.name, image_processor.version) == ("image", "0.1")


@pytest.mark.parametrize("mime_type", [PNG_MIME_TYPE, JPEG_MIME_TYPE])
def test_it_supports_the_declared_image_formats(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore, mime_type: str
) -> None:
    assert image_processor.supports(image_capture(store, mime_type=mime_type)) is True


@pytest.mark.parametrize(
    "mime_type",
    ["image/webp", "image/gif", "image/tiff", "image/svg+xml", PDF_MIME_TYPE, DOCX_MIME_TYPE],
)
def test_it_claims_no_other_format(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore, mime_type: str
) -> None:
    """Deferred formats and document formats alike are somebody else's, or nobody's."""
    assert image_processor.supports(image_capture(store, mime_type=mime_type)) is False


def test_it_claims_no_capture_without_a_raw_object(image_processor: ImageProcessor) -> None:
    capture = make_capture(payload_type=CapturePayloadType.IMAGE, raw_object=None)

    assert image_processor.supports(capture) is False


def test_it_claims_no_other_payload_type(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = image_capture(store, payload_type=CapturePayloadType.DOCUMENT)

    assert image_processor.supports(capture) is False


def test_supports_reads_no_storage(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = image_capture(store)

    image_processor.supports(capture)

    assert store.accesses == []


# --- the canonical object ---------------------------------------------------


class TestAValidPngBecomesCanonicalContent:
    @pytest.fixture
    def content(
        self, image_processor: ImageProcessor, store: InMemoryRawObjectStore
    ) -> ContentObject:
        return image_processor.process(image_capture(store))

    def test_it_is_an_image_content_object(self, content: ContentObject) -> None:
        assert content.type is ContentType.IMAGE

    def test_it_carries_no_segments(self, content: ContentObject) -> None:
        """The decision the whole phase turns on: absence, not a placeholder."""
        assert content.segments == []

    def test_it_is_attributed_to_its_capture(self, content: ContentObject) -> None:
        assert content.source.capture_id == "cap_text_01"

    def test_the_original_is_the_one_asset(self, content: ContentObject) -> None:
        assert len(content.assets) == 1
        assert content.assets[0].role is AssetRole.ORIGINAL
        assert content.assets[0].mime_type == PNG_MIME_TYPE

    def test_the_asset_preserves_the_staged_identity(
        self,
        content: ContentObject,
        store: InMemoryRawObjectStore,
    ) -> None:
        staged = store.store_bytes(PNG_BYTES, mime_type=PNG_MIME_TYPE)
        assert content.assets[0].ref == staged.ref
        assert content.assets[0].sha256 == staged.sha256

    def test_the_original_reference_points_at_that_asset(self, content: ContentObject) -> None:
        assert content.original.asset_id == content.assets[0].id
        assert content.original.mime_type == PNG_MIME_TYPE
        assert content.original.sha256 == content.assets[0].sha256

    def test_the_metadata_is_exactly_the_required_vocabulary(self, content: ContentObject) -> None:
        assert content.metadata == {
            IMAGE_METADATA_KEY: {
                ENCODED_FORMAT_KEY: "png",
                ENCODED_WIDTH_KEY: images.DEFAULT_WIDTH,
                ENCODED_HEIGHT_KEY: images.DEFAULT_HEIGHT,
            }
        }

    def test_nothing_is_derived(self, content: ContentObject) -> None:
        assert content.derived.summary is None
        assert content.derived.topics == []
        assert content.derived.entities == []

    def test_one_complete_processing_record(self, content: ContentObject) -> None:
        assert len(content.processing) == 1
        record = content.processing[0]
        assert (record.processor, record.processor_version) == ("image", "0.1")
        assert record.status is ProcessingStatus.COMPLETE

    def test_a_successful_run_claims_no_warnings_or_errors(self, content: ContentObject) -> None:
        assert content.processing[0].warnings == []
        assert content.processing[0].errors == []

    def test_the_ids_are_not_the_digest(self, content: ContentObject) -> None:
        """Content and asset ids name records, not bytes."""
        digest = content.assets[0].sha256
        assert content.id != digest
        assert content.assets[0].id != digest


def test_a_valid_jpeg_becomes_the_same_shape(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    content = image_processor.process(image_capture(store, JPEG_BYTES, mime_type=JPEG_MIME_TYPE))

    assert content.type is ContentType.IMAGE
    assert content.segments == []
    assert content.assets[0].mime_type == JPEG_MIME_TYPE
    assert content.metadata == {
        IMAGE_METADATA_KEY: {
            ENCODED_FORMAT_KEY: "jpeg",
            ENCODED_WIDTH_KEY: images.DEFAULT_WIDTH,
            ENCODED_HEIGHT_KEY: images.DEFAULT_HEIGHT,
        }
    }


def test_the_submitted_title_is_carried_exactly(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    content = image_processor.process(image_capture(store, title=images.SUBMITTED_TITLE))

    assert content.title == images.SUBMITTED_TITLE


def test_no_submitted_title_stays_none(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """Nothing is invented: not a filename, not a digest, not a dimension string."""
    content = image_processor.process(image_capture(store, title=None))

    assert content.title is None


def test_nothing_about_the_image_reaches_a_title(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    content = image_processor.process(image_capture(store, title=None))

    assert content.title is None
    assert content.derived.summary is None


# --- preconditions and storage ---------------------------------------------


def test_a_capture_without_a_raw_object_is_refused(image_processor: ImageProcessor) -> None:
    capture = make_capture(payload_type=CapturePayloadType.IMAGE, raw_object=None)

    with pytest.raises(ProcessingInputError, match="no raw object"):
        image_processor.process(capture)


def test_a_raw_object_without_a_reference_is_refused(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = image_capture(store)
    assert capture.raw_object is not None
    capture.raw_object.ref = None

    with pytest.raises(ProcessingInputError, match="without a storage reference"):
        image_processor.process(capture)


@pytest.mark.parametrize("mime_type", [PDF_MIME_TYPE, "image/webp", "image/heic"])
def test_process_re_checks_the_declared_format(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore, mime_type: str
) -> None:
    """``process`` is callable without the router, so it enforces its own preconditions."""
    capture = image_capture(store, mime_type=mime_type)

    with pytest.raises(ProcessingInputError, match="still images only"):
        image_processor.process(capture)


def test_a_raw_object_declaring_no_format_is_refused(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """A missing declaration is its own fact, and is not a cue to sniff the bytes."""
    capture = image_capture(store, mime_type=None)

    with pytest.raises(ProcessingInputError, match="no mime_type"):
        image_processor.process(capture)


def test_a_storage_failure_keeps_its_own_type(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """A store that cannot produce the bytes says so in its own vocabulary."""
    capture = image_capture(store)
    assert capture.raw_object is not None
    capture.raw_object.ref = "sha256:" + "0" * 64

    with pytest.raises(RawObjectNotFoundError):
        image_processor.process(capture)


# --- MIME consistency -------------------------------------------------------


def test_png_bytes_declared_jpeg_fail_processing(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = image_capture(store, PNG_BYTES, mime_type=JPEG_MIME_TYPE)

    with pytest.raises(ProcessingInputError, match="start-of-image"):
        image_processor.process(capture)


def test_jpeg_bytes_declared_png_fail_processing(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    capture = image_capture(store, JPEG_BYTES, mime_type=PNG_MIME_TYPE)

    with pytest.raises(ProcessingInputError, match="PNG signature"):
        image_processor.process(capture)


def test_a_mismatch_does_not_correct_the_capture(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """The declaration is not auto-corrected, and the record is not rewritten."""
    capture = image_capture(store, PNG_BYTES, mime_type=JPEG_MIME_TYPE)

    with pytest.raises(ProcessingInputError):
        image_processor.process(capture)

    assert capture.raw_object is not None
    assert capture.raw_object.mime_type == JPEG_MIME_TYPE


# --- the PNG header contract ------------------------------------------------


def png_header(**overrides: Any) -> BinaryIO:
    """A stream over a PNG whose header says exactly what the keywords ask for."""
    return io.BytesIO(images.png(**overrides))


def test_the_required_png_prefix_is_thirty_three_bytes() -> None:
    """Signature, length, type, data, and the CRC — the whole mandatory chunk."""
    assert PNG_HEADER_SIZE == 33


def test_a_valid_png_header_reads_its_dimensions() -> None:
    header = read_png_header(png_header(width=1024, height=768))

    assert (header.encoded_format, header.width, header.height) == ("png", 1024, 768)


def test_only_the_fixed_prefix_is_consumed() -> None:
    """Nothing after the IHDR chunk is read, whatever the rest of the file holds."""
    stream = CountingStream(images.png())

    read_png_header(stream)

    assert stream.consumed == PNG_HEADER_SIZE


def test_a_png_whose_pixel_data_is_nonsense_still_processes(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """The header contract is what is verified; no pixel stream is decoded."""
    capture = image_capture(store, images.png(tail=b"not an IDAT chunk at all"))

    assert image_processor.process(capture).segments == []


def test_a_png_truncated_before_the_crc_is_refused() -> None:
    """The 29-byte case: the mandatory chunk is not present until its CRC is."""
    with pytest.raises(ProcessingInputError, match="33 bytes"):
        read_png_header(io.BytesIO(images.png()[:29]))


@pytest.mark.parametrize("length", [0, 8, 28, 32])
def test_a_png_shorter_than_the_prefix_is_refused(length: int) -> None:
    with pytest.raises(ProcessingInputError, match="too short"):
        read_png_header(io.BytesIO(images.png()[:length]))


def test_a_bad_png_signature_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="PNG signature"):
        read_png_header(png_header(signature=b"\x89PNG\r\n\x1a\x00"))


def test_a_declared_ihdr_length_other_than_thirteen_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="rather than the required 13"):
        read_png_header(png_header(declared_length=12))


def test_a_first_chunk_that_is_not_ihdr_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="first chunk is not IHDR"):
        read_png_header(png_header(chunk_type=b"sRGB"))


@pytest.mark.parametrize(("width", "height"), [(0, 3), (7, 0), (0, 0)])
def test_a_zero_png_dimension_is_refused(width: int, height: int) -> None:
    with pytest.raises(ProcessingInputError, match="zero width or height"):
        read_png_header(png_header(width=width, height=height))


def test_a_bad_ihdr_crc_is_refused() -> None:
    """A header that is complete but corrupt, which length checks alone would pass."""
    with pytest.raises(ProcessingInputError, match="CRC"):
        read_png_header(png_header(crc=0xDEADBEEF))


def test_the_crc_covers_the_chunk_type_and_data() -> None:
    """Computed over ``b"IHDR"`` plus the data, per the specification — not the length."""
    import zlib

    data = images.ihdr_data()
    over_length_and_data = zlib.crc32(struct.pack(">I", 13) + data)

    with pytest.raises(ProcessingInputError, match="CRC"):
        read_png_header(png_header(crc=over_length_and_data))


def test_an_undefined_compression_method_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="compression method"):
        read_png_header(png_header(compression=1))


def test_an_undefined_filter_method_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="filter method"):
        read_png_header(png_header(filter_method=1))


@pytest.mark.parametrize("interlace", [2, 3, 255])
def test_an_undefined_interlace_method_is_refused(interlace: int) -> None:
    with pytest.raises(ProcessingInputError, match="interlace method"):
        read_png_header(png_header(interlace=interlace))


@pytest.mark.parametrize("interlace", [0, 1])
def test_both_defined_interlace_methods_are_accepted(interlace: int) -> None:
    assert read_png_header(png_header(interlace=interlace)).width == images.DEFAULT_WIDTH


@pytest.mark.parametrize("colour_type", [1, 5, 7, 255])
def test_an_undefined_colour_type_is_refused(colour_type: int) -> None:
    with pytest.raises(ProcessingInputError, match="colour type"):
        read_png_header(png_header(colour_type=colour_type, bit_depth=8))


@pytest.mark.parametrize(
    ("colour_type", "bit_depth"),
    [
        (0, 3),
        (0, 32),
        (2, 1),
        (2, 2),
        (2, 4),
        (3, 16),
        (4, 1),
        (4, 4),
        (6, 2),
        (6, 4),
    ],
)
def test_an_illegal_bit_depth_for_its_colour_type_is_refused(
    colour_type: int, bit_depth: int
) -> None:
    """Every colour-type family has depths the specification does not permit for it."""
    with pytest.raises(ProcessingInputError, match="bit depth"):
        read_png_header(png_header(colour_type=colour_type, bit_depth=bit_depth))


@pytest.mark.parametrize(
    ("colour_type", "bit_depth"),
    [
        (0, 1),
        (0, 2),
        (0, 4),
        (0, 8),
        (0, 16),
        (2, 8),
        (2, 16),
        (3, 1),
        (3, 2),
        (3, 4),
        (3, 8),
        (4, 8),
        (4, 16),
        (6, 8),
        (6, 16),
    ],
)
def test_every_legal_colour_type_and_bit_depth_pair_is_accepted(
    colour_type: int, bit_depth: int
) -> None:
    header = read_png_header(png_header(colour_type=colour_type, bit_depth=bit_depth))

    assert header.encoded_format == "png"


def test_validated_header_fields_are_not_promoted_to_metadata(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """Bit depth and colour type gate acceptance and stop there."""
    capture = image_capture(store, images.png(colour_type=0, bit_depth=16))

    observed = image_processor.process(capture).metadata[IMAGE_METADATA_KEY]

    assert isinstance(observed, dict)
    assert set(observed) == {ENCODED_FORMAT_KEY, ENCODED_WIDTH_KEY, ENCODED_HEIGHT_KEY}


def test_a_very_large_declared_size_is_not_a_refusal() -> None:
    """No dimension ceiling: nothing is decoded, so a large number is just a number."""
    header = read_png_header(png_header(width=100_000, height=100_000))

    assert (header.width, header.height) == (100_000, 100_000)


# --- the JPEG marker walk ---------------------------------------------------


def test_a_valid_jpeg_header_reads_its_dimensions() -> None:
    header = read_jpeg_header(io.BytesIO(images.jpeg(width=1920, height=1080)))

    assert (header.encoded_format, header.width, header.height) == ("jpeg", 1920, 1080)


def test_app_segments_are_skipped_to_reach_the_frame_header() -> None:
    """EXIF, XMP and ICC live in APPn segments, and are stepped over unread."""
    leading = (
        images.JFIF_APP0
        + images.jpeg_segment(0xE1, b"Exif\x00\x00" + b"\x01" * 64)
        + images.jpeg_segment(0xE2, b"ICC_PROFILE\x00" + b"\x02" * 32)
        + images.jpeg_segment(0xDB, b"\x00" + b"\x10" * 64)
    )

    assert read_jpeg_header(io.BytesIO(images.jpeg(leading=leading))).width == 7


def test_fill_bytes_before_a_marker_are_consumed() -> None:
    assert read_jpeg_header(io.BytesIO(images.jpeg(leading=b"\xff\xff" + images.JFIF_APP0)))


@pytest.mark.parametrize(
    "marker", [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF]
)
def test_every_supported_frame_header_is_recognized(marker: int) -> None:
    header = read_jpeg_header(io.BytesIO(images.jpeg(marker=marker, width=11, height=13)))

    assert (header.width, header.height) == (11, 13)


@pytest.mark.parametrize("marker", [0xC4, 0xC8, 0xCC])
def test_the_markers_inside_the_sof_range_that_are_not_frames_are_skipped(marker: int) -> None:
    """``C4`` is DHT, ``C8`` is reserved, ``CC`` is DAC. None carries dimensions."""
    leading = images.jpeg_segment(marker, b"\x01\x02\x03\x04\x05\x06\x07\x08")

    header = read_jpeg_header(io.BytesIO(images.jpeg(leading=leading, width=5, height=9)))

    assert (header.width, header.height) == (5, 9)


@pytest.mark.parametrize("marker", [*range(0xD0, 0xD8), 0x01])
def test_standalone_markers_carry_no_length(marker: int) -> None:
    leading = bytes([0xFF, marker]) + images.JFIF_APP0

    assert read_jpeg_header(io.BytesIO(images.jpeg(leading=leading))).width == 7


def test_a_missing_soi_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="start-of-image"):
        read_jpeg_header(io.BytesIO(images.jpeg(soi=b"\xff\xd9")))


@pytest.mark.parametrize(("width", "height"), [(0, 5), (7, 0), (0, 0)])
def test_a_zero_jpeg_dimension_is_refused(width: int, height: int) -> None:
    with pytest.raises(ProcessingInputError, match="zero width or height"):
        read_jpeg_header(io.BytesIO(images.jpeg(width=width, height=height)))


def test_a_segment_length_below_two_is_refused() -> None:
    leading = bytes([0xFF, 0xE1]) + struct.pack(">H", 1) + b"\x00"

    with pytest.raises(ProcessingInputError, match="shorter than its own length field"):
        read_jpeg_header(io.BytesIO(images.jpeg(leading=leading)))


def test_a_segment_reaching_past_the_end_is_refused() -> None:
    data = images.JPEG_SOI + bytes([0xFF, 0xE1]) + struct.pack(">H", 4096) + b"\x00" * 8

    with pytest.raises(ProcessingInputError, match="truncated"):
        read_jpeg_header(io.BytesIO(data))


def test_a_truncated_length_field_is_refused() -> None:
    data = images.JPEG_SOI + bytes([0xFF, 0xE1, 0x00])

    with pytest.raises(ProcessingInputError, match="truncated"):
        read_jpeg_header(io.BytesIO(data))


def test_a_stream_that_ends_after_soi_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="truncated"):
        read_jpeg_header(io.BytesIO(images.JPEG_SOI))


def test_a_stream_that_ends_in_fill_bytes_is_refused() -> None:
    """Fill is consumed, and then there is no marker code left to read."""
    with pytest.raises(ProcessingInputError, match="truncated"):
        read_jpeg_header(io.BytesIO(images.JPEG_SOI + b"\xff\xff\xff"))


def test_a_truncated_frame_header_payload_is_refused() -> None:
    full = images.jpeg()
    with pytest.raises(ProcessingInputError, match="truncated"):
        read_jpeg_header(io.BytesIO(full[: full.index(b"\xff\xc0") + 6]))


def test_a_frame_header_too_short_for_its_dimensions_is_refused() -> None:
    leading = images.jpeg_segment(0xC0, b"\x08\x00\x05")

    with pytest.raises(ProcessingInputError, match="too short to carry its own dimensions"):
        read_jpeg_header(io.BytesIO(images.JPEG_SOI + leading + images.JPEG_SCAN_TAIL))


def test_a_missing_marker_introducer_is_refused() -> None:
    data = images.JPEG_SOI + b"\x00\x01\x02\x03"

    with pytest.raises(ProcessingInputError, match="marker introducer was expected"):
        read_jpeg_header(io.BytesIO(data))


def test_a_stuffed_byte_outside_a_scan_is_refused() -> None:
    data = images.JPEG_SOI + b"\xff\x00" + images.JFIF_APP0

    with pytest.raises(ProcessingInputError, match="stuffed byte"):
        read_jpeg_header(io.BytesIO(data))


def test_scan_data_before_a_frame_header_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="no frame header"):
        read_jpeg_header(io.BytesIO(images.jpeg_without_frame_header()))


def test_end_of_image_before_a_frame_header_is_refused() -> None:
    with pytest.raises(ProcessingInputError, match="no frame header"):
        read_jpeg_header(io.BytesIO(images.jpeg_without_frame_header(terminator=b"\xff\xd9")))


def test_the_walk_stops_at_the_frame_header() -> None:
    """A malformed segment after the SOF is never reached, so it cannot matter."""
    broken = images.JPEG_SOI + images.sof_segment() + b"\xff\xe1\x00\x01"

    assert read_jpeg_header(io.BytesIO(broken)).width == images.DEFAULT_WIDTH


def test_entropy_coded_data_is_never_entered(
    image_processor: ImageProcessor, store: InMemoryRawObjectStore
) -> None:
    """Scan bytes that no decoder would accept do not stop the header being read."""
    nonsense_scan = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + bytes(range(256)) * 4
    data = images.JPEG_SOI + images.JFIF_APP0 + images.sof_segment() + nonsense_scan

    content = image_processor.process(image_capture(store, data, mime_type=JPEG_MIME_TYPE))

    assert content.segments == []


def test_the_marker_segment_bound_is_two_hundred_and_fifty_six() -> None:
    assert MAX_JPEG_MARKER_SEGMENTS == 256


def test_the_byte_bound_is_one_mebibyte() -> None:
    assert MAX_JPEG_SCAN_BYTES == 1_048_576


def test_a_frame_header_within_the_segment_bound_is_found() -> None:
    within = images.jpeg_with_many_markers(MAX_JPEG_MARKER_SEGMENTS - 1)

    assert read_jpeg_header(io.BytesIO(within)).width == images.DEFAULT_WIDTH


def test_too_many_marker_segments_stops_the_search() -> None:
    beyond = images.jpeg_with_many_markers(MAX_JPEG_MARKER_SEGMENTS)

    with pytest.raises(ProcessingInputError, match="number of marker segments"):
        read_jpeg_header(io.BytesIO(beyond))


def test_too_many_bytes_stops_the_search() -> None:
    beyond = images.jpeg_with_bulky_segments(MAX_JPEG_SCAN_BYTES)

    with pytest.raises(ProcessingInputError, match="structural inspection limit"):
        read_jpeg_header(io.BytesIO(beyond))


def test_the_byte_bound_is_reached_well_inside_the_segment_bound() -> None:
    """A segment's length field is 16 bits, so the two bounds are genuinely distinct."""
    beyond = images.jpeg_with_bulky_segments(MAX_JPEG_SCAN_BYTES)
    segments = beyond.count(b"\xff\xe1")

    assert segments < MAX_JPEG_MARKER_SEGMENTS
    assert len(beyond) > MAX_JPEG_SCAN_BYTES


def test_exceeding_a_search_bound_does_not_call_the_file_invalid() -> None:
    """The refusal says this build stopped looking, not that the JPEG is broken."""
    with pytest.raises(ProcessingInputError, match="not being called"):
        read_jpeg_header(io.BytesIO(images.jpeg_with_bulky_segments(MAX_JPEG_SCAN_BYTES)))


def test_a_very_large_declared_jpeg_size_is_not_a_refusal() -> None:
    header = read_jpeg_header(io.BytesIO(images.jpeg(width=65535, height=65535)))

    assert (header.width, header.height) == (65535, 65535)
