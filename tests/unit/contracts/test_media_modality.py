"""First-class audio at schema 0.3, and the video compatibility it must not break.

Two rules are being pinned, and they pull in opposite directions on purpose.

``audio`` is **new** vocabulary. It arrived with schema version 0.3, no document
written before that could have carried it, and a 0.1 or 0.2 document claiming it
is wrong about its own shape — exactly as a 0.1 ``CaptureRecord`` carrying a
``title`` is wrong about its own shape. So the contracts refuse it there.

``video`` is **old** vocabulary. ``CapturePayloadType.VIDEO`` and
``ContentType.VIDEO`` have been in the closed sets since Phase 0A, at schema
0.1. Nothing may gate them now: a stored 0.1 ``VIDEO`` document is historically
valid, and a build that stopped reading it would be breaking the compatibility
promise ADR-002 makes, in order to tidy up a modality it is not even about.

**Schema support is not deployment capability.** Everything below says the
contracts have the words. Nothing below says a deployment can ingest audio.
Phase 5A-2 added the processors and an opt-in intake gate, so a build *handed a*
:class:`~core.processing.media_probe.MediaProbe` now can — and the default build
still cannot, refusing every media capture at the door. The two facts stay
separate, and ``test_the_modality_has_processors_but_no_engine`` is what keeps
them from being confused.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from core.contracts import (
    AUDIO_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SCHEMA_VERSIONS_BEFORE_AUDIO,
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
    ContentSource,
    ContentType,
    OriginalReference,
    SchemaVersion,
)

CAPTURED_AT = datetime(2026, 9, 1, 10, 11, 12, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 9, 1, 10, 11, 30, tzinfo=UTC)

FILE_REF = "sha256:" + "a" * 64

#: Every supported version that predates the modality, and every supported
#: version full stop. Both are written out rather than derived so the parameters
#: carry the contract's own ``SchemaVersion`` type; the test below is what stops
#: either list going stale.
VERSIONS_BEFORE_AUDIO: list[SchemaVersion] = ["0.1", "0.2"]
EVERY_VERSION: list[SchemaVersion] = ["0.1", "0.2", "0.3"]


def test_the_parametrized_versions_are_the_ones_the_contracts_name() -> None:
    assert frozenset(VERSIONS_BEFORE_AUDIO) == SCHEMA_VERSIONS_BEFORE_AUDIO


#: The two time-based modalities, and the one fact that separates them: which
#: schema versions may name each.
TIME_BASED = [
    (CapturePayloadType.AUDIO, ContentType.AUDIO, "audio"),
    (CapturePayloadType.VIDEO, ContentType.VIDEO, "video"),
]


def media_payload(payload_type: CapturePayloadType, **overrides: Any) -> CapturePayload:
    fields: dict[str, Any] = {
        "type": payload_type,
        "mime_type": "audio/mpeg" if payload_type is CapturePayloadType.AUDIO else "video/mp4",
        "file_ref": FILE_REF,
    }
    return CapturePayload(**(fields | overrides))


def envelope_fields(payload_type: CapturePayloadType) -> dict[str, Any]:
    return {
        "id": "cap_media_01",
        "source": CaptureSource(type=CaptureSourceType.UPLOAD, provider="curl"),
        "payload": media_payload(payload_type),
        "context": CaptureContext(captured_at=CAPTURED_AT),
    }


def record_fields(payload_type: CapturePayloadType) -> dict[str, Any]:
    return {
        "id": "cap_media_01",
        "status": CaptureStatus.STORED,
        "received_at": RECEIVED_AT,
        "source": CaptureSource(type=CaptureSourceType.UPLOAD, provider="curl"),
        "payload_type": payload_type,
    }


def content_fields(content_type: ContentType) -> dict[str, Any]:
    return {
        "id": "con_media_01",
        "type": content_type,
        "source": ContentSource(capture_id="cap_media_01"),
        "original": OriginalReference(mime_type="audio/mpeg"),
    }


class TestAudioIsAStagedPayload:
    """``AUDIO`` is shaped like the other staged modalities, and only like them."""

    def test_an_audio_payload_requires_a_file_ref(self) -> None:
        """Time-based media is never inline, so there is no second shape."""
        with pytest.raises(ValidationError, match="audio payload requires file_ref"):
            CapturePayload(type=CapturePayloadType.AUDIO, mime_type="audio/mpeg")

    def test_inline_text_does_not_stand_in_for_one(self) -> None:
        with pytest.raises(ValidationError, match="audio payload requires file_ref"):
            CapturePayload(type=CapturePayloadType.AUDIO, text="a transcript")

    def test_an_audio_payload_with_a_file_ref_is_valid(self) -> None:
        payload = media_payload(CapturePayloadType.AUDIO)

        assert payload.type is CapturePayloadType.AUDIO
        assert payload.file_ref == FILE_REF

    def test_the_payload_itself_carries_no_version_of_its_own(self) -> None:
        """A nested contract participates in its document's version (ADR-002).

        So ``CapturePayload`` cannot gate ``audio``, and the rule lives on the
        envelope. This is the assertion that says the absence is deliberate.
        """
        assert "schema_version" not in CapturePayload.model_fields


class TestAudioNeedsSchema03:
    """The gate, on each of the three versioned contracts."""

    @pytest.mark.parametrize("version", VERSIONS_BEFORE_AUDIO)
    def test_an_audio_envelope_is_rejected_before_0_3(self, version: SchemaVersion) -> None:
        with pytest.raises(ValidationError, match="has no audio payload type"):
            CaptureEnvelope(
                **envelope_fields(CapturePayloadType.AUDIO),
                schema_version=version,
            )

    @pytest.mark.parametrize("version", VERSIONS_BEFORE_AUDIO)
    def test_an_audio_record_is_rejected_before_0_3(self, version: SchemaVersion) -> None:
        with pytest.raises(ValidationError, match="has no audio payload type"):
            CaptureRecord(
                **record_fields(CapturePayloadType.AUDIO),
                schema_version=version,
                context=CaptureContext(captured_at=CAPTURED_AT) if version != "0.1" else None,
            )

    @pytest.mark.parametrize("version", VERSIONS_BEFORE_AUDIO)
    def test_an_audio_content_object_is_rejected_before_0_3(self, version: SchemaVersion) -> None:
        with pytest.raises(ValidationError, match="has no audio content type"):
            ContentObject(
                **content_fields(ContentType.AUDIO),
                schema_version=version,
            )

    @pytest.mark.parametrize("version", VERSIONS_BEFORE_AUDIO)
    def test_the_message_names_the_version_that_introduced_it(self, version: SchemaVersion) -> None:
        with pytest.raises(ValidationError, match=f"it was added in {AUDIO_SCHEMA_VERSION}"):
            CaptureEnvelope(
                **envelope_fields(CapturePayloadType.AUDIO),
                schema_version=version,
            )

    def test_an_audio_envelope_is_valid_at_0_3(self) -> None:
        envelope = CaptureEnvelope(
            **envelope_fields(CapturePayloadType.AUDIO),
            schema_version="0.3",
        )

        assert envelope.schema_version == "0.3"
        assert envelope.payload.type is CapturePayloadType.AUDIO

    def test_an_audio_record_is_valid_at_0_3(self) -> None:
        record = CaptureRecord(
            **record_fields(CapturePayloadType.AUDIO),
            schema_version="0.3",
            context=CaptureContext(captured_at=CAPTURED_AT),
        )

        assert record.schema_version == "0.3"
        assert record.payload_type is CapturePayloadType.AUDIO

    def test_an_audio_content_object_is_valid_at_0_3(self) -> None:
        content = ContentObject(
            **content_fields(ContentType.AUDIO),
            schema_version="0.3",
        )

        assert content.schema_version == "0.3"
        assert content.type is ContentType.AUDIO
        assert content.segments == []

    def test_the_default_version_already_admits_audio(self) -> None:
        """No explicit version needed: a newly built contract is 0.3."""
        envelope = CaptureEnvelope(**envelope_fields(CapturePayloadType.AUDIO))

        assert envelope.schema_version == SCHEMA_VERSION == "0.3"

    def test_an_audio_document_round_trips_through_json(self) -> None:
        envelope = CaptureEnvelope(**envelope_fields(CapturePayloadType.AUDIO))

        restored = CaptureEnvelope.model_validate_json(envelope.model_dump_json())

        assert restored == envelope
        assert restored.model_dump(mode="json")["payload"]["type"] == "audio"


class TestVideoStaysHistoricallyValid:
    """``video`` predates every rule above and must not acquire one."""

    @pytest.mark.parametrize("version", EVERY_VERSION)
    def test_a_video_envelope_is_valid_at_every_supported_version(
        self, version: SchemaVersion
    ) -> None:
        envelope = CaptureEnvelope(
            **envelope_fields(CapturePayloadType.VIDEO),
            schema_version=version,
        )

        assert envelope.schema_version == version
        assert envelope.payload.type is CapturePayloadType.VIDEO

    @pytest.mark.parametrize("version", EVERY_VERSION)
    def test_a_video_record_is_valid_at_every_supported_version(
        self, version: SchemaVersion
    ) -> None:
        record = CaptureRecord(
            **record_fields(CapturePayloadType.VIDEO),
            schema_version=version,
            context=None if version == "0.1" else CaptureContext(captured_at=CAPTURED_AT),
        )

        assert record.schema_version == version
        assert record.payload_type is CapturePayloadType.VIDEO

    @pytest.mark.parametrize("version", EVERY_VERSION)
    def test_a_video_content_object_is_valid_at_every_supported_version(
        self, version: SchemaVersion
    ) -> None:
        content = ContentObject(
            **content_fields(ContentType.VIDEO),
            schema_version=version,
        )

        assert content.schema_version == version
        assert content.type is ContentType.VIDEO

    def test_a_video_payload_still_requires_a_file_ref(self) -> None:
        """Unchanged by this slice; asserted so a shared match arm cannot drop it."""
        with pytest.raises(ValidationError, match="video payload requires file_ref"):
            CapturePayload(type=CapturePayloadType.VIDEO, mime_type="video/mp4")


class TestAudioAndVideoAreDistinct:
    """Standalone audio is its own modality, not a video with no pictures."""

    def test_they_are_separate_members_with_separate_wire_values(self) -> None:
        payload_values = {member.name: member.value for member in CapturePayloadType}
        content_values = {member.name: member.value for member in ContentType}

        assert payload_values["AUDIO"] == "audio"
        assert payload_values["VIDEO"] == "video"
        assert content_values["AUDIO"] == "audio"
        assert content_values["VIDEO"] == "video"

    def test_the_audio_asset_role_is_a_different_thing_entirely(self) -> None:
        """``AssetRole.AUDIO`` has existed since 0.1 and is not this modality.

        It names a *part* of some other content object — an audio track pulled
        out of a video — and Phase 5A extracts none. A reader must not take the
        two ``"audio"`` strings for one concept, which is why nothing here
        derives one from the other.
        """
        from core.contracts import AssetRole

        assert AssetRole.AUDIO.value == ContentType.AUDIO.value
        assert {member.value for member in AssetRole} != {member.value for member in ContentType}
        assert "AUDIO" in AssetRole.__members__
        assert "AUDIO" in ContentType.__members__


def test_the_modality_has_processors_but_no_engine() -> None:
    """What Phase 5A-2 changed here, and what it deliberately did not.

    Phase 5A-1 asserted that *nothing* normalized audio or video: the contracts
    had the vocabulary and the system had no use for it. Phase 5A-2 supplies the
    policy, so ``AudioProcessor`` and ``VideoProcessor`` now exist — and the half
    that has not moved is the one still worth pinning. There is no engine, no
    concrete adapter, and no ``unimem_media`` package; a deployment reaches media
    only by being handed a
    :class:`~core.processing.media_probe.MediaProbe` explicitly, and the default
    one still refuses every media capture at intake.

    That intake refusal is asserted where intake's refusals live, in
    ``tests/unit/intake/test_failures.py``.
    """
    import importlib.util

    import core.processing as processing

    assert hasattr(processing, "AudioProcessor")
    assert hasattr(processing, "VideoProcessor")

    assert not hasattr(processing, "FfprobeMediaProbe")
    assert importlib.util.find_spec("unimem_media") is None
