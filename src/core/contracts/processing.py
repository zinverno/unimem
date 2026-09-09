"""Record of a processing run over a content object."""

from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from core.contracts.base import DomainModel, NonBlankStr
from core.contracts.enums import ProcessingStatus


class ProcessingRecord(DomainModel):
    """What a processor did to a content object, and how it went.

    A ``failed`` run is not required to carry errors and a ``partial`` run is
    not required to carry warnings: processors report what they know, and the
    contract does not invent bookkeeping obligations for them.
    """

    processor: NonBlankStr
    processor_version: NonBlankStr
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    status: ProcessingStatus
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_interval(self) -> Self:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")
        return self
