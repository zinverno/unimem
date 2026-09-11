"""``POST /v1/captures`` twice: when that is a replay, and when it is a conflict.

The requirement is the browser connector's, and it is specific. The extension
mints a capture id, POSTs, and the response is lost on the way back. It cannot
tell a request that never arrived from one that completed, and it holds an
envelope it can resend verbatim. The server's answer is the whole of this file:

* **201** — this attempt created and completed the capture;
* **200** — this was an equivalent resubmission of one already complete;
* **409** — everything else, exactly as before.

So the assertions come in two families. One is that a replay returns *the first
submission's result* — the same content id, from the same stored object. The
other, which matters more, is that a replay **does nothing**: no processor run,
no second content object, no rewritten record, no re-stored bytes, no touched
timestamp. A replay that quietly reprocessed would return the right body and
still be wrong.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.contracts import (
    CaptureContext,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
)
from core.persistence import CaptureRecordNotFoundError, ContentObjectCorruptError
from core.processing import TextProcessor
from tests.unit.api.builders import CAPTURE_ID, CAPTURED_AT, TITLE, text_envelope
from tests.unit.api.conftest import Stack, build_stack, served_paths
from tests.unit.api.doubles import (
    FakeCaptureRecordStore,
    FakeContentObjectStore,
    FakeRawObjectStore,
    RecordingProcessor,
)


@pytest.fixture
def submitted(stack: Stack) -> Stack:
    """A stack holding one durably complete capture, submitted the ordinary way."""
    assert stack.client.post("/v1/captures", json=text_envelope()).status_code == 201
    return stack


def replay(stack: Stack, **overrides: Any) -> Any:
    """Resend the envelope, optionally with one fact changed."""
    return stack.client.post("/v1/captures", json=text_envelope(**overrides))


class TestTheTwoSuccesses:
    """201 and 200 are the same body under two different claims."""

    def test_the_first_submission_is_still_201(self, client: TestClient) -> None:
        assert client.post("/v1/captures", json=text_envelope()).status_code == 201

    def test_an_equivalent_resubmission_is_200(self, submitted: Stack) -> None:
        assert replay(submitted).status_code == 200

    def test_the_replay_names_the_same_capture(self, submitted: Stack) -> None:
        assert replay(submitted).json()["capture_id"] == CAPTURE_ID

    def test_the_replay_returns_the_existing_content_id(self, submitted: Stack) -> None:
        first = submitted.client.post("/v1/captures", json=text_envelope(id="cap_second")).json()
        assert first["content_id"]  # the second capture is untouched by any of this

        replayed = replay(submitted).json()

        assert replayed["content_id"] == submitted.content_store.get_for_capture(CAPTURE_ID).id

    def test_the_replayed_content_id_is_the_one_201_reported(self, stack: Stack) -> None:
        created = stack.client.post("/v1/captures", json=text_envelope()).json()

        assert replay(stack).json()["content_id"] == created["content_id"]

    def test_the_replay_reports_complete(self, submitted: Stack) -> None:
        assert replay(submitted).json()["status"] == CaptureStatus.COMPLETE.value

    def test_the_replay_body_is_the_created_body(self, stack: Stack) -> None:
        """No ``replayed`` flag, no ``duplicate`` field. The status code says it."""
        created = stack.client.post("/v1/captures", json=text_envelope()).json()

        assert replay(stack).json() == created

    def test_replaying_twice_answers_the_same_way(self, submitted: Stack) -> None:
        """The guarantee is not a one-shot token: it is a property of the capture."""
        first = replay(submitted)
        second = replay(submitted)

        assert (first.status_code, second.status_code) == (200, 200)
        assert first.json() == second.json()

    def test_two_distinct_ids_with_the_same_text_are_both_created(self, stack: Stack) -> None:
        """Replay is about capture identity, never about matching bytes."""
        first = stack.client.post("/v1/captures", json=text_envelope(id="cap_a"))
        second = stack.client.post("/v1/captures", json=text_envelope(id="cap_b"))

        assert (first.status_code, second.status_code) == (201, 201)
        assert first.json()["content_id"] != second.json()["content_id"]


class TestAReplayDoesNothing:
    """The negative space, which is the actual guarantee."""

    def test_the_processor_does_not_run_again(self) -> None:
        raw_store = FakeRawObjectStore()
        recorder = RecordingProcessor(TextProcessor(raw_store))
        stack = build_stack(raw_store=raw_store, processors=[recorder])
        stack.client.post("/v1/captures", json=text_envelope())
        assert recorder.journal == ["process.enter", "process.return"]

        assert replay(stack).status_code == 200

        assert recorder.journal == ["process.enter", "process.return"]

    def test_no_second_content_object_is_created(self, submitted: Stack) -> None:
        before = submitted.content_store.stored_content_ids()

        replay(submitted)

        assert submitted.content_store.stored_content_ids() == before
        assert len(before) == 1

    def test_no_raw_bytes_are_written_again(self, submitted: Stack) -> None:
        before = list(submitted.raw_store.writes)

        replay(submitted)

        assert submitted.raw_store.writes == before
        assert len(before) == 1

    def test_the_capture_record_is_byte_for_byte_unchanged(self, submitted: Stack) -> None:
        """Status, timestamps, raw reference, metadata — the whole snapshot."""
        before = submitted.record_store.get(CAPTURE_ID).model_dump_json()

        replay(submitted)

        assert submitted.record_store.get(CAPTURE_ID).model_dump_json() == before

    def test_no_timestamp_moves(self, submitted: Stack) -> None:
        before = submitted.record_store.get(CAPTURE_ID)

        replay(submitted)

        after = submitted.record_store.get(CAPTURE_ID)
        assert after.received_at == before.received_at
        assert after.updated_at == before.updated_at

    def test_the_canonical_content_is_unchanged(self, submitted: Stack) -> None:
        before = submitted.content_store.get_for_capture(CAPTURE_ID).model_dump_json()

        replay(submitted)

        assert submitted.content_store.get_for_capture(CAPTURE_ID).model_dump_json() == before

    def test_the_record_still_reads_complete_afterwards(self, submitted: Stack) -> None:
        replay(submitted)

        assert submitted.record_store.get(CAPTURE_ID).status is CaptureStatus.COMPLETE


#: One durable capture, and every single-fact change a client could make to
#: the request it resends. Each stays a conflict.
CONFLICTING: dict[str, dict[str, Any]] = {
    "different selected text": {
        "payload": {"type": "text", "mime_type": "text/plain", "text": "other", "title": TITLE}
    },
    "different source url": {
        "source": {"type": "api", "provider": "curl", "url": "https://example.com/elsewhere"}
    },
    "different source provider": {
        "source": {"type": "api", "provider": "httpie", "url": "https://example.com/notes"}
    },
    "different source type": {
        "source": {"type": "browser", "provider": "curl", "url": "https://example.com/notes"}
    },
    "different title": {
        "payload": {
            "type": "text",
            "mime_type": "text/plain",
            "text": "unchanged",
            "title": "a different title",
        }
    },
    "no title at all": {
        "payload": {"type": "text", "mime_type": "text/plain", "text": "unchanged"}
    },
    "different captured_at": {
        "context": {
            "captured_at": "2026-01-02T03:04:06+00:00",
            "device": "laptop",
            "application": "terminal",
        }
    },
    "different device": {
        "context": {"captured_at": CAPTURED_AT, "device": "phone", "application": "terminal"}
    },
    "different intent action": {
        "intent": {
            "action": "analyze",
            "collection": "reading",
            "tags": ["architecture", "http"],
        }
    },
    "no intent at all": {"intent": None},
    "different tags": {
        "intent": {"action": "save", "collection": "reading", "tags": ["architecture"]}
    },
    "reordered tags": {
        "intent": {"action": "save", "collection": "reading", "tags": ["http", "architecture"]}
    },
    "different mime type": {
        "payload": {
            "type": "text",
            "mime_type": "text/markdown",
            "text": "unchanged",
            "title": TITLE,
        }
    },
    "no mime type": {"payload": {"type": "text", "text": "unchanged", "title": TITLE}},
    "an added html rendering": {
        "payload": {
            "type": "text",
            "mime_type": "text/plain",
            "text": "unchanged",
            "html": "<p>unchanged</p>",
            "title": TITLE,
        }
    },
    "an added file reference": {
        "payload": {
            "type": "text",
            "mime_type": "text/plain",
            "text": "unchanged",
            "file_ref": "blob://somewhere",
            "title": TITLE,
        }
    },
    "an older schema version": {"schema_version": "0.1"},
}


class TestSameIdIsNotSameRequest:
    """Every fact a client could change, and the conflict each one keeps.

    A capture id is a claim the client makes about identity, not evidence about
    content. If a replay were granted on the id alone, a colliding or reused id
    would be answered with somebody else's content — which is a far worse
    failure than the spurious conflict refusing it produces.
    """

    @pytest.fixture
    def seeded(self, stack: Stack) -> Stack:
        """One complete capture whose text is the plain word the cases reuse."""
        body = text_envelope(
            payload={"type": "text", "mime_type": "text/plain", "text": "unchanged", "title": TITLE}
        )
        assert stack.client.post("/v1/captures", json=body).status_code == 201
        return stack

    @pytest.mark.parametrize("overrides", CONFLICTING.values(), ids=list(CONFLICTING))
    def test_it_is_a_conflict(self, seeded: Stack, overrides: dict[str, Any]) -> None:
        response = seeded.client.post("/v1/captures", json=text_envelope(**overrides))

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"

    @pytest.mark.parametrize("overrides", CONFLICTING.values(), ids=list(CONFLICTING))
    def test_it_never_returns_the_existing_content(
        self, seeded: Stack, overrides: dict[str, Any]
    ) -> None:
        body = seeded.client.post("/v1/captures", json=text_envelope(**overrides)).json()

        assert set(body) == {"error"}

    @pytest.mark.parametrize("overrides", CONFLICTING.values(), ids=list(CONFLICTING))
    def test_the_incoming_request_is_not_processed(
        self, seeded: Stack, overrides: dict[str, Any]
    ) -> None:
        """A refused replay is refused, not quietly captured under another shape."""
        before = seeded.record_store.get(CAPTURE_ID).model_dump_json()
        writes = list(seeded.raw_store.writes)

        seeded.client.post("/v1/captures", json=text_envelope(**overrides))

        assert seeded.record_store.get(CAPTURE_ID).model_dump_json() == before
        assert seeded.raw_store.writes == writes
        assert len(seeded.content_store.stored_content_ids()) == 1

    def test_a_legacy_envelope_is_not_replayable(self, seeded: Stack) -> None:
        """0.1 is refused for lack of proof, not by a version rule invented here.

        Intake writes every record at the current schema version, so a 0.1
        submission produces a 0.2 record that cannot vouch for a 0.1 request's
        metadata. Rather than build migration machinery to bridge that, the
        duplicate keeps the conflict it would have received anyway — which is
        not a regression, because a duplicate was always a conflict.
        """
        legacy = text_envelope(schema_version="0.1")

        assert seeded.client.post("/v1/captures", json=legacy).status_code == 409


#: Every lifecycle state that is not ``COMPLETE``, including the two this
#: build never writes yet. A capture in any of them is not replayable.
UNFINISHED = [
    CaptureStatus.RECEIVED,
    CaptureStatus.STORED,
    CaptureStatus.QUEUED,
    CaptureStatus.PROCESSING,
    CaptureStatus.PARTIAL,
    CaptureStatus.FAILED,
]


def stack_holding(status: CaptureStatus) -> Stack:
    """One complete capture, rewound to an earlier lifecycle state.

    The record is edited through the store rather than by contriving a failure,
    so every state is reachable — including ``queued`` and ``partial``, which no
    code path in this build writes yet and which must still not be mistaken for
    a finished capture.
    """
    stack = build_stack()
    stack.client.post("/v1/captures", json=text_envelope())
    complete = stack.record_store.get(CAPTURE_ID)
    stack.record_store.replace(
        CaptureRecord.model_validate(complete.model_dump() | {"status": status})
    )
    return stack


class TestADuplicateThatIsNotComplete:
    """Only a finished capture is replayable. Nothing here is resumed."""

    @pytest.mark.parametrize("status", UNFINISHED, ids=lambda s: s.value)
    def test_it_conflicts(self, status: CaptureStatus) -> None:
        stack = stack_holding(status)

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"

    @pytest.mark.parametrize("status", UNFINISHED, ids=lambda s: s.value)
    def test_the_content_store_is_never_consulted(self, status: CaptureStatus) -> None:
        """Checked before content, so a capture mid-run cannot be read as one
        whose content has gone missing."""
        stack = stack_holding(status)
        stack.content_store.capture_lookups.clear()

        stack.client.post("/v1/captures", json=text_envelope())

        assert stack.content_store.capture_lookups == []

    @pytest.mark.parametrize("status", UNFINISHED, ids=lambda s: s.value)
    def test_the_stranded_capture_is_left_exactly_as_it_was(self, status: CaptureStatus) -> None:
        """Not resumed, not retried, not marked complete, not reconciled."""
        stack = stack_holding(status)
        before = stack.record_store.get(CAPTURE_ID).model_dump_json()

        stack.client.post("/v1/captures", json=text_envelope())

        assert stack.record_store.get(CAPTURE_ID).model_dump_json() == before

    def test_it_is_never_answered_with_202_or_200(self) -> None:
        """A capture still running is not "accepted" again, and not complete."""
        stack = stack_holding(CaptureStatus.PROCESSING)

        assert stack.client.post("/v1/captures", json=text_envelope()).status_code == 409


class TestARecordThatIsNotThereToRead:
    """Creation was refused and the record is gone. Rare, and still an answer.

    Nothing here waits for it to reappear or retries the submission: the client
    is told the id is taken, which is the last true thing the server said about
    it.
    """

    def test_it_is_the_ordinary_duplicate_conflict(self) -> None:
        record_store = FakeCaptureRecordStore(
            fail_get_with=CaptureRecordNotFoundError("capture record is not stored")
        )
        stack = build_stack(record_store=record_store)
        # Take the id, so intake refuses creation while the read finds nothing.
        record_store.create(
            CaptureRecord(
                id=CAPTURE_ID,
                status=CaptureStatus.COMPLETE,
                received_at=datetime(2026, 1, 2, 3, 4, 9, tzinfo=UTC),
                source=CaptureSource(type=CaptureSourceType.API),
                payload_type=CapturePayloadType.TEXT,
                context=CaptureContext(captured_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
            )
        )

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"

    def test_the_content_store_is_never_consulted(self) -> None:
        record_store = FakeCaptureRecordStore(
            fail_get_with=CaptureRecordNotFoundError("capture record is not stored")
        )
        stack = build_stack(record_store=record_store)
        record_store.create(
            CaptureRecord(
                id=CAPTURE_ID,
                status=CaptureStatus.COMPLETE,
                received_at=datetime(2026, 1, 2, 3, 4, 9, tzinfo=UTC),
                source=CaptureSource(type=CaptureSourceType.API),
                payload_type=CapturePayloadType.TEXT,
                context=CaptureContext(captured_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
            )
        )

        stack.client.post("/v1/captures", json=text_envelope())

        assert stack.content_store.capture_lookups == []


class TestCompleteWithoutContent:
    """The invariant Phase 0I established, and what its violation must look like.

    A ``COMPLETE`` capture *has* canonical content: the orchestrator writes the
    content first and the snapshot second. So a complete capture with nothing to
    return is the server contradicting itself — not a client's bad request, not
    a missing resource, and certainly not a replay that can be reported as
    success with no content id to give.
    """

    def test_missing_content_under_a_complete_record_is_500(self, submitted: Stack) -> None:
        submitted.content_store.drop_for_capture(CAPTURE_ID)

        response = replay(submitted)

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "data_integrity_error"

    def test_it_is_not_reported_as_a_conflict_or_a_404(self, submitted: Stack) -> None:
        submitted.content_store.drop_for_capture(CAPTURE_ID)

        status_code = replay(submitted).status_code

        assert status_code not in (200, 201, 404, 409)

    def test_it_does_not_reprocess_the_capture_to_repair_it(self, submitted: Stack) -> None:
        submitted.content_store.drop_for_capture(CAPTURE_ID)
        writes = list(submitted.raw_store.writes)

        replay(submitted)

        assert submitted.content_store.stored_content_ids() == set()
        assert submitted.raw_store.writes == writes

    def test_content_that_cannot_be_read_back_is_the_existing_integrity_error(self) -> None:
        """A corrupt canonical snapshot keeps core's own mapping, not a new one."""
        content_store = FakeContentObjectStore(
            fail_get_for_capture_with=ContentObjectCorruptError("stored content is not valid JSON")
        )
        stack = build_stack(content_store=content_store)
        stack.client.post("/v1/captures", json=text_envelope())

        response = replay(stack)

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "data_integrity_error"

    @pytest.mark.parametrize("case", ["missing", "corrupt"])
    def test_the_response_gives_nothing_away(self, case: str) -> None:
        """No path, no id, no store name, no exception text — the fixed message."""
        detail = "/var/lib/unimem/private/unimem.sqlite3"
        if case == "missing":
            stack = build_stack()
            stack.client.post("/v1/captures", json=text_envelope())
            stack.content_store.drop_for_capture(CAPTURE_ID)
        else:
            stack = build_stack(
                content_store=FakeContentObjectStore(
                    fail_get_for_capture_with=ContentObjectCorruptError(f"unreadable at {detail}")
                )
            )
            stack.client.post("/v1/captures", json=text_envelope())

        body = replay(stack).json()

        assert body["error"]["message"] == "stored data for this capture could not be read back"
        assert detail not in body["error"]["message"]
        assert CAPTURE_ID not in body["error"]["message"]
        assert "sqlite" not in body["error"]["message"].lower()


class TestTheSurfaceItself:
    """Replay changed the POST's answers, and nothing else about the API."""

    def test_no_route_was_added(self, submitted: Stack) -> None:
        """No ``/retry``, no ``/replay``, no idempotency endpoint, no PUT."""
        paths = {p for p in served_paths(submitted.app) if p.startswith(("/health", "/v1"))}

        assert paths == {
            "/health",
            "/v1/uploads",
            "/v1/captures",
            "/v1/captures/{capture_id}",
            "/v1/captures/{capture_id}/content",
        }

    def test_the_captures_path_still_answers_only_post(self, submitted: Stack) -> None:
        methods = {
            method
            for route in submitted.app.routes
            if getattr(route, "path", "") == "/v1/captures"
            for method in getattr(route, "methods", set())
        }

        assert methods == {"POST"}

    def test_openapi_documents_both_successes(self, submitted: Stack) -> None:
        responses = submitted.app.openapi()["paths"]["/v1/captures"]["post"]["responses"]

        assert "201" in responses
        assert "200" in responses

    def test_openapi_gives_both_successes_the_same_body(self, submitted: Stack) -> None:
        responses = submitted.app.openapi()["paths"]["/v1/captures"]["post"]["responses"]

        def schema(code: str) -> Any:
            return responses[code]["content"]["application/json"]["schema"]

        assert schema("200") == schema("201")

    def test_openapi_still_documents_the_error_envelope(self, submitted: Stack) -> None:
        responses = submitted.app.openapi()["paths"]["/v1/captures"]["post"]["responses"]

        assert "4XX" in responses
        assert "5XX" in responses

    def test_a_malformed_body_is_still_rejected_before_anything_runs(
        self, submitted: Stack
    ) -> None:
        """Validation is unchanged: replay lives past intake, not before it."""
        response = submitted.client.post("/v1/captures", json={"id": CAPTURE_ID})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_an_unknown_field_is_still_rejected(self, submitted: Stack) -> None:
        response = submitted.client.post("/v1/captures", json=text_envelope(sneaky="value"))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_non_text_capture_behaves_exactly_as_before(self, stack: Stack) -> None:
        """Replay is defined for the one materialization this build supports."""
        body = text_envelope(
            payload={"type": "image", "mime_type": "image/png", "file_ref": "blob://shot"}
        )

        first = stack.client.post("/v1/captures", json=body)
        second = stack.client.post("/v1/captures", json=body)

        assert (first.status_code, second.status_code) == (422, 422)
        assert first.json()["error"]["code"] == "unsupported_payload"
        assert stack.record_store.get_or_none(CAPTURE_ID) is None
