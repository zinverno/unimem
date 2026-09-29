"""Strict user URL parsing. Only the canonical video ID reaches acquisition."""

import re
from urllib.parse import parse_qs, urlsplit

from unimem_youtube.errors import AcquisitionError

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*\Z")


def video_id_from_url(url: str) -> str:
    """Accept watch, short, embed, live and youtu.be URLs, never playlist-only URLs."""
    try:
        if len(url) > 4096 or any(ord(char) <= 32 for char in url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            raise ValueError
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=40)
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch":
                values = query.get("v", [])
                if len(values) != 1:
                    raise ValueError
                video_id = values[0]
            else:
                parts = parsed.path.split("/")
                if len(parts) != 3 or parts[1] not in {"shorts", "embed", "live"}:
                    raise ValueError
                video_id = parts[2]
        else:
            raise ValueError
        if VIDEO_ID.fullmatch(video_id) is None:
            raise ValueError
        return video_id
    except ValueError:
        raise AcquisitionError("invalid_url", "Expected a supported YouTube video URL.") from None


def source_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def validate_languages(languages: tuple[str, ...]) -> None:
    if not 1 <= len(languages) <= 10 or any(not LANGUAGE.fullmatch(item) for item in languages):
        raise AcquisitionError("invalid_languages", "Supply 1-10 language codes in priority order.")
