"""A real PDF, through the real application, over real HTTP, on a real directory.

Nothing is faked. ``build_local_app`` wires ``LocalRawObjectStore``,
``SqliteCaptureRecordStore``, ``SqliteContentObjectStore``, ``CaptureIntake``,
``TextProcessor``, ``WebpageProcessor``, ``PdfProcessor``, ``ProcessorRouter``,
and ``ProcessingOrchestrator`` against a temporary directory, and every
assertion is made against what is on disk or what came back over the wire.

This is the file that has to be true for Phase 3 PR 1 to mean anything, and it
is deliberately the whole two-step flow rather than either half::

    PDF bytes
        -> POST /v1/uploads          (multipart)   -> file_ref
        -> POST /v1/captures         (the canonical envelope, naming that ref)
        -> 201, complete
        -> GET  /v1/captures/{id}                  -> COMPLETE
        -> GET  /v1/captures/{id}/content          -> a document ContentObject

:class:`TestSurvivingARestart` is where durability stops being a claim: the whole
application is thrown away and rebuilt over the same directory.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.contracts.enums import AssetRole, CapturePayloadType, ProvenanceSourceType, SegmentType
from tests import pdfs
from tests.unit.api.builders import CAPTURE_ID as TEXT_CAPTURE_ID
from tests.unit.api.builders import (
    WEBPAGE_CAPTURE_ID,
    text_envelope,
    webpage_envelope,
)
from unimem_api import RAW_DIRNAME, build_local_app

CAPTURE_ID = "cap_http_pdf_01"
PDF_MIME = "application/pdf"
CAPTURED_AT = "2026-01-02T03:04:05+00:00"

#: The document every test uses unless it says otherwise: text on page one, a
#: blank page two, text on page three. It is the fixture that makes physical
#: page number and canonical position visibly different facts.
DOCUMENT = pdfs.blank_middle_pdf()
DOCUMENT_DIGEST = hashlib.sha256(DOCUMENT).hexdigest()


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


def stage(client: TestClient, data: bytes = DOCUMENT, *, filename: str = "paper.pdf") -> str:
    """Upload bytes and return the ``file_ref`` the server handed back."""
    response = client.post("/v1/uploads", files={"file": (filename, data, PDF_MIME)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def document_envelope(
    file_ref: str, *, payload: dict[str, object] | None = None, **overrides: object
) -> dict[str, object]:
    """The canonical envelope a client assembles, as a JSON-ready dictionary.

    Built as a dictionary rather than dumped from a model, for the same reason
    every other HTTP test here does: a real client posts JSON it wrote itself,
    and this has to validate as a document that was never a Python object.

    ``payload`` merges into the payload rather than replacing it, because most
    tests vary one field of it and repeating the other three is how a fixture
    drifts away from the thing it is supposed to represent.
    """
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "document", "mime_type": PDF_MIME, "file_ref": file_ref}
        | (payload or {}),
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
        response = client.post("/v1/captures", json=document_envelope(file_ref))
        assert response.status_code == 201, response.text
        return file_ref

    def test_the_upload_returned_a_content_addressed_reference(self, submitted: str) -> None:
        assert submitted == f"sha256:{DOCUMENT_DIGEST}"

    def test_the_upload_digest_is_the_sha256_of_the_uploaded_bytes(
        self, client: TestClient
    ) -> None:
        body = client.post("/v1/uploads", files={"file": ("paper.pdf", DOCUMENT, PDF_MIME)}).json()

        assert body["sha256"] == hashlib.sha256(DOCUMENT).hexdigest()

    def test_the_capture_response_says_complete(self, client: TestClient) -> None:
        file_ref = stage(client)
        response = client.post(
            "/v1/captures", json=document_envelope(file_ref, id="cap_http_pdf_second")
        )

        assert response.status_code == 201
        assert response.json()["status"] == "complete"
        assert response.json()["capture_id"] == "cap_http_pdf_second"

    def test_the_capture_record_is_complete(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_capture_record_is_a_document(self, client: TestClient) -> None:
        assert record_of(client).payload_type is CapturePayloadType.DOCUMENT

    def test_the_capture_record_points_at_the_staged_object(
        self, client: TestClient, submitted: str
    ) -> None:
        raw_object = record_of(client).raw_object

        assert raw_object is not None
        assert raw_object.ref == submitted
        assert raw_object.sha256 == DOCUMENT_DIGEST
        assert raw_object.mime_type == PDF_MIME

    def test_the_content_object_is_a_document(self, client: TestClient) -> None:
        assert content_of(client).type is ContentType.DOCUMENT

    def test_the_expected_page_text_is_there(self, client: TestClient) -> None:
        texts = [segment.text for segment in content_of(client).segments]

        assert texts == [pdfs.TWO_PAGE_TEXT_EXTRACTED, pdfs.TWO_PAGE_SECOND_EXTRACTED]

    def test_the_page_numbers_are_the_physical_ones(self, client: TestClient) -> None:
        """Page two was blank, so the pages are 1 and 3."""
        segments = content_of(client).segments

        assert [segment.spatial.page for segment in segments] == [1, 3]  # type: ignore[union-attr]

    def test_the_positions_are_contiguous_canonical_reading_order(self, client: TestClient) -> None:
        segments = content_of(client).segments

        assert [segment.position for segment in segments] == [0, 1]

    def test_every_segment_is_text_from_the_original(self, client: TestClient) -> None:
        segments = content_of(client).segments

        assert {segment.type for segment in segments} == {SegmentType.TEXT}
        assert {segment.provenance.source_type for segment in segments} == {
            ProvenanceSourceType.ORIGINAL
        }

    def test_the_processor_is_recorded(self, client: TestClient) -> None:
        content = content_of(client)

        assert [(record.processor, record.processor_version) for record in content.processing] == [
            ("pdf", "0.1")
        ]

    def test_there_is_one_original_asset(self, client: TestClient, submitted: str) -> None:
        content = content_of(client)

        assert [asset.role for asset in content.assets] == [AssetRole.ORIGINAL]
        assert content.assets[0].ref == submitted
        assert content.assets[0].sha256 == DOCUMENT_DIGEST
        assert content.assets[0].mime_type == PDF_MIME

    def test_the_stored_original_is_the_uploaded_pdf_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_raw_digest_is_the_sha256_of_the_uploaded_pdf(self, data_dir: Path) -> None:
        stored = raw_path(data_dir, DOCUMENT_DIGEST).read_bytes()

        assert hashlib.sha256(stored).hexdigest() == DOCUMENT_DIGEST

    def test_exactly_one_raw_object_exists(self, data_dir: Path) -> None:
        """The upload wrote it; intake did not write a second copy."""
        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]

        assert len(objects) == 1


class TestUploadingIsNotCapturing:
    def test_an_upload_alone_leaves_no_capture(self, client: TestClient) -> None:
        stage(client)

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_an_upload_alone_leaves_no_content(self, client: TestClient) -> None:
        digest = stage(client).split(":", 1)[1]

        assert client.get(f"/v1/captures/{digest}/content").status_code == 404

    def test_an_unclaimed_upload_stays_on_disk(self, client: TestClient, data_dir: Path) -> None:
        """Deliberate: an object nobody references is immutable and harmless.

        There is no lease, expiry, upload table, or collector in this phase, and
        this is the test that says so on purpose rather than by omission.
        """
        stage(client)

        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_same_pdf_uploaded_twice_returns_the_same_reference(
        self, client: TestClient, data_dir: Path
    ) -> None:
        first = stage(client)
        second = stage(client, filename="a-completely-different-name.pdf")

        assert first == second
        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]
        assert len(objects) == 1


class TestTitlePrecedence:
    def test_the_pdf_metadata_title_is_used_when_the_capture_sends_none(
        self, client: TestClient
    ) -> None:
        file_ref = stage(client, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).title == pdfs.METADATA_TITLE

    def test_the_submitted_title_wins_over_the_pdf_metadata(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))
        client.post(
            "/v1/captures",
            json=document_envelope(file_ref, payload={"title": "What I called it"}),
        )

        assert content_of(client).title == "What I called it"

    def test_the_submitted_title_stays_on_the_capture_record(self, client: TestClient) -> None:
        """The PDF's own metadata never rewrites what the submitter said."""
        file_ref = stage(client, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))
        client.post(
            "/v1/captures",
            json=document_envelope(file_ref, payload={"title": "What I called it"}),
        )

        assert record_of(client).title == "What I called it"

    def test_the_metadata_title_does_not_reach_the_capture_record(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.two_page_pdf(title=pdfs.METADATA_TITLE))
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert record_of(client).title is None

    def test_the_uploaded_filename_never_becomes_the_title(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.two_page_pdf(), filename="Quarterly Report Final v3.pdf")
        client.post("/v1/captures", json=document_envelope(file_ref))

        content = content_of(client)

        assert content.title is None
        assert "Quarterly Report" not in client.get(f"/v1/captures/{CAPTURE_ID}").text
        assert "Quarterly Report" not in client.get(f"/v1/captures/{CAPTURE_ID}/content").text


class TestReferencesTheServerRefuses:
    """A ``file_ref`` is resolved against the raw store, and never dereferenced."""

    def test_a_reference_to_material_that_was_never_staged_is_422(self, client: TestClient) -> None:
        response = client.post("/v1/captures", json=document_envelope(f"sha256:{'f' * 64}"))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "capture_material_unavailable"

    def test_that_refusal_leaves_no_capture_record(self, client: TestClient) -> None:
        client.post("/v1/captures", json=document_envelope(f"sha256:{'f' * 64}"))

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_that_refusal_leaves_no_content(self, client: TestClient) -> None:
        client.post("/v1/captures", json=document_envelope(f"sha256:{'f' * 64}"))

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_an_absolute_local_path_is_refused_and_never_opened(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """The path exists and holds a perfectly good PDF. It is still refused."""
        real = tmp_path / "on-disk.pdf"
        real.write_bytes(DOCUMENT)

        response = client.post("/v1/captures", json=document_envelope(str(real)))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    @pytest.mark.parametrize(
        "file_ref",
        [
            "file:///etc/passwd",
            "http://127.0.0.1:1/paper.pdf",
            "https://example.invalid/paper.pdf",
            "s3://bucket/paper.pdf",
            "/etc/passwd",
            "~/Documents/paper.pdf",
        ],
        ids=["file-url", "http", "https", "s3", "etc-passwd", "home"],
    )
    def test_no_url_is_fetched_and_no_path_is_read(self, client: TestClient, file_ref: str) -> None:
        """Each is refused as a reference this build does not resolve.

        The HTTP ones matter most: an unreachable host would take a connection
        timeout to fail, and these return immediately because nothing is dialled.
        """
        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_the_refusal_does_not_echo_the_reference(self, client: TestClient) -> None:
        response = client.post(
            "/v1/captures", json=document_envelope("/home/someone/private/taxes.pdf")
        )

        assert "taxes.pdf" not in response.text
        assert "/home/someone" not in response.text

    @pytest.mark.parametrize(
        "mime_type",
        ["application/epub+zip", "text/plain", "application/octet-stream"],
        ids=["epub", "text", "octets"],
    )
    def test_a_document_of_another_format_is_refused_at_intake(
        self, client: TestClient, mime_type: str
    ) -> None:
        """It never reaches the router, so nothing strands mid-lifecycle."""
        file_ref = stage(client)

        response = client.post(
            "/v1/captures", json=document_envelope(file_ref, payload={"mime_type": mime_type})
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404


class TestDocumentsThatFailProcessing:
    def test_a_textless_pdf_is_a_safe_422(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.textless_pdf())

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_a_textless_pdf_leaves_a_truthful_failed_capture(self, client: TestClient) -> None:
        """Not an empty ``complete``: the document was not remembered."""
        file_ref = stage(client, pdfs.textless_pdf())
        client.post("/v1/captures", json=document_envelope(file_ref))

        record = record_of(client)

        assert record.status is CaptureStatus.FAILED
        assert record.error is not None

    def test_a_textless_pdf_produces_no_content_object(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.textless_pdf())
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_a_textless_pdf_keeps_its_original(self, client: TestClient, data_dir: Path) -> None:
        data = pdfs.textless_pdf()
        file_ref = stage(client, data)
        client.post("/v1/captures", json=document_envelope(file_ref))

        digest = file_ref.split(":", 1)[1]

        assert raw_path(data_dir, digest).read_bytes() == data

    @pytest.mark.parametrize(
        ("label", "data"),
        [("corrupt", pdfs.corrupt_pdf()), ("encrypted", pdfs.encrypted_pdf())],
        ids=["corrupt", "encrypted"],
    )
    def test_an_unreadable_document_fails_safely(
        self, client: TestClient, label: str, data: bytes
    ) -> None:
        file_ref = stage(client, data)

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert record_of(client).status is CaptureStatus.FAILED

    def test_a_corrupt_document_leaks_no_stack_or_path(self, client: TestClient) -> None:
        file_ref = stage(client, pdfs.corrupt_pdf())

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        body = response.text
        assert "Traceback" not in body
        assert "pypdf" not in body
        assert "/tmp" not in body
        assert "site-packages" not in body


class TestReplayIsNotGeneralized:
    """Document idempotency is not designed yet, and this states it plainly."""

    def test_a_resubmitted_document_capture_id_is_409(self, client: TestClient) -> None:
        file_ref = stage(client)
        assert client.post("/v1/captures", json=document_envelope(file_ref)).status_code == 201

        second = client.post("/v1/captures", json=document_envelope(file_ref))

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "capture_already_exists"

    def test_the_first_capture_is_untouched_by_the_conflict(self, client: TestClient) -> None:
        file_ref = stage(client)
        first = client.post("/v1/captures", json=document_envelope(file_ref)).json()

        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).id == first["content_id"]


class TestTwoCapturesOfOneDocument:
    def test_a_new_capture_id_produces_a_distinct_content_object(self, client: TestClient) -> None:
        file_ref = stage(client)
        first = client.post("/v1/captures", json=document_envelope(file_ref)).json()

        second = client.post(
            "/v1/captures", json=document_envelope(file_ref, id="cap_http_pdf_02")
        ).json()

        assert first["content_id"] != second["content_id"]

    def test_both_captures_point_at_the_same_raw_digest(self, client: TestClient) -> None:
        file_ref = stage(client)
        client.post("/v1/captures", json=document_envelope(file_ref))
        client.post("/v1/captures", json=document_envelope(file_ref, id="cap_http_pdf_02"))

        first = content_of(client)
        second = content_of(client, "cap_http_pdf_02")

        assert first.original.sha256 == second.original.sha256 == DOCUMENT_DIGEST
        assert first.id != second.id
        assert first.assets[0].id != second.assets[0].id

    def test_only_one_raw_object_is_on_disk(self, client: TestClient, data_dir: Path) -> None:
        file_ref = stage(client)
        client.post("/v1/captures", json=document_envelope(file_ref))
        client.post("/v1/captures", json=document_envelope(file_ref, id="cap_http_pdf_02"))

        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]

        assert len(objects) == 1


class TestSurvivingARestart:
    """The whole application is thrown away and rebuilt over the same directory."""

    def test_the_capture_record_is_still_complete(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)
            first.post("/v1/captures", json=document_envelope(file_ref))

        with TestClient(build_local_app(data_dir)) as second:
            assert record_of(second).status is CaptureStatus.COMPLETE

    def test_the_content_object_is_still_there(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)
            content_id = first.post("/v1/captures", json=document_envelope(file_ref)).json()[
                "content_id"
            ]

        with TestClient(build_local_app(data_dir)) as second:
            reloaded = content_of(second)

        assert reloaded.id == content_id
        assert reloaded.type is ContentType.DOCUMENT

    def test_the_pages_are_still_there(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)
            first.post("/v1/captures", json=document_envelope(file_ref))

        with TestClient(build_local_app(data_dir)) as second:
            segments = content_of(second).segments

        assert [segment.text for segment in segments] == [
            pdfs.TWO_PAGE_TEXT_EXTRACTED,
            pdfs.TWO_PAGE_SECOND_EXTRACTED,
        ]
        assert [segment.spatial.page for segment in segments] == [1, 3]  # type: ignore[union-attr]

    def test_the_original_pdf_is_still_retrievable(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            stage(first)

        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_same_pdf_uploaded_after_a_restart_returns_the_same_reference(
        self, data_dir: Path
    ) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            before = stage(first)

        with TestClient(build_local_app(data_dir)) as second:
            after = stage(second)

        assert before == after

    def test_a_capture_can_reference_material_staged_before_the_restart(
        self, data_dir: Path
    ) -> None:
        """The staged object is durable, so the two steps need not share a process."""
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)

        with TestClient(build_local_app(data_dir)) as second:
            response = second.post("/v1/captures", json=document_envelope(file_ref))

            assert response.status_code == 201
            assert content_of(second).type is ContentType.DOCUMENT


class TestTheOtherModalitiesAreUnchanged:
    def test_a_text_capture_still_works(self, client: TestClient) -> None:
        assert client.post("/v1/captures", json=text_envelope()).status_code == 201
        assert content_of(client, TEXT_CAPTURE_ID).type is ContentType.TEXT

    def test_a_webpage_capture_still_works(self, client: TestClient) -> None:
        assert client.post("/v1/captures", json=webpage_envelope()).status_code == 201
        assert content_of(client, WEBPAGE_CAPTURE_ID).type is ContentType.WEB

    def test_a_text_capture_still_replays(self, client: TestClient) -> None:
        """Phase 1's replay guarantee is untouched by any of this."""
        assert client.post("/v1/captures", json=text_envelope()).status_code == 201
        assert client.post("/v1/captures", json=text_envelope()).status_code == 200
