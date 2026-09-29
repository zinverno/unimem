import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.contracts import ContentObject
from core.rendering import MarkdownRenderer
from tests.unit.youtube.test_pipeline import fixture_acquire
from tests.youtube_fixtures import URL, artifact
from unimem_youtube.__main__ import main
from unimem_youtube.errors import ExportError
from unimem_youtube.export import CaptionMarkdownRenderer, export_markdown
from unimem_youtube.service import YoutubeCaptureService


def saved(tmp_path: Path) -> ContentObject:
    return YoutubeCaptureService(tmp_path / "data").capture(
        URL,
        ("ru", "en"),
        acquire=fixture_acquire,
    )


def test_frontmatter_text_time_links_and_projection_are_safe(tmp_path: Path) -> None:
    material = artifact()
    material.track.language = 'Русский "quoted"\n---\ninjected: true\n☃'
    content = YoutubeCaptureService(tmp_path / "data").capture(
        URL,
        ("ru",),
        acquire=lambda *_: material,
    )
    snapshot = content.model_dump_json()
    renderer = CaptionMarkdownRenderer()
    markdown = renderer.render(content)
    frontmatter, body = markdown.removeprefix("---\n").split("\n---\n", 1)
    # Each field is one static key plus a JSON scalar: valid YAML without
    # implicit dates/booleans or multiline source data escaping the document.
    fields = {
        line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
        for line in frontmatter.splitlines()
    }
    assert fields["language"] == material.track.language
    assert "injected" not in fields
    assert fields["captured_at"] == material.captured_at.isoformat()
    assert fields["capture_id"] == content.source.capture_id
    assert fields["content_id"] == content.id
    assert fields["track_origin"] == "youtube_manual"
    assert fields["unimem_audio_analysis"] is False
    assert fields["unimem_visual_analysis"] is False
    assert f"[1.25s - 3.75s]({URL}&t=1s)" in body
    assert f"[4.0s]({URL}&t=4s)" in body
    assert "````text\n  repeat\n``` yaml\nx: yes\n```  \n````" in body
    assert body.count("  repeat") == 2
    assert "Привет & мир — café 😺" in body
    assert "did not analyse" in body
    assert content.model_dump_json() == snapshot
    assert renderer.render(content) == markdown
    # The existing renderer remains its same title/text-only projection.
    assert MarkdownRenderer().version == "0.1"
    assert MarkdownRenderer().render(content) == "\n\n".join(
        segment.text for segment in content.segments if segment.text is not None
    )


def test_export_conflict_and_failure_do_not_destroy_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = saved(tmp_path)
    path = export_markdown(content, tmp_path / "out")
    original = path.read_bytes()
    with pytest.raises(ExportError, match="already exists"):
        export_markdown(content, tmp_path / "out")
    assert path.read_bytes() == original
    assert list((tmp_path / "out").glob(".unimem-*")) == []

    def deny_link(source: Path, destination: Path) -> None:
        raise OSError("filesystem unavailable")

    monkeypatch.setattr("unimem_youtube.export.os.link", deny_link)
    with pytest.raises(ExportError, match="Could not write"):
        export_markdown(content, tmp_path / "other")
    assert list((tmp_path / "other").iterdir()) == []
    assert YoutubeCaptureService(tmp_path / "data").read(content.source.capture_id) == content


def test_offline_render_in_new_process_without_retrieval_dependencies(tmp_path: Path) -> None:
    content = saved(tmp_path)
    script = """
import importlib.abc
import socket
import sys
class NoRetrieval(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'youtube_transcript_api', 'requests', 'defusedxml'}:
            raise ModuleNotFoundError(fullname)
def no_network(*args, **kwargs):
    raise AssertionError('offline render tried to use the network')
sys.meta_path.insert(0, NoRetrieval())
socket.socket.connect = no_network
from unimem_youtube.__main__ import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            "render",
            content.source.capture_id,
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "offline"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["content_id"] == content.id
    assert output["capture_status"] == "complete"
    assert Path(output["path"]).read_text() == CaptionMarkdownRenderer().render(content)


def test_cli_read_and_export_failures_have_distinct_results(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content = saved(tmp_path)
    args = [
        "render",
        content.source.capture_id,
        "--data-dir",
        str(tmp_path / "data"),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["export_status"] == "complete"
    assert main(args) == 4
    failure = json.loads(capsys.readouterr().err)
    assert failure["capture_status"] == "complete"
    assert failure["export_status"] == "failed"
    assert failure["capture_id"] == content.source.capture_id
    args[1] = "missing"
    assert main(args) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "capture_or_read_failed"


def test_cli_invalid_url_does_not_claim_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "capture",
                "https://evil.test",
                "--data-dir",
                str(tmp_path / "data"),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().err)
    assert result["code"] == "invalid_url"
    assert "capture_id" not in result
    assert not (tmp_path / "out").exists()


def test_untimed_cue_and_generated_track(tmp_path: Path) -> None:
    material = artifact("<transcript><text>time not provided</text></transcript>")
    material.track.is_generated = True
    content = YoutubeCaptureService(tmp_path / "data").capture(
        URL,
        ("ru",),
        acquire=lambda *_: material,
    )
    markdown = CaptionMarkdownRenderer().render(content)
    assert "youtube_generated" in markdown
    assert "Time unavailable" in markdown
    assert "&t=" not in markdown
