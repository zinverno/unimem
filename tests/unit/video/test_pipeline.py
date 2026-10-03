"""Fake model evidence, real synthetic decoder and existing persistence/delivery."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.contracts import ContentType, SegmentType
from tests.api_auth import AUTH_HEADERS, TEST_SECURITY
from tests.unit.asr.test_transcription import transcript
from tests.unit.video.test_decode import movie
from tests.unit.vision.test_description import result as description
from unimem_api.obsidian_contract import VideoDelivery, video_package_digest
from unimem_api.obsidian_store import ObsidianStore
from unimem_api.wiring import build_local_app
from unimem_delivery.operations import OperationError
from unimem_video.export import VideoMarkdownRenderer
from unimem_video.operations import VideoOperationStore, VideoRequest
from unimem_video.policy import ASR_PROFILE, VISION_PROFILE, VideoError
from unimem_video.service import VideoCaptureService
from unimem_video.worker import execute_video


def staged(
    path: Path,
    *,
    speech: bool = True,
    frames: bool = True,
    describe: bool = True,
    audio: bool = True,
) -> tuple[VideoCaptureService, VideoOperationStore, VideoRequest]:
    service = VideoCaptureService(path)
    raw = service.raw_store.store_bytes(movie(audio=audio))
    assert raw.ref
    request = VideoRequest(
        operation_id="video-one",
        file_ref=raw.ref,
        speech=speech,
        frames=frames,
        describe=describe,
        language="en",
        captured_at=datetime.now(UTC),
        asr_profile=ASR_PROFILE if speech else None,
        vision_profile=VISION_PROFILE if describe else None,
    )
    return service, VideoOperationStore(path / "unimem.sqlite3"), request


def test_one_video_common_time_saved_results_and_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store, request = staged(tmp_path)
    op, _ = store.register(request)
    assert store.claim()
    calls = []

    def asr(*_: Any) -> Any:
        calls.append("asr")
        return transcript(
            container="mp4",
            codec="aac",
            selected_language="en",
            duration=_[0].stat().st_size / 32000,
        )

    def vision(*_: Any) -> Any:
        calls.append("vision")
        return description()

    execute_video(tmp_path, request.operation_id, asr, vision)
    final = store.get(request.operation_id)
    assert final.state == "complete", final.error_code
    assert calls == ["asr", "vision", "vision", "vision"]
    content = service.read(op.reserved_capture_id)
    assert content.type is ContentType.VIDEO
    assert len(content.assets) == 4
    speech = content.segments[0]
    assert speech.type is SegmentType.TRANSCRIPT
    assert speech.temporal
    assert 0.9 <= speech.temporal.start <= 1  # type: ignore[operator]
    assert all(s.provenance.capture_id == op.reserved_capture_id for s in content.segments)
    markdown = VideoMarkdownRenderer().render(content)
    # Remove access to the original after completion. Reads and delivery need only
    # canonical evidence and selected PNGs, never decode or recognize again.
    original_open = service.raw_store.open

    def only_derivatives(raw: Any) -> Any:
        assert raw.ref != request.file_ref
        return original_open(raw)

    monkeypatch.setattr(
        "unimem_video.service.decode", lambda *_args, **_kwargs: pytest.fail("decode again")
    )
    execute_video(tmp_path, request.operation_id, asr, vision)
    assert not store.register(request)[1]
    for field, value in [
        ("language", "ru"),
        ("sampling", "other"),
        ("asr_profile", "other"),
        ("vision_profile", "other"),
    ]:
        with pytest.raises(OperationError, match="operation_conflict"):
            store.register(request.model_copy(update={field: value}))
    app = build_local_app(tmp_path, security=TEST_SECURITY)
    client = TestClient(app, headers=AUTH_HEADERS, base_url="http://127.0.0.1:8765")
    # Class-wide raw guard also covers independently composed delivery stores.
    from core.storage import LocalRawObjectStore

    monkeypatch.setattr(LocalRawObjectStore, "open", lambda _self, raw: only_derivatives(raw))
    saved = client.get("/v1/video/operations/video-one/result")
    assert saved.status_code == 200, saved.text
    assert saved.json()["markdown"] == markdown
    assert saved.json()["delivery_version"] == "3"
    delivery = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3")
    destination, token = delivery.create_destination("Video fixture")
    _other, other_token = delivery.create_destination("Other")
    headers = {"Authorization": f"Bearer {token}"}
    owner = {"receiver_id": str(uuid4()), "claim_id": str(uuid4())}
    body = {
        "destination_id": destination.destination_id,
        "source_capture_id": op.reserved_capture_id,
    }
    assert client.post("/v3/deliveries", json=body).status_code == 409
    assert client.post("/v1/deliveries", json=body).status_code == 409
    assert client.post("/v2/deliveries", json=body).status_code == 409
    assert (
        client.post(
            "/v3/receiver/capabilities", json={"receiver_id": owner["receiver_id"], "enabled": True}
        ).status_code
        == 401
    )
    assert client.get("/v1/video/capabilities", headers=headers).status_code == 401
    assert (
        client.post(
            "/v3/receiver/capabilities",
            headers=headers,
            json={"receiver_id": owner["receiver_id"], "enabled": True},
        ).status_code
        == 200
    )
    response = client.post("/v3/deliveries", json=body)
    assert response.status_code == 202, response.text
    package = delivery.get(destination.destination_id, response.json()["delivery_id"])
    assert isinstance(package, VideoDelivery)
    assert package.markdown == markdown
    assert package.package_sha256 == video_package_digest(package)
    assert len(package.attachments) == 3
    route = f"/v3/receiver/deliveries/{package.delivery_id}"
    for a in package.attachments:
        asset = client.get(route + "/assets/" + a.asset_id, headers=headers)
        assert asset.status_code == 200
        assert len(asset.content) == a.size_bytes
        assert (
            client.get(
                route + "/assets/" + a.asset_id, headers={"Authorization": f"Bearer {other_token}"}
            ).status_code
            == 404
        )
    assert client.get("/v2/receiver/deliveries/next", headers=headers).json() is None
    assert client.get("/v1/receiver/deliveries/next", headers=headers).json() is None
    assert client.post(route + "/claim", headers=headers, json=owner).status_code == 200
    assert (
        client.post(
            route.replace("/v3/", "/v1/") + "/ack",
            headers=headers,
            json={**owner, "markdown_sha256": package.markdown_sha256},
        ).status_code
        == 409
    )
    ack = {**owner, "package_sha256": package.package_sha256}
    assert client.post(route + "/ack", headers=headers, json=ack).json()["state"] == "imported"
    assert client.post(route + "/ack", headers=headers, json=ack).json()["state"] == "imported"
    assert client.post("/v3/deliveries", json=body).status_code == 200
    assert calls == ["asr", "vision", "vision", "vision"]


@pytest.mark.parametrize(
    ("speech", "audio", "outcome"),
    [(False, True, "not_requested"), (True, False, "no_audio_track"), (True, True, "no_speech")],
)
def test_distinct_no_speech_results_and_text_v1(
    tmp_path: Path, speech: bool, audio: bool, outcome: str
) -> None:
    service, store, request = staged(
        tmp_path, speech=speech, frames=False, describe=False, audio=audio
    )
    op, _ = store.register(request)
    store.claim()
    calls = []

    def asr(*_: Any) -> Any:
        calls.append(1)
        return transcript(
            container="mp4",
            codec="aac",
            selected_language="en",
            duration=_[0].stat().st_size / 32000,
            outcome="no_speech",
            segments=[],
        )

    execute_video(tmp_path, request.operation_id, asr)
    assert store.get(request.operation_id).state == "complete"
    content = service.read(op.reserved_capture_id)
    facts = content.metadata["video_notes"]
    assert isinstance(facts, dict)
    assert facts["speech_outcome"] == outcome
    assert calls == ([1] if speech and audio else [])
    assert len(content.assets) == 1
    assert not content.segments
    client = TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY),
        headers=AUTH_HEADERS,
        base_url="http://127.0.0.1:8765",
    )
    destination, _ = ObsidianStore(tmp_path / "obsidian-delivery.sqlite3").create_destination(
        "Text"
    )
    response = client.post(
        "/v1/deliveries",
        json={
            "destination_id": destination.destination_id,
            "source_capture_id": op.reserved_capture_id,
        },
    )
    assert response.status_code == 202
    assert response.json()["protocol_version"] == "1"


@pytest.mark.parametrize("stage", ["asr", "vision"])
def test_failed_required_stage_no_fake_completion_or_retry(tmp_path: Path, stage: str) -> None:
    service, store, request = staged(tmp_path)
    op, _ = store.register(request)
    store.claim()
    calls = []

    def fail(*_: Any) -> Any:
        calls.append(1)
        raise VideoError("test_stage_failed")

    execute_video(
        tmp_path,
        request.operation_id,
        fail
        if stage == "asr"
        else lambda *args: transcript(
            container="mp4",
            codec="aac",
            selected_language="en",
            duration=args[0].stat().st_size / 32000,
        ),
        fail,
    )
    assert store.get(request.operation_id).state == "failed"
    assert store.get(request.operation_id).error_code == "test_stage_failed"
    assert store.get(request.operation_id).content_id is None
    assert service.record_store.get(op.reserved_capture_id).raw_object
    execute_video(tmp_path, request.operation_id, fail, fail)
    assert calls == [1]


def test_capability_before_acceptance_and_replay_without_models(tmp_path: Path) -> None:
    _, store, request = staged(tmp_path)
    client = TestClient(
        build_local_app(tmp_path, security=TEST_SECURITY, video_notes=True),
        headers=AUTH_HEADERS,
        base_url="http://127.0.0.1:8765",
    )
    assert (
        client.post("/v1/video/operations", json=request.model_dump(mode="json")).json()["error"][
            "code"
        ]
        == "asr_disabled"
    )
    with pytest.raises(OperationError, match="operation_not_found"):
        store.get(request.operation_id)
    store.register(request)
    assert (
        client.post("/v1/video/operations", json=request.model_dump(mode="json")).status_code == 200
    )
