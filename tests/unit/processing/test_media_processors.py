"""Canonical audio and video processing, and the policy that decides it.

Everything here runs against :class:`~tests.unit.processing.doubles.FakeMediaProbe`.
There is no real media adapter in this repository, no ``ffprobe``, no ``ffmpeg``,
no subprocess, no temporary file and no media fixture — which is the property the
seam was designed for rather than a limitation of these tests. The policy is
values in and values out, so it runs on a machine with no media tooling at all.

Three separations are being pinned, and they are what the slice is:

* **The declaration routes; the observation verifies.** A capture declaring
  ``video/mp4`` whose bytes probe as something else is refused, never corrected.
* **A trusted verdict is not an untrusted result.** A container mismatch or a
  missing required stream is a ``ProcessingInputError`` about the submitted
  bytes, reached from a result this build trusts; a probe that produced no
  trusted structural result is a ``MediaProbeExecutionError`` that passes through
  untouched, leaving no basis for a verdict about the media.
* **Audio and video require different things.** Audio needs at least one audio
  stream; video needs at least one video stream, and its audio is optional —
  present or absent, one stream or several, all valid.
"""

from typing import Any, cast

import pytest

from core.contracts import (
    SCHEMA_VERSION,
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentType,
    ProcessingStatus,
)
from core.processing import (
    AUDIO_MIME_TYPES,
    MEDIA_CONTAINERS,
    VIDEO_MIME_TYPES,
    AudioProcessor,
    ProcessingInputError,
    VideoProcessor,
)
from core.processing.media_probe import MediaProbeExecutionError
from core.storage import build_raw_ref
from tests.unit.processing.builders import store_and_capture
from tests.unit.processing.doubles import (
    FakeMediaProbe,
    InMemoryRawObjectStore,
    audio_stream,
    probe_result,
    video_stream,
)

MP3 = b"\xff\xfb\x90\x00fake mp3 payload"
MP4 = b"\x00\x00\x00\x20ftypisom fake mp4 payload"

#: One audio stream and nothing else — the minimum a valid audio capture needs.
AUDIO_ONLY = (audio_stream(index=0),)

#: One video stream and nothing else — a silent clip, which is valid video.
VIDEO_ONLY = (video_stream(index=0),)


@pytest.fixture
def store() -> InMemoryRawObjectStore:
    return InMemoryRawObjectStore()


def audio_capture(
    store: InMemoryRawObjectStore, *, mime_type: str | None = "audio/mpeg", **overrides: Any
) -> CaptureRecord:
    return store_and_capture(
        store,
        MP3,
        mime_type=mime_type,
        id="cap_audio_01",
        payload_type=CapturePayloadType.AUDIO,
        **overrides,
    )


def video_capture(
    store: InMemoryRawObjectStore, *, mime_type: str | None = "video/mp4", **overrides: Any
) -> CaptureRecord:
    return store_and_capture(
        store,
        MP4,
        mime_type=mime_type,
        id="cap_video_01",
        payload_type=CapturePayloadType.VIDEO,
        **overrides,
    )


def audio_probe(**overrides: Any) -> FakeMediaProbe:
    fields: dict[str, Any] = {"container_names": ("mp3",), "audio_streams": AUDIO_ONLY}
    return FakeMediaProbe(result=probe_result(**(fields | overrides)))


def video_probe(**overrides: Any) -> FakeMediaProbe:
    fields: dict[str, Any] = {"container_names": ("mp4",), "video_streams": VIDEO_ONLY}
    return FakeMediaProbe(result=probe_result(**(fields | overrides)))


class TestTheCapabilityClaim:
    """``supports`` is a pure question about the capture record."""

    @pytest.mark.parametrize("mime_type", AUDIO_MIME_TYPES)
    def test_audio_claims_each_audio_format(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        processor = AudioProcessor(store, audio_probe())

        assert processor.supports(audio_capture(store, mime_type=mime_type))

    @pytest.mark.parametrize("mime_type", VIDEO_MIME_TYPES)
    def test_video_claims_each_video_format(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        processor = VideoProcessor(store, video_probe())

        assert processor.supports(video_capture(store, mime_type=mime_type))

    @pytest.mark.parametrize("mime_type", ["audio/flac", "audio/aac", "audio/mp4", "video/mp4"])
    def test_audio_declines_everything_else(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        """Not 'all audio'. An unclaimed format is a routing failure, not a guess."""
        processor = AudioProcessor(store, audio_probe())

        assert not processor.supports(audio_capture(store, mime_type=mime_type))

    @pytest.mark.parametrize("mime_type", ["video/quicktime", "video/x-matroska", "audio/ogg"])
    def test_video_declines_everything_else(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        processor = VideoProcessor(store, video_probe())

        assert not processor.supports(video_capture(store, mime_type=mime_type))

    def test_neither_claims_the_other_s_payload_type(self, store: InMemoryRawObjectStore) -> None:
        """Disjoint claims, so the router separates them with no precedence."""
        audio = audio_capture(store)
        video = video_capture(store)

        assert not VideoProcessor(store, video_probe()).supports(audio)
        assert not AudioProcessor(store, audio_probe()).supports(video)

    def test_a_capture_with_no_raw_object_is_not_claimed(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = audio_capture(store).model_copy(update={"raw_object": None})

        assert not AudioProcessor(store, audio_probe()).supports(capture)

    def test_supports_touches_neither_storage_nor_the_probe(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Routing must not read bytes, and must not probe to decide."""
        probe = audio_probe()
        capture = audio_capture(store)
        store.accesses.clear()

        assert AudioProcessor(store, probe).supports(capture)
        assert store.accesses == []
        assert probe.calls == []


class TestTheProbeIsCalledOnceAndValidated:
    """One look at the bytes, and nothing believes the answer before it is checked."""

    def test_a_successful_audio_run_probes_exactly_once(
        self, store: InMemoryRawObjectStore
    ) -> None:
        probe = audio_probe()

        AudioProcessor(store, probe).process(audio_capture(store))

        assert len(probe.calls) == 1

    def test_a_successful_video_run_probes_exactly_once(
        self, store: InMemoryRawObjectStore
    ) -> None:
        probe = video_probe()

        VideoProcessor(store, probe).process(video_capture(store))

        assert len(probe.calls) == 1

    def test_the_probe_is_handed_the_immutable_original(
        self, store: InMemoryRawObjectStore
    ) -> None:
        probe = audio_probe()

        AudioProcessor(store, probe).process(audio_capture(store))

        assert probe.calls == [MP3]

    def test_a_refused_capture_still_probes_only_once(self, store: InMemoryRawObjectStore) -> None:
        """A policy refusal never re-probes looking for a second opinion."""
        probe = audio_probe(container_names=("wav",))

        with pytest.raises(ProcessingInputError):
            AudioProcessor(store, probe).process(audio_capture(store))

        assert len(probe.calls) == 1

    @pytest.mark.parametrize(
        ("returns", "label"),
        [
            (None, "none"),
            ({"container_names": ("mp3",)}, "dict"),
            (probe_result(container_names=(), audio_streams=AUDIO_ONLY), "no-container"),
            (probe_result(container_names=("MP3",), audio_streams=AUDIO_ONLY), "uppercase"),
            (probe_result(container_names=("n/a",), audio_streams=AUDIO_ONLY), "placeholder"),
            (probe_result(container_names=("mp4", "mov"), audio_streams=AUDIO_ONLY), "unsorted"),
            (
                probe_result(container_names=("mp3",), duration_seconds=-1.0),
                "negative-duration",
            ),
            (
                probe_result(
                    container_names=("mp3",),
                    audio_streams=(audio_stream(index=0), audio_stream(index=0)),
                ),
                "duplicate-index",
            ),
        ],
    )
    def test_a_malformed_adapter_answer_is_an_execution_failure(
        self, store: InMemoryRawObjectStore, returns: object, label: str
    ) -> None:
        """``validate_media_probe_result`` is effectively enforced by the processor.

        Every one of these is an adapter contradicting its own contract, so none
        yields a structural result this build can trust — and without one there is
        no basis for a verdict about the file. Each must therefore surface as
        ``MediaProbeExecutionError`` and never as ``ProcessingInputError``.
        """
        probe = FakeMediaProbe(returns=returns)

        with pytest.raises(MediaProbeExecutionError) as raised:
            AudioProcessor(store, probe).process(audio_capture(store))

        assert not isinstance(raised.value, ProcessingInputError)

    def test_a_probe_failure_propagates_untouched(self, store: InMemoryRawObjectStore) -> None:
        """Not wrapped. The capture must stay non-terminal, which needs this type."""
        failure = MediaProbeExecutionError("the engine vanished")
        probe = FakeMediaProbe(raises=failure)

        with pytest.raises(MediaProbeExecutionError) as raised:
            VideoProcessor(store, probe).process(video_capture(store))

        assert raised.value is failure

    def test_a_probe_failure_is_not_a_processing_error(self, store: InMemoryRawObjectStore) -> None:
        """So the orchestrator leaves the capture ``PROCESSING`` rather than failing it."""
        probe = FakeMediaProbe(raises=MediaProbeExecutionError("no engine"))

        with pytest.raises(MediaProbeExecutionError) as raised:
            AudioProcessor(store, probe).process(audio_capture(store))

        assert not isinstance(raised.value, ProcessingInputError)


class TestContainerPolicy:
    """The declaration routes; the observation verifies; nothing is rewritten."""

    @pytest.mark.parametrize("mime_type", AUDIO_MIME_TYPES)
    def test_every_audio_mapping_verifies(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        required = MEDIA_CONTAINERS[mime_type]
        probe = audio_probe(container_names=(required,))

        content = AudioProcessor(store, probe).process(audio_capture(store, mime_type=mime_type))

        assert content.metadata["media"]["container"] == required  # type: ignore[index,call-overload]

    @pytest.mark.parametrize("mime_type", VIDEO_MIME_TYPES)
    def test_every_video_mapping_verifies(
        self, store: InMemoryRawObjectStore, mime_type: str
    ) -> None:
        required = MEDIA_CONTAINERS[mime_type]
        probe = video_probe(container_names=(required,))

        content = VideoProcessor(store, probe).process(video_capture(store, mime_type=mime_type))

        assert content.metadata["media"]["container"] == required  # type: ignore[index,call-overload]

    def test_the_five_mappings_are_exactly_these(self) -> None:
        """Pinned as a table, because these five pairs are the whole policy."""
        assert MEDIA_CONTAINERS == {
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/ogg": "ogg",
            "video/mp4": "mp4",
            "video/webm": "webm",
        }

    def test_the_policy_cannot_be_mutated_at_runtime(self) -> None:
        """It decides acceptance and stored metadata, so it is a build constant.

        ``Final`` binds a type checker and nothing at run time. A plain ``dict``
        behind it could be edited by any importer — widening or narrowing what
        this deployment accepts, and changing what ``media.container`` future
        content objects claim, for the life of the process. The read-only view
        makes every such attempt a ``TypeError`` at the point it is made.
        """
        with pytest.raises(TypeError):
            MEDIA_CONTAINERS["audio/flac"] = "flac"  # type: ignore[index]

        with pytest.raises(TypeError):
            del MEDIA_CONTAINERS["audio/mpeg"]  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            MEDIA_CONTAINERS.clear()  # type: ignore[attr-defined]

        with pytest.raises(AttributeError):
            MEDIA_CONTAINERS.update({"video/x-matroska": "matroska"})  # type: ignore[attr-defined]

        assert dict(MEDIA_CONTAINERS) == {
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/ogg": "ogg",
            "video/mp4": "mp4",
            "video/webm": "webm",
        }

    def test_extra_aliases_are_accepted_and_only_the_required_one_checked(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """An ISO base media file is a family, and describing it fully is not a fault."""
        probe = video_probe(container_names=("3g2", "3gp", "m4a", "mj2", "mov", "mp4"))

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata["media"]["container"] == "mp4"  # type: ignore[index,call-overload]

    def test_a_missing_required_alias_is_refused(self, store: InMemoryRawObjectStore) -> None:
        probe = video_probe(container_names=("matroska", "webm"))

        with pytest.raises(ProcessingInputError):
            VideoProcessor(store, probe).process(video_capture(store, mime_type="video/mp4"))

    def test_the_declaration_is_never_rewritten_to_match_the_bytes(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """No sniff-and-rewrite: a disagreement is a refusal, not a correction."""
        capture = audio_capture(store, mime_type="audio/wav")
        probe = audio_probe(container_names=("mp3",))

        with pytest.raises(ProcessingInputError):
            AudioProcessor(store, probe).process(capture)

        assert capture.raw_object is not None
        assert capture.raw_object.mime_type == "audio/wav"

    @pytest.mark.parametrize(
        "probe_builder",
        [
            lambda: audio_probe(container_names=("wav",)),
            lambda: audio_probe(container_names=("ogg",)),
        ],
        ids=["wav", "ogg"],
    )
    def test_the_mismatch_message_reveals_nothing_observed(
        self, store: InMemoryRawObjectStore, probe_builder: Any
    ) -> None:
        """One fixed generic sentence, identical whatever the probe saw.

        A message that varied with the observation would be a read oracle over
        someone's staged bytes.
        """
        with pytest.raises(ProcessingInputError) as raised:
            AudioProcessor(store, probe_builder()).process(audio_capture(store))

        message = str(raised.value)
        assert message == "media bytes do not match the declared supported format"
        for leaked in ("wav", "ogg", "mp3", "sha256", "audio/mpeg"):
            assert leaked not in message


class TestStructuralRequirements:
    """What each modality must carry, and the asymmetry between them."""

    def test_audio_requires_at_least_one_audio_stream(self, store: InMemoryRawObjectStore) -> None:
        probe = audio_probe(audio_streams=(), video_streams=(video_stream(index=0),))

        with pytest.raises(ProcessingInputError, match="lacks a verifiable audio stream"):
            AudioProcessor(store, probe).process(audio_capture(store))

    def test_audio_with_a_cover_art_video_stream_succeeds(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Embedded cover art is an ordinary MP3, described rather than refused."""
        probe = audio_probe(audio_streams=AUDIO_ONLY, video_streams=(video_stream(index=1),))

        content = AudioProcessor(store, probe).process(audio_capture(store))

        assert content.metadata["media"]["video_stream_count"] == 1  # type: ignore[index,call-overload]

    def test_video_requires_at_least_one_video_stream(self, store: InMemoryRawObjectStore) -> None:
        probe = video_probe(video_streams=(), audio_streams=(audio_stream(index=0),))

        with pytest.raises(ProcessingInputError, match="lacks a verifiable video stream"):
            VideoProcessor(store, probe).process(video_capture(store))

    def test_video_with_no_audio_stream_succeeds(self, store: InMemoryRawObjectStore) -> None:
        """A silent clip is valid video; requiring audio would invent a rule.

        The converse is *not* a rule either: audio in a ``VIDEO`` capture is
        perfectly valid and is described rather than refused — see
        ``test_video_with_audio_streams_succeeds`` below.
        """
        probe = video_probe(video_streams=VIDEO_ONLY, audio_streams=())

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata["media"]["audio_stream_count"] == 0  # type: ignore[index,call-overload]
        assert content.metadata["audio_streams"] == []

    @pytest.mark.parametrize("count", [1, 2, 3], ids=["one", "two", "three"])
    def test_video_with_audio_streams_succeeds(
        self, store: InMemoryRawObjectStore, count: int
    ) -> None:
        """Audio in a ``VIDEO`` capture is valid, and is described rather than refused.

        The requirement is one-sided: ``VIDEO`` needs a video stream and says
        nothing about audio. Most real video carries a soundtrack, so a rule
        rejecting it would refuse the ordinary case — this is the test that keeps
        "audio is optional" from drifting into "audio is forbidden".
        """
        audio = tuple(audio_stream(index=index) for index in range(1, count + 1))
        probe = video_probe(video_streams=(video_stream(index=0),), audio_streams=audio)

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata["media"]["audio_stream_count"] == count  # type: ignore[index,call-overload]
        assert len(content.metadata["audio_streams"]) == count  # type: ignore[arg-type]
        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_several_streams_of_each_kind_succeed(self, store: InMemoryRawObjectStore) -> None:
        """No primary stream is selected, and none is dropped."""
        probe = video_probe(
            audio_streams=(audio_stream(index=1), audio_stream(index=2)),
            video_streams=(video_stream(index=0), video_stream(index=3)),
        )

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata["media"]["audio_stream_count"] == 2  # type: ignore[index,call-overload]
        assert content.metadata["media"]["video_stream_count"] == 2  # type: ignore[index,call-overload]

    def test_the_stream_message_reveals_nothing_about_the_file(
        self, store: InMemoryRawObjectStore
    ) -> None:
        probe = audio_probe(audio_streams=())

        with pytest.raises(ProcessingInputError) as raised:
            AudioProcessor(store, probe).process(audio_capture(store))

        message = str(raised.value)
        assert message == "submitted audio lacks a verifiable audio stream"
        for leaked in ("mp3", "sha256", "container"):
            assert leaked not in message


class TestTheCanonicalObject:
    """What a successful media capture actually produces."""

    def test_audio_content_is_typed_audio_and_complete(self, store: InMemoryRawObjectStore) -> None:
        content = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        assert content.type is ContentType.AUDIO
        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_video_content_is_typed_video_and_complete(self, store: InMemoryRawObjectStore) -> None:
        content = VideoProcessor(store, video_probe()).process(video_capture(store))

        assert content.type is ContentType.VIDEO
        assert content.processing[0].status is ProcessingStatus.COMPLETE

    def test_there_are_no_segments(self, store: InMemoryRawObjectStore) -> None:
        """The decision the phase turns on: nothing listened, so nothing is claimed."""
        audio = AudioProcessor(store, audio_probe()).process(audio_capture(store))
        video = VideoProcessor(store, video_probe()).process(video_capture(store))

        assert audio.segments == []
        assert video.segments == []

    def test_the_original_is_the_only_asset(self, store: InMemoryRawObjectStore) -> None:
        content = VideoProcessor(store, video_probe()).process(video_capture(store))

        assert len(content.assets) == 1
        assert content.assets[0].role is AssetRole.ORIGINAL
        assert content.assets[0].mime_type == "video/mp4"

    def test_no_derived_binary_is_created(self, store: InMemoryRawObjectStore) -> None:
        """No extracted track, no keyframe, no thumbnail. Streams are metadata."""
        content = VideoProcessor(store, video_probe()).process(video_capture(store))

        roles = {asset.role for asset in content.assets}
        assert roles == {AssetRole.ORIGINAL}
        assert not roles & {AssetRole.AUDIO, AssetRole.KEYFRAME, AssetRole.THUMBNAIL}

    def test_the_original_reference_names_the_stored_bytes(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = audio_capture(store)
        assert capture.raw_object is not None

        content = AudioProcessor(store, audio_probe()).process(capture)

        assert content.original.sha256 == capture.raw_object.sha256
        assert content.original.asset_id == content.assets[0].id
        assert content.assets[0].ref == capture.raw_object.ref

    def test_the_title_is_the_submitted_one_or_none(self, store: InMemoryRawObjectStore) -> None:
        """No embedded tag, no artist, no album, no filename — this build reads none."""
        titled = AudioProcessor(store, audio_probe()).process(
            audio_capture(store, title="A recording")
        )
        untitled = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        assert titled.title == "A recording"
        assert untitled.title is None

    def test_the_content_names_the_capture_it_came_from(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = audio_capture(store)

        content = AudioProcessor(store, audio_probe()).process(capture)

        assert content.source.capture_id == capture.id
        assert content.source.provider == capture.source.provider
        assert content.source.url == capture.source.url


class TestTheProcessingRecord:
    """Exactly one record, naming this processor, with nothing else on it."""

    @pytest.mark.parametrize(
        ("processor_type", "capture_builder", "probe_builder", "name"),
        [
            (AudioProcessor, audio_capture, audio_probe, "audio"),
            (VideoProcessor, video_capture, video_probe, "video"),
        ],
        ids=["audio", "video"],
    )
    def test_one_complete_record_with_empty_warnings_and_errors(
        self,
        store: InMemoryRawObjectStore,
        processor_type: Any,
        capture_builder: Any,
        probe_builder: Any,
        name: str,
    ) -> None:
        content = processor_type(store, probe_builder()).process(capture_builder(store))

        assert len(content.processing) == 1
        record = content.processing[0]
        assert (record.processor, record.processor_version) == (name, "0.1")
        assert record.status is ProcessingStatus.COMPLETE
        assert record.warnings == []
        assert record.errors == []

    def test_missing_optional_metadata_is_not_a_warning(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """A container that declares less is a fact about the file, not a defect."""
        probe = audio_probe(
            duration_seconds=None,
            audio_streams=(audio_stream(index=0, codec=None, sample_rate=None, channels=None),),
        )

        content = AudioProcessor(store, probe).process(audio_capture(store))

        assert content.processing[0].status is ProcessingStatus.COMPLETE
        assert content.processing[0].warnings == []

    def test_the_run_is_bracketed_by_its_timestamps(self, store: InMemoryRawObjectStore) -> None:
        content = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        record = content.processing[0]
        assert record.completed_at is not None
        assert record.started_at <= record.completed_at


class TestTheMetadataShape:
    """The exact durable shape ADR-021 fixed, field by field."""

    def test_the_full_shape(self, store: InMemoryRawObjectStore) -> None:
        probe = video_probe(
            container_names=("mp4",),
            duration_seconds=61.5,
            audio_streams=(audio_stream(index=1, codec="aac", sample_rate=48000, channels=2),),
            video_streams=(
                video_stream(
                    index=0, codec="h264", width=1920, height=1080, frame_rate="30000/1001"
                ),
            ),
        )

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata == {
            "media": {
                "container": "mp4",
                "duration_seconds": 61.5,
                "audio_stream_count": 1,
                "video_stream_count": 1,
            },
            "audio_streams": [{"index": 1, "codec": "aac", "sample_rate": 48000, "channels": 2}],
            "video_streams": [
                {
                    "index": 0,
                    "codec": "h264",
                    "width": 1920,
                    "height": 1080,
                    "frame_rate": "30000/1001",
                }
            ],
        }

    def test_the_top_level_keys_are_exactly_three(self, store: InMemoryRawObjectStore) -> None:
        content = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        assert set(content.metadata) == {"media", "audio_streams", "video_streams"}

    def test_counts_are_derived_from_the_list_lengths(self, store: InMemoryRawObjectStore) -> None:
        """Never an independent observation. The lists remain the truth."""
        probe = video_probe(
            audio_streams=(audio_stream(index=1), audio_stream(index=2), audio_stream(index=3)),
            video_streams=(video_stream(index=0),),
        )

        content = VideoProcessor(store, probe).process(video_capture(store))

        media = content.metadata["media"]
        assert media["audio_stream_count"] == len(content.metadata["audio_streams"])  # type: ignore[index,call-overload,arg-type]
        assert media["video_stream_count"] == len(content.metadata["video_streams"])  # type: ignore[index,call-overload,arg-type]
        assert media["audio_stream_count"] == 3  # type: ignore[index,call-overload]

    def test_duration_is_omitted_when_the_container_declares_none(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Omitted entirely — not null, not zero."""
        probe = audio_probe(duration_seconds=None)

        content = AudioProcessor(store, probe).process(audio_capture(store))

        media = content.metadata["media"]
        assert "duration_seconds" not in media  # type: ignore[operator]
        assert media == {"container": "mp3", "audio_stream_count": 1, "video_stream_count": 0}

    def test_every_optional_stream_field_is_omitted_when_absent(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Only ``index`` survives, because only ``index`` is always observed."""
        probe = video_probe(
            audio_streams=(audio_stream(index=1, codec=None, sample_rate=None, channels=None),),
            video_streams=(
                video_stream(index=0, codec=None, width=None, height=None, frame_rate=None),
            ),
        )

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert content.metadata["audio_streams"] == [{"index": 1}]
        assert content.metadata["video_streams"] == [{"index": 0}]

    def test_an_empty_stream_list_stays_present(self, store: InMemoryRawObjectStore) -> None:
        """An observed absence, which omitting would make indistinguishable from unchecked."""
        content = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        assert content.metadata["video_streams"] == []
        assert content.metadata["media"]["video_stream_count"] == 0  # type: ignore[index,call-overload]

    def test_stream_order_is_the_normalized_order(self, store: InMemoryRawObjectStore) -> None:
        probe = video_probe(
            video_streams=(video_stream(index=0), video_stream(index=2), video_stream(index=5))
        )

        content = VideoProcessor(store, probe).process(video_capture(store))

        streams = cast(list[dict[str, Any]], content.metadata["video_streams"])
        indexes = [entry["index"] for entry in streams]
        assert indexes == [0, 2, 5]

    @pytest.mark.parametrize(
        "forbidden",
        [
            "engine",
            "engine_version",
            "bitrate",
            "language",
            "artist",
            "album",
            "tags",
            "title",
            "disposition",
            "default",
            "rotation",
            "chapters",
            "subtitles",
            "raw",
        ],
    )
    def test_no_forbidden_fact_is_recorded_anywhere(
        self, store: InMemoryRawObjectStore, forbidden: str
    ) -> None:
        probe = video_probe(audio_streams=(audio_stream(index=1),))

        content = VideoProcessor(store, probe).process(video_capture(store))

        assert forbidden not in repr(content.metadata)

    def test_the_metadata_survives_a_json_round_trip(self, store: InMemoryRawObjectStore) -> None:
        """It is stored as contract JSON, so every value must survive one."""
        from core.contracts import ContentObject

        content = VideoProcessor(store, video_probe()).process(video_capture(store))

        restored = ContentObject.model_validate_json(content.model_dump_json())

        assert restored.metadata == content.metadata


class TestPreconditions:
    """``process`` is callable directly and re-checks what routing would have asked."""

    def test_a_capture_of_the_wrong_payload_type_is_refused(
        self, store: InMemoryRawObjectStore
    ) -> None:
        with pytest.raises(ProcessingInputError):
            AudioProcessor(store, audio_probe()).process(video_capture(store))

    def test_a_capture_with_no_raw_object_is_refused(self, store: InMemoryRawObjectStore) -> None:
        capture = audio_capture(store).model_copy(update={"raw_object": None})

        with pytest.raises(ProcessingInputError, match="no raw object"):
            AudioProcessor(store, audio_probe()).process(capture)

    def test_a_raw_object_without_a_storage_reference_is_refused(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Reachable on a snapshot whose assignment was rejected after the write."""
        from core.contracts import RawObjectRef

        capture = audio_capture(store).model_copy(
            update={"raw_object": RawObjectRef(id="x" * 64, mime_type="audio/mpeg", ref=None)}
        )

        with pytest.raises(ProcessingInputError, match="without a storage reference"):
            AudioProcessor(store, audio_probe()).process(capture)

    def test_a_raw_object_declaring_no_mime_type_is_refused(
        self, store: InMemoryRawObjectStore
    ) -> None:
        capture = audio_capture(store, mime_type=None)

        with pytest.raises(ProcessingInputError, match="no mime_type"):
            AudioProcessor(store, audio_probe()).process(capture)

    def test_an_unclaimed_mime_type_is_refused(self, store: InMemoryRawObjectStore) -> None:
        capture = audio_capture(store, mime_type="audio/flac")

        with pytest.raises(ProcessingInputError):
            AudioProcessor(store, audio_probe()).process(capture)

    def test_a_raw_store_failure_propagates_and_the_probe_is_not_called(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """Storage failures keep their own type, exactly as for every processor.

        ``RawObjectNotFoundError`` describes the state of the store, not of the
        capture, and a caller deciding whether to retry needs it distinguishable
        from media that will never process. Nothing here catches it — the
        assertion is that no translation was added — and the probe never runs,
        because there was no stream to hand it.
        """
        from core.storage import RawObjectNotFoundError

        probe = audio_probe()
        capture = audio_capture(store)
        assert capture.raw_object is not None
        # A reference the store has never held: well-formed, and absent.
        missing = capture.raw_object.model_copy(
            update={"ref": build_raw_ref("b" * 64), "sha256": "b" * 64, "id": "b" * 64}
        )

        with pytest.raises(RawObjectNotFoundError):
            AudioProcessor(store, probe).process(capture.model_copy(update={"raw_object": missing}))

        assert probe.calls == []

    def test_preconditions_are_settled_before_the_probe_runs(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """A refusal knowable from the record alone never opens the bytes."""
        probe = audio_probe()

        with pytest.raises(ProcessingInputError):
            AudioProcessor(store, probe).process(audio_capture(store, mime_type="audio/flac"))

        assert probe.calls == []


class TestSchemaVersionOfTheResult:
    """A new document written by this build, whatever the capture's own version."""

    def test_new_content_carries_the_current_schema(self, store: InMemoryRawObjectStore) -> None:
        content = AudioProcessor(store, audio_probe()).process(audio_capture(store))

        assert content.schema_version == SCHEMA_VERSION == "0.3"

    @pytest.mark.parametrize("version", ["0.1", "0.2"])
    def test_a_historical_video_capture_produces_0_3_content(
        self, store: InMemoryRawObjectStore, version: str
    ) -> None:
        """The capture keeps its version; the content object is new and is 0.3.

        ``capture.schema_version`` is deliberately *not* copied across. The two
        describe different documents, and a stored 0.1 ``VIDEO`` record stays 0.1
        under ADR-002's compatibility promise while anything this build writes
        today is written at today's version.
        """
        # 0.1 predates the capture metadata fields and may not carry them; 0.2
        # requires ``context``. Both are historical records this build still reads.
        overrides: dict[str, Any] = {"schema_version": version}
        if version == "0.1":
            overrides["context"] = None
        capture = video_capture(store, **overrides)

        content = VideoProcessor(store, video_probe()).process(capture)

        assert capture.schema_version == version
        assert content.schema_version == "0.3"
