"""Real ffprobe, real media files, and the facts it actually reports.

Everything above this file runs against a double. This is the only place where
the adapter meets the program it was written for, and it is the only place that
can answer the questions a double cannot: does ffprobe really report an MP4's
container as a six-alias family, does it really spell an unknown frame rate
``0/0``, does a WAV really declare its sample rate as a string.

Every file probed here is built during the test by the system ``ffmpeg`` from a
synthetic tone or test pattern. **Nothing under ``src/`` invokes ffmpeg**; it is
a test dependency used to create inputs, and what the code under test sees is
the bytes, exactly as a submitted upload would arrive.

Without the tooling these skip. With ``UNIMEM_REQUIRE_MEDIA_INTEGRATION=1``
every missing prerequisite is a failure instead, because a skipped acceptance
test and a passing one look identical in a green CI summary.
"""

import io
from pathlib import Path

import pytest

from core.processing.media import MEDIA_CONTAINERS
from core.processing.media_probe import (
    MediaProbeExecutionError,
    MediaProbeResult,
    validate_media_probe_result,
)
from tests import media_fixtures as fixtures
from tests.media_support import require_media_tooling
from unimem_media import FfprobeMediaProbe, build_ffprobe_media_probe

require_media_tooling()


@pytest.fixture(scope="module")
def probe() -> FfprobeMediaProbe:
    """One production probe, built through the production factory.

    Module-scoped and shared by every test below, which is not a convenience:
    the same instance serving many probes concurrently is the arrangement the
    composition root uses, so exercising it here is the point.
    """
    built = build_ffprobe_media_probe()
    assert isinstance(built, FfprobeMediaProbe)
    return built


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """Every fixture, encoded once for the module."""
    directory = tmp_path_factory.mktemp("media")
    made = {fixture.name: fixtures.build(fixture, directory) for fixture in fixtures.ALL}
    made[fixtures.WEBM_WITH_SUBTITLES.name] = fixtures.build_subtitled_webm(directory)
    return made


def probed(
    probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
) -> MediaProbeResult:
    return probe.probe(io.BytesIO(built[fixture.name]))


class TestTheFixturesAreReal:
    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_each_one_was_actually_encoded(
        self, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert len(built[fixture.name]) > 0

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_each_declared_type_is_one_this_build_routes(
        self, fixture: fixtures.MediaFixture
    ) -> None:
        assert MEDIA_CONTAINERS[fixture.mime_type] == fixture.container

    def test_all_five_supported_families_are_covered(self) -> None:
        """WAV, MP3, OGG, MP4, WebM — the whole of what this build accepts."""
        covered = {fixture.container for fixture in fixtures.ALL}

        assert covered == set(MEDIA_CONTAINERS.values())


class TestTheProbeReadsRealContainers:
    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_required_alias_is_observed(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        """What the media processors check: the declared type's alias is present."""
        assert fixture.container in probed(probe, built, fixture).container_names

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_result_satisfies_the_port_contract(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        """Core's own validator, over output a real engine produced."""
        validate_media_probe_result(probed(probe, built, fixture))

    def test_an_mp4_reports_its_whole_family(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        """An ISO base media file genuinely is six things; none is picked."""
        names = probed(probe, built, fixtures.MP4).container_names

        assert names == ("3g2", "3gp", "m4a", "mj2", "mov", "mp4")

    def test_a_webm_reports_matroska_too(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        assert probed(probe, built, fixtures.WEBM).container_names == ("matroska", "webm")

    @pytest.mark.parametrize(
        "fixture", [fixtures.WAV, fixtures.MP3, fixtures.OGG], ids=lambda item: item.name
    )
    def test_a_single_family_container_reports_one_name(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert probed(probe, built, fixture).container_names == (fixture.container,)

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_the_names_come_back_sorted_and_lowercase(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        names = probed(probe, built, fixture).container_names

        assert list(names) == sorted(names)
        assert all(name == name.lower() for name in names)


class TestTheProbeReadsRealDurations:
    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_a_one_second_clip_declares_about_one_second(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        duration = probed(probe, built, fixture).duration_seconds

        assert duration is not None
        assert 0.9 <= duration <= 1.2

    @pytest.mark.parametrize("fixture", fixtures.ALL, ids=lambda item: item.name)
    def test_it_is_a_float_and_not_an_int(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert isinstance(probed(probe, built, fixture).duration_seconds, float)


class TestTheProbeReadsRealAudioStreams:
    @pytest.mark.parametrize(
        "fixture",
        [fixtures.WAV, fixtures.MP3, fixtures.OGG, fixtures.MP4_WITH_AUDIO],
        ids=lambda item: item.name,
    )
    def test_an_audio_stream_is_found(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert len(probed(probe, built, fixture).audio_streams) == 1

    @pytest.mark.parametrize(
        ("fixture", "codec"),
        [
            (fixtures.WAV, "pcm_s16le"),
            (fixtures.MP3, "mp3"),
            (fixtures.OGG, "vorbis"),
            (fixtures.MP4_WITH_AUDIO, "aac"),
        ],
        ids=lambda item: str(item),
    )
    def test_the_codec_is_the_one_it_was_encoded_with(
        self,
        probe: FfprobeMediaProbe,
        built: dict[str, bytes],
        fixture: fixtures.MediaFixture,
        codec: str,
    ) -> None:
        assert probed(probe, built, fixture).audio_streams[0].codec == codec

    @pytest.mark.parametrize(
        "fixture",
        [fixtures.WAV, fixtures.MP3, fixtures.OGG],
        ids=lambda item: item.name,
    )
    def test_the_sample_rate_is_a_real_integer(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        """ffprobe writes it as a JSON string; the port requires an int."""
        stream = probed(probe, built, fixture).audio_streams[0]

        assert stream.sample_rate == fixtures.AUDIO_SAMPLE_RATE
        assert isinstance(stream.sample_rate, int)

    @pytest.mark.parametrize(
        "fixture",
        [fixtures.WAV, fixtures.MP3, fixtures.OGG],
        ids=lambda item: item.name,
    )
    def test_the_channel_count_is_read(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert probed(probe, built, fixture).audio_streams[0].channels == fixtures.AUDIO_CHANNELS

    @pytest.mark.parametrize(
        "fixture", [fixtures.WAV, fixtures.MP3, fixtures.OGG], ids=lambda item: item.name
    )
    def test_an_audio_file_has_no_video_streams(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert probed(probe, built, fixture).video_streams == ()


class TestTheProbeReadsRealVideoStreams:
    @pytest.mark.parametrize(
        "fixture",
        [fixtures.MP4, fixtures.WEBM, fixtures.MP4_WITH_AUDIO],
        ids=lambda item: item.name,
    )
    def test_a_video_stream_is_found(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        assert len(probed(probe, built, fixture).video_streams) == 1

    @pytest.mark.parametrize(
        ("fixture", "codec"),
        [(fixtures.MP4, "h264"), (fixtures.WEBM, "vp8"), (fixtures.MP4_WITH_AUDIO, "h264")],
        ids=lambda item: str(item),
    )
    def test_the_codec_is_the_one_it_was_encoded_with(
        self,
        probe: FfprobeMediaProbe,
        built: dict[str, bytes],
        fixture: fixtures.MediaFixture,
        codec: str,
    ) -> None:
        assert probed(probe, built, fixture).video_streams[0].codec == codec

    @pytest.mark.parametrize(
        "fixture",
        [fixtures.MP4, fixtures.WEBM, fixtures.MP4_WITH_AUDIO],
        ids=lambda item: item.name,
    )
    def test_the_dimensions_are_the_ones_it_was_built_with(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        stream = probed(probe, built, fixture).video_streams[0]

        assert stream.width == fixtures.VIDEO_WIDTH
        assert stream.height == fixtures.VIDEO_HEIGHT

    @pytest.mark.parametrize(
        "fixture",
        [fixtures.MP4, fixtures.WEBM, fixtures.MP4_WITH_AUDIO],
        ids=lambda item: item.name,
    )
    def test_the_frame_rate_is_an_exact_rational(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes], fixture: fixtures.MediaFixture
    ) -> None:
        """`25/1`, not `25.0`: the exact fact the container carries."""
        assert probed(probe, built, fixture).video_streams[0].frame_rate == (
            fixtures.VIDEO_FRAME_RATE
        )

    def test_a_silent_video_is_valid_media_with_no_audio(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        """Silence is a fact about the file, not a reason to refuse it."""
        result = probed(probe, built, fixtures.MP4)

        assert result.audio_streams == ()
        assert len(result.video_streams) == 1

    def test_a_video_with_sound_reports_both_in_index_order(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        result = probed(probe, built, fixtures.MP4_WITH_AUDIO)

        assert result.video_streams[0].index == 0
        assert result.audio_streams[0].index == 1

    def test_an_unknown_frame_rate_never_reaches_the_result(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        """ffprobe spells it `0/0` on every audio stream; the port must see None."""
        for fixture in (fixtures.WAV, fixtures.MP3, fixtures.OGG):
            result = probed(probe, built, fixture)

            assert result.video_streams == ()
            assert "0/0" not in repr(result)


class TestStreamsTheProbeIgnores:
    def test_a_subtitle_track_is_ignored_rather_than_refused(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        result = probed(probe, built, fixtures.WEBM_WITH_SUBTITLES)

        assert len(result.video_streams) == 1
        assert result.audio_streams == ()

    def test_the_subtitled_file_still_satisfies_the_port(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        validate_media_probe_result(probed(probe, built, fixtures.WEBM_WITH_SUBTITLES))

    def test_its_container_is_still_the_declared_family(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        names = probed(probe, built, fixtures.WEBM_WITH_SUBTITLES).container_names

        assert "webm" in names


class TestTheRealEngineFailing:
    def test_unreadable_bytes_are_an_execution_failure(self, probe: FfprobeMediaProbe) -> None:
        """A real nonzero exit, and never a verdict about the submitted file."""
        with pytest.raises(MediaProbeExecutionError):
            probe.probe(io.BytesIO(fixtures.unreadable_bytes()))

    def test_empty_bytes_are_an_execution_failure(self, probe: FfprobeMediaProbe) -> None:
        with pytest.raises(MediaProbeExecutionError):
            probe.probe(io.BytesIO(b""))

    def test_a_truncated_file_is_an_execution_failure_or_an_honest_result(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        """Either answer is correct; inventing a container would not be."""
        head = built[fixtures.MP3.name][:40]

        try:
            result = probe.probe(io.BytesIO(head))
        except MediaProbeExecutionError:
            return
        validate_media_probe_result(result)


class TestTheRealEngineIsShareable:
    def test_one_instance_probes_every_fixture_in_turn(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        observed = [probed(probe, built, fixture).container_names for fixture in fixtures.ALL]

        assert len(observed) == len(fixtures.ALL)
        assert all(names for names in observed)

    def test_the_same_file_probes_identically_twice(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        first = probed(probe, built, fixtures.MP4_WITH_AUDIO)
        second = probed(probe, built, fixtures.MP4_WITH_AUDIO)

        assert first == second

    def test_concurrent_probes_on_one_instance_do_not_interfere(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        """Real processes, real temporary files, one shared probe object."""
        import concurrent.futures

        wanted = [fixtures.WAV, fixtures.MP4, fixtures.OGG, fixtures.WEBM] * 3
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda fixture: probed(probe, built, fixture), wanted))

        for fixture, result in zip(wanted, results, strict=True):
            assert fixture.container in result.container_names

    def test_it_does_not_close_the_stream_it_was_given(
        self, probe: FfprobeMediaProbe, built: dict[str, bytes]
    ) -> None:
        handle = io.BytesIO(built[fixtures.WAV.name])

        probe.probe(handle)

        assert handle.closed is False


class TestTheStartupGateOnThisMachine:
    def test_the_real_ffprobe_reports_a_version(self) -> None:
        from unimem_media import engine_version

        assert engine_version()

    def test_the_real_ffprobe_supports_the_required_protocol(self) -> None:
        from unimem_media import INPUT_PROTOCOL, input_protocols

        assert INPUT_PROTOCOL in input_protocols()

    def test_a_missing_engine_refuses_to_build_a_probe(self, tmp_path: Path) -> None:
        from unimem_media import MediaPrerequisiteError

        with pytest.raises(MediaPrerequisiteError):
            build_ffprobe_media_probe(executable=str(tmp_path / "no-such-ffprobe"))

    def test_the_operator_report_names_this_machine_s_engine(self) -> None:
        from unimem_media import describe_prerequisites, engine_version

        assert engine_version() in describe_prerequisites()
