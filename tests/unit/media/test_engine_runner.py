"""Staging, bounds, and the six ways one invocation can fail.

This file is about :mod:`unimem_media.engine` — the part that owns a subprocess
and two temporary files — and deliberately not about what the JSON means.

Three things it pins down that nothing else can:

* **the submitted stream is copied in fixed-size pieces**, never read whole, so
  a gigabyte upload costs one buffer;
* **every temporary resource is closed on every path out**, success and failure
  alike, and the caller's own handle is never closed;
* **the child's output is read under a byte budget**, and output past it is a
  refusal rather than a truncation that would parse into a smaller truth.
"""

import subprocess
import tempfile
from typing import IO, Any, Final

import pytest

from unimem_media.engine import (
    INVALID_UTF8,
    LAUNCH_ERROR,
    NONZERO_EXIT,
    OUTPUT_TOO_LARGE,
    STAGING_ERROR,
    TIMEOUT,
    EngineInvocationError,
    FfprobeInvocation,
    run_ffprobe,
    stage,
)
from unimem_media.policy import MediaProbeLimits

from .fakes import (
    PAYLOAD,
    RUN,
    TEMPORARY_FILE,
    FailingStream,
    FakeCompleted,
    RecordingStream,
    write_stdout,
)

OK: Final = b'{"format": {"format_name": "wav"}}'


def invocation(**overrides: Any) -> FfprobeInvocation:
    return FfprobeInvocation(limits=MediaProbeLimits(**overrides))


class TestTheInputIsCopiedInChunks:
    def test_it_never_reads_the_stream_whole(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unbounded `read()` would hold a whole media file in this process."""
        monkeypatch.setattr(RUN, write_stdout(OK))
        stream = RecordingStream()

        run_ffprobe(stream, invocation())  # type: ignore[arg-type]

        assert stream.read_sizes
        assert None not in stream.read_sizes

    def test_every_read_asks_for_the_configured_chunk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(RUN, write_stdout(OK))
        stream = RecordingStream()

        run_ffprobe(stream, invocation(copy_chunk_size=64))  # type: ignore[arg-type]

        assert set(stream.read_sizes) == {64}

    def test_it_takes_as_many_reads_as_the_payload_needs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One read per chunk plus the final empty one that ends the loop."""
        monkeypatch.setattr(RUN, write_stdout(OK))
        stream = RecordingStream()
        chunk = 1024

        run_ffprobe(stream, invocation(copy_chunk_size=chunk))  # type: ignore[arg-type]

        expected = -(-len(PAYLOAD) // chunk) + 1
        assert len(stream.read_sizes) == expected

    def test_it_does_not_close_the_callers_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The port says the caller owns it; this runner closes what it opened."""
        monkeypatch.setattr(RUN, write_stdout(OK))
        stream = RecordingStream()

        run_ffprobe(stream, invocation())  # type: ignore[arg-type]

        assert stream.closed is False

    def test_the_staged_copy_holds_every_submitted_byte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        staged: dict[str, bytes] = {}

        def capture(arguments: list[str], **kwargs: Any) -> Any:
            source: IO[bytes] = kwargs["stdin"]
            staged["bytes"] = source.read()
            return write_stdout(OK)(arguments, **kwargs)

        monkeypatch.setattr(RUN, capture)
        run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert staged["bytes"] == PAYLOAD

    def test_the_staged_copy_is_rewound_before_the_child_reads_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        positions: dict[str, int] = {}

        def capture(arguments: list[str], **kwargs: Any) -> Any:
            positions["at_launch"] = kwargs["stdin"].tell()
            return write_stdout(OK)(arguments, **kwargs)

        monkeypatch.setattr(RUN, capture)
        run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert positions["at_launch"] == 0

    def test_stage_reports_what_it_copied(self) -> None:
        import tempfile

        stream = RecordingStream()
        with tempfile.TemporaryFile() as sink:
            assert stage(stream, sink, chunk_size=128) == len(PAYLOAD)  # type: ignore[arg-type]
            assert sink.tell() == 0

    def test_an_empty_stream_stages_cleanly(self) -> None:
        import tempfile

        stream = RecordingStream(data=b"")
        with tempfile.TemporaryFile() as sink:
            assert stage(stream, sink, chunk_size=128) == 0  # type: ignore[arg-type]


class TestTemporaryResourceLifetime:
    def _descriptors(self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        def capture(arguments: list[str], **run_kwargs: Any) -> Any:
            seen["stdin"] = run_kwargs["stdin"]
            seen["stdout"] = run_kwargs["stdout"]
            if kwargs.get("raise_") is not None:
                raise kwargs["raise_"]
            if kwargs.get("returncode"):
                return FakeCompleted(returncode=kwargs["returncode"])
            return write_stdout(kwargs.get("output", OK))(arguments, **run_kwargs)

        monkeypatch.setattr(RUN, capture)
        return seen

    def test_both_temporary_files_are_closed_after_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._descriptors(monkeypatch)

        run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert seen["stdin"].closed
        assert seen["stdout"].closed

    def test_both_are_closed_after_a_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._descriptors(monkeypatch, returncode=3)

        with pytest.raises(EngineInvocationError):
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert seen["stdin"].closed
        assert seen["stdout"].closed

    def test_both_are_closed_after_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._descriptors(
            monkeypatch, raise_=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30.0)
        )

        with pytest.raises(EngineInvocationError):
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert seen["stdin"].closed
        assert seen["stdout"].closed

    def test_both_are_closed_after_an_unexpected_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even a bug leaves no descriptor behind: the context manager is the rule."""
        seen = self._descriptors(monkeypatch, raise_=RuntimeError("a defect"))

        with pytest.raises(RuntimeError):
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert seen["stdin"].closed
        assert seen["stdout"].closed

    def test_no_temporary_file_is_ever_named_on_the_command_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(arguments: list[str], **kwargs: Any) -> Any:
            seen["arguments"] = arguments
            return write_stdout(OK)(arguments, **kwargs)

        monkeypatch.setattr(RUN, capture)
        run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert not [element for element in seen["arguments"] if "/tmp" in element]

    def test_two_probes_stage_into_different_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No shared temporary name, so two concurrent probes cannot collide."""
        descriptors: list[int] = []

        def capture(arguments: list[str], **kwargs: Any) -> Any:
            descriptors.append(kwargs["stdin"].fileno())
            return write_stdout(OK)(arguments, **kwargs)

        monkeypatch.setattr(RUN, capture)
        first = RecordingStream()
        second = RecordingStream()

        def nested(arguments: list[str], **kwargs: Any) -> Any:
            descriptors.append(kwargs["stdin"].fileno())
            if len(descriptors) == 1:
                run_ffprobe(second, invocation())  # type: ignore[arg-type]
            return write_stdout(OK)(arguments, **kwargs)

        monkeypatch.setattr(RUN, nested)
        run_ffprobe(first, invocation())  # type: ignore[arg-type]

        assert len(descriptors) == 2
        assert descriptors[0] != descriptors[1]


class TestTheOutputBudget:
    def test_output_exactly_at_the_budget_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = b'{"format": {"format_name": "wav"}}'
        padded = document + b" " * (64 - len(document))
        monkeypatch.setattr(RUN, write_stdout(padded))

        printed = run_ffprobe(
            RecordingStream(),  # type: ignore[arg-type]
            invocation(max_output_bytes=64),
        )

        assert printed == padded.decode()

    def test_one_byte_past_the_budget_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(b"x" * 65))

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(
                RecordingStream(),  # type: ignore[arg-type]
                invocation(max_output_bytes=64),
            )

        assert caught.value.reason == OUTPUT_TOO_LARGE

    def test_over_budget_output_is_never_truncated_and_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A truncated JSON document could parse into a smaller, wrong truth."""
        monkeypatch.setattr(
            RUN,
            write_stdout(b'{"format": {"format_name": "wav"}} ' + b"x" * 100),
        )

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(
                RecordingStream(),  # type: ignore[arg-type]
                invocation(max_output_bytes=32),
            )

        assert caught.value.reason == OUTPUT_TOO_LARGE

    def test_the_message_names_the_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(b"x" * 65))

        with pytest.raises(EngineInvocationError, match="64 byte budget"):
            run_ffprobe(
                RecordingStream(),  # type: ignore[arg-type]
                invocation(max_output_bytes=64),
            )

    def test_empty_output_is_not_an_output_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It is not valid JSON either, but that is the adapter's call, not this one."""
        monkeypatch.setattr(RUN, write_stdout(b""))

        assert run_ffprobe(RecordingStream(), invocation()) == ""  # type: ignore[arg-type]


class TestEveryFailureIsClassified:
    def test_a_missing_executable_is_a_launch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def missing(arguments: list[str], **kwargs: Any) -> Any:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(RUN, missing)

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == LAUNCH_ERROR
        assert caught.value.detail == "No such file or directory"

    def test_a_permission_failure_is_also_a_launch_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(arguments: list[str], **kwargs: Any) -> Any:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(RUN, refused)

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == LAUNCH_ERROR

    def test_a_timeout_is_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def slow(arguments: list[str], **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30.0)

        monkeypatch.setattr(RUN, slow)

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == TIMEOUT
        assert "30.0s" in str(caught.value)

    @pytest.mark.parametrize("status", [1, 2, 69, 255, -9])
    def test_any_nonzero_exit_is_a_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        monkeypatch.setattr(RUN, lambda *a, **k: FakeCompleted(returncode=status))

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == NONZERO_EXIT
        assert caught.value.returncode == status

    def test_invalid_utf8_output_is_invalid_utf8(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(b'{"a": "\xff\xfe"}'))

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == INVALID_UTF8

    def test_a_failing_input_stream_is_a_staging_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def never(arguments: list[str], **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError("the child must not be launched")

        monkeypatch.setattr(RUN, never)

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(FailingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == STAGING_ERROR
        assert caught.value.detail == "Input/output error"

    def test_an_unreadable_output_file_is_a_staging_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A temporary file this machine cannot read back is never the media's fault."""

        class Unreadable:
            """Delegates everything but `read`, which fails as a bad disk does."""

            def __init__(self, real: IO[bytes]) -> None:
                self.real = real

            def __enter__(self) -> "Unreadable":
                self.real.__enter__()
                return self

            def __exit__(self, *exc: Any) -> None:
                self.real.__exit__(*exc)

            def write(self, data: bytes) -> int:
                return self.real.write(data)

            def seek(self, offset: int, whence: int = 0) -> int:
                return self.real.seek(offset, whence)

            def tell(self) -> int:
                return self.real.tell()

            def fileno(self) -> int:
                return self.real.fileno()

            def read(self, size: int = -1) -> bytes:
                raise OSError(5, "Input/output error")

        original = tempfile.TemporaryFile
        created = 0

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            nonlocal created
            created += 1
            handle = original(*args, **kwargs)
            # The sink is the second file created within one invocation.
            return Unreadable(handle) if created == 2 else handle

        monkeypatch.setattr(TEMPORARY_FILE, wrapped)
        monkeypatch.setattr(RUN, write_stdout(OK))

        with pytest.raises(EngineInvocationError) as caught:
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

        assert caught.value.reason == STAGING_ERROR
        assert caught.value.detail == "Input/output error"

    def test_nothing_broader_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A defect in this module is a bug, not a probe outcome."""

        def broken(arguments: list[str], **kwargs: Any) -> Any:
            raise TypeError("a defect")

        monkeypatch.setattr(RUN, broken)

        with pytest.raises(TypeError):
            run_ffprobe(RecordingStream(), invocation())  # type: ignore[arg-type]

    def test_every_reason_is_a_distinct_constant(self) -> None:
        reasons = {
            STAGING_ERROR,
            LAUNCH_ERROR,
            TIMEOUT,
            NONZERO_EXIT,
            OUTPUT_TOO_LARGE,
            INVALID_UTF8,
        }

        assert len(reasons) == 6
