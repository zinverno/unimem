import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.contracts import CaptureEnvelope, CaptureRecord, CaptureStatus, ContentObject
from core.intake.errors import UnsupportedCapturePayloadError
from core.persistence import ContentObjectPersistenceError
from tests.youtube_fixtures import URL, artifact
from unimem_youtube.errors import AcquisitionError, CapturePipelineError, ExportError
from unimem_youtube.export import CaptionMarkdownRenderer
from unimem_youtube.service import YoutubeCaptureService


def test_acquisition_dependency_absence_is_actionable_without_breaking_base(tmp_path: Path) -> None:
    script = """
import importlib.abc
import sys
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('youtube_transcript_api'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Missing())
from unimem_api.wiring import build_local_app
from unimem_youtube.__main__ import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            "capture",
            URL,
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "dependency_missing"
    assert result.stdout == ""
    assert not (tmp_path / "out").exists()


def test_provider_mutation_cannot_override_source(tmp_path: Path) -> None:
    material = artifact()
    material.source_url = "https://evil.test"
    with pytest.raises(AcquisitionError, match="Invalid caption artifact"):
        YoutubeCaptureService(tmp_path).capture(URL, ("ru",), acquire=lambda *_: material)


def test_artifact_byte_budget_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unimem_youtube.service.MAX_ARTIFACT_BYTES", 10)
    with pytest.raises(AcquisitionError) as failure:
        YoutubeCaptureService(tmp_path).capture(URL, ("ru",), acquire=lambda *_: artifact())
    assert failure.value.code == "response_limit"


def test_store_failure_keeps_processing_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = YoutubeCaptureService(tmp_path)

    def fail(content: ContentObject) -> None:
        raise ContentObjectPersistenceError("private path must not reach CLI")

    monkeypatch.setattr(service.content_store, "create", fail)
    with pytest.raises(CapturePipelineError) as failure:
        service.capture(URL, ("ru",), acquire=lambda *_: artifact())
    assert failure.value.code == "storage_failed"
    assert service.record_store.get(failure.value.capture_id).status is CaptureStatus.PROCESSING
    assert "private path" not in str(failure.value)


def test_intake_failure_is_not_claimed_as_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = YoutubeCaptureService(tmp_path)

    def fail(envelope: CaptureEnvelope) -> CaptureRecord:
        raise UnsupportedCapturePayloadError("unsupported")

    monkeypatch.setattr(service.intake, "accept", fail)
    with pytest.raises(CapturePipelineError) as failure:
        service.capture(URL, ("ru",), acquire=lambda *_: artifact())
    assert failure.value.code == "intake_failed"


@pytest.mark.parametrize("change", ["absent", "source", "track", "date"])
def test_renderer_does_not_invent_missing_or_damaged_metadata(tmp_path: Path, change: str) -> None:
    content = YoutubeCaptureService(tmp_path).capture(URL, ("ru",), acquire=lambda *_: artifact())
    facts = content.metadata["youtube_captions"]
    assert isinstance(facts, dict)
    if change == "absent":
        content.metadata.clear()
    elif change == "source":
        facts["video_id"] = "bad"
    elif change == "track":
        facts["track"] = {}
    else:
        facts["captured_at"] = "2026-09-29"
    with pytest.raises(ExportError):
        CaptionMarkdownRenderer().render(content)


def test_live_smoke_is_explicit_and_reports_failure_without_network() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/youtube_live_smoke.py",
            "https://evil.test",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"live_acceptance": "FAIL", "code": "invalid_url"}
