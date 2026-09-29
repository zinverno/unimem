# ADR-025: Protected local delivery with durable YouTube operation IDs

Status: accepted for B1. Follows merged PR #31 / ADR-024. Supersedes ADR-011's
unauthenticated deployment and no-queue restriction **only as described here**;
ADR-013's synchronous capture replay and existing 200/201 meanings stay intact.

## Threat model and connection

An unrelated webpage must not read data or submit captures through localhost.
An unconnected local client must not do so either. Host substitution/DNS
rebinding, oversized/streaming bodies, request floods and accidental diagnostic
disclosure are in scope. OS compromise, same-user secret theft, malicious clients
already holding the credential, and exhausting the machine outside this service
are outside this boundary. There are no accounts, OAuth or cloud dependencies.

The operator explicitly initializes a random 256-bit bearer token in an
owner-only regular file (0600); explicit CLI commands display or rotate it.
Rotation takes effect after server restart. Reading refuses symlinks, another
owner and group/world permissions. No HTTP route issues secrets. No query
strings are accepted. Authorization is checked before request-body parsing on
**every** data/read/upload/docs route; only `GET /health` is public liveness.
CLI credentials are never implicit defaults. The fixed token in tests is only
an explicitly injected fixture, never a production fallback.

The CLI permits only loopback bindings. Every request needs an exact loopback
Host with the configured port; forwarded headers are not trusted. An absent
Origin still requires a credential. An Origin, if present, must be a serialized
`moz-extension://UUID` or `chrome-extension://[a-p]{32}` origin. This is a
class restriction, **not extension identity authentication**: every such origin
still needs the secret. The narrow preflight permits GET/POST and
Authorization/Content-Type only. Data responses reflect only an allowed origin,
never `*`, and carry `no-store`/`nosniff`. Ordinary web origins and `null` fail.

Why no single hard-coded Firefox origin: Mozilla documents random per-instance
resource UUIDs in [Chrome incompatibilities](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Chrome_incompatibilities#web_accessible_resources).
The next extension must fetch from its trusted background/extension context
with explicit loopback host permission; content scripts have different privileges
([MDN host permissions](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/host_permissions),
[Chrome network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)).
These documents were checked on 2026-09-29. This policy is tested as HTTP,
not certified against a running Zen, Firefox or Chromium browser.

Ingress limits: 8 MiB for JSON/other routes, 512 MiB total for multipart uploads
(including framing), 120 seconds to receive a body, 8 active requests and 600
requests/minute per process. The upload limit preserves substantial local
documents/media without granting unlimited staging; larger files must be split
or handled outside HTTP. Starlette's native [RequestBodyLimitMiddleware](https://starlette.dev/middleware/#requestbodylimitmiddleware)
counts streamed bytes before parsing/spooling, including absent Content-Length.
Its introduction requires Starlette >=1.6 and the compatible FastAPI >=0.141.1;
both were already installed for the previous slice's tests. No new framework.
Uvicorn also bounds connections. Limits are resource budgets, not a quota on
total disk accumulated by an authorized operator. Access logs are disabled;
unexpected request failures return a fixed 500 without logging request content,
credentials or backend exceptions. Worker failures emit only a fixed diagnostic.

## Acceptance, execution and replay

One `youtube_operations` table joins the existing `unimem.sqlite3`. It contains
the client-known ID, canonical URL, ordered language priority, state/timestamps,
reserved capture ID, actual capture/content IDs and safe error code. It is
not a second CaptureRecord or ContentObject. No canonical schema changes.

After auth/validation, `BEGIN IMMEDIATE` registers the operation and reserves
its capture UUID atomically. A new ID returns 202 **after commit**; the same ID
and equivalent canonical URL/ordered languages return the existing record (200).
Different parameters conflict (409). Different IDs intentionally create different
captures. Capacity is 32 queued/running operations; 10,000 retained receipts
bound history without silently deleting idempotency keys. Full queue/history
refuse new IDs (429/507); existing receipts remain replayable.

One local coordinator claims queued work; one bounded child executes at a time.
The worker calls ADR-024's service with the reserved ID. Acquisition budgets
remain unchanged; intake creates CaptureRecord, orchestrator advances its
lifecycle, existing raw/content stores persist material. Neither HTTP nor the
operation store writes canonical lifecycle state. Accepted work has no dependency
on request connections, tabs, popups or extension background lifetime.

States: `queued → running → complete | failed | interrupted`. `complete` requires
a COMPLETE capture and canonical content. `failed` is a known acquisition or
processing failure. `interrupted` preserves uncertainty; it is never automatically
retried. A lost connection is a client's observation failure, not a job state.
GET status/content/Markdown is read-only and never claims work or retrieves
captions. The existing CaptionMarkdownRenderer uses stored content, with no
server-side file export. A Markdown error leaves capture and operation intact.
No state claims Obsidian import; this slice never touches a vault.

## Restart and process ownership

The supported deployment is **one Linux API process per data directory**.
Lifespan holds an exclusive `flock` on `.api-server.lock`; a worker inherits that
file descriptor. Do not unlink the lock file or run this on filesystems without
working local SQLite/flock semantics. A second API process refuses startup even
on another port. This lock applies with YouTube disabled too.

Graceful stop kills/joins an active worker and reconciles its durable evidence;
queued work survives. SIGKILL may leave the child alive with the lease. A child
alarm bounds that orphan to 75 seconds; a new server must wait for it to exit.
Parent also imposes a 75-second execution ceiling. No blind external-network
retry and no exactly-once network claim.

Startup, with the lease and no child, reconciles running operations: linked
COMPLETE capture plus content becomes complete without acquisition; FAILED
capture becomes failed; missing or incomplete evidence becomes interrupted.
Content saved before the capture's own COMPLETE transition is **not** promoted
to success; available IDs are exposed for inspection. Queued work may then run.
This is operation recovery, not a repair engine for historical captures.
An infrastructure failure stops the coordinator with a fixed restart-required
diagnostic; if storage itself is unavailable, its last durable state remains
authoritative until the operator repairs storage and restarts.

## Compatibility and verification

CLI capture/render retain their interfaces; offline render needs no retrieval
dependency. `--youtube` explicitly enables HTTP capability and fails startup
without the optional extra. Existing API callers now need Authorization;
the old installed extension has no connection UI and is not yet compatible.
Its regression harness supplies credentials explicitly without changing the UI.
Zen/Linux connection/settings, selected-tab action and status UI are B2–B4.
Agreed Obsidian import is C; multimedia and GitHub remain D.

Real TCP/process tests cover disconnect/replay, concurrent submit, SIGTERM and
SIGKILL, queued restart, orphan lease, worker deadline and capture-commit crashes.
Unit/integration tests cover safe failures, bounds, authorization and dependency
isolation. Reproduction and measured evidence: [local delivery guide](../LOCAL_DELIVERY.md).
