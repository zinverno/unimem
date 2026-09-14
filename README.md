# capture-core

Core domain contracts, immutable raw-object storage, capture-record
persistence, capture intake for text, HTML, PDF and DOCX — with optional local OCR
for scanned PDF pages — and a local HTTP capture API
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
- **Phase 3, PR 2 — DOCX document ingestion.** A second document format, on the
  staging route that was already there. `POST /v1/uploads` is unchanged and
  still knows nothing about document formats; a `document` envelope declaring
  the OOXML `.docx` MIME type reaches a new `DocxProcessor`, which turns the
  main document body into ordered `text` segments — paragraphs and table rows,
  in the order they occur. **No page numbers**: a DOCX has flow content, and
  which page a paragraph lands on depends on who renders it. No new route, no
  contract change, and the schema stays `0.2`. See *Capturing a DOCX document*,
  below.
- **Phase 3, PR 3 — opt-in local OCR for scanned PDF pages.** The first
  *optional* capability in the build. Started with `--pdf-ocr`, a deployment
  registers `PdfOcrProcessor` **instead of** `PdfProcessor`: a PDF page that
  carries embedded text is read exactly as before, and a page that carries none
  is rasterized once and recognized once by a local Tesseract, becoming an `ocr`
  segment with `ocr` provenance. Without the flag the build is byte-for-byte the
  one above — the same refusal of textless PDFs, and no rasterizer, imaging
  library, or OCR engine imported, probed, or executed. No new route, no new
  request field, no new MIME type, no contract change, and the schema stays
  `0.2`. Recognition is a deployment decision, never something a request can
  switch. See *Scanned PDFs: opt-in local OCR*, below.

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

That is the **default deployment**, and it needs nothing beyond the Python
package: no rasterizer, no imaging library, and no OCR engine. There is one
optional capability, off unless you ask for it — local recognition of scanned PDF
pages, added with `--pdf-ocr`, which has extra install steps and its own
[section below](#scanned-pdfs-opt-in-local-ocr). Every other behaviour described
in this README is identical in both modes.

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

- **The endpoint now processes plain text, HTML-backed webpages, and PDF and
  DOCX documents** (see *Capturing a PDF document* and *Capturing a DOCX
  document*, below). Images, video, and files are still accepted by the contract
  and refused with `422 unsupported_payload`.
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
- **The default PDF processor supports text-bearing, unencrypted PDF only.** No
  OCR, no forms, annotations, attachments, embedded images, layout
  reconstruction, or table structure. A `mime_type` this build has no processor
  for — an EPUB, a legacy `.doc` — is refused at intake with
  `422 unsupported_payload` rather than stranding a capture. DOCX is the one
  other document format that is supported, and it has a processor of its own:
  see below.
- **Scanned PDFs fail by default**, with `422 processing_failed` and a capture
  that reads `failed`. That is deliberate: a scan carries no embedded text, and
  reporting `complete` with no content would claim your document was remembered
  when it was not. The exact PDF is kept either way, so the same bytes can be
  read later — and a deployment started with `--pdf-ocr` can read them now. See
  [Scanned PDFs: opt-in local OCR](#scanned-pdfs-opt-in-local-ocr).
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

### Capturing a DOCX document

A `.docx` is binary too, so it takes **exactly the same two steps as a PDF**.
There is no `/v1/docx` route, no second staging mechanism, and nothing about the
upload changed: `POST /v1/uploads` still stores whatever bytes it is given and
knows nothing about document formats. The only difference is the `mime_type` the
capture declares.

Step one: stage the bytes.

```bash
curl -sS \
  -F 'file=@example.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  http://127.0.0.1:8765/v1/uploads
```

```json
{
  "file_ref": "sha256:3b1c0d4f9a2e5b8c7d6e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c",
  "sha256": "3b1c0d4f9a2e5b8c7d6e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c",
  "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

As with a PDF, nothing has been captured yet: no capture id, no `CaptureRecord`,
no processor, no `ContentObject`.

Step two: submit the capture, naming that `file_ref` in the same canonical
`document` envelope.

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "0.2",
    "id": "cap_readme_docx_01",
    "source": {"type": "upload", "provider": "curl"},
    "payload": {
      "type": "document",
      "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "file_ref": "sha256:3b1c0d4f9a2e5b8c7d6e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c"
    },
    "context": {"captured_at": "2026-01-02T03:04:05+00:00"}
  }'
```

The response is the same `201` shape as every other capture, and the content
object comes back with `"type": "document"`:

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_docx_01/content
```

For a document that reads *paragraph, 2x2 table, paragraph*:

```json
{
  "type": "document",
  "title": "A DOCX that names itself",
  "segments": [
    {"type": "text", "text": "The canonical object is not Markdown.",
     "position": 0, "metadata": {"docx_block": "paragraph"}},
    {"type": "text", "text": "Format\tPagination",
     "position": 1,
     "metadata": {"docx_block": "table_row", "table_index": 0, "row_index": 0}},
    {"type": "text", "text": "docx\trenderer-dependent",
     "position": 2,
     "metadata": {"docx_block": "table_row", "table_index": 0, "row_index": 1}},
    {"type": "text", "text": "It is a ContentObject.",
     "position": 3, "metadata": {"docx_block": "paragraph"}}
  ]
}
```

A few things are worth being precise about:

- **The upload route is generic and did not change.** It streams exact bytes
  into the raw store, returns `file_ref`/`sha256`/the declared MIME type, and
  does not validate, sniff, or unzip anything. Acquisition stores bytes; the
  capture declares what those bytes mean; the processor validates that
  declaration.
- **DOCX uses the same staging mechanism as PDF** — the same route, the same
  content-addressed store, the same `sha256:<64 lowercase hex>` reference rules,
  the same refusals for paths and URLs, and the same
  `422 capture_material_unavailable` for a reference nobody staged.
- **Only modern OOXML `.docx` is supported**, declared as exactly
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`.
  The format is never inferred from a filename, an extension, a `source.url`, or
  the ZIP's contents — a client has to say.
- **Legacy `.doc` is not supported.** `application/msword` is a different binary
  format needing a different reader, and it is refused at intake with
  `422 unsupported_payload`. So are `.docm`, ODT, RTF, EPUB, and a bare
  `application/zip`.
- **The main document body is extracted: paragraphs and tables.** Body order is
  preserved, so a table that sits between two paragraphs produces segments
  between those two paragraphs' segments, and `position` is one contiguous
  sequence across both kinds.
- **Table rows become tab-separated `text` segments**, one per nonblank row. The
  tab is an explicit canonical flattening boundary between cells, not a
  character claimed to have been in the document — `metadata.docx_block`,
  `table_index`, and `row_index` are what let you tell a flattened row from a
  paragraph that happens to contain tabs. Merged cells are taken as the reader
  presents them: a horizontally merged cell repeats across the grid columns it
  spans.
- **There are no DOCX page numbers, deliberately.** A `.docx` holds flow
  content; the page a paragraph lands on depends on fonts, page size, and the
  renderer, so two machines can legitimately disagree. No DOCX segment carries
  `spatial.page`, and nothing here renders or paginates a document to invent
  one. (A PDF page, by contrast, *is* recorded in the file, which is why PDF
  segments do carry `spatial.page`.)
- **Word heading styles are not interpreted.** A `Heading 1` is a paragraph of
  text in this build. Turning styles into a section hierarchy is a design
  question waiting for a downstream requirement.
- **Headers, footers, footnotes, endnotes, comments, tracked-change history,
  text boxes, embedded images, charts, and equations are not extracted** in this
  build. Nothing is executed, and no external relationship is ever fetched.
- **An image-only DOCX fails** with `422 processing_failed` and a capture that
  reads `failed`, exactly as a scanned PDF does — rather than becoming an empty
  `complete` document that claims your file was remembered when it was not. No
  OCR and no vision model runs. The exact `.docx` is kept, so a richer build can
  read those same bytes later. A corrupt package, and a password-protected one,
  fail the same safe way; no password is attempted.
- **The original `.docx` is always preserved**, byte for byte, and stays
  retrievable through the content object's original asset. Paragraph and cell
  text reaches the segment exactly as the reader returned it — no stripping, no
  Unicode normalization, no whitespace tidying.
- **The filename is not identity and not a title.** Title precedence is: the
  `payload.title` you submitted, else the document's own core-properties
  `title`, else none. Never the filename, the digest, the first paragraph, or a
  heading.
- **Duplicate document replay is still not implemented**, for DOCX as for PDF:
  resubmitting the same capture id is `409` even when the request is identical.

### Scanned PDFs: opt-in local OCR

A scan carries no embedded text, so the default build refuses it. A deployment
that says so explicitly can instead **recognize** the pages that carry no text,
using a local Tesseract. It is off unless you ask for it, and asking for it takes
two installs and one flag.

**Install the optional Python extra:**

```bash
pip install 'capture-core[ocr]'          # adds pypdfium2 and Pillow
```

**Install the system prerequisites separately.** They are not Python packages and
nothing in this project installs, downloads, or vendors them:

```bash
# Debian / Ubuntu
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus

# macOS
brew install tesseract tesseract-lang

# Fedora
sudo dnf install tesseract tesseract-langpack-eng tesseract-langpack-rus
```

**Both** the English and Russian language files are required — not one, not
whichever happens to be installed. Recognition runs `eng+rus` in a single pass,
and a deployment that asked for that and silently got English alone would change
what Russian documents are remembered as saying with nothing in the result saying
so.

**Start the server with the flag:**

```bash
python -m unimem_api --data-dir ./data --pdf-ocr
```

Startup checks the packages, the executable, and both language files before the
port is bound. If anything is missing it exits with a sentence naming it:

```
--pdf-ocr was requested but local OCR is unavailable: the OCR engine 'tesseract'
(version 5.3.4) is missing language data for rus. …
```

It does **not** start with recognition quietly disabled, and it does **not** fall
back to English.

#### Capturing a scan

Exactly the same two steps as any other PDF — the same `POST /v1/uploads`, the
same `CaptureEnvelope`, the same `201`. There is no new route, no new field, and
no new MIME type, and nothing in a request can enable, disable, or configure
recognition:

```bash
curl -sS -F 'file=@scan.pdf;type=application/pdf' \
  http://127.0.0.1:8765/v1/uploads
```

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "0.2",
    "id": "cap_readme_scan_01",
    "source": {"type": "upload", "provider": "curl"},
    "payload": {
      "type": "document",
      "mime_type": "application/pdf",
      "file_ref": "sha256:…the ref the upload returned…"
    },
    "context": {"captured_at": "2026-01-02T03:04:05+00:00"}
  }'
```

#### Telling recognized text from extracted text

Read the content object and look at three fields — `type`, `provenance`, and
`spatial.page`. Here is a mixed document: page one carried its own text, page two
was a scan, page three was blank.

```bash
curl -sS http://127.0.0.1:8765/v1/captures/cap_readme_scan_01/content
```

```json
{
  "type": "document",
  "segments": [
    {
      "type": "text",
      "text": "This page carries its own embedded text.\n",
      "spatial": {"page": 1},
      "position": 0,
      "provenance": {"source_type": "original", "processor": "pdf-ocr",
                     "processor_version": "0.1"}
    },
    {
      "type": "ocr",
      "text": "The middle page is a scan.\n",
      "spatial": {"page": 2},
      "position": 1,
      "provenance": {"source_type": "ocr", "processor": "pdf-ocr",
                     "processor_version": "0.1"}
    }
  ],
  "metadata": {
    "pdf_ocr": {
      "page_count": 3,
      "embedded_text_pages": [1],
      "ocr_attempted_pages": [2, 3],
      "ocr_pages_without_text": [3],
      "engine": "tesseract",
      "engine_version": "5.3.4",
      "rasterizer": "pypdfium2",
      "rasterizer_version": "5.13.0",
      "settings": {"languages": "eng+rus", "render_dpi": 300, "psm": 3, "oem": 1,
                   "max_pages": 50, "document_budget_seconds": 120.0}
    }
  }
}
```

- `"type": "text"` with `"source_type": "original"` is text the **document
  carried**, read by the ordinary parser. That page was never rasterized.
- `"type": "ocr"` with `"source_type": "ocr"` is text a recognizer **inferred from
  pixels**. Weight it accordingly.
- `spatial.page` is the physical PDF page in both cases; `position` is canonical
  reading order. Page three produced no segment, which is the gap you can see.
- `metadata.pdf_ocr` records what actually ran — the installed engine and
  rasterizer versions, the effective settings, and which pages went which way.

#### A document that was already refused

Enabling OCR does not revisit anything. A capture that already `failed` as a scan
stays `failed` with no content, and there is no reprocessing or migration step.
**Submit the same bytes under a new capture id:**

```bash
# the same file_ref, a different capture id
curl -sS -X POST http://127.0.0.1:8765/v1/captures \
  -H 'content-type: application/json' \
  -d '{"schema_version": "0.2", "id": "cap_readme_scan_retry", … }'
```

The raw bytes are shared — identical content deduplicates — and the canonical
identities are not: a fresh content id, a fresh asset id, and fresh segment ids.
Resubmitting the *original* id is still `409`.

#### What this does not do

Being precise here matters more than usual, because a `complete` document looks
the same whether or not everything on the page was read:

- **A page with any embedded text is treated as covered, whole.** If such a page
  also contains a photograph of a sign or a scanned figure with a caption, those
  words are **not** read and no segment reports them. There is no region-level
  OCR, no assessment of whether an existing text layer is good, and no blending
  of extracted and recognized text on one page.
- **`complete` is not an accuracy claim.** It means this policy finished and its
  content was persisted. It does not mean the recognition was correct or that
  every visible word was captured. Expect ordinary OCR errors; nothing is
  spell-corrected, de-hyphenated, or tidied, because a correction nobody can
  audit is worse than a visible error.
- **No confidence scores.** The engine is not asked for one and none is invented.
- **An empty result is not proof a page was blank.** Pages that recognized to
  nothing are listed under `ocr_pages_without_text`, which is named after what
  was observed rather than after a conclusion.
- **Only PDF pages.** Not uploaded images, not DOCX, not handwriting, not layout
  or table structure, and no searchable-PDF rewriting — the original PDF is never
  modified.
- **Local only.** No cloud OCR service, no model download, no network call, and
  no external resource fetched from the document. PDF JavaScript, XFA, and form
  environments are never initialized, and annotations and form fields are not
  rendered.
- **Bounded, not sandboxed.** A PDF over **50 pages** is refused whole rather than
  partly read; a page whose raster would exceed **20,000,000 pixels** is refused
  rather than quietly downscaled; each page gets at most **30 s** of engine time
  and each document at most **120 s** of recognition in total. Those are limits,
  not a memory or CPU sandbox: they do not bound this process's memory, and the
  HTTP request itself has no deadline at all.
- **Recognition runs inside the request.** A 300 DPI page through a real engine
  takes seconds, and a long scan can take the whole budget. There is no queue and
  no worker.
- **An engine failure is not a document verdict.** If the engine is missing,
  crashes, times out, or exhausts the budget mid-run — or the renderer fails to
  open the document for a reason that is not a statement *about* the document,
  such as an I/O error or a failure it declines to explain — the response is
  `503 ocr_unavailable`, no content is stored, and the capture is left
  `processing` rather than `failed` — because nothing was learned about the
  document. A capture left `processing` is **not automatically resumable**;
  `GET /v1/captures/{id}` shows the state, and recovery is not implemented here.
- **A document nothing could be read from still fails.** `422 processing_failed`
  and a durable `failed`, never an empty `complete`.
- **Encrypted and malformed PDFs are refused before anything is rasterized.** OCR
  is not a repair pass, and no password is attempted.

See [ADR-018](docs/ADR/ADR-018-opt-in-local-pdf-ocr.md).

To check PDF, DOCX and OCR ingestion by hand on your own machine, work through
[docs/MANUAL_DOCUMENT_ACCEPTANCE.md](docs/MANUAL_DOCUMENT_ACCEPTANCE.md) — a
runnable owner acceptance guide, in Russian. Nothing in it is claimed to have
passed automatically.

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
src/core/processing/  processor port, router, the text, webpage, pdf, pdf-ocr and docx processors, the OCR port, and the lifecycle orchestrator
src/core/rendering/   renderer port and the JSON and Markdown projections
src/core/persistence/ capture record store port and the SQLite adapter
src/core/intake/      capture intake, the envelope-to-stored-capture flow
src/unimem_api/       the HTTP delivery adapter and its CLI — outside core
src/unimem_ocr/       the optional local PDFium/Tesseract recognizer — outside core
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

That is the full baseline, and it runs on an installation with **no OCR extra and
no Tesseract**: the native recognition tests skip, and say so under `-rs`. To run
them, install the extra and the system prerequisites from
[the OCR section](#scanned-pdfs-opt-in-local-ocr) and then:

```bash
uv pip install --python .venv/bin/python -e ".[dev,ocr]"

# skips if the engine or a language pack is missing
.venv/bin/pytest tests/unit/ocr tests/integration/ocr -rs

# same tests, but a missing prerequisite is a failure instead of a skip
UNIMEM_REQUIRE_PDF_OCR_INTEGRATION=1 \
  .venv/bin/pytest tests/unit/ocr tests/integration/ocr -v -rs \
  --cov=core --cov=unimem_api --cov=unimem_ocr
```

`UNIMEM_REQUIRE_PDF_OCR_INTEGRATION=1` is what CI sets. A skipped acceptance test
and a passing one look the same in a green summary, so in CI every reason to skip
— a missing extra, a missing engine, a missing language pack, a missing font — is
a failure instead.

The browser connector is plain ES modules with **no dependencies** — no bundler,
no test framework, no build step. `npm test` runs Node's own test runner, and
the directory is loadable as an unpacked extension exactly as it sits in the
repository.

Runtime dependencies: **pydantic**, **pypdf** and **python-docx** for `core`,
plus **fastapi**, **uvicorn** and **python-multipart** for the `unimem_api`
delivery adapter. `core` imports none of the latter and is usable without a web
framework. The two parsers are the kernel's only non-pydantic dependencies, and
each is confined by test to the one processor whose format it reads: `pypdf` to
`core.processing.pdf`, and `python-docx` — with the `lxml` it brings — to
`core.processing.docx`. Nothing in `core` imports a converter, a renderer, a
rasterizer, an imaging library, or `subprocess`. `python-multipart` is what
FastAPI parses the upload's `multipart/form-data` body with. Persistence uses the
standard library's `sqlite3`.

Optional dependencies: the `[ocr]` extra adds **pypdfium2** (page rasterization)
and **Pillow** (PNG encoding), used only by `src/unimem_ocr/` and imported only
when a deployment actually asks for recognition. `unimem_ocr` depends on `core`
and never the other way round; `core` does not know its name, and `unimem_api`
imports it in exactly one place — `unimem_api.__main__`, inside the function that
handles `--pdf-ocr`. **Tesseract and its `eng`/`rus` language data are system
prerequisites**, installed by the machine's package manager and never by this
project. A test asserts that importing the application loads no rasterizer even
where the extra *is* installed.
