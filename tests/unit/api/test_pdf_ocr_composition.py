"""Composition: which PDF processor a deployment gets, and who decides.

Three claims, and they are the whole of the opt-in design:

* **Without ``--pdf-ocr`` nothing changes.** The same processor is registered, a
  textless PDF is refused with the same status and code, and no optional package
  is imported, probed, or executed. Proved in a *real interpreter with the native
  packages blocked*, because "it does not import them" is a claim about imports
  and an AST scan is not an interpreter.
* **With ``--pdf-ocr`` exactly one PDF processor is registered**, and it is the
  recognizing one. Never both — that is an ambiguity error, checked in
  ``tests/unit/processing/test_pdf_ocr_routing.py``.
* **No request can reach the decision.** Not a field, not metadata, not an
  intent, not a MIME type. The only way to switch is to restart the process with
  a different command line.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import unimem_api.__main__ as cli
import unimem_ocr
from core.contracts import ContentObject
from core.contracts.base import SCHEMA_VERSION
from core.processing import OCR_METADATA_KEY
from tests import pdfs
from tests.unit.processing.doubles import FakePdfPageOcr
from unimem_api import build_local_app
from unimem_ocr import OcrPrerequisiteError

PDF_MIME: Final = "application/pdf"
CAPTURE_ID: Final = "cap_ocr_composition_01"
CAPTURED_AT: Final = "2026-05-06T07:08:09+00:00"

#: A structurally perfect PDF with no embedded text: the document the default
#: build refuses and an OCR-enabled one can read.
SCAN: Final = pdfs.textless_pdf()

#: An ordinary text PDF, which must behave identically in both deployments. Two
#: pages, matching :data:`SCAN`, so one recognizer stands in for both documents.
TEXT_DOCUMENT: Final = pdfs.two_page_pdf()

#: Blocks the optional native packages in a subprocess, so an interpreter can be
#: put in the state an ordinary installation is actually in.
BLOCK_NATIVE: Final = '''
import sys

class BlockNative:
    """A meta-path finder that refuses the optional OCR wheels."""

    blocked = {"pypdfium2", "pypdfium2_raw", "PIL"}

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.blocked:
            raise ImportError(f"No module named {name!r} (blocked by the test)")
        return None

sys.meta_path.insert(0, BlockNative())
'''


class RecordingServer:
    """Stands in for uvicorn: remembers the call and returns."""

    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, str, int]] = []

    def __call__(self, app: FastAPI, *, host: str, port: int) -> None:
        self.calls.append((app, host, port))


def run_script(body: str, workspace: Path) -> dict[str, Any]:
    """Run one Python script in a fresh interpreter and read the JSON it printed.

    A subprocess rather than ``monkeypatch``, because what is under test is what a
    *process* imports. A non-zero exit is a failure carrying the child's stderr.
    """
    script = workspace / "probe.py"
    script.write_text(body, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(workspace),
    )
    assert run.returncode == 0, run.stderr
    printed: dict[str, Any] = json.loads(run.stdout)
    return printed


def stage(client: TestClient, data: bytes) -> str:
    response = client.post("/v1/uploads", files={"file": ("scan.pdf", data, PDF_MIME)})
    assert response.status_code == 200, response.text
    file_ref: str = response.json()["file_ref"]
    return file_ref


def document_envelope(file_ref: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": CAPTURE_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {"type": "document", "mime_type": PDF_MIME, "file_ref": file_ref},
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


def submit(client: TestClient, data: bytes, **overrides: object) -> Any:
    return client.post("/v1/captures", json=document_envelope(stage(client, data), **overrides))


@pytest.fixture
def recognizer() -> FakePdfPageOcr:
    """A recognizer that reads two pages, standing in for the real engine."""
    return FakePdfPageOcr(page_count=2, texts={1: "Recognized page one.\n", 2: "And page two.\n"})


@pytest.fixture
def default_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(build_local_app(tmp_path / "default")) as running:
        yield running


@pytest.fixture
def ocr_client(tmp_path: Path, recognizer: FakePdfPageOcr) -> Iterator[TestClient]:
    with TestClient(build_local_app(tmp_path / "ocr", pdf_ocr=recognizer)) as running:
        yield running


class TestTheCommandLine:
    def test_the_flag_defaults_to_off(self, tmp_path: Path) -> None:
        assert cli.parse_args(["--data-dir", str(tmp_path)]).pdf_ocr is False

    def test_the_flag_turns_it_on(self, tmp_path: Path) -> None:
        assert cli.parse_args(["--data-dir", str(tmp_path), "--pdf-ocr"]).pdf_ocr is True

    def test_the_flag_takes_no_value(self, tmp_path: Path) -> None:
        """Nothing about the policy is configurable from the command line."""
        with pytest.raises(SystemExit):
            cli.parse_args(["--data-dir", str(tmp_path), "--pdf-ocr", "eng"])

    def test_there_is_no_flag_for_languages_or_resolution(self, tmp_path: Path) -> None:
        for rejected in ("--ocr-languages", "--ocr-dpi", "--tesseract", "--ocr-timeout"):
            with pytest.raises(SystemExit):
                cli.parse_args(["--data-dir", str(tmp_path), rejected, "x"])

    def test_the_help_names_the_prerequisites(self) -> None:
        help_text = cli.build_parser().format_help()

        assert "'ocr' extra" in help_text
        assert "eng and rus" in help_text


class TestMainWiresTheRecognizerThrough:
    def test_the_flag_reaches_the_composition_root(
        self, tmp_path: Path, recognizer: FakePdfPageOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unimem_ocr, "build_tesseract_ocr", lambda: recognizer)
        server = RecordingServer()

        exit_code = cli.main(["--data-dir", str(tmp_path / "data"), "--pdf-ocr"], server=server)

        assert exit_code == 0
        with TestClient(server.calls[0][0]) as client:
            response = submit(client, SCAN)
        assert response.status_code == 201, response.text

    def test_the_app_it_builds_recognizes_with_the_adapter_it_was_handed(
        self, tmp_path: Path, recognizer: FakePdfPageOcr, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unimem_ocr, "build_tesseract_ocr", lambda: recognizer)
        server = RecordingServer()

        cli.main(["--data-dir", str(tmp_path / "data"), "--pdf-ocr"], server=server)

        with TestClient(server.calls[0][0]) as client:
            submit(client, SCAN)
            content = ContentObject.model_validate(
                client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
            )
        assert content.metadata[OCR_METADATA_KEY] == {
            "page_count": 2,
            "embedded_text_pages": [],
            "ocr_attempted_pages": [1, 2],
            "ocr_pages_without_text": [],
            "engine": "fake-ocr",
            "engine_version": "9.9.9",
            "rasterizer": "fake-raster",
            "rasterizer_version": "1.2.3",
            "settings": {"languages": "fake+fake"},
        }

    def test_without_the_flag_nothing_builds_a_recognizer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> object:
            raise AssertionError("a default start must not construct an OCR adapter")

        monkeypatch.setattr(unimem_ocr, "build_tesseract_ocr", refuse)

        cli.main(["--data-dir", str(tmp_path / "data")], server=RecordingServer())


class TestStartupRefusesWhenThePrerequisitesAreMissing:
    def test_a_missing_prerequisite_exits_rather_than_serving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing() -> object:
            raise OcrPrerequisiteError("language data for rus is missing")

        monkeypatch.setattr(unimem_ocr, "build_tesseract_ocr", missing)
        server = RecordingServer()

        with pytest.raises(SystemExit) as raised:
            cli.main(["--data-dir", str(tmp_path / "data"), "--pdf-ocr"], server=server)

        assert "language data for rus is missing" in str(raised.value)
        assert server.calls == []

    def test_it_fails_before_the_data_directory_is_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing is half-created: the refusal happens before any composition."""
        data_dir = tmp_path / "never-created"
        monkeypatch.setattr(
            unimem_ocr,
            "build_tesseract_ocr",
            lambda: (_ for _ in ()).throw(OcrPrerequisiteError("no engine")),
        )

        with pytest.raises(SystemExit):
            cli.main(["--data-dir", str(data_dir), "--pdf-ocr"], server=RecordingServer())

        assert not data_dir.exists()

    def test_it_never_silently_disables_ocr_and_starts_anyway(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The negative assertion: no fallback to the refusing build."""
        monkeypatch.setattr(
            unimem_ocr,
            "build_tesseract_ocr",
            lambda: (_ for _ in ()).throw(OcrPrerequisiteError("no engine")),
        )
        server = RecordingServer()

        with pytest.raises(SystemExit):
            cli.main(["--data-dir", str(tmp_path), "--pdf-ocr"], server=server)

        assert server.calls == []


class TestTheDefaultDeploymentIsUnchanged:
    def test_a_textless_pdf_is_still_refused(self, default_client: TestClient) -> None:
        response = submit(default_client, SCAN)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_the_refusal_still_says_this_build_does_no_ocr(
        self, default_client: TestClient
    ) -> None:
        response = submit(default_client, SCAN)

        assert "does not perform OCR" in response.json()["error"]["message"]

    def test_an_ordinary_text_pdf_is_processed_by_the_default_processor(
        self, default_client: TestClient
    ) -> None:
        assert submit(default_client, TEXT_DOCUMENT).status_code == 201

        content = ContentObject.model_validate(
            default_client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )
        assert [record.processor for record in content.processing] == ["pdf"]
        assert OCR_METADATA_KEY not in content.metadata

    def test_the_same_scan_succeeds_in_an_ocr_enabled_deployment(
        self, ocr_client: TestClient
    ) -> None:
        """The same bytes, the same envelope, a different deployment."""
        assert submit(ocr_client, SCAN).status_code == 201

        content = ContentObject.model_validate(
            ocr_client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )
        assert [record.processor for record in content.processing] == ["pdf-ocr"]

    def test_an_ordinary_text_pdf_is_processed_identically_in_both(
        self, default_client: TestClient, ocr_client: TestClient
    ) -> None:
        submit(default_client, TEXT_DOCUMENT)
        submit(ocr_client, TEXT_DOCUMENT)

        def texts(client: TestClient) -> list[str | None]:
            content = ContentObject.model_validate(
                client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
            )
            return [segment.text for segment in content.segments]

        assert texts(default_client) == texts(ocr_client)


class TestNoRequestCanSwitchTheEngine:
    def test_an_unknown_envelope_field_is_rejected_outright(
        self, default_client: TestClient
    ) -> None:
        response = default_client.post(
            "/v1/captures",
            json=document_envelope(stage(default_client, SCAN), pdf_ocr=True),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_an_unknown_payload_field_is_rejected_outright(
        self, default_client: TestClient
    ) -> None:
        body = document_envelope(stage(default_client, SCAN))
        payload = body["payload"]
        assert isinstance(payload, dict)
        payload["ocr"] = "eng+rus"

        response = default_client.post("/v1/captures", json=body)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_an_intent_asking_for_analysis_does_not_enable_recognition(
        self, default_client: TestClient
    ) -> None:
        """``intent`` says what the user wants done, not which processor runs."""
        response = default_client.post(
            "/v1/captures",
            json=document_envelope(
                stage(default_client, SCAN),
                intent={"action": "analyze", "tags": ["ocr", "pdf-ocr", "tesseract"]},
            ),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "processing_failed"

    def test_a_client_declaring_an_image_mime_type_is_not_routed_to_ocr(
        self, ocr_client: TestClient
    ) -> None:
        """The recognizer claims ``application/pdf`` and nothing else."""
        body = document_envelope(stage(ocr_client, SCAN))
        payload = body["payload"]
        assert isinstance(payload, dict)
        payload["mime_type"] = "image/png"

        response = ocr_client.post("/v1/captures", json=body)

        assert response.status_code >= 400

    def test_the_recorded_settings_come_from_the_adapter_not_the_request(
        self, ocr_client: TestClient, recognizer: FakePdfPageOcr
    ) -> None:
        submit(
            ocr_client,
            SCAN,
            intent={"action": "save", "tags": ["languages=deu", "dpi=1200"]},
        )

        content = ContentObject.model_validate(
            ocr_client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
        )
        recorded = content.metadata[OCR_METADATA_KEY]
        assert isinstance(recorded, dict)
        assert recorded["settings"] == dict(recognizer.settings)


class TestAnOrdinaryInstallationWithoutTheExtra:
    """The optional boundary, checked in interpreters rather than in source text."""

    def test_importing_the_application_loads_no_rasterizer(self, tmp_path: Path) -> None:
        """No blocker here: ``pypdfium2`` *is* installed and must stay unloaded.

        The rasterizer is the marker, and Pillow deliberately is not: ``pypdf`` —
        an unconditional dependency — imports it whenever it happens to be
        present, so its appearance in ``sys.modules`` says nothing about the OCR
        extra either way. What matters is that a machine which *has* installed the
        extra still does not load the renderer until something asks to recognize a
        page.
        """
        result = run_script(
            """
import json
import sys

import unimem_api
import unimem_api.__main__
import unimem_api.wiring
import unimem_ocr
import core.processing

print(json.dumps({
    "loaded": sorted(name for name in sys.modules if name.split(".")[0] == "pypdfium2"),
}))
""",
            tmp_path,
        )

        assert result["loaded"] == []

    def test_the_default_deployment_works_with_the_native_packages_absent(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "text.pdf").write_bytes(TEXT_DOCUMENT)
        (tmp_path / "scan.pdf").write_bytes(SCAN)

        result = run_script(
            BLOCK_NATIVE
            + """
import json
from pathlib import Path

from fastapi.testclient import TestClient

from unimem_api import build_local_app

app = build_local_app(Path("data"))
out = {}
with TestClient(app) as client:
    for name, path in (("text", "text.pdf"), ("scan", "scan.pdf")):
        data = Path(path).read_bytes()
        upload = client.post(
            "/v1/uploads", files={"file": (path, data, "application/pdf")}
        )
        envelope = {
            "schema_version": "0.2",
            "id": f"cap_{name}",
            "source": {"type": "upload", "provider": "curl"},
            "payload": {
                "type": "document",
                "mime_type": "application/pdf",
                "file_ref": upload.json()["file_ref"],
            },
            "context": {"captured_at": "2026-05-06T07:08:09+00:00"},
        }
        response = client.post("/v1/captures", json=envelope)
        out[name] = [response.status_code, response.json()]
print(json.dumps(out))
""",
            tmp_path,
        )

        assert result["text"][0] == 201
        assert result["scan"][0] == 422
        assert result["scan"][1]["error"]["code"] == "processing_failed"

    def test_asking_for_ocr_without_the_extra_fails_startup_clearly(self, tmp_path: Path) -> None:
        result = run_script(
            BLOCK_NATIVE
            + """
import json

from unimem_api.__main__ import main


def never(app, *, host, port):
    raise AssertionError("the server must not start")


try:
    main(["--data-dir", "data", "--pdf-ocr"], server=never)
except SystemExit as exit_request:
    print(json.dumps({"exit": str(exit_request)}))
else:
    print(json.dumps({"exit": None}))
""",
            tmp_path,
        )

        message = result["exit"]
        assert message is not None
        assert "--pdf-ocr was requested but local OCR is unavailable" in message
        assert "capture-core[ocr]" in message
        assert "pypdfium2" in message
        assert not (tmp_path / "data").exists()
