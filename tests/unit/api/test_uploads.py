"""``POST /v1/uploads`` — staging immutable bytes, and doing nothing else.

The route exists because a PDF cannot travel inside a JSON envelope. What these
tests spend most of their effort on is therefore not what it *does* — one store
call — but everything it must **not** do: no capture, no lifecycle, no
processing, no content, and above all no authority granted to the filename a
client attached.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from core.contracts import CaptureRecord, ContentObject, RawObjectRef
from core.storage import RawObjectWriteError, build_raw_ref
from tests.pdfs import one_page_pdf, two_page_pdf
from tests.unit.api.conftest import Stack, build_stack
from tests.unit.api.doubles import FakeRawObjectStore

PDF_MIME = "application/pdf"


def upload(
    client: TestClient, data: bytes, *, filename: str = "paper.pdf", mime: str = PDF_MIME
) -> Response:
    """POST bytes as a multipart file part, exactly as ``curl -F`` would."""
    return client.post("/v1/uploads", files={"file": (filename, data, mime)})


def test_uploading_a_pdf_is_answered_200(client: TestClient) -> None:
    """200, not 201.

    The raw store deduplicates by content and does not report whether this
    request created a file or found identical bytes already there, so "created"
    is a fact the server does not have. 200 says the true thing: the object is
    available.
    """
    assert upload(client, one_page_pdf()).status_code == 200


def test_the_response_carries_the_logical_raw_reference(client: TestClient) -> None:
    """``file_ref`` is the store's own ``sha256:<digest>`` handle, ready to paste."""
    data = one_page_pdf()

    body = upload(client, data).json()

    assert body["file_ref"] == build_raw_ref(hashlib.sha256(data).hexdigest())


def test_the_response_carries_the_digest_of_the_exact_bytes(client: TestClient) -> None:
    """A client can hash the file it sent and check the server agrees."""
    data = two_page_pdf()

    body = upload(client, data).json()

    assert body["sha256"] == hashlib.sha256(data).hexdigest()


def test_the_two_reference_forms_agree(client: TestClient) -> None:
    body = upload(client, one_page_pdf()).json()

    assert body["file_ref"] == f"sha256:{body['sha256']}"


def test_the_response_echoes_the_declared_mime_type(client: TestClient) -> None:
    """Descriptive only: it says what the upload claimed, and decides nothing."""
    assert upload(client, one_page_pdf()).json()["mime_type"] == PDF_MIME


def test_the_response_has_no_other_fields(client: TestClient) -> None:
    """No capture id, no status, no filename, no "created" flag."""
    assert set(upload(client, one_page_pdf()).json()) == {"file_ref", "sha256", "mime_type"}


def test_the_exact_uploaded_bytes_are_retrievable_from_the_raw_store(stack: Stack) -> None:
    """Byte for byte, through the reference the response handed back."""
    data = two_page_pdf()

    body = upload(stack.client, data).json()

    stored = stack.raw_store.read_bytes(
        RawObjectRef(id=body["sha256"], sha256=body["sha256"], ref=body["file_ref"])
    )
    assert stored == data


def test_the_same_bytes_twice_yield_the_same_reference(client: TestClient) -> None:
    """Content addressing, observed from outside: no second identity is minted."""
    data = one_page_pdf()

    first = upload(client, data).json()
    second = upload(client, data).json()

    assert first == second


def test_different_bytes_yield_different_references(client: TestClient) -> None:
    first = upload(client, one_page_pdf()).json()
    second = upload(client, two_page_pdf()).json()

    assert first["file_ref"] != second["file_ref"]


def test_the_declared_mime_type_does_not_affect_identity(client: TestClient) -> None:
    """Identity is the bytes. A label is not part of them."""
    data = one_page_pdf()

    as_pdf = upload(client, data, mime=PDF_MIME).json()
    as_octets = upload(client, data, mime="application/octet-stream").json()

    assert as_pdf["sha256"] == as_octets["sha256"]
    assert as_pdf["file_ref"] == as_octets["file_ref"]


def test_the_filename_does_not_affect_identity(client: TestClient) -> None:
    data = one_page_pdf()

    first = upload(client, data, filename="paper.pdf").json()
    second = upload(client, data, filename="something-else-entirely.bin").json()

    assert first["file_ref"] == second["file_ref"]


@pytest.mark.parametrize(
    "filename",
    [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "/etc/shadow",
        "~/.ssh/id_rsa",
        "paper.pdf\x00.exe",
        "....//....//evil",
    ],
    ids=["dotdot", "windows", "absolute", "home", "nul", "doubled"],
)
def test_a_hostile_filename_reaches_no_storage_path(stack: Stack, filename: str) -> None:
    """The filename is not sanitized; it is never read.

    Storage layout is derived from a digest the store computed itself, so a
    traversal sequence in a filename is not dangerous input that has been made
    safe — it is input with nowhere to go. The proof is that the object lands at
    exactly the same content-addressed reference as a benign name.
    """
    data = one_page_pdf()

    hostile = upload(stack.client, data, filename=filename)
    benign = upload(stack.client, data, filename="paper.pdf")

    assert hostile.status_code == 200
    assert hostile.json() == benign.json()
    assert hostile.json()["file_ref"] == build_raw_ref(hashlib.sha256(data).hexdigest())


def test_the_filename_is_not_echoed_anywhere_in_the_response(client: TestClient) -> None:
    """Echoing it back would suggest the server had kept it. It has not."""
    response = upload(client, one_page_pdf(), filename="private-tax-return-2025.pdf")

    assert "private-tax-return" not in response.text


def test_the_response_never_echoes_the_file_contents(client: TestClient) -> None:
    """A capture surface that quotes uploads back is a capture surface that leaks."""
    data = one_page_pdf()

    response = upload(client, data)

    assert data[:16] not in response.content
    assert "canonical object is not Markdown" not in response.text
    assert len(response.content) < len(data)


class TestUploadingIsNotCapturing:
    """The whole reason this route is separate: it starts no lifecycle."""

    def test_no_capture_record_is_created(self, stack: Stack) -> None:
        upload(stack.client, one_page_pdf())

        assert stack.record_store.stored_capture_ids() == set()

    def test_no_content_object_is_created(self, stack: Stack) -> None:
        upload(stack.client, one_page_pdf())

        assert stack.content_store.stored_content_ids() == set()

    def test_no_processor_runs(self) -> None:
        """A processor set that explodes if consulted, and it never is."""

        class ExplodingProcessor:
            name = "exploding"
            version = "0.1"

            def supports(self, capture: CaptureRecord) -> bool:
                raise AssertionError("the upload route routed something")

            def process(self, capture: CaptureRecord) -> ContentObject:
                raise AssertionError("the upload route processed something")

        stack = build_stack(processors=[ExplodingProcessor()])
        with stack.client as client:
            assert upload(client, one_page_pdf()).status_code == 200

    def test_the_uploaded_object_is_not_readable_as_a_capture(self, stack: Stack) -> None:
        """The digest is not a capture id, and nothing pretends otherwise."""
        digest = upload(stack.client, one_page_pdf()).json()["sha256"]

        assert stack.client.get(f"/v1/captures/{digest}").status_code == 404


class TestUploadFailures:
    def test_a_raw_store_outage_is_a_safe_storage_error(self) -> None:
        stack = build_stack(
            raw_store=FakeRawObjectStore(fail_with=RawObjectWriteError("/tmp/staging/x is full"))
        )

        with stack.client as client:
            response = upload(client, one_page_pdf())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"

    def test_a_raw_store_outage_leaks_no_path_or_stack(self) -> None:
        stack = build_stack(
            raw_store=FakeRawObjectStore(fail_with=RawObjectWriteError("/tmp/staging/x is full"))
        )

        with stack.client as client:
            response = upload(client, one_page_pdf())

        body = response.text
        assert "/tmp" not in body
        assert "Traceback" not in body
        assert "RawObjectWriteError" not in body

    def test_a_request_with_no_file_part_is_rejected(self, client: TestClient) -> None:
        response = client.post("/v1/uploads")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_json_body_is_not_accepted_as_an_upload(self, client: TestClient) -> None:
        response = client.post("/v1/uploads", json={"file": "not multipart"})

        assert response.status_code == 422


class TestTheUploadIsDocumented:
    def test_the_openapi_schema_declares_a_multipart_body(self, stack: Stack) -> None:
        schema = stack.client.get("/openapi.json").json()

        content = schema["paths"]["/v1/uploads"]["post"]["requestBody"]["content"]

        assert list(content) == ["multipart/form-data"]

    def test_the_multipart_body_declares_one_required_binary_file_part(self, stack: Stack) -> None:
        """A generated client sends a file, not a JSON string that looks like one."""
        schema = stack.client.get("/openapi.json").json()
        content = schema["paths"]["/v1/uploads"]["post"]["requestBody"]["content"]

        reference = content["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[-1]
        body = schema["components"]["schemas"][reference]

        assert body["required"] == ["file"]
        assert body["properties"]["file"]["contentMediaType"] == "application/octet-stream"

    def test_the_success_response_is_the_upload_model(self, stack: Stack) -> None:
        schema = stack.client.get("/openapi.json").json()

        responses = schema["paths"]["/v1/uploads"]["post"]["responses"]
        reference = responses["200"]["content"]["application/json"]["schema"]["$ref"]

        assert reference.endswith("/UploadedObjectResponse")

    def test_the_route_documents_the_shared_error_envelope(self, stack: Stack) -> None:
        schema = stack.client.get("/openapi.json").json()

        responses = schema["paths"]["/v1/uploads"]["post"]["responses"]

        assert {"4XX", "5XX"} <= set(responses)
