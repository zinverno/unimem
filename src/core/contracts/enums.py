"""Closed sets of the domain language.

Values are lowercase and stable: they are part of the serialized wire format
and must not change meaning between schema versions.
"""

from enum import StrEnum


class CaptureSourceType(StrEnum):
    """Where a capture entered the system from."""

    BROWSER = "browser"
    FILESYSTEM = "filesystem"
    UPLOAD = "upload"
    API = "api"


class CapturePayloadType(StrEnum):
    """What kind of thing was captured.

    Members are listed in the order they entered the vocabulary, so the tail of
    the list reads as its history. ``AUDIO`` arrived last, in schema version
    0.3, and is the only member a supported version can lack — see
    :data:`~core.contracts.base.AUDIO_SCHEMA_VERSIONS`. ``VIDEO`` has been here
    since 0.1 and is valid at every supported version, which is why standalone
    audio needed its own member rather than being folded into it: an audio file
    is not a video with no pictures.
    """

    TEXT = "text"
    WEBPAGE = "webpage"
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    FILE = "file"
    URL = "url"
    AUDIO = "audio"


class CaptureStatus(StrEnum):
    """Lifecycle state of an accepted capture."""

    RECEIVED = "received"
    STORED = "stored"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ContentType(StrEnum):
    """Kind of a canonical :class:`~core.contracts.content.ContentObject`.

    Listed in the order they entered the vocabulary, like
    :class:`CapturePayloadType`. ``AUDIO`` arrived in schema version 0.3;
    ``VIDEO`` has been here since 0.1.
    """

    TEXT = "text"
    WEB = "web"
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    AUDIO = "audio"


class SegmentType(StrEnum):
    """Kind of a structured content segment."""

    TEXT = "text"
    SECTION = "section"
    TRANSCRIPT = "transcript"
    OCR = "ocr"
    VISUAL = "visual"


class ProvenanceSourceType(StrEnum):
    """Which representation a segment was derived from."""

    ORIGINAL = "original"
    HTML = "html"
    OCR = "ocr"
    TRANSCRIPT = "transcript"
    VISION = "vision"
    PROCESSOR = "processor"


class AssetRole(StrEnum):
    """Role a binary or external artifact plays for a content object."""

    ORIGINAL = "original"
    IMAGE = "image"
    KEYFRAME = "keyframe"
    THUMBNAIL = "thumbnail"
    AUDIO = "audio"
    ATTACHMENT = "attachment"


class ProcessingStatus(StrEnum):
    """Outcome of a processing run. ``partial`` is a first-class state."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class IntentAction(StrEnum):
    """What the capturing user asked the system to do with the capture."""

    SAVE = "save"
    ANALYZE = "analyze"
