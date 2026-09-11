# capture-core

Core domain contracts, immutable raw-object storage, capture-record
persistence, capture intake for text, HTML and PDF, and a local HTTP capture API
for a universal multimodal capture and ingestion layer. Canonical contracts are at schema
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
  *Browser capture*, below.
- **Phase 1, PR 3 — completed-capture replay.** The connector's first real
  requirement: a client that loses the response to its POST can resend the
  identical envelope under the identical capture id and be handed the capture it
  already made. `201` means this request created and completed the capture;
  `200` means it was an equivalent replay of one already complete. Equivalence is
  *proven* from the durable record and the raw object's SHA-256 — same id and any
  difference is still `409`, and a duplicate that is not complete is still `409`.
  No idempotency key, no idempotency table, no schema change, and no change under
  `src/core/`.

Phase 2 asks the first question about *modality*:

- **Phase 2, PR 1 — HTML webpage ingestion.** An HTML-backed `webpage`
  `CaptureEnvelope` becomes an immutable HTML original and a deterministic
  canonical `web` `ContentObject`, through the existing lifecycle and route. No
  contract change; the schema stays `0.2`.
- **Phase 2, PR 2 — browser whole-page capture.** The extension learns the other
  half: right-click its toolbar icon and choose **Save whole page to UniMem**,
  and the current document's serialized DOM is submitted as exactly that
  envelope. `contextMenus` is the only new permission, and it grants access to
  no website — see *Browser capture*, below. **Nothing under `src/` changed.**

Phase 2's manual acceptance checklist A–G was run by hand against a real
Chromium installation and a real local server, and all of it passed — see
*Manual acceptance checklist*, below. Phase 3 asks what it takes to ingest
material that is not a string:

- **Phase 3, PR 1 — PDF upload and document ingestion.** A new staging route,
  `POST /v1/uploads`, accepts binary bytes and returns the content-addressed
  `file_ref` that an ordinary `document` `CaptureEnvelope` then names. The PDF
  becomes an immutable original and a canonical `document` `ContentObject` with
  one text segment per nonblank page, each carrying the physical page it came
  from. **Upload is not capture**: staging mints no capture id, starts no
  lifecycle, and runs no processor. No contract change; the schema stays `0.2`.
  See *Capturing a PDF document*, below.

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

Errors come back as `{"error": {"code": ..., "message": ...}}`.

Posting the same capture id twice is `409 capture_already_exists` — **unless** it
is the identical request against a capture that is already complete, which is
answered `200` with that capture's existing result. That is the whole of the
idempotency here: it lets a client whose response was lost resend safely, and it
is granted only when every submitted fact matches the durable record and the
submitted text hashes to the stored raw object. A capture that is still
`processing`, or that failed, is `409` like any other duplicate; nothing is
resumed, reprocessed, or reconciled. See
[ADR-013](docs/ADR/ADR-013-completed-capture-replay.md).

### Capturing an HTML webpage

The same endpoint also ingests webpages submitted as HTML. Quoting a whole HTML
document inside a shell argument is miserable, so write the request body to a
file first — here with a here-doc, which needs no escaping at all:

```bash
cat > page.html <<'HTML'
<!doctype html>
<html>
<head>
  <title>Example &amp; Test</title>
  <style>.x { display: none; }</style>
</head>
<body>
  <main>
    <h1>Hello</h1>
    <p>First <strong>paragraph</strong>.</p>
    <script>window.secret = "not content"</script>
    <p>Second&nbsp;paragraph.</p>
  </main>
</body>
</html>
HTML

python - page.html > page-capture.json <<'PY'
import json
import sys

html = open(sys.argv[1], encoding="utf-8").read()

print(json.dumps({
    "schema_version": "0.2",
    "id": "cap_readme_web_01",
    "source": {"type": "api", "provider": "curl",
               "url": "https://example.com/article"},
    "payload": {"type": "webpage", "mime_type": "text/html", "html": html},
    "context": {"captured_at": "2026-01-02T03:04:05+00:00"},
}))
PY

curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  --data-binary @page-capture.json
```

The response is the same `201` shape. The content object then comes back with
`"type": "web"`, a title of `Example & Test` taken from the page's `<title>`,
and one text segment reading:

```
Hello

First paragraph.

Second paragraph.
```

The JavaScript and the CSS are not in it, and the `&nbsp;` in the second
paragraph is preserved as a real no-break space (U+00A0) rather than collapsed
to an ordinary one:

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_web_01/content
```

A few things are worth being precise about:

- **The endpoint now processes plain text, HTML-backed webpages, and PDF
  documents** (see *Capturing a PDF document*, below). Images, video, and files
  are still accepted by the contract and refused with `422 unsupported_payload`.
- **Webpage support is deterministic text extraction, not reader mode.** Scripts,
  styles, `noscript`, `template`, and `svg` are dropped, block elements separate
  paragraphs, and entities are decoded. Navigation, menus, and footers are text
  on the page and come out as text — there is no article extraction, no
  boilerplate removal, and no CSS or JavaScript evaluation. The exact submitted
  HTML is kept immutably and stays retrievable, so a better extractor later can
  re-read exactly what this one saw.
- **The server never fetches `source.url`.** It is metadata. The only bytes
  processed are the ones in your request, and nothing on the page is executed,
  fetched, or loaded.
- **The submitted `payload.title` wins** over the page's `<title>` when you send
  one.
- **A webpage must be submitted as `html` alone.** Sending `html` together with
  `text` or `file_ref` is refused with `422 unsupported_payload` rather than
  silently dropping one of them — a capture stores one raw original, and this
  build will not choose for you.
- **The extension can now capture a whole page**, by right-clicking its toolbar
  icon — see *Browser capture*, below. What it submits is the browser's
  serialization of the current DOM, not the page's original network source. You
  can still POST a page's HTML yourself, exactly as above.
- Completed replay is text-only, so resubmitting the same webpage capture id is
  `409` even when the request is identical. The extension handles that: it makes
  one read-only check and reports what the server actually holds.

### Capturing a PDF document

A PDF is binary, and a `CaptureEnvelope` is JSON — so the bytes are staged
first, and the envelope names them. **These are two separate operations, and
the upload is not a capture.**

Step one: stage the bytes.

```bash
curl -sS -F 'file=@example.pdf;type=application/pdf' \
  http://127.0.0.1:8765/v1/uploads
```

```json
{
  "file_ref": "sha256:f407468e79a468a73965db940e076d3bd997a46acf7df2d0df3deb15dcc13357",
  "sha256": "f407468e79a468a73965db940e076d3bd997a46acf7df2d0df3deb15dcc13357",
  "mime_type": "application/pdf"
}
```

Nothing has been captured yet. No capture id was minted, no `CaptureRecord`
exists, no processor ran, and there is no `ContentObject`. All that happened is
that the exact bytes are now an immutable, content-addressed raw object, and
`file_ref` is the handle to them.

Step two: submit the capture, putting that `file_ref` into the existing
canonical envelope.

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "0.2",
    "id": "cap_readme_pdf_01",
    "source": {"type": "upload", "provider": "curl"},
    "payload": {
      "type": "document",
      "mime_type": "application/pdf",
      "file_ref": "sha256:f407468e79a468a73965db940e076d3bd997a46acf7df2d0df3deb15dcc13357"
    },
    "context": {"captured_at": "2026-01-02T03:04:05+00:00"}
  }'
```

The response is the same `201` shape as every other capture. The content object
comes back with `"type": "document"` and one text segment per nonblank page:

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_pdf_01/content
```

```json
{
  "type": "document",
  "title": "A PDF that names itself",
  "segments": [
    {"type": "text", "text": "…page one…", "spatial": {"page": 1}, "position": 0},
    {"type": "text", "text": "…page three…", "spatial": {"page": 3}, "position": 1}
  ]
}
```

That example had a blank page two, which shows the one thing worth
understanding about the shape: **`spatial.page` is where the text is in the PDF,
and `position` is where it comes in the document's content.** A blank page
leaves a gap in the first and no gap in the second.

A few things are worth being precise about:

- **Upload and capture are two operations, and only the second is a capture.**
  Staging bytes starts no lifecycle. That is what lets you upload a large file
  once and decide separately — even from another process, after a restart —
  whether and how to capture it.
- **`file_ref` is the only connection between them**, and it is
  content-addressed. The same PDF uploaded twice returns the same `file_ref` and
  is stored once. Two captures of one PDF are two distinct `ContentObject`s
  pointing at one set of bytes.
- **`file_ref` is a UniMem raw reference, not a path.** Only
  `sha256:<64 lowercase hex>` is resolved. A filesystem path, a `file://` URL,
  an HTTP URL, or an S3 URL is refused with `422 unsupported_payload` and is
  never opened or fetched — the server reads no local file you name and dials no
  host. A well-formed reference to bytes that were never staged is
  `422 capture_material_unavailable`, and leaves no capture record behind.
- **The current document processor supports text-bearing, unencrypted PDF
  only.** No OCR, no forms, annotations, attachments, embedded images, layout
  reconstruction, or table structure. A DOCX or EPUB `mime_type` is refused at
  intake with `422 unsupported_payload` rather than stranding a capture.
- **Scanned PDFs currently fail**, with `422 processing_failed` and a capture
  that reads `failed`. That is deliberate: a scan carries no embedded text, and
  reporting `complete` with no content would claim your document was remembered
  when it was not. The exact PDF is kept, so a build with OCR can read those
  same bytes later.
- **The original PDF is always preserved**, byte for byte, and stays retrievable
  through the content object's original asset. Nothing is rewritten,
  recompressed, or normalized — and the page text you get back is exactly what
  the parser returned, with no Unicode normalization or whitespace tidying.
- **The filename is not identity and not a title.** It decides no storage path,
  is not recorded on the capture, and is never returned. Title precedence is:
  the `payload.title` you submitted, else the PDF's own metadata `/Title`, else
  none. Never the filename, the digest, or the first line of the document.
- **`source.url` is not fetched**, here as everywhere else. It is metadata.
- **Duplicate document replay is not implemented.** Completed replay is
  text-only, so resubmitting the same document capture id is `409` even when the
  request is identical. Use `GET /v1/captures/{id}` to see what the server
  actually holds.

`GET /health` reports process liveness only and checks nothing else.

## Browser capture

The first real client of that API: a Chromium extension that saves either the
text you have selected or the page you are looking at.

1. Start the UniMem API:

   ```bash
   python -m unimem_api --data-dir ./data
   ```

2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select `clients/browser-extension`.
6. Pin the extension if you want it visible in the toolbar.

### Saving a selection

1. Open an ordinary `http://` or `https://` page.
2. Select some text.
3. **Left-click** the UniMem toolbar icon.
4. `OK` means the server confirmed a complete capture.

### Saving a whole page

1. Open an ordinary `http://` or `https://` page.
2. **Right-click** the UniMem toolbar icon.
3. Choose **Save whole page to UniMem**.
4. `OK` means the server confirmed a complete capture.

**What "whole page" means, precisely.** The extension submits
`document.documentElement.outerHTML`: the browser's serialization of the
document's current top-level DOM, as it is at the moment you choose the menu
item. **It is not the original network source and not "view source".** It
includes changes JavaScript has already made to the page, and it differs from
what the server sent because the browser parsed the document and serialized it
back. It does not include a doctype, shadow DOM, iframe contents, stylesheets,
images, or any other resource — those are referenced, not contained. What UniMem
guarantees is the narrow, true thing: the exact string the extension submitted is
what is stored, byte for byte, and stays retrievable.

Left-click is still selection capture, and right-click is the only new
interaction. There is no popup: the click *is* the permission — it grants
`activeTab` for that one tab and that one gesture, which is why the extension
holds standing access to no website at all.

### The badge

The badge is the whole UI, for both captures:

| Badge | Meaning |
| --- | --- |
| `...` | sending |
| `OK` | saved — the server confirmed a complete capture |
| `!` | not saved, or not confirmed |

Hover the toolbar icon for the detail (`UniMem: saved`, `UniMem: select some text
first`, `UniMem: could not read this page`, `UniMem: service unavailable`, and so
on). A capture confirmed after a lost response reads `UniMem: saved (confirmed
retry)` or `UniMem: saved (confirmed after a network error)` — still one capture,
and still `OK`.

Either capture goes through the same path as any other client: it becomes a
canonical `CaptureEnvelope`, is POSTed to `http://127.0.0.1:8765/v1/captures`,
and is stored, normalized, and completed synchronously. Read it back with the
same two `GET`s shown above.

### Permissions

```json
"permissions": ["activeTab", "scripting", "contextMenus"],
"host_permissions": ["http://127.0.0.1/*"]
```

That is the entire manifest's access story. `contextMenus` is what puts the item
in the menu and grants access to no website; `host_permissions` names only the
local API. There is no `<all_urls>`, no `tabs`, no `storage`, no `cookies`, no
`webRequest`, and no content script — nothing touches a page until you ask.

### Current limitations

All deliberate for this phase:

- **Chrome/Chromium MV3 only.** No Firefox or Safari port.
- **Top-level document only**, for both captures. A selection inside a
  cross-origin iframe is not captured, and an iframe's contents are not part of a
  page snapshot. Widening permissions to reach them is not a trade this connector
  makes.
- **A page snapshot is a DOM serialization**, with the consequences described
  above. There is no reader mode, article extraction, screenshot, or resource
  archiving.
- **The API address is fixed** at `http://127.0.0.1:8765`. There is no options
  page and no configurable host or port; the server's `--host`/`--port` flags
  still work, but this connector targets the documented default.
- **Only `http://` and `https://` pages.** Acting on `chrome://`, extension, or
  `file://` pages fails locally and sends nothing.
- **No server authentication.** Keep the API on localhost.
- **No general retry policy — one bounded resend.** A capture that gets any HTTP
  response is never sent again: the server answered, and that is the answer. Only
  a request that fails at the *network* layer, where the outcome is genuinely
  unknown, is resent — once, as the byte-identical envelope under the same
  capture id. If that resend is also lost, or comes back as a conflict the
  connector cannot interpret, it makes one read-only check and tells you what it
  found. The hard limit for one gesture is two POSTs and one read; there is no
  loop, no backoff, and no queue.
- **A lost whole-page response costs that extra check.** Completed replay is
  text-only, so a resent page capture is answered `409` and the connector
  confirms the outcome with the read rather than guessing from the conflict.
- **No extension history.** The extension stores nothing; the server is the only
  record of what was captured.

### Manual acceptance checklist

The automated suites cover the flows, the envelopes, the network bounds, and the
extension's real registration in Chromium. **They do not dispatch a real toolbar
click or right-click** — that needs browser automation this phase deliberately
does not add. Run these by hand, with the API started as above:

| | Check | Expect |
| --- | --- | --- |
| **A** | Select text on an ordinary page, left-click the icon | `...` then `OK`; `GET /v1/captures/{id}/content` shows the selection exactly |
| **B** | Right-click the icon, choose **Save whole page to UniMem** | `...` then `OK`; a `web` `ContentObject` with the page's visible text |
| **C** | Do B on a page with `<script>` and `<style>` | Script and CSS text are absent from the canonical segment; the stored original still contains them |
| **D** | Do B on a page you have changed with JavaScript first (expand a section, or edit the DOM in DevTools) | The snapshot reflects what is on screen now, not the original source |
| **E** | Do B on `chrome://extensions` | `!` and `UniMem: cannot capture from this page`; no request is sent |
| **F** | Stop the API, then do A and B | `!` and a service-unavailable tooltip; nothing is queued |
| **G** | Restart the API on the same `--data-dir` and re-read the captures from A and B | Both are still `complete`, with their content and their exact originals |

Nothing here is claimed to have passed automatically.

**A–G were run by hand, against a real Chromium installation and a real local
UniMem server, and all of them passed.** That is a human result and is recorded
as one — the suites above still do not dispatch a click, and none of this was
driven by CI. Macro Phase 2 closed on the strength of that run.

## Layout

```
src/core/contracts/   canonical domain contracts (Pydantic v2 models)
src/core/storage/     raw object store port and local backend
src/core/processing/  processor port, router, the text, webpage and pdf processors, and the lifecycle orchestrator
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

Runtime dependencies: **pydantic** and **pypdf** for `core`, plus **fastapi**,
**uvicorn** and **python-multipart** for the `unimem_api` delivery adapter.
`core` imports none of the latter and is usable without a web framework;
`pypdf` is confined to `core.processing.pdf` and is the kernel's only
non-pydantic dependency, held there by test. `python-multipart` is what FastAPI
parses the upload's `multipart/form-data` body with. Persistence uses the
standard library's `sqlite3`.
