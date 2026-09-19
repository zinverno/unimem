"""Media over the real HTTP stack: what succeeds, what fails, and how.

The distinction this file exists to pin is the one that is easiest to collapse
and worst to get wrong:

* a **deterministic verdict about the bytes** — the container contradicts the
  declared type, or the required stream is absent — is a ``ProcessingInputError``,
  answered **422 ``processing_failed``**, and the capture is durably ``FAILED``.
  The same submission will fail the same way forever, so a terminal state is
  honest.
* a **probe that produced no trusted structural result** is a
  ``MediaProbeExecutionError``, answered **503 ``media_probe_unavailable``**, and
  the capture stays ``PROCESSING``. Without a result this build trusts there is
  no basis for a verdict, so marking it failed would record a conclusion nobody
  reached. Note what is *not* claimed: not that the bytes went unexamined — the
  failure may arrive before, during, or after reading them — not that the cause
  is transient, and not that a retry would succeed.

Either way no content object is persisted, and the fixed 503 sentence reveals
nothing the probe saw.

Everything runs against a fake probe. No ``ffprobe``, no subprocess, no media
fixture, and no real container anywhere.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.contracts import CaptureStatus, ContentType, ProcessingStatus
from core.processing.media_probe import MediaProbeExecutionError
from core.storage import build_raw_ref
from tests.unit.processing.doubles import (
    FakeMediaProbe,
    audio_stream,
    probe_result,
    video_stream,
)
from unimem_api import build_local_app

MP3 = b"\xff\xfb\x90\x00fake mp3 payload"
MP3_FILE_REF = build_raw_ref(hashlib.sha256(MP3).hexdigest())

AUDIO_ID = "cap_media_audio"
VIDEO_ID = "cap_media_video"
CAPTURED_AT = "2026-05-06T07:08:09+00:00"

#: The probe answer an ordinary single-stream MP3 produces.
GOOD_AUDIO = probe_result(
    container_names=("mp3",), duration_seconds=12.5, audio_streams=(audio_stream(index=0),)
)
#: The probe answer an ordinary silent MP4 produces.
GOOD_VIDEO = probe_result(
    container_names=("mp4",), duration_seconds=61.0, video_streams=(video_stream(index=0),)
)


def envelope(
    capture_id: str, payload_type: str, mime_type: str, file_ref: str, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "0.3",
        "id": capture_id,
        "source": {"type": "upload", "provider": "curl"},
        "payload": {
            "type": payload_type,
            "mime_type": mime_type,
            "file_ref": file_ref,
            "title": "A recording",
        },
        "context": {"captured_at": CAPTURED_AT},
    }
    return body | overrides


def app_with(probe: FakeMediaProbe, tmp_path: Path) -> Any:
    return build_local_app(tmp_path / "data", media_probe=probe)


def stage(client: TestClient, mime_type: str) -> str:
    upload = client.post("/v1/uploads", files={"file": ("clip", MP3, mime_type)})
    assert upload.status_code == 200
    return str(upload.json()["file_ref"])


def submit_audio(client: TestClient) -> Any:
    return client.post(
        "/v1/captures",
        json=envelope(AUDIO_ID, "audio", "audio/mpeg", stage(client, "audio/mpeg")),
    )


def submit_video(client: TestClient) -> Any:
    return client.post(
        "/v1/captures",
        json=envelope(VIDEO_ID, "video", "video/mp4", stage(client, "video/mp4")),
    )


class TestASuccessfulMediaCapture:
    """201, complete, and canonical content on the other side."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> Any:
        with TestClient(app_with(FakeMediaProbe(result=GOOD_AUDIO), tmp_path)) as test_client:
            yield test_client

    def test_audio_is_created(self, client: TestClient) -> None:
        assert submit_audio(client).status_code == 201

    def test_the_capture_is_complete(self, client: TestClient) -> None:
        submit_audio(client)

        record = client.get(f"/v1/captures/{AUDIO_ID}").json()

        assert record["status"] == CaptureStatus.COMPLETE.value
        assert record["error"] is None

    def test_the_canonical_content_is_audio_with_no_segments(self, client: TestClient) -> None:
        submit_audio(client)

        content = client.get(f"/v1/captures/{AUDIO_ID}/content").json()

        assert content["type"] == ContentType.AUDIO.value
        assert content["segments"] == []
        assert content["schema_version"] == "0.3"

    def test_the_structural_metadata_reaches_the_wire(self, client: TestClient) -> None:
        submit_audio(client)

        content = client.get(f"/v1/captures/{AUDIO_ID}/content").json()

        assert content["metadata"] == {
            "media": {
                "container": "mp3",
                "duration_seconds": 12.5,
                "audio_stream_count": 1,
                "video_stream_count": 0,
            },
            "audio_streams": [{"index": 0, "codec": "mp3", "sample_rate": 44100, "channels": 2}],
            "video_streams": [],
        }

    def test_the_processing_record_names_the_audio_processor(self, client: TestClient) -> None:
        submit_audio(client)

        content = client.get(f"/v1/captures/{AUDIO_ID}/content").json()

        record = content["processing"][0]
        assert (record["processor"], record["processor_version"]) == ("audio", "0.1")
        assert record["status"] == ProcessingStatus.COMPLETE.value

    def test_video_completes_too(self, tmp_path: Path) -> None:
        with TestClient(app_with(FakeMediaProbe(result=GOOD_VIDEO), tmp_path)) as client:
            assert submit_video(client).status_code == 201

            content = client.get(f"/v1/captures/{VIDEO_ID}/content").json()

        assert content["type"] == ContentType.VIDEO.value
        assert content["metadata"]["media"]["audio_stream_count"] == 0


class TestAProbeWithNoTrustedResult:
    """503, and a capture left truthfully non-terminal."""

    @pytest.fixture
    def failing(self, tmp_path: Path) -> Any:
        probe = FakeMediaProbe(
            raises=MediaProbeExecutionError(
                "the probe at /home/someone/.local/bin/ffprobe exited with status 1 "
                "after reading container 'matroska,webm'"
            )
        )
        with TestClient(app_with(probe, tmp_path)) as test_client:
            yield test_client

    def test_it_is_a_fixed_503(self, failing: TestClient) -> None:
        response = submit_audio(failing)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "media_probe_unavailable"

    def test_the_capture_is_left_processing(self, failing: TestClient) -> None:
        """Not ``FAILED``: with no trusted result there is no basis for a verdict."""
        submit_audio(failing)

        record = failing.get(f"/v1/captures/{AUDIO_ID}").json()

        assert record["status"] == CaptureStatus.PROCESSING.value

    def test_no_content_is_persisted(self, failing: TestClient) -> None:
        submit_audio(failing)

        assert failing.get(f"/v1/captures/{AUDIO_ID}/content").status_code == 404

    def test_the_body_leaks_nothing_the_probe_knew(self, failing: TestClient) -> None:
        """The underlying message names a path, an engine and a container. None travel."""
        body = submit_audio(failing).json()

        serialized = repr(body)
        for leaked in ("ffprobe", "/home/someone", "matroska", "status 1", "webm"):
            assert leaked not in serialized

    def test_the_public_sentence_is_the_fixed_one(self, failing: TestClient) -> None:
        message = submit_audio(failing).json()["error"]["message"]

        assert message == (
            "media structure probing could not be completed; "
            "the capture is stored and no content was produced"
        )

    @pytest.mark.parametrize(
        ("returns", "label"),
        [
            (None, "none"),
            ({"container_names": ("mp3",)}, "dict"),
            (probe_result(container_names=("MP3",), audio_streams=(audio_stream(),)), "case"),
        ],
    )
    def test_a_malformed_adapter_answer_is_the_same_fixed_503(
        self, tmp_path: Path, returns: object, label: str
    ) -> None:
        """An adapter contradicting its own contract yields no trusted result."""
        with TestClient(app_with(FakeMediaProbe(returns=returns), tmp_path)) as client:
            response = submit_audio(client)

            assert response.status_code == 503
            assert response.json()["error"]["code"] == "media_probe_unavailable"
            assert (
                client.get(f"/v1/captures/{AUDIO_ID}").json()["status"]
                == CaptureStatus.PROCESSING.value
            )


class TestADeterministicVerdictAboutTheBytes:
    """422, and a capture durably failed."""

    def test_a_container_mismatch_is_422_and_failed(self, tmp_path: Path) -> None:
        mismatched = probe_result(container_names=("wav",), audio_streams=(audio_stream(index=0),))
        with TestClient(app_with(FakeMediaProbe(result=mismatched), tmp_path)) as client:
            response = submit_audio(client)

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "processing_failed"
            record = client.get(f"/v1/captures/{AUDIO_ID}").json()
            assert record["status"] == CaptureStatus.FAILED.value
            assert client.get(f"/v1/captures/{AUDIO_ID}/content").status_code == 404

    def test_a_missing_required_stream_is_422_and_failed(self, tmp_path: Path) -> None:
        streamless = probe_result(container_names=("mp3",), audio_streams=())
        with TestClient(app_with(FakeMediaProbe(result=streamless), tmp_path)) as client:
            response = submit_audio(client)

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "processing_failed"
            record = client.get(f"/v1/captures/{AUDIO_ID}").json()
            assert record["status"] == CaptureStatus.FAILED.value
            assert client.get(f"/v1/captures/{AUDIO_ID}/content").status_code == 404

    def test_a_video_missing_its_video_stream_is_422(self, tmp_path: Path) -> None:
        streamless = probe_result(
            container_names=("mp4",), audio_streams=(audio_stream(index=0),), video_streams=()
        )
        with TestClient(app_with(FakeMediaProbe(result=streamless), tmp_path)) as client:
            assert submit_video(client).status_code == 422

    def test_the_422_body_reveals_nothing_observed(self, tmp_path: Path) -> None:
        mismatched = probe_result(container_names=("ogg",), audio_streams=(audio_stream(index=0),))
        with TestClient(app_with(FakeMediaProbe(result=mismatched), tmp_path)) as client:
            body = submit_audio(client).json()

        assert "ogg" not in repr(body)

    def test_the_two_failures_are_answered_differently(self, tmp_path: Path) -> None:
        """The whole point: 'no result we can trust' is not 'a trusted result, and no'."""
        verdict = probe_result(container_names=("wav",), audio_streams=(audio_stream(),))
        # Separate data directories: one capture id, two independent deployments.
        # Sharing a directory would make the second submission a duplicate.
        with TestClient(app_with(FakeMediaProbe(result=verdict), tmp_path / "a")) as client:
            deterministic = submit_audio(client).status_code
        with TestClient(
            app_with(FakeMediaProbe(raises=MediaProbeExecutionError("gone")), tmp_path / "b")
        ) as client:
            untrusted = submit_audio(client).status_code

        assert (deterministic, untrusted) == (422, 503)


class TestTheUploadRouteIsUnchanged:
    """Staging stays format-blind, whatever the media capability is."""

    @pytest.mark.parametrize("media", [True, False], ids=["media-on", "media-off"])
    def test_staging_media_bytes_is_accepted_either_way(self, tmp_path: Path, media: bool) -> None:
        probe = FakeMediaProbe(result=GOOD_AUDIO) if media else None
        app = build_local_app(tmp_path / "data", media_probe=probe)

        with TestClient(app) as client:
            response = client.post("/v1/uploads", files={"file": ("clip.mp3", MP3, "audio/mpeg")})

        assert response.status_code == 200
        assert response.json()["file_ref"] == MP3_FILE_REF

    def test_the_route_never_sees_the_probe(self, tmp_path: Path) -> None:
        """Upload knows nothing about media capability, and nothing probes on staging."""
        probe = FakeMediaProbe(result=GOOD_AUDIO)
        with TestClient(app_with(probe, tmp_path)) as client:
            client.post("/v1/uploads", files={"file": ("clip.mp3", MP3, "audio/mpeg")})

        assert probe.calls == []
