"""The two read endpoints: what a client can see after a POST, good or bad.

These are what make the lifecycle observable. Phase 0 chose truthful
non-terminal states over invented terminal ones, and that choice is only worth
anything if someone can look.
"""

import json

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.persistence import CaptureRecordPersistenceError
from core.processing import ProcessingInputError
from tests.unit.api.builders import CAPTURE_ID, text_envelope
from tests.unit.api.conftest import Stack, build_stack
from tests.unit.api.doubles import FailingProcessor, FakeCaptureRecordStore


class TestGetCapture:
    """``GET /v1/captures/{id}`` returns the store's authoritative record."""

    def test_it_returns_the_record_the_store_holds(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=text_envelope())

        response = stack.client.get(f"/v1/captures/{CAPTURE_ID}")

        assert response.status_code == 200
        assert response.json() == json.loads(stack.record_store.get(CAPTURE_ID).model_dump_json())

    def test_the_body_validates_back_into_the_canonical_contract(self, stack: Stack) -> None:
        """Not a projection of a record — a record."""
        stack.client.post("/v1/captures", json=text_envelope())

        record = CaptureRecord.model_validate(stack.client.get(f"/v1/captures/{CAPTURE_ID}").json())

        assert record.id == CAPTURE_ID
        assert record.status is CaptureStatus.COMPLETE

    def test_a_missing_capture_is_404(self, client: TestClient) -> None:
        response = client.get("/v1/captures/never-submitted")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_it_reveals_a_failed_capture(self) -> None:
        """A ``ProcessingError`` became a durable ``FAILED``, and here it is."""
        stack = build_stack(
            processors=[FailingProcessor(ProcessingInputError("nothing to normalize"))]
        )
        assert stack.client.post("/v1/captures", json=text_envelope()).status_code == 422

        body = stack.client.get(f"/v1/captures/{CAPTURE_ID}").json()

        assert body["status"] == CaptureStatus.FAILED.value
        assert body["error"]

    def test_it_reveals_a_capture_stranded_at_received(self) -> None:
        """A raw-store failure after the receipt leaves ``RECEIVED``, truthfully.

        The HTTP call failed, nothing terminal was written, and the capture says
        exactly how far it got. That is Phase 0F's rule, visible over HTTP.
        """
        stack = build_stack(
            record_store=FakeCaptureRecordStore(
                fail_replace_with=CaptureRecordPersistenceError("the database is unavailable")
            )
        )
        assert stack.client.post("/v1/captures", json=text_envelope()).status_code == 503

        body = stack.client.get(f"/v1/captures/{CAPTURE_ID}").json()

        assert body["status"] == CaptureStatus.RECEIVED.value
        assert body["error"] is None


class TestGetCaptureContent:
    """``GET /v1/captures/{id}/content`` returns the canonical object itself."""

    def test_it_returns_the_object_the_store_holds(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=text_envelope())

        response = stack.client.get(f"/v1/captures/{CAPTURE_ID}/content")

        assert response.status_code == 200
        expected = stack.content_store.get_for_capture(CAPTURE_ID).model_dump_json()
        assert response.json() == json.loads(expected)

    def test_the_body_validates_back_into_the_canonical_contract(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=text_envelope())

        content = ContentObject.model_validate(
            stack.client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )

        assert content.source.capture_id == CAPTURE_ID
        assert content.segments

    def test_it_is_the_canonical_object_and_not_a_rendering(self, stack: Stack) -> None:
        """Markdown is lossy and JSON rendering is derived; this is neither.

        The structural fields a renderer drops — provenance, assets, processing
        history — are all present, which is the observable difference.
        """
        stack.client.post("/v1/captures", json=text_envelope())

        body = stack.client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert set(body) == {
            "schema_version",
            "id",
            "type",
            "source",
            "original",
            "title",
            "metadata",
            "segments",
            "assets",
            "derived",
            "processing",
        }
        assert body["segments"][0]["provenance"]["capture_id"] == CAPTURE_ID
        assert body["processing"][0]["processor"] == "text"

    @pytest.mark.parametrize("capture_id", ["never-submitted", CAPTURE_ID])
    def test_absent_content_is_404(self, capture_id: str) -> None:
        """A capture that does not exist and one that produced nothing look alike here.

        Telling them apart is ``GET /v1/captures/{id}``'s job, which is why that
        endpoint exists.
        """
        stack = build_stack(
            processors=[FailingProcessor(ProcessingInputError("nothing to normalize"))]
        )
        stack.client.post("/v1/captures", json=text_envelope())

        response = stack.client.get(f"/v1/captures/{capture_id}/content")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_the_content_type_is_json_on_both_reads(stack: Stack) -> None:
    stack.client.post("/v1/captures", json=text_envelope())

    for path in (f"/v1/captures/{CAPTURE_ID}", f"/v1/captures/{CAPTURE_ID}/content"):
        assert stack.client.get(path).headers["content-type"].startswith("application/json")
