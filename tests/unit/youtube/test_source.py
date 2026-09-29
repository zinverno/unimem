import pytest

from tests.youtube_fixtures import VIDEO_ID
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.source import validate_languages, video_id_from_url


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=ignored&t=4",
        f"http://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?si=ignored",
        *(f"https://youtube.com/{path}/{VIDEO_ID}" for path in ("shorts", "embed", "live")),
    ],
)
def test_supported_urls(url: str) -> None:
    assert video_id_from_url(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.test/watch?v=abcdefghijk",
        "https://youtube.com@evil.test/watch?v=abcdefghijk",
        "https://evil.test@youtube.com/watch?v=abcdefghijk",
        "https://youtube.com./watch?v=abcdefghijk",
        "https://youtube.com:443/watch?v=abcdefghijk",
        "https://youtube.com:bad/watch?v=abcdefghijk",
        "https://youtube.com\\@evil.test/watch?v=abcdefghijk",
        "https://youtube.com/watch?v=abcdefghijk&v=lmnopqrstuv",
        "https://youtube.com/playlist?list=abcdefghijk",
        "https://youtu.be/abcdefghijk/extra",
        "abcdefghijk",
        "file:///tmp/video",
        "https://youtube.com/watch?v=too_short",
        "https://youtube.com/watch?v=abcdefghij%2F",
        "\nhttps://youtube.com/watch?v=abcdefghijk",
        "https://127.0.0.1/watch?v=abcdefghijk",
    ],
)
def test_url_spoofing_and_ambiguity_rejected(url: str) -> None:
    with pytest.raises(AcquisitionError) as failure:
        video_id_from_url(url)
    assert failure.value.code == "invalid_url"
    assert url not in str(failure.value)


@pytest.mark.parametrize("languages", [(), ("",), ("../ru",), ("ru,en",), ("en",) * 11])
def test_language_input(languages: tuple[str, ...]) -> None:
    with pytest.raises(AcquisitionError, match="language codes"):
        validate_languages(languages)
