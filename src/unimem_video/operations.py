"""Video receipts reuse the durable operation store, claim and recovery protocol."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from unimem_delivery.operations import DurableOperationStore, Operation, OperationRequest, State
from unimem_video.policy import ASR_PROFILE, DECODER, SAMPLING, VISION_PROFILE


class VideoRequest(OperationRequest):
    file_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    declared_mime: str = Field(default="", max_length=100)
    speech: bool = Field(strict=True)
    language: Literal["ru", "en", "auto"]
    frames: bool = Field(strict=True)
    describe: bool = Field(default=False, strict=True)
    captured_at: AwareDatetime
    decoder: str = Field(default=DECODER, max_length=100)
    sampling: str = Field(default=SAMPLING, max_length=100)
    asr_profile: str | None = Field(default=None, max_length=100)
    vision_profile: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.describe and not self.frames:
            raise ValueError("description requires frames")
        if (self.asr_profile is not None) != self.speech or (
            self.vision_profile is not None
        ) != self.describe:
            raise ValueError("explicit profiles must match requested stages")
        return self

    def supported(self) -> bool:
        return (
            self.decoder == DECODER
            and self.sampling == SAMPLING
            and self.asr_profile == (ASR_PROFILE if self.speech else None)
            and self.vision_profile == (VISION_PROFILE if self.describe else None)
        )


Stage = Literal["waiting", "extracting", "transcribing", "frame_1", "frame_2", "frame_3", "saving"]


class VideoOperation(Operation[VideoRequest]):
    stage: Stage = "waiting"
    execution_seconds: float | None = Field(default=None, ge=0)
    peak_process_tree_rss_bytes: int | None = Field(default=None, ge=0)


class VideoOperationStore(DurableOperationStore[VideoRequest]):
    def __init__(self, database: Path) -> None:
        super().__init__(
            database,
            request_type=VideoRequest,
            operation_type=VideoOperation,
            table="video_operations",
            capacity=8,
        )

    def stage(self, operation_id: str, stage: Stage) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,state,payload FROM video_operations WHERE id=?", (operation_id,)
            ).fetchone()
            op = self._decode(row)
            if op.state != "running":
                return
            updated = op.model_copy(update={"stage": stage, "updated_at": datetime.now(UTC)})
            db.execute(
                "UPDATE video_operations SET payload=? WHERE id=?",
                (updated.model_dump_json(), operation_id),
            )

    def resources(self, operation_id: str, seconds: float, peak: int) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,state,payload FROM video_operations WHERE id=?", (operation_id,)
            ).fetchone()
            op = self._decode(row)
            updated = op.model_copy(
                update={"execution_seconds": seconds, "peak_process_tree_rss_bytes": peak}
            )
            db.execute(
                "UPDATE video_operations SET payload=? WHERE id=?",
                (updated.model_dump_json(), operation_id),
            )

    def finish(
        self,
        op: Operation[VideoRequest],
        state: State,
        *,
        capture_id: str | None = None,
        content_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        # Preserve the last truthful stage when the parent reconciles a killed child.
        super().finish(
            self.get(op.request.operation_id),
            state,
            capture_id=capture_id,
            content_id=content_id,
            error_code=error_code,
        )
