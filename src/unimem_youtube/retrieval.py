"""Real, optional YouTube retrieval. No import of this module on offline render."""

from datetime import UTC, datetime
from importlib.metadata import version
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from pydantic import ValidationError
from requests.exceptions import JSONDecodeError, RequestException, Timeout
from urllib3.exceptions import ReadTimeoutError
from youtube_transcript_api import (
    AgeRestricted,
    FailedToCreateConsentCookie,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
    YouTubeTranscriptApiException,
)

from unimem_youtube.artifact import CaptionArtifact, CaptionTrack
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.source import source_url, validate_languages, video_id_from_url
from unimem_youtube.transport import BoundedSession


def acquire(url: str, languages: tuple[str, ...]) -> CaptionArtifact:
    """Language priority first, manual before generated within each language.

    No translation, fallback to a different language, proxy rotation, cookies,
    video download or retries. Library exceptions are converted to safe codes.
    """
    video_id = video_id_from_url(url)
    validate_languages(languages)
    try:
        with BoundedSession(video_id) as session:
            tracks = list(YouTubeTranscriptApi(http_client=session).list(video_id))
            if not tracks:
                raise AcquisitionError(
                    "captions_unavailable", "YouTube returned no caption tracks."
                )
            selected = next(
                (
                    track
                    for language in languages
                    for generated in (False, True)
                    for track in tracks
                    if track.language_code == language and track.is_generated is generated
                ),
                None,
            )
            if selected is None:
                raise AcquisitionError(
                    "language_unavailable", "No track matches the requested languages."
                )
            # Public API performs the request. Keep the response body rather than
            # its lossy parsed snippets (missing duration becomes 0 in 1.2.4).
            selected.fetch(preserve_formatting=True)
            if not session.caption_body:
                raise AcquisitionError(
                    "invalid_response", "YouTube returned an empty caption body."
                )
            return CaptionArtifact(
                retrieval_version=version("youtube-transcript-api"),
                video_id=video_id,
                source_url=source_url(video_id),
                captured_at=datetime.now(UTC),
                track=CaptionTrack(
                    language=selected.language,
                    language_code=selected.language_code,
                    is_generated=selected.is_generated,
                    is_translatable=selected.is_translatable,
                ),
                captions_xml=session.caption_body.decode("utf-8"),
            )
    except AcquisitionError:
        raise
    except (RequestBlocked, PoTokenRequired):
        raise AcquisitionError(
            "request_blocked", "YouTube blocked this request; no bypass attempted."
        ) from None
    except (AgeRestricted, FailedToCreateConsentCookie):
        raise AcquisitionError(
            "access_required", "YouTube requires login or consent; not attempted."
        ) from None
    except TranscriptsDisabled:
        raise AcquisitionError(
            "captions_unavailable", "YouTube reports captions unavailable."
        ) from None
    except NoTranscriptFound:
        raise AcquisitionError(
            "language_unavailable", "No track matches the requested languages."
        ) from None
    except (VideoUnavailable, VideoUnplayable):
        raise AcquisitionError(
            "video_unavailable", "YouTube reports this video unavailable."
        ) from None
    except Timeout:
        raise AcquisitionError("timeout", "YouTube request timed out.") from None
    except JSONDecodeError:
        raise AcquisitionError("invalid_response", "YouTube returned damaged JSON.") from None
    except RequestException as exc:
        # Requests wraps a streamed urllib3 read timeout in ConnectionError.
        if exc.args and isinstance(exc.args[0], ReadTimeoutError):
            raise AcquisitionError("timeout", "YouTube response timed out.") from None
        raise AcquisitionError(
            "network_error", "Could not complete the YouTube network request."
        ) from None
    except (
        YouTubeTranscriptApiException,
        ValidationError,
        ParseError,
        DefusedXmlException,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        raise AcquisitionError(
            "invalid_response", "YouTube returned an unsupported or damaged response."
        ) from None
