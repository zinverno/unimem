"""A PDFium load failure, through the real adapter, to a real HTTP response.

The classification rule lives in :meth:`unimem_ocr.tesseract.TesseractPdfPageOcr._open`
and is unit-tested there. This file asserts the part that actually matters to a
deployment: that the rule reaches the wire and the lifecycle intact. A load
failure PDFium would not explain must produce ``503 ocr_unavailable``, leave the
capture ``PROCESSING``, and persist no ``ContentObject`` — while a recognized
format failure keeps the ``422``/``FAILED`` behaviour it always had.

Everything on the path is real except the one injected load failure: the real
composition root, the real orchestrator, the real SQLite stores, the real
content-addressed raw store, and the real ``TesseractPdfPageOcr``. The engine is
never reached — loading fails first — so this module needs only the optional
rasterization extra and not Tesseract, and its adapter is deliberately pointed at
a non-existent executable so that an accidental invocation could not pass for
success.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureRecord, CaptureStatus
from core.contracts import CaptureStatus as Status
from core.contracts.base import SCHEMA_VERSION
from tests import ocr_support, pdfs
from unimem_api import DATABASE_FILENAME, RAW_DIRNAME, build_local_app

ocr_support.require_rasterizer()

import pypdfium2 as pdfium  # noqa: E402 - only importable once the guard has passed
import pypdfium2.raw as pdfium_raw  # noqa: E402

from unimem_ocr.tesseract import TesseractPdfPageOcr  # noqa: E402

PDF_MIME: Final = "application/pdf"
CAPTURE_ID: Final = "cap_load_failure_01"
CAPTURED_AT: Final = "2026-04-05T06:07:08+00:00"

#: A structurally valid PDF with no embedded text. ``pypdf`` reads it happily and
#: finds nothing, which is exactly what hands the document to the recognizer — and
#: therefore to the injected load failure.
SCAN: Final = pdfs.textless_pdf()
SCAN_DIGEST: Final = hashlib.sha256(SCAN).hexdigest()

#: Load reasons that are not verdicts about the document.
UNPROVEN_CODES: Final = (
    pytest.param(pdfium_raw.FPDF_ERR_UNKNOWN, id="unknown"),
    pytest.param(pdfium_raw.FPDF_ERR_FILE, id="file"),
    pytest.param(pdfium_raw.FPDF_ERR_PAGE, id="page"),
    pytest.param(None, id="absent"),
    pytest.param(4242, id="unrecognized"),
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "unimem-data"


@pytest.fixture
def client(data_dir: Path, tmp_path: Path) -> Iterator[TestClient]:
    """The real stack, with a real recognizer whose engine cannot be reached."""
    recognizer = TesseractPdfPageOcr(
        engine_version="load-failure-regression",
        executable=str(tmp_path / "deliberately-absent-engine"),
    )
    with TestClient(build_local_app(data_dir, pdf_ocr=recognizer)) as running:
        yield running


def failing_loader(
    monkeypatch: pytest.MonkeyPatch, code: int | None
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """Make every ``PdfDocument(...)`` raise one load failure, and record the calls.

    The recorded calls are asserted on, so a test cannot pass because the patch
    silently failed to apply and something else produced the same status code.
    """
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def explode(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise pdfium.PdfiumError(f"injected load failure {code!r}", err_code=code)

    monkeypatch.setattr(pdfium, "PdfDocument", explode)
    return calls


def stage(client: TestClient) -> str:
    response = client.post("/v1/uploads", files={"file": ("scan.pdf", SCAN, PDF_MIME)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def submit(client: TestClient, capture_id: str = CAPTURE_ID) -> Any:
    return client.post(
        "/v1/captures",
        json={
            "schema_version": SCHEMA_VERSION,
            "id": capture_id,
            "source": {"type": "upload", "provider": "curl"},
            "payload": {"type": "document", "mime_type": PDF_MIME, "file_ref": stage(client)},
            "context": {"captured_at": CAPTURED_AT},
        },
    )


def record_of(client: TestClient, capture_id: str = CAPTURE_ID) -> CaptureRecord:
    return CaptureRecord.model_validate(client.get(f"/v1/captures/{capture_id}").json())


def content_rows(data_dir: Path, capture_id: str = CAPTURE_ID) -> int:
    """Canonical content rows for this capture, read straight from SQLite."""
    connection = sqlite3.connect(data_dir / DATABASE_FILENAME)
    try:
        found = connection.execute(
            "SELECT COUNT(*) FROM content_objects WHERE capture_id = ?", (capture_id,)
        ).fetchone()
    finally:
        connection.close()
    return int(found[0])


def raw_path(data_dir: Path) -> Path:
    return data_dir / RAW_DIRNAME / "sha256" / SCAN_DIGEST[:2] / SCAN_DIGEST[2:4] / SCAN_DIGEST


class TestAnUnprovenLoadFailureIsNonTerminal:
    """The regression this file exists for."""

    @pytest.mark.parametrize("code", UNPROVEN_CODES)
    def test_the_client_gets_a_safe_503(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        calls = failing_loader(monkeypatch, code)

        response = submit(client)

        assert calls, "the injected loader never ran; this test would prove nothing"
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "ocr_unavailable"

    @pytest.mark.parametrize("code", UNPROVEN_CODES)
    def test_the_capture_is_left_processing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        """Not ``FAILED``: nothing was established about the document."""
        failing_loader(monkeypatch, code)

        assert submit(client).status_code == 503

        status = record_of(client).status
        assert status is CaptureStatus.PROCESSING
        assert status not in {Status.FAILED, Status.COMPLETE}

    @pytest.mark.parametrize("code", UNPROVEN_CODES)
    def test_no_content_object_is_persisted(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int | None
    ) -> None:
        failing_loader(monkeypatch, code)

        assert submit(client).status_code == 503

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404
        assert content_rows(data_dir) == 0

    def test_the_public_message_says_nothing_about_the_failure_internals(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_UNKNOWN)

        message = submit(client).json()["error"]["message"]

        assert "err_code" not in message
        assert "PDFium" not in message
        assert "injected" not in message
        assert "/" not in message

    def test_the_uploaded_pdf_survives_byte_for_byte(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_UNKNOWN)

        assert submit(client).status_code == 503

        assert raw_path(data_dir).read_bytes() == SCAN

    def test_the_stranded_capture_is_observable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_UNKNOWN)
        submit(client)

        assert client.get(f"/v1/captures/{CAPTURE_ID}").status_code == 200


class TestARecognizedFormatFailureStaysAnInputVerdict:
    """The other half: narrowing the allowlist must not lose the 422 behaviour."""

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param(pdfium_raw.FPDF_ERR_FORMAT, id="format"),
            pytest.param(pdfium_raw.FPDF_ERR_PASSWORD, id="password"),
            pytest.param(pdfium_raw.FPDF_ERR_SECURITY, id="security"),
        ],
    )
    def test_the_client_gets_the_existing_422(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        calls = failing_loader(monkeypatch, code)

        response = submit(client)

        assert calls, "the injected loader never ran; this test would prove nothing"
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "processing_failed"

    def test_the_capture_is_durably_failed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_FORMAT)

        assert submit(client).status_code == 422

        assert record_of(client).status is CaptureStatus.FAILED

    def test_no_content_object_is_persisted_either(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_FORMAT)

        assert submit(client).status_code == 422

        assert client.get(f"/v1/captures/{CAPTURE_ID}/content").status_code == 404
        assert content_rows(data_dir) == 0

    def test_the_uploaded_pdf_survives_byte_for_byte(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_FORMAT)

        assert submit(client).status_code == 422

        assert raw_path(data_dir).read_bytes() == SCAN

    def test_the_two_classifications_really_do_differ_on_the_same_document(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same bytes, same request, two codes, two lifecycles.

        Written as one test so the contrast cannot drift apart: the only thing
        that differs between the two submissions is PDFium's reported reason.
        """
        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_FORMAT)
        assert submit(client, "cap_format").status_code == 422
        assert record_of(client, "cap_format").status is CaptureStatus.FAILED

        failing_loader(monkeypatch, pdfium_raw.FPDF_ERR_UNKNOWN)
        assert submit(client, "cap_unknown").status_code == 503
        assert record_of(client, "cap_unknown").status is CaptureStatus.PROCESSING

        assert content_rows(data_dir, "cap_format") == 0
        assert content_rows(data_dir, "cap_unknown") == 0
