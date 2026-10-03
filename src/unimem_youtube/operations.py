"""YouTube request validation and the unchanged B1 table/receipt contract."""

from pathlib import Path

from pydantic import Field, field_validator

from unimem_delivery.operations import DurableOperationStore, Operation, OperationRequest
from unimem_delivery.operations import OperationError as OperationError
from unimem_delivery.operations import State as State
from unimem_youtube.source import source_url, validate_languages, video_id_from_url


class YoutubeRequest(OperationRequest):
    url: str = Field(max_length=4096)
    languages: tuple[str, ...] = ("en",)

    @field_validator("url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        return source_url(video_id_from_url(value))

    @field_validator("languages")
    @classmethod
    def language_priority(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validate_languages(value)
        return value


YoutubeOperation = Operation[YoutubeRequest]


class OperationStore(DurableOperationStore[YoutubeRequest]):
    def __init__(self, database: Path, *, capacity: int = 32, history_limit: int = 10_000) -> None:
        super().__init__(
            database,
            request_type=YoutubeRequest,
            operation_type=YoutubeOperation,
            table="youtube_operations",
            capacity=capacity,
            history_limit=history_limit,
        )
