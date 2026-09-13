"""A scan through a real server process, on a real socket, with the real engine.

``TestClient`` runs the application inside the test's own interpreter, which is
enough for almost everything and not enough for this: ``--pdf-ocr`` is a *command
line*, and "stop the server and start it again over the same directory" is only a
durability claim if the first process is genuinely gone. So these tests run
``python -m unimem_api --pdf-ocr`` as a subprocess on a dynamically allocated port
and talk to it over HTTP.

The engine is the installed Tesseract, the renderer is PDFium, and the documents
are bitmaps with no text layer — proved in :mod:`tests.integration.ocr.test_real_pdf_ocr`.
The one thing substituted anywhere in this file is the engine in
:class:`TestARuntimeEngineFailureOverHttp`, which needs a Tesseract that passes
startup validation and then fails on a page; no real engine can be asked to do
that on demand, and the behaviour it proves — a non-terminal capture and a safe
503 — is the one a running deployment most needs to be true.

A server that will not start is always a failure here, never a skip.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import httpx2
import pytest

from core.contracts import CaptureRecord, CaptureStatus, ContentObject, ContentType
from core.contracts.base import SCHEMA_VERSION
from core.contracts.enums import ProvenanceSourceType, SegmentType
from core.processing import OCR_METADATA_KEY
from tests import ocr_support, pdfs
from tests.unit.ocr import fakes
from unimem_api import DATABASE_FILENAME, RAW_DIRNAME

ocr_support.require_local_ocr()

from tests import ocr_fixtures  # noqa: E402 - needs Pillow, so only past the guard

PDF_MIME: Final = "application/pdf"
CAPTURED_AT: Final = "2026-02-03T04:05:06+00:00"

#: How long one HTTP call may take. Recognition happens inside the request, and a
#: 300 DPI page through a real engine is not instant.
HTTP_TIMEOUT: Final = 180.0


def normalized(text: str | None) -> str:
    return " ".join((text or "").split())


def raw_path(data_dir: Path, digest: str) -> Path:
    return data_dir / RAW_DIRNAME / "sha256" / digest[:2] / digest[2:4] / digest


def envelope(capture_id: str, file_ref: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": capture_id,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "document", "mime_type": PDF_MIME, "file_ref": file_ref},
        "context": {"captured_at": CAPTURED_AT},
    }


class Api:
    """The three calls these tests make, over a real socket to a real process."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.client = httpx2.Client(timeout=HTTP_TIMEOUT)

    def close(self) -> None:
        self.client.close()

    def upload(self, data: bytes, *, filename: str = "scan.pdf") -> str:
        response = self.client.post(
            f"{self.base}/v1/uploads", files={"file": (filename, data, PDF_MIME)}
        )
        assert response.status_code == 200, response.text
        file_ref: str = response.json()["file_ref"]
        return file_ref

    def capture(self, capture_id: str, data: bytes) -> Any:
        return self.client.post(
            f"{self.base}/v1/captures", json=envelope(capture_id, self.upload(data))
        )

    def record(self, capture_id: str) -> CaptureRecord:
        response = self.client.get(f"{self.base}/v1/captures/{capture_id}")
        assert response.status_code == 200, response.text
        return CaptureRecord.model_validate(response.json())

    def content(self, capture_id: str) -> ContentObject:
        response = self.client.get(f"{self.base}/v1/captures/{capture_id}/content")
        assert response.status_code == 200, response.text
        return ContentObject.model_validate(response.json())

    def status_of(self, path: str) -> int:
        return self.client.get(f"{self.base}{path}").status_code


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


@pytest.fixture
def api(data_dir: Path) -> Iterator[Api]:
    """A server process with ``--pdf-ocr``, and a client pointed at it."""
    with ocr_support.serve_process(data_dir) as base:
        served = Api(base)
        try:
            yield served
        finally:
            served.close()


def capture_rows(data_dir: Path, table: str, capture_id: str, column: str) -> int:
    connection = sqlite3.connect(data_dir / DATABASE_FILENAME)
    try:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (capture_id,)
        ).fetchone()
    finally:
        connection.close()
    return int(found[0])


class TestAScanOverRealHttp:
    DOCUMENT: Final = ocr_fixtures.english_scan()
    DIGEST: Final = hashlib.sha256(DOCUMENT).hexdigest()

    @pytest.fixture(autouse=True)
    def submitted(self, api: Api) -> Any:
        response = api.capture("cap_http_scan", self.DOCUMENT)
        assert response.status_code == 201, response.text
        return response

    def test_the_server_reported_the_pipeline_finished(self, submitted: Any) -> None:
        body = submitted.json()

        assert body["status"] == "complete"
        assert body["capture_id"] == "cap_http_scan"
        assert body["content_id"]

    def test_the_capture_record_is_complete(self, api: Api) -> None:
        assert api.record("cap_http_scan").status is CaptureStatus.COMPLETE

    def test_the_content_is_a_document_with_a_recognized_page(self, api: Api) -> None:
        content = api.content("cap_http_scan")

        assert content.type is ContentType.DOCUMENT
        assert len(content.segments) == 1
        assert content.segments[0].type is SegmentType.OCR
        assert content.segments[0].provenance.source_type is ProvenanceSourceType.OCR

    def test_the_recognized_words_came_back_over_the_wire(self, api: Api) -> None:
        read = normalized(api.content("cap_http_scan").segments[0].text)

        for phrase in ocr_fixtures.ENGLISH_PHRASES:
            assert phrase in read

    def test_the_segment_names_the_physical_page(self, api: Api) -> None:
        spatial = api.content("cap_http_scan").segments[0].spatial

        assert spatial is not None
        assert spatial.page == 1

    def test_the_provenance_names_the_recognizing_processor(self, api: Api) -> None:
        provenance = api.content("cap_http_scan").segments[0].provenance

        assert provenance.processor == "pdf-ocr"
        assert provenance.processor_version == "0.1"
        assert provenance.capture_id == "cap_http_scan"

    def test_the_installed_engine_is_recorded_on_the_content(self, api: Api) -> None:
        recorded = api.content("cap_http_scan").metadata[OCR_METADATA_KEY]

        assert isinstance(recorded, dict)
        assert recorded["engine"] == "tesseract"
        assert recorded["rasterizer"] == "pypdfium2"
        settings = recorded["settings"]
        assert isinstance(settings, dict)
        assert settings["languages"] == "eng+rus"

    def test_the_original_pdf_on_disk_is_byte_for_byte_what_was_uploaded(
        self, data_dir: Path
    ) -> None:
        assert raw_path(data_dir, self.DIGEST).read_bytes() == self.DOCUMENT

    def test_no_page_image_was_persisted_beside_it(self, data_dir: Path) -> None:
        stored = sorted(path for path in (data_dir / RAW_DIRNAME).rglob("*") if path.is_file())

        assert stored == [raw_path(data_dir, self.DIGEST)]

    def test_a_russian_scan_works_through_the_same_server(self, api: Api) -> None:
        response = api.capture("cap_http_russian", ocr_fixtures.russian_scan())
        assert response.status_code == 201, response.text

        read = normalized(api.content("cap_http_russian").segments[0].text)
        for phrase in ocr_fixtures.RUSSIAN_PHRASES:
            assert phrase in read

    def test_a_mixed_document_keeps_its_embedded_text_and_recognizes_the_scan(
        self, api: Api
    ) -> None:
        response = api.capture("cap_http_mixed", ocr_fixtures.mixed_document())
        assert response.status_code == 201, response.text

        content = api.content("cap_http_mixed")
        assert len(content.segments) == 2
        embedded, recognized = content.segments
        assert embedded.text == ocr_fixtures.MIXED_EMBEDDED_TEXT
        assert embedded.type is SegmentType.TEXT
        assert embedded.provenance.source_type is ProvenanceSourceType.ORIGINAL
        assert recognized.type is SegmentType.OCR
        for phrase in ocr_fixtures.MIXED_SCANNED_PHRASES:
            assert phrase in normalized(recognized.text)
        assert "middle page" not in normalized(embedded.text)

    def test_a_blank_scan_does_not_become_an_empty_complete_document(
        self, api: Api, data_dir: Path
    ) -> None:
        response = api.capture("cap_http_blank", ocr_fixtures.blank_scan())

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"
        assert api.record("cap_http_blank").status is CaptureStatus.FAILED
        assert api.status_of("/v1/captures/cap_http_blank/content") == 404

    def test_an_ordinary_text_pdf_still_works(self, api: Api) -> None:
        response = api.capture("cap_http_textpdf", pdfs.two_page_pdf())
        assert response.status_code == 201, response.text

        content = api.content("cap_http_textpdf")
        assert [segment.type for segment in content.segments] == [SegmentType.TEXT] * 2

    def test_a_duplicate_capture_id_is_still_a_conflict(self, api: Api) -> None:
        response = api.capture("cap_http_scan", self.DOCUMENT)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "capture_already_exists"


class TestARestartOfTheServerProcess:
    """The first process is stopped, a second starts over the same directory."""

    DOCUMENT: Final = ocr_fixtures.mixed_document()
    DIGEST: Final = hashlib.sha256(DOCUMENT).hexdigest()

    @pytest.fixture
    def before(self, data_dir: Path) -> dict[str, Any]:
        with ocr_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                assert api.capture("cap_restart", self.DOCUMENT).status_code == 201
                return {
                    "record": api.record("cap_restart"),
                    "content": api.content("cap_restart"),
                }
            finally:
                api.close()

    def test_everything_reads_back_identically_from_a_new_process(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with ocr_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                after_record = api.record("cap_restart")
                after_content = api.content("cap_restart")
            finally:
                api.close()

        assert after_record == before["record"]
        assert after_content == before["content"]

    def test_the_ids_the_text_and_the_metadata_all_survive(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with ocr_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                after = api.content("cap_restart")
            finally:
                api.close()

        original: ContentObject = before["content"]
        assert after.id == original.id
        assert [segment.id for segment in after.segments] == [
            segment.id for segment in original.segments
        ]
        assert [segment.text for segment in after.segments] == [
            segment.text for segment in original.segments
        ]
        assert after.metadata == original.metadata
        assert after.assets[0].id == original.assets[0].id

    def test_the_raw_original_survives_the_restart_byte_for_byte(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with ocr_support.serve_process(data_dir):
            pass

        assert raw_path(data_dir, self.DIGEST).read_bytes() == self.DOCUMENT

    def test_nothing_is_reprocessed_and_no_second_content_object_appears(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        with ocr_support.serve_process(data_dir) as base:
            api = Api(base)
            try:
                api.content("cap_restart")
            finally:
                api.close()

        assert capture_rows(data_dir, "content_objects", "cap_restart", "capture_id") == 1
        assert capture_rows(data_dir, "capture_records", "cap_restart", "id") == 1

    def test_a_default_server_can_read_what_the_ocr_server_wrote(
        self, data_dir: Path, before: dict[str, Any]
    ) -> None:
        """Stored canonical content needs no engine to serve."""
        with ocr_support.serve_process(data_dir, pdf_ocr=False) as base:
            api = Api(base)
            try:
                after = api.content("cap_restart")
            finally:
                api.close()

        assert after == before["content"]


class TestARuntimeEngineFailureOverHttp:
    """The engine passes startup validation, then fails on a page.

    This is the state a deployment can actually fall into — a language file
    removed, a broken upgrade, an out-of-memory kill — and it is the one whose
    promise matters most: the capture must not be marked ``failed``, because
    nothing was learned about the document.
    """

    @pytest.fixture
    def broken_engine(self, tmp_path: Path) -> Path:
        directory = tmp_path / "broken-engine"
        directory.mkdir()
        fakes.write_fake_engine(directory, languages=("eng", "osd", "rus"), recognize_exit=1)
        return directory

    @pytest.fixture
    def api(self, data_dir: Path, broken_engine: Path) -> Iterator[Api]:
        import os

        with ocr_support.serve_process(
            data_dir,
            env={"PATH": f"{broken_engine}{os.pathsep}{os.environ['PATH']}"},
        ) as base:
            served = Api(base)
            try:
                yield served
            finally:
                served.close()

    def test_the_server_started_because_startup_validation_passed(self, api: Api) -> None:
        """The fake answers ``--version`` and lists both languages, so it starts."""
        assert api.status_of("/health") == 200

    def test_the_client_gets_a_safe_503(self, api: Api) -> None:
        response = api.capture("cap_http_broken", ocr_fixtures.english_scan())

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "ocr_unavailable"
        assert "status 1" not in body["error"]["message"]
        assert "/" not in body["error"]["message"]

    def test_the_capture_is_left_processing_rather_than_failed(self, api: Api) -> None:
        api.capture("cap_http_broken", ocr_fixtures.english_scan())

        record = api.record("cap_http_broken")
        assert record.status is CaptureStatus.PROCESSING
        assert record.status not in {CaptureStatus.FAILED, CaptureStatus.COMPLETE}

    def test_no_content_was_persisted(self, api: Api, data_dir: Path) -> None:
        api.capture("cap_http_broken", ocr_fixtures.english_scan())

        assert api.status_of("/v1/captures/cap_http_broken/content") == 404
        assert capture_rows(data_dir, "content_objects", "cap_http_broken", "capture_id") == 0

    def test_the_uploaded_pdf_still_survives_byte_for_byte(self, api: Api, data_dir: Path) -> None:
        document = ocr_fixtures.english_scan()
        api.capture("cap_http_broken", document)

        digest = hashlib.sha256(document).hexdigest()
        assert raw_path(data_dir, digest).read_bytes() == document

    def test_the_stranded_capture_is_observable_but_not_resumable(self, api: Api) -> None:
        """Recovery is not implemented here; the state is at least truthful."""
        api.capture("cap_http_broken", ocr_fixtures.english_scan())

        assert api.status_of("/v1/captures/cap_http_broken") == 200
        retry = api.capture("cap_http_broken", ocr_fixtures.english_scan())
        assert retry.status_code == 409


class TestStartupRefusalIsNotASkip:
    def test_a_missing_language_pack_stops_the_server_from_starting(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        """The startup gate, exercised as a real process refusing to come up."""
        import os
        import subprocess
        import sys

        directory = tmp_path / "english-only"
        directory.mkdir()
        fakes.write_fake_engine(directory, languages=("eng", "osd"))

        finished = subprocess.run(
            [
                sys.executable,
                "-m",
                "unimem_api",
                "--data-dir",
                str(data_dir),
                "--port",
                str(ocr_support.free_port()),
                "--pdf-ocr",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ | {"PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"},
        )

        assert finished.returncode != 0
        assert "missing language data for rus" in finished.stderr
        assert not data_dir.exists()
