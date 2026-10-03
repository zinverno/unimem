"""Image boundary validation and composition; canonical owners remain in core."""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentObject,
)
from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import (
    ImageOcrProcessor,
    ImageProcessor,
    ProcessingOrchestrator,
    Processor,
    ProcessorRouter,
)
from core.processing.image_recognition import ImageOcr
from core.storage import LocalRawObjectStore
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_operations import ImageOperation

if TYPE_CHECKING:
    from unimem_vision.engine import Description
from unimem_images import (
    IMAGE_MEMORY as IMAGE_MEMORY,
)
from unimem_images import (
    IMAGE_SECONDS as IMAGE_SECONDS,
)
from unimem_images import (
    MAX_IMAGE_BYTES as MAX_IMAGE_BYTES,
)
from unimem_images import (
    MAX_IMAGE_PIXELS as MAX_IMAGE_PIXELS,
)
from unimem_images import (
    ImageError as ImageError,
)
from unimem_images import (
    inspect_image as inspect_image,
)
from unimem_images import (
    validate_pixels as validate_pixels,
)


class ImageCaptureService:
    def __init__(self, data_dir: Path) -> None:
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(data_dir / "unimem.sqlite3")
        self.content_store = SqliteContentObjectStore(data_dir / "unimem.sqlite3")

    def capture(
        self,
        op: ImageOperation,
        ocr: ImageOcr | None,
        describe: Callable[[BinaryIO], "Description"] | None = None,
    ) -> ContentObject:
        request = op.request
        with self.raw_store.open(raw_object_ref(parse_raw_ref(request.file_ref))) as stream:
            data, mime, header = inspect_image(stream, request.declared_mime)
        validate_pixels(data, header)
        if request.mode == "ocr" and ocr is None:
            raise ImageError("ocr_disabled")
        if request.mode == "describe" and describe is None:
            raise ImageError("vision_disabled")
        stored = CaptureIntake(self.raw_store, self.record_store).accept(
            CaptureEnvelope(
                id=op.reserved_capture_id,
                source=CaptureSource(type=CaptureSourceType.UPLOAD, provider="local-image"),
                payload=CapturePayload(
                    type=CapturePayloadType.IMAGE, file_ref=request.file_ref, mime_type=mime
                ),
                context=CaptureContext(captured_at=request.captured_at, application="unimem-image"),
            )
        )
        processor: Processor = (
            ImageOcrProcessor(self.raw_store, ocr)
            if ocr is not None and request.mode == "ocr"
            else ImageProcessor(self.raw_store)
        )
        if request.mode == "describe" and describe is not None:
            from unimem_vision.processor import ImageDescriptionProcessor

            processor = ImageDescriptionProcessor(self.raw_store, describe)
        return ProcessingOrchestrator(
            ProcessorRouter([processor]),
            self.record_store,
            self.content_store,
        ).process(stored.id)

    def read(self, capture_id: str) -> ContentObject:
        if self.record_store.get(capture_id).status is not CaptureStatus.COMPLETE:
            raise ImageError("result_not_ready")
        return self.content_store.get_for_capture(capture_id)


def ocr_status(content: ContentObject) -> str:
    meta = content.metadata.get("image_ocr")
    if not isinstance(meta, dict):
        return "not_requested"
    if not meta.get("engine_invoked"):
        return "skipped"
    return "text" if content.segments else "empty"
