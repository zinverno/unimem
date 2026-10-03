"""Validated ASR observations; no new canonical schema or transcript original."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from unimem_asr.policy import MAX_SECONDS, MAX_SEGMENTS, MAX_TEXT_BYTES


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=100_000)
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    container: Literal["wav", "mp3", "ogg"]
    codec: str
    sample_rate: int = Field(ge=8000, le=48000)
    channels: int = Field(ge=1, le=2)
    duration: float = Field(gt=0, le=MAX_SECONDS)
    selected_language: Literal["auto", "ru", "en"]
    detected_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    language_probability: float | None = Field(default=None, ge=0, le=1)
    outcome: Literal["transcribed", "no_speech"]
    segments: list[Cue] = Field(max_length=MAX_SEGMENTS)
    model_sha256: dict[str, str]
    versions: dict[str, str]
    elapsed_seconds: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.outcome == "transcribed") != bool(self.segments):
            raise ValueError("outcome does not match segments")
        previous = 0.0
        size = 0
        for cue in self.segments:
            # Whisper's timestamp grid is 20 ms; 100 ms also covers codec padding.
            if (
                not cue.text.strip()
                or cue.start < previous
                or cue.end < cue.start
                or cue.end > self.duration + 0.1
            ):
                raise ValueError("invalid ASR timeline")
            previous = cue.end
            size += len(cue.text.encode("utf-8"))
        if size > MAX_TEXT_BYTES:
            raise ValueError("ASR text budget exceeded")
        return self
