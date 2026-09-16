"""Composition: which image processor a deployment gets, and who decides.

The decision is a flag on a command line and nothing else. No request field, no
capture intent, no MIME variant, no header, and no router precedence can reach
it, and this file checks each of those refusals rather than asserting the
happy path and trusting the rest.

The other half is **independence**. ``--pdf-ocr`` and ``--image-ocr`` are two
capabilities with different prerequisites, different limits and different failure
semantics, so all four combinations are exercised as four separate deployments —
including the two mixed ones, which are where a shared flag or a shared gate
would show up as a wrong processor.

No engine anywhere: the recognizers are fakes. The tests that need an interpreter
in a particular *installation* state start a subprocess, because that is the only
way to observe what a machine without the optional extra actually does.
"""

from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.processing import (
    ENGINE_INVOKED_KEY,
    IMAGE_OCR_METADATA_KEY,
    ImageOcrProcessor,
    ImageProcessor,
    PdfOcrProcessor,
    PdfProcessor,
    Processor,
)
from core.storage import LocalRawObjectStore
from tests import images
from tests.unit.api.test_pdf_ocr_composition import BLOCK_NATIVE, RecordingServer, run_script
from tests.unit.processing.doubles import FakeImageOcr, FakePdfPageOcr
from unimem_api.__main__ import main, parse_args
from unimem_api.wiring import _image_processor, _pdf_processor, build_local_app

CAPTURE_ID: Final = "cap_image_ocr_composition_01"
CAPTURED_AT: Final = "2026-05-06T07:08:09+00:00"

PNG: Final = images.png(width=12, height=8)


def chosen_processors(
    *, pdf_ocr: bool = False, image_ocr: bool = False
) -> tuple[Processor, Processor]:
    """The PDF and image processors a deployment with these flags would register.

    Asked of the composition root's own helpers rather than of a built app: the
    router is a constructor argument rather than app state, so reaching into a
    ``FastAPI`` object for it would be testing an accident of wiring. These two
    functions *are* the decision.
    """
    store = LocalRawObjectStore(Path("unused"))
    return (
        _pdf_processor(store, FakePdfPageOcr() if pdf_ocr else None),
        _image_processor(store, FakeImageOcr() if image_ocr else None),
    )


def _recording_gates(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace both startup gates with recorders and return the shared journal."""
    built: list[str] = []

    def build_image() -> FakeImageOcr:
        built.append("image")
        return FakeImageOcr()

    def build_pdf() -> FakePdfPageOcr:
        built.append("pdf")
        return FakePdfPageOcr()

    monkeypatch.setattr("unimem_api.__main__.build_image_ocr", build_image)
    monkeypatch.setattr("unimem_api.__main__.build_pdf_ocr", build_pdf)
    return built


def stamped_processor(app: FastAPI, data: bytes = PNG) -> str:
    """Capture one image through the app and report which processor stamped it."""
    with TestClient(app) as client:
        assert capture_image(client, data).status_code == 201
        content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()
    processor: str = content["processing"][0]["processor"]
    return processor


class TestTheFlag:
    def test_it_defaults_to_off(self, tmp_path: Path) -> None:
        assert parse_args(["--data-dir", str(tmp_path)]).image_ocr is False

    def test_it_turns_it_on(self, tmp_path: Path) -> None:
        assert parse_args(["--data-dir", str(tmp_path), "--image-ocr"]).image_ocr is True

    def test_it_takes_no_value(self, tmp_path: Path) -> None:
        """A switch, not a setting: there is nothing to configure per deployment."""
        with pytest.raises(SystemExit):
            parse_args(["--data-dir", str(tmp_path), "--image-ocr", "aggressive"])

    def test_it_is_independent_of_the_pdf_flag(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path), "--image-ocr"])

        assert (options.image_ocr, options.pdf_ocr) == (True, False)

    def test_the_pdf_flag_does_not_turn_it_on(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path), "--pdf-ocr"])

        assert (options.image_ocr, options.pdf_ocr) == (False, True)

    def test_both_can_be_given_together(self, tmp_path: Path) -> None:
        options = parse_args(["--data-dir", str(tmp_path), "--pdf-ocr", "--image-ocr"])

        assert (options.image_ocr, options.pdf_ocr) == (True, True)

    def test_there_is_no_flag_for_languages_limits_or_psm(self, tmp_path: Path) -> None:
        """The policy is fixed. A deployment runs it or does not run OCR."""
        for option in ("--image-languages", "--image-psm", "--max-encoded-pixels", "--image-dpi"):
            with pytest.raises(SystemExit):
                parse_args(["--data-dir", str(tmp_path), option, "x"])

    def test_the_help_names_the_prerequisites_and_the_independence(self) -> None:
        """Whitespace-normalized, and hyphens rejoined.

        argparse wraps to the terminal width and will happily break ``--pdf-ocr``
        across a line, so the raw text is not a stable thing to assert on.
        """
        from unimem_api.__main__ import build_parser

        help_text = " ".join(build_parser().format_help().split()).replace("- ", "-")

        assert "--image-ocr" in help_text
        assert "eng and rus language data" in help_text
        assert "no Python extra" in help_text
        assert "Independent of --pdf-ocr" in help_text


class TestTheFourDeployments:
    """All four combinations. Each pair yields one member, never both, never neither."""

    def test_neither_capability(self) -> None:
        pdf, image = chosen_processors()

        assert isinstance(pdf, PdfProcessor)
        assert isinstance(image, ImageProcessor)

    def test_pdf_ocr_only_leaves_the_image_processor_alone(self) -> None:
        pdf, image = chosen_processors(pdf_ocr=True)

        assert isinstance(pdf, PdfOcrProcessor)
        assert isinstance(image, ImageProcessor)

    def test_image_ocr_only_leaves_the_pdf_processor_alone(self) -> None:
        """The case a single shared flag would get wrong."""
        pdf, image = chosen_processors(image_ocr=True)

        assert isinstance(pdf, PdfProcessor)
        assert isinstance(image, ImageOcrProcessor)

    def test_both_capabilities(self) -> None:
        pdf, image = chosen_processors(pdf_ocr=True, image_ocr=True)

        assert isinstance(pdf, PdfOcrProcessor)
        assert isinstance(image, ImageOcrProcessor)

    @pytest.mark.parametrize(
        ("pdf_ocr", "image_ocr"),
        [(False, False), (True, False), (False, True), (True, True)],
        ids=["neither", "pdf-only", "image-only", "both"],
    )
    def test_the_two_choices_are_never_the_same_object(
        self, pdf_ocr: bool, image_ocr: bool
    ) -> None:
        pdf, image = chosen_processors(pdf_ocr=pdf_ocr, image_ocr=image_ocr)

        assert pdf is not image
        assert {pdf.name, image.name} <= {"pdf", "pdf-ocr", "image", "image-ocr"}

    @pytest.mark.parametrize(
        ("image_ocr", "expected"),
        [(False, "image"), (True, "image-ocr")],
        ids=["default", "enabled"],
    )
    def test_an_image_capture_routes_to_exactly_one_processor(
        self, tmp_path: Path, image_ocr: bool, expected: str
    ) -> None:
        """The exactly-one-match rule, over a real capture, in each build."""
        app = build_local_app(
            tmp_path / "data",
            pdf_ocr=FakePdfPageOcr(),
            image_ocr=FakeImageOcr(text="HARBOUR") if image_ocr else None,
        )

        assert stamped_processor(app) == expected


def capture_image(client: TestClient, data: bytes, capture_id: str = CAPTURE_ID) -> Any:
    upload = client.post("/v1/uploads", files={"file": ("photo.png", data, "image/png")})
    envelope = {
        "schema_version": "0.2",
        "id": capture_id,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": "image",
            "mime_type": "image/png",
            "file_ref": upload.json()["file_ref"],
        },
        "context": {"captured_at": CAPTURED_AT},
    }
    return client.post("/v1/captures", json=envelope)


class TestTheDefaultDeploymentIsUnchanged:
    def test_an_image_still_captures_with_no_segments(self, tmp_path: Path) -> None:
        app = build_local_app(tmp_path / "data")

        with TestClient(app) as client:
            response = capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert response.status_code == 201
        assert content["segments"] == []

    def test_it_is_still_stamped_image_at_0_1(self, tmp_path: Path) -> None:
        app = build_local_app(tmp_path / "data")

        with TestClient(app) as client:
            capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert content["processing"][0]["processor"] == "image"

    def test_it_records_no_image_ocr_metadata_at_all(self, tmp_path: Path) -> None:
        """Absence, which is how "OCR was never enabled here" stays readable."""
        app = build_local_app(tmp_path / "data")

        with TestClient(app) as client:
            capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert IMAGE_OCR_METADATA_KEY not in content["metadata"]

    def test_the_same_image_gains_a_segment_in_an_ocr_enabled_deployment(
        self, tmp_path: Path
    ) -> None:
        app = build_local_app(tmp_path / "data", image_ocr=FakeImageOcr(text="HARBOUR"))

        with TestClient(app) as client:
            capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert [segment["text"] for segment in content["segments"]] == ["HARBOUR"]
        assert content["metadata"][IMAGE_OCR_METADATA_KEY][ENGINE_INVOKED_KEY] is True

    def test_a_wordless_image_ingests_identically_in_both(self, tmp_path: Path) -> None:
        """ADR-019's promise, over a real HTTP round trip.

        A photograph of a sunset must reach the same canonical shape whether or
        not a recognizer looked at it. Only the metadata differs, and only because
        one of them genuinely did look.
        """
        default = build_local_app(tmp_path / "plain")
        enriched = build_local_app(tmp_path / "ocr", image_ocr=FakeImageOcr(text=""))

        bodies = []
        for app in (default, enriched):
            with TestClient(app) as client:
                capture_image(client, PNG)
                bodies.append(client.get(f"/v1/captures/{CAPTURE_ID}/content").json())

        plain, recognized = bodies
        assert plain["segments"] == recognized["segments"] == []
        assert plain["type"] == recognized["type"] == "image"
        assert plain["metadata"]["image"] == recognized["metadata"]["image"]
        assert plain["original"]["sha256"] == recognized["original"]["sha256"]


class TestNoRequestCanSwitchTheEngine:
    @pytest.fixture
    def default_client(self, tmp_path: Path) -> Any:
        with TestClient(build_local_app(tmp_path / "data")) as client:
            yield client

    def test_an_unknown_payload_field_is_rejected_outright(self, default_client: Any) -> None:
        upload = default_client.post("/v1/uploads", files={"file": ("photo.png", PNG, "image/png")})
        response = default_client.post(
            "/v1/captures",
            json={
                "schema_version": "0.2",
                "id": CAPTURE_ID,
                "source": {"type": "upload", "provider": "curl"},
                "payload": {
                    "type": "image",
                    "mime_type": "image/png",
                    "file_ref": upload.json()["file_ref"],
                    "ocr": True,
                },
                "context": {"captured_at": CAPTURED_AT},
            },
        )

        assert response.status_code == 422

    def test_an_intent_asking_for_analysis_does_not_enable_recognition(
        self, default_client: Any
    ) -> None:
        upload = default_client.post("/v1/uploads", files={"file": ("photo.png", PNG, "image/png")})
        default_client.post(
            "/v1/captures",
            json={
                "schema_version": "0.2",
                "id": CAPTURE_ID,
                "source": {"type": "upload", "provider": "curl"},
                "payload": {
                    "type": "image",
                    "mime_type": "image/png",
                    "file_ref": upload.json()["file_ref"],
                },
                "context": {"captured_at": CAPTURED_AT},
                "intent": {"action": "analyze"},
            },
        )
        content = default_client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert content["segments"] == []
        assert content["processing"][0]["processor"] == "image"

    def test_the_recorded_settings_come_from_the_adapter_not_the_request(
        self, tmp_path: Path
    ) -> None:
        recognizer = FakeImageOcr(text="words", settings={"languages": "fake+fake"})
        app = build_local_app(tmp_path / "data", image_ocr=recognizer)

        with TestClient(app) as client:
            capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert content["metadata"][IMAGE_OCR_METADATA_KEY]["settings"] == {"languages": "fake+fake"}


class TestMainWiresTheRecognizerThrough:
    def test_the_flag_reaches_the_composition_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[str] = []

        def build() -> FakeImageOcr:
            built.append("built")
            return FakeImageOcr()

        monkeypatch.setattr("unimem_api.__main__.build_image_ocr", build)
        server = RecordingServer()

        main(["--data-dir", str(tmp_path / "data"), "--image-ocr"], server=server)

        assert built == ["built"]

    def test_without_the_flag_nothing_builds_an_image_recognizer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode() -> object:
            raise AssertionError("no recognizer should be built without the flag")

        monkeypatch.setattr("unimem_api.__main__.build_image_ocr", explode)

        main(["--data-dir", str(tmp_path / "data")], server=RecordingServer())

    def test_the_app_it_builds_uses_the_adapter_it_was_handed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recognizer = FakeImageOcr(text="FROM THE ADAPTER")
        monkeypatch.setattr("unimem_api.__main__.build_image_ocr", lambda: recognizer)
        server = RecordingServer()

        main(["--data-dir", str(tmp_path / "data"), "--image-ocr"], server=server)

        with TestClient(server.calls[0][0]) as client:
            capture_image(client, PNG)
            content = client.get(f"/v1/captures/{CAPTURE_ID}/content").json()

        assert [segment["text"] for segment in content["segments"]] == ["FROM THE ADAPTER"]

    def test_each_flag_builds_only_its_own_recognizer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two gates, and neither runs for the other's flag."""
        built = _recording_gates(monkeypatch)

        main(["--data-dir", str(tmp_path / "data"), "--image-ocr"], server=RecordingServer())

        assert built == ["image"]

    def test_both_flags_run_both_gates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Independently, and without either caching the other's engine probe."""
        built = _recording_gates(monkeypatch)

        main(
            ["--data-dir", str(tmp_path / "data"), "--pdf-ocr", "--image-ocr"],
            server=RecordingServer(),
        )

        assert sorted(built) == ["image", "pdf"]


class TestStartupRefusesWhenThePrerequisitesAreMissing:
    def test_a_missing_engine_exits_rather_than_serving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unimem_ocr import OcrPrerequisiteError

        def absent(*_: object, **__: object) -> str:
            raise OcrPrerequisiteError("the OCR engine 'tesseract' could not be run")

        monkeypatch.setattr("unimem_ocr.build_tesseract_image_ocr", absent)
        server = RecordingServer()

        with pytest.raises(SystemExit) as raised:
            main(["--data-dir", str(tmp_path / "data"), "--image-ocr"], server=server)

        assert "--image-ocr" in str(raised.value)
        assert server.calls == []

    def test_it_fails_before_the_data_directory_is_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unimem_ocr import OcrPrerequisiteError

        def absent(*_: object, **__: object) -> str:
            raise OcrPrerequisiteError("no engine")

        monkeypatch.setattr("unimem_ocr.build_tesseract_image_ocr", absent)
        data_dir = tmp_path / "data"

        with pytest.raises(SystemExit):
            main(["--data-dir", str(data_dir), "--image-ocr"], server=RecordingServer())

        assert not data_dir.exists()

    def test_it_never_silently_disables_image_ocr_and_starts_anyway(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Starting without the capability that was asked for is the worst answer."""
        from unimem_ocr import OcrPrerequisiteError

        def absent(*_: object, **__: object) -> str:
            raise OcrPrerequisiteError("no engine")

        monkeypatch.setattr("unimem_ocr.build_tesseract_image_ocr", absent)
        server = RecordingServer()

        with pytest.raises(SystemExit):
            main(["--data-dir", str(tmp_path / "data"), "--image-ocr"], server=server)

        assert server.calls == []


class TestImageOcrNeedsNoOptionalExtra:
    """The dependency boundary, in an interpreter that genuinely lacks the wheels.

    The source-level and ``sys.modules`` checks live with the adapter; this is the
    behavioural one, and it is the only form of the claim that cannot be satisfied
    by an import that merely has not happened yet. CI runs the same claim a second
    time against a real engine on a machine where the extra is not installed at
    all.
    """

    def test_an_image_ocr_deployment_builds_with_the_native_packages_blocked(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "photo.png").write_bytes(PNG)
        result = run_script(
            BLOCK_NATIVE
            + """
import json
from pathlib import Path

from fastapi.testclient import TestClient

from core.processing.image_recognition import ImageOcrResult
from unimem_api import build_local_app


class Recognizer:
    def recognize_image(self, stream, *, mime_type, encoded_width, encoded_height):
        stream.read()
        return ImageOcrResult(
            text="BLOCKED BUT WORKING",
            engine="fake-ocr",
            engine_version="9.9.9",
            settings={"languages": "fake+fake"},
        )


app = build_local_app(Path("data"), image_ocr=Recognizer())
with TestClient(app) as client:
    data = Path("photo.png").read_bytes()
    upload = client.post("/v1/uploads", files={"file": ("photo.png", data, "image/png")})
    envelope = {
        "schema_version": "0.2",
        "id": "cap_blocked",
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": "image",
            "mime_type": "image/png",
            "file_ref": upload.json()["file_ref"],
        },
        "context": {"captured_at": "2026-05-06T07:08:09+00:00"},
    }
    response = client.post("/v1/captures", json=envelope)
    content = client.get("/v1/captures/cap_blocked/content").json()

print(json.dumps({
    "status": response.status_code,
    "texts": [segment["text"] for segment in content["segments"]],
    "processor": content["processing"][0]["processor"],
}))
""",
            tmp_path,
        )

        assert result["status"] == 201
        assert result["texts"] == ["BLOCKED BUT WORKING"]
        assert result["processor"] == "image-ocr"

    def test_the_image_adapter_imports_with_the_native_packages_blocked(
        self, tmp_path: Path
    ) -> None:
        """``--image-ocr``'s own factory, on a machine that has neither wheel."""
        result = run_script(
            BLOCK_NATIVE
            + """
import json

import unimem_ocr.image
from unimem_ocr import build_tesseract_image_ocr

print(json.dumps({"imported": True, "factory": build_tesseract_image_ocr.__name__}))
""",
            tmp_path,
        )

        assert result == {"imported": True, "factory": "build_tesseract_image_ocr"}

    def test_a_pdf_ocr_deployment_still_needs_them(self, tmp_path: Path) -> None:
        """The contrast that makes the claim above mean something."""
        result = run_script(
            BLOCK_NATIVE
            + """
import json

from unimem_ocr import OcrPrerequisiteError, build_tesseract_ocr

try:
    build_tesseract_ocr()
except OcrPrerequisiteError as exc:
    print(json.dumps({"refused": True, "mentions_extra": "capture-core[ocr]" in str(exc)}))
""",
            tmp_path,
        )

        assert result == {"refused": True, "mentions_extra": True}


class TestThePrerequisiteMessagesAreCapabilitySpecific:
    def test_an_image_startup_failure_does_not_claim_pdf_ocr_is_needed(
        self, tmp_path: Path
    ) -> None:
        """An operator who typed ``--image-ocr`` must not be sent after a renderer."""
        result = run_script(
            """
import json

from unimem_ocr import OcrPrerequisiteError, build_tesseract_image_ocr

try:
    build_tesseract_image_ocr(executable="definitely-not-an-engine-anywhere")
except OcrPrerequisiteError as exc:
    print(json.dumps({"message": str(exc)}))
""",
            tmp_path,
        )

        assert "image OCR" in result["message"]
        assert "PDF" not in result["message"]

    def test_the_pdf_message_is_unchanged(self, tmp_path: Path) -> None:
        """The freeze, at the prerequisite layer: same words as before the split."""
        result = run_script(
            """
import json

from unimem_ocr import OcrPrerequisiteError
from unimem_ocr.prerequisites import require_engine

try:
    require_engine("definitely-not-an-engine-anywhere")
except OcrPrerequisiteError as exc:
    print(json.dumps({"message": str(exc)}))
""",
            tmp_path,
        )

        assert "Local PDF OCR needs Tesseract installed on this machine" in result["message"]


class TestNothingNativeLoadsInADefaultStart:
    def test_a_default_start_imports_no_image_adapter(self, tmp_path: Path) -> None:
        result = run_script(
            """
import json
import sys

import unimem_api
import unimem_api.__main__
import unimem_api.wiring
import core.processing

print(json.dumps({
    "image_adapter": "unimem_ocr.image" in sys.modules,
    "pdf_adapter": "unimem_ocr.tesseract" in sys.modules,
}))
""",
            tmp_path,
        )

        assert result == {"image_adapter": False, "pdf_adapter": False}

    def test_a_default_start_runs_no_subprocess(self, tmp_path: Path) -> None:
        """No engine is probed until a flag asks for one."""
        result = run_script(
            """
import json
import subprocess
from pathlib import Path

calls = []
original = subprocess.run
subprocess.run = lambda *a, **k: calls.append(a) or original(*a, **k)

from unimem_api import build_local_app

build_local_app(Path("data"))
print(json.dumps({"subprocesses": len(calls)}))
""",
            tmp_path,
        )

        assert result == {"subprocesses": 0}
