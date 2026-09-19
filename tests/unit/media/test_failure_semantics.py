"""One failure type, and the lifecycle that depends on it.

The single most important property of this adapter is negative: **it never
reaches a verdict about the submitted media.** Every way it can fail — the
engine is missing, it timed out, it exited nonzero, it printed garbage, it
printed too much — leaves as
:class:`~core.processing.media_probe.MediaProbeExecutionError`, which means *no
trusted structural result*, and never as
:class:`~core.processing.errors.ProcessingInputError`, which would durably mark
somebody's capture ``failed`` on evidence that does not exist.

That distinction is what keeps the two existing lifecycles apart:

    a runtime probe failure -> MediaProbeExecutionError -> 503 -> capture stays
    PROCESSING, and no ContentObject is written

    a trusted result that fails container or stream policy -> ProcessingInputError
    -> 422 -> capture FAILED

Phase 5A-3 changes neither, and this file is where that is checked rather than
asserted in prose.
"""

import inspect
import subprocess
from typing import Any

import pytest

from core.processing.errors import ProcessingError, ProcessingInputError
from core.processing.media_probe import MediaProbeExecutionError
from unimem_media import engine, ffprobe
from unimem_media.ffprobe import FfprobeMediaProbe
from unimem_media.policy import MediaProbeLimits

from .fakes import RUN, FailingStream, FakeCompleted, RecordingStream, write_stdout


def failing(**kwargs: Any) -> Any:
    """A `subprocess.run` double that fails the way ``kwargs`` describes."""

    def run(arguments: list[str], **run_kwargs: Any) -> Any:
        if "raise_" in kwargs:
            raise kwargs["raise_"]
        if "returncode" in kwargs:
            return FakeCompleted(returncode=kwargs["returncode"])
        return write_stdout(kwargs["output"])(arguments, **run_kwargs)

    return run


class TestEveryAdapterFailureIsAnExecutionFailure:
    @pytest.mark.parametrize(
        ("label", "double"),
        [
            ("launch failure", failing(raise_=FileNotFoundError(2, "No such file"))),
            ("permission failure", failing(raise_=PermissionError(13, "Permission denied"))),
            (
                "timeout",
                failing(raise_=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30.0)),
            ),
            ("nonzero exit", failing(returncode=1)),
            ("invalid utf-8", failing(output=b'{"format": "\xff\xfe"}')),
            ("invalid json", failing(output=b"not json at all")),
            ("wrong top-level shape", failing(output=b"[]")),
            ("no format section", failing(output=b'{"streams": []}')),
            ("no container name", failing(output=b'{"format": {"duration": "1.0"}}')),
            (
                "unnormalizable value",
                failing(output=b'{"format": {"format_name": "wav", "duration": "soon"}}'),
            ),
            (
                "stream with no index",
                failing(
                    output=b'{"format": {"format_name": "wav"}, '
                    b'"streams": [{"codec_type": "audio"}]}'
                ),
            ),
            (
                "stream that is not an object",
                failing(output=b'{"format": {"format_name": "wav"}, "streams": ["audio"]}'),
            ),
        ],
    )
    def test_it_becomes_a_media_probe_execution_error(
        self, monkeypatch: pytest.MonkeyPatch, label: str, double: Any
    ) -> None:
        monkeypatch.setattr(RUN, double)

        with pytest.raises(MediaProbeExecutionError):
            FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]

    def test_output_past_the_budget_becomes_one_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(b"x" * 200))
        probe = FfprobeMediaProbe(limits=MediaProbeLimits(max_output_bytes=100))

        with pytest.raises(MediaProbeExecutionError):
            probe.probe(RecordingStream())  # type: ignore[arg-type]

    def test_a_staging_failure_becomes_one_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(b"{}"))

        with pytest.raises(MediaProbeExecutionError):
            FfprobeMediaProbe().probe(FailingStream())  # type: ignore[arg-type]

    def test_the_internal_error_type_never_escapes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, failing(returncode=1))

        with pytest.raises(MediaProbeExecutionError) as caught:
            FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]

        assert not isinstance(caught.value, engine.EngineInvocationError)
        assert isinstance(caught.value.__cause__, engine.EngineInvocationError)


class TestItNeverReachesAVerdict:
    @pytest.mark.parametrize(
        "double",
        [
            failing(returncode=1),
            failing(output=b"not json"),
            failing(raise_=subprocess.TimeoutExpired(cmd="ffprobe", timeout=1.0)),
            failing(output=b'{"format": {"format_name": "N/A"}}'),
        ],
    )
    def test_no_failure_is_a_processing_input_error(
        self, monkeypatch: pytest.MonkeyPatch, double: Any
    ) -> None:
        monkeypatch.setattr(RUN, double)

        with pytest.raises(MediaProbeExecutionError) as caught:
            FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]

        assert not isinstance(caught.value, ProcessingInputError)
        assert not isinstance(caught.value, ProcessingError)

    def test_the_adapter_does_not_import_the_verdict_type_at_all(self) -> None:
        """It cannot raise what it has no name for."""
        source = inspect.getsource(ffprobe)

        assert "ProcessingInputError" not in source.split('"""')[-1]

    def test_the_execution_error_is_outside_the_processing_hierarchy(self) -> None:
        """Which is what lets it flow through the orchestrator untouched."""
        assert not issubclass(MediaProbeExecutionError, ProcessingError)

    def test_stderr_is_never_read_by_the_runner(self) -> None:
        """So ffprobe's prose can never be parsed into a claim about the media."""
        source = inspect.getsource(engine.run_ffprobe)

        assert "stderr=subprocess.DEVNULL" in source
        assert "completed.stderr" not in source

    def test_a_nonzero_exit_message_says_nothing_about_the_media(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(RUN, failing(returncode=1))

        with pytest.raises(MediaProbeExecutionError) as caught:
            FfprobeMediaProbe().probe(RecordingStream())  # type: ignore[arg-type]

        message = str(caught.value).lower()
        assert "corrupt" not in message
        assert "invalid media" not in message
        assert "unsupported" not in message
