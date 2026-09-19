"""Local media probing: the optional adapter, outside ``core``.

This package is the concrete half of one port —
:mod:`core.processing.media_probe` — backed by a system ``ffprobe``. Nothing in
it is imported by a default deployment, and it is reached only by an application
explicitly composed with the capability enabled::

    python -m unimem_api --data-dir ./data --media

It depends on ``core`` and never the other way round. ``core`` does not import
it, does not know its name, and cannot be made to load it; the only things that
cross between them are the :class:`~core.processing.media_probe.MediaProbe`
protocol and the plain frozen values beside it.

**Importing this package costs nothing, and it adds no Python dependency at
all.** Every module here is standard library plus ``core``: there is no media
framework, no codec binding, no imaging package, no native wheel, and
deliberately no ``[media]`` extra to install. The whole prerequisite is a system
executable the machine's package manager provides — see the README — and this
project neither installs, downloads, nor vendors one.

**What this capability is, and what it is not.** It reads container structure:
format family, declared duration, and the audio and video streams a container
declares. It does not transcribe, decode a frame, extract an audio track, take a
keyframe or a thumbnail, read an embedded tag, choose a primary stream, convert
or transcode anything, or contact a service. ``ffmpeg`` is never invoked by
anything under ``src/``; ``ffprobe`` is, once per capture, with a fixed argument
list. See `ADR-023 <../../docs/ADR/ADR-023-local-ffprobe-media-capability.md>`_.

**It is independent of the two OCR capabilities**, and of everything else. It
shares no engine, no policy, no prerequisite and no failure type with them;
enabling it never enables or requires either, and every combination of the three
flags is a valid deployment when its own prerequisites exist.
"""

from typing import Final

from core.processing.media_probe import MediaProbe
from unimem_media.errors import MediaPrerequisiteError
from unimem_media.ffprobe import FfprobeMediaProbe, normalize
from unimem_media.policy import (
    DEFAULT_LIMITS,
    FFPROBE_EXECUTABLE,
    INPUT_PROTOCOL,
    INPUT_URL,
    LOG_LEVEL,
    PREREQUISITE_TIMEOUT_SECONDS,
    PRINT_FORMAT,
    PROTOCOL_WHITELIST,
    SHOW_ENTRIES,
    MediaProbeLimits,
)
from unimem_media.prerequisites import (
    CAPABILITY,
    describe_prerequisites,
    engine_version,
    input_protocols,
    require_engine,
)

#: The processor names a media-enabled deployment registers, restated here so the
#: composition layer can name them without importing a processor.
AUDIO_PROCESSOR_NAME: Final = "audio"
VIDEO_PROCESSOR_NAME: Final = "video"

__all__ = [
    "AUDIO_PROCESSOR_NAME",
    "CAPABILITY",
    "DEFAULT_LIMITS",
    "FFPROBE_EXECUTABLE",
    "INPUT_PROTOCOL",
    "INPUT_URL",
    "LOG_LEVEL",
    "PREREQUISITE_TIMEOUT_SECONDS",
    "PRINT_FORMAT",
    "PROTOCOL_WHITELIST",
    "SHOW_ENTRIES",
    "VIDEO_PROCESSOR_NAME",
    "FfprobeMediaProbe",
    "MediaPrerequisiteError",
    "MediaProbeLimits",
    "build_ffprobe_media_probe",
    "describe_prerequisites",
    "engine_version",
    "input_protocols",
    "normalize",
    "require_engine",
]


def build_ffprobe_media_probe(
    *,
    executable: str = FFPROBE_EXECUTABLE,
    limits: MediaProbeLimits = DEFAULT_LIMITS,
) -> MediaProbe:
    """Validate every prerequisite and return a probe, or refuse to build one.

    The whole startup gate:

    1. the engine must run and report a version;
    2. that build must be able to open the ``fd`` input protocol this adapter
       feeds it through.

    Any failure is a :class:`~unimem_media.errors.MediaPrerequisiteError` and
    nothing is returned. Nothing is installed, downloaded, or worked around, and
    the adapter is never quietly reconfigured to reach the file some other way.

    **The version the engine reported is deliberately discarded.** It exists for
    the message a failed gate prints and for
    :func:`~unimem_media.prerequisites.describe_prerequisites`; it is not passed
    to the probe and never reaches canonical metadata, because ADR-021 fixed that
    which build of which tool read a container does not change what the container
    says. That is the opposite of the OCR adapters, which *do* carry their engine
    version onto every content object they contribute to — and the difference is
    the point: an OCR engine's identity is part of what its output means, and a
    demuxer's is not.

    The returned probe is stateless and reentrant. One instance is built here and
    handed to both media processors, which is the arrangement
    :func:`~unimem_api.wiring.build_local_app` already expects.
    """
    require_engine(executable)
    return FfprobeMediaProbe(executable=executable, limits=limits)
