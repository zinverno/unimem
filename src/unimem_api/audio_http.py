"""Staged binary references in; durable audio acceptance out. No native work here."""

from fastapi import FastAPI, Response

from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.audio_operations import AudioOperation, AudioOperationStore, AudioRequest
from unimem_api.security import refusal
from unimem_asr.export import AudioMarkdownRenderer
from unimem_asr.input import inspect_input
from unimem_asr.policy import AsrError
from unimem_asr.service import AudioCaptureService
from unimem_delivery.operations import OperationError


def audio_view(op: AudioOperation) -> dict[str, object]:
    return {
        **op.model_dump(mode="json", exclude={"request", "reserved_capture_id"}),
        **op.request.model_dump(mode="json"),
        "kind": "audio-transcription",
        "result_available": op.state == "complete",
    }


def install_audio_routes(
    app: FastAPI, store: AudioOperationStore, service: AudioCaptureService, *, enabled: bool
) -> None:
    @app.exception_handler(AsrError)
    async def failure(request: object, exc: AsrError) -> Response:
        status = (
            503
            if exc.code in {"asr_disabled", "model_unavailable", "markdown_unavailable"}
            else 422
        )
        return refusal(status, exc.code)

    @app.post("/v1/audio/operations", status_code=202)
    def submit(request: AudioRequest, response: Response) -> dict[str, object]:
        # Existing receipts replay without reading or decoding the original again.
        try:
            existing = store.get(request.operation_id)
        except OperationError as exc:
            if exc.code != "operation_not_found":
                raise
        else:
            if existing.request != request:
                raise OperationError("operation_conflict")
            response.status_code = 200
            return audio_view(existing)
        if not enabled:
            raise AsrError("asr_disabled")
        raw = raw_object_ref(parse_raw_ref(request.file_ref))
        with service.raw_store.open(raw) as stream:
            inspect_input(stream, request.declared_mime)
        # Only a fully uploaded immutable object can reach durable registration.
        operation, created = store.register(request)
        response.status_code = 202 if created else 200
        return audio_view(operation)

    @app.get("/v1/audio/operations/{operation_id}")
    def status(operation_id: str) -> dict[str, object]:
        return audio_view(store.get(operation_id))

    @app.get("/v1/audio/operations/{operation_id}/markdown")
    def markdown(operation_id: str) -> Response:
        operation = store.get(operation_id)
        if operation.state != "complete" or operation.capture_id is None:
            raise OperationError("result_not_ready")
        rendered = AudioMarkdownRenderer().render(service.read(operation.capture_id))
        return Response(rendered, media_type="text/markdown")
