"""The media probe seam, and the runtime validation that makes it trustworthy.

An adapter is third-party code by construction: ``core`` declares the shape it
wants and gets back whatever the implementation actually built. Type hints bind
a type checker and nothing at run time, so every rule the port depends on is
re-checked here — and every violation is a
:class:`~core.processing.media_probe.MediaProbeExecutionError`, never a
:class:`~core.processing.errors.ProcessingInputError`. A probe that contradicts
itself has said nothing about the submitted bytes, so the bytes must not be
blamed for it.

Nothing in this file needs ``ffprobe``, ``ffmpeg``, a subprocess, a temporary
file, or any system media tool, and that is a property of the design rather than
of the tests: the port is values in and values out, so the policy side is
testable on a machine with no media tooling at all.
"""

import inspect
from dataclasses import FrozenInstanceError, fields
from typing import Any

import pytest

from core.processing import (
    AudioStreamInfo,
    MediaProbe,
    MediaProbeExecutionError,
    MediaProbeResult,
    VideoStreamInfo,
    validate_media_probe_result,
)
from core.processing.errors import ProcessingError, ProcessingInputError


def audio(index: Any = 0, **overrides: Any) -> AudioStreamInfo:
    fields_: dict[str, Any] = {
        "index": index,
        "codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }
    return AudioStreamInfo(**(fields_ | overrides))


def video(index: Any = 1, **overrides: Any) -> VideoStreamInfo:
    fields_: dict[str, Any] = {
        "index": index,
        "codec": "h264",
        "width": 1920,
        "height": 1080,
        "frame_rate": "30000/1001",
    }
    return VideoStreamInfo(**(fields_ | overrides))


def result(**overrides: Any) -> MediaProbeResult:
    fields_: dict[str, Any] = {
        "container_names": ("mov", "mp4"),
        "duration_seconds": 12.5,
        "audio_streams": (audio(),),
        "video_streams": (video(),),
    }
    return MediaProbeResult(**(fields_ | overrides))


class TestThePortSurface:
    """What crosses the seam, and — more importantly — what does not."""

    def test_the_public_surface_is_exactly_six_names(self) -> None:
        """The seam is the six names below, and nothing else escapes with them.

        ``_PLACEHOLDER_VALUES`` is the validator's own vocabulary for one kind of
        malformed answer, not part of the contract. Exporting it would read as a
        list of spellings for an adapter to avoid — and therefore as a licence to
        emit anything not on it — when the actual instruction is to *omit* what
        was not observed. Keeping it private also lets the set grow as engines
        are met without that being a public change.
        """
        import core.processing as processing
        import core.processing.media_probe as media_probe

        exported = {
            name
            for name in vars(media_probe)
            if not name.startswith("_")
            and name not in {"math", "re", "dataclass", "BinaryIO", "Final", "Protocol"}
        }

        assert exported == {
            "MediaProbe",
            "MediaProbeResult",
            "AudioStreamInfo",
            "VideoStreamInfo",
            "MediaProbeExecutionError",
            "validate_media_probe_result",
        }
        assert exported <= set(processing.__all__)
        assert not hasattr(processing, "PLACEHOLDER_VALUES")
        assert "PLACEHOLDER_VALUES" not in processing.__all__
        assert not hasattr(media_probe, "PLACEHOLDER_VALUES")

    def test_probe_takes_the_stream_and_nothing_else(self) -> None:
        """The narrowness is the contract, so it is asserted rather than trusted.

        A MIME type would let the adapter agree with what the submitter
        declared instead of observing independently; a path would make this port
        able to express a filesystem instruction. Neither may be added without
        this test failing first.
        """
        signature = inspect.signature(MediaProbe.probe)

        assert list(signature.parameters) == ["self", "stream"]

    @pytest.mark.parametrize(
        "forbidden",
        ["mime_type", "modality", "capture_id", "filename", "raw_object", "path", "timeout"],
    )
    def test_no_capture_or_execution_detail_is_passed(self, forbidden: str) -> None:
        assert forbidden not in inspect.signature(MediaProbe.probe).parameters

    def test_the_result_has_no_stream_count_fields(self) -> None:
        """Counts are derived — ``len(...)`` cannot disagree with its own list."""
        names = {field.name for field in fields(MediaProbeResult)}

        assert names == {
            "container_names",
            "duration_seconds",
            "audio_streams",
            "video_streams",
        }
        assert not {name for name in names if "count" in name}

    def test_the_result_models_no_other_stream_type(self) -> None:
        """Subtitle, data and attachment streams are allowed and not described."""
        names = {field.name for field in fields(MediaProbeResult)}

        assert not names & {"subtitle_streams", "data_streams", "attachment_streams", "streams"}

    def test_the_result_records_no_engine_identity(self) -> None:
        """Which tool read the container does not change what the container says."""
        names = {field.name for field in fields(MediaProbeResult)}

        assert not names & {"engine", "engine_version", "raw", "json", "ffprobe"}

    @pytest.mark.parametrize(
        ("value_type", "expected"),
        [
            (AudioStreamInfo, {"index", "codec", "sample_rate", "channels"}),
            (VideoStreamInfo, {"index", "codec", "width", "height", "frame_rate"}),
        ],
        ids=["audio", "video"],
    )
    def test_the_stream_values_carry_exactly_their_documented_fields(
        self, value_type: type, expected: set[str]
    ) -> None:
        assert {field.name for field in fields(value_type)} == expected

    @pytest.mark.parametrize(
        ("value", "field"),
        [(result(), "duration_seconds"), (audio(), "index"), (video(), "index")],
        ids=["result", "audio", "video"],
    )
    def test_the_values_are_immutable(self, value: Any, field: str) -> None:
        """A validated result stays the value that was validated."""
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, 99)


class TestTheFailureType:
    """One failure, and what it deliberately is not."""

    def test_it_is_not_a_processing_error(self) -> None:
        """So the orchestrator leaves the capture non-terminal rather than failed."""
        assert not issubclass(MediaProbeExecutionError, ProcessingError)
        assert not issubclass(MediaProbeExecutionError, ProcessingInputError)

    def test_it_is_a_plain_exception(self) -> None:
        assert issubclass(MediaProbeExecutionError, Exception)
        assert MediaProbeExecutionError.__mro__[1] is Exception

    def test_there_is_no_prerequisite_error_in_core(self) -> None:
        """Whether an engine is installed is an adapter's fact, not the kernel's."""
        import core.processing as processing
        import core.processing.media_probe as media_probe

        assert not hasattr(media_probe, "MediaProbePrerequisiteError")
        assert not hasattr(processing, "MediaProbePrerequisiteError")


class TestAValidResultIsAccepted:
    """The shapes an adapter is allowed to report, each returning quietly."""

    def test_a_full_audio_and_video_result(self) -> None:
        validate_media_probe_result(result())

    def test_an_audio_only_result(self) -> None:
        validate_media_probe_result(
            result(container_names=("mp3",), audio_streams=(audio(),), video_streams=())
        )

    def test_a_video_only_result_needs_no_audio(self) -> None:
        """ADR-021: ``VIDEO`` requires a video stream and does not require audio."""
        validate_media_probe_result(
            result(container_names=("webm",), audio_streams=(), video_streams=(video(index=0),))
        )

    def test_a_result_with_no_streams_at_all(self) -> None:
        """Not this validator's verdict to reach — that is the processors' policy."""
        validate_media_probe_result(result(audio_streams=(), video_streams=()))

    def test_a_result_with_no_duration(self) -> None:
        """A live-recorded or truncated container may declare none."""
        validate_media_probe_result(result(duration_seconds=None))

    def test_a_zero_duration(self) -> None:
        validate_media_probe_result(result(duration_seconds=0.0))

    def test_every_optional_stream_field_may_be_absent(self) -> None:
        validate_media_probe_result(
            result(
                audio_streams=(
                    AudioStreamInfo(index=0, codec=None, sample_rate=None, channels=None),
                ),
                video_streams=(
                    VideoStreamInfo(index=1, codec=None, width=None, height=None, frame_rate=None),
                ),
            )
        )

    def test_a_single_container_family(self) -> None:
        validate_media_probe_result(result(container_names=("wav",)))

    @pytest.mark.parametrize("frame_rate", ["30/1", "25/1", "30000/1001", "24000/1001", "1/1"])
    def test_canonical_frame_rates(self, frame_rate: str) -> None:
        validate_media_probe_result(result(video_streams=(video(frame_rate=frame_rate),)))


def expect_rejection(candidate: object, *, match: str) -> None:
    """Every rejection is the one execution error, and never a verdict."""
    with pytest.raises(MediaProbeExecutionError, match=match) as raised:
        validate_media_probe_result(candidate)

    assert not isinstance(raised.value, ProcessingError)


class TestTheResultTypeItself:
    """The first check, because an adapter can return anything at all."""

    @pytest.mark.parametrize(
        "candidate",
        [None, {}, {"container_names": ("mp4",)}, [], "mp4", 0, object()],
        ids=["none", "empty-dict", "dict", "list", "str", "int", "object"],
    )
    def test_a_non_result_is_rejected(self, candidate: object) -> None:
        expect_rejection(candidate, match="where a probe result was expected")

    def test_the_message_repeats_only_the_type_name(self) -> None:
        """It reaches a server log, and the object may hold anything at all."""
        with pytest.raises(MediaProbeExecutionError) as raised:
            validate_media_probe_result({"secret": "/home/someone/private.mp4"})

        assert "private.mp4" not in str(raised.value)
        assert "dict" in str(raised.value)


class TestContainerNames:
    """Non-empty, normalized, unique, and deterministically ordered."""

    def test_an_empty_tuple_is_rejected(self) -> None:
        expect_rejection(result(container_names=()), match="no container name at all")

    @pytest.mark.parametrize(
        "names", [["mp4"], "mp4", {"mp4"}, None], ids=["list", "str", "set", "none"]
    )
    def test_a_non_tuple_collection_is_rejected(self, names: object) -> None:
        expect_rejection(result(container_names=names), match="tuple of container names")

    @pytest.mark.parametrize("name", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
    def test_a_blank_name_is_rejected(self, name: str) -> None:
        expect_rejection(result(container_names=(name,)), match="a container name as an empty")

    @pytest.mark.parametrize("name", ["MP4", "Matroska", "WebM"])
    def test_an_uppercase_name_is_rejected(self, name: str) -> None:
        expect_rejection(result(container_names=(name,)), match="not lowercase")

    @pytest.mark.parametrize("name", [" mp4", "mp4 ", "\tmp4"])
    def test_a_name_with_surrounding_whitespace_is_rejected(self, name: str) -> None:
        expect_rejection(result(container_names=(name,)), match="surrounding whitespace")

    @pytest.mark.parametrize("name", ["n/a", "na", "unknown", "none", "null"])
    def test_a_placeholder_name_is_rejected(self, name: str) -> None:
        """An engine's way of writing ``None`` must arrive as omission, not text."""
        expect_rejection(result(container_names=(name,)), match="placeholder")

    @pytest.mark.parametrize("name", [None, 4, b"mp4"], ids=["none", "int", "bytes"])
    def test_a_non_string_name_is_rejected(self, name: object) -> None:
        expect_rejection(result(container_names=(name,)), match="where a container name")

    def test_a_duplicate_name_is_rejected(self) -> None:
        expect_rejection(result(container_names=("mp4", "mp4")), match="same container name twice")

    @pytest.mark.parametrize(
        "names", [("mp4", "mov"), ("webm", "matroska"), ("b", "a")], ids=["mp4", "webm", "ab"]
    )
    def test_an_unsorted_tuple_is_rejected(self, names: tuple[str, ...]) -> None:
        """Two probes of one file must not produce two different stored objects."""
        expect_rejection(result(container_names=names), match="out of sorted order")

    def test_nothing_is_repaired_on_the_way_through(self) -> None:
        """A valid result comes back untouched; the validator returns ``None``."""
        probed = result(container_names=("mov", "mp4"))

        validate_media_probe_result(probed)

        assert probed.container_names == ("mov", "mp4")


class TestDuration:
    """Finite, non-negative, and the declared type."""

    @pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_duration_is_rejected(self, duration: float) -> None:
        expect_rejection(result(duration_seconds=duration), match="duration of")

    def test_a_negative_duration_is_rejected(self) -> None:
        expect_rejection(result(duration_seconds=-0.5), match="duration of -0.5 seconds")

    @pytest.mark.parametrize(
        "duration", ["12.5", True, 12, None.__class__], ids=["str", "bool", "int", "type"]
    )
    def test_a_duration_of_the_wrong_type_is_rejected(self, duration: object) -> None:
        expect_rejection(result(duration_seconds=duration), match="where a float or nothing")


class TestStreamIndexes:
    """Real, non-negative, globally unique, and in order."""

    def test_a_negative_audio_index_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(audio(index=-1),), video_streams=()),
            match="stream index of -1",
        )

    def test_a_negative_video_index_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(index=-2),)),
            match="stream index of -2",
        )

    @pytest.mark.parametrize("index", [True, False], ids=["true", "false"])
    def test_a_boolean_index_is_rejected(self, index: object) -> None:
        """``bool`` is a subclass of ``int``; ``isinstance`` alone would let it by."""
        expect_rejection(
            result(audio_streams=(audio(index=index),), video_streams=()),
            match="bool as a stream index",
        )

    @pytest.mark.parametrize("index", ["0", 0.0, None], ids=["str", "float", "none"])
    def test_a_non_integer_index_is_rejected(self, index: object) -> None:
        expect_rejection(
            result(audio_streams=(audio(index=index),), video_streams=()),
            match="as a stream index",
        )

    def test_a_duplicate_index_within_the_audio_streams_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(audio(index=0), audio(index=0)), video_streams=()),
            match="stream index 0 more than once",
        )

    def test_a_duplicate_index_within_the_video_streams_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(index=1), video(index=1))),
            match="stream index 1 more than once",
        )

    def test_an_index_shared_across_audio_and_video_is_rejected(self) -> None:
        """Container indexes number one sequence, so a shared slot is two claims."""
        expect_rejection(
            result(audio_streams=(audio(index=0),), video_streams=(video(index=0),)),
            match="stream index 0 more than once",
        )

    def test_unsorted_audio_streams_are_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(audio(index=2), audio(index=0)), video_streams=()),
            match="audio streams out of index order",
        )

    def test_unsorted_video_streams_are_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(index=3), video(index=1))),
            match="video streams out of index order",
        )

    def test_index_zero_is_accepted(self) -> None:
        """Container numbering starts at zero; the rule is non-negative, not positive."""
        validate_media_probe_result(
            result(audio_streams=(audio(index=0),), video_streams=(video(index=1),))
        )

    def test_the_two_collections_may_interleave(self) -> None:
        """Each list is ordered in itself; they need not be contiguous."""
        validate_media_probe_result(
            result(
                audio_streams=(audio(index=1), audio(index=3)),
                video_streams=(video(index=0), video(index=2)),
            )
        )


class TestStreamCollections:
    """Typed values in typed tuples, and nothing that merely resembles one."""

    @pytest.mark.parametrize("streams", [[audio()], audio(), None], ids=["list", "bare", "none"])
    def test_a_non_tuple_audio_collection_is_rejected(self, streams: object) -> None:
        expect_rejection(result(audio_streams=streams), match="tuple of audio streams")

    @pytest.mark.parametrize("streams", [[video()], video(), None], ids=["list", "bare", "none"])
    def test_a_non_tuple_video_collection_is_rejected(self, streams: object) -> None:
        expect_rejection(result(video_streams=streams), match="tuple of video streams")

    def test_a_video_stream_in_the_audio_collection_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(video(index=0),)), match="where an audio stream was expected"
        )

    def test_an_audio_stream_in_the_video_collection_is_rejected(self) -> None:
        expect_rejection(
            result(video_streams=(audio(index=0),)), match="where a video stream was expected"
        )

    def test_a_mapping_that_looks_right_is_still_rejected(self) -> None:
        """Raw engine output wearing a costume is not a normalized value."""
        expect_rejection(
            result(audio_streams=({"index": 0, "codec": "aac"},)),
            match="where an audio stream was expected",
        )


class TestOptionalNumericFields:
    """Present means a genuine positive ``int``; absent means ``None``."""

    @pytest.mark.parametrize("field", ["sample_rate", "channels"])
    @pytest.mark.parametrize("value", [0, -1], ids=["zero", "negative"])
    def test_a_non_positive_audio_number_is_rejected(self, field: str, value: int) -> None:
        expect_rejection(
            result(audio_streams=(audio(**{field: value}),), video_streams=()),
            match="for stream 0",
        )

    @pytest.mark.parametrize("field", ["width", "height"])
    @pytest.mark.parametrize("value", [0, -1080], ids=["zero", "negative"])
    def test_a_non_positive_video_number_is_rejected(self, field: str, value: int) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(**{field: value}),)),
            match="for stream 1",
        )

    @pytest.mark.parametrize("field", ["sample_rate", "channels"])
    @pytest.mark.parametrize("value", [True, "48000", 48000.0], ids=["bool", "str", "float"])
    def test_an_audio_number_of_the_wrong_type_is_rejected(self, field: str, value: object) -> None:
        expect_rejection(
            result(audio_streams=(audio(**{field: value}),), video_streams=()),
            match="where a positive integer or nothing was expected",
        )

    @pytest.mark.parametrize("field", ["width", "height"])
    @pytest.mark.parametrize("value", [True, "1920", 1920.0], ids=["bool", "str", "float"])
    def test_a_video_number_of_the_wrong_type_is_rejected(self, field: str, value: object) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(**{field: value}),)),
            match="where a positive integer or nothing was expected",
        )


class TestCodecNames:
    """Same normalization rule as a container name, and for the same reason."""

    @pytest.mark.parametrize("codec", ["", "  "], ids=["empty", "spaces"])
    def test_a_blank_codec_is_rejected(self, codec: str) -> None:
        expect_rejection(
            result(audio_streams=(audio(codec=codec),), video_streams=()),
            match="the codec of stream 0 as an empty",
        )

    @pytest.mark.parametrize("codec", ["AAC", "H264"])
    def test_an_uppercase_codec_is_rejected(self, codec: str) -> None:
        expect_rejection(
            result(audio_streams=(audio(codec=codec),), video_streams=()), match="not lowercase"
        )

    @pytest.mark.parametrize("codec", ["n/a", "N/A", "unknown", "none"])
    def test_a_placeholder_codec_is_rejected(self, codec: str) -> None:
        """``N/A`` is an engine writing ``None``; omission says it honestly."""
        expect_rejection(
            result(audio_streams=(audio(codec=codec),), video_streams=()),
            match="placeholder|not lowercase",
        )

    def test_a_codec_with_surrounding_whitespace_is_rejected(self) -> None:
        expect_rejection(
            result(audio_streams=(), video_streams=(video(codec=" h264"),)),
            match="surrounding whitespace",
        )

    @pytest.mark.parametrize("codec", [0, b"aac", ["aac"]], ids=["int", "bytes", "list"])
    def test_a_non_string_codec_is_rejected(self, codec: object) -> None:
        expect_rejection(
            result(audio_streams=(audio(codec=codec),), video_streams=()),
            match="where the codec of stream 0 was expected",
        )

    def test_the_message_names_the_stream_it_came_from(self) -> None:
        with pytest.raises(MediaProbeExecutionError, match="the codec of stream 4"):
            validate_media_probe_result(
                result(audio_streams=(audio(index=4, codec="AAC"),), video_streams=())
            )


class TestFrameRate:
    """A canonical, reduced, positive rational string, or nothing."""

    @pytest.mark.parametrize(
        "frame_rate",
        ["0/0", "0/1", "30/0", "-30/1", "30", "29.97", "30:1", "", " 30/1", "30/1 ", "30 / 1"],
    )
    def test_a_malformed_frame_rate_is_rejected(self, frame_rate: str) -> None:
        expect_rejection(
            result(video_streams=(video(frame_rate=frame_rate),)),
            match="not a canonical positive rational",
        )

    @pytest.mark.parametrize("frame_rate", ["030/1", "30/001"])
    def test_a_leading_zero_is_rejected(self, frame_rate: str) -> None:
        expect_rejection(
            result(video_streams=(video(frame_rate=frame_rate),)),
            match="not a canonical positive rational",
        )

    @pytest.mark.parametrize("frame_rate", ["60/2", "50/2", "60000/2002"])
    def test_an_unreduced_frame_rate_is_rejected(self, frame_rate: str) -> None:
        """``60/2`` and ``30/1`` are one rate written two ways, and would compare unequal."""
        expect_rejection(
            result(video_streams=(video(frame_rate=frame_rate),)), match="is not reduced"
        )

    @pytest.mark.parametrize(
        "frame_rate", [30, 29.97, True, ["30", "1"]], ids=["int", "float", "bool", "list"]
    )
    def test_a_non_string_frame_rate_is_rejected(self, frame_rate: object) -> None:
        expect_rejection(
            result(video_streams=(video(frame_rate=frame_rate),)),
            match="where a rational string or nothing was expected",
        )

    def test_the_message_names_the_stream_it_came_from(self) -> None:
        with pytest.raises(MediaProbeExecutionError, match="frame rate of stream 7"):
            validate_media_probe_result(result(video_streams=(video(index=7, frame_rate="0/0"),)))


class TestNothingHereIsAPolicyDecision:
    """The validator checks the port contract and stops there."""

    def test_an_audio_only_container_with_a_video_name_is_accepted(self) -> None:
        """Whether ``mp4`` may carry no video is the processors' question, later."""
        validate_media_probe_result(
            result(container_names=("mp4",), audio_streams=(audio(),), video_streams=())
        )

    def test_an_unknown_container_family_is_accepted(self) -> None:
        """The future MIME and container allowlists are policy, not port shape."""
        validate_media_probe_result(result(container_names=("flac",)))

    def test_many_streams_of_each_kind_are_accepted(self) -> None:
        """No primary-stream selection happens here, or anywhere in Phase 5A."""
        validate_media_probe_result(
            result(
                audio_streams=(audio(index=1), audio(index=2)),
                video_streams=(video(index=0), video(index=3)),
            )
        )
