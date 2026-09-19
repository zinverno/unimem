"""Completed media replay, and the one thing it cannot prove.

The lost-response problem is the same as for text: the client POSTed, the reply
vanished, and resubmitting the identical envelope must not duplicate the
capture. For staged media the proof is *easier* than for text — the submitted
material is a content-addressed digest the client named, so "the same bytes" is
a comparison rather than a re-read — and one part is impossible.

**Media replay requires the incoming envelope to declare schema 0.3.** Intake
records every capture at the current version and nothing durably keeps the
version the request arrived with, so a legacy 0.1 or 0.2 ``VIDEO`` resubmission
has no stored fact that could prove it matches. Rather than add an
``ingress_schema_version`` column purely to serve a replay, such a duplicate
keeps the conflict it would always have received. The legacy capture is still
accepted and processed the *first* time; only the exact resubmission is a 409.

The whole path is read-only: no probe runs, no bytes are opened, nothing is
reprocessed, and nothing is written.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.storage import build_raw_ref
from tests.unit.processing.doubles import (
    FakeMediaProbe,
    audio_stream,
    probe_result,
    video_stream,
)
from unimem_api import build_local_app

MP3 = b"\xff\xfb\x90\x00fake mp3 payload"
OTHER = b"a different staged recording entirely"
MP3_FILE_REF = build_raw_ref(hashlib.sha256(MP3).hexdigest())
OTHER_FILE_REF = build_raw_ref(hashlib.sha256(OTHER).hexdigest())

AUDIO_ID = "cap_replay_audio"
VIDEO_ID = "cap_replay_video"
CAPTURED_AT = "2026-05-06T07:08:09+00:00"

GOOD_AUDIO = probe_result(
    container_names=("mp3",), duration_seconds=12.5, audio_streams=(audio_stream(index=0),)
)
GOOD_VIDEO = probe_result(
    container_names=("mp4",), duration_seconds=61.0, video_streams=(video_stream(index=0),)
)


def audio_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "0.3",
        "id": AUDIO_ID,
        "source": {"type": "upload", "provider": "curl", "url": "https://example.com/a"},
        "payload": {
            "type": "audio",
            "mime_type": "audio/mpeg",
            "file_ref": MP3_FILE_REF,
            "title": "A recording",
        },
        "context": {"captured_at": CAPTURED_AT, "device": "laptop"},
        "intent": {"action": "save", "collection": "listening", "tags": ["podcast"]},
    }
    return body | overrides


def video_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "0.3",
        "id": VIDEO_ID,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": "video",
            "mime_type": "video/mp4",
            "file_ref": MP3_FILE_REF,
            "title": "A clip",
        },
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


@pytest.fixture
def probe() -> FakeMediaProbe:
    return FakeMediaProbe(result=GOOD_AUDIO)


@pytest.fixture
def client(tmp_path: Path, probe: FakeMediaProbe) -> Any:
    with TestClient(build_local_app(tmp_path / "data", media_probe=probe)) as test_client:
        yield test_client


def stage(client: TestClient, data: bytes = MP3, mime_type: str = "audio/mpeg") -> None:
    assert client.post("/v1/uploads", files={"file": ("clip", data, mime_type)}).status_code == 200


@pytest.fixture
def submitted(client: TestClient) -> TestClient:
    """A completed audio capture, ready to be resubmitted.

    A *second*, unrelated object is staged too. Intake resolves material before
    it discovers the duplicate id, so a resubmission naming bytes nobody staged
    would be answered "material unavailable" and never reach the equivalence
    question at all — which would test the wrong thing.
    """
    stage(client)
    stage(client, OTHER, "audio/mpeg")
    assert client.post("/v1/captures", json=audio_body()).status_code == 201
    return client


class TestAnEquivalentMediaResubmission:
    """The lost response, answered with what the first attempt produced."""

    def test_audio_replay_is_200(self, submitted: TestClient) -> None:
        assert submitted.post("/v1/captures", json=audio_body()).status_code == 200

    def test_the_replay_names_the_same_capture_and_content(self, submitted: TestClient) -> None:
        first = submitted.get(f"/v1/captures/{AUDIO_ID}/content").json()

        replay = submitted.post("/v1/captures", json=audio_body()).json()

        assert replay["capture_id"] == AUDIO_ID
        assert replay["content_id"] == first["id"]

    def test_video_replay_is_200(self, tmp_path: Path) -> None:
        probe = FakeMediaProbe(result=GOOD_VIDEO)
        with TestClient(build_local_app(tmp_path / "d", media_probe=probe)) as client:
            stage(client, MP3, "video/mp4")
            assert client.post("/v1/captures", json=video_body()).status_code == 201

            assert client.post("/v1/captures", json=video_body()).status_code == 200

    def test_the_replay_never_probes_again(
        self, submitted: TestClient, probe: FakeMediaProbe
    ) -> None:
        """The whole point of a replay: the engine does not run a second time."""
        assert len(probe.calls) == 1

        submitted.post("/v1/captures", json=audio_body())

        assert len(probe.calls) == 1

    def test_the_replay_creates_no_second_capture_or_content(self, submitted: TestClient) -> None:
        before = submitted.get(f"/v1/captures/{AUDIO_ID}/content").json()

        submitted.post("/v1/captures", json=audio_body())
        after = submitted.get(f"/v1/captures/{AUDIO_ID}/content").json()

        assert after == before

    def test_replaying_twice_answers_the_same_way(self, submitted: TestClient) -> None:
        first = submitted.post("/v1/captures", json=audio_body())
        second = submitted.post("/v1/captures", json=audio_body())

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()


#: Every fact that must match, and a submission differing in exactly one of them.
DIFFERENT: dict[str, dict[str, Any]] = {
    "a different source": {
        "source": {"type": "upload", "provider": "curl", "url": "https://example.com/b"}
    },
    "a different context": {"context": {"captured_at": CAPTURED_AT, "device": "phone"}},
    "a different intent": {"intent": {"action": "analyze"}},
    "no intent at all": {"intent": None},
    "a different title": {
        "payload": {
            "type": "audio",
            "mime_type": "audio/mpeg",
            "file_ref": MP3_FILE_REF,
            "title": "Another recording",
        }
    },
    "a different mime type": {
        "payload": {
            "type": "audio",
            "mime_type": "audio/ogg",
            "file_ref": MP3_FILE_REF,
            "title": "A recording",
        }
    },
    "a different payload type": {
        "payload": {
            "type": "video",
            "mime_type": "video/mp4",
            "file_ref": MP3_FILE_REF,
            "title": "A recording",
        }
    },
    "a different file_ref": {
        "payload": {
            "type": "audio",
            "mime_type": "audio/mpeg",
            "file_ref": OTHER_FILE_REF,
            "title": "A recording",
        }
    },
}


class TestSameIdIsNotSameRequest:
    """A capture id is a claim, not proof. Every difference keeps the conflict."""

    @pytest.mark.parametrize("change", DIFFERENT.values(), ids=list(DIFFERENT))
    def test_a_changed_fact_is_a_conflict(
        self, submitted: TestClient, change: dict[str, Any]
    ) -> None:
        response = submitted.post("/v1/captures", json=audio_body(**change))

        assert response.status_code == 409

    @pytest.mark.parametrize("change", DIFFERENT.values(), ids=list(DIFFERENT))
    def test_a_changed_fact_never_reprocesses(
        self, submitted: TestClient, probe: FakeMediaProbe, change: dict[str, Any]
    ) -> None:
        submitted.post("/v1/captures", json=audio_body(**change))

        assert len(probe.calls) == 1

    def test_text_alongside_the_file_ref_is_refused_before_identity_is_considered(
        self, submitted: TestClient
    ) -> None:
        """Not a 409, and that is correct: intake refuses the envelope outright.

        A media payload carrying ``text`` beside its ``file_ref`` offers more
        material than one capture can hold, so it is rejected as an unsupported
        shape before anything asks whose capture id it is. Either way it is not
        granted a replay — it simply fails earlier, and more informatively.
        """
        response = submitted.post(
            "/v1/captures",
            json=audio_body(
                payload={
                    "type": "audio",
                    "mime_type": "audio/mpeg",
                    "file_ref": MP3_FILE_REF,
                    "text": "a transcript",
                    "title": "A recording",
                }
            ),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_payload"

    @pytest.mark.parametrize("version", ["0.1", "0.2"])
    def test_an_older_schema_version_cannot_even_be_expressed_for_audio(
        self, submitted: TestClient, version: str
    ) -> None:
        """``AUDIO`` is version-gated to 0.3, so there is no legacy audio request.

        The Phase 5A-1 contract gate rejects the envelope before delivery reaches
        a replay question. That is why the legacy-replay limit below is stated
        for ``VIDEO`` only: video is the one media type that *has* a history.
        """
        response = submitted.post("/v1/captures", json=audio_body(schema_version=version))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_capture_that_is_not_complete_is_a_conflict(self, tmp_path: Path) -> None:
        """An untrusted probe result leaves the capture ``PROCESSING``; a duplicate is 409."""
        from core.processing.media_probe import MediaProbeExecutionError

        probe = FakeMediaProbe(raises=MediaProbeExecutionError("gone"))
        with TestClient(build_local_app(tmp_path / "d", media_probe=probe)) as client:
            stage(client)
            assert client.post("/v1/captures", json=audio_body()).status_code == 503

            assert client.post("/v1/captures", json=audio_body()).status_code == 409


class TestLegacyVideoCanBeCapturedButNotReplayed:
    """The limit, stated as behaviour rather than worked around."""

    @pytest.mark.parametrize("version", ["0.1", "0.2"])
    def test_a_legacy_video_submission_is_accepted_and_processed(
        self, tmp_path: Path, version: str
    ) -> None:
        """Historical ``VIDEO`` is valid at 0.1 and 0.2, and still ingests."""
        probe = FakeMediaProbe(result=GOOD_VIDEO)
        with TestClient(build_local_app(tmp_path / "d", media_probe=probe)) as client:
            stage(client, MP3, "video/mp4")

            response = client.post("/v1/captures", json=video_body(schema_version=version))

            assert response.status_code == 201
            content = client.get(f"/v1/captures/{VIDEO_ID}/content").json()

        # The capture is recorded at the current version; the content is new.
        assert content["schema_version"] == "0.3"

    @pytest.mark.parametrize("version", ["0.1", "0.2"])
    def test_its_exact_resubmission_is_a_conflict(self, tmp_path: Path, version: str) -> None:
        """Nothing durable retains the ingress version, so equivalence is unprovable."""
        probe = FakeMediaProbe(result=GOOD_VIDEO)
        with TestClient(build_local_app(tmp_path / "d", media_probe=probe)) as client:
            stage(client, MP3, "video/mp4")
            body = video_body(schema_version=version)
            assert client.post("/v1/captures", json=body).status_code == 201

            assert client.post("/v1/captures", json=body).status_code == 409

    def test_no_ingress_version_was_added_to_persistence(self) -> None:
        """The column that would have made legacy replay provable is deliberately absent."""
        from core.contracts import CaptureRecord

        assert "ingress_schema_version" not in CaptureRecord.model_fields


class TestOtherModalitiesAreUnchanged:
    """Phase 5A-2 widened replay for media only."""

    def test_text_replay_still_works(self, tmp_path: Path) -> None:
        probe = FakeMediaProbe(result=GOOD_AUDIO)
        body = {
            "schema_version": "0.3",
            "id": "cap_text_replay",
            "source": {"type": "api", "provider": "curl"},
            "payload": {"type": "text", "mime_type": "text/plain", "text": "a note"},
            "context": {"captured_at": CAPTURED_AT},
        }
        with TestClient(build_local_app(tmp_path / "d", media_probe=probe)) as client:
            assert client.post("/v1/captures", json=body).status_code == 201

            assert client.post("/v1/captures", json=body).status_code == 200

    @pytest.mark.parametrize(
        ("payload_type", "mime_type"),
        [("document", "application/pdf"), ("image", "image/png")],
    )
    def test_document_and_image_are_still_not_replayable(
        self, tmp_path: Path, payload_type: str, mime_type: str
    ) -> None:
        """Their duplicates were always conflicts, and this phase did not change that."""
        from core.contracts import CaptureEnvelope, CaptureRecord, CaptureStatus
        from unimem_api.replay import _is_equivalent_replay

        envelope = CaptureEnvelope.model_validate(
            {
                "schema_version": "0.3",
                "id": "cap_other",
                "source": {"type": "upload", "provider": "curl"},
                "payload": {
                    "type": payload_type,
                    "mime_type": mime_type,
                    "file_ref": MP3_FILE_REF,
                },
                "context": {"captured_at": CAPTURED_AT},
            }
        )
        record = CaptureRecord.model_validate(
            {
                "schema_version": "0.3",
                "id": "cap_other",
                "status": CaptureStatus.COMPLETE.value,
                "received_at": CAPTURED_AT,
                "source": {"type": "upload", "provider": "curl"},
                "payload_type": payload_type,
                "context": {"captured_at": CAPTURED_AT},
            }
        )

        assert not _is_equivalent_replay(envelope, record)


class TestThePredicateDirectly:
    """The defensive branches, exercised where the HTTP stack cannot reach them.

    Several of these are unreachable through a live request — intake or the
    contracts refuse first — but ``is_equivalent_media_replay`` is a public
    predicate that must be sound on its own terms. A validated snapshot is not a
    continuously enforced object, so it checks rather than assumes.
    """

    @staticmethod
    def envelope_and_record(
        **record_overrides: Any,
    ) -> tuple[Any, Any]:
        from core.contracts import (
            CaptureEnvelope,
            CaptureRecord,
            CaptureStatus,
            RawObjectRef,
        )

        digest = hashlib.sha256(MP3).hexdigest()
        envelope = CaptureEnvelope.model_validate(audio_body())
        fields: dict[str, Any] = {
            "schema_version": "0.3",
            "id": AUDIO_ID,
            "status": CaptureStatus.COMPLETE,
            "received_at": CAPTURED_AT,
            "source": envelope.source,
            "payload_type": envelope.payload.type,
            "context": envelope.context,
            "intent": envelope.intent,
            "title": envelope.payload.title,
            "raw_object": RawObjectRef(
                id=digest, mime_type="audio/mpeg", sha256=digest, ref=MP3_FILE_REF
            ),
        }
        return envelope, CaptureRecord(**(fields | record_overrides))

    def test_the_matching_pair_is_a_replay(self) -> None:
        """The baseline the negative cases each break one fact of."""
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record()

        assert is_equivalent_media_replay(envelope, record)

    def test_a_different_capture_id_is_not(self) -> None:
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record(id="cap_other")

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_at_an_older_schema_version_is_not(self) -> None:
        """A 0.3 request cannot be proven against a record an older build wrote."""
        from core.contracts import CapturePayloadType
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record(
            schema_version="0.2", payload_type=CapturePayloadType.VIDEO
        )
        envelope = envelope.model_copy(
            update={"payload": envelope.payload.model_copy(update={"type": "video"})}
        )

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_of_a_different_payload_type_is_not(self) -> None:
        from core.contracts import CapturePayloadType
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record(payload_type=CapturePayloadType.VIDEO)

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_with_no_raw_object_is_not(self) -> None:
        """A ``COMPLETE`` record without one cannot vouch for any bytes."""
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record(raw_object=None)

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_whose_ref_and_digest_disagree_is_not(self) -> None:
        """The store does not get to pick one, and neither does a replay."""
        from core.contracts import RawObjectRef
        from unimem_api.replay import is_equivalent_media_replay

        other = hashlib.sha256(OTHER).hexdigest()
        envelope, record = self.envelope_and_record(
            raw_object=RawObjectRef(
                id=other, mime_type="audio/mpeg", sha256=other, ref=MP3_FILE_REF
            )
        )

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_whose_id_alone_disagrees_is_not(self) -> None:
        """All three durable spellings must agree, not a convenient two.

        A raw object writes its content digest three times — in ``id``, in
        ``sha256``, and inside ``ref``. Here the last two match the submitted
        ``file_ref`` perfectly and only ``id`` is wrong, which is exactly the case
        a two-of-three check would wave through. Such a record contradicts itself
        about which bytes the capture was built from, and a replay granted on it
        would hand back content on the strength of an identity nobody can pin
        down.
        """
        from core.contracts import RawObjectRef
        from unimem_api.replay import is_equivalent_media_replay

        digest = hashlib.sha256(MP3).hexdigest()
        other = hashlib.sha256(OTHER).hexdigest()
        envelope, record = self.envelope_and_record(
            raw_object=RawObjectRef(
                id=other, mime_type="audio/mpeg", sha256=digest, ref=MP3_FILE_REF
            )
        )

        # Precisely the two-of-three situation: only ``id`` is out of step.
        assert record.raw_object is not None
        assert record.raw_object.sha256 == digest
        assert record.raw_object.ref == MP3_FILE_REF
        assert record.raw_object.id != digest

        assert not is_equivalent_media_replay(envelope, record)

    def test_a_record_whose_sha256_alone_disagrees_is_not(self) -> None:
        """The mirror case, so no single spelling is the one that is trusted."""
        from core.contracts import RawObjectRef
        from unimem_api.replay import is_equivalent_media_replay

        digest = hashlib.sha256(MP3).hexdigest()
        other = hashlib.sha256(OTHER).hexdigest()
        envelope, record = self.envelope_and_record(
            raw_object=RawObjectRef(
                id=digest, mime_type="audio/mpeg", sha256=other, ref=MP3_FILE_REF
            )
        )

        assert not is_equivalent_media_replay(envelope, record)

    def test_all_three_spellings_agreeing_is_what_grants_the_replay(self) -> None:
        """The positive control for the three negatives above."""
        from unimem_api.replay import is_equivalent_media_replay

        digest = hashlib.sha256(MP3).hexdigest()
        envelope, record = self.envelope_and_record()

        assert record.raw_object is not None
        assert record.raw_object.id == digest
        assert record.raw_object.sha256 == digest
        assert record.raw_object.ref == MP3_FILE_REF

        assert is_equivalent_media_replay(envelope, record)

    def test_an_unparseable_incoming_file_ref_is_not(self) -> None:
        """Unprovable and disproven land in the same place: no replay."""
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record()
        broken = envelope.model_copy(
            update={
                "payload": envelope.payload.model_copy(
                    update={"file_ref": "/home/someone/private.mp3"}
                )
            }
        )

        assert not is_equivalent_media_replay(broken, record)

    def test_a_missing_file_ref_is_not(self) -> None:
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record()
        stripped = envelope.model_copy(
            update={"payload": envelope.payload.model_copy(update={"file_ref": None})}
        )

        assert not is_equivalent_media_replay(stripped, record)

    def test_material_the_record_cannot_represent_is_not(self) -> None:
        """``text`` or ``html`` beside the reference is something the record never saw.

        Intake refuses these shapes outright, so a live request never reaches
        here — but the predicate must still refuse rather than ignore them,
        because an unprovable equivalence is not a replay.
        """
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record()
        for extra in ({"text": "a transcript"}, {"html": "<p>notes</p>"}):
            smuggled = envelope.model_copy(
                update={"payload": envelope.payload.model_copy(update=extra)}
            )

            assert not is_equivalent_media_replay(smuggled, record)

    def test_a_non_media_envelope_is_not(self) -> None:
        """The predicate is defined for audio and video and says nothing else."""
        from unimem_api.replay import is_equivalent_media_replay

        envelope, record = self.envelope_and_record()
        text = envelope.model_copy(
            update={
                "payload": envelope.payload.model_copy(
                    update={"type": "text", "text": "a note", "file_ref": None}
                )
            }
        )

        assert not is_equivalent_media_replay(text, record)
