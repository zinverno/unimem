"""The OCR-enabled image processor: enrichment that never costs the capture.

The assertions this phase turns on are the three that all end in ``COMPLETE``.
An image whose pixels carry words gains one ``OCR`` segment. An image the engine
read and found nothing in gains none — and is *not* called blank. An image this
deployment declined to spend the work on gains none either, and says so. All
three keep the original, the structural metadata, and the title semantics Phase
4A fixed, byte for byte.

The fourth outcome is the one that must not join them: a recognition that
produced no trusted result is not a successful capture with OCR skipped. Those
are different facts, and the tests below check that the processor refuses to
merge them.

There is no engine anywhere in this file, no imaging library, no rasterizer, and
no subprocess. The recognizer is a fake that answers from a script — which is the
whole point of the port existing.
"""

from typing import Any

import pytest

from core.contracts import (
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentObject,
    ContentType,
    ProcessingStatus,
    ProvenanceSourceType,
    SegmentType,
)
from core.processing import (
    ENCODED_BYTE_LIMIT,
    ENCODED_FORMAT_KEY,
    ENCODED_HEIGHT_KEY,
    ENCODED_PIXEL_LIMIT,
    ENCODED_WIDTH_KEY,
    ENGINE_INVOKED_KEY,
    ENGINE_KEY,
    ENGINE_VERSION_KEY,
    IMAGE_METADATA_KEY,
    IMAGE_OCR_METADATA_KEY,
    JPEG_MIME_TYPE,
    MAX_ENCODED_BYTES_KEY,
    MAX_ENCODED_PIXELS_KEY,
    PDF_MIME_TYPE,
    PNG_MIME_TYPE,
    SETTINGS_KEY,
    SKIPPED_REASON_KEY,
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
    ImageOcrProcessor,
    ImageProcessor,
    ProcessingInputError,
)
from tests import images
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import FakeImageOcr, InMemoryRawObjectStore

PNG_BYTES = images.png()
JPEG_BYTES = images.jpeg()

#: Text with leading, trailing, and internal whitespace, so any test that passes
#: while the processor trims is a test that was not looking.
RAGGED_TEXT = "  HARBOUR  \n\n  pier 4\n"


def image_capture(
    store: InMemoryRawObjectStore,
    data: bytes = PNG_BYTES,
    *,
    mime_type: str | None = PNG_MIME_TYPE,
    **overrides: Any,
) -> CaptureRecord:
    fields: dict[str, Any] = {"payload_type": CapturePayloadType.IMAGE}
    return store_and_capture(store, data, mime_type=mime_type, **(fields | overrides))


@pytest.fixture
def recognizer() -> FakeImageOcr:
    return FakeImageOcr(text=RAGGED_TEXT)


@pytest.fixture
def ocr_processor(store: InMemoryRawObjectStore, recognizer: FakeImageOcr) -> ImageOcrProcessor:
    return ImageOcrProcessor(store, recognizer)


def image_metadata(content: ContentObject) -> dict[str, Any]:
    """The ``image_ocr`` mapping, narrowed from ``JsonValue`` so it can be indexed."""
    mapping = content.metadata[IMAGE_OCR_METADATA_KEY]
    assert isinstance(mapping, dict)
    return mapping


def structural_metadata(content: ContentObject) -> dict[str, Any]:
    """The Phase 4A ``image`` mapping, narrowed the same way."""
    mapping = content.metadata[IMAGE_METADATA_KEY]
    assert isinstance(mapping, dict)
    return mapping


class TestItsIdentity:
    def test_it_names_itself_image_ocr(self, ocr_processor: ImageOcrProcessor) -> None:
        assert (ocr_processor.name, ocr_processor.version) == ("image-ocr", "0.1")

    def test_it_is_not_the_default_processors_identity(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Two names, so a stored object says which semantics produced it."""
        assert ocr_processor.name != ImageProcessor(store).name

    def test_it_is_not_a_subclass_of_the_default_processor(
        self, ocr_processor: ImageOcrProcessor
    ) -> None:
        """Inheritance would make the default build depend on this one's overrides."""
        assert not isinstance(ocr_processor, ImageProcessor)


class TestWhatItClaims:
    def test_it_claims_a_staged_png(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert ocr_processor.supports(image_capture(store))

    def test_it_claims_a_staged_jpeg(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert ocr_processor.supports(image_capture(store, JPEG_BYTES, mime_type=JPEG_MIME_TYPE))

    def test_it_claims_exactly_what_the_default_processor_claims(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The same claim, which is what makes them alternatives rather than a pair.

        Any divergence here would be a routing bug rather than a feature: a
        capture one of them handled and the other did not would behave
        differently depending on a deployment flag, which is precisely what the
        exactly-one-match rule exists to prevent.
        """
        default = ImageProcessor(store)
        captures = [
            image_capture(store),
            image_capture(store, JPEG_BYTES, mime_type=JPEG_MIME_TYPE),
            image_capture(store, b"%PDF-1.7", mime_type=PDF_MIME_TYPE),
            image_capture(store, PNG_BYTES, mime_type=None),
            make_capture(),
            store_and_capture(store, b"doc", mime_type=PDF_MIME_TYPE),
        ]

        assert [ocr_processor.supports(c) for c in captures] == [
            default.supports(c) for c in captures
        ]

    def test_it_does_not_claim_a_document(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert not ocr_processor.supports(
            store_and_capture(store, b"%PDF", mime_type=PDF_MIME_TYPE)
        )

    def test_supports_reads_no_storage(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The router asks every processor, so answering must stay free."""
        capture = image_capture(store)
        store.accesses.clear()

        ocr_processor.supports(capture)

        assert store.accesses == []

    def test_supports_calls_no_recognizer(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        ocr_processor.supports(image_capture(store))

        assert recognizer.calls == []

    def test_an_unreadable_image_is_still_claimed(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Whether the header parses is a ``process`` question, not a routing one."""
        assert ocr_processor.supports(image_capture(store, b"not an image at all"))


class TestPreconditionsMatchPhase4A:
    """``process`` is callable directly, so it re-asks what ``supports`` asked."""

    def test_a_capture_with_no_raw_object_is_refused(
        self, ocr_processor: ImageOcrProcessor
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(make_capture(payload_type=CapturePayloadType.IMAGE))

    def test_a_raw_object_with_no_mime_type_is_refused(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, mime_type=None))

    def test_a_document_handed_in_directly_is_refused(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, b"%PDF-1.7", mime_type=PDF_MIME_TYPE))

    @pytest.mark.parametrize(
        "capture_data",
        [b"", b"not an image", PNG_BYTES[:20]],
        ids=["empty", "garbage", "truncated"],
    )
    def test_no_precondition_failure_ever_reaches_the_recognizer(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
        capture_data: bytes,
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, capture_data))

        assert recognizer.calls == []


class TestHeaderValidationRunsFirstAndIsFinal:
    """OCR is never attempted as a repair for an image the parser refused."""

    def test_a_malformed_png_fails_before_recognition(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, images.png(crc=0xDEADBEEF)))

        assert recognizer.calls == []

    def test_a_jpeg_declared_as_png_fails_before_recognition(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        """A contradiction is refused; it is not re-examined to find what it is."""
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, JPEG_BYTES, mime_type=PNG_MIME_TYPE))

        assert recognizer.calls == []

    def test_a_png_declared_as_jpeg_fails_before_recognition(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, PNG_BYTES, mime_type=JPEG_MIME_TYPE))

        assert recognizer.calls == []

    def test_a_refusal_is_the_same_type_the_default_build_raises(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """So the delivery layer answers 422 and the capture durably fails, as in 4A."""
        broken = images.png(crc=0xDEADBEEF)
        default = ImageProcessor(store)

        with pytest.raises(ProcessingInputError):
            default.process(image_capture(store, broken))
        with pytest.raises(ProcessingInputError):
            ocr_processor.process(image_capture(store, broken))


class TestItReusesThePhase4AParsers:
    def test_the_structural_metadata_is_what_the_default_build_records(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Not merely equivalent: the same mapping, from the same parsers."""
        data = images.png(width=640, height=480)
        default = ImageProcessor(store).process(image_capture(store, data))
        enriched = ocr_processor.process(image_capture(store, data))

        assert enriched.metadata[IMAGE_METADATA_KEY] == default.metadata[IMAGE_METADATA_KEY]

    def test_png_dimensions_come_from_the_header(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store, images.png(width=21, height=13)))

        assert content.metadata[IMAGE_METADATA_KEY] == {
            ENCODED_FORMAT_KEY: "png",
            ENCODED_WIDTH_KEY: 21,
            ENCODED_HEIGHT_KEY: 13,
        }

    def test_jpeg_dimensions_come_from_the_header(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(
            image_capture(store, images.jpeg(width=33, height=11), mime_type=JPEG_MIME_TYPE)
        )

        assert content.metadata[IMAGE_METADATA_KEY] == {
            ENCODED_FORMAT_KEY: "jpeg",
            ENCODED_WIDTH_KEY: 33,
            ENCODED_HEIGHT_KEY: 11,
        }

    def test_the_header_dimensions_are_handed_to_the_recognizer(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        """So an adapter can enforce a pixel budget without re-parsing the header."""
        ocr_processor.process(image_capture(store, images.png(width=800, height=600)))

        assert (recognizer.width, recognizer.height) == (800, 600)

    def test_the_verified_mime_type_is_handed_to_the_recognizer(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        ocr_processor.process(image_capture(store, JPEG_BYTES, mime_type=JPEG_MIME_TYPE))

        assert recognizer.mime_type == JPEG_MIME_TYPE


class TestTheRecognizerGetsItsOwnStream:
    def test_the_original_is_opened_twice(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Once to read the header, once to recognize. Never a rewind."""
        capture = image_capture(store)
        store.accesses.clear()

        ocr_processor.process(capture)

        assert store.accesses == ["open", "open"]

    def test_the_recognizer_receives_the_whole_original_from_byte_zero(
        self,
        ocr_processor: ImageOcrProcessor,
        store: InMemoryRawObjectStore,
        recognizer: FakeImageOcr,
    ) -> None:
        """A handle the parser had already consumed would arrive short."""
        ocr_processor.process(image_capture(store))

        assert recognizer.received_bytes == PNG_BYTES

    def test_it_never_seeks(self, store: InMemoryRawObjectStore, recognizer: FakeImageOcr) -> None:
        """A non-seekable backend is a non-event rather than a special case.

        The store double hands over a stream whose ``seek`` and ``tell`` raise,
        so anything on this path that tried to rewind would fail loudly here.
        """
        store.forbid_seeking()
        capture = image_capture(store)

        content = ImageOcrProcessor(store, recognizer).process(capture)

        assert content.type is ContentType.IMAGE
        assert recognizer.received_bytes == PNG_BYTES


class TestRecognizedText:
    def test_one_segment_for_the_whole_image(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert len(content.segments) == 1

    def test_the_segment_is_an_ocr_segment(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].type is SegmentType.OCR

    def test_the_text_is_stored_exactly_as_the_engine_returned_it(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``strip`` decides blankness and never rewrites what is kept."""
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].text == RAGGED_TEXT

    def test_the_segment_is_at_position_zero(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].position == 0

    def test_the_segment_has_no_spatial_location(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No page, no box, no region — and none invented to fill the field.

        ``SpatialLocation`` refuses to be constructed locating nothing, so
        omitting it is the only honest option and this is what that looks like.
        """
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].spatial is None

    def test_the_segment_has_no_temporal_location(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].temporal is None

    def test_the_provenance_says_ocr(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].provenance.source_type is ProvenanceSourceType.OCR

    def test_the_provenance_points_at_the_original_asset(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Not a dangling id: the pixels the engine read are that asset, in full."""
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].provenance.asset_id == content.assets[0].id

    def test_the_provenance_names_this_processor(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))
        provenance = content.segments[0].provenance

        assert (provenance.processor, provenance.processor_version) == ("image-ocr", "0.1")

    def test_the_provenance_names_this_capture(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = image_capture(store)

        content = ocr_processor.process(capture)

        assert content.segments[0].provenance.capture_id == capture.id

    def test_the_segment_metadata_names_only_the_engine(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The full settings block lives once, on the object, not on every segment."""
        content = ocr_processor.process(image_capture(store))

        assert content.segments[0].metadata == {
            IMAGE_OCR_METADATA_KEY: {ENGINE_KEY: "fake-ocr", ENGINE_VERSION_KEY: "9.9.9"}
        }

    def test_nothing_about_confidence_language_or_layout_is_invented(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))
        recorded = content.model_dump_json()

        for forbidden in ("confidence", "detected_language", "orientation", "bbox", "rasterizer"):
            assert forbidden not in recorded

    def test_the_run_is_complete(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.processing[0].status is ProcessingStatus.COMPLETE
        assert content.processing[0].processor == "image-ocr"


class TestTheCanonicalObjectIsPhase4As:
    """Enrichment adds a segment. It changes nothing else about the image."""

    def test_the_type_is_image(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.type is ContentType.IMAGE

    def test_there_is_exactly_one_asset_and_it_is_the_original(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No thumbnail, no normalized copy, no raster."""
        content = ocr_processor.process(image_capture(store))

        assert len(content.assets) == 1
        assert content.assets[0].role is AssetRole.ORIGINAL

    def test_the_asset_carries_the_submitted_bytes_identity(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = image_capture(store)
        assert capture.raw_object is not None

        content = ocr_processor.process(capture)

        assert content.assets[0].ref == capture.raw_object.ref
        assert content.assets[0].sha256 == capture.raw_object.sha256
        assert content.assets[0].mime_type == PNG_MIME_TYPE

    def test_the_original_reference_points_at_that_asset(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store))

        assert content.original.asset_id == content.assets[0].id
        assert content.original.mime_type == PNG_MIME_TYPE

    def test_the_submitted_title_is_kept_exactly(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = ocr_processor.process(image_capture(store, title=images.SUBMITTED_TITLE))

        assert content.title == images.SUBMITTED_TITLE

    def test_no_title_is_read_out_of_the_recognized_text(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A title from OCR is a guess dressed as a fact."""
        content = ocr_processor.process(image_capture(store))

        assert content.title is None

    def test_the_source_is_carried_from_the_capture(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = image_capture(store)

        content = ocr_processor.process(capture)

        assert content.source.capture_id == capture.id
        assert content.source.provider == capture.source.provider
        assert content.source.url == capture.source.url

    def test_everything_but_the_segments_and_ocr_metadata_matches_the_default_build(
        self, ocr_processor: ImageOcrProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The strongest form of "enabling OCR changes nothing else"."""
        capture = image_capture(store, title=images.SUBMITTED_TITLE)
        default = ImageProcessor(store).process(capture)
        enriched = ocr_processor.process(capture)

        assert enriched.type == default.type
        assert enriched.title == default.title
        assert enriched.source == default.source
        assert enriched.original.mime_type == default.original.mime_type
        assert enriched.original.sha256 == default.original.sha256
        assert enriched.metadata[IMAGE_METADATA_KEY] == default.metadata[IMAGE_METADATA_KEY]
        assert [a.ref for a in enriched.assets] == [a.ref for a in default.assets]
        assert [a.sha256 for a in enriched.assets] == [a.sha256 for a in default.assets]


class TestTheEngineRanAndReadNothing:
    @pytest.mark.parametrize("answer", ["", "   ", "\n\n\t "], ids=["empty", "spaces", "newlines"])
    def test_no_segment_is_emitted(self, store: InMemoryRawObjectStore, answer: str) -> None:
        content = ImageOcrProcessor(store, FakeImageOcr(text=answer)).process(image_capture(store))

        assert content.segments == []

    @pytest.mark.parametrize("answer", ["", "   "], ids=["empty", "spaces"])
    def test_the_capture_still_completes(self, store: InMemoryRawObjectStore, answer: str) -> None:
        content = ImageOcrProcessor(store, FakeImageOcr(text=answer)).process(image_capture(store))

        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_the_image_is_still_canonical_content(self, store: InMemoryRawObjectStore) -> None:
        capture = image_capture(store)

        content = ImageOcrProcessor(store, FakeImageOcr(text="")).process(capture)

        assert content.type is ContentType.IMAGE
        assert len(content.assets) == 1
        assert structural_metadata(content)[ENCODED_FORMAT_KEY] == "png"

    def test_the_metadata_says_the_engine_was_invoked(self, store: InMemoryRawObjectStore) -> None:
        """This is what makes "ran and read nothing" distinguishable from "never ran"."""
        content = ImageOcrProcessor(store, FakeImageOcr(text="")).process(image_capture(store))

        assert image_metadata(content)[ENGINE_INVOKED_KEY] is True

    def test_the_engine_identity_and_settings_are_recorded(
        self, store: InMemoryRawObjectStore
    ) -> None:
        content = ImageOcrProcessor(store, FakeImageOcr(text="")).process(image_capture(store))
        recorded = image_metadata(content)

        assert recorded[ENGINE_KEY] == "fake-ocr"
        assert recorded[ENGINE_VERSION_KEY] == "9.9.9"
        assert recorded[SETTINGS_KEY] == {"languages": "fake+fake"}

    def test_no_returned_text_flag_is_recorded(self, store: InMemoryRawObjectStore) -> None:
        """Derivable from the segments, so a second copy is a second thing to be wrong."""
        content = ImageOcrProcessor(store, FakeImageOcr(text="")).process(image_capture(store))

        assert "returned_text" not in image_metadata(content)

    def test_the_image_is_never_called_blank(self, store: InMemoryRawObjectStore) -> None:
        """An empty result is evidence about the recognizer, not about the picture."""
        content = ImageOcrProcessor(store, FakeImageOcr(text="")).process(image_capture(store))

        assert "blank" not in content.model_dump_json()

    def test_no_placeholder_segment_of_any_kind_appears(
        self, store: InMemoryRawObjectStore
    ) -> None:
        content = ImageOcrProcessor(store, FakeImageOcr(text="  ")).process(image_capture(store))

        assert content.segments == []
        assert "visual" not in content.model_dump_json()


class TestAResourcePolicySkip:
    """Refuse the enrichment, never the artifact."""

    @staticmethod
    def refusing(reason: str, limit: int) -> FakeImageOcr:
        return FakeImageOcr(
            raises=ImageOcrLimitExceeded("refused", reason=reason, limit=limit)  # type: ignore[arg-type]
        )

    def test_a_pixel_limit_skip_still_completes(self, store: InMemoryRawObjectStore) -> None:
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000))

        content = processor.process(image_capture(store))

        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_a_pixel_limit_skip_carries_no_segments(self, store: InMemoryRawObjectStore) -> None:
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000))

        assert processor.process(image_capture(store)).segments == []

    def test_a_pixel_limit_skip_records_exactly_three_facts(
        self, store: InMemoryRawObjectStore
    ) -> None:
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000))

        content = processor.process(image_capture(store))

        assert image_metadata(content) == {
            ENGINE_INVOKED_KEY: False,
            SKIPPED_REASON_KEY: ENCODED_PIXEL_LIMIT,
            MAX_ENCODED_PIXELS_KEY: 20_000_000,
        }

    def test_a_byte_limit_skip_records_exactly_three_facts(
        self, store: InMemoryRawObjectStore
    ) -> None:
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_BYTE_LIMIT, 67_108_864))

        content = processor.process(image_capture(store))

        assert image_metadata(content) == {
            ENGINE_INVOKED_KEY: False,
            SKIPPED_REASON_KEY: ENCODED_BYTE_LIMIT,
            MAX_ENCODED_BYTES_KEY: 67_108_864,
        }

    @pytest.mark.parametrize(
        ("reason", "limit"),
        [(ENCODED_PIXEL_LIMIT, 20_000_000), (ENCODED_BYTE_LIMIT, 67_108_864)],
    )
    def test_no_run_facts_are_recorded_when_nothing_ran(
        self, store: InMemoryRawObjectStore, reason: str, limit: int
    ) -> None:
        """Nothing ran, so there is no engine to name and no settings to describe."""
        processor = ImageOcrProcessor(store, self.refusing(reason, limit))

        recorded = image_metadata(processor.process(image_capture(store)))

        assert ENGINE_KEY not in recorded
        assert ENGINE_VERSION_KEY not in recorded
        assert SETTINGS_KEY not in recorded

    def test_the_skipped_image_keeps_every_phase_4a_fact(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = image_capture(store, images.png(width=99, height=44), title="Harbour")
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_PIXEL_LIMIT, 1))

        content = processor.process(capture)

        assert content.type is ContentType.IMAGE
        assert content.title == "Harbour"
        assert content.metadata[IMAGE_METADATA_KEY] == {
            ENCODED_FORMAT_KEY: "png",
            ENCODED_WIDTH_KEY: 99,
            ENCODED_HEIGHT_KEY: 44,
        }
        assert len(content.assets) == 1

    def test_the_limit_signal_never_escapes_the_processor(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Orchestration must never see it: it is not a failure of anything."""
        processor = ImageOcrProcessor(store, self.refusing(ENCODED_BYTE_LIMIT, 5))

        processor.process(image_capture(store))  # does not raise

    def test_a_skip_is_distinguishable_from_a_no_text_recognition(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Both carry zero segments, and the metadata is what tells them apart."""
        skipped = ImageOcrProcessor(store, self.refusing(ENCODED_PIXEL_LIMIT, 1)).process(
            image_capture(store)
        )
        read_nothing = ImageOcrProcessor(store, FakeImageOcr(text="")).process(image_capture(store))

        assert skipped.segments == read_nothing.segments == []
        assert image_metadata(skipped)[ENGINE_INVOKED_KEY] is False
        assert image_metadata(read_nothing)[ENGINE_INVOKED_KEY] is True


class TestAnExecutionFailureIsNotASkip:
    def test_it_propagates_untouched(self, store: InMemoryRawObjectStore) -> None:
        failure = ImageOcrExecutionError("the engine exited with status 1")
        processor = ImageOcrProcessor(store, FakeImageOcr(raises=failure))

        with pytest.raises(ImageOcrExecutionError) as raised:
            processor.process(image_capture(store))

        assert raised.value is failure

    def test_it_is_never_converted_into_a_successful_skip(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """ "The engine crashed" and "the engine found no text" are different facts."""
        processor = ImageOcrProcessor(store, FakeImageOcr(raises=ImageOcrExecutionError("gone")))

        with pytest.raises(ImageOcrExecutionError):
            processor.process(image_capture(store))

    def test_no_content_object_is_returned(self, store: InMemoryRawObjectStore) -> None:
        processor = ImageOcrProcessor(store, FakeImageOcr(raises=ImageOcrExecutionError("gone")))
        capture = image_capture(store)

        with pytest.raises(ImageOcrExecutionError):
            processor.process(capture)

    def test_it_is_not_a_processing_error_so_the_capture_stays_non_terminal(
        self, store: InMemoryRawObjectStore
    ) -> None:
        processor = ImageOcrProcessor(store, FakeImageOcr(raises=ImageOcrExecutionError("gone")))

        with pytest.raises(ImageOcrExecutionError) as raised:
            processor.process(image_capture(store))

        assert not isinstance(raised.value, ProcessingInputError)


class TestAMalformedAdapterAnswer:
    def test_a_blank_engine_name_becomes_an_execution_failure(
        self, store: InMemoryRawObjectStore
    ) -> None:
        processor = ImageOcrProcessor(store, FakeImageOcr(text="words", engine=""))

        with pytest.raises(ImageOcrExecutionError):
            processor.process(image_capture(store))

    def test_a_blank_engine_version_becomes_an_execution_failure(
        self, store: InMemoryRawObjectStore
    ) -> None:
        processor = ImageOcrProcessor(store, FakeImageOcr(text="words", engine_version=""))

        with pytest.raises(ImageOcrExecutionError):
            processor.process(image_capture(store))

    def test_it_never_becomes_a_blank_recognition(self, store: InMemoryRawObjectStore) -> None:
        """A bug in an adapter must not be recorded as an image with no words in it."""
        processor = ImageOcrProcessor(store, FakeImageOcr(text="", engine=""))

        with pytest.raises(ImageOcrExecutionError):
            processor.process(image_capture(store))

    def test_it_never_becomes_an_input_verdict(self, store: InMemoryRawObjectStore) -> None:
        processor = ImageOcrProcessor(store, FakeImageOcr(text="words", engine="  "))

        with pytest.raises(ImageOcrExecutionError) as raised:
            processor.process(image_capture(store))

        assert not isinstance(raised.value, ProcessingInputError)
