"""HTTP fixtures below the real retrieval library and UniMem network policy."""

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter

from tests.youtube_fixtures import VIDEO_ID, XML


def track(
    language: str, *, generated: bool = False, host: str = "www.youtube.com"
) -> dict[str, object]:
    return {
        "baseUrl": f"https://{host}/api/timedtext?v={VIDEO_ID}&lang={language}&kind={generated}",
        "languageCode": language,
        "name": {"runs": [{"text": language}]},
        "kind": "asr" if generated else "standard",
        "isTranslatable": False,
    }


def player(tracks: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "playabilityStatus": {"status": "OK"},
            "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": tracks}},
        }
    ).encode()


@dataclass
class FakeHttp:
    responses: list[tuple[int, bytes]]
    requests: list[PreparedRequest] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def send(adapter: HTTPAdapter, request: PreparedRequest, **kwargs: Any) -> Response:
            self.requests.append(request)
            self.options.append(kwargs)
            status, data = self.responses.pop(0)
            response = Response()
            response.status_code = status
            response.raw = io.BytesIO(data)
            response.url = request.url or ""
            response.request = request
            if status == 302:
                response.headers["Location"] = "http://127.0.0.1/private"
            return response

        monkeypatch.setattr(HTTPAdapter, "send", send)


def happy_http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp(
        [
            (200, b'{"INNERTUBE_API_KEY":"public-page-key"}'),
            (200, player([track("en"), track("ru", generated=True), track("ru")])),
            (200, XML.encode()),
        ]
    )
    http.install(monkeypatch)
    return http
