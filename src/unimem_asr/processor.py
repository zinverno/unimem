"""One new-capture processor, composed only by the explicit ASR scenario."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import uuid4

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
from core.processing.errors import ProcessingInputError, ProcessingOutputError
from core.storage import RawObjectStore
from unimem_asr.policy import MODEL_ID, MODEL_REVISION, PARAMETERS
from unimem_asr.result import Transcript


class AudioTranscriptionProcessor:
    name = "local-audio-transcription"
    version = "1"

    def __init__(
        self,
        raw_store: RawObjectStore,
        recognize: Callable[[BinaryIO], Transcript],
        operation_id: str,
    ) -> None:
        self.raw_store, self.recognize, self.operation_id = raw_store, recognize, operation_id

    def supports(self, capture: CaptureRecord) -> bool:
        return capture.payload_type is CapturePayloadType.AUDIO

    def process(self, capture: CaptureRecord) -> ContentObject:
        raw = capture.raw_object
        if not self.supports(capture) or raw is None or raw.ref is None or raw.mime_type is None:
            raise ProcessingInputError("audio original required")
        started = datetime.now(UTC)
        with self.raw_store.open(raw) as stream:
            result = self.recognize(stream)
        try:
            result = Transcript.model_validate_json(result.model_dump_json())
        except ValueError:
            raise ProcessingOutputError("invalid ASR result") from None
        original = Asset(
            id=str(uuid4()),
            role=AssetRole.ORIGINAL,
            ref=raw.ref,
            mime_type=raw.mime_type,
            sha256=raw.sha256,
        )
        return ContentObject(
            id=str(uuid4()),
            type=ContentType.AUDIO,
            source=ContentSource(capture_id=capture.id, provider="local-audio"),
            original=OriginalReference(
                asset_id=original.id, mime_type=raw.mime_type, sha256=raw.sha256
            ),
            title="Аудиозапись — автоматическая расшифровка",
            assets=[original],
            metadata={
                "audio_transcription": {
                    **result.model_dump(mode="json", exclude={"segments"}),
                    "operation_id": self.operation_id,
                    "captured_at": capture.context.captured_at.isoformat()
                    if capture.context
                    else None,
                    "model": MODEL_ID,
                    "model_revision": MODEL_REVISION,
                    "parameters": PARAMETERS,
                    "engine": "faster-whisper",
                }
            },
            segments=[
                Segment(
                    id=str(uuid4()),
                    type=SegmentType.TRANSCRIPT,
                    text=cue.text,
                    position=index,
                    temporal=TemporalLocation(start=cue.start, end=cue.end),
                    provenance=Provenance(
                        capture_id=capture.id,
                        asset_id=original.id,
                        source_type=ProvenanceSourceType.TRANSCRIPT,
                        processor=self.name,
                        processor_version=self.version,
                    ),
                )
                for index, cue in enumerate(result.segments)
            ],
            processing=[
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    status=ProcessingStatus.COMPLETE,
                    warnings=["Automatic transcription; recognition may contain errors."],
                )
            ],
        )
