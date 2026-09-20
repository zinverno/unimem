"""The startup gate: what it proves, and what a failure means.

Two things are proved before a media-enabled deployment is allowed to start: the
configured ``ffprobe`` runs and reports a version, and the build that answered
can open the ``fd`` input protocol the adapter feeds its bytes through.

The second is the one worth being deliberate about. FFmpeg builds are
configurable; a stripped-down one that lacks ``fd`` would accept this adapter's
command line and fail on the *first capture*, giving a server that starts,
accepts audio at the door, and then answers 503 to every one of them. Proving it
at startup turns that into a refusal with a sentence.

Every refusal here is a :class:`~unimem_media.errors.MediaPrerequisiteError`,
which is not a :class:`~core.processing.media_probe.MediaProbeExecutionError`:
one says "do not start", the other says "one capture could not be probed", and
conflating them would make a misconfigured deployment look transient.
"""

import subprocess
from typing import Any, Final

import pytest

from core.processing.media_probe import MediaProbeExecutionError
from unimem_media import build_ffprobe_media_probe
from unimem_media.errors import MediaPrerequisiteError
from unimem_media.ffprobe import FfprobeMediaProbe
from unimem_media.policy import PREREQUISITE_TIMEOUT_SECONDS
from unimem_media.prerequisites import (
    describe_prerequisites,
    engine_version,
    input_protocols,
    require_engine,
)

from .fakes import PROBE_RUN

#: A real `ffprobe -version` banner.
VERSION_BANNER: Final = (
    b"ffprobe version 6.1.1-3ubuntu5 Copyright (c) 2007-2023 the FFmpeg developers\n"
    b"built with gcc 13 (Ubuntu 13.2.0-23ubuntu3)\n"
)

#: A real `ffprobe -protocols` listing, abridged but structurally exact.
PROTOCOLS: Final = (
    b"Supported file protocols:\n"
    b"Input:\n"
    b"  async\n"
    b"  cache\n"
    b"  concat\n"
    b"  fd\n"
    b"  file\n"
    b"  http\n"
    b"Output:\n"
    b"  crypto\n"
    b"  fd\n"
    b"  file\n"
)

#: The same listing from a build compiled without the `fd` protocol.
NO_FD: Final = PROTOCOLS.replace(b"  fd\n", b"", 1)


class Completed:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def answering(**by_argument: bytes) -> Any:
    """A `subprocess.run` double answering each fixed probe argument."""

    def run(arguments: list[str], **kwargs: Any) -> Completed:
        return Completed(stdout=by_argument[arguments[1]])

    return run


def working() -> Any:
    return answering(**{"-version": VERSION_BANNER, "-protocols": PROTOCOLS})


class TestTheVersionProbe:
    def test_it_reads_the_version_from_the_banner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        assert engine_version() == "6.1.1-3ubuntu5"

    def test_it_uses_a_two_element_fixed_argument_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Completed:
            seen["arguments"] = arguments
            seen["kwargs"] = kwargs
            return Completed(stdout=VERSION_BANNER)

        monkeypatch.setattr(PROBE_RUN, record)
        engine_version()

        assert seen["arguments"] == ["ffprobe", "-version"]
        assert seen["kwargs"]["shell"] is False

    def test_the_startup_probe_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Completed:
            seen["timeout"] = kwargs["timeout"]
            return Completed(stdout=VERSION_BANNER)

        monkeypatch.setattr(PROBE_RUN, record)
        engine_version()

        assert seen["timeout"] == PREREQUISITE_TIMEOUT_SECONDS
        assert seen["timeout"] > 0

    def test_a_missing_executable_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def missing(arguments: list[str], **kwargs: Any) -> Completed:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(PROBE_RUN, missing)

        with pytest.raises(MediaPrerequisiteError, match="could not be run"):
            engine_version()

    def test_the_message_says_nothing_is_installed_for_you(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing(arguments: list[str], **kwargs: Any) -> Completed:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(PROBE_RUN, missing)

        with pytest.raises(MediaPrerequisiteError, match="does not install it"):
            engine_version()

    def test_a_timeout_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def slow(arguments: list[str], **kwargs: Any) -> Completed:
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30.0)

        monkeypatch.setattr(PROBE_RUN, slow)

        with pytest.raises(MediaPrerequisiteError, match="did not answer"):
            engine_version()

    def test_a_nonzero_exit_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PROBE_RUN, lambda *a, **k: Completed(returncode=1))

        with pytest.raises(MediaPrerequisiteError, match="status 1"):
            engine_version()

    @pytest.mark.parametrize("banner", [b"", b"\n", b"something else entirely\n"])
    def test_an_unrecognizable_banner_refuses(
        self, monkeypatch: pytest.MonkeyPatch, banner: bytes
    ) -> None:
        monkeypatch.setattr(PROBE_RUN, lambda *a, **k: Completed(stdout=banner))

        with pytest.raises(MediaPrerequisiteError, match="did not report a version"):
            engine_version()

    def test_a_non_utf8_banner_does_not_break_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A diagnostic banner is decoded permissively; capture output is not."""
        monkeypatch.setattr(
            PROBE_RUN,
            lambda *a, **k: Completed(stdout=b"ffprobe version 6.1\xff Copyright\n"),
        )

        assert engine_version().startswith("6.1")


class TestTheProtocolProbe:
    def test_it_reads_the_input_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        assert input_protocols() == frozenset({"async", "cache", "concat", "fd", "file", "http"})

    def test_it_stops_at_the_output_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A build that can only *write* `fd` cannot serve a capture."""
        monkeypatch.setattr(
            PROBE_RUN,
            answering(**{"-version": VERSION_BANNER, "-protocols": NO_FD}),
        )

        assert "fd" not in input_protocols()

    def test_it_uses_a_two_element_fixed_argument_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Completed:
            seen["arguments"] = arguments
            seen["kwargs"] = kwargs
            return Completed(stdout=PROTOCOLS)

        monkeypatch.setattr(PROBE_RUN, record)
        input_protocols()

        assert seen["arguments"] == ["ffprobe", "-protocols"]
        assert seen["kwargs"]["shell"] is False


class TestTheWholeGate:
    def test_it_returns_the_version_when_everything_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        assert require_engine() == "6.1.1-3ubuntu5"

    def test_a_build_without_the_protocol_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            PROBE_RUN,
            answering(**{"-version": VERSION_BANNER, "-protocols": NO_FD}),
        )

        with pytest.raises(MediaPrerequisiteError, match="'fd' input protocol"):
            require_engine()

    def test_that_refusal_explains_there_is_no_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quietly passing a path instead would break this adapter's one promise."""
        monkeypatch.setattr(
            PROBE_RUN,
            answering(**{"-version": VERSION_BANNER, "-protocols": NO_FD}),
        )

        with pytest.raises(MediaPrerequisiteError, match="filesystem path"):
            require_engine()

    def test_it_installs_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The only commands it may run are the two fixed probes."""
        commands: list[list[str]] = []

        def record(arguments: list[str], **kwargs: Any) -> Completed:
            commands.append(arguments)
            return Completed(stdout=VERSION_BANNER if arguments[1] == "-version" else PROTOCOLS)

        monkeypatch.setattr(PROBE_RUN, record)
        require_engine()

        assert commands == [["ffprobe", "-version"], ["ffprobe", "-protocols"]]


class TestTheFactory:
    def test_it_returns_a_probe_when_the_gate_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        assert isinstance(build_ffprobe_media_probe(), FfprobeMediaProbe)

    def test_it_refuses_rather_than_returning_a_broken_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing(arguments: list[str], **kwargs: Any) -> Completed:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(PROBE_RUN, missing)

        with pytest.raises(MediaPrerequisiteError):
            build_ffprobe_media_probe()

    def test_the_configured_executable_reaches_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        probe = build_ffprobe_media_probe(executable="/opt/bin/ffprobe")

        assert isinstance(probe, FfprobeMediaProbe)
        assert probe.executable == "/opt/bin/ffprobe"

    def test_the_engine_version_is_not_carried_by_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-021: the probing engine is not canonical metadata."""
        monkeypatch.setattr(PROBE_RUN, working())

        probe = build_ffprobe_media_probe()

        assert "6.1.1-3ubuntu5" not in repr(vars(probe))


class TestThePrerequisiteErrorIsItsOwnThing:
    def test_it_is_not_an_execution_failure(self) -> None:
        assert not issubclass(MediaPrerequisiteError, MediaProbeExecutionError)

    def test_core_does_not_define_it(self) -> None:
        import core.processing.media_probe as port

        assert not hasattr(port, "MediaPrerequisiteError")


class TestTheOperatorReport:
    def test_it_names_the_engine_and_the_protocol(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PROBE_RUN, working())

        report = describe_prerequisites()

        assert "6.1.1-3ubuntu5" in report
        assert "fd" in report
        assert "True" in report

    def test_it_reports_a_missing_protocol_honestly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            PROBE_RUN,
            answering(**{"-version": VERSION_BANNER, "-protocols": NO_FD}),
        )

        assert "False" in describe_prerequisites()
