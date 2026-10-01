"""Browser submission and independently authenticated receiver routes."""

from typing import Annotated

from fastapi import FastAPI, Path, Request, Response

from core.contracts import CaptureStatus
from core.persistence import CaptureRecordStore, ContentObjectStore
from core.rendering import MarkdownRenderer
from unimem_api.obsidian_contract import (
    UUID_PATTERN,
    AckRequest,
    ClaimRequest,
    Delivery,
    DeliveryError,
    DeliveryRequest,
    FailureRequest,
)
from unimem_api.obsidian_store import ObsidianStore, suggested_filename
from unimem_api.security import refusal
from unimem_youtube.errors import ExportError
from unimem_youtube.export import CaptionMarkdownRenderer

UuidPath = Annotated[str, Path(pattern=UUID_PATTERN)]
CapturePath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def receipt(delivery: Delivery) -> dict[str, object]:
    return delivery.model_dump(mode="json", exclude={"markdown"})


def install_obsidian_routes(
    app: FastAPI, store: ObsidianStore, captures: CaptureRecordStore, contents: ContentObjectStore
) -> None:
    @app.exception_handler(DeliveryError)
    async def failure(request: Request, exc: DeliveryError) -> Response:
        code = exc.code
        status = {
            "destination_not_found": 404,
            "delivery_not_found": 404,
            "markdown_too_large": 413,
            "delivery_history_full": 507,
            "delivery_storage_unavailable": 503,
            "markdown_unavailable": 503,
        }.get(code, 409)
        return refusal(status, code)

    @app.get("/v1/destinations")
    def destinations() -> list[dict[str, object]]:
        return [d.model_dump(mode="json", exclude={"receiver_id"}) for d in store.destinations()]

    @app.get("/v1/destinations/{destination_id}/captures/{capture_id}/delivery")
    def lookup(destination_id: UuidPath, capture_id: CapturePath) -> dict[str, object]:
        store.destination(destination_id)
        existing = store.find(destination_id, capture_id)
        return {
            "delivery": receipt(existing) if existing else None,
            "suggested_filename": suggested_filename(capture_id),
        }

    @app.post("/v1/deliveries", status_code=202)
    def submit(request: DeliveryRequest, response: Response) -> dict[str, object]:
        existing = store.find(request.destination_id, request.source_capture_id)
        if existing:
            response.status_code = 200
            return receipt(existing)
        if captures.get(request.source_capture_id).status != CaptureStatus.COMPLETE:
            raise DeliveryError("result_not_ready")
        content = contents.get_for_capture(request.source_capture_id)
        renderer = (
            CaptionMarkdownRenderer()
            if "youtube_captions" in content.metadata
            else MarkdownRenderer()
        )
        try:
            markdown = renderer.render(content)
        except ExportError:
            raise DeliveryError("markdown_unavailable") from None
        delivery, created = store.register(
            request.destination_id,
            request.source_capture_id,
            content.id,
            markdown,
            renderer.name,
            renderer.version,
        )
        response.status_code = 202 if created else 200
        return receipt(delivery)

    @app.get("/v1/receiver/destination")
    def destination(request: Request) -> dict[str, object]:
        return store.destination(request.state.destination_id).model_dump(mode="json")

    @app.get("/v1/receiver/deliveries/next")
    def next_delivery(request: Request) -> Delivery | None:
        return store.next(request.state.destination_id)

    @app.get("/v1/receiver/deliveries/{delivery_id}")
    def get_delivery(request: Request, delivery_id: UuidPath) -> Delivery:
        return store.get(request.state.destination_id, delivery_id)

    @app.post("/v1/receiver/deliveries/{delivery_id}/claim")
    def claim(request: Request, delivery_id: UuidPath, body: ClaimRequest) -> Delivery:
        return store.claim(request.state.destination_id, delivery_id, body)

    @app.post("/v1/receiver/deliveries/{delivery_id}/ack")
    def ack(request: Request, delivery_id: UuidPath, body: AckRequest) -> Delivery:
        return store.finish(request.state.destination_id, delivery_id, body)

    @app.post("/v1/receiver/deliveries/{delivery_id}/fail")
    def fail(request: Request, delivery_id: UuidPath, body: FailureRequest) -> Delivery:
        return store.finish(request.state.destination_id, delivery_id, body)
