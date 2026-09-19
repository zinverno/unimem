"""Turning ffprobe's vocabulary into ``core``'s values, exactly and only.

Every document below is the shape a real ffprobe prints — verified against
ffprobe 6.1 while this adapter was written — so these are recorded engine
answers rather than invented ones.

What is being pinned down is the translation itself: aliases split and sorted,
numbers parsed from strings, ``"N/A"`` becoming omission, ``"0/0"`` becoming
``None``, subtitle and data streams ignored, and a stable order. And the
negative half, which matters just as much: nothing is manufactured, nothing is
repaired, and a value that cannot be normalized is a failure rather than a
quiet zero.
"""

import json
from typing import Any, Final

import pytest

from core.processing.media_probe import (
    MediaProbeExecutionError,
    MediaProbeResult,
    validate_media_probe_result,
)
from unimem_media.ffprobe import normalize

#: A real ffprobe answer for a one-second mono WAV.
WAV: Final = {
    "programs": [],
    "streams": [
        {
            "index": 0,
            "codec_name": "pcm_s16le",
            "codec_type": "audio",
            "sample_rate": "44100",
            "channels": 1,
            "avg_frame_rate": "0/0",
        }
    ],
    "format": {"format_name": "wav", "duration": "1.000000"},
}

#: A real ffprobe answer for an MP4 carrying video and audio.
AV_MP4: Final = {
    "programs": [],
    "streams": [
        {
            "index": 0,
            "codec_name": "h264",
            "codec_type": "video",
            "width": 64,
            "height": 48,
            "avg_frame_rate": "30000/1001",
        },
        {
            "index": 1,
            "codec_name": "aac",
            "codec_type": "audio",
            "sample_rate": "48000",
            "channels": 2,
            "avg_frame_rate": "0/0",
        },
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.001000"},
}


def probe(payload: Any) -> MediaProbeResult:
    return normalize(json.dumps(payload))


class TestContainerAliases:
    def test_a_single_name_becomes_a_one_element_tuple(self) -> None:
        assert probe(WAV).container_names == ("wav",)

    def test_a_family_is_split_on_commas(self) -> None:
        assert probe(AV_MP4).container_names == ("3g2", "3gp", "m4a", "mj2", "mov", "mp4")

    def test_the_whole_family_is_kept(self) -> None:
        """A container genuinely is several things; picking one would be a rewrite."""
        assert len(probe(AV_MP4).container_names) == 6

    def test_matroska_and_webm_both_survive(self) -> None:
        payload = {"format": {"format_name": "matroska,webm"}}

        assert probe(payload).container_names == ("matroska", "webm")

    def test_names_are_lowercased(self) -> None:
        payload = {"format": {"format_name": "MOV,MP4"}}

        assert probe(payload).container_names == ("mov", "mp4")

    def test_names_are_trimmed(self) -> None:
        payload = {"format": {"format_name": " mov , mp4 "}}

        assert probe(payload).container_names == ("mov", "mp4")

    def test_names_are_sorted(self) -> None:
        payload = {"format": {"format_name": "webm,matroska"}}

        assert probe(payload).container_names == ("matroska", "webm")

    def test_duplicates_collapse(self) -> None:
        payload = {"format": {"format_name": "mp4,mp4,mov"}}

        assert probe(payload).container_names == ("mov", "mp4")

    def test_empty_aliases_are_dropped(self) -> None:
        payload = {"format": {"format_name": "mp4,,mov"}}

        assert probe(payload).container_names == ("mov", "mp4")

    def test_a_missing_container_name_is_an_execution_failure(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe({"format": {"duration": "1.0"}})

    def test_a_placeholder_container_name_is_an_execution_failure(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe({"format": {"format_name": "N/A"}})

    def test_a_blank_container_name_is_an_execution_failure(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe({"format": {"format_name": "  ,  "}})

    def test_a_non_string_container_name_is_an_execution_failure(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe({"format": {"format_name": 4}})


class TestDuration:
    def test_a_declared_duration_becomes_a_float(self) -> None:
        assert probe(WAV).duration_seconds == 1.0

    def test_precision_is_preserved(self) -> None:
        assert probe(AV_MP4).duration_seconds == 1.001

    def test_an_absent_duration_is_none(self) -> None:
        """A live stream or a truncated upload declares none, which is a fact."""
        assert probe({"format": {"format_name": "ogg"}}).duration_seconds is None

    def test_the_unavailable_representation_is_none(self) -> None:
        payload = {"format": {"format_name": "ogg", "duration": "N/A"}}

        assert probe(payload).duration_seconds is None

    def test_zero_is_a_duration_and_not_an_omission(self) -> None:
        payload = {"format": {"format_name": "wav", "duration": "0.000000"}}

        assert probe(payload).duration_seconds == 0.0

    def test_a_numeric_duration_is_accepted_too(self) -> None:
        payload = {"format": {"format_name": "wav", "duration": 2.5}}

        assert probe(payload).duration_seconds == 2.5

    def test_it_is_never_inferred_from_a_stream(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [
                {"index": 0, "codec_type": "audio", "duration": "9.0", "avg_frame_rate": "0/0"}
            ],
        }

        assert probe(payload).duration_seconds is None

    @pytest.mark.parametrize("value", ["abc", "-1.0", "nan", "inf", True, []])
    def test_an_unusable_duration_is_an_execution_failure(self, value: Any) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe({"format": {"format_name": "wav", "duration": value}})


class TestAudioStreams:
    def test_the_stream_is_translated(self) -> None:
        audio = probe(WAV).audio_streams

        assert len(audio) == 1
        assert audio[0].index == 0
        assert audio[0].codec == "pcm_s16le"
        assert audio[0].sample_rate == 44100
        assert audio[0].channels == 1

    def test_the_sample_rate_string_becomes_an_integer(self) -> None:
        assert probe(AV_MP4).audio_streams[0].sample_rate == 48000

    def test_an_absent_codec_is_omitted(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0, "codec_type": "audio"}],
        }

        assert probe(payload).audio_streams[0].codec is None

    def test_a_placeholder_codec_is_omitted(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "N/A"}],
        }

        assert probe(payload).audio_streams[0].codec is None

    def test_an_absent_sample_rate_is_omitted_and_never_zero(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0, "codec_type": "audio"}],
        }
        stream = probe(payload).audio_streams[0]

        assert stream.sample_rate is None
        assert stream.channels is None

    def test_a_placeholder_channel_count_is_omitted(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0, "codec_type": "audio", "channels": "N/A"}],
        }

        assert probe(payload).audio_streams[0].channels is None

    @pytest.mark.parametrize("value", ["0", 0, -1, "abc", True, []])
    def test_an_unusable_sample_rate_is_an_execution_failure(self, value: Any) -> None:
        """Turning a broken declaration into `None` would manufacture an omission."""
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0, "codec_type": "audio", "sample_rate": value}],
        }

        with pytest.raises(MediaProbeExecutionError):
            probe(payload)


class TestVideoStreams:
    def test_the_stream_is_translated(self) -> None:
        video = probe(AV_MP4).video_streams

        assert len(video) == 1
        assert video[0].index == 0
        assert video[0].codec == "h264"
        assert video[0].width == 64
        assert video[0].height == 48

    def test_the_frame_rate_keeps_its_exact_rational(self) -> None:
        """`30000/1001`, never `29.97`: the rounding could not be undone later."""
        assert probe(AV_MP4).video_streams[0].frame_rate == "30000/1001"

    def test_a_whole_number_rate_keeps_its_denominator(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": "25/1"}],
        }

        assert probe(payload).video_streams[0].frame_rate == "25/1"

    def test_an_unreduced_rate_is_reduced_exactly(self) -> None:
        """Two spellings of one rational would compare unequal on a stored object."""
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": "60/2"}],
        }

        assert probe(payload).video_streams[0].frame_rate == "30/1"

    def test_the_unknown_representation_becomes_none(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": "0/0"}],
        }

        assert probe(payload).video_streams[0].frame_rate is None

    def test_a_zero_numerator_is_also_unknown(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": "0/1"}],
        }

        assert probe(payload).video_streams[0].frame_rate is None

    def test_an_absent_frame_rate_is_none(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video"}],
        }

        assert probe(payload).video_streams[0].frame_rate is None

    @pytest.mark.parametrize("value", ["30", "30/0", "-30/1", "30/-1", "abc", "1/2/3", 25])
    def test_an_unusable_frame_rate_is_an_execution_failure(self, value: Any) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video", "avg_frame_rate": value}],
        }

        with pytest.raises(MediaProbeExecutionError):
            probe(payload)

    def test_absent_dimensions_are_omitted(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"index": 0, "codec_type": "video"}],
        }
        stream = probe(payload).video_streams[0]

        assert stream.width is None
        assert stream.height is None


class TestStreamsIgnoredAndOrdered:
    def test_subtitle_streams_are_ignored(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 0, "codec_type": "video", "avg_frame_rate": "25/1"},
                {"index": 1, "codec_type": "subtitle", "codec_name": "webvtt"},
            ],
        }
        result = probe(payload)

        assert len(result.video_streams) == 1
        assert result.audio_streams == ()

    @pytest.mark.parametrize("kind", ["subtitle", "data", "attachment", "nb", "unknown"])
    def test_every_other_stream_type_is_ignored(self, kind: str) -> None:
        payload = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {"index": 0, "codec_type": "audio"},
                {"index": 1, "codec_type": kind},
            ],
        }
        result = probe(payload)

        assert len(result.audio_streams) == 1
        assert result.video_streams == ()

    def test_a_stream_with_no_codec_type_is_ignored(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": 0}, {"index": 1, "codec_type": "audio"}],
        }

        assert [stream.index for stream in probe(payload).audio_streams] == [1]

    def test_ignoring_a_stream_leaves_a_gap_in_the_numbering(self) -> None:
        """Container indexes number one sequence; a gap is normal, not an error."""
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 0, "codec_type": "video", "avg_frame_rate": "25/1"},
                {"index": 1, "codec_type": "subtitle"},
                {"index": 2, "codec_type": "audio"},
            ],
        }
        result = probe(payload)

        assert result.video_streams[0].index == 0
        assert result.audio_streams[0].index == 2

    def test_streams_are_ordered_by_container_index(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 5, "codec_type": "audio"},
                {"index": 1, "codec_type": "audio"},
                {"index": 3, "codec_type": "audio"},
            ],
        }

        assert [stream.index for stream in probe(payload).audio_streams] == [1, 3, 5]

    def test_the_two_collections_are_ordered_independently(self) -> None:
        payload = {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"index": 3, "codec_type": "video", "avg_frame_rate": "25/1"},
                {"index": 2, "codec_type": "audio"},
                {"index": 1, "codec_type": "video", "avg_frame_rate": "25/1"},
                {"index": 0, "codec_type": "audio"},
            ],
        }
        result = probe(payload)

        assert [stream.index for stream in result.audio_streams] == [0, 2]
        assert [stream.index for stream in result.video_streams] == [1, 3]

    def test_the_same_document_normalizes_identically_twice(self) -> None:
        assert probe(AV_MP4) == probe(AV_MP4)

    def test_no_streams_at_all_is_an_empty_result_not_a_failure(self) -> None:
        """Whether a streamless container may be ingested is core's verdict."""
        result = probe({"format": {"format_name": "wav"}, "streams": []})

        assert result.audio_streams == ()
        assert result.video_streams == ()

    def test_a_missing_streams_key_is_the_same(self) -> None:
        result = probe({"format": {"format_name": "wav"}})

        assert result.audio_streams == ()
        assert result.video_streams == ()

    def test_a_stream_with_no_index_is_an_execution_failure(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"codec_type": "audio"}],
        }

        with pytest.raises(MediaProbeExecutionError):
            probe(payload)

    @pytest.mark.parametrize("value", [-1, True, "abc", 1.5, None])
    def test_an_unusable_index_is_an_execution_failure(self, value: Any) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": value, "codec_type": "audio"}],
        }

        with pytest.raises(MediaProbeExecutionError):
            probe(payload)

    def test_a_string_index_is_accepted(self) -> None:
        payload = {
            "format": {"format_name": "wav"},
            "streams": [{"index": "2", "codec_type": "audio"}],
        }

        assert probe(payload).audio_streams[0].index == 2


class TestMalformedEngineOutput:
    def test_output_that_is_not_json_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError, match="not JSON"):
            normalize("ffprobe: Invalid data found when processing input")

    def test_empty_output_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize("")

    @pytest.mark.parametrize("document", ["[]", '"text"', "4", "null", "true"])
    def test_a_non_object_document_fails(self, document: str) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize(document)

    def test_a_document_with_no_format_section_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize('{"streams": []}')

    def test_a_non_object_format_section_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize('{"format": "wav"}')

    def test_a_non_list_streams_section_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize('{"format": {"format_name": "wav"}, "streams": {"0": {}}}')

    def test_a_non_object_stream_fails(self) -> None:
        with pytest.raises(MediaProbeExecutionError):
            normalize('{"format": {"format_name": "wav"}, "streams": ["audio"]}')

    def test_an_unknown_top_level_key_is_ignored(self) -> None:
        """`programs` is always printed and answering more is not answering wrongly."""
        assert probe(WAV).container_names == ("wav",)


class TestTheResultSatisfiesCore:
    @pytest.mark.parametrize("document", [WAV, AV_MP4])
    def test_the_port_validator_accepts_it(self, document: Any) -> None:
        """The adapter normalizes; core's validator remains the final authority."""
        validate_media_probe_result(probe(document))

    def test_it_is_the_frozen_core_type(self) -> None:
        assert isinstance(probe(WAV), MediaProbeResult)

    def test_no_engine_identity_reaches_the_result(self) -> None:
        assert not [name for name in dir(probe(WAV)) if "version" in name or "engine" in name]

    def test_no_stream_is_marked_primary(self) -> None:
        result = probe(AV_MP4)

        assert not [name for name in dir(result) if "primary" in name]
