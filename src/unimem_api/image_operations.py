"""Explicit image operations over the existing durable acceptance mechanism."""

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from unimem_delivery.operations import DurableOperationStore, Operation, OperationRequest


class ImageRequest(OperationRequest):
    file_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    declared_mime: str = Field(default="", max_length=100)
    mode: Literal["original", "ocr", "describe"]
    captured_at: AwareDatetime


ImageOperation = Operation[ImageRequest]


class ImageOperationStore(DurableOperationStore[ImageRequest]):
    def __init__(self, database: Path, *, capacity: int = 8, history_limit: int = 10_000) -> None:
        super().__init__(
            database,
            request_type=ImageRequest,
            operation_type=ImageOperation,
            table="image_operations",
            capacity=capacity,
            history_limit=history_limit,
        )
