"""Synthetic codec/timeline checks; no model and no private media."""

import io
from fractions import Fraction

import pytest

from unimem_video.decode import decode, inspect_video
from unimem_video.policy import VideoError


def movie(
    *,
    audio: bool = True,
    start: int = 0,
    offset: int = 1,
    vfr: bool = False,
    codec: str = "libx264",
    width: int = 160,
    height: int = 96,
    audio_gap: bool = False,
) -> bytes:
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    data = io.BytesIO()
    with av.open(data, "w", format="mp4") as out:
        video = out.add_stream(codec, rate=10)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        video.time_base = Fraction(1, 10)
        video.codec_context.time_base = Fraction(1, 10)
        video.codec_context.max_b_frames = 0
        track = out.add_stream("aac", rate=16000) if audio else None
        if track:
            track.layout = "mono"
        for i in range(40):
            if vfr and i % 3 == 1:
                continue
            image = np.zeros((height, width, 3), dtype=np.uint8)
            image[:, :, min(2, i // 14)] = 220
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = start * 10 + i, Fraction(1, 10)
            out.mux(video.encode(frame))
        out.mux(video.encode(None))
        if track:
            for i in range(0, (4 - offset) * 16000, 1024):
                samples = min(1024, (4 - offset) * 16000 - i)
                af = av.AudioFrame.from_ndarray(
                    np.zeros((1, samples), dtype=np.int16), format="s16", layout="mono"
                )
                af.sample_rate = 16000
                af.pts, af.time_base = (start + offset) * 16000 + i, Fraction(1, 16000)
                if audio_gap and i >= 16384:
                    af.pts += 1600
                out.mux(track.encode(af))
            out.mux(track.encode(None))
    return data.getvalue()


@pytest.mark.parametrize("vfr", [False, True])
@pytest.mark.parametrize("start", [0, 5])
def test_common_timeline_real_pts_and_delayed_audio(start: int, vfr: bool) -> None:
    pcm = io.BytesIO()
    result = decode(
        io.BytesIO(movie(start=start, vfr=vfr)), "video/mp4", pcm, frames=True, speech=True
    )
    assert len(result.frames) == 3
    assert result.facts["origin_seconds"] == start
    assert result.audio is not None
    # AAC priming may start one codec frame before the requested offset; this
    # actual decoded start is retained, rather than silently rounded to 1 second.
    assert 0.9 <= result.audio["offset_seconds"] <= 1  # type: ignore[operator]
    assert len(pcm.getvalue()) == result.audio["pcm_samples"] * 2  # type: ignore[operator]
    for sample, target in zip(result.frames, (1, 2, 3), strict=True):
        facts = sample.facts
        assert facts["requested_seconds"] == target
        assert target <= facts["presentation_seconds"] <= target + 0.2  # type: ignore[operator]
        assert (
            float(int(str(facts["pts"])) * Fraction(str(facts["time_base"])) - start)
            == facts["presentation_seconds"]
        )
        assert sample.png.startswith(b"\x89PNG")


def test_no_audio_and_disabled_modes() -> None:
    pcm = io.BytesIO()
    result = decode(io.BytesIO(movie(audio=False)), "", pcm, frames=True, speech=True)
    assert result.audio is None
    assert len(result.frames) == 3
    assert not pcm.getvalue()
    result = decode(
        io.BytesIO(movie()), "application/octet-stream", pcm, frames=False, speech=False
    )
    assert result.frames == []
    assert result.audio is not None
    assert not pcm.getvalue()


def test_input_refusals() -> None:
    with pytest.raises(VideoError, match="unsupported_mime"):
        inspect_video(io.BytesIO(movie()), "video/webm")
    with pytest.raises(VideoError, match="unsupported_container"):
        inspect_video(io.BytesIO(b"#EXTM3U\nhttps://example.com"), "video/mp4")
    data = movie()
    with pytest.raises(VideoError, match="invalid_video"):
        decode(io.BytesIO(data[:100]), "video/mp4", io.BytesIO(), frames=True, speech=True)
    with pytest.raises(VideoError, match="unsupported_codec"):
        decode(io.BytesIO(movie(codec="mpeg4")), "", io.BytesIO(), frames=True, speech=True)
    with pytest.raises(VideoError, match="pixel_limit"):
        decode(io.BytesIO(movie(width=1936)), "", io.BytesIO(), frames=True, speech=False)


@pytest.mark.parametrize(
    "limit",
    [
        "MAX_BYTES",
        "MAX_PACKETS",
        "MAX_VIDEO_FRAMES",
        "MAX_AUDIO_FRAMES",
        "MAX_DECODED_PIXELS",
        "MAX_PNG_BYTES",
        "MAX_DERIVED_BYTES",
        "MAX_SECONDS",
    ],
)
def test_each_actual_work_bound(monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    source = movie()
    monkeypatch.setattr("unimem_video.decode." + limit, 1)
    with pytest.raises(VideoError):
        decode(io.BytesIO(source), "", io.BytesIO(), frames=True, speech=True)


def test_external_open_is_always_refused() -> None:
    from unimem_video.decode import deny_external

    for locator in ("https://example.org/a", "file:/etc/passwd", "concat:a|b", "playlist.m3u8"):
        with pytest.raises(VideoError, match="external_input_refused"):
            deny_external(locator, 0, {})


def test_audio_discontinuity_is_refused_instead_of_compressing_pause() -> None:
    with pytest.raises(VideoError, match="unsupported_timing"):
        decode(io.BytesIO(movie(audio_gap=True)), "", io.BytesIO(), frames=True, speech=True)


def test_missing_frame_timing_is_not_replaced_by_fps() -> None:
    from types import SimpleNamespace

    from unimem_video.decode import timestamp

    for pts, time_base in ((None, Fraction(1, 30)), (3, None), (3, Fraction(0))):
        with pytest.raises(VideoError, match="unsupported_timing"):
            timestamp(SimpleNamespace(pts=pts, time_base=time_base))


def test_decoder_context_updates_cannot_hide_a_resolution_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    av = pytest.importorskip("av")
    original = movie(audio=False)
    codec = SimpleNamespace(name="h264", width=160, height=96, thread_count=0)
    track = SimpleNamespace(
        index=0,
        codec_context=codec,
        sample_aspect_ratio=Fraction(1),
        start_time=0,
        duration=40,
        time_base=Fraction(1, 10),
    )

    class Streams(list[object]):
        def __init__(self) -> None:
            super().__init__([track])
            self.video = [track]
            self.audio: list[object] = []

    class Container:
        streams = Streams()
        duration = 4 * av.time_base

        def __enter__(self) -> "Container":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def demux(self) -> list[object]:
            # H.264 decoding can update codec_context.width when an SPS changes.
            codec.width = 320
            frame = SimpleNamespace(
                pts=0,
                time_base=Fraction(1, 10),
                is_corrupt=False,
                width=320,
                height=96,
                rotation=0,
                interlaced_frame=False,
            )
            return [SimpleNamespace(stream=track, is_corrupt=False, decode=lambda: [frame])]

    monkeypatch.setattr(av, "open", lambda *args, **kwargs: Container())
    with pytest.raises(VideoError, match="unsupported_orientation"):
        decode(io.BytesIO(original), "", io.BytesIO(), frames=True, speech=False)
