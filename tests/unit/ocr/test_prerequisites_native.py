"""The startup gate, checked in real interpreters where one optional wheel is gone.

:mod:`tests.unit.ocr.test_prerequisites` covers the engine and language probes with
a fake executable and needs nothing installed. This module covers the other half —
the *package* check — and it cannot be done with monkeypatching alone, because the
question is what a **process** can import.

The specific hole this file exists for: ``unimem_ocr.tesseract`` imports PDFium and
names no PIL symbol, and ``pypdfium2`` defers loading Pillow until ``to_pil()`` is
actually called. So importing the adapter proved only half of the extra, and a
machine with ``pypdfium2`` installed and ``Pillow`` missing used to start an
OCR-enabled server that then failed on the first scanned page.

Each test therefore runs a fresh interpreter with a meta-path finder that blocks
exactly one distribution and leaves the other genuinely importable — the defect
would be masked if both were missing, since the surviving PDFium check would catch
it. Nothing is mocked: the production
:func:`unimem_ocr.prerequisites.require_rasterizer` runs, reached through the
production :func:`unimem_ocr.build_tesseract_ocr` and, in one test, through the
production ``python -m unimem_api --pdf-ocr`` entry point.

The module needs the optional extra installed to be meaningful, so it obeys the
required-mode policy in :mod:`tests.ocr_support` like every other native module.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

from tests import ocr_support, pdfs

ocr_support.require_rasterizer()

#: Installs a finder that refuses one distribution and nothing else.
#:
#: ``ModuleNotFoundError`` with ``name=`` is what CPython itself raises for an
#: absent module, and the production code reads ``exc.name`` to say what is
#: missing — so a blocker that raised a bare ``ImportError`` would quietly test a
#: different error than the real one.
BLOCK: Final = """
import sys

class BlockOne:
    def __init__(self, distribution):
        self.blocked = distribution

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.blocked:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None

sys.meta_path.insert(0, BlockOne({blocked!r}))
"""

#: Blocking Pillow: the rasterizer stays installed and importable.
BLOCK_PILLOW: Final = BLOCK.format(blocked=("PIL",))

#: Blocking the rasterizer: Pillow stays installed, which is the mirror case.
BLOCK_PDFIUM: Final = BLOCK.format(blocked=("pypdfium2", "pypdfium2_raw"))


def run(body: str, workspace: Path) -> dict[str, Any]:
    """Run one script in a fresh interpreter and read the JSON it printed."""
    script = workspace / "probe.py"
    script.write_text(body, encoding="utf-8")
    finished = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(workspace),
    )
    assert finished.returncode == 0, finished.stderr
    printed: dict[str, Any] = json.loads(finished.stdout)
    return printed


class TestPillowMustBeProvenNotAssumed:
    def test_the_rasterizer_really_is_available_in_the_blocked_interpreter(
        self, tmp_path: Path
    ) -> None:
        """The precondition, asserted rather than assumed.

        If PDFium were unavailable too, the pre-existing check would refuse and this
        file would prove nothing about Pillow.
        """
        result = run(
            BLOCK_PILLOW
            + """
import importlib.util, json
found = importlib.util.find_spec("pypdfium2") is not None
import pypdfium2
try:
    import PIL.Image
    pillow = True
except ModuleNotFoundError:
    pillow = False
print(json.dumps({"pdfium_spec": found, "pdfium_imported": True, "pillow": pillow}))
""",
            tmp_path,
        )

        assert result["pdfium_spec"] is True
        assert result["pdfium_imported"] is True
        assert result["pillow"] is False

    def test_production_construction_refuses_when_pillow_is_absent(self, tmp_path: Path) -> None:
        """The regression: ``build_tesseract_ocr()`` must not succeed here."""
        result = run(
            BLOCK_PILLOW
            + """
import json

from unimem_ocr import OcrPrerequisiteError, build_tesseract_ocr

out = {}
try:
    build_tesseract_ocr()
    out["outcome"] = "built"
except OcrPrerequisiteError as exc:
    out["outcome"] = "refused"
    out["message"] = str(exc)
    out["cause_type"] = type(exc.__cause__).__name__
    out["cause_name"] = getattr(exc.__cause__, "name", None)
print(json.dumps(out))
""",
            tmp_path,
        )

        assert result["outcome"] == "refused"
        assert "PIL" in result["message"]
        assert "Pillow" in result["message"]
        assert "capture-core[ocr]" in result["message"]

    def test_the_original_importerror_is_preserved_as_the_cause(self, tmp_path: Path) -> None:
        result = run(
            BLOCK_PILLOW
            + """
import json

from unimem_ocr import OcrPrerequisiteError, build_tesseract_ocr

try:
    build_tesseract_ocr()
    print(json.dumps({"outcome": "built"}))
except OcrPrerequisiteError as exc:
    print(json.dumps({
        "outcome": "refused",
        "cause_type": type(exc.__cause__).__name__,
        "cause_name": getattr(exc.__cause__, "name", None),
        "cause_is_import_error": isinstance(exc.__cause__, ImportError),
    }))
""",
            tmp_path,
        )

        assert result["outcome"] == "refused"
        assert result["cause_is_import_error"] is True
        assert result["cause_type"] == "ModuleNotFoundError"
        assert result["cause_name"] == "PIL"

    def test_the_pdfium_check_is_preserved(self, tmp_path: Path) -> None:
        """The mirror case, so fixing Pillow did not replace the existing check."""
        result = run(
            BLOCK_PDFIUM
            + """
import json

import PIL.Image  # Pillow stays available; only the rasterizer is gone.

from unimem_ocr import OcrPrerequisiteError, build_tesseract_ocr

try:
    build_tesseract_ocr()
    print(json.dumps({"outcome": "built"}))
except OcrPrerequisiteError as exc:
    print(json.dumps({
        "outcome": "refused",
        "message": str(exc),
        "cause_name": getattr(exc.__cause__, "name", None),
    }))
""",
            tmp_path,
        )

        assert result["outcome"] == "refused"
        assert "pypdfium2" in result["message"]
        assert result["cause_name"] == "pypdfium2"


class TestTheProductionEntryPointRefusesToStart:
    def test_the_cli_exits_and_creates_no_application_data(self, tmp_path: Path) -> None:
        """Through ``main([... --pdf-ocr])``, the path a deployment actually takes.

        Nothing is mocked: the real argument parsing, the real
        ``build_pdf_ocr()``, the real prerequisite check. The server callable is
        replaced only to assert it is never reached.
        """
        result = run(
            BLOCK_PILLOW
            + """
import json
from pathlib import Path

from unimem_api.__main__ import main


def never(app, *, host, port):
    raise AssertionError("the server must not start")


out = {}
try:
    main(["--data-dir", "data", "--pdf-ocr"], server=never)
    out["outcome"] = "started"
except SystemExit as exit_request:
    out["outcome"] = "exited"
    out["message"] = str(exit_request)
out["data_dir_exists"] = Path("data").exists()
print(json.dumps(out))
""",
            tmp_path,
        )

        assert result["outcome"] == "exited"
        assert "--pdf-ocr was requested but local OCR is unavailable" in result["message"]
        assert "PIL" in result["message"]
        assert result["data_dir_exists"] is False
        assert not (tmp_path / "data").exists()

    def test_default_startup_still_works_with_pillow_absent(self, tmp_path: Path) -> None:
        """The other half of the boundary: a default deployment is unaffected."""
        (tmp_path / "text.pdf").write_bytes(pdfs.one_page_pdf())
        (tmp_path / "scan.pdf").write_bytes(pdfs.textless_pdf())

        result = run(
            BLOCK_PILLOW
            + """
import json
from pathlib import Path

from fastapi.testclient import TestClient

from unimem_api import build_local_app

out = {}
with TestClient(build_local_app(Path("data"))) as client:
    for name, path in (("text", "text.pdf"), ("scan", "scan.pdf")):
        data = Path(path).read_bytes()
        upload = client.post(
            "/v1/uploads", files={"file": (path, data, "application/pdf")}
        )
        response = client.post("/v1/captures", json={
            "schema_version": "0.2",
            "id": f"cap_{name}",
            "source": {"type": "upload", "provider": "curl"},
            "payload": {
                "type": "document",
                "mime_type": "application/pdf",
                "file_ref": upload.json()["file_ref"],
            },
            "context": {"captured_at": "2026-05-06T07:08:09+00:00"},
        })
        out[name] = [response.status_code, response.json()]
print(json.dumps(out))
""",
            tmp_path,
        )

        assert result["text"][0] == 201
        assert result["scan"][0] == 422
        assert result["scan"][1]["error"]["code"] == "processing_failed"
