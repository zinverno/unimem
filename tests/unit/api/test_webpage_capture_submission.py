"""Submitting an HTML webpage over the existing HTTP surface.

**No new route, no new request shape.** Everything here goes through the same
``POST /v1/captures`` a text capture uses, carrying the same canonical
``CaptureEnvelope`` as its body. That is the claim worth testing: a new
ingestion modality arrived and the delivery contract did not move.

The other claim is safety. Errors name payload types and field names; they never
echo submitted markup. A page that cannot be processed must not send its own
script text back out in a response body.
"""

from typing import Any

import pytest

from core.contracts import CaptureStatus, ContentObject, ContentType
from core.contracts.enums import ProvenanceSourceType
from tests.unit.api.builders import (
    HTML_PAGE,
    HTML_PAGE_TEXT,
    HTML_PAGE_TITLE,
    WEBPAGE_CAPTURE_ID,
    text_envelope,
    webpage_envelope,
)
from tests.unit.api.conftest import Stack, served_paths


def content_of(stack: Stack) -> ContentObject:
    response = stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}/content")
    assert response.status_code == 200
    return ContentObject.model_validate(response.json())


class TestTheHappyPath:
    def test_a_webpage_post_returns_201(self, stack: Stack) -> None:
        response = stack.client.post("/v1/captures", json=webpage_envelope())

        assert response.status_code == 201

    def test_the_response_is_the_existing_shape(self, stack: Stack) -> None:
        body = stack.client.post("/v1/captures", json=webpage_envelope()).json()

        assert set(body) == {"capture_id", "content_id", "status"}
        assert body["capture_id"] == WEBPAGE_CAPTURE_ID
        assert body["status"] == "complete"
        assert body["content_id"]

    def test_the_capture_reads_complete(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        record = stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").json()

        assert record["status"] == CaptureStatus.COMPLETE.value
        assert record["payload_type"] == "webpage"

    def test_the_content_object_is_a_web_object(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        assert content_of(stack).type is ContentType.WEB

    def test_the_segment_is_extracted_text(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        (segment,) = content_of(stack).segments

        assert segment.text == HTML_PAGE_TEXT

    def test_the_segment_holds_no_markup_script_or_css(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())
        (segment,) = content_of(stack).segments
        assert segment.text is not None

        for fragment in ("<p>", "<h1>", "<script", "window.secret", "display: none", "&amp;"):
            assert fragment not in segment.text

    def test_the_provenance_says_html(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        (segment,) = content_of(stack).segments

        assert segment.provenance.source_type is ProvenanceSourceType.HTML
        assert segment.provenance.processor == "webpage"
        assert segment.provenance.processor_version == "0.1"

    def test_the_submitted_source_survives(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        content = content_of(stack)

        assert content.source.provider == "chromium"
        assert content.source.url == "https://example.com/article"

    def test_the_exact_html_is_in_raw_storage(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())
        record = stack.record_store.get(WEBPAGE_CAPTURE_ID)
        assert record.raw_object is not None

        assert stack.raw_store.read_bytes(record.raw_object) == HTML_PAGE.encode("utf-8")

    def test_the_original_asset_points_back_at_those_bytes(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())
        record = stack.record_store.get(WEBPAGE_CAPTURE_ID)
        (asset,) = content_of(stack).assets

        assert record.raw_object is not None
        assert asset.ref == record.raw_object.ref
        assert asset.sha256 == record.raw_object.sha256


class TestTitleThroughTheApi:
    def test_a_submitted_title_wins(self, stack: Stack) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"title": "What I Called It"}

        stack.client.post("/v1/captures", json=body)

        assert content_of(stack).title == "What I Called It"

    def test_the_capture_record_keeps_the_submitted_title(self, stack: Stack) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"title": "What I Called It"}

        stack.client.post("/v1/captures", json=body)

        record = stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").json()
        assert record["title"] == "What I Called It"

    def test_no_submitted_title_falls_back_to_the_html_title(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        assert content_of(stack).title == HTML_PAGE_TITLE

    def test_the_extracted_title_never_reaches_the_capture_record(self, stack: Stack) -> None:
        """``CaptureRecord.title`` is submitted metadata and stays that."""
        stack.client.post("/v1/captures", json=webpage_envelope())

        record = stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").json()

        assert record["title"] is None
        assert content_of(stack).title == HTML_PAGE_TITLE

    def test_a_page_with_no_title_produces_no_title(self, stack: Stack) -> None:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {"html": "<p>Body only.</p>"}

        stack.client.post("/v1/captures", json=body)

        assert content_of(stack).title is None


class TestTwoCapturesOfOnePage:
    def test_both_are_created(self, stack: Stack) -> None:
        first = stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_a"))
        second = stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_b"))

        assert first.status_code == 201
        assert second.status_code == 201

    def test_they_get_distinct_content_ids(self, stack: Stack) -> None:
        first = stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_a")).json()
        second = stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_b")).json()

        assert first["content_id"] != second["content_id"]

    def test_they_share_one_raw_object(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_a"))
        stack.client.post("/v1/captures", json=webpage_envelope(id="cap_web_b"))

        first = stack.record_store.get("cap_web_a").raw_object
        second = stack.record_store.get("cap_web_b").raw_object
        assert first is not None
        assert second is not None
        assert first.sha256 == second.sha256


class TestDuplicateWebpageSubmission:
    """Completed replay stays TEXT-only in this PR, deliberately.

    Webpage replay equivalence is a separate requirement — the durable record
    would have to prove the submitted *HTML* matches, which is a different
    comparison from the text one — and smuggling it into the first webpage
    processor would be shipping an idempotency claim nobody designed.
    """

    def test_an_identical_resubmission_conflicts(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        second = stack.client.post("/v1/captures", json=webpage_envelope())

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "capture_already_exists"

    def test_it_does_not_replay_with_200(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope())

        assert stack.client.post("/v1/captures", json=webpage_envelope()).status_code != 200

    def test_no_second_content_object_is_made(self, stack: Stack) -> None:
        first = stack.client.post("/v1/captures", json=webpage_envelope()).json()
        stack.client.post("/v1/captures", json=webpage_envelope())

        assert stack.content_store.stored_content_ids() == {first["content_id"]}

    def test_text_replay_still_works(self, stack: Stack) -> None:
        """The existing TEXT idempotency is untouched by any of this."""
        stack.client.post("/v1/captures", json=text_envelope())

        second = stack.client.post("/v1/captures", json=text_envelope())

        assert second.status_code == 200
        assert second.json()["status"] == "complete"


UNSUPPORTED_SHAPES: list[Any] = [
    pytest.param({"type": "webpage", "html": "<p>hi</p>", "text": "hi"}, id="html+text"),
    pytest.param(
        {"type": "webpage", "html": "<p>hi</p>", "file_ref": "blob://page.mhtml"},
        id="html+file_ref",
    ),
    pytest.param({"type": "webpage", "text": "already extracted"}, id="text-only"),
]


@pytest.mark.parametrize("payload", UNSUPPORTED_SHAPES)
class TestUnsupportedWebpageShapes:
    """Refused with the existing capability envelope, before anything is durable."""

    def test_it_is_a_422_unsupported_payload(self, stack: Stack, payload: dict[str, Any]) -> None:
        response = stack.client.post("/v1/captures", json=webpage_envelope(payload=payload))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_no_new_error_code_was_invented(self, stack: Stack, payload: dict[str, Any]) -> None:
        response = stack.client.post("/v1/captures", json=webpage_envelope(payload=payload))

        assert response.json()["error"]["code"] in {
            "unsupported_payload",
            "invalid_capture_envelope",
            "invalid_request",
        }

    def test_nothing_is_durable(self, stack: Stack, payload: dict[str, Any]) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope(payload=payload))

        assert stack.record_store.get_or_none(WEBPAGE_CAPTURE_ID) is None
        assert stack.content_store.stored_content_ids() == set()

    def test_the_capture_is_not_readable_afterwards(
        self, stack: Stack, payload: dict[str, Any]
    ) -> None:
        stack.client.post("/v1/captures", json=webpage_envelope(payload=payload))

        assert stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").status_code == 404

    def test_no_submitted_material_is_echoed(self, stack: Stack, payload: dict[str, Any]) -> None:
        response = stack.client.post("/v1/captures", json=webpage_envelope(payload=payload))

        message = response.json()["error"]["message"]
        for secret in ("<p>hi</p>", "already extracted", "blob://page.mhtml"):
            assert secret not in message


class TestAPageWithNothingToExtract:
    @staticmethod
    def script_only() -> dict[str, Any]:
        body = webpage_envelope()
        body["payload"] = body["payload"] | {
            "html": '<html><body><script>var secret = "s3cr3t";</script></body></html>'
        }
        return body

    def test_it_is_a_422_processing_failure(self, stack: Stack) -> None:
        response = stack.client.post("/v1/captures", json=self.script_only())

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_the_page_is_not_echoed_in_the_error(self, stack: Stack) -> None:
        response = stack.client.post("/v1/captures", json=self.script_only())

        message = response.json()["error"]["message"]
        assert "s3cr3t" not in message
        assert "<script>" not in message
        assert "var secret" not in message

    def test_the_capture_is_durably_failed_and_observable(self, stack: Stack) -> None:
        """Existing truthful lifecycle semantics, unchanged."""
        stack.client.post("/v1/captures", json=self.script_only())

        record = stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}").json()

        assert record["status"] == CaptureStatus.FAILED.value

    def test_no_content_object_is_produced(self, stack: Stack) -> None:
        stack.client.post("/v1/captures", json=self.script_only())

        assert stack.content_store.stored_content_ids() == set()
        assert stack.client.get(f"/v1/captures/{WEBPAGE_CAPTURE_ID}/content").status_code == 404

    def test_the_submitted_html_is_still_stored(self, stack: Stack) -> None:
        """Processing failed; the original did not go anywhere."""
        stack.client.post("/v1/captures", json=self.script_only())
        record = stack.record_store.get(WEBPAGE_CAPTURE_ID)

        assert record.raw_object is not None
        assert b"s3cr3t" in stack.raw_store.read_bytes(record.raw_object)


class TestTheSurfaceDidNotGrow:
    def test_no_route_was_added(self, stack: Stack) -> None:
        """The four routes Phase 1 shipped, and only those. (FastAPI's own
        ``/docs``, ``/redoc`` and ``/openapi.json`` are the framework's.)"""
        assert {path for path in served_paths(stack.app) if path.startswith("/v1")} == {
            "/v1/captures",
            "/v1/captures/{capture_id}",
            "/v1/captures/{capture_id}/content",
        }
        assert "/health" in served_paths(stack.app)

    @pytest.mark.parametrize(
        "path", ["/v1/webpages", "/v1/pages", "/v1/html", "/v1/uploads", "/v1/captures/html/body"]
    )
    def test_no_webpage_specific_route_exists(self, stack: Stack, path: str) -> None:
        assert stack.client.post(path, json=webpage_envelope()).status_code == 404

    def test_the_post_body_is_still_the_canonical_envelope(self, stack: Stack) -> None:
        schema = stack.client.get("/openapi.json").json()
        body = schema["paths"]["/v1/captures"]["post"]["requestBody"]

        reference = body["content"]["application/json"]["schema"]["$ref"]
        assert reference.endswith("/CaptureEnvelope")

    def test_no_second_request_dto_appeared(self, stack: Stack) -> None:
        schemas = stack.client.get("/openapi.json").json()["components"]["schemas"]

        for name in schemas:
            assert "Webpage" not in name
            assert "Html" not in name

    def test_the_201_response_model_is_unchanged(self, stack: Stack) -> None:
        schema = stack.client.get("/openapi.json").json()
        responses = schema["paths"]["/v1/captures"]["post"]["responses"]

        assert set(responses) >= {"201", "200"}
