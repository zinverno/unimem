"""The time-based media probe port.

One narrow boundary, for one job: hand a staged audio or video stream to
something that can read its container structure, and get back the structural
facts it observed. It is a **sibling** of :mod:`core.processing.ocr` and
:mod:`core.processing.image_recognition`, and deliberately not a generalization
of either — see `ADR-021
<../../docs/ADR/ADR-021-original-first-time-based-media-ingestion.md>`_. Those
two ports answer "what words are in this?"; this one answers "what is this made
of?", and it never reads a frame, decodes a sample, or transcribes anything.

**Why this exists at all.** ``core`` may not import a media framework, a codec
library, or ``subprocess``, and the canonical policy that decides what a probe
result *means* — which declared MIME types route where, whether an ``AUDIO``
capture really carries an audio stream, what lands in durable metadata — must
stay testable on a machine with no media tooling installed at all. So the policy
lives in ``core``, the machinery lives in an adapter outside it, and this module
is the typed seam between them.

**What crosses the seam is values, not objects, and almost nothing goes in.**
The input is a binary stream and nothing else. No MIME type, no capture
modality, no capture id, no filename, no
:class:`~core.contracts.capture.RawObjectRef`, no filesystem path, and no
execution detail: an implementation is asked *"what structural facts were
observed in these bytes?"* and is given no way to answer a different question,
or to be told what answer is expected. A path in particular is a filesystem
instruction and this port must not be able to express one. What comes back is a
small frozen dataclass of plain strings, integers, and floats — never raw
``ffprobe`` JSON, never an engine object, never a temporary path.

The asymmetry with :class:`~core.processing.image_recognition.ImageOcr`, which
*does* take the declared MIME type and the validated dimensions, is deliberate.
That port takes them so a recognizer can refuse an oversized image before
decoding it. This one has nothing to pre-authorize: probing reads container
headers, and telling the adapter what the submitter declared would invite it to
agree. ADR-021 fixes that the declared type *routes* and the probed container
*verifies*, which only works if the two are observed independently.

**The result is validated before it is believed.** An adapter is
infrastructure, and infrastructure can be wrong in ways that would otherwise
become a permanent lie on a stored content object: a container name that is
still ``"N/A"``, a duration of ``nan``, two streams claiming index ``3``, a
``frame_rate`` of ``"0/0"``. :func:`validate_media_probe_result` rejects all of
those as :class:`MediaProbeExecutionError`, which is an *execution* failure and
never a verdict about the submitted media.
"""

import math
import re
from dataclasses import dataclass
from typing import BinaryIO, Final, Protocol

#: Strings an engine emits in place of a value it does not have. They are that
#: engine's way of writing ``None`` and must never survive normalization: a
#: content object recording ``codec: "n/a"`` claims an observation nobody made,
#: and omission already says the same thing honestly.
PLACEHOLDER_VALUES: Final[frozenset[str]] = frozenset({"n/a", "na", "unknown", "none", "null"})

#: A canonical positive rational, as ``ffprobe`` reports a frame rate:
#: ``"30/1"``, ``"30000/1001"``. Both parts are positive and carry no leading
#: zero, no sign, and no surrounding whitespace.
_RATIONAL = re.compile(r"^[1-9][0-9]*/[1-9][0-9]*$")


class MediaProbeExecutionError(Exception):
    """The probe did not produce a result ``core`` can trust.

    That sentence is the whole meaning, and what it deliberately does **not**
    say is as load-bearing as what it does.

    It does **not** claim the failure is transient. It covers a missing or
    vanished engine, a timeout, a crash, a nonzero exit, output that does not
    parse, and — the case this module's own validator produces — an adapter
    answering inconsistently with its own contract. Some of those are perfectly
    deterministic for the same bytes and a retry would fail identically.

    It does **not** claim anything about the media either. Deliberately **not** a
    :class:`~core.processing.errors.ProcessingError`, so it will flow through
    :class:`~core.processing.service.ProcessingOrchestrator` untouched when the
    media processors that raise it exist: the capture stays ``processing``, no
    content object is built or persisted, and the delivery layer answers a fixed
    503. Marking the capture ``failed`` would record a judgement about a file
    the system never managed to look at.

    **A malformed answer is this, and never
    :class:`~core.processing.errors.ProcessingInputError`.** An adapter that
    contradicts itself has told us nothing about the bytes, so the bytes must
    not be blamed for it — and specifically must not be recorded as having been
    examined and found to contain no streams.

    ``MediaProbePrerequisiteError`` is deliberately absent. Whether a particular
    engine is installed is a fact about a concrete adapter's deployment, and
    ``core`` neither knows which engine will be used nor has anything to do with
    the answer; that error belongs to the adapter package, not here.

    Its message is written for a server log rather than for a client. Nothing
    here is passed to the wire.
    """


@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    """One audio stream, as the container declares it.

    ``index`` is the stream's position within the container, exactly as the
    container numbers its streams — so it is shared with the video streams
    rather than being an index into this tuple, and it starts at zero.

    Everything else is optional because a container genuinely may not declare
    it, and **omission is how that is said**. A missing sample rate is ``None``,
    never ``0``, never ``"N/A"``, and never a guess: ``core`` records what was
    observed and nothing else.
    """

    index: int
    codec: str | None
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True, slots=True)
class VideoStreamInfo:
    """One video stream, as the container declares it.

    ``index`` means what it means on :class:`AudioStreamInfo`: the container's
    own numbering, shared across both collections.

    ``frame_rate`` is a rational string rather than a float on purpose.
    ``30000/1001`` is the exact fact the container carries, and ``29.97`` is a
    lossy rendering of it that no later stage could undo.
    """

    index: int
    codec: str | None
    width: int | None
    height: int | None
    frame_rate: str | None


@dataclass(frozen=True, slots=True)
class MediaProbeResult:
    """Everything one probe of one media file reports back.

    ``container_names`` is the container family or families the bytes were
    recognized as, lowercased and sorted — sorted because two probes of the same
    file must produce the same value, and a set's iteration order is not a fact
    about the media. It is a tuple rather than one string because some
    containers are genuinely a family: an ISO base media file is ``mp4`` and
    several siblings at once, and picking one of them here would be the
    sniff-and-rewrite ADR-021 forbids.

    ``duration_seconds`` is the container's declared duration, and ``None`` when
    it declares none. A live-recorded stream or a truncated upload may have no
    duration at all, which is a fact rather than a failure.

    There are deliberately **no stream-count fields.** ``len(audio_streams)`` and
    ``len(video_streams)`` are the counts, they cannot disagree with the lists
    they describe, and a count that could disagree is a second source of truth
    about the same thing.

    There is deliberately **no place for subtitle, data, attachment, or other
    stream types.** Phase 5A ingests time-based media as an immutable original
    with structural metadata; nothing in that job reads a subtitle track, and
    modelling one would be vocabulary invented ahead of a decision. A container
    carrying such streams is perfectly acceptable — they are simply not
    described here.

    There is also **no engine name or version.** ADR-021 fixes that the probing
    engine is not canonical metadata: which build of which tool read the
    container does not change what the container says, unlike an OCR engine,
    whose identity is part of what its output means.
    """

    container_names: tuple[str, ...]
    duration_seconds: float | None
    audio_streams: tuple[AudioStreamInfo, ...]
    video_streams: tuple[VideoStreamInfo, ...]


class MediaProbe(Protocol):
    """Reads the structure of one time-based media file.

    Synchronous, like every other port in ``core``, and for the same reason: the
    one thing that will call it is a synchronous processor, and an awaitable
    surface for implementations that do not exist yet would buy nothing.
    """

    def probe(self, stream: BinaryIO) -> MediaProbeResult:
        """Report the structural facts observed in the stream's bytes.

        ``stream`` is an open, rewound binary stream positioned at the start of
        the immutable original. The implementation reads it forward and closes
        nothing it did not open; the caller owns the handle.

        It is the only parameter, and that is the contract rather than an
        omission. The implementation is not told the declared MIME type, the
        capture modality, the capture id, a filename, or where the bytes live,
        because none of those are things it may consult when deciding what it
        saw. Core policy interprets the answer; the adapter only observes.

        The result must describe the bytes and nothing else. It must not
        transcribe, decode frames, extract embedded tags, select a "primary"
        stream, or repair values it considers wrong — every one of those is a
        policy decision, and this port has no standing to make one.

        Raises :class:`MediaProbeExecutionError` when no trusted structural
        result came back, for any of the reasons that error documents. It must
        raise rather than return an empty or partially filled result: a probe
        that failed and a file with no streams are different facts, and
        :func:`validate_media_probe_result` cannot tell them apart.

        It must **not** raise
        :class:`~core.processing.errors.ProcessingInputError`. A verdict about
        the submitted media is not this port's to reach.
        """
        ...


def _reject(message: str) -> MediaProbeExecutionError:
    return MediaProbeExecutionError(f"the media probe {message}")


def _check_normalized_text(value: object, *, label: str) -> None:
    """Accept nonblank, untrimmed, lowercase, non-placeholder text, or raise."""
    if not isinstance(value, str):
        raise _reject(f"reported {type(value).__name__} where {label} was expected")
    if not value.strip():
        raise _reject(f"reported {label} as an empty or whitespace-only string")
    if value != value.strip():
        raise _reject(f"reported {label} {value!r} with surrounding whitespace")
    if value != value.lower():
        raise _reject(f"reported {label} {value!r}, which is not lowercase")
    if value in PLACEHOLDER_VALUES:
        raise _reject(f"reported the placeholder {value!r} as {label} instead of omitting it")


def _check_positive_int(value: object, *, label: str, index: int) -> None:
    """Accept ``None`` or a genuine positive ``int``, or raise.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` in Python, so
    ``isinstance`` alone would let ``True`` through and record ``channels: true``
    in durable metadata.
    """
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(
            f"reported {type(value).__name__} as the {label} of stream {index}, "
            f"where a positive integer or nothing was expected"
        )
    if value <= 0:
        raise _reject(f"reported a {label} of {value} for stream {index}")


def _check_frame_rate(value: object, *, index: int) -> None:
    """Accept ``None`` or a canonical positive rational string, or raise.

    Canonical means ``"30000/1001"`` and not ``"60/2"``: the same rate written
    two ways would compare unequal on a stored object, so the reduced form is
    the only one that crosses. ``"0/0"`` — what an engine reports when it has no
    frame rate to give — is not a rate and must arrive as ``None``.
    """
    if value is None:
        return
    if not isinstance(value, str):
        raise _reject(
            f"reported {type(value).__name__} as the frame rate of stream {index}, "
            f"where a rational string or nothing was expected"
        )
    if not _RATIONAL.fullmatch(value):
        raise _reject(
            f"reported {value!r} as the frame rate of stream {index}, which is not a "
            f"canonical positive rational"
        )
    numerator, denominator = (int(part) for part in value.split("/"))
    if math.gcd(numerator, denominator) != 1:
        raise _reject(
            f"reported {value!r} as the frame rate of stream {index}, which is not reduced"
        )


def _check_index(value: object, *, seen: set[int]) -> int:
    """Accept a genuine, non-negative, globally unique stream index, or raise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(
            f"reported {type(value).__name__} as a stream index, where an integer was expected"
        )
    if value < 0:
        raise _reject(f"reported a stream index of {value}")
    if value in seen:
        raise _reject(f"reported stream index {value} more than once")
    seen.add(value)
    return value


def validate_media_probe_result(result: object) -> None:
    """Check that a result is a normalized description of the bytes, or raise.

    **The parameter is ``object``, and that is the first check rather than a
    looseness.** The annotation on :meth:`MediaProbe.probe` binds what a type
    checker will accept and nothing at run time, and an adapter is third-party
    code by construction. One that returned ``None`` or a ``dict`` would
    otherwise reach an attribute access and raise ``AttributeError``, which is
    not a :class:`MediaProbeExecutionError` and would therefore escape the
    lifecycle this port documents, surfacing as an unhandled bug and a generic
    500 instead of the fixed 503 an inconsistent adapter is supposed to produce.

    Every rule exists because breaking it would put something untrue, or
    something nondeterministic, onto a stored content object:

    * **The result is a :class:`MediaProbeResult`,** and both stream collections
      hold the matching typed values. A mapping that happens to have the right
      keys is raw engine output wearing a costume.
    * **``container_names`` is a non-empty tuple** of nonblank, lowercase,
      untrimmed, unique names in sorted order. Non-empty because a probe that
      recognized no container recognized nothing and should have raised; sorted
      because two probes of one file must agree; lowercase and placeholder-free
      because ``"MP4"``, ``"mp4"`` and ``"N/A"`` must not be three answers.
    * **``duration_seconds``, when present, is a finite non-negative
      ``float``.** ``nan`` and ``inf`` survive JSON serialization nowhere, a
      negative duration is not a duration, and an ``int`` is the declared type
      being contradicted.
    * **Every stream has a real, non-negative ``int`` index, and indexes are
      unique across audio *and* video together.** Container indexes number one
      sequence, so a video stream and an audio stream sharing index ``2`` are
      two claims about one slot. ``bool`` is rejected explicitly, for the reason
      :func:`_check_positive_int` documents.
    * **Each collection is ordered by index.** The order is the value: an
      adapter iterating a hash map would otherwise make two probes of one file
      produce two different stored objects.
    * **Optional numbers, when present, are positive ``int``s**, and optional
      ``codec`` text is normalized and not a placeholder.
    * **``frame_rate``, when present, is a canonical reduced positive
      rational.**

    Nothing is trimmed, lowercased, reduced, sorted, or otherwise repaired.
    Normalizing here would make this function a second normalizer competing with
    the adapter's, and would hide exactly the inconsistency it exists to catch.

    This validator is about the **port contract only**. Whether an ``AUDIO``
    capture must carry at least one audio stream, whether a declared
    ``video/mp4`` may probe as ``matroska``, and which containers this build
    accepts at all are canonical policy questions that ADR-021 assigns to the
    media processors — which do not exist yet — and not to the seam.

    Raises :class:`MediaProbeExecutionError`, never
    :class:`~core.processing.errors.ProcessingInputError`.
    """
    if not isinstance(result, MediaProbeResult):
        # The type name only. Nothing of the object's *contents* is repeated —
        # no ``repr``, no fields — because this came from third-party code and
        # may hold anything at all, and this message reaches a server log.
        raise _reject(f"returned {type(result).__name__} where a probe result was expected")

    _check_container_names(result.container_names)
    _check_duration(result.duration_seconds)

    indexes: set[int] = set()
    _check_audio_streams(result.audio_streams, indexes)
    _check_video_streams(result.video_streams, indexes)


def _check_container_names(names: object) -> None:
    if not isinstance(names, tuple):
        raise _reject(
            f"reported {type(names).__name__} where a tuple of container names was expected"
        )
    if not names:
        raise _reject("reported no container name at all")
    for name in names:
        _check_normalized_text(name, label="a container name")
    if len(set(names)) != len(names):
        raise _reject(f"reported the same container name twice in {list(names)}")
    if list(names) != sorted(names):
        raise _reject(f"reported container names {list(names)} out of sorted order")


def _check_duration(duration: object) -> None:
    if duration is None:
        return
    if isinstance(duration, bool) or not isinstance(duration, float):
        raise _reject(
            f"reported {type(duration).__name__} as the duration, where a float or nothing "
            f"was expected"
        )
    if not math.isfinite(duration):
        raise _reject(f"reported a duration of {duration}")
    if duration < 0:
        raise _reject(f"reported a duration of {duration} seconds")


def _check_ordered(indexes: list[int], *, label: str) -> None:
    if indexes != sorted(indexes):
        raise _reject(f"reported {label} out of index order: {indexes}")


def _check_audio_streams(streams: object, indexes: set[int]) -> None:
    if not isinstance(streams, tuple):
        raise _reject(
            f"reported {type(streams).__name__} where a tuple of audio streams was expected"
        )
    ordered: list[int] = []
    for stream in streams:
        if not isinstance(stream, AudioStreamInfo):
            raise _reject(f"reported {type(stream).__name__} where an audio stream was expected")
        index = _check_index(stream.index, seen=indexes)
        ordered.append(index)
        if stream.codec is not None:
            _check_normalized_text(stream.codec, label=f"the codec of stream {index}")
        _check_positive_int(stream.sample_rate, label="sample rate", index=index)
        _check_positive_int(stream.channels, label="channel count", index=index)
    _check_ordered(ordered, label="audio streams")


def _check_video_streams(streams: object, indexes: set[int]) -> None:
    if not isinstance(streams, tuple):
        raise _reject(
            f"reported {type(streams).__name__} where a tuple of video streams was expected"
        )
    ordered: list[int] = []
    for stream in streams:
        if not isinstance(stream, VideoStreamInfo):
            raise _reject(f"reported {type(stream).__name__} where a video stream was expected")
        index = _check_index(stream.index, seen=indexes)
        ordered.append(index)
        if stream.codec is not None:
            _check_normalized_text(stream.codec, label=f"the codec of stream {index}")
        _check_positive_int(stream.width, label="width", index=index)
        _check_positive_int(stream.height, label="height", index=index)
        _check_frame_rate(stream.frame_rate, index=index)
    _check_ordered(ordered, label="video streams")
