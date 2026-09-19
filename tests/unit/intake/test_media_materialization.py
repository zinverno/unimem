"""The media capability gate, and the staged path behind it.

Media is the first modality a deployment may or may not have, because
processing it needs an engine that is not part of this package. So intake grew
one boolean, ``media_enabled``, and two things are being pinned here.

**The default is off, and off means off before anything is touched.** A build
without the capability refuses a media envelope before it looks at the declared
format, before it parses the ``file_ref``, before it asks the raw store
anything, and before it reads the clock. That ordering is the guarantee rather
than an optimization: a refusal that varied with what happens to be staged would
let a client probe the object store through the shape of an error.

**With the capability on, media is the staged path documents and images already
use** — resolve, prove the bytes are there, write no second copy — and intake
still opens nothing, sniffs nothing, and probes nothing.
"""

import hashlib
from typing import Any

import pytest

from core.contracts import CaptureEnvelope, CapturePayload, CapturePayloadType, CaptureStatus
from core.intake import (
    AUDIO_MIME_TYPES,
    VIDEO_MIME_TYPES,
    CaptureIntake,
    CaptureMaterialUnavailableError,
    UnsupportedCapturePayloadError,
)
from core.storage import build_raw_ref
from tests.unit.intake.builders import (
    RECEIVED_AT,
    UPDATED_AT,
    make_audio_envelope,
    make_audio_payload,
    make_video_envelope,
    make_video_payload,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore

MEDIA_BYTES = b"fake staged media payload"
MEDIA_DIGEST = hashlib.sha256(MEDIA_BYTES).hexdigest()
MEDIA_FILE_REF = build_raw_ref(MEDIA_DIGEST)


def audio_payload(**overrides: Any) -> CapturePayload:
    """An audio payload naming the bytes the ``staged`` fixture holds."""
    return make_audio_payload(**({"file_ref": MEDIA_FILE_REF} | overrides))


def video_payload(**overrides: Any) -> CapturePayload:
    """A video payload naming the bytes the ``staged`` fixture holds."""
    return make_video_payload(**({"file_ref": MEDIA_FILE_REF} | overrides))


def audio_envelope(**overrides: Any) -> CaptureEnvelope:
    return make_audio_envelope(**({"payload": audio_payload()} | overrides))


def video_envelope(**overrides: Any) -> CaptureEnvelope:
    return make_video_envelope(**({"payload": video_payload()} | overrides))


#: Every media modality, so each rule is asserted for both rather than for audio
#: with video assumed to follow.
MODALITIES = [
    (audio_envelope, audio_payload, "audio"),
    (video_envelope, video_payload, "video"),
]
MODALITY_IDS = ["audio", "video"]


@pytest.fixture
def media_intake(
    raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore, clock: FakeClock
) -> CaptureIntake:
    """An intake that *does* have the media capability."""
    return CaptureIntake(raw_store, record_store, now=clock, media_enabled=True)


@pytest.fixture
def staged(raw_store: FakeRawObjectStore, journal: list[str]) -> FakeRawObjectStore:
    """A raw store already holding the media, as an upload would have left it.

    The staging write is erased from the journal and the write log the assertions
    read: it happened before the capture, through a different caller, and
    counting it as one of intake's calls would hide the thing under test.
    """
    raw_store.store_bytes(MEDIA_BYTES, mime_type="audio/mpeg")
    journal.clear()
    raw_store.writes.clear()
    return raw_store


class TestTheCapabilityDefaultsOff:
    """Today's deployment, unchanged."""

    def test_media_enabled_defaults_to_false(self) -> None:
        import inspect

        parameter = inspect.signature(CaptureIntake.__init__).parameters["media_enabled"]

        assert parameter.default is False

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_a_valid_media_envelope_is_refused(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        """Contract-valid, fully staged, and still unsupported: capability, not validity."""
        with pytest.raises(UnsupportedCapturePayloadError, match="no media capability"):
            intake.accept(builder())

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_the_refusal_names_the_capability_and_not_the_submission(
        self, intake: CaptureIntake, builder: Any, _payload: Any, label: str
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(builder())

        message = str(raised.value)
        assert MEDIA_FILE_REF not in message
        assert MEDIA_DIGEST not in message


class TestRefusalHappensBeforeAnythingIsTouched:
    """The ordering guarantee, asserted by what did *not* happen."""

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_no_store_is_read_or_written_and_no_clock_is_read(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        clock: FakeClock,
        journal: list[str],
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(builder())

        assert journal == []
        assert clock.reads == []
        assert record_store.created == []

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_it_holds_when_the_staged_reference_names_nothing(
        self,
        intake: CaptureIntake,
        journal: list[str],
        clock: FakeClock,
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        """Nothing is staged at all, and the answer is the identical one.

        A build with no capability must not touch storage to discover which way
        to say no — so the refusal cannot depend on whether the bytes are there.
        """
        with pytest.raises(UnsupportedCapturePayloadError, match="no media capability"):
            intake.accept(builder())

        assert journal == []
        assert clock.reads == []

    @pytest.mark.parametrize(
        ("payload_builder", "mime_type"),
        [(audio_payload, "audio/flac"), (video_payload, "video/quicktime")],
        ids=MODALITY_IDS,
    )
    def test_it_holds_for_a_mime_type_that_would_be_unsupported_anyway(
        self,
        intake: CaptureIntake,
        journal: list[str],
        clock: FakeClock,
        payload_builder: Any,
        mime_type: str,
    ) -> None:
        """Capability refusal has priority over media-specific validation."""
        base = make_audio_envelope()
        envelope = CaptureEnvelope(
            id="cap_media_gate",
            source=base.source,
            context=base.context,
            payload=payload_builder(mime_type=mime_type),
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no media capability"):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []

    def test_it_holds_for_an_unparseable_file_ref(
        self, intake: CaptureIntake, journal: list[str]
    ) -> None:
        """A path-shaped ``file_ref`` is never even parsed, let alone resolved."""
        envelope = make_audio_envelope(payload=audio_payload(file_ref="/home/someone/private.mp3"))

        with pytest.raises(UnsupportedCapturePayloadError, match="no media capability"):
            intake.accept(envelope)

        assert journal == []


class TestEnabledMediaUsesTheStagedPath:
    """With the capability on, exactly the document and image acquisition boundary."""

    @pytest.mark.parametrize("mime_type", AUDIO_MIME_TYPES)
    def test_every_audio_format_is_accepted(
        self, media_intake: CaptureIntake, staged: FakeRawObjectStore, mime_type: str
    ) -> None:
        stored = media_intake.accept(
            make_audio_envelope(payload=audio_payload(mime_type=mime_type))
        )

        assert stored.status is CaptureStatus.STORED
        assert stored.payload_type is CapturePayloadType.AUDIO
        assert stored.raw_object is not None
        assert stored.raw_object.mime_type == mime_type

    @pytest.mark.parametrize("mime_type", VIDEO_MIME_TYPES)
    def test_every_video_format_is_accepted(
        self, media_intake: CaptureIntake, staged: FakeRawObjectStore, mime_type: str
    ) -> None:
        stored = media_intake.accept(
            make_video_envelope(payload=video_payload(mime_type=mime_type))
        )

        assert stored.status is CaptureStatus.STORED
        assert stored.payload_type is CapturePayloadType.VIDEO
        assert stored.raw_object is not None
        assert stored.raw_object.mime_type == mime_type

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_the_record_points_at_the_bytes_that_were_already_staged(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        stored = media_intake.accept(builder())

        assert stored.raw_object is not None
        assert stored.raw_object.sha256 == MEDIA_DIGEST
        assert stored.raw_object.ref == MEDIA_FILE_REF

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_no_second_copy_of_the_bytes_is_written(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        """The original is immutable and content-addressed already."""
        media_intake.accept(builder())

        assert staged.writes == []
        assert "raw.store_bytes" not in journal
        assert "raw.store_stream" not in journal

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_the_receipt_is_durable_before_the_stored_snapshot(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        stored = media_intake.accept(builder())

        assert [entry for entry in journal if entry.startswith("records.")] == [
            "records.create",
            "records.replace",
        ]
        assert stored.received_at == RECEIVED_AT
        assert stored.updated_at == UPDATED_AT

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_the_submitted_title_survives(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        stored = media_intake.accept(builder())

        assert stored.title == builder().payload.title

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_intake_never_opens_the_material(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        """It proves the bytes are there and looks at none of them."""
        media_intake.accept(builder())

        assert "raw.open" not in journal


class TestEnabledMediaStillRefusesBadShapes:
    """Capability on does not mean anything goes."""

    @pytest.mark.parametrize(
        ("payload_builder", "mime_type"),
        [(audio_payload, "audio/flac"), (video_payload, "video/quicktime")],
        ids=MODALITY_IDS,
    )
    def test_an_unsupported_format_is_refused_before_any_side_effect(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        clock: FakeClock,
        payload_builder: Any,
        mime_type: str,
    ) -> None:
        base = make_audio_envelope()
        envelope = CaptureEnvelope(
            id="cap_media_bad_mime",
            source=base.source,
            context=base.context,
            payload=payload_builder(mime_type=mime_type),
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no processor for"):
            media_intake.accept(envelope)

        assert record_store.created == []
        assert clock.reads == []

    @pytest.mark.parametrize(("builder", "payload_builder", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_a_missing_mime_type_is_refused(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        builder: Any,
        payload_builder: Any,
        label: str,
    ) -> None:
        """Nothing is inferred from a container this boundary has not probed."""
        with pytest.raises(UnsupportedCapturePayloadError, match="no mime_type"):
            media_intake.accept(builder(payload=payload_builder(mime_type=None)))

        assert record_store.created == []

    @pytest.mark.parametrize(("builder", "payload_builder", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_text_alongside_the_file_ref_is_refused(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        builder: Any,
        payload_builder: Any,
        label: str,
    ) -> None:
        """One raw original per capture; this build will not choose between two."""
        with pytest.raises(UnsupportedCapturePayloadError, match="one raw original"):
            media_intake.accept(builder(payload=payload_builder(text="a transcript")))

        assert record_store.created == []

    @pytest.mark.parametrize(("builder", "payload_builder", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_html_alongside_the_file_ref_is_refused(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        builder: Any,
        payload_builder: Any,
        label: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError, match="one raw original"):
            media_intake.accept(builder(payload=payload_builder(html="<p>notes</p>")))

    @pytest.mark.parametrize(
        "file_ref",
        [
            "/home/someone/private.mp3",
            "file:///etc/passwd",
            "https://example.com/clip.mp4",
            "s3://bucket/key",
            "sha256:not-hex",
        ],
    )
    def test_a_reference_this_build_cannot_resolve_is_refused_and_never_fetched(
        self,
        media_intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        file_ref: str,
    ) -> None:
        """The one line between a client string and a local-file-read primitive."""
        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            media_intake.accept(make_audio_envelope(payload=audio_payload(file_ref=file_ref)))

        assert file_ref not in str(raised.value)
        assert journal == []

    @pytest.mark.parametrize(("builder", "_payload", "label"), MODALITIES, ids=MODALITY_IDS)
    def test_a_well_formed_reference_naming_nothing_is_material_unavailable(
        self,
        media_intake: CaptureIntake,
        record_store: FakeCaptureRecordStore,
        builder: Any,
        _payload: Any,
        label: str,
    ) -> None:
        """The envelope is right; the bytes have not been staged. A different error."""
        with pytest.raises(CaptureMaterialUnavailableError):
            media_intake.accept(builder())

        assert record_store.created == []
