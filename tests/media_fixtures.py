"""Small, real media files, built on demand and never committed.

No binary fixture is stored in this repository. Every file these suites probe is
produced here by the system ``ffmpeg`` from a synthetic source — a tone, a test
pattern — so a reader can see exactly what each one contains and a reviewer
never has to trust an opaque blob.

**ffmpeg is a test dependency and nothing more.** Production code under ``src/``
never invokes it: the adapter runs ``ffprobe`` and only ``ffprobe``. What
reaches the code under test is the bytes, handed over exactly as a submitted
upload would be.

Each builder is deterministic in what it *describes* — container, codec,
dimensions, frame rate, channel count — and the tests assert on those rather
than on byte-level identity, because an encoder's exact output varies with its
build.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: The encoder, and the duration every fixture runs for. One second is long
#: enough for a container to declare a duration and short enough that a whole
#: family of fixtures builds in a moment.
FFMPEG: Final = "ffmpeg"
DURATION_SECONDS: Final = 1.0

#: How long one fixture build may take.
BUILD_TIMEOUT: Final = 120.0

#: The synthetic sources. A sine tone for audio, a test pattern for video.
TONE: Final = f"sine=frequency=440:duration={DURATION_SECONDS}"
PATTERN: Final = f"testsrc=size=64x48:rate=25:duration={DURATION_SECONDS}"

#: What the video fixtures declare, restated here so a test can assert on
#: numbers written down rather than on numbers read back from the same file.
VIDEO_WIDTH: Final = 64
VIDEO_HEIGHT: Final = 48
VIDEO_FRAME_RATE: Final = "25/1"

#: The sample rate ffmpeg's `sine` source produces by default.
AUDIO_SAMPLE_RATE: Final = 44100
AUDIO_CHANNELS: Final = 1


@dataclass(frozen=True, slots=True)
class MediaFixture:
    """One built file, and what it was built to contain.

    ``container`` is the alias ``core.processing.media.MEDIA_CONTAINERS`` requires
    for ``mime_type``, so a test can assert the probe observed the family this
    build routes on without restating the mapping.
    """

    name: str
    mime_type: str
    container: str
    has_audio: bool
    has_video: bool


#: Every family this build supports, one fixture each.
WAV: Final = MediaFixture("tone.wav", "audio/wav", "wav", True, False)
MP3: Final = MediaFixture("tone.mp3", "audio/mpeg", "mp3", True, False)
OGG: Final = MediaFixture("tone.ogg", "audio/ogg", "ogg", True, False)
MP4: Final = MediaFixture("silent.mp4", "video/mp4", "mp4", False, True)
WEBM: Final = MediaFixture("silent.webm", "video/webm", "webm", False, True)

#: A video that also carries sound, which the audio-and-video path needs.
MP4_WITH_AUDIO: Final = MediaFixture("sound.mp4", "video/mp4", "mp4", True, True)

#: A video carrying a subtitle track, which must be ignored rather than refused.
WEBM_WITH_SUBTITLES: Final = MediaFixture("subtitled.webm", "video/webm", "webm", False, True)

#: Every fixture, for the suites that sweep all of them.
ALL: Final[tuple[MediaFixture, ...]] = (WAV, MP3, OGG, MP4, WEBM, MP4_WITH_AUDIO)

#: The encoder arguments for each fixture, after the shared prefix.
_RECIPES: Final[dict[str, tuple[str, ...]]] = {
    WAV.name: ("-f", "lavfi", "-i", TONE, "-c:a", "pcm_s16le"),
    MP3.name: ("-f", "lavfi", "-i", TONE, "-c:a", "libmp3lame"),
    OGG.name: ("-f", "lavfi", "-i", TONE, "-c:a", "libvorbis"),
    MP4.name: ("-f", "lavfi", "-i", PATTERN, "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    WEBM.name: ("-f", "lavfi", "-i", PATTERN, "-c:v", "libvpx", "-pix_fmt", "yuv420p"),
    MP4_WITH_AUDIO.name: (
        "-f",
        "lavfi",
        "-i",
        PATTERN,
        "-f",
        "lavfi",
        "-i",
        TONE,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
    ),
}


def build(fixture: MediaFixture, directory: Path) -> bytes:
    """Encode one fixture into ``directory`` and return its bytes.

    Raises ``RuntimeError`` if the encoder fails, which is a broken test machine
    rather than a missing prerequisite — the prerequisite is checked once, up
    front, by :func:`tests.media_support.require_encoder`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / fixture.name
    command = [FFMPEG, "-loglevel", "error", "-y", *_RECIPES[fixture.name], str(destination)]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=BUILD_TIMEOUT, check=False
    )
    if completed.returncode != 0:  # pragma: no cover - only on a broken host
        raise RuntimeError(f"could not build {fixture.name}: {completed.stderr.strip()}")
    return destination.read_bytes()


def build_subtitled_webm(directory: Path) -> bytes:
    """A WebM carrying video and a WebVTT subtitle track.

    Its own function rather than another recipe, because it needs a second input
    file written first. What it is for is the one assertion nothing else can
    make: a subtitle stream is *ignored*, not refused, and does not become a
    video or audio stream.
    """
    directory.mkdir(parents=True, exist_ok=True)
    subtitles = directory / "captions.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\ncaption\n\n")
    destination = directory / WEBM_WITH_SUBTITLES.name
    completed = subprocess.run(
        [
            FFMPEG,
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            PATTERN,
            "-i",
            str(subtitles),
            "-c:v",
            "libvpx",
            "-c:s",
            "webvtt",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - only on a broken host
        raise RuntimeError(f"could not build a subtitled WebM: {completed.stderr.strip()}")
    return destination.read_bytes()


def unreadable_bytes() -> bytes:
    """Bytes no demuxer can make sense of, built without an encoder.

    Deliberately *not* random: a fixed pattern means a failing run is the same
    failing run twice. It is long enough that ffprobe genuinely reads it and
    short enough to be irrelevant to anything else.
    """
    return bytes(range(256)) * 8
