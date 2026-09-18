"""A real image, through the real application, over real HTTP, on a real directory.

Nothing is faked. ``build_local_app`` wires ``LocalRawObjectStore``,
``SqliteCaptureRecordStore``, ``SqliteContentObjectStore``, ``CaptureIntake``,
every registered processor including ``ImageProcessor``, ``ProcessorRouter`` and
``ProcessingOrchestrator`` against a temporary directory, and every assertion is
made against what is on disk or what came back over the wire::

    PNG or JPEG bytes
        -> POST /v1/uploads          (multipart)   -> file_ref
        -> POST /v1/captures         (the canonical envelope, naming that ref)
        -> 201, complete
        -> GET  /v1/captures/{id}                  -> COMPLETE
        -> GET  /v1/captures/{id}/content          -> an image ContentObject
                                                      with no segments at all

The upload route is the one ADR-016 built, unchanged and still format-blind: it
is handed an image here and needed no edit to accept one. The claim this file
exists to make is the one on the last line — ``segments == []`` on a capture the
server reports ``complete`` — and it is checked against the JSON that actually
crossed the wire rather than against an object built in-process.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.contracts.enums import AssetRole, CapturePayloadType
from tests import images
from unimem_api import RAW_DIRNAME, build_local_app

CAPTURE_ID = "cap_http_img_01"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"
CAPTURED_AT = "2026-01-02T03:04:05+00:00"

#: The image every test uses unless it says otherwise. Its width and height
#: differ, so a transposition fails rather than passing by symmetry.
IMAGE = images.png()
IMAGE_DIGEST = hashlib.sha256(IMAGE).hexdigest()

JPEG_IMAGE = images.jpeg()
JPEG_DIGEST = hashlib.sha256(JPEG_IMAGE).hexdigest()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(data_dir)) as running:
        yield running


def raw_path(data_dir: Path, digest: str) -> Path:
    """Where ``LocalRawObjectStore`` keeps one object, addressed by its digest."""
    return data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest


def stage(
    client: TestClient,
    data: bytes = IMAGE,
    *,
    filename: str = "harbour.png",
    mime_type: str = PNG_MIME,
) -> str:
    """Upload bytes through the unchanged staging route and return the file_ref."""
    response = client.post("/v1/uploads", files={"file": (filename, data, mime_type)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def image_envelope(
    file_ref: str, *, payload: dict[str, object] | None = None, **overrides: object
) -> dict[str, object]:
    """The canonical envelope a client assembles, as a JSON-ready dictionary.

    A dictionary rather than a dumped model, for the same reason every other HTTP
    test here does it: a real client posts JSON it wrote itself, and this has to
    validate as a document that was never a Python object. There is no
    image-specific request contract, and this is what that means in practice —
    the body is ``CaptureEnvelope`` with ``payload.type`` set to ``image``.
    """
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "image", "mime_type": PNG_MIME, "file_ref": file_ref} | (payload or {}),
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


def record_of(client: TestClient, capture_id: str = CAPTURE_ID) -> CaptureRecord:
    return CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())


def content_of(client: TestClient, capture_id: str = CAPTURE_ID) -> ContentObject:
    return ContentObject.model_validate(client.get(f"/v1/captures/{capture_id}/content").json())


class TestTheRealVerticalSlice:
    """The whole flow, once, and every claim this PR makes about it."""

    @pytest.fixture(autouse=True)
    def submitted(self, client: TestClient) -> str:
        file_ref = stage(client)
        response = client.post("/v1/captures", json=image_envelope(file_ref))
        assert response.status_code == 201, response.text
        return file_ref

    def test_the_upload_returned_a_content_addressed_reference(self, submitted: str) -> None:
        assert submitted == f"sha256:{IMAGE_DIGEST}"

    def test_the_upload_answers_200(self, client: TestClient) -> None:
        """The store deduplicates by content and cannot honestly claim "created"."""
        response = client.post("/v1/uploads", files={"file": ("a.png", IMAGE, PNG_MIME)})

        assert response.status_code == 200

    def test_the_capture_response_says_complete(self, client: TestClient) -> None:
        file_ref = stage(client)

        body = client.post(
            "/v1/captures", json=image_envelope(file_ref, id="cap_http_img_02")
        ).json()

        assert body["status"] == "complete"
        assert body["content_id"]

    def test_the_capture_record_is_complete(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_capture_record_is_an_image(self, client: TestClient) -> None:
        assert record_of(client).payload_type is CapturePayloadType.IMAGE

    def test_the_capture_record_points_at_the_staged_object(
        self, client: TestClient, submitted: str
    ) -> None:
        raw_object = record_of(client).raw_object

        assert raw_object is not None
        assert raw_object.ref == submitted
        assert raw_object.sha256 == IMAGE_DIGEST
        assert raw_object.mime_type == PNG_MIME

    def test_the_content_object_is_an_image(self, client: TestClient) -> None:
        assert content_of(client).type is ContentType.IMAGE

    def test_the_content_object_carries_no_segments(self, client: TestClient) -> None:
        """The claim the phase turns on, asserted against what crossed the wire."""
        assert content_of(client).segments == []

    def test_the_raw_json_carries_an_empty_segment_list(self, client: TestClient) -> None:
        """Not absent, not null, not a placeholder object: an empty list."""
        body = json.loads(client.get(f"/v1/captures/{CAPTURE_ID}/content").text)

        assert body["segments"] == []

    def test_nothing_was_derived(self, client: TestClient) -> None:
        derived = content_of(client).derived

        assert (derived.summary, derived.topics, derived.entities) == (None, [], [])

    def test_there_is_one_original_asset(self, client: TestClient, submitted: str) -> None:
        assets = content_of(client).assets

        assert len(assets) == 1
        assert assets[0].role is AssetRole.ORIGINAL
        assert assets[0].mime_type == PNG_MIME
        assert assets[0].ref == submitted
        assert assets[0].sha256 == IMAGE_DIGEST

    def test_the_original_reference_resolves_to_that_asset(self, client: TestClient) -> None:
        content = content_of(client)

        assert content.original.asset_id == content.assets[0].id
        assert content.original.sha256 == IMAGE_DIGEST

    def test_the_metadata_is_exactly_the_required_vocabulary(self, client: TestClient) -> None:
        assert content_of(client).metadata == {
            "image": {
                "encoded_format": "png",
                "encoded_width": images.DEFAULT_WIDTH,
                "encoded_height": images.DEFAULT_HEIGHT,
            }
        }

    def test_the_processor_is_recorded(self, client: TestClient) -> None:
        processing = content_of(client).processing

        assert len(processing) == 1
        assert (processing[0].processor, processing[0].processor_version) == ("image", "0.1")
        assert processing[0].status.value == "complete"

    def test_the_documents_carry_the_current_canonical_schema(self, client: TestClient) -> None:
        """Image ingestion needed no contract change of its own.

        It asserted ``0.2`` when Phase 4 shipped and asserts the current version
        now: the canonical set advanced in Phase 5A for audio, which is not a
        fact about images, and pinning the literal here would have turned an
        unrelated version bump into an image-ingestion failure.
        """
        assert content_of(client).schema_version == SCHEMA_VERSION
        assert record_of(client).schema_version == SCHEMA_VERSION

    def test_the_stored_original_is_the_uploaded_image_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, IMAGE_DIGEST).read_bytes() == IMAGE

    def test_exactly_one_raw_object_exists(self, data_dir: Path) -> None:
        """Nothing was re-encoded, thumbnailed, or written a second time."""
        stored = list((data_dir / RAW_DIRNAME).rglob("*"))

        assert len([path for path in stored if path.is_file()]) == 1


class TestAJpegTakesTheSameRoute:
    @pytest.fixture(autouse=True)
    def submitted(self, client: TestClient) -> str:
        file_ref = stage(client, JPEG_IMAGE, filename="harbour.jpg", mime_type=JPEG_MIME)
        response = client.post(
            "/v1/captures",
            json=image_envelope(file_ref, payload={"mime_type": JPEG_MIME}),
        )
        assert response.status_code == 201, response.text
        return file_ref

    def test_the_capture_is_complete(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_content_object_carries_no_segments(self, client: TestClient) -> None:
        assert content_of(client).segments == []

    def test_the_metadata_names_the_jpeg_format(self, client: TestClient) -> None:
        assert content_of(client).metadata == {
            "image": {
                "encoded_format": "jpeg",
                "encoded_width": images.DEFAULT_WIDTH,
                "encoded_height": images.DEFAULT_HEIGHT,
            }
        }

    def test_the_original_identity_is_preserved(self, client: TestClient, submitted: str) -> None:
        assert content_of(client).assets[0].sha256 == JPEG_DIGEST
        assert content_of(client).assets[0].ref == submitted


class TestTitleSemantics:
    def test_the_submitted_title_reaches_the_content_object(self, client: TestClient) -> None:
        file_ref = stage(client)
        client.post(
            "/v1/captures",
            json=image_envelope(file_ref, payload={"title": images.SUBMITTED_TITLE}),
        )

        assert content_of(client).title == images.SUBMITTED_TITLE

    def test_the_submitted_title_stays_on_the_capture_record_too(self, client: TestClient) -> None:
        file_ref = stage(client)
        client.post(
            "/v1/captures",
            json=image_envelope(file_ref, payload={"title": images.SUBMITTED_TITLE}),
        )

        assert record_of(client).title == images.SUBMITTED_TITLE

    def test_no_title_is_invented(self, client: TestClient) -> None:
        """Not from the filename, not from the digest, not from the image."""
        file_ref = stage(client, filename="IMG_20260102_holiday_beach.png")
        client.post("/v1/captures", json=image_envelope(file_ref))

        content = content_of(client)

        assert content.title is None
        assert content.derived.summary is None

    def test_the_uploaded_filename_reaches_nothing(self, client: TestClient) -> None:
        file_ref = stage(client, filename="IMG_20260102_holiday_beach.png")
        client.post("/v1/captures", json=image_envelope(file_ref))

        served = client.get(f"/v1/captures/{CAPTURE_ID}/content").text

        assert "IMG_20260102" not in served
        assert "holiday_beach" not in served


class TestImagesThatFailProcessing:
    """Intake accepts the declaration; the processor finds the bytes disagree."""

    def test_a_malformed_image_is_a_safe_422(self, client: TestClient) -> None:
        file_ref = stage(client, b"this is not a PNG at all, not even close")

        response = client.post("/v1/captures", json=image_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_a_malformed_image_leaves_a_truthful_failed_capture(self, client: TestClient) -> None:
        file_ref = stage(client, b"this is not a PNG at all, not even close")
        client.post("/v1/captures", json=image_envelope(file_ref))

        record = record_of(client)

        assert record.status is CaptureStatus.FAILED
        assert record.error is not None

    def test_a_malformed_image_produces_no_content_object(self, client: TestClient) -> None:
        file_ref = stage(client, b"this is not a PNG at all, not even close")
        client.post("/v1/captures", json=image_envelope(file_ref))

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_png_bytes_declared_jpeg_fail_the_same_way(self, client: TestClient) -> None:
        """The declaration routes; the header is then checked against it."""
        file_ref = stage(client)

        response = client.post(
            "/v1/captures", json=image_envelope(file_ref, payload={"mime_type": JPEG_MIME})
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert record_of(client).status is CaptureStatus.FAILED
        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_jpeg_bytes_declared_png_fail_the_same_way(self, client: TestClient) -> None:
        file_ref = stage(client, JPEG_IMAGE, mime_type=JPEG_MIME)

        response = client.post("/v1/captures", json=image_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert record_of(client).status is CaptureStatus.FAILED

    def test_a_truncated_png_fails(self, client: TestClient) -> None:
        file_ref = stage(client, images.png()[:29])

        response = client.post("/v1/captures", json=image_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_the_declaration_is_not_corrected_on_the_record(self, client: TestClient) -> None:
        """A wrong MIME type is a refusal, never something quietly fixed."""
        file_ref = stage(client)
        client.post("/v1/captures", json=image_envelope(file_ref, payload={"mime_type": JPEG_MIME}))

        raw_object = record_of(client).raw_object

        assert raw_object is not None
        assert raw_object.mime_type == JPEG_MIME

    def test_a_failure_leaks_no_stack_or_path(self, client: TestClient) -> None:
        file_ref = stage(client, b"not an image")

        body = client.post("/v1/captures", json=image_envelope(file_ref)).text

        assert "Traceback" not in body
        assert "/tmp" not in body
        assert "site-packages" not in body

    def test_the_original_bytes_survive_a_processing_failure(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """Nothing submitted is lost by refusing; a later build can read them."""
        junk = b"this is not a PNG at all, not even close"
        client.post("/v1/captures", json=image_envelope(stage(client, junk)))

        assert raw_path(data_dir, hashlib.sha256(junk).hexdigest()).read_bytes() == junk


class TestFormatsThisBuildDeclines:
    @pytest.mark.parametrize("mime_type", ["image/webp", "image/gif", "image/svg+xml"])
    def test_a_deferred_format_is_refused_by_intake(
        self, client: TestClient, mime_type: str
    ) -> None:
        file_ref = stage(client)

        response = client.post(
            "/v1/captures", json=image_envelope(file_ref, payload={"mime_type": mime_type})
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_that_refusal_leaves_no_capture_record(self, client: TestClient) -> None:
        file_ref = stage(client)
        client.post(
            "/v1/captures", json=image_envelope(file_ref, payload={"mime_type": "image/webp"})
        )

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_a_reference_to_material_never_staged_is_refused(self, client: TestClient) -> None:
        response = client.post("/v1/captures", json=image_envelope(f"sha256:{'c' * 64}"))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "capture_material_unavailable"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    @pytest.mark.parametrize(
        "file_ref", ["/etc/passwd", "file:///etc/passwd", "http://example.com/a.png"]
    )
    def test_a_path_or_url_reference_is_refused_unopened(
        self, client: TestClient, file_ref: str
    ) -> None:
        response = client.post("/v1/captures", json=image_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert file_ref not in response.text


class TestUploadingIsNotCapturing:
    def test_an_image_upload_alone_leaves_no_capture(self, client: TestClient) -> None:
        stage(client)

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_the_same_image_uploaded_twice_returns_the_same_reference(
        self, client: TestClient
    ) -> None:
        assert stage(client, filename="one.png") == stage(client, filename="two.png")


class TestSurvivingARestart:
    def test_the_image_content_is_readable_from_a_rebuilt_application(self, data_dir: Path) -> None:
        """Durability, not a claim: the whole app is thrown away and rebuilt."""
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)
            assert first.post("/v1/captures", json=image_envelope(file_ref)).status_code == 201

        with TestClient(build_local_app(data_dir)) as second:
            content = content_of(second)

            assert content.type is ContentType.IMAGE
            assert content.segments == []
            assert content.assets[0].sha256 == IMAGE_DIGEST
