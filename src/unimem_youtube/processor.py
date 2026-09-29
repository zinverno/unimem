"""Normalize a stored external caption artifact, using only local input."""

import math
import uuid
from datetime import UTC, datetime
from xml.etree import ElementTree

from pydantic import JsonValue, ValidationError

from core.contracts import (
    Asset,
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    ContentObject,
    ContentSource,
    ContentType,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
    Segment,
    SegmentType,
    TemporalLocation,
)
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectStore
from unimem_youtube.artifact import CAPTION_MIME, MAX_ARTIFACT_BYTES, MAX_CUES, CaptionArtifact


def _seconds(value: str | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid cue time")
    return number


class YoutubeCaptionProcessor:
    name = "youtube-caption-xml"
    version = "1"

    def __init__(self, raw_store: RawObjectStore) -> None:
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        return (
            capture.payload_type is CapturePayloadType.FILE
            and capture.raw_object is not None
            and capture.raw_object.mime_type == CAPTION_MIME
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        started_at = datetime.now(UTC)
        raw = capture.raw_object
        if not self.supports(capture) or raw is None or raw.ref is None:
            raise ProcessingInputError("Expected a stored caption artifact.")
        with self._raw_store.open(raw) as stream:
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ProcessingInputError("Caption artifact exceeds its byte limit.")
        try:
            artifact = CaptionArtifact.model_validate_json(data)
            if (
                capture.source.provider != "youtube"
                or capture.source.url != artifact.source_url
                or capture.context is None
                or capture.context.captured_at != artifact.captured_at
            ):
                raise ValueError("artifact and receipt disagree")
            xml = artifact.captions_xml
            if "<!doctype" in xml.lower() or "<!entity" in xml.lower():
                raise ValueError("DTD and entities are not accepted")
            root = ElementTree.fromstring(xml)
            if root.tag != "transcript" or root.attrib or not 1 <= len(root) <= MAX_CUES:
                raise ValueError("unsupported transcript document")
            if (root.text or "").strip():
                raise ValueError("text outside a cue")
            asset = Asset(
                id=str(uuid.uuid4()),
                role=AssetRole.ORIGINAL,
                mime_type=CAPTION_MIME,
                ref=raw.ref,
                sha256=raw.sha256,
            )
            segments: list[Segment] = []
            for position, cue in enumerate(root):
                if (
                    cue.tag != "text"
                    or len(cue)
                    or set(cue.attrib) - {"start", "dur"}
                    or (cue.tail or "").strip()
                ):
                    raise ValueError("unsupported cue")
                text = cue.text or ""
                start = _seconds(cue.get("start"))
                duration = _seconds(cue.get("dur"))
                end = start + duration if start is not None and duration is not None else None
                if end is not None and not math.isfinite(end):
                    raise ValueError("invalid cue end")
                metadata: dict[str, JsonValue] = {}
                if duration is not None:
                    metadata["duration_seconds"] = duration
                if not text.strip():
                    metadata["caption_text"] = text
                segments.append(
                    Segment(
                        id=str(uuid.uuid4()),
                        type=SegmentType.TRANSCRIPT if text.strip() else SegmentType.SECTION,
                        text=text if text.strip() else None,
                        temporal=TemporalLocation(start=start, end=end)
                        if start is not None
                        else None,
                        position=position,
                        metadata=metadata,
                        provenance=Provenance(
                            capture_id=capture.id,
                            source_type=ProvenanceSourceType.ORIGINAL,
                            asset_id=asset.id,
                            processor=self.name,
                            processor_version=self.version,
                        ),
                    )
                )
            if not any(segment.text is not None for segment in segments):
                raise ValueError("track has no caption text")
        except (ValueError, ValidationError, ElementTree.ParseError, OverflowError):
            raise ProcessingInputError("Invalid or unsupported caption artifact.") from None

        return ContentObject(
            id=str(uuid.uuid4()),
            type=ContentType.TEXT,
            source=ContentSource(
                capture_id=capture.id, provider="youtube", url=artifact.source_url
            ),
            original=OriginalReference(
                asset_id=asset.id, mime_type=CAPTION_MIME, sha256=raw.sha256
            ),
            title=None,
            metadata={
                "youtube_captions": {
                    "artifact_format": artifact.format,
                    "origin": artifact.origin,
                    "serialization": artifact.serialization,
                    "video_id": artifact.video_id,
                    "captured_at": artifact.captured_at.isoformat(),
                    "track": artifact.track.model_dump(mode="json"),
                    "retrieval_library": artifact.retrieval_library,
                    "retrieval_version": artifact.retrieval_version,
                }
            },
            segments=segments,
            assets=[asset],
            processing=[
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    status=ProcessingStatus.COMPLETE,
                )
            ],
        )
