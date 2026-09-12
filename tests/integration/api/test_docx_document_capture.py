"""A real DOCX, through the real application, over real HTTP, on a real directory.

Nothing is faked. ``build_local_app`` wires ``LocalRawObjectStore``,
``SqliteCaptureRecordStore``, ``SqliteContentObjectStore``, ``CaptureIntake``,
``TextProcessor``, ``WebpageProcessor``, ``PdfProcessor``, ``DocxProcessor``,
``ProcessorRouter``, and ``ProcessingOrchestrator`` against a temporary
directory, and every assertion is made against what is on disk or what came back
over the wire.

This is the file that has to be true for Phase 3 PR 2 to mean anything, and what
makes it worth writing is how little of it is new::

    DOCX bytes
        -> POST /v1/uploads          (the same route, unchanged)   -> file_ref
        -> POST /v1/captures         (the same envelope, a different mime_type)
        -> 201, complete
        -> GET  /v1/captures/{id}                  -> COMPLETE
        -> GET  /v1/captures/{id}/content          -> a document ContentObject

There is no ``/v1/docx``, no second staging mechanism, and no second raw store.
:class:`TestBothDocumentFormatsOverOneApi` is where that stops being a claim
about design and becomes an observation: a PDF and a DOCX go through the same
two calls, land in the same store, and come back as the same canonical type —
differing only where they genuinely differ, which is that one of them knows what
page its text was on and the other cannot.

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
from tests import docxs, pdfs
from tests.unit.api.builders import CAPTURE_ID as TEXT_CAPTURE_ID
from tests.unit.api.builders import (
    WEBPAGE_CAPTURE_ID,
    text_envelope,
    webpage_envelope,
)
from unimem_api import RAW_DIRNAME, build_local_app

CAPTURE_ID = "cap_http_docx_01"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC_MIME = "application/msword"
PDF_MIME = "application/pdf"
CAPTURED_AT = "2026-01-02T03:04:05+00:00"

#: The document every test uses unless it says otherwise: a paragraph, a 2x2
#: table, and a second paragraph. It is the fixture that makes body order
#: visible — a build that read the tables but put them somewhere else would pass
#: everything except the ordering assertions.
DOCUMENT = docxs.paragraph_table_paragraph_docx()
DOCUMENT_DIGEST = hashlib.sha256(DOCUMENT).hexdigest()

#: The body of :data:`DOCUMENT`, flattened, in the order it must come back.
EXPECTED_BODY = [
    docxs.PARAGRAPH_A,
    docxs.TABLE_ROW_ONE,
    docxs.TABLE_ROW_TWO,
    docxs.PARAGRAPH_B,
]


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
    data: bytes = DOCUMENT,
    *,
    filename: str = "paper.docx",
    mime: str = DOCX_MIME,
) -> str:
    """Upload bytes and return the ``file_ref`` the server handed back.

    Deliberately the *same* helper shape the PDF suite uses, calling the *same*
    route with a different declared type. There is nothing DOCX-specific to do
    here, which is the result Phase 3 PR 1 was designed for.
    """
    response = client.post("/v1/uploads", files={"file": (filename, data, mime)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def document_envelope(
    file_ref: str, *, payload: dict[str, object] | None = None, **overrides: object
) -> dict[str, object]:
    """The canonical envelope a client assembles, as a JSON-ready dictionary."""
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "document", "mime_type": DOCX_MIME, "file_ref": file_ref}
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
        body = client.post(
            "/v1/uploads", files={"file": ("paper.docx", DOCUMENT, DOCX_MIME)}
        ).json()

        assert body["sha256"] == hashlib.sha256(DOCUMENT).hexdigest()

    def test_the_capture_response_says_complete(self, client: TestClient) -> None:
        body = client.post(
            "/v1/captures", json=document_envelope(stage(client), id="cap_http_docx_repeat")
        ).json()

        assert body["status"] == CaptureStatus.COMPLETE.value
        assert body["capture_id"] == "cap_http_docx_repeat"
        assert body["content_id"]

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
        assert raw_object.mime_type == DOCX_MIME

    def test_the_content_object_is_a_document(self, client: TestClient) -> None:
        assert content_of(client).type is ContentType.DOCUMENT

    def test_the_body_comes_back_in_document_order(self, client: TestClient) -> None:
        """Paragraph, table row, table row, paragraph — as written."""
        assert [segment.text for segment in content_of(client).segments] == EXPECTED_BODY

    def test_the_table_rows_sit_between_the_two_paragraphs(self, client: TestClient) -> None:
        segments = content_of(client).segments

        assert [segment.metadata.get("docx_block") for segment in segments] == [
            "paragraph",
            "table_row",
            "table_row",
            "paragraph",
        ]

    def test_the_table_cells_are_tab_separated(self, client: TestClient) -> None:
        segments = content_of(client).segments

        assert segments[1].text == "Format\tPagination"
        assert segments[2].text == "docx\trenderer-dependent"

    def test_no_segment_carries_a_page_number(self, client: TestClient) -> None:
        """A DOCX page is a rendering result, so canonical content does not claim one."""
        assert all(segment.spatial is None for segment in content_of(client).segments)

    def test_the_positions_are_contiguous_canonical_reading_order(self, client: TestClient) -> None:
        assert [segment.position for segment in content_of(client).segments] == [0, 1, 2, 3]

    def test_every_segment_is_text_from_the_original(self, client: TestClient) -> None:
        content = content_of(client)

        assert {segment.type for segment in content.segments} == {SegmentType.TEXT}
        assert {segment.provenance.source_type for segment in content.segments} == {
            ProvenanceSourceType.ORIGINAL
        }

    def test_the_processor_is_recorded(self, client: TestClient) -> None:
        content = content_of(client)

        assert content.processing[0].processor == "docx"
        assert content.processing[0].processor_version == "0.1"
        assert {segment.provenance.processor for segment in content.segments} == {"docx"}

    def test_there_is_one_original_asset(self, client: TestClient, submitted: str) -> None:
        content = content_of(client)

        assert [asset.role for asset in content.assets] == [AssetRole.ORIGINAL]
        assert content.assets[0].ref == submitted
        assert content.assets[0].sha256 == DOCUMENT_DIGEST
        assert content.assets[0].mime_type == DOCX_MIME

    def test_the_stored_original_is_the_uploaded_docx_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_raw_digest_is_the_sha256_of_the_uploaded_docx(self, data_dir: Path) -> None:
        stored = raw_path(data_dir, DOCUMENT_DIGEST).read_bytes()

        assert hashlib.sha256(stored).hexdigest() == DOCUMENT_DIGEST

    def test_exactly_one_raw_object_exists(self, data_dir: Path) -> None:
        """Staged once, referenced once, copied never."""
        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]

        assert len(objects) == 1


class TestUploadingIsNotCapturing:
    def test_an_upload_alone_leaves_no_capture(self, client: TestClient) -> None:
        stage(client)

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_an_upload_alone_leaves_no_content(self, client: TestClient) -> None:
        stage(client)

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_an_unclaimed_upload_stays_on_disk(self, client: TestClient, data_dir: Path) -> None:
        """No lease, no expiry, no collector: an unreferenced object is harmless."""
        stage(client)

        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_same_docx_uploaded_twice_returns_the_same_reference(
        self, client: TestClient, data_dir: Path
    ) -> None:
        first = stage(client)
        second = stage(client)

        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]
        assert first == second
        assert len(objects) == 1


class TestTitlePrecedence:
    def test_the_docx_core_title_is_used_when_the_capture_sends_none(
        self, client: TestClient
    ) -> None:
        file_ref = stage(client, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE))
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).title == docxs.CORE_TITLE

    def test_a_blank_core_title_is_treated_as_absent(self, client: TestClient) -> None:
        file_ref = stage(
            client, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.BLANK_CORE_TITLE)
        )
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).title is None

    def test_the_submitted_title_wins_over_the_docx_core_title(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE))
        client.post(
            "/v1/captures",
            json=document_envelope(file_ref, payload={"title": "What I called it"}),
        )

        assert content_of(client).title == "What I called it"

    def test_the_submitted_title_stays_on_the_capture_record(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE))
        client.post(
            "/v1/captures",
            json=document_envelope(file_ref, payload={"title": "What I called it"}),
        )

        assert record_of(client).title == "What I called it"

    def test_the_core_title_does_not_reach_the_capture_record(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.paragraphs_docx(docxs.PARAGRAPH_A, title=docxs.CORE_TITLE))
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert record_of(client).title is None

    def test_the_uploaded_filename_never_becomes_the_title(self, client: TestClient) -> None:
        """The filename is not persisted anywhere, so it cannot name anything."""
        file_ref = stage(
            client,
            docxs.paragraphs_docx(docxs.PARAGRAPH_A),
            filename="Quarterly Report FINAL v3.docx",
        )
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).title is None
        assert "Quarterly Report" not in client.get(f"/v1/captures/{CAPTURE_ID}").text

    def test_the_first_paragraph_never_becomes_the_title(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.paragraphs_docx(docxs.PARAGRAPH_A))
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert content_of(client).title is None


class TestFormatsTheServerRefuses:
    """Declared formats with no processor never reach the router."""

    @pytest.mark.parametrize(
        "mime_type",
        [
            DOC_MIME,
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.oasis.opendocument.text",
            "application/rtf",
            "application/epub+zip",
            "application/zip",
            "text/plain",
            "application/octet-stream",
        ],
        ids=["doc", "docm", "odt", "rtf", "epub", "zip", "text", "octets"],
    )
    def test_an_unsupported_document_format_is_refused_before_the_lifecycle(
        self, client: TestClient, mime_type: str
    ) -> None:
        file_ref = stage(client)

        response = client.post(
            "/v1/captures", json=document_envelope(file_ref, payload={"mime_type": mime_type})
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    def test_legacy_doc_is_refused_even_though_the_bytes_are_a_real_docx(
        self, client: TestClient
    ) -> None:
        """The declaration decides, and nothing looks inside the ZIP to correct it."""
        file_ref = stage(client, mime=DOC_MIME)

        response = client.post(
            "/v1/captures", json=document_envelope(file_ref, payload={"mime_type": DOC_MIME})
        )

        assert response.status_code == 422
        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_a_declared_mime_type_is_not_echoed_back(self, client: TestClient) -> None:
        file_ref = stage(client)

        response = client.post(
            "/v1/captures",
            json=document_envelope(file_ref, payload={"mime_type": "x-secret/internal"}),
        )

        assert "x-secret" not in response.text


class TestReferencesTheServerRefuses:
    """The line between an upload boundary and a local-file-read primitive."""

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
        """The path exists and holds a perfectly good DOCX. It is still refused."""
        real = tmp_path / "on-disk.docx"
        real.write_bytes(DOCUMENT)

        response = client.post("/v1/captures", json=document_envelope(str(real)))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 404

    @pytest.mark.parametrize(
        "file_ref",
        [
            "file:///etc/passwd",
            "http://127.0.0.1:1/paper.docx",
            "https://example.invalid/paper.docx",
            "s3://bucket/paper.docx",
            "/etc/passwd",
            "~/Documents/paper.docx",
        ],
        ids=["file-url", "http", "https", "s3", "etc-passwd", "home"],
    )
    def test_no_url_is_fetched_and_no_path_is_read(self, client: TestClient, file_ref: str) -> None:
        """The HTTP ones matter most: nothing is dialled, so nothing times out."""
        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    def test_the_source_url_is_never_fetched_either(self, client: TestClient) -> None:
        """``source.url`` is provenance a client supplied, not an instruction."""
        file_ref = stage(client)

        response = client.post(
            "/v1/captures",
            json=document_envelope(
                file_ref,
                source={
                    "type": "upload",
                    "provider": "curl",
                    "url": "http://127.0.0.1:1/never-fetched.docx",
                },
            ),
        )

        assert response.status_code == 201
        assert [segment.text for segment in content_of(client).segments] == EXPECTED_BODY

    def test_the_refusal_does_not_echo_the_reference(self, client: TestClient) -> None:
        response = client.post(
            "/v1/captures", json=document_envelope("/home/someone/private/taxes.docx")
        )

        assert "taxes.docx" not in response.text
        assert "/home/someone" not in response.text


class TestDocumentsThatFailProcessing:
    def test_an_image_only_docx_is_a_safe_422(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.image_only_docx())

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_a_textless_docx_leaves_a_truthful_failed_capture(self, client: TestClient) -> None:
        """Not an empty ``complete``: the document was not remembered."""
        file_ref = stage(client, docxs.image_only_docx())
        client.post("/v1/captures", json=document_envelope(file_ref))

        record = record_of(client)

        assert record.status is CaptureStatus.FAILED
        assert record.error is not None

    def test_a_textless_docx_produces_no_content_object(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.image_only_docx())
        client.post("/v1/captures", json=document_envelope(file_ref))

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

    def test_a_textless_docx_keeps_its_original(self, client: TestClient, data_dir: Path) -> None:
        data = docxs.image_only_docx()
        file_ref = stage(client, data)
        client.post("/v1/captures", json=document_envelope(file_ref))

        digest = file_ref.split(":", 1)[1]

        assert raw_path(data_dir, digest).read_bytes() == data

    @pytest.mark.parametrize(
        "data",
        [
            docxs.corrupt_docx(),
            docxs.truncated_body_docx(),
            docxs.encrypted_docx(),
            docxs.textless_docx(),
            docxs.header_only_docx(),
        ],
        ids=["corrupt", "broken-xml", "encrypted", "empty-body", "header-only"],
    )
    def test_an_unreadable_document_fails_safely(self, client: TestClient, data: bytes) -> None:
        file_ref = stage(client, data)

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert record_of(client).status is CaptureStatus.FAILED

    def test_a_corrupt_document_leaks_no_stack_or_path(self, client: TestClient) -> None:
        file_ref = stage(client, docxs.truncated_body_docx())

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        body = response.text
        assert "Traceback" not in body
        assert "lxml" not in body
        assert "python-docx" not in body
        assert "BadZipFile" not in body
        assert "XMLSyntaxError" not in body
        assert "word/document.xml" not in body
        assert "/tmp" not in body
        assert "site-packages" not in body

    def test_a_corrupt_document_still_names_the_format_it_could_not_read(
        self, client: TestClient
    ) -> None:
        """Saying "not a readable DOCX" is the useful part; how it failed is not."""
        file_ref = stage(client, docxs.truncated_body_docx())

        response = client.post("/v1/captures", json=document_envelope(file_ref))

        assert "DOCX package" in response.text


class TestReplayIsNotGeneralized:
    """Document idempotency is still not designed, and this states it plainly."""

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
            "/v1/captures", json=document_envelope(file_ref, id="cap_http_docx_02")
        ).json()

        assert first["content_id"] != second["content_id"]

    def test_both_captures_point_at_the_same_raw_digest(self, client: TestClient) -> None:
        file_ref = stage(client)
        client.post("/v1/captures", json=document_envelope(file_ref))
        client.post("/v1/captures", json=document_envelope(file_ref, id="cap_http_docx_02"))

        first = content_of(client)
        second = content_of(client, "cap_http_docx_02")

        assert first.original.sha256 == second.original.sha256 == DOCUMENT_DIGEST
        assert first.assets[0].ref == second.assets[0].ref
        assert first.id != second.id
        assert first.assets[0].id != second.assets[0].id

    def test_only_one_raw_object_is_on_disk(self, client: TestClient, data_dir: Path) -> None:
        file_ref = stage(client)
        client.post("/v1/captures", json=document_envelope(file_ref))
        client.post("/v1/captures", json=document_envelope(file_ref, id="cap_http_docx_02"))

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

    def test_the_body_is_still_in_order(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            file_ref = stage(first)
            first.post("/v1/captures", json=document_envelope(file_ref))

        with TestClient(build_local_app(data_dir)) as second:
            segments = content_of(second).segments

        assert [segment.text for segment in segments] == EXPECTED_BODY
        assert all(segment.spatial is None for segment in segments)

    def test_the_original_docx_is_still_retrievable(self, data_dir: Path) -> None:
        with TestClient(build_local_app(data_dir)) as first:
            stage(first)

        assert raw_path(data_dir, DOCUMENT_DIGEST).read_bytes() == DOCUMENT

    def test_the_same_docx_uploaded_after_a_restart_returns_the_same_reference(
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


class TestBothDocumentFormatsOverOneApi:
    """One API, one store, one lifecycle, two formats — and no ambiguity."""

    @pytest.fixture
    def captured(self, client: TestClient) -> None:
        docx_ref = stage(client)
        pdf_ref = stage(client, pdfs.blank_middle_pdf(), filename="paper.pdf", mime=PDF_MIME)
        assert client.post("/v1/captures", json=document_envelope(docx_ref)).status_code == 201
        assert (
            client.post(
                "/v1/captures",
                json=document_envelope(
                    pdf_ref, id="cap_http_mixed_pdf", payload={"mime_type": PDF_MIME}
                ),
            ).status_code
            == 201
        )

    def test_both_are_document_content_objects(self, client: TestClient, captured: None) -> None:
        assert content_of(client).type is ContentType.DOCUMENT
        assert content_of(client, "cap_http_mixed_pdf").type is ContentType.DOCUMENT

    def test_each_was_read_by_its_own_processor(self, client: TestClient, captured: None) -> None:
        assert content_of(client).processing[0].processor == "docx"
        assert content_of(client, "cap_http_mixed_pdf").processing[0].processor == "pdf"

    def test_the_pdf_still_carries_physical_page_numbers(
        self, client: TestClient, captured: None
    ) -> None:
        """Frozen behaviour: text on page 1, blank page 2, text on page 3."""
        segments = content_of(client, "cap_http_mixed_pdf").segments

        assert [segment.spatial.page for segment in segments] == [1, 3]  # type: ignore[union-attr]
        assert [segment.position for segment in segments] == [0, 1]

    def test_the_docx_carries_none(self, client: TestClient, captured: None) -> None:
        assert all(segment.spatial is None for segment in content_of(client).segments)

    def test_the_two_originals_are_distinct_raw_objects(
        self, client: TestClient, captured: None, data_dir: Path
    ) -> None:
        docx = content_of(client)
        pdf = content_of(client, "cap_http_mixed_pdf")

        assert docx.original.sha256 != pdf.original.sha256
        objects = [
            path for path in (data_dir / RAW_DIRNAME / "sha256").rglob("*") if path.is_file()
        ]
        assert len(objects) == 2

    def test_a_pdf_alone_still_behaves_exactly_as_before(self, client: TestClient) -> None:
        """The PDF path, checked with no DOCX anywhere near it."""
        pdf_ref = stage(client, pdfs.blank_middle_pdf(), filename="paper.pdf", mime=PDF_MIME)

        response = client.post(
            "/v1/captures",
            json=document_envelope(
                pdf_ref, id="cap_http_pdf_only", payload={"mime_type": PDF_MIME}
            ),
        )

        content = content_of(client, "cap_http_pdf_only")
        assert response.status_code == 201
        assert [segment.text for segment in content.segments] == [
            pdfs.TWO_PAGE_TEXT_EXTRACTED,
            pdfs.TWO_PAGE_SECOND_EXTRACTED,
        ]
        assert [segment.spatial.page for segment in content.segments] == [1, 3]  # type: ignore[union-attr]


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
