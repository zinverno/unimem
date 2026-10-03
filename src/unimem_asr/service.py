"""Original-first intake and normal orchestration, without changing --media."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

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
from core.processing import ProcessingOrchestrator, ProcessorRouter
from core.storage import LocalRawObjectStore
from unimem_asr.policy import AsrError
from unimem_asr.processor import AudioTranscriptionProcessor
from unimem_asr.result import Transcript


class AudioCaptureService:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(data_dir / "unimem.sqlite3")
        self.content_store = SqliteContentObjectStore(data_dir / "unimem.sqlite3")

    def capture(
        self,
        *,
        capture_id: str,
        operation_id: str,
        file_ref: str,
        mime_type: str,
        captured_at: datetime,
        recognize: Callable[[BinaryIO], Transcript],
    ) -> ContentObject:
        stored = CaptureIntake(self.raw_store, self.record_store, media_enabled=True).accept(
            CaptureEnvelope(
                id=capture_id,
                source=CaptureSource(type=CaptureSourceType.UPLOAD, provider="local-audio"),
                payload=CapturePayload(
                    type=CapturePayloadType.AUDIO, file_ref=file_ref, mime_type=mime_type
                ),
                context=CaptureContext(captured_at=captured_at, application="unimem-audio"),
            )
        )
        return ProcessingOrchestrator(
            ProcessorRouter([AudioTranscriptionProcessor(self.raw_store, recognize, operation_id)]),
            self.record_store,
            self.content_store,
        ).process(stored.id)

    def read(self, capture_id: str) -> ContentObject:
        if self.record_store.get(capture_id).status is not CaptureStatus.COMPLETE:
            raise AsrError("result_not_ready")
        return self.content_store.get_for_capture(capture_id)
