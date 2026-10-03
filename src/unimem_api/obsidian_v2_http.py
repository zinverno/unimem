"""Required single-image packages, isolated from strict v1 receivers."""

import hashlib

from fastapi import FastAPI, Request, Response

from core.contracts import CaptureStatus
from core.persistence import CaptureRecordStore, ContentObjectStore
from core.storage import RawObjectStore
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_export import ImageMarkdownRenderer, image_attachment
from unimem_api.obsidian_contract import (
    AnyDelivery,
    AttachmentPermission,
    ClaimRequest,
    DeliveryError,
    DeliveryRequest,
    FailureRequest,
    ImageDelivery,
    PackageAckRequest,
)
from unimem_api.obsidian_http import CapturePath, UuidPath, receipt
from unimem_api.obsidian_store import ObsidianStore, suggested_filename


def package(d: AnyDelivery) -> ImageDelivery:
    if not isinstance(d, ImageDelivery):
        raise DeliveryError("protocol_mismatch")
    return d


def install_attachment_routes(
    app: FastAPI,
    store: ObsidianStore,
    captures: CaptureRecordStore,
    contents: ContentObjectStore,
    raw: RawObjectStore,
) -> None:
    @app.post("/v2/receiver/capabilities")
    def permission(request: Request, body: AttachmentPermission) -> dict[str, object]:
        store.attachment_permission(request.state.destination_id, body.receiver_id, body.enabled)
        return {"protocol_version": "2", "attachments": body.enabled}

    @app.get("/v2/destinations/{destination_id}/captures/{capture_id}/delivery")
    def lookup(destination_id: UuidPath, capture_id: CapturePath) -> dict[str, object]:
        existing = store.find(destination_id, capture_id)
        return {
            "delivery": receipt(package(existing)) if existing else None,
            "attachments_enabled": store.attachments_enabled(destination_id),
            "suggested_filename": suggested_filename(capture_id),
        }

    @app.post("/v2/deliveries", status_code=202)
    def submit(body: DeliveryRequest, response: Response) -> dict[str, object]:
        existing = store.find(body.destination_id, body.source_capture_id)
        if existing:
            response.status_code = 200
            return receipt(package(existing))
        if not store.attachments_enabled(body.destination_id):
            raise DeliveryError("receiver_upgrade_required")
        capture = captures.get(body.source_capture_id)
        if capture.status is not CaptureStatus.COMPLETE:
            raise DeliveryError("result_not_ready")
        content = contents.get_for_capture(capture.id)
        attachment, ref = image_attachment(content, raw)
        renderer = ImageMarkdownRenderer()
        d, created = store.register(
            body.destination_id,
            capture.id,
            content.id,
            renderer.render(content, capture, attachment),
            renderer.name,
            renderer.version,
            attachment=attachment,
            raw_ref=ref,
        )
        response.status_code = 202 if created else 200
        return receipt(package(d))

    @app.get("/v2/receiver/deliveries/next")
    def next_delivery(request: Request) -> ImageDelivery | None:
        d = store.next(request.state.destination_id, "2")
        return package(d) if d else None

    @app.get("/v2/receiver/deliveries/{delivery_id}")
    def get(request: Request, delivery_id: UuidPath) -> ImageDelivery:
        return package(store.get(request.state.destination_id, delivery_id))

    @app.post("/v2/receiver/deliveries/{delivery_id}/claim")
    def claim(request: Request, delivery_id: UuidPath, body: ClaimRequest) -> ImageDelivery:
        get(request, delivery_id)
        if not store.attachments_enabled(request.state.destination_id):
            raise DeliveryError("receiver_upgrade_required")
        return package(store.claim(request.state.destination_id, delivery_id, body))

    @app.post("/v2/receiver/deliveries/{delivery_id}/ack")
    def ack(request: Request, delivery_id: UuidPath, body: PackageAckRequest) -> ImageDelivery:
        get(request, delivery_id)
        return package(store.finish(request.state.destination_id, delivery_id, body))

    @app.post("/v2/receiver/deliveries/{delivery_id}/fail")
    def fail(request: Request, delivery_id: UuidPath, body: FailureRequest) -> ImageDelivery:
        get(request, delivery_id)
        return package(store.finish(request.state.destination_id, delivery_id, body))

    @app.get("/v2/receiver/deliveries/{delivery_id}/assets/{asset_id}")
    def asset(request: Request, delivery_id: UuidPath, asset_id: CapturePath) -> Response:
        d = get(request, delivery_id)
        ref = store.asset_ref(request.state.destination_id, delivery_id, asset_id)
        a = d.attachments[0]
        with raw.open(raw_object_ref(parse_raw_ref(ref))) as stream:
            data = stream.read(a.size_bytes + 1)
        if len(data) != a.size_bytes or hashlib.sha256(data).hexdigest() != a.sha256:
            raise DeliveryError("asset_mismatch")
        return Response(data, media_type=a.mime_type)
