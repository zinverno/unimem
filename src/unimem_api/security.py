"""Local bearer authentication and bounded HTTP ingress. See ADR-025."""

import asyncio
import re
import secrets
from dataclasses import dataclass, field
from time import monotonic

from starlette.datastructures import Headers
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from unimem_api.obsidian_contract import DeliveryError
from unimem_api.obsidian_store import ObsidianStore

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
EXTENSION_ORIGIN = re.compile(
    r"(?:moz-extension://[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"|chrome-extension://[a-p]{32})\Z"
)


@dataclass(frozen=True)
class ApiSecurity:
    token: str = field(repr=False)
    port: int = 8765
    json_bytes: int = 8 * 1024 * 1024
    upload_bytes: int = 512 * 1024 * 1024
    requests_per_minute: int = 600
    concurrent_requests: int = 8
    body_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not TOKEN_PATTERN.fullmatch(self.token) or not 1 <= self.port <= 65535:
            raise ValueError("Invalid local API credential or port configuration.")
        if (
            min(
                self.json_bytes,
                self.upload_bytes,
                self.requests_per_minute,
                self.concurrent_requests,
                self.body_seconds,
            )
            <= 0
        ):
            raise ValueError("API limits must be positive.")


def refusal(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": "Request could not be served."}}, status_code=status
    )


class LocalApiSecurity:
    def __init__(
        self, app: ASGIApp, policy: ApiSecurity, receivers: ObsidianStore | None = None
    ) -> None:
        self.policy = policy
        self.receivers = receivers
        self.app = app
        self.json_app = RequestBodyLimitMiddleware(app, policy.json_bytes)
        self.upload_app = RequestBodyLimitMiddleware(app, policy.upload_bytes)
        self.window = monotonic()
        self.requests = 0
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        started = False

        async def safe_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [
                    *message.get("headers", []),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
                if origin and EXTENSION_ORIGIN.fullmatch(origin):
                    message["headers"] += [
                        (b"access-control-allow-origin", origin.encode()),
                        (b"vary", b"Origin"),
                    ]
            await send(message)

        now = monotonic()
        if now - self.window >= 60:
            self.window, self.requests = now, 0
        self.requests += 1
        code, status = "", 400
        hosts = {f"{host}:{self.policy.port}" for host in ("127.0.0.1", "localhost", "[::1]")}
        if self.policy.port == 80:
            hosts |= {"127.0.0.1", "localhost", "[::1]"}
        if len(headers.getlist("host")) != 1 or headers.get("host") not in hosts:
            code = "invalid_host"
        elif len(headers.getlist("origin")) > 1 or (
            origin is not None and not EXTENSION_ORIGIN.fullmatch(origin)
        ):
            code, status = "origin_denied", 403
        elif scope.get("query_string"):
            code = "query_not_supported"
        elif (
            self.requests > self.policy.requests_per_minute
            or self.active >= self.policy.concurrent_requests
        ):
            code, status = "request_limit", 429
        elif scope["method"] == "OPTIONS" and origin:
            if headers.get("access-control-request-method") not in {"GET", "POST"} or not {
                h.strip().lower()
                for h in headers.get("access-control-request-headers", "").split(",")
                if h.strip()
            } <= {"authorization", "content-type"}:
                code, status = "preflight_denied", 403
            else:
                await Response(
                    status_code=204,
                    headers={
                        "Access-Control-Allow-Methods": "GET, POST",
                        "Access-Control-Allow-Headers": "Authorization, Content-Type",
                        "Access-Control-Max-Age": "300",
                    },
                )(scope, receive, safe_send)
                return
        elif not (scope["path"] == "/health" and scope["method"] == "GET"):
            values = headers.getlist("authorization")
            if scope["path"].startswith("/v1/receiver/"):
                destination = None
                if (
                    self.receivers
                    and len(values) == 1
                    and values[0].startswith("Bearer ")
                    and TOKEN_PATTERN.fullmatch(values[0][7:])
                    and origin is None
                ):
                    # Reserve ingress capacity before yielding to credential IO.
                    self.active += 1
                    try:
                        destination = await asyncio.to_thread(
                            self.receivers.authenticate, values[0][7:]
                        )
                    except DeliveryError:
                        await refusal(503, "delivery_storage_unavailable")(
                            scope, receive, safe_send
                        )
                        return
                    finally:
                        self.active -= 1
                if destination is None:
                    code, status = "unauthorized", 401
                else:
                    scope.setdefault("state", {})["destination_id"] = destination
            elif len(values) != 1 or not secrets.compare_digest(
                values[0].encode("latin-1"), f"Bearer {self.policy.token}".encode()
            ):
                code, status = "unauthorized", 401
        if code:
            await refusal(status, code)(scope, receive, safe_send)
            return
        limit = (
            self.policy.upload_bytes if scope["path"] == "/v1/uploads" else self.policy.json_bytes
        )
        lengths = headers.getlist("content-length")
        if (
            len(lengths) > 1
            or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit()))
            or (lengths and "transfer-encoding" in headers)
        ):
            await refusal(400, "invalid_framing")(scope, receive, safe_send)
            return
        if lengths and (len(lengths[0]) > 20 or int(lengths[0]) > limit):
            await refusal(413, "body_too_large")(scope, receive, safe_send)
            return
        self.active += 1
        deadline = monotonic() + self.policy.body_seconds

        async def bounded_receive() -> Message:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                return await receive()

        try:
            body_app = self.upload_app if scope["path"] == "/v1/uploads" else self.json_app
            await body_app(scope, bounded_receive, safe_send)
        except TimeoutError:
            if not started:
                await refusal(408, "body_timeout")(scope, receive, safe_send)
        except Exception:
            # Do not let uvicorn log request data, credentials or backend stacks.
            if not started:
                await refusal(500, "internal_error")(scope, receive, safe_send)
        finally:
            self.active -= 1
