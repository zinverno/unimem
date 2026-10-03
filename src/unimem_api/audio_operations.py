"""Explicit audio requests using the existing B1 durable receipt mechanism."""

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from unimem_delivery.operations import DurableOperationStore, Operation, OperationRequest


class AudioRequest(OperationRequest):
    file_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    declared_mime: str = Field(default="", max_length=100)
    language: Literal["auto", "ru", "en"] = "auto"
    captured_at: AwareDatetime
    profile: Literal["faster-whisper-base-cpu-int8-v1"] = "faster-whisper-base-cpu-int8-v1"


AudioOperation = Operation[AudioRequest]


class AudioOperationStore(DurableOperationStore[AudioRequest]):
    def __init__(self, database: Path, *, capacity: int = 8, history_limit: int = 10_000) -> None:
        super().__init__(
            database,
            request_type=AudioRequest,
            operation_type=AudioOperation,
            table="audio_operations",
            capacity=capacity,
            history_limit=history_limit,
        )
