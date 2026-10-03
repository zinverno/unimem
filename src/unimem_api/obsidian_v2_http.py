"""Version-isolated v2 image and v3 selected-PNG delivery over the existing store."""

import hashlib

from fastapi import FastAPI, Request, Response

from core.contracts import CaptureStatus
from core.persistence import CaptureRecordStore, ContentObjectStore
from core.storage import RawObjectStore
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_export import image_attachment, image_renderer
from unimem_api.obsidian_contract import (
    AnyDelivery,
    AttachmentPermission,
    ClaimRequest,
    DeliveryError,
    DeliveryRequest,
    FailureRequest,
    ImageDelivery,
    PackageAckRequest,
    VideoDelivery,
)
from unimem_api.obsidian_http import CapturePath, UuidPath, receipt
from unimem_api.obsidian_store import ObsidianStore, suggested_filename


def package(d: AnyDelivery, version: str = "2") -> ImageDelivery | VideoDelivery:
    if not isinstance(d, (ImageDelivery, VideoDelivery)) or d.protocol_version != version:
        raise DeliveryError("protocol_mismatch")
    return d


def install_attachment_routes(
    app: FastAPI,
    store: ObsidianStore,
    captures: CaptureRecordStore,
    contents: ContentObjectStore,
    raw: RawObjectStore,
    *,
    version: str = "2",
) -> None:
    @app.post(f"/v{version}/receiver/capabilities")
    def permission(request: Request, body: AttachmentPermission) -> dict[str, object]:
        store.attachment_permission(
            request.state.destination_id, body.receiver_id, body.enabled, video=version == "3"
        )
        return {"protocol_version": version, "attachments": body.enabled}

    @app.get(f"/v{version}/destinations/{{destination_id}}/captures/{{capture_id}}/delivery")
    def lookup(destination_id: UuidPath, capture_id: CapturePath) -> dict[str, object]:
        existing = store.find(destination_id, capture_id)
        return {
            "delivery": receipt(package(existing, version)) if existing else None,
            "attachments_enabled": store.attachments_enabled(destination_id, video=version == "3"),
            "suggested_filename": suggested_filename(capture_id),
        }

    @app.post(f"/v{version}/deliveries", status_code=202)
    def submit(body: DeliveryRequest, response: Response) -> dict[str, object]:
        existing = store.find(body.destination_id, body.source_capture_id)
        if existing:
            response.status_code = 200
            return receipt(package(existing, version))
        if not store.attachments_enabled(body.destination_id, video=version == "3"):
            raise DeliveryError("receiver_upgrade_required")
        capture = captures.get(body.source_capture_id)
        if capture.status is not CaptureStatus.COMPLETE:
            raise DeliveryError("result_not_ready")
        content = contents.get_for_capture(capture.id)
        if version == "3":
            from unimem_video.export import VideoMarkdownRenderer, read_frame, video_attachments

            assets = video_attachments(content)
            if not assets:
                raise DeliveryError("protocol_mismatch")
            for a, ref in assets:
                read_frame(raw, a, ref)
            renderer3 = VideoMarkdownRenderer()
            d, created = store.register(
                body.destination_id,
                capture.id,
                content.id,
                renderer3.render(content),
                renderer3.name,
                renderer3.version,
                video_assets=assets,
            )
        else:
            attachment, ref = image_attachment(content, raw)
            renderer = image_renderer(content)
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
        return receipt(package(d, version))

    @app.get(f"/v{version}/receiver/deliveries/next")
    def next_delivery(request: Request) -> ImageDelivery | VideoDelivery | None:
        d = store.next(request.state.destination_id, version)
        return package(d, version) if d else None

    @app.get(f"/v{version}/receiver/deliveries/{{delivery_id}}")
    def get(request: Request, delivery_id: UuidPath) -> ImageDelivery | VideoDelivery:
        return package(store.get(request.state.destination_id, delivery_id), version)

    @app.post(f"/v{version}/receiver/deliveries/{{delivery_id}}/claim")
    def claim(
        request: Request, delivery_id: UuidPath, body: ClaimRequest
    ) -> ImageDelivery | VideoDelivery:
        get(request, delivery_id)
        if not store.attachments_enabled(request.state.destination_id, video=version == "3"):
            raise DeliveryError("receiver_upgrade_required")
        return package(store.claim(request.state.destination_id, delivery_id, body), version)

    @app.post(f"/v{version}/receiver/deliveries/{{delivery_id}}/ack")
    def ack(
        request: Request, delivery_id: UuidPath, body: PackageAckRequest
    ) -> ImageDelivery | VideoDelivery:
        get(request, delivery_id)
        return package(store.finish(request.state.destination_id, delivery_id, body), version)

    @app.post(f"/v{version}/receiver/deliveries/{{delivery_id}}/fail")
    def fail(
        request: Request, delivery_id: UuidPath, body: FailureRequest
    ) -> ImageDelivery | VideoDelivery:
        get(request, delivery_id)
        return package(store.finish(request.state.destination_id, delivery_id, body), version)

    @app.get(f"/v{version}/receiver/deliveries/{{delivery_id}}/assets/{{asset_id}}")
    def asset(request: Request, delivery_id: UuidPath, asset_id: CapturePath) -> Response:
        d = get(request, delivery_id)
        ref = store.asset_ref(request.state.destination_id, delivery_id, asset_id)
        a = next(a for a in d.attachments if a.asset_id == asset_id)
        with raw.open(raw_object_ref(parse_raw_ref(ref))) as stream:
            data = stream.read(a.size_bytes + 1)
        if len(data) != a.size_bytes or hashlib.sha256(data).hexdigest() != a.sha256:
            raise DeliveryError("asset_mismatch")
        return Response(data, media_type=a.mime_type)
