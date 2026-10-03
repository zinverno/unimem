"""Binary upload references and read-only persisted image results."""

from fastapi import FastAPI, Response

from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_export import image_attachment, image_renderer
from unimem_api.image_operations import ImageOperation, ImageOperationStore, ImageRequest
from unimem_api.image_service import ImageCaptureService, ImageError, inspect_image, ocr_status
from unimem_api.security import refusal
from unimem_delivery.operations import OperationError


def image_view(op: ImageOperation) -> dict[str, object]:
    return {
        **op.model_dump(mode="json", exclude={"request", "reserved_capture_id"}),
        **op.request.model_dump(mode="json"),
        "kind": "image-capture",
        "result_available": op.state == "complete",
    }


def install_image_routes(
    app: FastAPI,
    store: ImageOperationStore,
    service: ImageCaptureService,
    *,
    ocr_enabled: bool,
    description_capability: dict[str, str | bool] | None = None,
) -> None:
    capability = description_capability or {"ready": False, "code": "vision_disabled"}

    @app.get("/v1/image/capabilities")
    def capabilities() -> dict[str, object]:
        return {"description": capability, "ocr": ocr_enabled}

    @app.exception_handler(ImageError)
    async def failure(request: object, exc: ImageError) -> Response:
        return refusal(
            503 if exc.code in {"ocr_disabled", "image_decoder_unavailable"} else 422, exc.code
        )

    @app.post("/v1/image/operations", status_code=202)
    def submit(request: ImageRequest, response: Response) -> dict[str, object]:
        try:
            existing = store.get(request.operation_id)
        except OperationError as exc:
            if exc.code != "operation_not_found":
                raise
        else:
            if existing.request != request:
                raise OperationError("operation_conflict")
            response.status_code = 200
            return image_view(existing)
        if request.mode == "ocr" and not ocr_enabled:
            raise ImageError("ocr_disabled")
        if request.mode == "describe" and not capability["ready"]:
            raise ImageError(str(capability["code"]))
        with service.raw_store.open(raw_object_ref(parse_raw_ref(request.file_ref))) as stream:
            inspect_image(stream, request.declared_mime)
        operation, created = store.register(request)
        response.status_code = 202 if created else 200
        return image_view(operation)

    @app.get("/v1/image/operations/{operation_id}")
    def status(operation_id: str) -> dict[str, object]:
        return image_view(store.get(operation_id))

    def completed(operation_id: str) -> str:
        op = store.get(operation_id)
        if op.state != "complete" or op.capture_id is None:
            raise OperationError("result_not_ready")
        return op.capture_id

    @app.get("/v1/image/operations/{operation_id}/result")
    def result(operation_id: str) -> dict[str, object]:
        capture_id = completed(operation_id)
        content = service.read(capture_id)
        asset, _ = image_attachment(content, service.raw_store)
        markdown = image_renderer(content).render(
            content, service.record_store.get(capture_id), asset
        )
        return {
            "markdown": markdown,
            "markdown_bytes": len(markdown.encode()),
            "attachment": asset.model_dump(),
            "ocr_status": ocr_status(content),
            "description": content.metadata.get("image_description"),
        }

    @app.get("/v1/image/operations/{operation_id}/original")
    def original(operation_id: str) -> Response:
        content = service.read(completed(operation_id))
        asset, ref = image_attachment(content, service.raw_store)
        with service.raw_store.open(raw_object_ref(parse_raw_ref(ref))) as stream:
            data = stream.read(asset.size_bytes + 1)
        return Response(data, media_type=asset.mime_type)
