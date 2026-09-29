import pytest

pytest.importorskip("youtube_transcript_api")

from typing import Any

from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from tests.youtube_fixtures import URL, VIDEO_ID, XML
from tests.youtube_http import FakeHttp, happy_http, player, track
from unimem_youtube.errors import AcquisitionError
from unimem_youtube.retrieval import acquire
from unimem_youtube.transport import BoundedSession


def test_real_library_and_bounded_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    http = happy_http(monkeypatch)
    result = acquire(URL, ("ru", "en"))
    assert result.track.language_code == "ru"
    assert result.track.is_generated is False
    assert result.captions_xml == XML
    assert len(http.requests) == 3
    assert "lang=ru&kind=False" in str(http.requests[-1].url)
    for request, options in zip(http.requests, http.options, strict=True):
        assert "Cookie" not in request.headers
        assert "Authorization" not in request.headers
        assert options["timeout"] == (5.0, 10.0)
        assert options["verify"] is True
        assert options["proxies"] == {}


def test_language_priority_beats_manual_in_lower_priority_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = happy_http(monkeypatch)
    http.responses[1] = (200, player([track("en"), track("ru", generated=True)]))
    assert acquire(URL, ("ru", "en")).track.is_generated is True


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (player([track("fr")]), "language_unavailable"),
        (player([]), "captions_unavailable"),
        (b'{"playabilityStatus":{"status":"OK"}}', "captions_unavailable"),
        (
            b'{"playabilityStatus":{"status":"LOGIN_REQUIRED",'
            rb'"reason":"Sign in to confirm you\u2019re not a bot"}}',
            "request_blocked",
        ),
        (
            b'{"playabilityStatus":{"status":"ERROR","reason":"This video is unavailable"}}',
            "video_unavailable",
        ),
        (b"not json", "invalid_response"),
        (player([track("ru", host="127.0.0.1")]), "destination_denied"),
    ],
)
def test_metadata_errors_are_distinct(
    monkeypatch: pytest.MonkeyPatch, body: bytes, code: str
) -> None:
    http = happy_http(monkeypatch)
    http.responses[1] = (200, body)
    with pytest.raises(AcquisitionError) as failure:
        acquire(URL, ("ru", "en"))
    assert failure.value.code == code
    assert len(http.requests) == 2


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (429, b"sensitive server response", "request_blocked"),
        (403, b"denied", "request_blocked"),
        (500, b"server details", "http_error"),
        (302, b"redirect", "redirect_denied"),
        (200, b'<form action="https://consent.youtube.com/s">', "access_required"),
        (200, b"x" * (4 * 1024 * 1024 + 1), "response_limit"),
    ],
)
def test_http_refusals(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, code: str
) -> None:
    http = FakeHttp([(status, body)])
    http.install(monkeypatch)
    with pytest.raises(AcquisitionError) as failure:
        acquire(URL, ("ru",))
    assert failure.value.code == code
    assert len(http.requests) == 1
    assert "sensitive" not in str(failure.value)


@pytest.mark.parametrize(
    ("error", "code"), [(Timeout, "timeout"), (RequestsConnectionError, "network_error")]
)
def test_network_errors(monkeypatch: pytest.MonkeyPatch, error: type[Exception], code: str) -> None:
    def fail(self: HTTPAdapter, request: PreparedRequest, **kwargs: Any) -> Response:
        raise error("do not disclose URL or server details")

    monkeypatch.setattr(HTTPAdapter, "send", fail)
    with pytest.raises(AcquisitionError) as failure:
        acquire(URL, ("ru",))
    assert failure.value.code == code
    assert "disclose" not in str(failure.value)


def test_request_and_elapsed_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttp([(200, b"ok")] * 3).install(monkeypatch)
    with BoundedSession(VIDEO_ID) as session:
        for _ in range(3):
            session.get(URL)
        with pytest.raises(AcquisitionError, match="request limit"):
            session.get(URL)
    with BoundedSession(VIDEO_ID) as session:
        session.started -= 46
        with pytest.raises(AcquisitionError, match="elapsed-time"):
            session.get(URL)


def test_no_environment_credentials_or_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with BoundedSession(VIDEO_ID) as session:
        assert session.trust_env is False
        session.cookies.set("CONSENT", "anything")
        with pytest.raises(AcquisitionError, match="cookies or login"):
            session.get(URL)


@pytest.mark.parametrize(
    "destination",
    [
        "https://www.youtube.com/api/timedtext?v=lmnopqrstuv",
        f"https://www.youtube.com/api/timedtext?v={VIDEO_ID}&tlang=de",
        f"http://www.youtube.com/api/timedtext?v={VIDEO_ID}",
        f"https://www.youtube.com.evil.test/api/timedtext?v={VIDEO_ID}",
        f"https://user@www.youtube.com/api/timedtext?v={VIDEO_ID}",
        "https://www.youtube.com/videoplayback",
    ],
)
def test_network_destination_is_closed(destination: str) -> None:
    with BoundedSession(VIDEO_ID) as session, pytest.raises(AcquisitionError) as failure:
        session.get(destination)
    assert failure.value.code == "destination_denied"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not XML",
        b'<transcript><text start="1">broken',
        b"\xff",
        b'<transcript><text start="1">' + b"x" * (2 * 1024 * 1024) + b"</text></transcript>",
    ],
)
def test_caption_response_errors(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    http = happy_http(monkeypatch)
    http.responses[-1] = (200, body)
    with pytest.raises(AcquisitionError) as failure:
        acquire(URL, ("ru",))
    assert failure.value.code in {"invalid_response", "response_limit"}
    assert len(http.requests) == 3
