"""The HTTP error contract: every mapped core failure, and what it must not say.

Two properties matter here, and they pull in opposite directions:

* a client must be able to **branch on the code**, which means every mapped
  failure has to arrive as its documented status and code;
* a 5xx must **give nothing away**, which means the core error's own message —
  which names a SQLite file or a staging directory — must not reach the wire.

The table is exercised through a real request rather than by calling the
handlers, so what is asserted is what a client would actually receive.
"""

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureStatus
from core.intake import InvalidCaptureEnvelopeError, UnsupportedCapturePayloadError
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordCorruptError,
    CaptureRecordNotFoundError,
    CaptureRecordPersistenceError,
    ContentObjectAlreadyExistsError,
    ContentObjectCorruptError,
    ContentObjectNotFoundError,
    ContentObjectPersistenceError,
)
from core.processing import (
    AmbiguousProcessorError,
    InvalidCaptureProcessingStateError,
    NoProcessorError,
    ProcessingError,
    ProcessingInputError,
    ProcessingOutputError,
    ProcessorRoutingError,
    TextDecodingError,
)
from core.storage import (
    InvalidRawObjectRefError,
    RawObjectNotFoundError,
    RawObjectStoreError,
    RawObjectWriteError,
)
from tests.unit.api.builders import CAPTURE_ID, text_envelope
from tests.unit.api.conftest import build_stack
from tests.unit.api.doubles import (
    FailingProcessor,
    FakeCaptureRecordStore,
    FakeContentObjectStore,
    FakeRawObjectStore,
)
from unimem_api.errors import ERROR_MAPPINGS, HttpError

#: A path-like string planted inside every injected error message. If any of it
#: reaches a 5xx body, the safe-message rule has been broken.
BACKEND_DETAIL = "/var/lib/unimem/private/unimem.sqlite3"


def raising_client(exc: Exception) -> TestClient:
    """An app whose capture read fails with exactly ``exc``.

    ``GET /v1/captures/{id}`` is the shortest path from a raised exception to a
    rendered response, so it is the probe used for the whole table — including
    for errors a capture read would never raise on its own. The handlers are
    registered per exception type and know nothing about routes, so which route
    raised is immaterial to what is being checked.
    """
    stack = build_stack(record_store=FakeCaptureRecordStore(fail_get_with=exc))
    return stack.client


def probe(exc: Exception) -> tuple[int, dict[str, str]]:
    """Raise ``exc`` inside a request and return the status and the error body."""
    response = raising_client(exc).get(f"/v1/captures/{CAPTURE_ID}")
    body = response.json()
    assert set(body) == {"error"}, "every deliberate failure uses the error envelope"
    error: dict[str, str] = body["error"]
    return response.status_code, error


#: One instance per mapped type, each carrying a message that would be a leak.
MAPPED_ERRORS: list[Exception] = [
    CaptureRecordAlreadyExistsError(f"already stored, per {BACKEND_DETAIL}"),
    ContentObjectAlreadyExistsError(f"already has content, per {BACKEND_DETAIL}"),
    InvalidCaptureProcessingStateError(f"wrong state, per {BACKEND_DETAIL}"),
    CaptureRecordNotFoundError(f"no such record in {BACKEND_DETAIL}"),
    ContentObjectNotFoundError(f"no such content in {BACKEND_DETAIL}"),
    UnsupportedCapturePayloadError(f"unsupported, per {BACKEND_DETAIL}"),
    InvalidCaptureEnvelopeError(f"inconsistent, per {BACKEND_DETAIL}"),
    ProcessingInputError(f"unusable input at {BACKEND_DETAIL}"),
    TextDecodingError(f"not utf-8, read from {BACKEND_DETAIL}"),
    ProcessingOutputError(f"wrong capture, written to {BACKEND_DETAIL}"),
    ProcessorRoutingError(f"routing failed near {BACKEND_DETAIL}"),
    NoProcessorError(f"no processor, configured at {BACKEND_DETAIL}"),
    AmbiguousProcessorError(f"two processors, configured at {BACKEND_DETAIL}"),
    CaptureRecordCorruptError(f"corrupt snapshot in {BACKEND_DETAIL}"),
    ContentObjectCorruptError(f"corrupt content in {BACKEND_DETAIL}"),
    CaptureRecordPersistenceError(f"the database at {BACKEND_DETAIL} could not read"),
    ContentObjectPersistenceError(f"the database at {BACKEND_DETAIL} could not read"),
    RawObjectStoreError(f"the raw store at {BACKEND_DETAIL} could not read"),
]


def mapping_for(exc: Exception) -> HttpError:
    for exception_type, mapping in ERROR_MAPPINGS:
        if type(exc) is exception_type:
            return mapping
    raise AssertionError(f"{type(exc).__name__} is not in the error table")


class TestTheMappedMatrix:
    """Every row of the table, over a real request."""

    @pytest.mark.parametrize("exc", MAPPED_ERRORS, ids=lambda exc: type(exc).__name__)
    def test_status_and_code_match_the_table(self, exc: Exception) -> None:
        expected = mapping_for(exc)

        status_code, error = probe(exc)

        assert status_code == expected.status_code
        assert error["code"] == expected.code

    @pytest.mark.parametrize("exc", MAPPED_ERRORS, ids=lambda exc: type(exc).__name__)
    def test_the_message_is_never_empty(self, exc: Exception) -> None:
        _, error = probe(exc)

        assert error["message"].strip()

    @pytest.mark.parametrize("exc", MAPPED_ERRORS, ids=lambda exc: type(exc).__name__)
    def test_every_row_is_covered_by_this_suite(self, exc: Exception) -> None:
        """The parametrization and the table cannot drift apart."""
        assert mapping_for(exc) in {mapping for _, mapping in ERROR_MAPPINGS}

    def test_every_table_row_has_an_error_in_this_suite(self) -> None:
        assert {type(exc) for exc in MAPPED_ERRORS} == {
            exception_type for exception_type, _ in ERROR_MAPPINGS
        }


class TestFiveHundredsGiveNothingAway:
    """A 5xx says what the client can do, and nothing about the server."""

    @pytest.mark.parametrize(
        "exc",
        [exc for exc in MAPPED_ERRORS if mapping_for(exc).status_code >= 500],
        ids=lambda exc: type(exc).__name__,
    )
    def test_the_backend_detail_does_not_reach_the_body(self, exc: Exception) -> None:
        response = raising_client(exc).get(f"/v1/captures/{CAPTURE_ID}")

        assert response.status_code >= 500
        assert BACKEND_DETAIL not in response.text
        assert "unimem.sqlite3" not in response.text
        assert "/var/lib" not in response.text

    @pytest.mark.parametrize(
        "exc",
        [exc for exc in MAPPED_ERRORS if mapping_for(exc).status_code >= 500],
        ids=lambda exc: type(exc).__name__,
    )
    def test_the_message_is_the_fixed_public_one(self, exc: Exception) -> None:
        expected = mapping_for(exc)

        _, error = probe(exc)

        assert expected.message is not None
        assert error["message"] == expected.message

    @pytest.mark.parametrize(
        "exc",
        [exc for exc in MAPPED_ERRORS if mapping_for(exc).status_code >= 500],
        ids=lambda exc: type(exc).__name__,
    )
    def test_no_python_type_name_or_traceback_reaches_the_body(self, exc: Exception) -> None:
        response = raising_client(exc).get(f"/v1/captures/{CAPTURE_ID}")

        assert type(exc).__name__ not in response.text
        assert "Traceback" not in response.text
        assert 'File "' not in response.text


class TestFourHundredsExplainTheRequest:
    """A 4xx passes core's own message through, because the client can act on it."""

    @pytest.mark.parametrize(
        "exc",
        [exc for exc in MAPPED_ERRORS if mapping_for(exc).status_code < 500],
        ids=lambda exc: type(exc).__name__,
    )
    def test_the_message_is_the_core_error_text(self, exc: Exception) -> None:
        _, error = probe(exc)

        assert error["message"] == str(exc)


class TestSubclassesResolveThroughTheirBase:
    """Two base classes carry rows on purpose; their subclasses inherit them."""

    @pytest.mark.parametrize(
        "exc",
        [
            RawObjectNotFoundError("object missing"),
            InvalidRawObjectRefError("bad ref"),
            RawObjectWriteError("write failed"),
        ],
        ids=lambda exc: type(exc).__name__,
    )
    def test_raw_store_subclasses_are_503(self, exc: Exception) -> None:
        status_code, error = probe(exc)

        assert status_code == 503
        assert error["code"] == "storage_unavailable"

    @pytest.mark.parametrize(
        "exc",
        [NoProcessorError("none"), AmbiguousProcessorError("two")],
        ids=lambda exc: type(exc).__name__,
    )
    def test_routing_subclasses_are_500(self, exc: Exception) -> None:
        status_code, error = probe(exc)

        assert status_code == 500
        assert error["code"] == "processing_configuration_error"


class TestUnmappedFailuresStayUnhandled:
    """Nothing is adopted by a base class that happens to be nearby."""

    @pytest.mark.parametrize(
        "exc",
        [ProcessingError("a bare processing error"), RuntimeError("a bug")],
        ids=lambda exc: type(exc).__name__,
    )
    def test_an_unmapped_error_is_not_translated(self, exc: Exception) -> None:
        """It propagates as an unhandled server error, which is what a bug is.

        ``ProcessingError`` is the interesting one: its subclasses are mapped
        and it is not, so an unmapped future subclass cannot silently inherit a
        friendly 422.
        """
        client = raising_client(exc)

        with pytest.raises(type(exc)):
            client.get(f"/v1/captures/{CAPTURE_ID}")


class TestFailuresThroughTheRealPipeline:
    """The mappings that matter most, reached the way a client would reach them."""

    def test_a_storage_failure_is_503_and_invents_no_lifecycle_state(self) -> None:
        stack = build_stack(
            record_store=FakeCaptureRecordStore(
                fail_replace_with=CaptureRecordPersistenceError(
                    f"the capture record database at {BACKEND_DETAIL} could not store"
                )
            )
        )

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert BACKEND_DETAIL not in response.text
        # RECEIVED, not FAILED: the failure was the run's, not the capture's.
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.RECEIVED

    def test_a_raw_store_failure_is_503(self) -> None:
        stack = build_stack(
            raw_store=FakeRawObjectStore(
                fail_with=RawObjectWriteError(f"could not stage under {BACKEND_DETAIL}")
            )
        )

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 503
        assert BACKEND_DETAIL not in response.text
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.RECEIVED

    def test_a_routing_failure_is_a_safe_500(self) -> None:
        stack = build_stack(processors=[])

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "processing_configuration_error"
        # The capture is untouched by routing: it stays where intake left it.
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.STORED

    def test_a_content_conflict_is_409(self) -> None:
        stack = build_stack(
            content_store=FakeContentObjectStore(
                fail_create_with=ContentObjectAlreadyExistsError(
                    f"capture {CAPTURE_ID!r} already has a stored content object"
                )
            )
        )

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "content_conflict"
        # Not completed on the strength of content somebody else wrote.
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.PROCESSING

    @pytest.mark.parametrize(
        "failure",
        [
            ProcessingInputError("the raw object is empty"),
            TextDecodingError("the raw object is not valid utf-8"),
            ProcessingOutputError("content belongs to another capture"),
        ],
        ids=lambda exc: type(exc).__name__,
    )
    def test_a_processing_verdict_is_422_and_leaves_a_durable_failure(
        self, failure: ProcessingError
    ) -> None:
        stack = build_stack(processors=[FailingProcessor(failure)])

        response = stack.client.post("/v1/captures", json=text_envelope())

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.FAILED
