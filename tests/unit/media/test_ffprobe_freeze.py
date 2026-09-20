"""The command line, frozen. Nothing here is a style preference.

The argv this adapter builds is the whole of its attack surface, so it is
asserted element by element rather than by a substring match: a test that only
checked "``-protocol_whitelist`` appears somewhere" would pass for a command
line that also opened an HTTP URL.

Four properties are the reason this file exists:

* **no request-controlled string can reach the child**, because every element
  after the executable is a module constant and the input is an inherited
  descriptor rather than a name;
* **the protocol whitelist is present and is exactly ``fd``**, so arbitrary
  submitted bytes cannot make ffprobe open a path or a URL;
* **the structural query asks for the smallest thing that answers the port**,
  so tags, chapters, packets and frames cannot arrive by accident;
* **``shell=False``**, so there is no shell string to quote or interpret.
"""

import inspect
import subprocess
from typing import Any, Final

import pytest

from unimem_media import engine, policy
from unimem_media.engine import FfprobeInvocation, build_arguments
from unimem_media.ffprobe import FfprobeMediaProbe
from unimem_media.policy import DEFAULT_LIMITS, MediaProbeLimits

from .fakes import PAYLOAD, RUN, RecordingStream, write_stdout

#: The exact vector, written out rather than computed from the constants under
#: test. Deriving it from `policy` would make this file agree with any change to
#: them, which is the opposite of freezing.
EXPECTED: Final[list[str]] = [
    "ffprobe",
    "-hide_banner",
    "-loglevel",
    "error",
    "-protocol_whitelist",
    "fd",
    "-print_format",
    "json",
    "-show_entries",
    (
        "format=format_name,duration:"
        "stream=index,codec_type,codec_name,sample_rate,channels,width,height,avg_frame_rate"
    ),
    "-i",
    "fd:",
]

MINIMAL: Final = b'{"format": {"format_name": "wav"}, "streams": []}'


class TestTheCommandLineIsFrozen:
    def test_it_is_exactly_this(self) -> None:
        assert build_arguments(FfprobeInvocation()) == EXPECTED

    def test_only_the_executable_varies(self) -> None:
        built = build_arguments(FfprobeInvocation(executable="/opt/ffmpeg/bin/ffprobe"))

        assert built == ["/opt/ffmpeg/bin/ffprobe", *EXPECTED[1:]]

    def test_the_limits_do_not_reach_the_argv(self) -> None:
        """A timeout is enforced by this process, never asked of the child."""
        tightened = MediaProbeLimits(timeout_seconds=1.0, max_output_bytes=17)

        assert build_arguments(FfprobeInvocation(limits=tightened)) == EXPECTED

    def test_no_element_names_a_path(self) -> None:
        assert not [element for element in EXPECTED[1:] if "/" in element]

    def test_the_input_is_an_inherited_descriptor(self) -> None:
        assert EXPECTED[-2:] == ["-i", "fd:"]

    def test_it_is_a_list_and_never_a_string(self) -> None:
        """A string would be a shell command, which is what is being avoided."""
        assert isinstance(build_arguments(FfprobeInvocation()), list)
        assert all(isinstance(element, str) for element in EXPECTED)


class TestTheProtocolIsRestricted:
    def test_the_whitelist_is_present(self) -> None:
        assert "-protocol_whitelist" in EXPECTED

    def test_it_permits_exactly_one_protocol(self) -> None:
        value = EXPECTED[EXPECTED.index("-protocol_whitelist") + 1]

        assert value == "fd"
        assert "," not in value

    def test_the_whitelist_is_the_protocol_the_input_uses(self) -> None:
        assert f"{policy.PROTOCOL_WHITELIST}:" == policy.INPUT_URL

    @pytest.mark.parametrize("protocol", ["file", "http", "https", "concat", "data", "tcp"])
    def test_no_other_protocol_is_permitted(self, protocol: str) -> None:
        assert protocol not in policy.PROTOCOL_WHITELIST.split(",")


class TestTheStructuralQueryIsMinimal:
    def test_the_format_entries_are_exactly_two(self) -> None:
        assert policy.FORMAT_ENTRIES == ("format_name", "duration")

    def test_the_stream_entries_are_exactly_the_port_needs(self) -> None:
        assert policy.STREAM_ENTRIES == (
            "index",
            "codec_type",
            "codec_name",
            "sample_rate",
            "channels",
            "width",
            "height",
            "avg_frame_rate",
        )

    @pytest.mark.parametrize(
        "forbidden",
        [
            "tags",
            "chapters",
            "packets",
            "frames",
            "bit_rate",
            "language",
            "disposition",
            "title",
            "nb_frames",
            "profile",
        ],
    )
    def test_nothing_beyond_the_port_is_requested(self, forbidden: str) -> None:
        assert forbidden not in policy.SHOW_ENTRIES

    def test_the_frame_rate_asked_for_is_the_average(self) -> None:
        """`r_frame_rate` is ffprobe's own guess; `avg_frame_rate` is declared."""
        assert "avg_frame_rate" in policy.STREAM_ENTRIES
        assert "r_frame_rate" not in policy.SHOW_ENTRIES

    def test_no_flag_asks_for_more_sections(self) -> None:
        for flag in ("-show_packets", "-show_frames", "-show_chapters", "-show_programs"):
            assert flag not in EXPECTED


class TestTheProcessIsLaunchedSafely:
    def _run(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Any:
            seen["arguments"] = arguments
            seen["kwargs"] = kwargs
            return write_stdout(MINIMAL)(arguments, **kwargs)

        monkeypatch.setattr(RUN, record)
        FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]
        return seen

    def test_shell_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch)["kwargs"]["shell"] is False

    def test_the_frozen_argv_is_what_actually_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch)["arguments"] == EXPECTED

    def test_the_timeout_is_the_policy_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch)["kwargs"]["timeout"] == DEFAULT_LIMITS.timeout_seconds

    def test_the_default_timeout_is_thirty_seconds(self) -> None:
        assert DEFAULT_LIMITS.timeout_seconds == 30.0

    def test_a_tightened_timeout_reaches_the_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Any:
            seen["kwargs"] = kwargs
            return write_stdout(MINIMAL)(arguments, **kwargs)

        monkeypatch.setattr(RUN, record)
        probe = FfprobeMediaProbe(limits=MediaProbeLimits(timeout_seconds=2.5))
        probe.probe(RecordingStream())  # type: ignore[arg-type]

        assert seen["kwargs"]["timeout"] == 2.5

    def test_stderr_is_discarded_rather_than_captured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never read, so ffprobe's prose can never become a verdict or a body."""
        assert self._run(monkeypatch)["kwargs"]["stderr"] is subprocess.DEVNULL

    def test_a_nonzero_exit_is_not_raised_by_subprocess_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`check=False`, so the runner classifies the failure rather than `run`."""
        assert self._run(monkeypatch)["kwargs"]["check"] is False

    def test_stdin_and_stdout_are_real_files_not_pipes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seekable temporary files, so a demuxer can rewind and nothing deadlocks.

        Both descriptors are read *during* the call, because they are closed the
        moment it returns — which is itself the property
        ``test_engine_runner.py`` pins down.
        """
        seen: dict[str, Any] = {}

        def record(arguments: list[str], **kwargs: Any) -> Any:
            seen["stdin_fd"] = kwargs["stdin"].fileno()
            seen["stdout_fd"] = kwargs["stdout"].fileno()
            seen["stdin_seekable"] = kwargs["stdin"].seekable()
            return write_stdout(MINIMAL)(arguments, **kwargs)

        monkeypatch.setattr(RUN, record)
        FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]

        assert seen["stdin_fd"] >= 0
        assert seen["stdout_fd"] >= 0
        assert seen["stdin_seekable"] is True


class TestNothingInTheAdapterBuildsAShellString:
    def test_no_module_imports_os_system_or_popen(self) -> None:
        for module in (engine, policy):
            source = inspect.getsource(module)
            assert "os.system" not in source
            assert "shell=True" not in source

    def test_the_runner_never_uses_a_shell(self) -> None:
        assert "shell=False" in inspect.getsource(engine.run_ffprobe)


class TestTheOutputBudgetIsFrozen:
    def test_it_is_one_mebibyte(self) -> None:
        assert DEFAULT_LIMITS.max_output_bytes == 1024 * 1024

    def test_the_probe_reports_the_limits_it_will_run_under(self) -> None:
        """Read-only, for diagnostics: nothing consults it to make a decision."""
        tightened = MediaProbeLimits(timeout_seconds=1.0)
        probe = FfprobeMediaProbe(limits=tightened)

        assert probe.limits is tightened
        assert probe.limits.timeout_seconds == 1.0

    def test_a_default_probe_reports_the_default_limits(self) -> None:
        assert FfprobeMediaProbe().limits is DEFAULT_LIMITS

    def test_the_copy_chunk_is_bounded_and_positive(self) -> None:
        assert 0 < DEFAULT_LIMITS.copy_chunk_size <= DEFAULT_LIMITS.max_output_bytes

    def test_the_payload_is_bigger_than_one_chunk_in_these_tests(self) -> None:
        """Otherwise the chunked-copy assertions elsewhere would prove nothing."""
        assert len(PAYLOAD) > 1
