import pytest

pytest.importorskip("youtube_transcript_api")

import json
import subprocess
import sys
from pathlib import Path

from tests.youtube_fixtures import URL
from tests.youtube_http import happy_http
from unimem_youtube.__main__ import main
from unimem_youtube.service import YoutubeCaptureService


def test_real_retrieval_library_to_cli_and_fresh_process_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http = happy_http(monkeypatch)
    assert (
        main(
            [
                "capture",
                URL,
                "--languages",
                "ru",
                "en",
                "--data-dir",
                str(tmp_path / "data"),
                "--output-dir",
                str(tmp_path / "first"),
            ]
        )
        == 0
    )
    captured = json.loads(capsys.readouterr().out)
    assert len(http.requests) == 3
    assert captured["capture_status"] == "complete"
    assert captured["export_status"] == "complete"
    content = YoutubeCaptureService(tmp_path / "data").read(captured["capture_id"])
    assert content.id == captured["content_id"]
    assert content.segments[1].temporal is not None
    assert content.segments[1].temporal.end is None
    rendered = subprocess.run(
        [
            sys.executable,
            "-m",
            "unimem_youtube",
            "render",
            captured["capture_id"],
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "second"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    result = json.loads(rendered.stdout)
    assert Path(result["path"]).read_bytes() == Path(captured["path"]).read_bytes()


def test_capture_survives_export_error_and_can_be_rendered_later(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    happy_http(monkeypatch)
    unusable_output = tmp_path / "not-a-directory"
    unusable_output.write_text("keep me")
    assert (
        main(
            [
                "capture",
                URL,
                "--languages",
                "ru",
                "--data-dir",
                str(tmp_path / "data"),
                "--output-dir",
                str(unusable_output),
            ]
        )
        == 4
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["capture_status"] == "complete"
    assert failed["export_status"] == "failed"
    assert unusable_output.read_text() == "keep me"
    assert (
        main(
            [
                "render",
                failed["capture_id"],
                "--data-dir",
                str(tmp_path / "data"),
                "--output-dir",
                str(tmp_path / "good"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["content_id"] == failed["content_id"]


def test_cli_reports_identified_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    http = happy_http(monkeypatch)
    # The retrieval library accepts this float; our normalization rejects it.
    http.responses[-1] = (200, b'<transcript><text start="NaN">x</text></transcript>')
    assert (
        main(
            [
                "capture",
                URL,
                "--languages",
                "ru",
                "--data-dir",
                str(tmp_path / "data"),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 3
    )
    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "capture_incomplete"
    assert failure["code"] == "processing_failed"
    assert not (tmp_path / "out").exists()
    stored = YoutubeCaptureService(tmp_path / "data").record_store.get(failure["capture_id"])
    assert stored.status.value == "failed"
