# Protected local YouTube delivery (B1)

Server-side delivery is implemented. Zen/Linux remains the required next browser
target; **Zen runtime acceptance: NOT RUN**. No extension connection UI, browser
port, Obsidian/Companion integration or vault writes are included here.

## Install and explicitly connect

From the repository, in your Python environment:

```bash
python -m pip install '.[youtube]'
python -m unimem_api --data-dir ./data --init-token
python -m unimem_api --data-dir ./data --youtube
```

Default address: `http://127.0.0.1:8765`. The operator chooses data directory,
loopback host/port, and optionally `--token-file /absolute/private/path`.
No HTTP client can choose a filesystem destination. Without `--youtube`, the
base API works without the retrieval extra and does not expose YouTube routes.
With the flag but without the extra, startup fails rather than claiming readiness.
`/health` only reports process liveness, not YouTube network availability.

The token is generated locally, random, and 0600. Initialization never overwrites
an existing token. For the future extension's explicit connection settings, show
it only when needed; no HTTP endpoint returns it:

```bash
python -m unimem_api --data-dir ./data --show-token
# Stop the server, rotate, restart, and update connected clients:
python -m unimem_api --data-dir ./data --rotate-token
python -m unimem_api --data-dir ./data --youtube
```

Keep secrets out of URLs, shell history, tickets and logs. Avoid shell tracing.
The following shell helper feeds curl's header configuration through stdin,
keeping the token out of curl's command-line arguments:

```bash
export UNIMEM_TOKEN_FILE="$PWD/data/api.token"
ucurl() {
  printf 'header = "Authorization: Bearer %s"\n' "$(cat "$UNIMEM_TOKEN_FILE")" |
    curl --config - "$@"
}
```

All examples below and the README's old capture/upload examples use this helper.
Missing or wrong credentials return 401. Ordinary web Origins return 403; an
invalid Host returns 400. No Origin is acceptable for CLI clients **with** the
credential. The extension-origin policy and threat model are in
[ADR-025](ADR/ADR-025-protected-local-youtube-delivery.md).

**Old clients:** the installed Chromium connector does not send a token and will
receive 401. It has not acquired a connection UI or Zen support in this PR. Its
existing text/page HTTP contracts are preserved; Python/Node integration harnesses
now explicitly inject test authorization. Do not disable auth to use an old UI.

## Submit, close the client, recover status and Markdown

Pick and keep an operation ID before sending. Example public URL is only an
example: captions can be absent, blocked or unavailable in your languages.

```bash
OP=yt-demo-001
ucurl --fail-with-body -sS -X POST http://127.0.0.1:8765/v1/youtube/operations \
  -H 'Content-Type: application/json' \
  --data '{"operation_id":"yt-demo-001","url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","languages":["ru","en"]}'
```

202 means the request has committed to SQLite, not that capture has completed.
The returned record includes `operation_id`, canonical `url`, ordered `languages`,
`state`, timestamps, actual `capture_id`/`content_id` when known, `error_code` and
`result_available`. The reserved internal capture UUID is not a claim that a
capture already exists. Close curl/the terminal; the worker continues.

In another terminal, define the helper again and use the **same known ID**:

```bash
ucurl --fail-with-body -sS http://127.0.0.1:8765/v1/youtube/operations/yt-demo-001
# Once state is complete:
ucurl --fail-with-body -sS http://127.0.0.1:8765/v1/youtube/operations/yt-demo-001/content
ucurl --fail-with-body -sS http://127.0.0.1:8765/v1/youtube/operations/yt-demo-001/markdown
```

If the submit response was lost, repeat the exact submit: 200 returns its existing
operation, including queued/running/failed/interrupted states. No second capture
is launched. Equivalent URL forms are canonicalized; language order matters.
Changing parameters under an existing ID returns 409. A different ID explicitly
requests another capture. Reading status/results never triggers acquisition.
Markdown is returned in HTTP, not written to the server or a vault.

States are `queued`, `running`, `complete`, `failed`, `interrupted`.
`result_available` means canonical capture completion; a Markdown request can
still fail independently (503 `markdown_unavailable`) and can be retried without
losing capture. A connection error means **status could not be observed**;
it does not mean the server job failed. No response means “imported in Obsidian”.

Stop/restart the server with the same data directory. Queued work resumes.
Complete captures/results are read offline. A crash after canonical completion
but before operation completion is reconciled by the reserved capture ID.
Incomplete evidence becomes interrupted, even if a partial content write exists.
Inspect the exposed capture IDs before explicitly submitting a new operation ID;
there is no automatic retry of interrupted/failed work.

## Bounds and reproducible acceptance

One Linux server/worker per data directory is enforced by an OS file lock. The
lock file must never be deleted to “unlock” a running server. After a parent
SIGKILL an orphan may retain the lock for up to 75 seconds; wait and restart.
Do not configure multiple Uvicorn workers, reload, network filesystems or a
second writer to the operation table. CLI capture/render remain independent;
neither interprets operation records or attempts operation recovery.

Budgets: 32 queued/running jobs, one acquisition at a time, 10,000 retained
receipts; new submissions fail at the bound, and existing IDs still replay.
History is retained intentionally: do not delete receipts and then reuse IDs.
Ingress: 8 MiB JSON, 512 MiB **whole multipart request** for documents/media,
120-second body deadline, 8 active requests, 600 requests/minute. There is no
infinite retry. Existing A1 destination/size/timeout/request budgets are unchanged.

Deterministic acceptance with synthetic captions and actual TCP/child processes:

```bash
python -m pip install -e '.[dev]'
pytest tests/unit/api/test_security.py tests/unit/api/test_delivery.py \
  tests/unit/api/test_worker.py tests/unit/youtube/test_operations.py \
  tests/integration/api/test_youtube_delivery.py -q
```

The TCP suite sends and closes a socket before reading its submit response,
counts acquisitions/captures, then checks the same IDs and Markdown after server
restart. It also covers concurrent duplicate delivery, graceful shutdown,
SIGKILL/orphan locking, queued restart, crash after saved capture, safe acquisition
errors, and oversized/chunked multipart. No real YouTube, browser or vault is
needed. A live capture can fail with YouTube's honest blocked/timeout/no-track
status; no bypass is attempted. A1's separate prior live evidence is in the
[caption guide](YOUTUBE_CAPTIONS.md#opt-in-live-acceptance).

## Verification record (2026-09-29)

- Full regression: **4,310 passed**; required core/API statement + branch
  coverage **99.19%** (full run also measured YouTube).
- Dedicated B1 gate: **36 passed**, **94.79%** over credentials, ingress,
  worker, HTTP operation routes and operation persistence. The new operation
  store alone is 99%, HTTP operation routes 100%; no old-core-only denominator.
- Dedicated YouTube gate: **102 passed**, **96.20%** package coverage.
- Ruff check/format and strict mypy pass (246 source/test modules). Existing
  browser Node suites: **11 passed**; these are not runtime browser acceptance.
- Wheel and sdist built. Installed-wheel base environment has neither `requests`
  nor `youtube-transcript-api`: **42 passed, 1 skipped** (the retrieval-only
  integration module); an additional fresh-process offline CLI render passed.
- Linux, Python **3.13.13**, FastAPI **0.141.1**; main environment Starlette
  **1.6.0**, base wheel environment **1.7.0**. TCP providers are synthetic;
  B1 makes no new live YouTube or browser acceptance claim.

CI is checked against the PR's actual head at handoff; these local figures do
not imply a hosted CI result. No previous owner media acceptance is reopened.
