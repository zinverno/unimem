"""Composition: what ``--media`` switches on, and who is allowed to decide.

Phase 5A-2 proved the *wiring*: that ``media_probe`` reaches both intake and the
router, and that the two halves cannot drift apart. This file is about the layer
above it — the command line that builds a real probe and hands it in — and about
the four claims that make the capability genuinely opt-in:

* **Without the flag nothing changes and nothing is loaded.** No ffprobe is
  probed or executed, :mod:`unimem_media` is not imported, audio and video are
  refused at the door, and that is proved in a *real interpreter with the flag
  absent*, because "it does not import it" is a claim about imports.
* **With the flag a real MediaProbe is built and passed**, and the prerequisites
  are validated before anything is created or bound.
* **A missing ffprobe refuses startup** rather than disabling media and serving
  a build that answers 503 to everything it just agreed to accept.
* **The three capabilities are independent.** Every combination is a deployment.

Nothing here runs a real ffprobe: the gate is the thing under test, so it is
driven from both sides with a double. The native suites do the rest.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI

import unimem_api.__main__ as cli
import unimem_media
from core.processing import AudioProcessor, VideoProcessor
from core.processing.media_probe import MediaProbe
from tests.unit.processing.doubles import FakeMediaProbe, audio_stream, probe_result
from unimem_api.wiring import _media_processors
from unimem_media import MediaPrerequisiteError

#: A subprocess that asks an interpreter what a start actually loaded and did.
#:
#: Two questions at once, and both need a real process: whether `unimem_media`
#: ended up in `sys.modules`, and whether anything ran a child. An AST scan
#: could answer neither.
#:
#: **It answers the two prerequisite probes itself and runs no engine.** These
#: are composition tests and must pass on an ordinary installation with no
#: FFmpeg anywhere — proving that real ffprobe works is the dedicated
#: `Local media probing` job's job, not this file's. So the child's
#: `subprocess.run` intercepts exactly `["ffprobe", "-version"]` and
#: `["ffprobe", "-protocols"]`, returns realistic successful results for them,
#: and delegates anything else to the real function.
#:
#: The match is **exact**, and `delegated` records every command that did not
#: match. If the adapter's probe argv ever changes, the stub stops answering,
#: the command falls through to the host, and
#: `test_the_stub_answered_every_probe` fails loudly — which is what stops this
#: file quietly depending on a real engine again.
OBSERVE_START: Final = """
import json
import subprocess
import sys

VERSION_BANNER = (
    "ffprobe version 6.1.1-stub Copyright (c) 2007-2026 the FFmpeg developers\\n"
    "built with gcc 13 (Ubuntu 13.2.0-23ubuntu3)\\n"
    "configuration: --enable-gpl\\n"
)
PROTOCOLS = (
    "Supported file protocols:\\n"
    "Input:\\n"
    "  cache\\n"
    "  concat\\n"
    "  data\\n"
    "  fd\\n"
    "  file\\n"
    "  http\\n"
    "  https\\n"
    "Output:\\n"
    "  crypto\\n"
    "  fd\\n"
    "  file\\n"
)
ANSWERED = {
    ("ffprobe", "-version"): VERSION_BANNER,
    ("ffprobe", "-protocols"): PROTOCOLS,
}

launched = []
delegated = []
real_run = subprocess.run


def fake_run(*args, **kwargs):
    command = args[0] if args else kwargs.get("args")
    launched.append(command)
    key = tuple(command) if isinstance(command, (list, tuple)) else None
    if key in ANSWERED:
        return subprocess.CompletedProcess(
            args=list(key),
            returncode=0,
            stdout=ANSWERED[key].encode("utf-8"),
            stderr=b"",
        )
    delegated.append(command)
    return real_run(*args, **kwargs)


subprocess.run = fake_run

import unimem_api.__main__ as cli

cli.main(sys.argv[1:], server=lambda app, *, host, port: None)


def rendered(commands):
    return [
        list(map(str, command)) if isinstance(command, (list, tuple)) else str(command)
        for command in commands
    ]


print(json.dumps({
    "media_imported": any(name.startswith("unimem_media") for name in sys.modules),
    "ocr_imported": any(name.startswith("unimem_ocr") for name in sys.modules),
    "launched": rendered(launched),
    "delegated": rendered(delegated),
}))
"""


class RecordingServer:
    """Stands in for uvicorn: remembers the call and returns."""

    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, str, int]] = []

    def __call__(self, app: FastAPI, *, host: str, port: int) -> None:
        self.calls.append((app, host, port))


def working_probe() -> FakeMediaProbe:
    return FakeMediaProbe(
        result=probe_result(container_names=("mp3",), audio_streams=(audio_stream(index=0),))
    )


def observe(tmp_path: Path, *flags: str) -> dict[str, Any]:
    """Start the CLI in a fresh interpreter and report what it loaded and ran."""
    completed = subprocess.run(
        [sys.executable, "-c", OBSERVE_START, "--data-dir", str(tmp_path), *flags],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    parsed: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return parsed


class TestTheFlag:
    def test_it_defaults_to_off(self, tmp_path: Path) -> None:
        assert cli.parse_args(["--data-dir", str(tmp_path)]).media is False

    def test_it_turns_it_on(self, tmp_path: Path) -> None:
        assert cli.parse_args(["--data-dir", str(tmp_path), "--media"]).media is True

    def test_it_takes_no_value(self, tmp_path: Path) -> None:
        """Deployment configuration, not a tunable: there is nothing to pass."""
        with pytest.raises(SystemExit):
            cli.parse_args(["--data-dir", str(tmp_path), "--media", "mp4"])

    def test_it_is_independent_of_the_ocr_flags(self, tmp_path: Path) -> None:
        options = cli.parse_args(["--data-dir", str(tmp_path), "--media"])

        assert options.pdf_ocr is False
        assert options.image_ocr is False

    def test_neither_ocr_flag_turns_it_on(self, tmp_path: Path) -> None:
        options = cli.parse_args(["--data-dir", str(tmp_path), "--pdf-ocr", "--image-ocr"])

        assert options.media is False

    def test_all_three_can_be_given_together(self, tmp_path: Path) -> None:
        options = cli.parse_args(
            ["--data-dir", str(tmp_path), "--media", "--pdf-ocr", "--image-ocr"]
        )

        assert (options.media, options.pdf_ocr, options.image_ocr) == (True, True, True)

    def test_there_is_no_flag_for_the_engine_the_timeout_or_the_budget(
        self, tmp_path: Path
    ) -> None:
        for rejected in ("--ffprobe", "--media-timeout", "--media-engine", "--containers"):
            with pytest.raises(SystemExit):
                cli.parse_args(["--data-dir", str(tmp_path), rejected, "x"])

    def test_the_help_names_the_prerequisite_and_the_independence(self) -> None:
        help_text = " ".join(cli.build_parser().format_help().split())

        assert "ffprobe" in help_text
        assert "'fd' input protocol" in help_text
        assert "Independent of --pdf-ocr and --image-ocr" in help_text

    def test_the_help_says_what_the_capability_is_not(self) -> None:
        help_text = " ".join(cli.build_parser().format_help().split())

        assert "no transcription" in help_text
        assert "no thumbnails" in help_text
        assert "no ffmpeg" in help_text


class TestMainWiresTheProbeThrough:
    def test_the_flag_reaches_the_composition_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}
        probe = working_probe()

        def record(data_dir: Path, **kwargs: Any) -> FastAPI:
            seen.update(kwargs)
            return FastAPI()

        monkeypatch.setattr(cli, "build_media_probe", lambda: probe)
        monkeypatch.setattr(cli, "build_local_app", record)
        cli.main(["--data-dir", str(tmp_path), "--media"], server=RecordingServer())

        assert seen["media_probe"] is probe

    def test_without_the_flag_nothing_builds_a_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def never() -> MediaProbe:  # pragma: no cover - must not be reached
            raise AssertionError("no probe may be built without --media")

        monkeypatch.setattr(cli, "build_media_probe", never)
        cli.main(["--data-dir", str(tmp_path)], server=RecordingServer())

    def test_without_the_flag_the_composition_root_gets_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def record(data_dir: Path, **kwargs: Any) -> FastAPI:
            seen.update(kwargs)
            return FastAPI()

        monkeypatch.setattr(cli, "build_local_app", record)
        cli.main(["--data-dir", str(tmp_path)], server=RecordingServer())

        assert seen["media_probe"] is None

    def test_each_flag_builds_only_its_own_capability(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[str] = []

        monkeypatch.setattr(cli, "build_media_probe", lambda: built.append("media"))
        monkeypatch.setattr(cli, "build_pdf_ocr", lambda: built.append("pdf"))
        monkeypatch.setattr(cli, "build_image_ocr", lambda: built.append("image"))
        monkeypatch.setattr(cli, "build_local_app", lambda *a, **k: FastAPI())
        cli.main(["--data-dir", str(tmp_path), "--media"], server=RecordingServer())

        assert built == ["media"]

    def test_all_three_gates_run_when_all_three_flags_are_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[str] = []

        monkeypatch.setattr(cli, "build_media_probe", lambda: built.append("media"))
        monkeypatch.setattr(cli, "build_pdf_ocr", lambda: built.append("pdf"))
        monkeypatch.setattr(cli, "build_image_ocr", lambda: built.append("image"))
        monkeypatch.setattr(cli, "build_local_app", lambda *a, **k: FastAPI())
        cli.main(
            ["--data-dir", str(tmp_path), "--media", "--pdf-ocr", "--image-ocr"],
            server=RecordingServer(),
        )

        assert sorted(built) == ["image", "media", "pdf"]

    def test_the_probe_it_builds_is_the_real_adapter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unimem_media, "require_engine", lambda *a, **k: "6.1.1")

        probe = cli.build_media_probe()

        assert isinstance(probe, unimem_media.FfprobeMediaProbe)

    def test_that_probe_is_what_the_media_processors_get(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One instance, shared by both, exactly as the adapter is built for."""
        from core.storage import LocalRawObjectStore

        monkeypatch.setattr(unimem_media, "require_engine", lambda *a, **k: "6.1.1")
        probe = cli.build_media_probe()

        processors = _media_processors(LocalRawObjectStore(tmp_path / "raw"), probe)

        assert len(processors) == 2
        assert isinstance(processors[0], AudioProcessor)
        assert isinstance(processors[1], VideoProcessor)


class TestStartupRefusesWhenThePrerequisiteIsMissing:
    def _missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*args: Any, **kwargs: Any) -> str:
            raise MediaPrerequisiteError("the media probe 'ffprobe' could not be run")

        monkeypatch.setattr(unimem_media, "require_engine", refuse)

    def test_a_missing_engine_exits_rather_than_serving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._missing(monkeypatch)
        server = RecordingServer()

        with pytest.raises(SystemExit) as caught:
            cli.main(["--data-dir", str(tmp_path / "data"), "--media"], server=server)

        assert "--media was requested" in str(caught.value)
        assert server.calls == []

    def test_the_message_carries_the_prerequisite_explanation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._missing(monkeypatch)

        with pytest.raises(SystemExit) as caught:
            cli.main(["--data-dir", str(tmp_path / "data"), "--media"], server=RecordingServer())

        assert "could not be run" in str(caught.value)

    def test_it_fails_before_the_data_directory_is_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The existing startup ordering: gates first, workspace second."""
        self._missing(monkeypatch)
        data_dir = tmp_path / "never-created"

        with pytest.raises(SystemExit):
            cli.main(["--data-dir", str(data_dir), "--media"])

        assert not data_dir.exists()

    def test_it_never_silently_disables_media_and_starts_anyway(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Serving with media off would 503 every capture it just accepted."""
        self._missing(monkeypatch)
        built: list[Any] = []

        def record(*args: Any, **kwargs: Any) -> FastAPI:
            built.append(kwargs)
            return FastAPI()

        monkeypatch.setattr(cli, "build_local_app", record)

        with pytest.raises(SystemExit):
            cli.main(["--data-dir", str(tmp_path / "data"), "--media"], server=RecordingServer())

        assert built == []

    def test_a_missing_ffprobe_does_not_affect_a_default_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._missing(monkeypatch)
        server = RecordingServer()

        assert cli.main(["--data-dir", str(tmp_path)], server=server) == 0
        assert len(server.calls) == 1


class TestADefaultStartLoadsAndRunsNothing:
    """What a start loads and runs, observed in a fresh interpreter.

    The negatives are the point, and they are only evidence because the
    positives below prove the same observation can come back true. Neither half
    needs an engine on this machine: the child answers the two prerequisite
    probes itself, so the whole class passes on an ordinary installation with no
    FFmpeg — which is the state the `Quality gates` and `Local PDF OCR` jobs run
    in, and the state this file must never stop supporting.
    """

    def test_it_does_not_import_the_media_adapter(self, tmp_path: Path) -> None:
        assert observe(tmp_path)["media_imported"] is False

    def test_it_does_not_import_either_ocr_adapter_either(self, tmp_path: Path) -> None:
        assert observe(tmp_path)["ocr_imported"] is False

    def test_it_runs_no_subprocess_at_all(self, tmp_path: Path) -> None:
        """No ffprobe is probed, so a machine without FFmpeg starts normally."""
        assert observe(tmp_path)["launched"] == []

    def test_it_delegates_nothing_to_the_host_either(self, tmp_path: Path) -> None:
        """Nothing ran at all, so nothing could have fallen through to a real one."""
        assert observe(tmp_path)["delegated"] == []

    def test_a_media_start_does_import_the_adapter(self, tmp_path: Path) -> None:
        """The negative above is only evidence if the positive is reachable."""
        observed = observe(tmp_path, "--media")

        assert observed["media_imported"] is True

    def test_a_media_start_probes_the_engine_before_serving(self, tmp_path: Path) -> None:
        launched = observe(tmp_path, "--media")["launched"]

        assert ["ffprobe", "-version"] in launched
        assert ["ffprobe", "-protocols"] in launched

    def test_it_probes_each_prerequisite_exactly_once(self, tmp_path: Path) -> None:
        launched = observe(tmp_path, "--media")["launched"]

        assert launched.count(["ffprobe", "-version"]) == 1
        assert launched.count(["ffprobe", "-protocols"]) == 1

    def test_the_stub_answered_every_probe(self, tmp_path: Path) -> None:
        """The guard against this file quietly depending on a real engine again.

        `delegated` holds every command the stub did **not** recognize and
        therefore handed to the host's real `subprocess.run`. It must be empty:
        if the adapter's probe argv ever changes, the exact match stops matching,
        the command escapes to the machine, and this fails — loudly, here, rather
        than silently passing on a developer's laptop and failing in a job with
        no FFmpeg installed.
        """
        assert observe(tmp_path, "--media")["delegated"] == []

    def test_a_media_start_launches_nothing_but_those_two_probes(self, tmp_path: Path) -> None:
        """Startup probes the engine; it does not probe a file or run ffmpeg."""
        launched = observe(tmp_path, "--media")["launched"]

        assert launched == [["ffprobe", "-version"], ["ffprobe", "-protocols"]]

    def test_a_media_start_imports_no_ocr_adapter(self, tmp_path: Path) -> None:
        assert observe(tmp_path, "--media")["ocr_imported"] is False


class TestNoCorePolicyMovedIntoTheCli:
    def test_the_cli_names_no_container_or_mime_type(self) -> None:
        import inspect

        source = inspect.getsource(cli)
        body = source.split('"""', 2)[-1]

        for owned_by_core in ("audio/mpeg", "video/mp4", "matroska", "AUDIO", "VIDEO"):
            assert owned_by_core not in body

    def test_the_cli_decides_nothing_about_streams(self) -> None:
        import inspect

        source = inspect.getsource(cli)

        for owned_by_core in ("audio_streams", "video_streams", "container_names"):
            assert owned_by_core not in source

    def test_the_cli_does_not_know_what_a_probe_result_is(self) -> None:
        import inspect

        assert "MediaProbeResult" not in inspect.getsource(cli)

    def test_intake_acceptance_is_still_derived_in_the_composition_root(self) -> None:
        import inspect

        from unimem_api import wiring

        assert "media_enabled=media_probe is not None" in inspect.getsource(wiring)
