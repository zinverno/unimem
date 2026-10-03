"""Explicit new-capture interpretation, reusing the existing original image processor."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import uuid4

from core.contracts import (
    CaptureRecord,
    ContentObject,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
    Segment,
    SegmentType,
)
from core.processing.image import ImageProcessor
from core.storage import RawObjectStore
from unimem_vision.engine import Description, provenance
from unimem_vision.policy import VisionError


class ImageDescriptionProcessor(ImageProcessor):
    name = "local-image-description"
    version = "1"

    def __init__(
        self, raw_store: RawObjectStore, recognize: Callable[[BinaryIO], Description]
    ) -> None:
        super().__init__(raw_store)
        self.raw_store = raw_store
        self.recognize = recognize
        self.original_processor = ImageProcessor(raw_store)

    def process(self, capture: CaptureRecord) -> ContentObject:
        started = datetime.now(UTC)
        original = self.original_processor.process(capture)
        assert capture.raw_object is not None
        assert original.original is not None
        with self.raw_store.open(capture.raw_object) as stream:
            result = Description.model_validate_json(self.recognize(stream).model_dump_json())
        if result.answer.status == "refused" or not result.answer.description.strip():
            raise VisionError("vision_invalid_output")
        fields = original.model_dump()
        fields["segments"] = [
            Segment(
                id=str(uuid4()),
                type=SegmentType.VISUAL,
                text=result.answer.description,
                position=0,
                provenance=Provenance(
                    capture_id=capture.id,
                    asset_id=original.original.asset_id,
                    source_type=ProvenanceSourceType.VISION,
                    processor=self.name,
                    processor_version=self.version,
                ),
            )
        ]
        fields["metadata"]["image_description"] = {
            **result.model_dump(mode="json"),
            **provenance(),
        }
        fields["processing"] = [
            *original.processing,
            ProcessingRecord(
                processor=self.name,
                processor_version=self.version,
                started_at=started,
                completed_at=datetime.now(UTC),
                status=ProcessingStatus.COMPLETE,
                warnings=["Model interpretation may contain invented or omitted details. Not OCR."],
            ),
        ]
        # Only this canonical result reaches persistence. Never save a placeholder
        # original-only result on a failed, empty, refused or interrupted inference.
        return ContentObject.model_validate(fields)
