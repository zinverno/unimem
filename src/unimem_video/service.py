"""One application service, one VIDEO capture, one persisted canonical result."""

import io
import resource
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, BinaryIO
from uuid import uuid4

from core.contracts import (
    Asset,
    AssetRole,
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
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
from core.contracts.base import JsonMapping
from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import ProcessingOrchestrator, ProcessorRouter
from core.storage import LocalRawObjectStore
from unimem_asr.policy import MODEL_ID, MODEL_REVISION, PARAMETERS
from unimem_asr.result import Transcript
from unimem_video.decode import DecodedVideo, decode
from unimem_video.operations import Stage, VideoOperation, VideoRequest
from unimem_video.policy import VideoError

if TYPE_CHECKING:
    from unimem_vision.engine import Description

Recognizer = Callable[[Path, DecodedVideo, VideoRequest], Transcript]
Describer = Callable[[BinaryIO], "Description"]


class VideoNotesProcessor:
    name = "local-video-notes"
    version = "1"

    def __init__(
        self,
        raw: LocalRawObjectStore,
        op: VideoOperation,
        stage: Callable[[Stage], None],
        recognize: Recognizer | None,
        describe: Describer | None,
    ) -> None:
        self.raw, self.op, self.stage = raw, op, stage
        self.recognize, self.describe = recognize, describe

    def supports(self, capture: CaptureRecord) -> bool:
        return capture.payload_type is CapturePayloadType.VIDEO

    def process(self, capture: CaptureRecord) -> ContentObject:
        started, clock = datetime.now(UTC), monotonic()
        raw, request = capture.raw_object, self.op.request
        if not self.supports(capture) or raw is None or raw.ref is None:
            raise VideoError("invalid_video")
        original = Asset(
            id=str(uuid4()),
            role=AssetRole.ORIGINAL,
            ref=raw.ref,
            sha256=raw.sha256,
            mime_type="video/mp4",
        )
        assets = [original]
        segments: list[Segment] = []
        transcript = None
        stage_times: JsonMapping = {}
        self.stage("extracting")
        with tempfile.TemporaryDirectory(prefix="unimem-video-") as directory:
            pcm = Path(directory) / "audio.s16le"
            with self.raw.open(raw) as source, pcm.open("wb") as output:
                decoded = decode(
                    source,
                    request.declared_mime,
                    output,
                    frames=request.frames,
                    speech=request.speech,
                )
            stage_times["extracting"] = monotonic() - clock
            outcome = "not_requested" if not request.speech else "no_audio_track"
            if request.speech and decoded.audio is not None:
                if self.recognize is None:
                    raise VideoError("asr_disabled")
                self.stage("transcribing")
                then = monotonic()
                transcript = Transcript.model_validate_json(
                    self.recognize(pcm, decoded, request).model_dump_json()
                )
                if (
                    transcript.container != "mp4"
                    or transcript.codec != "aac"
                    or transcript.selected_language != request.language
                    or abs(transcript.duration - pcm.stat().st_size / 32000) > 1 / 16000
                    or transcript.sample_rate != decoded.audio["sample_rate"]
                    or transcript.channels != decoded.audio["channels"]
                ):
                    raise VideoError("invalid_asr_result")
                stage_times["transcribing"] = monotonic() - then
                outcome = transcript.outcome
                offset = float(str(decoded.audio["offset_seconds"]))
                for cue in transcript.segments:
                    segments.append(
                        Segment(
                            id=str(uuid4()),
                            type=SegmentType.TRANSCRIPT,
                            text=cue.text,
                            temporal=TemporalLocation(
                                start=cue.start + offset, end=cue.end + offset
                            ),
                            provenance=Provenance(
                                capture_id=capture.id,
                                asset_id=original.id,
                                source_type=ProvenanceSourceType.TRANSCRIPT,
                                processor="local-audio-transcription",
                                processor_version="1",
                            ),
                            metadata={
                                "pcm_start": cue.start,
                                "pcm_end": cue.end,
                                "offset_seconds": offset,
                            },
                            position=len(segments),
                        )
                    )
        # Persist selected derivatives before invoking any vision model. They are
        # assets of this capture, never separate user captures or originals.
        for sample in decoded.frames:
            stored = self.raw.store_bytes(sample.png, mime_type="image/png")
            assert stored.ref is not None
            assets.append(
                Asset(
                    id=str(uuid4()),
                    role=AssetRole.KEYFRAME,
                    ref=stored.ref,
                    mime_type="image/png",
                    sha256=stored.sha256,
                    metadata={
                        **sample.facts,
                        "source_asset_id": original.id,
                        "source_sha256": original.sha256,
                    },
                )
            )
        for i, (asset, sample) in enumerate(zip(assets[1:], decoded.frames, strict=True)):
            if not request.describe:
                continue
            if self.describe is None:
                raise VideoError("vision_disabled")
            from unimem_vision.engine import Description, provenance

            self.stage(("frame_1", "frame_2", "frame_3")[i])
            then = monotonic()
            description = Description.model_validate_json(
                self.describe(io.BytesIO(sample.png)).model_dump_json()
            )
            if description.answer.status == "refused" or not description.answer.description.strip():
                raise VideoError("vision_invalid_output")
            stage_times[f"frame_{i + 1}"] = monotonic() - then
            segments.append(
                Segment(
                    id=str(uuid4()),
                    type=SegmentType.VISUAL,
                    text=description.answer.description,
                    temporal=TemporalLocation(
                        start=float(str(sample.facts["presentation_seconds"]))
                    ),
                    provenance=Provenance(
                        capture_id=capture.id,
                        asset_id=asset.id,
                        source_type=ProvenanceSourceType.VISION,
                        processor="local-image-description",
                        processor_version="1",
                    ),
                    metadata={
                        "description": {**description.model_dump(mode="json"), **provenance()}
                    },
                    position=len(segments),
                )
            )
        self.stage("saving")
        return ContentObject(
            id=str(uuid4()),
            type=ContentType.VIDEO,
            source=ContentSource(capture_id=capture.id, provider="local-video"),
            original=OriginalReference(
                asset_id=original.id, mime_type="video/mp4", sha256=raw.sha256
            ),
            title="Видео — речь и выбранные кадры",
            assets=assets,
            segments=segments,
            metadata={
                "video_notes": {
                    **decoded.facts,
                    "operation_id": request.operation_id,
                    "request": request.model_dump(mode="json"),
                    "audio": decoded.audio,
                    "speech_outcome": outcome,
                    "frames_outcome": "extracted" if request.frames else "not_requested",
                    "vision_outcome": "described" if request.describe else "not_requested",
                    "transcript": (
                        {
                            **transcript.model_dump(mode="json"),
                            "model": MODEL_ID,
                            "model_revision": MODEL_REVISION,
                            "parameters": PARAMETERS,
                        }
                        if transcript
                        else None
                    ),
                    "stage_seconds": stage_times,
                    "total_seconds": monotonic() - clock,
                    "peak_worker_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * 1024,
                    "peak_child_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                    * 1024,
                }
            },
            processing=[
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    status=ProcessingStatus.COMPLETE,
                    warnings=[
                        "Sampled still images do not describe all events. "
                        "ASR and vision may contain errors."
                    ],
                )
            ],
        )


class VideoCaptureService:
    def __init__(self, data_dir: Path) -> None:
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(data_dir / "unimem.sqlite3")
        self.content_store = SqliteContentObjectStore(data_dir / "unimem.sqlite3")

    def capture(
        self,
        op: VideoOperation,
        stage: Callable[[Stage], None],
        recognize: Recognizer | None,
        describe: Describer | None,
    ) -> ContentObject:
        request = op.request
        capture = CaptureIntake(self.raw_store, self.record_store, media_enabled=True).accept(
            CaptureEnvelope(
                id=op.reserved_capture_id,
                source=CaptureSource(type=CaptureSourceType.UPLOAD, provider="local-video"),
                payload=CapturePayload(
                    type=CapturePayloadType.VIDEO, file_ref=request.file_ref, mime_type="video/mp4"
                ),
                context=CaptureContext(captured_at=request.captured_at, application="unimem-video"),
            )
        )
        return ProcessingOrchestrator(
            ProcessorRouter([VideoNotesProcessor(self.raw_store, op, stage, recognize, describe)]),
            self.record_store,
            self.content_store,
        ).process(capture.id)

    def read(self, capture_id: str) -> ContentObject:
        if self.record_store.get(capture_id).status is not CaptureStatus.COMPLETE:
            raise VideoError("result_not_ready")
        return self.content_store.get_for_capture(capture_id)
