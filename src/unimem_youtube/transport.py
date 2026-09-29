"""Optional Requests transport with a closed destination policy and finite budgets."""

from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit

from requests import PreparedRequest, Response, Session
from requests.adapters import HTTPAdapter

from unimem_youtube.artifact import MAX_XML_BYTES
from unimem_youtube.errors import AcquisitionError

MAX_REQUESTS = 3  # watch page, player metadata, one caption track; zero retries
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
TOTAL_SECONDS = 45.0
SOCKET_TIMEOUT = (5.0, 10.0)


def allowed_destination(url: str, method: str) -> str:
    """Only the three endpoints used by the pinned library; reject every redirect."""
    parsed = urlsplit(url)
    if (
        len(url) > 8192
        or parsed.scheme != "https"
        or parsed.hostname != "www.youtube.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
        or "\\" in url
        or any(ord(char) <= 32 for char in url)
        or (method, parsed.path)
        not in {("GET", "/watch"), ("POST", "/youtubei/v1/player"), ("GET", "/api/timedtext")}
    ):
        raise AcquisitionError("destination_denied", "YouTube returned an unsupported destination.")
    return parsed.path


class BoundedSession(Session):
    """One instance per acquisition; no environment auth, proxies, cookies or redirects.

    The elapsed budget is checked at request/chunk boundaries. Requests socket
    timeouts bound network waits; this is not a hard deadline for OS DNS lookup.
    """

    def __init__(self, video_id: str) -> None:
        super().__init__()
        self.video_id = video_id
        self.trust_env = False
        self.mount("https://", HTTPAdapter(max_retries=0))
        self.requests_made = 0
        self.bytes_read = 0
        self.started = monotonic()
        self.caption_body: bytes | None = None

    def send(self, request: PreparedRequest, **kwargs: Any) -> Response:
        path = allowed_destination(request.url or "", request.method or "")
        if path in {"/watch", "/api/timedtext"}:
            query = parse_qs(urlsplit(request.url or "").query, keep_blank_values=True)
            if query.get("v") != [self.video_id] or "tlang" in query:
                raise AcquisitionError(
                    "destination_denied", "Caption destination changed the video."
                )
        if self.requests_made >= MAX_REQUESTS:
            raise AcquisitionError(
                "request_limit", "Caption acquisition exceeded its request limit."
            )
        self._check_time()
        if request.headers.get("Cookie") or request.headers.get("Authorization"):
            raise AcquisitionError(
                "access_required", "This acquisition would require cookies or login."
            )
        self.cookies.clear()
        self.requests_made += 1
        # Session.send may consume a redirect body even with allow_redirects=False
        # while preparing Response.next. Go straight to its configured adapter so
        # every response body remains behind our streaming budget, including 3xx.
        response = self.get_adapter(request.url or "").send(
            request,
            stream=True,
            timeout=SOCKET_TIMEOUT,
            proxies={},
            verify=True,
            cert=None,
        )
        try:
            if 300 <= response.status_code < 400:
                raise AcquisitionError(
                    "redirect_denied", "YouTube redirected the request; not followed."
                )
            if response.status_code in {401, 403, 429}:
                raise AcquisitionError(
                    "request_blocked", "YouTube denied or rate-limited this request."
                )
            if response.status_code != 200:
                raise AcquisitionError(
                    "http_error", f"YouTube returned HTTP {response.status_code}."
                )
            limit = MAX_XML_BYTES if path == "/api/timedtext" else MAX_RESPONSE_BYTES
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                self._check_time()
                self.bytes_read += len(chunk)
                if len(body) + len(chunk) > limit or self.bytes_read > MAX_TOTAL_BYTES:
                    raise AcquisitionError(
                        "response_limit", "YouTube response exceeded its byte limit."
                    )
                body.extend(chunk)
            # Requests' buffered response contract lets the library use .text/.json
            # after our bounded streaming read. No second HTTP request is involved.
            response._content = bytes(body)
            response.encoding = "utf-8"
            if path == "/watch" and b"consent.youtube.com/s" in body:
                raise AcquisitionError(
                    "access_required", "YouTube requires consent; no cookie is created."
                )
            if path == "/api/timedtext":
                self.caption_body = bytes(body)
            return response
        finally:
            response.close()
            self.cookies.clear()

    def _check_time(self) -> None:
        if monotonic() - self.started > TOTAL_SECONDS:
            raise AcquisitionError(
                "timeout", "Caption acquisition exceeded its elapsed-time budget."
            )
