"""Authenticated local references, explicit profiles and read-only saved previews."""

from pathlib import Path
from typing import cast

from fastapi import FastAPI, Response

from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.security import refusal
from unimem_delivery.operations import OperationError
from unimem_video.decode import inspect_video
from unimem_video.export import VideoMarkdownRenderer, read_frame, video_attachments
from unimem_video.operations import VideoOperation, VideoOperationStore, VideoRequest
from unimem_video.policy import ASR_PROFILE, DECODER, SAMPLING, VISION_PROFILE, VideoError
from unimem_video.service import VideoCaptureService


def view(op: VideoOperation) -> dict[str, object]:
    return {
        **op.model_dump(mode="json", exclude={"request", "reserved_capture_id"}),
        **op.request.model_dump(mode="json"),
        "kind": "video-notes",
        "result_available": op.state == "complete",
    }


def install_video_routes(
    app: FastAPI,
    store: VideoOperationStore,
    service: VideoCaptureService,
    *,
    enabled: bool,
    asr: bool,
    vision: dict[str, str | bool] | None,
    model_dir: Path | None = None,
    vision_dir: Path | None = None,
) -> None:
    @app.exception_handler(VideoError)
    async def failure(request: object, exc: VideoError) -> Response:
        return refusal(422, exc.code)

    @app.get("/v1/video/capabilities")
    def capabilities() -> dict[str, object]:
        return {
            "ready": enabled,
            "asr": asr,
            "vision": bool(vision and vision.get("ready")),
            "decoder": DECODER,
            "sampling": SAMPLING,
            "asr_profile": ASR_PROFILE,
            "vision_profile": VISION_PROFILE,
        }

    @app.post("/v1/video/operations", status_code=202)
    def submit(request: VideoRequest, response: Response) -> dict[str, object]:
        try:
            existing = store.get(request.operation_id)
        except OperationError as exc:
            if exc.code != "operation_not_found":
                raise
        else:
            if existing.request != request:
                raise OperationError("operation_conflict")
            response.status_code = 200
            return view(cast(VideoOperation, existing))
        if not enabled:
            raise VideoError("video_disabled")
        if not request.supported():
            raise VideoError("profile_unavailable")
        if request.speech and not asr:
            raise VideoError("asr_disabled")
        if request.describe and not (vision and vision.get("ready")):
            raise VideoError("vision_disabled")
        # Readiness is confirmed again before acceptance, not just at API startup.
        if request.speech and model_dir is not None:
            from unimem_asr.model import verify_model

            verify_model(model_dir)
        if request.describe and vision_dir is not None:
            from unimem_vision.policy import VisionError
            from unimem_vision.profile import verify_profile

            try:
                verify_profile(vision_dir)
            except VisionError as exc:
                raise VideoError(exc.code) from None
        with service.raw_store.open(raw_object_ref(parse_raw_ref(request.file_ref))) as stream:
            inspect_video(stream, request.declared_mime)
        op, created = store.register(request)
        response.status_code = 202 if created else 200
        return view(cast(VideoOperation, op))

    @app.get("/v1/video/operations/{operation_id}")
    def status(operation_id: str) -> dict[str, object]:
        return view(cast(VideoOperation, store.get(operation_id)))

    def capture_id(operation_id: str) -> str:
        op = store.get(operation_id)
        if op.state != "complete" or not op.capture_id:
            raise OperationError("result_not_ready")
        return op.capture_id

    @app.get("/v1/video/operations/{operation_id}/result")
    def result(operation_id: str) -> dict[str, object]:
        content = service.read(capture_id(operation_id))
        assets = video_attachments(content)
        markdown = VideoMarkdownRenderer().render(content)
        return {
            "markdown": markdown,
            "markdown_bytes": len(markdown.encode()),
            "attachments": [a.model_dump() for a, _ in assets],
            "delivery_version": "3" if assets else "1",
            "content": content.model_dump(mode="json"),
        }

    @app.get("/v1/video/operations/{operation_id}/frames/{asset_id}")
    def frame(operation_id: str, asset_id: str) -> Response:
        for a, ref in video_attachments(service.read(capture_id(operation_id))):
            if a.asset_id == asset_id:
                return Response(read_frame(service.raw_store, a, ref), media_type="image/png")
        raise VideoError("asset_not_found")
