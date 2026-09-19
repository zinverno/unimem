"""The concrete media probe: system ffprobe, normalized into ``core``'s values.

This module is the half of :mod:`core.processing.media_probe` that knows an
engine exists. It implements :class:`~core.processing.media_probe.MediaProbe`
over a local ``ffprobe``, and its whole job is to turn one engine's
representation into the small frozen values ``core`` defined, or to fail
honestly.

**Normalization happens here, and only here.** ffprobe writes a container family
as ``"mov,mp4,m4a,3gp,3g2,mj2"``, a sample rate as the string ``"44100"``, an
absent value as ``"N/A"``, and an unknown frame rate as ``"0/0"``. Every one of
those is engine vocabulary, and none of it may reach a stored content object: a
canonical object recording ``codec: "n/a"`` claims an observation nobody made.
So aliases are split, lowercased, de-duplicated and sorted; numbers are parsed
and checked; placeholders become omission; and ``"0/0"`` becomes ``None``.

**Nothing is manufactured.** A value the container did not declare is ``None``,
never ``0``, never ``""``, and never a guess. A duration is read from the
container and never inferred from a stream. No stream is chosen as "primary", no
tag is read, no frame is decoded, and no engine identity is recorded — ADR-021
fixed that which build of which tool read a container does not change what the
container says.

**Every failure is one failure.** Whatever goes wrong — a missing executable, a
timeout, a nonzero exit, invalid UTF-8, malformed JSON, a shape this module does
not recognize, a required field that is not there, a value that cannot be
normalized, output past the budget — leaves as
:class:`~core.processing.media_probe.MediaProbeExecutionError`, which means *no
trusted structural result*, and never as
:class:`~core.processing.errors.ProcessingInputError`, which would be a verdict
about somebody's file that this adapter has no standing to reach. ffprobe's
standard error is never read, so its prose can never become that verdict either.

**The result is not validated here.**
:func:`~core.processing.media_probe.validate_media_probe_result` is the port
contract's authority and it runs in ``core``, where the media processors call
it. Re-running it here would be a second normalizer competing with this one and
would hide exactly the inconsistency it exists to catch.

**One instance is reentrant and is meant to be shared.** The audio and the video
processor are handed the same probe deliberately. This class holds two frozen
values and no mutable state: every call to :meth:`FfprobeMediaProbe.probe`
creates its own temporary files and its own child process, so there is no shared
buffer, no shared temporary name, no global process state, and no lock.
"""

import json
import math
from typing import Any, BinaryIO, Final

from core.processing.media_probe import (
    AudioStreamInfo,
    MediaProbeExecutionError,
    MediaProbeResult,
    VideoStreamInfo,
)
from unimem_media.engine import EngineInvocationError, FfprobeInvocation, run_ffprobe
from unimem_media.policy import (
    AUDIO_CODEC_TYPE,
    DEFAULT_LIMITS,
    FFPROBE_EXECUTABLE,
    PLACEHOLDER_VALUES,
    UNKNOWN_FRAME_RATE,
    VIDEO_CODEC_TYPE,
    MediaProbeLimits,
)

#: The top-level JSON keys this adapter reads. Everything else ffprobe prints —
#: ``programs``, for instance — is ignored rather than rejected: the query asked
#: for two things and a build that volunteers a third has not answered wrongly.
_FORMAT_KEY: Final = "format"
_STREAMS_KEY: Final = "streams"


def _fail(message: str) -> MediaProbeExecutionError:
    return MediaProbeExecutionError(f"the ffprobe media probe {message}")


def _text(value: object) -> str | None:
    """Engine text, normalized, or ``None`` when the engine had nothing to say.

    Trimmed and lowercased because ``core`` requires exactly that, and mapped to
    ``None`` when what came back is one of ffprobe's ways of writing "I do not
    know". A non-string is not text and is reported as nothing rather than
    coerced, so a caller that requires the value raises a specific failure.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or normalized in PLACEHOLDER_VALUES:
        return None
    return normalized


def _declared_text(value: object, *, label: str, index: int) -> str | None:
    """Optional engine text that, when present, must genuinely be text.

    The difference from :func:`_text` is what a wrong *type* means. That function
    is used where a non-string simply is not the thing being looked for — a
    ``codec_type`` that is a number routes nowhere, and ignoring the stream is
    the documented behaviour for any type this build does not describe. Here the
    field is one this adapter reports, so a present non-string is a malformed
    answer rather than an absent value: reporting it as omission would
    manufacture a missing declaration out of a broken one, which is the one thing
    normalization must never do.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(
            f"reported {type(value).__name__} as the {label} of stream {index}, "
            f"where text or nothing was expected"
        )
    return _text(value)


def _positive_int(value: object, *, label: str, index: int) -> int | None:
    """A declared positive integer, or ``None`` when nothing was declared.

    ffprobe writes some of these as JSON strings (``"44100"``) and others as JSON
    numbers (``48``), so both are accepted and normalized to ``int``. ``bool`` is
    refused explicitly: it is a subclass of ``int`` in Python, so accepting it
    would let ``channels: true`` reach durable metadata.

    A value that is present but cannot be read as a positive integer is a
    **failure**, not an omission. Turning ``"0"`` or ``"abc"`` into ``None``
    would manufacture a missing declaration out of a broken one, and ``core``'s
    own validator reaches the same conclusion for the same reason.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = _text(value)
        if text is None:
            return None
        try:
            value = int(text)
        except ValueError:
            raise _fail(
                f"reported {text!r} as the {label} of stream {index}, which is not an integer"
            ) from None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(
            f"reported {type(value).__name__} as the {label} of stream {index}, "
            f"where a positive integer or nothing was expected"
        )
    if value <= 0:
        raise _fail(f"reported a {label} of {value} for stream {index}")
    return value


def _frame_rate(value: object, *, index: int) -> str | None:
    """An exact positive rational, or ``None`` when the rate is unknown.

    Kept as a rational string rather than a float on purpose: ``30000/1001`` is
    the fact the container carries and ``29.97`` is a lossy rendering of it that
    no later stage could undo.

    Reduced to lowest terms, which is exact — ``60/2`` and ``30/1`` are the same
    rational, and ``core`` requires the canonical spelling so that two probes of
    one file cannot produce two unequal stored objects. Reduction is the one
    arithmetic this function performs and it changes no value.

    ``"0/0"`` is ffprobe's "no average frame rate", and a zero numerator means
    the same thing however it is spelled, so both arrive as ``None``. A zero
    *denominator* with a nonzero numerator is not a rational at all, and anything
    that is not two non-negative integers separated by a slash is not a frame
    rate: both are failures rather than silent omissions.
    """
    text = _declared_text(value, label="frame rate", index=index)
    if text is None or text == UNKNOWN_FRAME_RATE:
        return None
    numerator_text, separator, denominator_text = text.partition("/")
    if not separator or not numerator_text.isdigit() or not denominator_text.isdigit():
        raise _fail(
            f"reported {text!r} as the frame rate of stream {index}, which is not a rational"
        )
    numerator, denominator = int(numerator_text), int(denominator_text)
    if numerator == 0:
        # A rate of zero is not a rate. ffprobe spells the unknown case "0/0",
        # and a build that spells it "0/1" means the same thing.
        return None
    if denominator == 0:
        raise _fail(
            f"reported {text!r} as the frame rate of stream {index}, which has a zero denominator"
        )
    common = math.gcd(numerator, denominator)
    return f"{numerator // common}/{denominator // common}"


def _index(stream: dict[str, Any]) -> int:
    """The container's own number for this stream. Required, never invented."""
    value = stream.get("index")
    if isinstance(value, str):
        text = _text(value)
        if text is not None and text.isdigit():
            value = int(text)
    if value is None:
        raise _fail("reported a stream with no index")
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(
            f"reported {type(value).__name__} as a stream index, where an integer was expected"
        )
    if value < 0:
        raise _fail(f"reported a stream index of {value}")
    return value


def _container_names(container: dict[str, Any]) -> tuple[str, ...]:
    """The container family, as the normalized tuple the port requires.

    ffprobe reports a family as one comma-separated string — an ISO base media
    file is ``"mov,mp4,m4a,3gp,3g2,mj2"`` — and that is a genuine observation
    rather than indecision, so every alias is kept. They are lowercased,
    de-duplicated and sorted because two probes of one file must produce the same
    value and because ``"MP4"`` and ``"mp4"`` must not be two answers.

    Missing or unusable container information is an **execution failure**: a
    probe that recognized no container recognized nothing, and reporting that as
    an empty tuple would ask ``core`` to reach a verdict about bytes nothing
    managed to read.
    """
    raw = container.get("format_name")
    if raw is None:
        raise _fail("reported no container name at all")
    if not isinstance(raw, str):
        raise _fail(f"reported {type(raw).__name__} where a container name was expected")
    names = {alias for part in raw.split(",") if (alias := _text(part)) is not None}
    if not names:
        raise _fail(f"reported {raw!r} as the container, which names nothing usable")
    return tuple(sorted(names))


def _duration(container: dict[str, Any]) -> float | None:
    """The container's declared duration in seconds, or ``None``.

    Format level only. ADR-021 fixes the container's declaration as the duration,
    and a live-recorded stream or a truncated upload genuinely declares none —
    which is a fact rather than a failure. Summing or maximizing stream durations
    would be this adapter concluding something the container did not say.

    A declaration that is present and not a finite non-negative number is a
    failure: ``nan`` and ``inf`` serialize nowhere, and a negative duration is
    not a duration.
    """
    value = container.get("duration")
    if value is None:
        return None
    if isinstance(value, str):
        text = _text(value)
        if text is None:
            return None
        try:
            value = float(text)
        except ValueError:
            raise _fail(f"reported {text!r} as the duration, which is not a number") from None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(
            f"reported {type(value).__name__} as the duration, where a number or nothing "
            f"was expected"
        )
    duration = float(value)
    if not math.isfinite(duration):
        raise _fail(f"reported a duration of {duration}")
    if duration < 0:
        raise _fail(f"reported a duration of {duration} seconds")
    return duration


def _audio_stream(stream: dict[str, Any]) -> AudioStreamInfo:
    index = _index(stream)
    return AudioStreamInfo(
        index=index,
        codec=_declared_text(stream.get("codec_name"), label="codec", index=index),
        sample_rate=_positive_int(stream.get("sample_rate"), label="sample rate", index=index),
        channels=_positive_int(stream.get("channels"), label="channel count", index=index),
    )


def _video_stream(stream: dict[str, Any]) -> VideoStreamInfo:
    index = _index(stream)
    return VideoStreamInfo(
        index=index,
        codec=_declared_text(stream.get("codec_name"), label="codec", index=index),
        width=_positive_int(stream.get("width"), label="width", index=index),
        height=_positive_int(stream.get("height"), label="height", index=index),
        frame_rate=_frame_rate(stream.get("avg_frame_rate"), index=index),
    )


def _codec_type(stream: dict[str, Any], *, position: int) -> str:
    """What kind of stream this is, as normalized text, or an execution failure.

    **The distinction this function exists to hold is between a stream this
    build does not model and an answer it cannot trust**, and collapsing them is
    unsafe in one specific direction.

    A container carrying a subtitle, data or attachment track is perfectly
    ordinary. :class:`~core.processing.media_probe.MediaProbeResult` has nowhere
    to put one and inventing somewhere would be vocabulary ahead of a decision,
    so such a stream is *ignored* — and so is any other genuinely-named kind a
    future container brings. That is a normal observation about the media.

    A ``codec_type`` that is **missing, not a string, blank, or one of the
    engine's placeholders** is none of those things. It is an answer about the
    structure that cannot be believed, and treating it as "some kind we do not
    model" would quietly delete a stream from the result. That matters because
    of where the deletion would land:

        malformed stream -> silently dropped -> a result with no audio stream
        -> core's ``ProcessingInputError`` -> 422 -> capture durably FAILED

    which turns infrastructure corruption into a verdict about somebody's file —
    exactly the boundary Phase 5A exists to hold. So it is a
    :class:`~core.processing.media_probe.MediaProbeExecutionError` instead, and
    the capture stays ``PROCESSING`` behind a 503 with nothing recorded.

    ``position`` is the stream's place in the printed list, not a container
    index: the index is a field of the entry and this function is deliberately
    reached before anything in the entry has been trusted.
    """
    value = stream.get("codec_type")
    if value is None:
        raise _fail(f"reported the stream at position {position} with no codec type")
    if not isinstance(value, str):
        raise _fail(
            f"reported {type(value).__name__} as the codec type of the stream at position "
            f"{position}, where text was expected"
        )
    normalized = _text(value)
    if normalized is None:
        raise _fail(
            f"reported {value!r} as the codec type of the stream at position {position}, "
            f"which names no kind of stream"
        )
    return normalized


def _streams(
    payload: dict[str, Any],
) -> tuple[tuple[AudioStreamInfo, ...], tuple[VideoStreamInfo, ...]]:
    """Both stream collections, ordered by the container's own stream index.

    Only ``audio`` and ``video`` are described. A subtitle, data or attachment
    stream — or any other genuinely-named kind this build does not model — is
    ignored rather than refused: a container carrying one is perfectly ordinary,
    :class:`~core.processing.media_probe.MediaProbeResult` has nowhere to put it,
    and inventing somewhere would be vocabulary ahead of a decision. Their
    indexes are simply absent from both tuples, which is why a gap in the
    numbering is normal.

    **A stream whose ``codec_type`` is missing or malformed is not one of
    those.** It is refused, for the reason :func:`_codec_type` sets out: silently
    dropping an untrustworthy stream could turn a broken engine answer into a
    durable verdict about the submitted file.

    Sorted by index because the order is part of the value: a probe that returned
    streams in the order a hash map happened to iterate would make two probes of
    one file produce two different stored objects.

    A missing ``streams`` key is an empty container rather than a malformed
    answer. Whether a file with no audio or no video may be ingested is canonical
    policy, which ``core`` applies to this result; answering "no streams" with an
    execution failure would take that verdict away from it.
    """
    listed = payload.get(_STREAMS_KEY, [])
    if not isinstance(listed, list):
        raise _fail(f"reported {type(listed).__name__} where a list of streams was expected")
    audio: list[AudioStreamInfo] = []
    video: list[VideoStreamInfo] = []
    for position, entry in enumerate(listed):
        if not isinstance(entry, dict):
            raise _fail(f"reported {type(entry).__name__} where a stream was expected")
        codec_type = _codec_type(entry, position=position)
        if codec_type == AUDIO_CODEC_TYPE:
            audio.append(_audio_stream(entry))
        elif codec_type == VIDEO_CODEC_TYPE:
            video.append(_video_stream(entry))
    return (
        tuple(sorted(audio, key=lambda item: item.index)),
        tuple(sorted(video, key=lambda item: item.index)),
    )


def normalize(printed: str) -> MediaProbeResult:
    """Turn one ffprobe JSON document into the port's frozen result, or fail.

    A module-level function rather than a method so the whole normalization can
    be exercised over recorded engine output without a subprocess — which is what
    makes "this is what a real MP4 becomes" a test rather than a claim.
    """
    try:
        payload = json.loads(printed)
    except json.JSONDecodeError as exc:
        raise _fail("printed output that is not JSON") from exc
    if not isinstance(payload, dict):
        raise _fail(f"printed a JSON {type(payload).__name__} where an object was expected")
    container = payload.get(_FORMAT_KEY)
    if container is None:
        raise _fail("printed no container information at all")
    if not isinstance(container, dict):
        raise _fail(f"printed {type(container).__name__} where an object was expected")
    audio, video = _streams(payload)
    return MediaProbeResult(
        container_names=_container_names(container),
        duration_seconds=_duration(container),
        audio_streams=audio,
        video_streams=video,
    )


class FfprobeMediaProbe:
    """Reads container structure with a local ffprobe. Stateless and reentrant.

    Satisfies :class:`~core.processing.media_probe.MediaProbe` structurally; the
    protocol is not inherited from, because ``core`` defines a
    :class:`~typing.Protocol` precisely so an adapter need not import a base
    class to be one.

    **Its two attributes are frozen values and it has no others.** There is no
    per-run field, no cached handle, no reusable buffer, no shared temporary
    name, no module-level state, and no lock — every call to :meth:`probe` opens
    its own temporary files and starts its own child process. That is what makes
    the single instance the composition root hands to *both*
    :class:`~core.processing.media.AudioProcessor` and
    :class:`~core.processing.media.VideoProcessor` correct rather than merely
    convenient, and it is what makes two concurrent calls on one instance safe.
    """

    def __init__(
        self,
        *,
        executable: str = FFPROBE_EXECUTABLE,
        limits: MediaProbeLimits = DEFAULT_LIMITS,
    ) -> None:
        self._invocation = FfprobeInvocation(executable=executable, limits=limits)

    @property
    def executable(self) -> str:
        """Which ffprobe this probe runs. Read-only, and for diagnostics only."""
        return self._invocation.executable

    @property
    def limits(self) -> MediaProbeLimits:
        """The bounds one invocation runs inside. Read-only."""
        return self._invocation.limits

    def probe(self, stream: BinaryIO) -> MediaProbeResult:
        """Report the structural facts ffprobe observed in the stream's bytes.

        The stream is read forward and is **not** closed: the port says the
        caller owns the handle.

        Every failure — including one that happened before a single byte was read
        — becomes :class:`~core.processing.media_probe.MediaProbeExecutionError`.
        Nothing here raises
        :class:`~core.processing.errors.ProcessingInputError`, inspects ffprobe's
        standard error, or otherwise reaches a verdict about the submitted media:
        this method answers *what was observed*, and what that means is ``core``'s
        question.
        """
        try:
            printed = run_ffprobe(stream, self._invocation)
        except EngineInvocationError as exc:
            raise MediaProbeExecutionError(str(exc)) from exc
        return normalize(printed)
