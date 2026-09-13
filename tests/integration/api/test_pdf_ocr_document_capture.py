"""A scanned PDF through the real application, over real HTTP, on a real directory.

Everything is real except the engine. ``build_local_app`` wires
``LocalRawObjectStore``, both SQLite stores, ``CaptureIntake``,
``PdfOcrProcessor``, ``ProcessorRouter``, and ``ProcessingOrchestrator`` against a
temporary directory, and every assertion is made against what is on disk or what
came back over the wire. The recognizer is a fake, so that the *lifecycle* claims
— what is durable when, what state a failure leaves behind, what a client is told
— can be made exactly, including for failures a real engine cannot be asked to
produce on demand. The real engine is driven in ``tests/integration/ocr``.

The flow is the one Phase 3 PR 1 established, unchanged::

    PDF bytes
        -> POST /v1/uploads          (multipart)   -> file_ref
        -> POST /v1/captures         (the canonical envelope, naming that ref)
        -> 201, complete
        -> GET  /v1/captures/{id}                  -> COMPLETE
        -> GET  /v1/captures/{id}/content          -> a document ContentObject

No new route, no new field, no new status.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.contracts.enums import ProvenanceSourceType, SegmentType
from core.processing import OCR_METADATA_KEY
from core.processing.ocr import PdfOcrExecutionError
from tests import pdfs
from tests.unit.processing.doubles import FakePdfPageOcr
from unimem_api import DATABASE_FILENAME, RAW_DIRNAME, build_local_app

CAPTURE_ID: Final = "cap_http_ocr_01"
PDF_MIME: Final = "application/pdf"
CAPTURED_AT: Final = "2026-01-02T03:04:05+00:00"

#: An image-only document: two physical pages, neither carrying embedded text.
SCAN: Final = pdfs.textless_pdf()
SCAN_DIGEST: Final = hashlib.sha256(SCAN).hexdigest()

#: Embedded text on page one, a scan on page two, a blank sheet on page three.
MIXED: Final = pdfs.build_pdf([pdfs.TWO_PAGE_TEXT, [], []], title=pdfs.METADATA_TITLE)
MIXED_DIGEST: Final = hashlib.sha256(MIXED).hexdigest()

#: What the fake engine reads from the two pages of :data:`SCAN`.
SCAN_TEXTS: Final = {1: "Page one, read from pixels.\n", 2: "Page two, also a scan.\n"}

#: What it reads from :data:`MIXED`: page two has words, page three has none.
MIXED_TEXTS: Final = {2: "The scanned middle page.\n", 3: ""}


def recognizer(page_count: int, texts: dict[int, str]) -> FakePdfPageOcr:
    return FakePdfPageOcr(page_count=page_count, texts=texts)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


def app_over(data_dir: Path, ocr: FakePdfPageOcr) -> TestClient:
    return TestClient(build_local_app(data_dir, pdf_ocr=ocr))


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as running:
        yield running


def raw_path(data_dir: Path, digest: str) -> Path:
    """Where ``LocalRawObjectStore`` keeps one object, addressed by its digest."""
    return data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest


def stage(client: TestClient, data: bytes = SCAN, *, filename: str = "scan.pdf") -> str:
    response = client.post("/v1/uploads", files={"file": (filename, data, PDF_MIME)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def document_envelope(file_ref: str, **overrides: object) -> dict[str, object]:
    """The canonical envelope a client assembles, as a JSON-ready dictionary."""
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "document", "mime_type": PDF_MIME, "file_ref": file_ref},
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


def submit(client: TestClient, data: bytes = SCAN, **overrides: object) -> Any:
    return client.post("/v1/captures", json=document_envelope(stage(client, data), **overrides))


def record_of(client: TestClient, capture_id: str = CAPTURE_ID) -> CaptureRecord:
    return CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())


def content_of(client: TestClient, capture_id: str = CAPTURE_ID) -> ContentObject:
    return ContentObject.model_validate(client.get(f"/v1/captures/{capture_id}/content").json())


def rows(data_dir: Path, table: str, capture_id: str, column: str) -> int:
    """Count rows for one capture, read straight from SQLite, around both stores."""
    connection = sqlite3.connect(data_dir / DATABASE_FILENAME)
    try:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (capture_id,)
        ).fetchone()
    finally:
        connection.close()
    return int(found[0])


class TestTheScannedVerticalSlice:
    @pytest.fixture(autouse=True)
    def submitted(self, client: TestClient) -> str:
        file_ref = stage(client)
        response = client.post("/v1/captures", json=document_envelope(file_ref))
        assert response.status_code == 201, response.text
        return file_ref

    def test_the_document_the_default_build_refuses_is_accepted(self, submitted: str) -> None:
        assert submitted == f"sha256:{SCAN_DIGEST}"

    def test_the_capture_record_is_complete(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_capture_record_carries_no_error(self, client: TestClient) -> None:
        assert record_of(client).error is None

    def test_the_content_object_is_a_document(self, client: TestClient) -> None:
        assert content_of(client).type is ContentType.DOCUMENT

    def test_both_pages_are_ocr_segments(self, client: TestClient) -> None:
        content = content_of(client)

        assert [segment.type for segment in content.segments] == [SegmentType.OCR] * 2
        assert [segment.provenance.source_type for segment in content.segments] == [
            ProvenanceSourceType.OCR
        ] * 2

    def test_the_recognized_text_came_back_over_the_wire_unchanged(
        self, client: TestClient
    ) -> None:
        assert [segment.text for segment in content_of(client).segments] == [
            SCAN_TEXTS[1],
            SCAN_TEXTS[2],
        ]

    def test_the_physical_pages_are_recorded(self, client: TestClient) -> None:
        assert [
            segment.spatial and segment.spatial.page for segment in content_of(client).segments
        ] == [1, 2]

    def test_the_processing_record_names_the_recognizing_processor(
        self, client: TestClient
    ) -> None:
        record = content_of(client).processing[0]

        assert (record.processor, record.processor_version) == ("pdf-ocr", "0.1")

    def test_the_recognized_content_is_durable_before_complete_is_reported(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """201 means the content exists, so both rows are already on disk."""
        assert rows(data_dir, "content_objects", CAPTURE_ID, "capture_id") == 1
        assert rows(data_dir, "capture_records", CAPTURE_ID, "id") == 1
        assert record_of(client).status is CaptureStatus.COMPLETE

    def test_the_original_pdf_is_on_disk_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, SCAN_DIGEST).read_bytes() == SCAN

    def test_no_page_image_was_persisted(self, data_dir: Path) -> None:
        """One raw object: the submitted PDF. Rasters are temporary computation."""
        stored = sorted(path for path in (data_dir / RAW_DIRNAME).rglob("*") if path.is_file())

        assert stored == [raw_path(data_dir, SCAN_DIGEST)]

    def test_there_is_exactly_one_asset_and_it_is_the_pdf(self, client: TestClient) -> None:
        content = content_of(client)

        assert [asset.mime_type for asset in content.assets] == [PDF_MIME]
        assert content.assets[0].ref == f"sha256:{SCAN_DIGEST}"

    def test_every_segment_references_that_asset(self, client: TestClient) -> None:
        content = content_of(client)

        assert {segment.provenance.asset_id for segment in content.segments} == {
            content.assets[0].id
        }

    def test_the_engine_that_ran_is_recorded_on_the_content(self, client: TestClient) -> None:
        recorded = content_of(client).metadata[OCR_METADATA_KEY]

        assert isinstance(recorded, dict)
        assert recorded["engine"] == "fake-ocr"
        assert recorded["ocr_attempted_pages"] == [1, 2]

    def test_a_duplicate_capture_id_is_still_a_conflict(self, client: TestClient) -> None:
        response = client.post("/v1/captures", json=document_envelope(stage(client)))

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"


class TestAMixedDocument:
    @pytest.fixture
    def client(self, data_dir: Path) -> Iterator[TestClient]:
        with app_over(data_dir, recognizer(3, MIXED_TEXTS)) as running:
            yield running

    @pytest.fixture(autouse=True)
    def submitted(self, client: TestClient) -> None:
        assert submit(client, MIXED).status_code == 201

    def test_the_embedded_page_and_the_scan_are_both_present(self, client: TestClient) -> None:
        content = content_of(client)

        assert [segment.text for segment in content.segments] == [
            pdfs.TWO_PAGE_TEXT_EXTRACTED,
            MIXED_TEXTS[2],
        ]

    def test_the_two_sources_are_distinguishable_over_the_wire(self, client: TestClient) -> None:
        served = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert [
            (segment["type"], segment["provenance"]["source_type"])
            for segment in served["segments"]
        ] == [("text", "original"), ("ocr", "ocr")]

    def test_the_embedded_page_was_never_offered_to_the_engine(self, client: TestClient) -> None:
        content = content_of(client)
        recorded = content.metadata[OCR_METADATA_KEY]

        assert isinstance(recorded, dict)
        assert recorded["embedded_text_pages"] == [1]
        assert recorded["ocr_attempted_pages"] == [2, 3]

    def test_the_blank_page_is_recorded_without_being_called_blank(
        self, client: TestClient
    ) -> None:
        recorded = content_of(client).metadata[OCR_METADATA_KEY]

        assert isinstance(recorded, dict)
        assert recorded["ocr_pages_without_text"] == [3]

    def test_nothing_is_duplicated(self, client: TestClient) -> None:
        content = content_of(client)

        assert len(content.segments) == 2
        assert [segment.position for segment in content.segments] == [0, 1]

    def test_the_title_still_comes_from_the_documents_own_metadata(
        self, client: TestClient
    ) -> None:
        assert content_of(client).title == pdfs.METADATA_TITLE

    def test_the_original_survives_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, MIXED_DIGEST).read_bytes() == MIXED


class TestADocumentNothingCouldBeReadFrom:
    """A blank scan: an input verdict, so a durable ``FAILED`` and no content."""

    @pytest.fixture
    def client(self, data_dir: Path) -> Iterator[TestClient]:
        with app_over(data_dir, recognizer(2, {1: "", 2: "   \n"})) as running:
            yield running

    @pytest.fixture(autouse=True)
    def rejected(self, client: TestClient) -> Any:
        response = submit(client)
        assert response.status_code == 422
        return response

    def test_the_client_is_told_the_request_could_not_be_processed(self, rejected: Any) -> None:
        assert rejected.json()["error"]["code"] == "processing_failed"

    def test_the_message_says_nothing_was_read(self, rejected: Any) -> None:
        assert "yielded no text" in rejected.json()["error"]["message"]

    def test_the_capture_is_durably_failed(self, client: TestClient) -> None:
        assert record_of(client).status is CaptureStatus.FAILED

    def test_no_content_object_exists(self, client: TestClient, data_dir: Path) -> None:
        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404
        assert rows(data_dir, "content_objects", CAPTURE_ID, "capture_id") == 0

    def test_it_never_becomes_an_empty_complete_document(self, client: TestClient) -> None:
        """The whole point of refusing: no content object with no segments."""
        assert record_of(client).status is not CaptureStatus.COMPLETE

    def test_the_original_is_still_on_disk(self, data_dir: Path) -> None:
        assert raw_path(data_dir, SCAN_DIGEST).read_bytes() == SCAN


class TestAnEngineFailureDuringARun:
    """Infrastructure, not a verdict: a non-terminal capture and a 503."""

    @pytest.fixture
    def failing(self) -> FakePdfPageOcr:
        return FakePdfPageOcr(
            raises=PdfOcrExecutionError("the OCR engine exited with status 1 on page 2")
        )

    @pytest.fixture
    def client(self, data_dir: Path, failing: FakePdfPageOcr) -> Iterator[TestClient]:
        with app_over(data_dir, failing) as running:
            yield running

    @pytest.fixture(autouse=True)
    def attempted(self, client: TestClient) -> Any:
        response = submit(client)
        assert response.status_code == 503, response.text
        return response

    def test_the_client_is_told_recognition_is_unavailable(self, attempted: Any) -> None:
        assert attempted.json()["error"]["code"] == "ocr_unavailable"

    def test_the_public_message_says_nothing_about_this_machine(self, attempted: Any) -> None:
        message = attempted.json()["error"]["message"]

        assert "status 1" not in message
        assert "tesseract" not in message.lower()
        assert "page 2" not in message

    def test_the_capture_is_left_processing(self, client: TestClient) -> None:
        """Not ``FAILED``: nothing was learned about the document."""
        assert record_of(client).status is CaptureStatus.PROCESSING

    def test_the_capture_is_not_marked_failed(self, client: TestClient) -> None:
        assert record_of(client).status is not CaptureStatus.FAILED

    def test_no_content_object_was_persisted(self, client: TestClient, data_dir: Path) -> None:
        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404
        assert rows(data_dir, "content_objects", CAPTURE_ID, "capture_id") == 0

    def test_no_partial_document_escaped_the_failure(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """A failure on any page leaves nothing canonical behind, not a prefix of it."""
        assert rows(data_dir, "content_objects", CAPTURE_ID, "capture_id") == 0

    def test_the_original_pdf_survives_byte_for_byte(self, data_dir: Path) -> None:
        assert raw_path(data_dir, SCAN_DIGEST).read_bytes() == SCAN

    def test_the_stranded_capture_is_observable(self, client: TestClient) -> None:
        """``GET`` is the whole recovery story this phase offers, and it still works."""
        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 200

    def test_resubmitting_the_same_id_is_a_conflict_rather_than_a_retry(
        self, client: TestClient
    ) -> None:
        """A capture left ``PROCESSING`` is not automatically resumable."""
        response = client.post("/v1/captures", json=document_envelope(stage(client)))

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"


class TestALaterPageFailure:
    def test_a_failure_after_some_pages_read_persists_nothing(self, data_dir: Path) -> None:
        """The port answers all-or-nothing, and this is what that buys.

        The engine is asked once for the whole document, so there is no window in
        which page one's text is canonical and page two's failure is still to come.
        """

        class FailsPartWayThrough:
            """Reads page one, then fails. Nothing it read may survive."""

            def __init__(self) -> None:
                self.read: list[int] = []

            def recognize_missing_pages(self, stream: Any, *, embedded_pages: Any) -> Any:
                self.read.append(1)
                raise PdfOcrExecutionError("the OCR engine did not finish page 2")

        engine = FailsPartWayThrough()
        with TestClient(build_local_app(data_dir, pdf_ocr=engine)) as client:
            assert submit(client).status_code == 503
            assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404

        assert engine.read == [1]
        assert rows(data_dir, "content_objects", CAPTURE_ID, "capture_id") == 0


class TestSurvivingARestart:
    """The application is thrown away and rebuilt over the same directory."""

    @pytest.fixture
    def first(self, data_dir: Path) -> Iterator[ContentObject]:
        with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as client:
            assert submit(client).status_code == 201
            yield content_of(client)

    def test_everything_reads_back_identically(self, data_dir: Path, first: ContentObject) -> None:
        with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as restarted:
            assert content_of(restarted) == first
            assert record_of(restarted).status is CaptureStatus.COMPLETE

    def test_the_recognized_text_and_metadata_survive(
        self, data_dir: Path, first: ContentObject
    ) -> None:
        with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as restarted:
            after = content_of(restarted)

        assert after.id == first.id
        assert [segment.text for segment in after.segments] == [SCAN_TEXTS[1], SCAN_TEXTS[2]]
        assert after.metadata == first.metadata

    def test_a_default_deployment_can_still_read_what_an_ocr_one_wrote(
        self, data_dir: Path, first: ContentObject
    ) -> None:
        """Stored content is just canonical content; reading it needs no engine."""
        with TestClient(build_local_app(data_dir)) as default:
            assert content_of(default) == first

    def test_nothing_is_reprocessed_on_restart(self, data_dir: Path, first: ContentObject) -> None:
        untouched = recognizer(2, SCAN_TEXTS)
        with app_over(data_dir, untouched) as restarted:
            content_of(restarted)
            record_of(restarted)

        assert untouched.calls == []


class TestTheSameBytesUnderTwoCaptureIds:
    def test_a_previously_refused_document_is_ingested_under_a_new_id(self, data_dir: Path) -> None:
        """The migration story: a new capture id, never a revisit of the old record."""
        with TestClient(build_local_app(data_dir)) as default:
            assert submit(default, id="cap_refused").status_code == 422
            assert record_of(default, "cap_refused").status is CaptureStatus.FAILED

        with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as recognizing:
            assert submit(recognizing, id="cap_recognized").status_code == 201

            assert record_of(recognizing, "cap_refused").status is CaptureStatus.FAILED
            assert recognizing.get("/v1/captures/cap_refused/content").status_code == 404
            assert record_of(recognizing, "cap_recognized").status is CaptureStatus.COMPLETE

    def test_the_two_captures_share_raw_bytes_and_no_canonical_identity(
        self, data_dir: Path
    ) -> None:
        with app_over(data_dir, recognizer(2, SCAN_TEXTS)) as client:
            assert submit(client, id="cap_one").status_code == 201
            assert submit(client, id="cap_two").status_code == 201
            first = content_of(client, "cap_one")
            second = content_of(client, "cap_two")

        stored = sorted(path for path in (data_dir / RAW_DIRNAME).rglob("*") if path.is_file())
        assert stored == [raw_path(data_dir, SCAN_DIGEST)]
        assert first.id != second.id
        assert first.assets[0].id != second.assets[0].id
        assert {segment.id for segment in first.segments}.isdisjoint(
            segment.id for segment in second.segments
        )
        assert first.original.sha256 == second.original.sha256 == SCAN_DIGEST
