"""One probe instance, shared by two processors and called concurrently.

The composition root hands the *same* :class:`~unimem_media.ffprobe.FfprobeMediaProbe`
to :class:`~core.processing.media.AudioProcessor` and
:class:`~core.processing.media.VideoProcessor`, deliberately. That is only
correct if the instance holds nothing on a caller's behalf, so this file checks
the absence of state rather than the presence of a feature: no mutable
attribute, no shared buffer, no shared temporary name, no module-level process
state, and no lock.

The concurrency test runs real threads against one instance, with a subprocess
double that interleaves them.
"""

import threading
from typing import Any, Final

import pytest

from core.processing.media_probe import MediaProbeResult
from unimem_media.ffprobe import FfprobeMediaProbe

from .fakes import RUN, RecordingStream, write_stdout

AUDIO: Final = (
    b'{"format": {"format_name": "wav", "duration": "1.0"}, '
    b'"streams": [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le", '
    b'"sample_rate": "44100", "channels": 1}]}'
)
VIDEO: Final = (
    b'{"format": {"format_name": "matroska,webm", "duration": "2.0"}, '
    b'"streams": [{"index": 0, "codec_type": "video", "codec_name": "vp8", '
    b'"width": 64, "height": 48, "avg_frame_rate": "25/1"}]}'
)


class TestTheInstanceHoldsNoRunState:
    def test_its_attributes_are_one_frozen_value(self) -> None:
        probe = FfprobeMediaProbe()

        assert list(vars(probe)) == ["_invocation"]

    def test_that_value_is_frozen(self) -> None:
        probe = FfprobeMediaProbe()

        with pytest.raises(AttributeError):
            probe._invocation.executable = "other"  # type: ignore[misc]

    def test_probing_adds_no_attribute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RUN, write_stdout(AUDIO))
        probe = FfprobeMediaProbe()
        before = dict(vars(probe))

        probe.probe(RecordingStream())  # type: ignore[arg-type]

        assert vars(probe) == before

    def test_it_needs_no_lock(self) -> None:
        assert not [name for name in vars(FfprobeMediaProbe()) if "lock" in name]

    def test_the_module_holds_no_mutable_state(self) -> None:
        import unimem_media.ffprobe as module

        mutable = [
            name
            for name, value in vars(module).items()
            if not name.startswith("__") and isinstance(value, list | dict | set)
        ]

        assert mutable == []


class TestOneInstanceServesBothProcessors:
    def test_two_sequential_probes_are_independent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        outputs = [AUDIO, VIDEO]

        def alternating(arguments: list[str], **kwargs: Any) -> Any:
            return write_stdout(outputs.pop(0))(arguments, **kwargs)

        monkeypatch.setattr(RUN, alternating)
        probe = FfprobeMediaProbe()

        first = probe.probe(RecordingStream())  # type: ignore[arg-type]
        second = probe.probe(RecordingStream())  # type: ignore[arg-type]

        assert first.container_names == ("wav",)
        assert second.container_names == ("matroska", "webm")

    def test_the_second_probe_carries_nothing_from_the_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outputs = [VIDEO, AUDIO]

        def alternating(arguments: list[str], **kwargs: Any) -> Any:
            return write_stdout(outputs.pop(0))(arguments, **kwargs)

        monkeypatch.setattr(RUN, alternating)
        probe = FfprobeMediaProbe()

        probe.probe(RecordingStream())  # type: ignore[arg-type]
        second = probe.probe(RecordingStream())  # type: ignore[arg-type]

        assert second.video_streams == ()
        assert len(second.audio_streams) == 1


class TestConcurrentProbesOnOneInstance:
    def test_two_threads_get_their_own_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both children are in flight at once, sharing one probe object."""
        both_started = threading.Barrier(2, timeout=30)
        assignment = threading.local()

        def interleaved(arguments: list[str], **kwargs: Any) -> Any:
            # Neither call may return until the other has also started, so a
            # shared buffer or a shared temporary name would be visible here.
            both_started.wait()
            return write_stdout(assignment.output)(arguments, **kwargs)

        monkeypatch.setattr(RUN, interleaved)
        probe = FfprobeMediaProbe()
        results: dict[str, MediaProbeResult] = {}
        failures: list[BaseException] = []

        def run(name: str, output: bytes) -> None:
            assignment.output = output
            try:
                results[name] = probe.probe(RecordingStream())  # type: ignore[arg-type]
            except BaseException as exc:  # pragma: no cover - only on a real defect
                failures.append(exc)

        threads = [
            threading.Thread(target=run, args=("audio", AUDIO)),
            threading.Thread(target=run, args=("video", VIDEO)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not failures
        assert results["audio"].container_names == ("wav",)
        assert results["video"].container_names == ("matroska", "webm")
        assert results["audio"].duration_seconds == 1.0
        assert results["video"].duration_seconds == 2.0

    def test_concurrent_probes_stage_into_different_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        both_started = threading.Barrier(2, timeout=30)
        descriptors: list[int] = []
        guard = threading.Lock()

        def interleaved(arguments: list[str], **kwargs: Any) -> Any:
            both_started.wait()
            with guard:
                descriptors.append(kwargs["stdin"].fileno())
            return write_stdout(AUDIO)(arguments, **kwargs)

        monkeypatch.setattr(RUN, interleaved)
        probe = FfprobeMediaProbe()

        threads = [
            threading.Thread(target=lambda: probe.probe(RecordingStream()))  # type: ignore[arg-type]
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(descriptors) == 2
        assert descriptors[0] != descriptors[1]
