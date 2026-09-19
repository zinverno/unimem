"""Canonical processing of staged audio and video captures.

Phase 5A-1 gave ``core`` the vocabulary and the seam: ``AUDIO`` at schema 0.3,
and :mod:`core.processing.media_probe`, a port that answers *what structural
facts were observed in these bytes?* and nothing else. This module is the policy
that interprets those facts, and it is the first thing in the system that can
turn a staged media file into canonical content.

Two processors, not one::

    AudioProcessor   AUDIO  + audio/mpeg | audio/wav | audio/ogg  -> ContentType.AUDIO
    VideoProcessor   VIDEO  + video/mp4  | video/webm             -> ContentType.VIDEO

They are siblings rather than one parameterized processor because what they
*require* differs, and that difference is the whole of the structural policy: an
``AUDIO`` capture must carry at least one audio stream, a ``VIDEO`` capture at
least one video stream, and a silent video is valid media while a soundless
audio file is not a recording of anything. Folding both into one class with a
mode flag would put that asymmetry in a conditional instead of in a type.

**The declared MIME type routes; the probed container verifies.** ADR-021 fixed
this and it is the reason :meth:`~core.processing.media_probe.MediaProbe.probe`
is not handed the declared type: the two facts must be observed independently or
the verification is circular. A declaration this build disagrees with is refused,
never corrected — there is no sniff-and-rewrite here, and a capture declaring
``video/mp4`` whose bytes probe as something else is a
:class:`~core.processing.errors.ProcessingInputError` rather than a declaration
to quietly fix.

**Two failures that mean opposite things, and they must not be conflated.**

* :class:`~core.processing.errors.ProcessingInputError` — a *verdict about the
  submitted bytes*, reached deterministically: the container does not match the
  declaration, or the required stream is absent. The same bytes will fail the
  same way forever, so the orchestrator durably marks the capture ``failed``.
* :class:`~core.processing.media_probe.MediaProbeExecutionError` — **no trusted
  structural result was produced**, so UniMem cannot reach a verdict about the
  submitted media. It is deliberately outside the ``ProcessingError`` hierarchy,
  so it propagates through this module **untouched** and leaves the capture
  non-terminal. Wrapping it in a ``ProcessingInputError`` would record a verdict
  this build has no evidence for, which is the one mistake this separation exists
  to prevent.

  Note what that error does **not** say. It does not say the bytes were never
  examined: the failure may arrive before any byte is read, after some, or after
  all of them. It does not say the failure is transient, and it does not promise
  that the identical bytes would succeed on a retry. The one durable conclusion
  is the absence of a result this build can trust.

Raw-store failures propagate unchanged too, exactly as they do from every other
processor: they describe the state of the store rather than of the capture.

**What a successful media capture produces is deliberately small.** A
``COMPLETE`` content object with the immutable original as its one asset,
structural metadata describing what the container declares, and **no segments at
all** — the same honest normalization ADR-019 fixed for an uninterpreted image,
applied to material whose content is what it sounds or looks like over time.
Nothing is transcribed, no audio track is extracted, no keyframe is taken, and no
placeholder segment stands in for the interpretation that did not happen.
Transcription is Phase 5B.

This module imports no media framework, no codec binding, no ``subprocess`` and
no ``tempfile``, and Phase 5A-2 adds none of those to the media processing path:
the engine lives behind the port, and this module is testable on a machine with
no media tooling installed at all. That is a statement about *this path*, not
about ``core`` as a whole — :mod:`core.storage.local` has staged immutable raw
objects through ``tempfile`` since Phase 0B, and this slice leaves it untouched.
See `ADR-022 <../../docs/ADR/ADR-022-engine-independent-media-processing.md>`_.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from core.contracts import (
    Asset,
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentObject,
    ContentSource,
    ContentType,
    JsonMapping,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    RawObjectRef,
)
from core.processing.errors import ProcessingInputError
from core.processing.media_probe import (
    AudioStreamInfo,
    MediaProbe,
    MediaProbeResult,
    VideoStreamInfo,
    validate_media_probe_result,
)
from core.storage import RawObjectStore

#: The audio formats this build has a processor for. An ``AUDIO`` capture must
#: declare one of them exactly; nothing here infers a format from bytes, from a
#: filename it was never given, or from a container it has not yet probed.
AUDIO_MIME_TYPES: Final[tuple[str, ...]] = ("audio/mpeg", "audio/wav", "audio/ogg")

#: The video formats this build has a processor for, on the same terms.
#:
#: A second tuple rather than a widened first one, for the reason the document
#: and image tuples are also separate: these gate different payload types and a
#: single merged list would let ``audio/ogg`` be declared on a ``VIDEO`` capture.
VIDEO_MIME_TYPES: Final[tuple[str, ...]] = ("video/mp4", "video/webm")

#: Declared MIME type -> the container alias the probe must have observed, which
#: is also the canonical family recorded in durable metadata.
#:
#: One mapping serves both jobs on purpose. The alias a probe must report and the
#: family this build writes down are the same fact, and two tables would be two
#: places for it to drift.
#:
#: **Only the required alias is checked.** A probe legitimately reports a family:
#: an ISO base media file is ``mov``, ``mp4``, ``m4a``, ``3gp``, ``3g2`` and
#: ``mj2`` at once, and demanding an exact tuple would refuse a perfectly ordinary
#: MP4 for being described completely. Requiring membership answers the only
#: question policy has — *are these bytes the kind of container that was
#: declared?* — and leaves the rest of the family as the observation it is.
#:
#: **Runtime-immutable on purpose.** This table decides which captures are
#: accepted and what ``media.container`` a stored content object claims, so it is
#: a build constant rather than runtime configuration. ``Final`` binds a type
#: checker and nothing at run time — a plain ``dict`` behind it could still be
#: mutated by any importer, silently widening or narrowing canonical acceptance
#: for the life of the process. The ``MappingProxyType`` makes that a
#: ``TypeError`` at the point of the attempt.
#:
#: It is a read-only view, not a registry or a configuration system: five pairs,
#: written here, changed only by editing this line in a reviewed diff.
MEDIA_CONTAINERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "video/mp4": "mp4",
        "video/webm": "webm",
    }
)

#: ``ContentObject.metadata`` key holding the container-level facts.
MEDIA_METADATA_KEY: Final = "media"

#: Keys within ``metadata["media"]``.
CONTAINER_KEY: Final = "container"
DURATION_SECONDS_KEY: Final = "duration_seconds"
AUDIO_STREAM_COUNT_KEY: Final = "audio_stream_count"
VIDEO_STREAM_COUNT_KEY: Final = "video_stream_count"

#: Top-level ``ContentObject.metadata`` keys holding the per-stream facts.
AUDIO_STREAMS_KEY: Final = "audio_streams"
VIDEO_STREAMS_KEY: Final = "video_streams"

#: Keys within one stream entry.
INDEX_KEY: Final = "index"
CODEC_KEY: Final = "codec"
SAMPLE_RATE_KEY: Final = "sample_rate"
CHANNELS_KEY: Final = "channels"
WIDTH_KEY: Final = "width"
HEIGHT_KEY: Final = "height"
FRAME_RATE_KEY: Final = "frame_rate"

#: What a client is told when the bytes contradict the declaration.
#:
#: Deliberately generic, and deliberately identical for every mismatch. The
#: observed aliases, the probe's answer, the ``file_ref``, the digest and the
#: declared type are all absent: a refusal is not a reason to publish what the
#: server saw inside somebody's file, and a message that varied with the
#: observation would be a read oracle over staged bytes.
_FORMAT_MISMATCH: Final = "media bytes do not match the declared supported format"


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule, and ids are explicitly
    *not* derived from the media's digest: that address identifies bytes, while
    two captures of the identical recording are two captures.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str, mime_type: str) -> Asset:
    """Describe the immutable original as the content object's one asset.

    As with an image, the asset is where the entire content lives: this build
    derives nothing from the samples or the frames, so a media content object
    without its original would be a record of a few integers.

    It is the **only** asset, and that is a decision rather than an omission. No
    ``AssetRole.AUDIO`` track is extracted from a video, no keyframe is taken, and
    no thumbnail is rendered — every one of those would be a new binary this
    phase did not produce. Streams are *metadata*; they are not assets.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=mime_type,
        ref=ref,
        sha256=raw_object.sha256,
    )


def _preconditions(
    capture: CaptureRecord, payload_type: CapturePayloadType, mime_types: tuple[str, ...]
) -> tuple[RawObjectRef, str, str]:
    """Settle everything knowable from the capture record, before any I/O.

    Repeats what ``supports`` already asked, because ``process`` is callable
    directly and must not read an OGG as though it were a WebM just because
    nobody routed the capture first.

    Returns the raw object, its storage reference, and its declared MIME type.
    """
    if capture.payload_type is not payload_type:
        raise ProcessingInputError(f"capture {capture.id!r} is not a {payload_type.value} capture")
    raw_object = capture.raw_object
    if raw_object is None:
        raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
    if raw_object.ref is None:
        raise ProcessingInputError(
            f"capture {capture.id!r} references raw object {raw_object.id!r} "
            f"without a storage reference"
        )
    mime_type = raw_object.mime_type
    if mime_type is None:
        raise ProcessingInputError(
            f"capture {capture.id!r} references a raw object declaring no mime_type; "
            f"this processor reads declared {payload_type.value} formats and does not "
            f"infer one from the bytes"
        )
    if mime_type not in mime_types:
        raise ProcessingInputError(
            f"capture {capture.id!r} references a raw object declaring a mime_type this "
            f"processor does not read"
        )
    return raw_object, raw_object.ref, mime_type


def _probe_once(
    raw_store: RawObjectStore, media_probe: MediaProbe, raw_object: RawObjectRef
) -> MediaProbeResult:
    """Open the immutable original, probe it exactly once, and validate the answer.

    One call, and the handle is the only thing that crosses: the adapter is told
    nothing about the capture, the declaration, or where the bytes live.

    :func:`~core.processing.media_probe.validate_media_probe_result` runs before
    anything believes the result, and both it and the probe raise
    :class:`~core.processing.media_probe.MediaProbeExecutionError` — *no trusted
    structural result* — which is allowed straight through. Nothing here catches
    it, and nothing re-probes: a second call would be a second chance for two
    answers about one file.
    """
    with raw_store.open(raw_object) as handle:
        result = media_probe.probe(handle)
    validate_media_probe_result(result)
    return result


def _verified_container(result: MediaProbeResult, mime_type: str) -> str:
    """Check the observed container against the declaration, or refuse.

    Returns the canonical family to record. Raises
    :class:`~core.processing.errors.ProcessingInputError` with a fixed generic
    message when the bytes contradict what was declared — a verdict about the
    submitted file, reached deterministically, that the same bytes will earn
    again.
    """
    required = MEDIA_CONTAINERS[mime_type]
    if required not in result.container_names:
        raise ProcessingInputError(_FORMAT_MISMATCH)
    return required


def _audio_entry(stream: AudioStreamInfo) -> JsonMapping:
    """One audio stream, omission-first.

    ``index`` is always present; every other field appears only when the
    container declared it. Absence is written as absence — never ``null``, never
    ``0``, never ``"unknown"`` — because a stored object claiming ``channels: 0``
    would be an observation nobody made.
    """
    entry: JsonMapping = {INDEX_KEY: stream.index}
    if stream.codec is not None:
        entry[CODEC_KEY] = stream.codec
    if stream.sample_rate is not None:
        entry[SAMPLE_RATE_KEY] = stream.sample_rate
    if stream.channels is not None:
        entry[CHANNELS_KEY] = stream.channels
    return entry


def _video_entry(stream: VideoStreamInfo) -> JsonMapping:
    """One video stream, on the same omission-first terms."""
    entry: JsonMapping = {INDEX_KEY: stream.index}
    if stream.codec is not None:
        entry[CODEC_KEY] = stream.codec
    if stream.width is not None:
        entry[WIDTH_KEY] = stream.width
    if stream.height is not None:
        entry[HEIGHT_KEY] = stream.height
    if stream.frame_rate is not None:
        entry[FRAME_RATE_KEY] = stream.frame_rate
    return entry


def _media_metadata(result: MediaProbeResult, container: str) -> JsonMapping:
    """Build the durable structural metadata ADR-021 fixed.

    Always present: ``media``, its ``container`` and both counts, and both stream
    lists. An empty list stays present because it is a real observation — this
    stream type was looked for and was absent — and omitting it would make "no
    audio" indistinguishable from "nobody checked".

    **The counts are stored but never independently observed.** They are exactly
    ``len(result.audio_streams)`` and ``len(result.video_streams)`` and are
    computed by no other means, so they are a projection of the lists beside them
    rather than a second source of truth. The probe is never asked for a count and
    has no way to report one.

    ``duration_seconds`` is omitted entirely when the container declared none,
    which a live-recorded or truncated file legitimately does.

    Stream order is the normalized order the port already guaranteed — ascending
    by container index — and is preserved rather than re-sorted here.
    """
    media: JsonMapping = {CONTAINER_KEY: container}
    if result.duration_seconds is not None:
        media[DURATION_SECONDS_KEY] = result.duration_seconds
    media[AUDIO_STREAM_COUNT_KEY] = len(result.audio_streams)
    media[VIDEO_STREAM_COUNT_KEY] = len(result.video_streams)

    return {
        MEDIA_METADATA_KEY: media,
        AUDIO_STREAMS_KEY: [_audio_entry(stream) for stream in result.audio_streams],
        VIDEO_STREAMS_KEY: [_video_entry(stream) for stream in result.video_streams],
    }


def _build_content(
    capture: CaptureRecord,
    *,
    content_type: ContentType,
    raw_object: RawObjectRef,
    ref: str,
    mime_type: str,
    metadata: JsonMapping,
    processor: str,
    version: str,
    started_at: datetime,
    completed_at: datetime,
) -> ContentObject:
    """Assemble the canonical object. Shared because it is identical either way.

    The **schema version is not copied from the capture**, and that is the one
    subtlety worth naming. A historical ``VIDEO`` ``CaptureRecord`` written at
    schema 0.1 or 0.2 keeps its own version — ADR-002's compatibility promise —
    but the content object this run produces is a *new* document written by this
    build, so it carries the current 0.3 like every other new contract. The two
    versions describe different documents and neither inherits from the other.
    """
    original = _original_asset(raw_object, ref, mime_type)
    return ContentObject(
        id=_new_id(),
        type=content_type,
        source=ContentSource(
            capture_id=capture.id,
            provider=capture.source.provider,
            url=capture.source.url,
        ),
        original=OriginalReference(
            asset_id=original.id,
            mime_type=mime_type,
            sha256=raw_object.sha256,
        ),
        # The submitted capture title, exactly as given, or none. Deliberately no
        # fallback and no second source: not an embedded ``title`` tag, not an
        # artist or album, not the uploaded filename, and nothing read out of the
        # container — this build reads no tags at all.
        title=capture.title,
        metadata=metadata,
        # Empty, and this is what the whole phase turns on. Media nothing has
        # listened to or watched has no segments: a segment is something a
        # processor read, recognized, or transcribed, and this one did none of
        # those. A placeholder would be indistinguishable downstream from a
        # transcript a model actually produced. See ADR-021 and ADR-019.
        segments=[],
        assets=[original],
        processing=[
            ProcessingRecord(
                processor=processor,
                processor_version=version,
                started_at=started_at,
                completed_at=completed_at,
                # ``COMPLETE`` describes *this processor's* run and nothing more:
                # the original is held and its structure described, entirely.
                # ``PARTIAL`` would claim something was attempted and missed, and
                # missing optional container metadata is not a warning — it is a
                # fact about the file.
                status=ProcessingStatus.COMPLETE,
            )
        ],
    )


class AudioProcessor:
    """Normalizes staged audio captures into canonical content."""

    name = "audio"
    #: The first version of these semantics. ``version`` describes *what this
    #: processor produces*, so any change to the supported formats, the container
    #: policy, the structural requirement, the metadata vocabulary, or the
    #: zero-segment decision changes it.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore, media_probe: MediaProbe) -> None:
        """Take the store the original is read through and the probe that reads it.

        Two ports and nothing else. This processor uses no media framework, no
        codec binding, no ``subprocess``, no ``tempfile``, no HTTP client, and no
        filesystem access of its own — which is what keeps this policy testable on
        a machine with no media tooling installed. (The raw object store it reads
        through has its own Phase 0B staging, which is that store's business.)

        The probe is a constructor argument rather than a per-call one, so a
        deployment that supplied none cannot end up with a processor holding a
        ``None`` engine that fails on the first recording.
        """
        self._raw_store = raw_store
        self._media_probe = media_probe

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged audio and nothing else. Pure; touches no storage.

        Three facts, all read from the capture record: it is an ``audio``
        capture, it has a raw original, and that original is declared one of
        :data:`AUDIO_MIME_TYPES`.

        Deliberately *not* "all audio". Claiming the payload type alone would
        claim FLAC, AAC and every other format the day one arrives, and the
        router's exactly-one-match rule would report an ambiguity that is really
        a design mistake here.

        Whether the bytes really carry that container is a ``process`` question:
        answering it here would mean probing to route, and an audio capture whose
        original cannot currently be read is still an audio capture.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.AUDIO
            and raw_object is not None
            and raw_object.mime_type in AUDIO_MIME_TYPES
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Probe the original's structure and build canonical content around it.

        The capture record is only read, never written: lifecycle is
        orchestration's business, the declared MIME type is not corrected, and the
        raw original is not modified.

        **An audio capture must carry at least one audio stream.** A file
        declaring itself audio whose container holds none is not a recording of
        anything, and that is a deterministic verdict about the submitted bytes.
        Video streams are welcome alongside — embedded cover art is an ordinary
        MP3 — and are described rather than refused. Several audio streams are
        equally valid, and no primary one is selected.
        """
        started_at = datetime.now(UTC)
        raw_object, ref, mime_type = _preconditions(
            capture, CapturePayloadType.AUDIO, AUDIO_MIME_TYPES
        )

        result = _probe_once(self._raw_store, self._media_probe, raw_object)
        container = _verified_container(result, mime_type)
        if not result.audio_streams:
            raise ProcessingInputError("submitted audio lacks a verifiable audio stream")

        return _build_content(
            capture,
            content_type=ContentType.AUDIO,
            raw_object=raw_object,
            ref=ref,
            mime_type=mime_type,
            metadata=_media_metadata(result, container),
            processor=self.name,
            version=self.version,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )


class VideoProcessor:
    """Normalizes staged video captures into canonical content."""

    name = "video"
    #: The first version of these semantics, on the same terms as
    #: :attr:`AudioProcessor.version`.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore, media_probe: MediaProbe) -> None:
        """Take the store the original is read through and the probe that reads it.

        The same two ports :class:`AudioProcessor` takes, and the same single
        :class:`~core.processing.media_probe.MediaProbe` instance may serve both:
        the port is stateless with respect to the caller, and one engine reading
        two kinds of container is the ordinary case.
        """
        self._raw_store = raw_store
        self._media_probe = media_probe

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged video and nothing else. Pure; touches no storage.

        The same three record-only facts :meth:`AudioProcessor.supports` reads,
        against :data:`VIDEO_MIME_TYPES`. The two processors claim disjoint
        payload types, so the router separates them with no precedence and no
        ordering.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.VIDEO
            and raw_object is not None
            and raw_object.mime_type in VIDEO_MIME_TYPES
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Probe the original's structure and build canonical content around it.

        **A video capture must carry at least one video stream; audio is
        optional.** Both shapes are valid and neither is preferred: a silent clip
        with no audio stream at all, and a video carrying one or several audio
        streams, are equally ordinary video. Optional means exactly that — there
        is no rule against audio here, and any audio streams present are described
        in the metadata rather than refused. This method inspects only
        ``result.video_streams``.

        That asymmetry with :class:`AudioProcessor` is the point. Requiring audio
        would invent a requirement no video format has, while audio with no audio
        stream is not a recording of anything. Several streams of either kind are
        valid, and no primary one is selected.
        """
        started_at = datetime.now(UTC)
        raw_object, ref, mime_type = _preconditions(
            capture, CapturePayloadType.VIDEO, VIDEO_MIME_TYPES
        )

        result = _probe_once(self._raw_store, self._media_probe, raw_object)
        container = _verified_container(result, mime_type)
        if not result.video_streams:
            raise ProcessingInputError("submitted video lacks a verifiable video stream")

        return _build_content(
            capture,
            content_type=ContentType.VIDEO,
            raw_object=raw_object,
            ref=ref,
            mime_type=mime_type,
            metadata=_media_metadata(result, container),
            processor=self.name,
            version=self.version,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
