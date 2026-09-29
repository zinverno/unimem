"""Application operations over the existing stores and lifecycle owners."""

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

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
from core.intake.errors import CaptureIntakeError
from core.persistence import (
    CaptureRecordStoreError,
    ContentObjectStoreError,
    SqliteCaptureRecordStore,
    SqliteContentObjectStore,
)
from core.processing import ProcessingOrchestrator, ProcessorRouter
from core.processing.errors import ProcessingError
from core.storage import LocalRawObjectStore, RawObjectStoreError
from unimem_youtube.artifact import CAPTION_MIME, MAX_ARTIFACT_BYTES, CaptionArtifact
from unimem_youtube.errors import AcquisitionError, CapturePipelineError, ExportError
from unimem_youtube.processor import YoutubeCaptionProcessor
from unimem_youtube.source import source_url, validate_languages, video_id_from_url


def acquire_captions(url: str, languages: tuple[str, ...]) -> CaptionArtifact:
    """Only this operation imports the optional retrieval stack."""
    try:
        from unimem_youtube.retrieval import acquire
    except ImportError:
        raise AcquisitionError(
            "dependency_missing", 'Capture requires the optional extra: pip install ".[youtube]".'
        ) from None
    return acquire(url, languages)


class YoutubeCaptureService:
    """Callable from any delivery adapter. No CLI/HTTP/browser types or lifecycle writes.

    Stores use the same raw/ and unimem.sqlite3 layout as the local API; each
    SQLite operation already closes its connection. Recreating this service
    reads the same canonical data without retrieving or processing it again.
    """

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(data_dir / "unimem.sqlite3")
        self.content_store = SqliteContentObjectStore(data_dir / "unimem.sqlite3")
        self.intake = CaptureIntake(
            self.raw_store,
            self.record_store,
            caption_artifacts_enabled=True,
        )
        self.orchestrator = ProcessingOrchestrator(
            ProcessorRouter([YoutubeCaptionProcessor(self.raw_store)]),
            self.record_store,
            self.content_store,
        )

    def capture(
        self,
        url: str,
        languages: tuple[str, ...],
        *,
        acquire: Callable[[str, tuple[str, ...]], CaptionArtifact] = acquire_captions,
    ) -> ContentObject:
        video_id = video_id_from_url(url)
        validate_languages(languages)
        artifact = acquire(source_url(video_id), languages)
        # Revalidate a snapshot: a provider may return an in-place mutated model.
        data = artifact.model_dump_json().encode("utf-8")
        if len(data) > MAX_ARTIFACT_BYTES:
            raise AcquisitionError("response_limit", "Caption artifact exceeds its byte limit.")
        try:
            artifact = CaptionArtifact.model_validate_json(data)
        except ValueError:
            raise AcquisitionError("invalid_response", "Invalid caption artifact.") from None
        if artifact.video_id != video_id or artifact.track.language_code not in languages:
            raise AcquisitionError(
                "invalid_response", "Acquisition returned a different video or language."
            )
        raw = self.raw_store.store_bytes(data, mime_type=CAPTION_MIME)
        capture_id = str(uuid4())
        envelope = CaptureEnvelope(
            id=capture_id,
            source=CaptureSource(
                type=CaptureSourceType.API, provider="youtube", url=artifact.source_url
            ),
            payload=CapturePayload(
                type=CapturePayloadType.FILE, mime_type=CAPTION_MIME, file_ref=raw.ref
            ),
            context=CaptureContext(captured_at=artifact.captured_at, application="unimem-youtube"),
        )
        try:
            stored = self.intake.accept(envelope)
            return self.orchestrator.process(stored.id)
        except ProcessingError as exc:
            raise CapturePipelineError(capture_id, "processing_failed") from exc
        except CaptureIntakeError as exc:
            raise CapturePipelineError(capture_id, "intake_failed") from exc
        except (CaptureRecordStoreError, ContentObjectStoreError, RawObjectStoreError) as exc:
            raise CapturePipelineError(capture_id, "storage_failed") from exc

    def read(self, capture_id: str) -> ContentObject:
        record = self.record_store.get(capture_id)
        if record.status is not CaptureStatus.COMPLETE:
            raise ExportError(f"Capture is {record.status.value}; no completed result to export.")
        return self.content_store.get_for_capture(capture_id)
