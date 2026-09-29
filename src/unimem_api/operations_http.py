"""Delivery translations only; queue/capture lifecycle belongs to its owners."""

from fastapi import FastAPI, Response

from unimem_api.security import refusal
from unimem_youtube.errors import AcquisitionError, ExportError
from unimem_youtube.export import CaptionMarkdownRenderer
from unimem_youtube.operations import (
    OperationError,
    OperationStore,
    YoutubeOperation,
    YoutubeRequest,
)
from unimem_youtube.service import YoutubeCaptureService


def operation_view(op: YoutubeOperation) -> dict[str, object]:
    return {
        "operation_id": op.request.operation_id,
        "url": op.request.url,
        "languages": list(op.request.languages),
        "state": op.state,
        "accepted_at": op.accepted_at.isoformat(),
        "updated_at": op.updated_at.isoformat(),
        "started_at": op.started_at.isoformat() if op.started_at else None,
        "finished_at": op.finished_at.isoformat() if op.finished_at else None,
        "capture_id": op.capture_id,
        "content_id": op.content_id,
        "result_available": op.state == "complete",
        "error_code": op.error_code,
        "next_action": "Inspect the capture before explicitly submitting a new operation ID."
        if op.state == "interrupted"
        else None,
    }


def install_operation_routes(
    app: FastAPI, store: OperationStore, service: YoutubeCaptureService
) -> None:
    @app.exception_handler(OperationError)
    async def operation_error(request: object, exc: OperationError) -> Response:
        code = exc.code
        status = {
            "operation_not_found": 404,
            "operation_conflict": 409,
            "queue_full": 429,
            "operation_history_full": 507,
            "result_not_ready": 409,
        }.get(code, 503)
        return refusal(status, code)

    @app.exception_handler(AcquisitionError)
    async def input_error(request: object, exc: AcquisitionError) -> Response:
        return refusal(422, exc.code)

    @app.post("/v1/youtube/operations", status_code=202)
    def submit(request: YoutubeRequest, response: Response) -> dict[str, object]:
        operation, created = store.register(request)
        response.status_code = 202 if created else 200
        return operation_view(operation)

    @app.get("/v1/youtube/operations/{operation_id}")
    def status(operation_id: str) -> dict[str, object]:
        return operation_view(store.get(operation_id))

    @app.get("/v1/youtube/operations/{operation_id}/content")
    def content(operation_id: str) -> Response:
        op = store.get(operation_id)
        if op.state != "complete" or op.capture_id is None:
            raise OperationError("result_not_ready")
        saved = service.read(op.capture_id)
        return Response(saved.model_dump_json(), media_type="application/json")

    @app.get("/v1/youtube/operations/{operation_id}/markdown")
    def markdown(operation_id: str) -> Response:
        op = store.get(operation_id)
        if op.state != "complete" or op.capture_id is None:
            raise OperationError("result_not_ready")
        try:
            rendered = CaptionMarkdownRenderer().render(service.read(op.capture_id))
        except ExportError:
            return refusal(503, "markdown_unavailable")
        return Response(rendered, media_type="text/markdown")
