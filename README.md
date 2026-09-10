# capture-core

Core domain contracts, immutable raw-object storage, capture-record
persistence, text capture intake, and a local HTTP capture API for a universal
multimodal capture and ingestion layer. Canonical contracts are at schema
version **0.2**; `0.1` documents remain readable and are rewritten as `0.1`.

Implemented so far:

- **Phase 0A — domain contracts.** The stable domain language:
  `CaptureEnvelope`, `CaptureRecord`, `ContentObject`, `Segment`, `Provenance`,
  `Asset`, `ProcessingRecord`, and the validation rules the rest of the system
  depends on.
- **Phase 0B — local immutable raw-object storage.** A `RawObjectStore` port
  and a local content-addressed backend that persists original bytes, addressed
  by SHA-256 and never modified once stored.
- **Phase 0C — processing foundation and UTF-8 text normalization.** A
  `Processor` port, a `ProcessorRouter` that requires exactly one matching
  processor, and `TextProcessor`, which turns a stored UTF-8 original into a
  canonical `ContentObject`.
- **Phase 0D — derived representations.** A `Renderer` port and two pure
  projections of a `ContentObject`: `JsonRenderer`, the full-fidelity form that
  round-trips back into the object, and `MarkdownRenderer`, a deliberately
  lossy title-and-text projection for humans and LLMs. Renderers return
  strings; nothing is written or exported.
- **Phase 0E — capture record persistence.** A `CaptureRecordStore` port with
  `create`, `get`, and `replace`, and `SqliteCaptureRecordStore`, a file-backed
  adapter over the standard library's `sqlite3`. Records are stored as whole
  validated snapshots of their own contract JSON, keyed by the record's own id.
  No delete, listing, query, upsert, or migration framework.
- **Phase 0F — text capture intake.** `CaptureIntake.accept(envelope)`, the
  first orchestration: it registers a `RECEIVED` capture record, stores the
  envelope's text as exact UTF-8 bytes, then replaces the record with `STORED`
  and the raw reference. Inline text only; the capture record keeps the
  envelope's id, and a duplicate id is an error rather than a silent retry.
- **Phase 0G — durable capture metadata, and schema 0.2.** `CaptureRecord`
  gains `context`, `intent`, and `title`, reusing the models `CaptureEnvelope`
  already used, so capture-time facts survive intake instead of being dropped.
  Intake writes them into the `RECEIVED` snapshot, before the bytes are stored.
  `context` is required at 0.2; `intent` and `title` are optional and never
  fabricated. Content stays in the raw store, and no database migration was
  needed.
- **Phase 0H — processing orchestration.** `ProcessingOrchestrator.process(capture_id)`
  loads the authoritative record, requires `stored`, routes it, marks it
  `processing` durably, runs the one selected processor, and records `complete`
  — or `failed`, but only for a `ProcessingError`; an infrastructure failure
  leaves the capture `processing` rather than inventing a terminal state.
  `TextProcessor` 0.2 carries the capture's submitted title onto the content
  object.
- **Phase 0I — canonical content persistence.** A `ContentObjectStore` port
  (`create`, `get`, `get_for_capture`) and `SqliteContentObjectStore`, storing
  the `ContentObject` contract's own JSON — never a renderer's output. The
  orchestrator stores the canonical object *before* it writes `complete`, so
  `complete` now means the content is durable. One canonical object per
  capture, enforced by a `UNIQUE` key; no `replace`, upsert, or delete.

**This closes the Phase-0 foundation.** A text capture can be accepted, stored,
normalized, and completed, and everything up to canonical content survives a
restart. There is no queueing or AI code, and no ORM.

Phase 1 builds the first product surface around it:

- **Phase 1, PR 1 — local HTTP capture API.** A FastAPI delivery adapter in
  `src/unimem_api/`, **outside `core`**: four routes, a synchronous
  intake-plus-processing `POST`, a stable typed error envelope, and
  `python -m unimem_api`. The request body is the canonical `CaptureEnvelope`
  itself, so every connector hands over the same document. `core` gained no
  dependency and imports nothing from the adapter.
- **Phase 1, PR 2 — browser selection connector.** A Chromium MV3 extension in
  `clients/browser-extension/`: select text, click the action, and the selection
  becomes a canonical `CaptureEnvelope` POSTed to the local API. The first real
  client of the Phase-1 surface, and the first end-to-end product path — see
  *Browser selection capture*, below.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for scope and invariants.

## Quick start

Run the local capture server:

```bash
python -m unimem_api --data-dir ./data
```

It binds `127.0.0.1:8765` by default and creates `./data` if it is missing:

```
./data/raw/              immutable originals, addressed by SHA-256
./data/unimem.sqlite3    capture records and canonical content
```

> **This server has no authentication, authorization, or TLS.** Anyone who can
> reach the port can submit captures and read everything stored. Keep the default
> localhost binding, and do not expose it to an untrusted network.

Submit a capture. The body is the canonical `CaptureEnvelope`, and the client
picks the capture id:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "0.2",
    "id": "cap_readme_01",
    "source": {"type": "api", "provider": "curl"},
    "payload": {"type": "text", "mime_type": "text/plain",
                "text": "The canonical object is not Markdown.",
                "title": "A note"},
    "context": {"captured_at": "2026-01-02T03:04:05+00:00"},
    "intent": {"action": "save", "tags": ["architecture"]}
  }'
```

`201` is returned only once intake **and** processing have finished, so the
content already exists by the time you read the response:

```json
{"capture_id": "cap_readme_01", "content_id": "...", "status": "complete"}
```

Read the capture's authoritative lifecycle record — this is also how a failure
after `POST` is inspected (`received`, `stored`, `processing`, `failed`):

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_01
```

Read the canonical `ContentObject` it normalized into (the canonical object
itself, not a rendering):

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_01/content
```

Errors come back as `{"error": {"code": ..., "message": ...}}`. There is no
idempotency yet: posting the same capture id twice is `409
capture_already_exists`. Only inline `text` payloads are processed today; a
valid image, webpage, or document envelope is accepted by the contract and
refused with `422 unsupported_payload`.

`GET /health` reports process liveness only and checks nothing else.

## Browser selection capture

The first real client of that API: a Chromium extension that saves the text you
have selected on a page.

1. Start the UniMem API:

   ```bash
   python -m unimem_api --data-dir ./data
   ```

2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select `clients/browser-extension`.
6. Pin the extension if you want it visible in the toolbar.
7. Open an ordinary `http://` or `https://` page.
8. Select some text.
9. Click **Save selected text to UniMem**.

The badge is the whole UI:

| Badge | Meaning |
| --- | --- |
| `...` | sending |
| `OK` | saved — the server confirmed a complete capture |
| `!` | not saved, or not confirmed |

Hover the toolbar icon for the detail (`UniMem: saved`, `UniMem: select some text
first`, `UniMem: service unavailable`, and so on).

The capture goes through the same path as any other client: your selection
becomes a canonical `CaptureEnvelope`, is POSTed to
`http://127.0.0.1:8765/v1/captures`, and is stored, normalized, and completed
synchronously. Read it back with the same two `GET`s shown above.

Current limitations, all deliberate for this phase:

- **Chrome/Chromium MV3 only.** No Firefox or Safari port.
- **Top-level document selection only.** A selection inside a cross-origin
  iframe is not captured, and widening permissions to reach one is not a trade
  this connector makes.
- **The API address is fixed** at `http://127.0.0.1:8765`. There is no options
  page and no configurable host or port; the server's `--host`/`--port` flags
  still work, but this connector targets the documented default.
- **Only `http://` and `https://` pages.** Clicking on `chrome://`, extension,
  or `file://` pages fails locally and sends nothing.
- **No server authentication.** Keep the API on localhost.
- **No automatic retries.** A capture is POSTed exactly once. If the request
  fails at the network layer, the extension makes one read-only check to see
  whether the capture landed, and tells you what it found — it never re-sends.
- **No extension history.** The extension stores nothing; the server is the only
  record of what was captured.
- **Selection only.** No whole-page capture, no HTML, no screenshots.

## Layout

```
src/core/contracts/   canonical domain contracts (Pydantic v2 models)
src/core/storage/     raw object store port and local backend
src/core/processing/  processor port, router, text processor, and the lifecycle orchestrator
src/core/rendering/   renderer port and the JSON and Markdown projections
src/core/persistence/ capture record store port and the SQLite adapter
src/core/intake/      capture intake, the envelope-to-stored-capture flow
src/unimem_api/       the HTTP delivery adapter and its CLI — outside core
clients/              connectors that call the HTTP API — outside core and unimem_api
tests/                unit and integration tests
docs/                 architecture notes and ADRs
```

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/pytest --cov=core --cov=unimem_api --cov-report=term-missing
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                          # type checking

npm test --prefix clients/browser-extension             # browser connector
```

The browser connector is plain ES modules with **no dependencies** — no bundler,
no test framework, no build step. `npm test` runs Node's own test runner, and
the directory is loadable as an unpacked extension exactly as it sits in the
repository.

Runtime dependencies: **pydantic** for `core`, plus **fastapi** and **uvicorn**
for the `unimem_api` delivery adapter. `core` imports none of the latter and is
usable without a web framework. Persistence uses the standard library's
`sqlite3`.
