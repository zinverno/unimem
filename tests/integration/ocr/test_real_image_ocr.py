"""Direct-image OCR against the real engine, on a machine without the extra.

Everything else about image recognition is tested with a fake, which proves the
policy. This file proves the *machine*: a real PNG, a real Tesseract, real words
coming back, and a real HTTP round trip through an ``--image-ocr`` server
process.

**No rasterizer and no imaging library is imported here, and that is the point.**
The guard is :func:`~tests.ocr_support.require_engine_for_image_ocr`, which asks
for the executable and the two language packs and nothing else — so this suite
runs to completion in the CI job that deliberately installs no optional extra.
A fixture rendered with Pillow would make that circular, which is why
:func:`tests.images.render_text_png` draws its own PNG out of ``zlib``.

Locally these skip when Tesseract is absent. Wherever
``UNIMEM_REQUIRE_IMAGE_OCR_INTEGRATION`` is set, every one of those reasons is a
failure instead.
"""

import io
import json
from pathlib import Path
from typing import Any, Final

import pytest

from core.contracts import (
    SCHEMA_VERSION,
    CapturePayloadType,
    CaptureRecord,
    ContentType,
    SegmentType,
)
from core.processing import ImageOcrProcessor, read_png_header
from core.processing.image_recognition import validate_image_ocr_result
from core.storage import LocalRawObjectStore
from tests import images, ocr_support
from tests.unit.processing.builders import make_capture

ocr_support.require_engine_for_image_ocr()

from unimem_ocr import build_tesseract_image_ocr  # noqa: E402 - guarded above

CAPTURE_ID: Final = "cap_real_image_ocr_01"
CAPTURED_AT: Final = "2026-05-06T07:08:09+00:00"
PNG_MIME: Final = "image/png"

#: A genuine greyscale PNG with a distinctive word drawn on it, built from
#: ``struct`` and ``zlib`` and nothing else.
READABLE_PNG: Final = images.render_text_png()

#: The fixture's own encoded dimensions, read with this build's parser rather
#: than written down. They are what the processor would pass to the port, so a
#: test that made them up would be exercising a call the system never makes.
READABLE_HEADER: Final = read_png_header(io.BytesIO(READABLE_PNG))


def recognize(recognizer: Any, data: bytes = READABLE_PNG) -> Any:
    """Hand one image to the real adapter exactly as the processor would."""
    header = read_png_header(io.BytesIO(data))
    return recognizer.recognize_image(
        io.BytesIO(data),
        mime_type=PNG_MIME,
        encoded_width=header.width,
        encoded_height=header.height,
    )


@pytest.fixture(scope="module")
def recognizer() -> Any:
    """The real adapter, over the real engine, built through the real factory."""
    return build_tesseract_image_ocr()


class TestTheFixtureIsHonest:
    """If the fixture were wrong, every assertion below would be about nothing."""

    def test_it_is_a_structurally_valid_png_this_build_reads(self) -> None:
        assert READABLE_HEADER.encoded_format == "png"
        assert READABLE_HEADER.width > 0
        assert READABLE_HEADER.height > 0

    def test_it_is_deterministic(self) -> None:
        """Built from ``struct`` and ``zlib``, so the same call gives the same bytes.

        Which is what lets the CI job that installs no imaging library render it
        at test time rather than needing a committed binary.
        """
        assert images.render_text_png() == READABLE_PNG

    def test_it_is_large_enough_for_an_engine_to_read(self) -> None:
        """Small synthetic text recognizes by luck; this is well clear of that."""
        assert READABLE_HEADER.width >= 200
        assert READABLE_HEADER.height >= 100


class TestTheEngineReallyReads:
    def test_it_recognizes_the_token_that_was_drawn(self, recognizer: Any) -> None:
        """Asserted on one distinctive token, not on the whole output.

        Tesseract versions differ in how they space and break what they read, so
        matching the entire string would make this a test of the installed
        version. The token is a word the engine could not plausibly invent.
        """
        result = recognize(recognizer)

        assert images.NATIVE_TOKEN in result.text.upper().replace(" ", "")

    def test_the_result_passes_the_ports_own_validation(self, recognizer: Any) -> None:
        result = recognize(recognizer)

        validate_image_ocr_result(result)

    def test_it_reports_the_engine_that_actually_ran(self, recognizer: Any) -> None:
        result = recognize(recognizer)

        assert result.engine == "tesseract"
        assert result.engine_version

    def test_the_recorded_settings_describe_this_run(self, recognizer: Any) -> None:
        result = recognize(recognizer)

        assert result.settings["languages"] == "eng+rus"
        assert result.settings["dpi_supplied"] is False
        assert result.settings["input"] == "original_encoded_bytes"

    def test_an_image_with_no_words_recognizes_to_nothing_rather_than_failing(
        self, recognizer: Any
    ) -> None:
        """The case ADR-019 fixed: a wordless picture is not an error."""
        result = recognize(recognizer, images.render_text_png(" "))

        assert result.text.strip() == ""


class TestTheProcessorOverTheRealEngine:
    """The canonical policy, driven by the real adapter rather than a fake.

    Deliberately *not* the place to assert that no rasterizer was loaded. This
    module shares an interpreter with the PDF suites in a full run, so
    ``sys.modules`` there describes the session rather than this code path, and an
    assertion about it would pass or fail on test ordering. The dependency claim
    is checked where it can be true: in the subprocess probes beside the adapter
    and the composition root, and in the CI job that installs no extra at all.
    """

    @pytest.fixture
    def capture(self, tmp_path: Path) -> tuple[LocalRawObjectStore, CaptureRecord]:
        store = LocalRawObjectStore(tmp_path / "raw")
        raw_object = store.store_bytes(READABLE_PNG, mime_type=PNG_MIME)
        return store, make_capture(
            id=CAPTURE_ID,
            payload_type=CapturePayloadType.IMAGE,
            raw_object=raw_object,
        )

    def test_it_produces_one_ocr_segment(
        self, recognizer: Any, capture: tuple[LocalRawObjectStore, CaptureRecord]
    ) -> None:
        store, record = capture

        content = ImageOcrProcessor(store, recognizer).process(record)

        assert len(content.segments) == 1
        assert content.segments[0].type is SegmentType.OCR

    def test_the_segment_carries_the_recognized_token(
        self, recognizer: Any, capture: tuple[LocalRawObjectStore, CaptureRecord]
    ) -> None:
        store, record = capture

        content = ImageOcrProcessor(store, recognizer).process(record)

        text = content.segments[0].text or ""
        assert images.NATIVE_TOKEN in text.upper().replace(" ", "")

    def test_the_object_is_a_complete_image(
        self, recognizer: Any, capture: tuple[LocalRawObjectStore, CaptureRecord]
    ) -> None:
        store, record = capture

        content = ImageOcrProcessor(store, recognizer).process(record)

        assert content.type is ContentType.IMAGE
        assert content.processing[0].processor == "image-ocr"

    def test_the_segment_names_the_engine_that_ran(
        self, recognizer: Any, capture: tuple[LocalRawObjectStore, CaptureRecord]
    ) -> None:
        store, record = capture

        content = ImageOcrProcessor(store, recognizer).process(record)

        recorded = content.segments[0].metadata["image_ocr"]
        assert isinstance(recorded, dict)
        assert recorded["engine"] == "tesseract"


class TestOverRealHttp:
    """One end-to-end pass through a real server process with ``--image-ocr``."""

    def test_an_image_captures_and_reads_back_with_its_ocr_segment(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        image_path = tmp_path / "harbour.png"
        image_path.write_bytes(READABLE_PNG)

        with ocr_support.serve_process(data_dir, pdf_ocr=False, image_ocr=True) as base:
            file_ref = _upload(base, image_path)
            status = _submit(base, file_ref)
            content = ocr_support.read_json(f"{base}/v1/captures/{CAPTURE_ID}/content")

        assert status == 201
        assert content["type"] == "image"
        assert content["processing"][0]["processor"] == "image-ocr"
        texts = [segment["text"].upper().replace(" ", "") for segment in content["segments"]]
        assert any(images.NATIVE_TOKEN in text for text in texts)

    def test_the_server_starts_without_the_optional_extra(self, tmp_path: Path) -> None:
        """``--image-ocr`` on a machine with no rasterizer: the whole point."""
        with ocr_support.serve_process(tmp_path / "data", pdf_ocr=False, image_ocr=True) as base:
            assert ocr_support.read_json(f"{base}/health")["status"] == "ok"


def _upload(base: str, path: Path) -> str:
    """POST one multipart upload with no client library, and return the file_ref."""
    import urllib.request
    import uuid

    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {PNG_MIME}\r\n\r\n"
        ).encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )

    request = urllib.request.Request(
        f"{base}/v1/uploads",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        parsed: dict[str, Any] = json.loads(response.read())
    return str(parsed["file_ref"])


def _submit(base: str, file_ref: str) -> int:
    import urllib.request

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "image", "mime_type": PNG_MIME, "file_ref": file_ref},
        "context": {"captured_at": CAPTURED_AT},
    }
    request = urllib.request.Request(
        f"{base}/v1/captures",
        data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return int(response.status)
