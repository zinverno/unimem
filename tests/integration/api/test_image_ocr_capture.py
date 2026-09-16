"""Image OCR over the real HTTP surface, for every outcome the matrix names.

The unit tests prove the processor builds the right object. This file proves the
*lifecycle* around it: which status code a client sees, what the capture record
durably says afterwards, and whether canonical content exists to read back. Those
are the things a deployment actually behaves as, and three of the six rows differ
only there.

The recognizer is a fake, so no engine, no imaging library and no subprocess is
involved — the real one is exercised by the native suite. What is real here is
everything else: a live ASGI app, the upload route, intake, the orchestrator, and
both SQLite stores.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from core.processing import (
    ENCODED_BYTE_LIMIT,
    ENCODED_PIXEL_LIMIT,
    ENGINE_INVOKED_KEY,
    ENGINE_KEY,
    ENGINE_VERSION_KEY,
    IMAGE_OCR_METADATA_KEY,
    MAX_ENCODED_BYTES_KEY,
    MAX_ENCODED_PIXELS_KEY,
    SETTINGS_KEY,
    SKIPPED_REASON_KEY,
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
)
from tests import images
from tests.unit.processing.doubles import FakeImageOcr
from unimem_api.wiring import build_local_app

CAPTURE_ID: Final = "cap_image_ocr_http_01"
CAPTURED_AT: Final = "2026-05-06T07:08:09+00:00"
PNG_MIME: Final = "image/png"

PNG: Final = images.png(width=16, height=9)

#: Deliberately ragged, so a response that has been tidied up fails the test.
RECOGNIZED: Final = "  HARBOUR\n\n  pier 4  \n"


def client_for(tmp_path: Path, recognizer: FakeImageOcr | None) -> Iterator[TestClient]:
    app = build_local_app(tmp_path / "data", image_ocr=recognizer)
    with TestClient(app) as running:
        yield running


@pytest.fixture
def reading_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from client_for(tmp_path, FakeImageOcr(text=RECOGNIZED))


@pytest.fixture
def silent_client(tmp_path: Path) -> Iterator[TestClient]:
    """A deployment whose engine runs and reads nothing."""
    yield from client_for(tmp_path, FakeImageOcr(text=""))


def submit(client: TestClient, data: bytes = PNG, **overrides: Any) -> Any:
    upload = client.post("/v1/uploads", files={"file": ("photo.png", data, PNG_MIME)})
    assert upload.status_code == 200, upload.text
    envelope: dict[str, Any] = {
        "schema_version": "0.2",
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": "image",
            "mime_type": PNG_MIME,
            "file_ref": upload.json()["file_ref"],
        },
        "context": {"captured_at": CAPTURED_AT},
    }
    return client.post("/v1/captures", json=envelope | overrides)


def content_of(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/v1/captures/{CAPTURE_ID}/content")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def record_of(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/v1/captures/{CAPTURE_ID}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ocr_metadata(body: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = body["metadata"][IMAGE_OCR_METADATA_KEY]
    return mapping


class TestAnImageWithWordsInIt:
    def test_the_capture_is_created(self, reading_client: TestClient) -> None:
        assert submit(reading_client).status_code == 201

    def test_the_capture_completes(self, reading_client: TestClient) -> None:
        submit(reading_client)

        assert record_of(reading_client)["status"] == "complete"

    def test_the_content_carries_one_ocr_segment(self, reading_client: TestClient) -> None:
        submit(reading_client)

        segments = content_of(reading_client)["segments"]

        assert len(segments) == 1
        assert segments[0]["type"] == "ocr"

    def test_the_text_survives_the_round_trip_unchanged(self, reading_client: TestClient) -> None:
        submit(reading_client)

        assert content_of(reading_client)["segments"][0]["text"] == RECOGNIZED

    def test_the_segment_has_no_page_and_no_box(self, reading_client: TestClient) -> None:
        submit(reading_client)

        segment = content_of(reading_client)["segments"][0]

        assert segment["spatial"] is None
        assert segment["temporal"] is None

    def test_the_provenance_survives_the_round_trip(self, reading_client: TestClient) -> None:
        submit(reading_client)

        body = content_of(reading_client)
        provenance = body["segments"][0]["provenance"]

        assert provenance["source_type"] == "ocr"
        assert provenance["processor"] == "image-ocr"
        assert provenance["asset_id"] == body["assets"][0]["id"]

    def test_the_object_is_still_an_image_with_one_original(
        self, reading_client: TestClient
    ) -> None:
        submit(reading_client)

        body = content_of(reading_client)

        assert body["type"] == "image"
        assert [asset["role"] for asset in body["assets"]] == ["original"]

    def test_the_structural_metadata_is_still_recorded(self, reading_client: TestClient) -> None:
        submit(reading_client)

        assert content_of(reading_client)["metadata"]["image"] == {
            "encoded_format": "png",
            "encoded_width": 16,
            "encoded_height": 9,
        }

    def test_the_engine_is_recorded_as_invoked(self, reading_client: TestClient) -> None:
        submit(reading_client)

        recorded = ocr_metadata(content_of(reading_client))

        assert recorded[ENGINE_INVOKED_KEY] is True
        assert recorded[ENGINE_KEY] == "fake-ocr"
        assert recorded[ENGINE_VERSION_KEY] == "9.9.9"


class TestAnImageTheEngineReadNothingIn:
    def test_the_capture_still_succeeds(self, silent_client: TestClient) -> None:
        assert submit(silent_client).status_code == 201

    def test_the_capture_still_completes(self, silent_client: TestClient) -> None:
        submit(silent_client)

        assert record_of(silent_client)["status"] == "complete"

    def test_there_are_no_segments(self, silent_client: TestClient) -> None:
        submit(silent_client)

        assert content_of(silent_client)["segments"] == []

    def test_the_engine_is_still_recorded_as_invoked(self, silent_client: TestClient) -> None:
        """Which is the whole difference from a deployment that never ran one."""
        submit(silent_client)

        recorded = ocr_metadata(content_of(silent_client))

        assert recorded[ENGINE_INVOKED_KEY] is True
        assert SETTINGS_KEY in recorded

    def test_no_returned_text_flag_is_recorded(self, silent_client: TestClient) -> None:
        submit(silent_client)

        assert "returned_text" not in ocr_metadata(content_of(silent_client))

    def test_the_word_blank_appears_nowhere(self, silent_client: TestClient) -> None:
        submit(silent_client)

        assert "blank" not in str(content_of(silent_client))

    def test_a_whitespace_only_answer_behaves_the_same(self, tmp_path: Path) -> None:
        for running in client_for(tmp_path, FakeImageOcr(text="  \n\t ")):
            submit(running)

            assert content_of(running)["segments"] == []
            assert ocr_metadata(content_of(running))[ENGINE_INVOKED_KEY] is True


class TestAResourcePolicySkipOverHttp:
    @staticmethod
    def refusing(reason: str, limit: int) -> FakeImageOcr:
        return FakeImageOcr(
            raises=ImageOcrLimitExceeded("refused", reason=reason, limit=limit)  # type: ignore[arg-type]
        )

    def test_a_pixel_skip_is_a_created_capture(self, tmp_path: Path) -> None:
        """Not a 422 and not a 503: an over-budget image is still a good image."""
        for running in client_for(tmp_path, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000)):
            assert submit(running).status_code == 201

    def test_a_pixel_skip_completes_the_capture(self, tmp_path: Path) -> None:
        for running in client_for(tmp_path, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000)):
            submit(running)

            assert record_of(running)["status"] == "complete"

    def test_a_pixel_skip_records_exactly_what_stopped_it(self, tmp_path: Path) -> None:
        for running in client_for(tmp_path, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000)):
            submit(running)

            assert ocr_metadata(content_of(running)) == {
                ENGINE_INVOKED_KEY: False,
                SKIPPED_REASON_KEY: ENCODED_PIXEL_LIMIT,
                MAX_ENCODED_PIXELS_KEY: 20_000_000,
            }

    def test_a_byte_skip_records_exactly_what_stopped_it(self, tmp_path: Path) -> None:
        for running in client_for(tmp_path, self.refusing(ENCODED_BYTE_LIMIT, 67_108_864)):
            submit(running)

            assert ocr_metadata(content_of(running)) == {
                ENGINE_INVOKED_KEY: False,
                SKIPPED_REASON_KEY: ENCODED_BYTE_LIMIT,
                MAX_ENCODED_BYTES_KEY: 67_108_864,
            }

    def test_a_skipped_image_is_still_fully_readable(self, tmp_path: Path) -> None:
        for running in client_for(tmp_path, self.refusing(ENCODED_PIXEL_LIMIT, 1)):
            submit(running)

            body = content_of(running)

            assert body["type"] == "image"
            assert body["segments"] == []
            assert body["metadata"]["image"]["encoded_width"] == 16
            assert body["original"]["sha256"]

    def test_the_skip_survives_a_restart_over_the_same_data_directory(self, tmp_path: Path) -> None:
        """Durable, not merely returned once: a later reader must see the skip."""
        for running in client_for(tmp_path, self.refusing(ENCODED_PIXEL_LIMIT, 20_000_000)):
            submit(running)

        with TestClient(build_local_app(tmp_path / "data")) as reopened:
            recorded = ocr_metadata(content_of(reopened))

        assert recorded[SKIPPED_REASON_KEY] == ENCODED_PIXEL_LIMIT


class TestAnExecutionFailureOverHttp:
    @pytest.fixture
    def broken_client(self, tmp_path: Path) -> Iterator[TestClient]:
        yield from client_for(
            tmp_path,
            FakeImageOcr(
                raises=ImageOcrExecutionError(
                    "the OCR engine at /opt/local/bin/tesseract exited with status 139"
                )
            ),
        )

    def test_the_client_gets_a_503(self, broken_client: TestClient) -> None:
        assert submit(broken_client).status_code == 503

    def test_the_code_names_image_recognition(self, broken_client: TestClient) -> None:
        response = submit(broken_client)

        assert response.json()["error"]["code"] == "image_ocr_unavailable"

    def test_the_public_message_is_the_fixed_one(self, broken_client: TestClient) -> None:
        response = submit(broken_client)

        assert response.json()["error"]["message"] == (
            "text recognition for this image could not be completed; "
            "the capture is stored and no content was produced"
        )

    def test_the_message_never_leaks_the_engine_or_a_local_path(
        self, broken_client: TestClient
    ) -> None:
        body = str(submit(broken_client).json())

        for secret in ("/opt/local", "tesseract", "139"):
            assert secret not in body

    def test_the_capture_is_left_non_terminal(self, broken_client: TestClient) -> None:
        """Not ``failed``: nothing was learned about the image, so nothing is claimed."""
        submit(broken_client)

        assert record_of(broken_client)["status"] == "processing"

    def test_no_content_object_exists_to_read(self, broken_client: TestClient) -> None:
        submit(broken_client)

        assert broken_client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_the_capture_record_carries_no_error_verdict(self, broken_client: TestClient) -> None:
        submit(broken_client)

        assert record_of(broken_client)["error"] is None

    def test_resubmitting_the_same_id_conflicts(self, broken_client: TestClient) -> None:
        """Recovery is a new capture over the same staged bytes, unchanged from ADR-018."""
        submit(broken_client)

        assert submit(broken_client).status_code == 409


class TestAnInconsistentAdapterOverHttp:
    """One case, because the lifecycle is what this file owns.

    Every shape of adapter inconsistency is covered by the unit tests; what is
    only observable here is that an adapter returning something that is not a
    result at all reaches a client as the *same* fixed 503 a crashed engine does,
    rather than as an unhandled ``AttributeError`` and a generic 500. The two are
    the same fact — no trusted recognition result — so they get the same answer.
    """

    @pytest.fixture
    def inconsistent_client(self, tmp_path: Path) -> Iterator[TestClient]:
        yield from client_for(tmp_path, FakeImageOcr(returns={"text": "words"}))

    def test_the_client_gets_the_fixed_503(self, inconsistent_client: TestClient) -> None:
        response = submit(inconsistent_client)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "image_ocr_unavailable"

    def test_the_capture_is_left_non_terminal(self, inconsistent_client: TestClient) -> None:
        submit(inconsistent_client)

        assert record_of(inconsistent_client)["status"] == "processing"

    def test_no_content_object_exists_to_read(self, inconsistent_client: TestClient) -> None:
        submit(inconsistent_client)

        assert inconsistent_client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404


class TestAMalformedImageIsUnchangedFrom4A:
    @pytest.fixture
    def reading_client_for_bad_input(self, tmp_path: Path) -> Iterator[TestClient]:
        yield from client_for(tmp_path, FakeImageOcr(text=RECOGNIZED))

    def test_a_broken_header_is_still_a_422(self, reading_client_for_bad_input: TestClient) -> None:
        response = submit(reading_client_for_bad_input, images.png(crc=0xDEADBEEF))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_the_capture_durably_fails(self, reading_client_for_bad_input: TestClient) -> None:
        submit(reading_client_for_bad_input, images.png(crc=0xDEADBEEF))

        assert record_of(reading_client_for_bad_input)["status"] == "failed"

    def test_the_recognizer_was_never_asked(self, tmp_path: Path) -> None:
        recognizer = FakeImageOcr(text=RECOGNIZED)

        for running in client_for(tmp_path, recognizer):
            submit(running, images.png(crc=0xDEADBEEF))

        assert recognizer.calls == []

    def test_a_declared_type_the_bytes_contradict_is_still_a_422(
        self, reading_client_for_bad_input: TestClient
    ) -> None:
        response = submit(reading_client_for_bad_input, images.jpeg())

        assert response.status_code == 422


class TestTheDefaultBuildIsUntouched:
    @pytest.fixture
    def plain_client(self, tmp_path: Path) -> Iterator[TestClient]:
        yield from client_for(tmp_path, None)

    def test_an_image_still_completes_with_no_segments(self, plain_client: TestClient) -> None:
        assert submit(plain_client).status_code == 201
        assert content_of(plain_client)["segments"] == []

    def test_it_is_stamped_image_at_0_1(self, plain_client: TestClient) -> None:
        submit(plain_client)

        assert content_of(plain_client)["processing"][0]["processor"] == "image"

    def test_it_carries_no_image_ocr_key(self, plain_client: TestClient) -> None:
        submit(plain_client)

        assert IMAGE_OCR_METADATA_KEY not in content_of(plain_client)["metadata"]

    def test_a_malformed_image_still_fails_the_same_way(self, plain_client: TestClient) -> None:
        response = submit(plain_client, images.png(crc=0xDEADBEEF))

        assert response.status_code == 422
        assert record_of(plain_client)["status"] == "failed"
