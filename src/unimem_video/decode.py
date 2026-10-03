"""One sequential bounded MP4 decode. PTS, never frame number/fps, is authoritative."""

import hashlib
import io
import math
from dataclasses import dataclass
from fractions import Fraction
from importlib.metadata import version
from time import monotonic
from typing import Any, BinaryIO

from core.contracts.base import JsonMapping
from unimem_video.policy import (
    DECODE_SECONDS,
    DECODER,
    MAX_AUDIO_FRAMES,
    MAX_BYTES,
    MAX_DECODED_PIXELS,
    MAX_DERIVED_BYTES,
    MAX_PACKETS,
    MAX_PIXELS,
    MAX_PNG_BYTES,
    MAX_SECONDS,
    MAX_VIDEO_FRAMES,
    SAMPLING,
    VideoError,
)


def inspect_video(stream: BinaryIO, declared: str) -> None:
    """Bounded signature gate; acceptance does not claim that decoding succeeded."""
    if declared not in ("", "application/octet-stream", "video/mp4"):
        raise VideoError("unsupported_mime")
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if not 0 < size <= MAX_BYTES:
        raise VideoError("input_size_limit")
    header = stream.read(64)
    stream.seek(0)
    if len(header) < 16 or header[4:8] != b"ftyp":
        raise VideoError("unsupported_container")
    box = int.from_bytes(header[:4], "big")
    if not 16 <= box <= 64 or box % 4 or len(header) < box:
        raise VideoError("unsupported_container")
    brands = [header[8:12], *[header[n : n + 4] for n in range(16, box, 4)]]
    if not set(brands) & {b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}:
        raise VideoError("unsupported_container")


def deny_external(*args: object, **kwargs: object) -> None:
    raise VideoError("external_input_refused")


def require_decoder() -> None:
    try:
        if version("av") != "16.1.0":
            raise VideoError("video_decoder_unavailable")
        __import__("av")
        __import__("PIL.Image")
    except (ImportError, OSError):
        raise VideoError("video_decoder_unavailable") from None


@dataclass
class Sample:
    png: bytes
    facts: JsonMapping


@dataclass
class DecodedVideo:
    frames: list[Sample]
    facts: JsonMapping
    audio: JsonMapping | None


def timestamp(frame: Any) -> Fraction:
    if frame.pts is None or frame.time_base is None or frame.time_base <= 0:
        raise VideoError("unsupported_timing")
    return Fraction(frame.pts) * Fraction(frame.time_base)


def dimensions(width: int, height: int) -> None:
    if min(width, height) < 1 or max(width, height) > 1920 or width * height > MAX_PIXELS:
        raise VideoError("pixel_limit")


def encode_frame(frame: Any, requested: float, origin: Fraction, track: int) -> Sample:
    # MVP refuses display transforms, anamorphic pixels and interlacing explicitly.
    # The encoded-to-output transformation is still recorded for every PNG.
    if frame.rotation != 0 or frame.interlaced_frame:
        raise VideoError("unsupported_orientation")
    scale = min(1.0, 1024 / max(frame.width, frame.height))
    width, height = max(1, int(frame.width * scale)), max(1, int(frame.height * scale))
    image = frame.reformat(width=width, height=height, format="rgb24").to_image()
    out = io.BytesIO()
    image.save(out, format="PNG")
    data = out.getvalue()
    if len(data) > MAX_PNG_BYTES:
        raise VideoError("derived_size_limit")
    return Sample(
        data,
        {
            "requested_seconds": requested,
            "presentation_seconds": float(timestamp(frame) - origin),
            "pts": frame.pts,
            "time_base": str(frame.time_base),
            "stream_index": track,
            "encoded_width": frame.width,
            "encoded_height": frame.height,
            "width": width,
            "height": height,
            "rotation_degrees": 0,
            "transform": "fit-1024-rgb24-bilinear/1",
            "cropped": False,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        },
    )


def decode(
    stream: BinaryIO, declared: str, pcm: BinaryIO, *, frames: bool, speech: bool
) -> DecodedVideo:
    """Retain decoder packet buffers and at most three PNGs, never the whole video.

    PCM is staged, mono s16le/16kHz, without deleting internal silence. Its first
    sample has audio.offset_seconds on the common movie timeline. The processor
    adds that offset to ASR cues. Discontinuities are refused, never concatenated.
    """
    inspect_video(stream, declared)
    require_decoder()
    import av

    began = monotonic()
    try:
        with av.open(
            stream,
            mode="r",
            format="mov",
            io_open=deny_external,
            options={
                "protocol_whitelist": "",
                "err_detect": "explode",
                "enable_drefs": "0",
                "use_absolute_path": "0",
            },
        ) as container:
            if (
                len(container.streams.video) != 1
                or len(container.streams.audio) > 1
                or len(container.streams) != 1 + len(container.streams.audio)
            ):
                raise VideoError("unsupported_structure")
            video = container.streams.video[0]
            audio = container.streams.audio[0] if container.streams.audio else None
            vc = video.codec_context
            if vc.name != "h264" or video.sample_aspect_ratio not in (None, Fraction(1)):
                raise VideoError("unsupported_codec")
            dimensions(vc.width, vc.height)
            encoded_size = (vc.width, vc.height)
            if audio and (
                audio.codec_context.name != "aac"
                or audio.codec_context.channels not in (1, 2)
                or not 8000 <= audio.codec_context.sample_rate <= 48000
            ):
                raise VideoError("unsupported_codec")
            audio_rate = audio.codec_context.sample_rate if audio else 0
            audio_channels = audio.codec_context.channels if audio else 0
            starts: dict[int, Fraction] = {}
            ends: dict[int, Fraction] = {}
            for track in container.streams:
                track.codec_context.thread_count = 1
                if (
                    track.start_time is None
                    or track.duration is None
                    or not track.time_base
                    or track.duration <= 0
                ):
                    raise VideoError("unsupported_timing")
                starts[track.index] = track.start_time * track.time_base
                ends[track.index] = starts[track.index] + track.duration * track.time_base
            origin = min(starts.values())
            duration = float(max(ends.values()) - origin)
            if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
                raise VideoError("duration_limit")
            if (
                container.duration is None
                or abs(container.duration / av.time_base - duration) > 0.1
            ):
                raise VideoError("inconsistent_timing")
            targets = [duration * f / 4 for f in (1, 2, 3)] if frames else []
            samples: list[Sample] = []
            last: dict[int, Fraction] = {}
            first: dict[int, Fraction] = {}
            actual_end: dict[int, Fraction] = {}
            counts = {video.index: 0}
            if audio:
                counts[audio.index] = 0
            pixels = pcm_samples = packet_count = 0
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

            def write_pcm(frame: Any) -> None:
                nonlocal pcm_samples
                for output in resampler.resample(frame):
                    pcm_samples += output.samples
                    if pcm_samples > MAX_SECONDS * 16000:
                        raise VideoError("duration_limit")
                    pcm.write(output.to_ndarray().astype("<i2").tobytes())

            for packet in container.demux():
                packet_count += 1
                if packet_count > MAX_PACKETS or monotonic() - began > DECODE_SECONDS:
                    raise VideoError("decode_budget_exceeded")
                if packet.is_corrupt or packet.stream.index not in counts:
                    raise VideoError("invalid_video")
                for decoded_frame in packet.decode():
                    frame: Any = decoded_frame  # streams were restricted to H.264/AAC above
                    idx = packet.stream.index
                    counts[idx] += 1
                    is_video = idx == video.index
                    if counts[idx] > (MAX_VIDEO_FRAMES if is_video else MAX_AUDIO_FRAMES):
                        raise VideoError("decode_budget_exceeded")
                    pts = timestamp(frame)
                    if frame.is_corrupt or (idx in last and pts <= last[idx]):
                        raise VideoError("unsupported_timing")
                    first.setdefault(idx, pts)
                    if abs(float(first[idx] - starts[idx])) > 0.002:
                        raise VideoError("inconsistent_timing")
                    if is_video:
                        dimensions(frame.width, frame.height)
                        if (
                            (frame.width, frame.height) != encoded_size
                            or frame.rotation != 0
                            or frame.interlaced_frame
                        ):
                            raise VideoError("unsupported_orientation")
                        pixels += frame.width * frame.height
                        if pixels > MAX_DECODED_PIXELS:
                            raise VideoError("decode_budget_exceeded")
                        if not frame.duration or frame.duration <= 0:
                            raise VideoError("unsupported_timing")
                        end = pts + frame.duration * frame.time_base
                        if idx in actual_end and abs(float(pts - actual_end[idx])) > 0.002:
                            raise VideoError("unsupported_timing")
                        if targets and float(pts - origin) >= targets[0]:
                            requested = targets.pop(0)
                            samples.append(encode_frame(frame, requested, origin, idx))
                            # A single held frame may cover more than one position: keep it once.
                            while targets and float(pts - origin) >= targets[0]:
                                targets.pop(0)
                    else:
                        assert audio is not None
                        if (
                            frame.sample_rate != audio_rate
                            or frame.layout.nb_channels != audio_channels
                        ):
                            raise VideoError("unsupported_structure")
                        end = pts + Fraction(frame.samples, frame.sample_rate)
                        if idx in actual_end and abs(pts - actual_end[idx]) > Fraction(
                            2, frame.sample_rate
                        ):
                            raise VideoError("unsupported_timing")
                        if speech:
                            # Continuity checked above; offset retained.
                            frame.pts = None
                            write_pcm(frame)
                    if float(end - origin) > MAX_SECONDS + 0.025 or end > ends[idx] + Fraction(
                        1, 20
                    ):
                        raise VideoError("duration_limit")
                    last[idx], actual_end[idx] = pts, end
            if speech and audio:
                write_pcm(None)
            if any(not count for count in counts.values()):
                raise VideoError("invalid_video")
            if any(abs(float(actual_end[i] - ends[i])) > 0.05 for i in ends):
                raise VideoError("inconsistent_timing")
            if frames and not samples:
                raise VideoError("unsupported_timing")
            if sum(len(s.png) for s in samples) > MAX_DERIVED_BYTES:
                raise VideoError("derived_size_limit")
            return DecodedVideo(
                samples,
                {
                    "decoder": DECODER,
                    "sampling": SAMPLING,
                    "container": "mp4",
                    "codec": "h264",
                    "origin_seconds": float(origin),
                    "origin_rational": str(origin),
                    "duration_seconds": duration,
                    "video_stream_index": video.index,
                    "video_start_seconds": float(first[video.index] - origin),
                    "packets": packet_count,
                    "video_frames": counts[video.index],
                    "decoded_pixels": pixels,
                    "av_version": av.__version__,
                    "ffmpeg_versions": {
                        k: ".".join(map(str, v)) for k, v in av.library_versions.items()
                    },
                    "decode_seconds": monotonic() - began,
                },
                {
                    "stream_index": audio.index,
                    "codec": "aac",
                    "sample_rate": audio_rate,
                    "channels": audio_channels,
                    "offset_seconds": float(first[audio.index] - origin),
                    "start_pts": int(first[audio.index] / Fraction(str(audio.time_base))),
                    "time_base": str(audio.time_base),
                    "pcm_samples": pcm_samples,
                    "pcm_rate": 16000,
                    "decoded_frames": counts[audio.index],
                }
                if audio
                else None,
            )
    except (av.error.FFmpegError, OSError, ValueError, OverflowError):
        raise VideoError("invalid_video") from None
