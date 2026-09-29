"""Version 1 of the stored input: UniMem JSON wrapping a caption response body.

This is neither a video original nor a byte-for-byte network response. Track
metadata comes from youtube-transcript-api; XML is the bounded, UTF-8 decoded
HTTP entity body. Keeping XML avoids the library's default duration=0 and text
filtering becoming invented timing or loss in our canonical representation.
"""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from unimem_youtube.source import VIDEO_ID, source_url

CAPTION_MIME = "application/vnd.unimem.youtube-captions+json"
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_CUES = 20_000


class CaptionTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: str = Field(min_length=1, max_length=256)
    language_code: str = Field(min_length=1, max_length=64)
    is_generated: bool
    is_translatable: bool


class CaptionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["unimem.youtube-captions/1"] = "unimem.youtube-captions/1"
    origin: Literal["youtube_caption_track"] = "youtube_caption_track"
    serialization: Literal["unimem_json_with_utf8_xml_body"] = "unimem_json_with_utf8_xml_body"
    retrieval_library: Literal["youtube-transcript-api"] = "youtube-transcript-api"
    retrieval_version: str = Field(min_length=1, max_length=64)
    video_id: str
    source_url: str
    captured_at: AwareDatetime
    track: CaptionTrack
    captions_xml: str = Field(min_length=1, max_length=MAX_XML_BYTES)

    @model_validator(mode="after")
    def check_source_and_size(self) -> Self:
        if not VIDEO_ID.fullmatch(self.video_id) or self.source_url != source_url(self.video_id):
            raise ValueError("inconsistent caption source")
        if len(self.captions_xml.encode("utf-8")) > MAX_XML_BYTES:
            raise ValueError("caption body exceeds byte limit")
        return self
