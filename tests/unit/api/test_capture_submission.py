"""``POST /v1/captures`` — the whole synchronous pipeline behind one request.

These tests care about two things: that the canonical ``CaptureEnvelope`` is the
request contract, and that ``201`` is a claim about the *pipeline* rather than
about intake.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureStatus, ContentType, ProvenanceSourceType, SegmentType
from core.processing import ProcessingInputError, TextProcessor
from tests.unit.api.builders import (
    AWKWARD_TEXT,
    CAPTURE_ID,
    CAPTURED_AT,
    TITLE,
    image_envelope,
    text_envelope,
)
from tests.unit.api.conftest import Stack, build_stack
from tests.unit.api.doubles import FailingProcessor, FakeRawObjectStore, RecordingProcessor


def test_valid_text_capture_is_created(client: TestClient) -> None:
    response = client.post("/v1/captures", json=text_envelope())

    assert response.status_code == 201


def test_success_response_names_the_capture_the_content_and_the_status(
    client: TestClient,
) -> None:
    body = client.post("/v1/captures", json=text_envelope()).json()

    assert body["capture_id"] == CAPTURE_ID
    assert body["status"] == CaptureStatus.COMPLETE.value
    assert isinstance(body["content_id"], str)
    assert body["content_id"]
    # The content object's own id, minted by the processor — never the capture's.
    assert body["content_id"] != CAPTURE_ID


def test_response_carries_nothing_beyond_the_three_documented_fields(
    client: TestClient,
) -> None:
    body = client.post("/v1/captures", json=text_envelope()).json()

    assert set(body) == {"capture_id", "content_id", "status"}


class TestTheEnvelopeIsTheRequestContract:
    """The POST body is validated by ``CaptureEnvelope`` itself, not by a copy."""

    def test_an_unknown_field_is_rejected(self, stack: Stack) -> None:
        """``extra="forbid"`` is the contract's rule, and it reaches the wire.

        A second HTTP-specific request model would have had to re-declare this,
        and the day it forgot, derived data would ride in on a capture.
        """
        response = stack.client.post(
            "/v1/captures", json=text_envelope(summary="a derived summary")
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_naive_timestamp_is_rejected(self, stack: Stack) -> None:
        """``AwareDatetime`` is a contract rule; HTTP does not soften it."""
        response = stack.client.post(
            "/v1/captures",
            json=text_envelope(context={"captured_at": "2026-01-02T03:04:05"}),
        )

        assert response.status_code == 422

    def test_a_text_payload_without_text_is_rejected(self, stack: Stack) -> None:
        """``CapturePayload``'s cross-field validator runs at the HTTP boundary."""
        response = stack.client.post(
            "/v1/captures", json=text_envelope(payload={"type": "text", "title": "empty"})
        )

        assert response.status_code == 422

    def test_a_url_payload_without_a_source_url_is_rejected(self, stack: Stack) -> None:
        """``CaptureEnvelope``'s own cross-model validator runs too."""
        response = stack.client.post(
            "/v1/captures",
            json=text_envelope(
                payload={"type": "url"}, source={"type": "browser", "provider": "firefox"}
            ),
        )

        assert response.status_code == 422


class TestMalformedBodies:
    """Rejected before core is called, and in this API's error envelope."""

    @pytest.mark.parametrize(
        ("body", "label"),
        [
            ({}, "empty object"),
            ({"id": "cap_1"}, "missing required fields"),
            ({"id": "", "source": {}, "payload": {}, "context": {}}, "blank identifier"),
            (
                text_envelope(source={"type": "telepathy"}),
                "unknown source type",
            ),
            (
                text_envelope(payload={"type": "text", "text": "   "}),
                "blank text",
            ),
        ],
    )
    def test_malformed_body_is_422(self, stack: Stack, body: dict[str, Any], label: str) -> None:
        response = stack.client.post("/v1/captures", json=body)

        assert response.status_code == 422, label
        assert response.json()["error"]["code"] == "invalid_request"

    def test_malformed_body_leaves_no_side_effect(self, stack: Stack) -> None:
        """Nothing durable, and nothing even attempted.

        Validation happens in FastAPI before the route body runs, so intake was
        never called: no receipt, no bytes, no content.
        """
        stack.client.post("/v1/captures", json={"id": CAPTURE_ID})

        assert stack.record_store.get_or_none(CAPTURE_ID) is None
        assert stack.content_store.get_or_none(CAPTURE_ID) is None

    def test_the_validation_message_does_not_echo_the_submitted_values(self, stack: Stack) -> None:
        """A rejected body is not repeated back into logs and error trackers."""
        secret = "hunter2-do-not-echo-me"
        response = stack.client.post(
            "/v1/captures",
            json=text_envelope(credentials=secret, payload={"type": "text"}),
        )

        assert response.status_code == 422
        assert secret not in response.text

    def test_a_non_json_body_is_422(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/captures",
            content=b"this is not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422


class TestUnknownSchemaVersion:
    """``SchemaVersion`` is a closed ``Literal``; a newer version is not guessed at."""

    @pytest.mark.parametrize("version", ["0.3", "1.0", "", "latest"])
    def test_unknown_schema_version_is_422(self, stack: Stack, version: str) -> None:
        response = stack.client.post("/v1/captures", json=text_envelope(schema_version=version))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_supported_older_version_is_still_accepted(self, stack: Stack) -> None:
        """0.1 is readable, so a 0.1 envelope is a valid submission."""
        response = stack.client.post("/v1/captures", json=text_envelope(schema_version="0.1"))

        assert response.status_code == 201


class TestUnsupportedPayload:
    """A valid envelope naming a capability this build does not have."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "image", "file_ref": "blob://screenshot"},
            {"type": "webpage", "html": "<p>hi</p>"},
            {"type": "document", "file_ref": "blob://report.pdf"},
            {"type": "video", "file_ref": "blob://clip.mp4"},
            {"type": "file", "file_ref": "blob://archive.zip"},
        ],
    )
    def test_non_text_payload_is_mapped_to_422(self, stack: Stack, payload: dict[str, Any]) -> None:
        """It passes HTTP validation and is refused by intake, not by the router."""
        response = stack.client.post("/v1/captures", json=text_envelope(payload=payload))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_the_refusal_happens_before_anything_is_durable(self, stack: Stack) -> None:
        """Intake refuses before the clock is read or a store is touched."""
        stack.client.post("/v1/captures", json=image_envelope())

        assert stack.record_store.get_or_none(CAPTURE_ID) is None

    def test_nothing_pretends_to_support_the_payload(self, stack: Stack) -> None:
        """No content object is fabricated for a capability that does not exist."""
        stack.client.post("/v1/captures", json=image_envelope())

        assert stack.content_store.get_or_none(CAPTURE_ID) is None


class TestDuplicateCaptureId:
    """No idempotency in this phase: a repeat is a conflict, deliberately."""

    def test_the_second_post_conflicts(self, client: TestClient) -> None:
        body = text_envelope()
        assert client.post("/v1/captures", json=body).status_code == 201

        response = client.post("/v1/captures", json=body)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"

    def test_the_conflict_does_not_return_the_previous_result(self, client: TestClient) -> None:
        """It is a refusal, not a replayed success — no content id comes back."""
        body = text_envelope()
        client.post("/v1/captures", json=body)

        conflict = client.post("/v1/captures", json=body).json()

        assert set(conflict) == {"error"}

    def test_the_first_capture_is_untouched(self, stack: Stack) -> None:
        body = text_envelope()
        first = stack.client.post("/v1/captures", json=body).json()

        stack.client.post("/v1/captures", json=body)

        stored = stack.content_store.get_for_capture(CAPTURE_ID)
        assert stored.id == first["content_id"]

    def test_a_different_id_with_identical_text_succeeds(self, stack: Stack) -> None:
        """Raw deduplication is about bytes; capture identity is about the event."""
        first = stack.client.post("/v1/captures", json=text_envelope(id="cap_a")).json()
        second = stack.client.post("/v1/captures", json=text_envelope(id="cap_b")).json()

        assert first["content_id"] != second["content_id"]
        assert (
            stack.record_store.get("cap_a").raw_object == stack.record_store.get("cap_b").raw_object
        )


class TestSuccessMeansTheWholePipelineFinished:
    """``201`` is written after the orchestrator returns, and only then."""

    def test_intake_reaching_stored_is_not_enough(self) -> None:
        """The capture is durably ``STORED`` and the response is still not 201."""
        stack = build_stack(
            processors=[FailingProcessor(ProcessingInputError("cannot normalize this"))]
        )

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 422
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.FAILED

    def test_the_processor_returns_before_the_response_is_written(self) -> None:
        raw_store = FakeRawObjectStore()
        recorder = RecordingProcessor(TextProcessor(raw_store))
        stack = build_stack(raw_store=raw_store, processors=[recorder])

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 201
        assert recorder.journal == ["process.enter", "process.return"]

    def test_content_is_durable_by_the_time_201_is_returned(self, stack: Stack) -> None:
        body = stack.client.post("/v1/captures", json=text_envelope()).json()

        assert stack.content_store.get(body["content_id"]).source.capture_id == CAPTURE_ID

    def test_a_router_that_matches_nothing_does_not_produce_201(self) -> None:
        stack = build_stack(processors=[])

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 500


class TestWhatSurvivesTheRoundTrip:
    """The submitted capture, reachable afterwards, unchanged."""

    @pytest.fixture
    def submitted(self, stack: Stack) -> Stack:
        stack.client.post("/v1/captures", json=text_envelope())
        return stack

    def test_the_capture_record_is_complete(self, submitted: Stack) -> None:
        assert submitted.record_store.get(CAPTURE_ID).status is CaptureStatus.COMPLETE

    def test_a_content_object_exists_for_the_capture(self, submitted: Stack) -> None:
        content = submitted.content_store.get_for_capture(CAPTURE_ID)

        assert content.type is ContentType.TEXT

    def test_the_exact_submitted_text_survives(self, submitted: Stack) -> None:
        """Byte for byte: no trimming, normalization, or line-ending rewriting."""
        content = submitted.content_store.get_for_capture(CAPTURE_ID)

        assert [segment.text for segment in content.segments] == [AWKWARD_TEXT]

    def test_the_submitted_title_survives(self, submitted: Stack) -> None:
        assert submitted.record_store.get(CAPTURE_ID).title == TITLE
        assert submitted.content_store.get_for_capture(CAPTURE_ID).title == TITLE

    def test_captured_at_survives_and_is_not_the_receipt_time(self, submitted: Stack) -> None:
        record = submitted.record_store.get(CAPTURE_ID)

        assert record.context is not None
        assert record.context.captured_at.isoformat() == CAPTURED_AT
        assert record.context.captured_at != record.received_at

    def test_the_capture_context_details_survive(self, submitted: Stack) -> None:
        record = submitted.record_store.get(CAPTURE_ID)

        assert record.context is not None
        assert record.context.device == "laptop"
        assert record.context.application == "terminal"

    def test_intent_and_tags_survive_on_the_capture_record(self, submitted: Stack) -> None:
        record = submitted.record_store.get(CAPTURE_ID)

        assert record.intent is not None
        assert record.intent.action is not None
        assert record.intent.action.value == "save"
        assert record.intent.collection == "reading"
        assert record.intent.tags == ["architecture", "http"]

    def test_the_raw_reference_and_digest_are_recorded(self, submitted: Stack) -> None:
        raw_object = submitted.record_store.get(CAPTURE_ID).raw_object

        assert raw_object is not None
        assert raw_object.sha256 is not None
        assert len(raw_object.sha256) == 64
        assert raw_object.ref == f"sha256:{raw_object.sha256}"

    def test_the_stored_bytes_are_the_submitted_text(self, submitted: Stack) -> None:
        raw_object = submitted.record_store.get(CAPTURE_ID).raw_object

        assert raw_object is not None
        assert submitted.raw_store.read_bytes(raw_object).decode("utf-8") == AWKWARD_TEXT

    def test_content_provenance_points_back_at_the_capture(self, submitted: Stack) -> None:
        content = submitted.content_store.get_for_capture(CAPTURE_ID)

        assert content.source.capture_id == CAPTURE_ID
        for segment in content.segments:
            assert segment.provenance.capture_id == CAPTURE_ID
            assert segment.provenance.source_type is ProvenanceSourceType.ORIGINAL
            assert segment.type is SegmentType.TEXT

    def test_content_provenance_resolves_to_an_asset_of_the_object(self, submitted: Stack) -> None:
        content = submitted.content_store.get_for_capture(CAPTURE_ID)
        asset_ids = {asset.id for asset in content.assets}

        for segment in content.segments:
            assert segment.provenance.asset_id in asset_ids


def test_the_router_still_requires_exactly_one_processor() -> None:
    """Two claimants is a wiring error the API reports, never resolves."""
    raw_store = FakeRawObjectStore()
    stack = build_stack(
        raw_store=raw_store,
        processors=[TextProcessor(raw_store), TextProcessor(raw_store)],
    )

    response = stack.client.post("/v1/captures", json=text_envelope())

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "processing_configuration_error"
