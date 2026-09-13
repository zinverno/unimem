# Architecture

## Purpose

This project will become a universal multimodal capture and ingestion layer:
it takes heterogeneous digital content — text, webpages, images, documents,
video, code, files — and normalizes it into a versioned canonical
representation that a larger shared-memory / AI context system can build on.

## Current scope

**Phase 0A — domain contracts.** Answers "what are the fundamental objects
flowing through this system?" Pydantic models, enums, and validation rules in
`src/core/contracts/`, with no dependency on storage, transport, or AI.

**Phase 0B — immutable raw object storage.** Answers "how does UniMem persist
immutable original bytes?" A storage port and one local backend in
`src/core/storage/`.

**Phase 0C — processing foundation.** Answers "how does a stored raw object
become a canonical `ContentObject`?" A processor port, a router, and one real
processor for UTF-8 text in `src/core/processing/`.

**Phase 0D — derived representations.** Answers "how do we derive an external
readable representation from an existing `ContentObject`?" A `Renderer` port
and two pure projections — JSON and Markdown — in `src/core/rendering/`.

**Phase 0E — capture record persistence.** Answers "how is a valid
`CaptureRecord` durably stored and retrieved without coupling domain code to a
database?" A `CaptureRecordStore` port and one file-backed SQLite adapter in
`src/core/persistence/`.

**Phase 0F — text capture intake.** Answers "how does a valid inline-text
`CaptureEnvelope` become an immutable raw object plus a durably registered
`CaptureRecord`?" The first orchestration: `CaptureIntake` in
`src/core/intake/`.

**Phase 0G — durable capture metadata, and schema version 0.2.** Answers "how
does capture-time metadata survive intake durably, without being smuggled into
raw bytes or unrelated fields?" `CaptureRecord` gains `context`, `intent`, and
`title`, and the canonical contract set advances to `0.2` while staying able to
read and rewrite `0.1` documents.

**Phase 0H — processing orchestration.** Answers "how does a durably `stored`
capture become `processing`, get normalized by exactly one processor, and end
`complete` or in a truthful incomplete state?" `ProcessingOrchestrator` in
`src/core/processing/service.py`, plus `TextProcessor` 0.2, which carries the
capture's submitted title onto the content object.

**Phase 0I — canonical content persistence.** Answers "how does a normalized
`ContentObject` become durable before a capture is allowed to become
`complete`?" A `ContentObjectStore` port and a SQLite adapter in
`src/core/persistence/`, and one new step in the orchestrator's success path.
**This closes the Phase-0 foundation** — see *Phase 0 is closed*, below.

### Macro Phase 1 — Capture Surface

**Phase 0 is closed and stays closed.** Phase 1 does not revise, reopen, or
extend the foundation; it builds the first product surface *around* it.

**Phase 1, PR 1 — local HTTP capture API.** Answers "how does a client outside
this process hand UniMem a capture and get the result back?" A FastAPI delivery
adapter in `src/unimem_api/`, **outside `core`**, with four routes, a synchronous
intake-plus-processing `POST`, a stable typed error envelope, and
`python -m unimem_api`. The POST body is the canonical `CaptureEnvelope` itself —
there is no HTTP-specific copy of it. See *The HTTP capture surface (Phase 1)*,
below, and [ADR-011](ADR/ADR-011-local-http-capture-surface.md).

**Phase 1, PR 2 — browser selection connector.** Answers "how does text a person
selected in a browser become a durable canonical capture?" A Chromium MV3
extension in `clients/browser-extension/`, outside both `core` and `unimem_api`,
and the first real client of the HTTP surface — see *The browser selection
connector (Phase 1)*, below, and
[ADR-012](ADR/ADR-012-browser-selection-connector.md).

**Phase 1, PR 3 — completed-capture replay.** Answers "how does a client that
lost the response to its POST resend the same capture without creating a second
one?" A delivery-layer, read-only equivalence check in `src/unimem_api/replay.py`
— see *Completed-capture replay (Phase 1)*, below, and
[ADR-013](ADR/ADR-013-completed-capture-replay.md). **This closes Macro Phase 1.**

### Macro Phase 2 — Webpage Ingestion

**Phase 1 is closed and stays closed.** Phase 2 does not revise HTTP transport
design, browser selection capture, or replay. It asks the first question about
*modality*: UniMem has ingested one kind of material since Phase 0F — plain
text. What does it take to ingest a second?

**Phase 2, PR 1 — HTML-backed webpage vertical slice.** Answers "how does an
HTML-backed `webpage` `CaptureEnvelope` become an immutable HTML original and a
deterministic canonical web `ContentObject`, using the existing lifecycle?"
`WebpageProcessor` in `src/core/processing/webpage.py`, plus one widened intake
capability. The contracts already contained `webpage`, `web`, and `html`
provenance, so **nothing in `src/core/contracts/` changed and the schema stays
`0.2`** — see *HTML webpage ingestion (Phase 2)*, below, and
[ADR-014](ADR/ADR-014-html-webpage-ingestion.md).

**Phase 2, PR 2 — browser whole-page capture.** Answers "how does the existing
browser extension let a user deliberately save the current page as HTML, while
preserving one-click selection capture, without gaining persistent access to
every website?" A `chrome.contextMenus` item on the toolbar action's own
context, and `contextMenus` is the only permission it cost. **Nothing under
`src/` changed**: the server already accepts and processes exactly the envelope
this connector learned to produce.

```
left click the icon    ->  window.getSelection()
                       ->  TEXT envelope     (payload.text)
                              \
right click the icon            >-> the same local API
  -> "Save whole page"         /   -> the modality-specific processor
  -> documentElement.outerHTML/    -> ContentObject
  -> WEBPAGE envelope (payload.html)
```

See *Browser whole-page capture (Phase 2)*, below, and
[ADR-015](ADR/ADR-015-browser-whole-page-capture.md).

### Macro Phase 3 — Document Ingestion

**Phase 2 is closed and stays closed.** Its manual acceptance checklist A–G was
run by the user, by hand, against a real local Chromium installation and a real
local UniMem server, and every check passed. That is a *human* result: the
automated suites cover the flows, the envelopes, the network bounds, and the
extension's real registration in Chromium, and they deliberately do not dispatch
a toolbar click or a right-click. Nothing here claims CI drove those gestures.

Phase 3 does not reopen webpage extraction semantics, browser selection or
whole-page semantics, replay architecture, or Chrome permissions and UI. It asks
the next question about modality: **both kinds of material UniMem ingests today
are strings. What does it take to ingest something that is not?**

**Phase 3, PR 1 — PDF upload and ingestion vertical slice.** Answers "how does a
locally uploaded PDF become an immutable raw original and then a page-aware
canonical `document` `ContentObject`, through the existing capture lifecycle,
without smuggling binary data into the JSON `CaptureEnvelope`?" A narrow staging
route, `POST /v1/uploads`, plus `PdfProcessor` in
`src/core/processing/pdf.py` and one widened intake capability. The contracts
already contained `document`, `file_ref`, `SpatialLocation.page`, and `original`
provenance, so **nothing in `src/core/contracts/` changed and the schema stays
`0.2`**.

```
PDF bytes
  -> POST /v1/uploads      immutable content-addressed raw object
  -> file_ref              "sha256:<digest>"
  -> POST /v1/captures     the existing canonical envelope, naming those bytes
  -> CaptureIntake         resolve + verify, then RECEIVED, then STORED
  -> PdfProcessor          one TEXT segment per nonblank page
  -> ContentObject(document)
  -> COMPLETE
```

**Upload is not capture.** The staging route mints no capture id, creates no
`CaptureRecord`, advances no lifecycle, runs no processor, and produces no
content. It puts immutable bytes somewhere a later envelope can name them, and
`file_ref` is the whole of the connection between the two operations. See
*PDF document ingestion (Phase 3)*, below, and
[ADR-016](ADR/ADR-016-pdf-document-ingestion.md).

**Phase 3, PR 2 — DOCX document ingestion.** Answers "how does a previously
staged DOCX become a canonical `document` `ContentObject` through the same
immutable-original and capture lifecycle introduced for PDF, while preserving
document body order without pretending DOCX has stable physical page numbers?"
`DocxProcessor` in `src/core/processing/docx.py`, one tuple entry in intake's
supported document MIME types, and one name in the composition root's processor
list. **No new route, no contract change, and the schema stays `0.2`.**

```
DOCX bytes
  -> POST /v1/uploads      the same route, unchanged, still format-blind
  -> file_ref              "sha256:<digest>"
  -> POST /v1/captures     the same canonical envelope, a different mime_type
  -> CaptureIntake         resolve + verify, then RECEIVED, then STORED
  -> DocxProcessor         ordered TEXT segments: body paragraphs and table rows
  -> ContentObject(document)
  -> COMPLETE
```

This is the PR that turns PR 1's two prospective decisions — a format-blind
upload route, and a processor claiming a MIME type rather than a payload type —
into demonstrated ones. **No DOCX segment carries a page number**, because a
`.docx` holds flow content and which page a paragraph lands on is a property of
the renderer rather than of the document. See *DOCX document ingestion
(Phase 3)*, below, and
[ADR-017](ADR/ADR-017-docx-document-ingestion.md).

**Phase 3, PR 3 — opt-in local OCR for scanned PDF pages.** Answers "how can an
explicitly OCR-enabled deployment ingest scanned PDF pages into page-aware
canonical content, while preserving embedded text, exact originals, and the
distinction between extraction and recognition?" One CLI flag, `--pdf-ocr`;
`PdfOcrProcessor` in `src/core/processing/pdf_ocr.py`; a narrow recognition port
in `src/core/processing/ocr.py`; and the concrete PDFium/Tesseract adapter in
`src/unimem_ocr/`, outside `core` and behind an optional `[ocr]` extra. **No new
route, no new request field, no new MIME type, no contract change, and the schema
stays `0.2`.**

```
scanned PDF bytes
  -> POST /v1/uploads      the same route, unchanged, still format-blind
  -> POST /v1/captures     the same canonical envelope, the same mime_type
  -> CaptureIntake         RECEIVED, then STORED
  -> PdfOcrProcessor       extract_pages() first; pages with no text are
                           rasterized once and recognized once
  -> ContentObject(document)  TEXT/ORIGINAL for extracted pages,
                              OCR/OCR for recognized ones
  -> COMPLETE
```

This is the first **optional** capability in the build, and the first place two
processors claim the same thing. `PdfProcessor` and `PdfOcrProcessor` both claim
`DOCUMENT` + `application/pdf`, so they are alternatives: composition registers
one or the other, registering both is the router's ambiguity error, and nothing in
a request can choose between them. Without the flag the deployment is
byte-for-byte the one PR 1 shipped — the same refusal of textless PDFs, and no
rasterizer, imaging library, or engine imported, probed, or executed.
`src/core/processing/pdf.py` is unchanged, and its public `extract_pages()` is
reused as-is so that an embedded-text page reaches a segment through exactly the
path it always did. See *Opt-in local PDF OCR (Phase 3)*, below, and
[ADR-018](ADR/ADR-018-opt-in-local-pdf-ocr.md). **Macro Phase 3 remains
open.**

## Future data flow

```
Browser extension / script / curl        (an Obsidian connector is later work)
  -> HTTP               (unimem_api — Phase 1)
  -> CaptureEnvelope    the canonical ingress contract, unchanged by transport
  -> core
  -> durable raw bytes + capture records + canonical content
```

In more detail, and unchanged below the transport:

```
Source
  -> Capture            (CaptureEnvelope, CaptureRecord)
  -> Capture Record     (durable snapshot, stored by CaptureRecordStore — Phase 0E)
  -> Raw Original       (immutable bytes, stored by RawObjectStore — Phase 0B)
  -> Processing         (processors; recorded as ProcessingRecord — Phase 0C)
  -> ContentObject      (canonical, with Segments + Provenance + Assets)
  -> Representations    (derived JSON / derived Markdown — Phase 0D)
  -> later: retrieval
  -> later: ctxalloc
  -> later: agents
```

The capture, capture-record, raw-original, processing, content, and
representation steps exist today for UTF-8 text; everything below them is
future work. Two orchestrators drive that sequence: Phase 0F's `CaptureIntake`
takes an envelope to a stored capture record, and Phase 0H's
`ProcessingOrchestrator` takes that record to a canonical `ContentObject` and a
completed capture. Since Phase 0I that content object is durable — stored
before the capture is marked `complete` — so everything up to and including
canonical content survives a restart. Rendered representations are still
derived on demand and persisted nowhere.

## The canonical ContentObject

`ContentObject` is the normalized representation of one captured thing. It
holds:

- what it is (`type`) and where it came from (`source`, `original`),
- its content as an ordered list of `Segment`s, each with mandatory
  `Provenance`,
- its binary artifacts as `Asset`s, referenced storage-neutrally,
- analysis results in `derived`, which are outputs, never inputs,
- a history of `ProcessingRecord`s.

Markdown, JSON views, and any future rendering are **derived** from the
content object. They are never the source of truth, and nothing in the system
is expected to parse them back into domain objects. See
[ADR-001](ADR/ADR-001-canonical-content-object.md).

## Phase 0A boundaries

In scope:

- domain enums, value objects, and the seven core contracts
- validation rules (structural and cross-field)
- JSON serialization / deserialization via standard Pydantic APIs
- unit tests and this documentation

Out of scope, by decision and not by omission: storage of any kind, HTTP APIs,
queues and workers, extraction (HTML, OCR, transcription, vision), AI
providers, embeddings and vector search, browser extensions or companion apps,
`ctxalloc` integration, agent routing, authentication.

## Raw object storage (Phase 0B)

Raw originals are the only irreplaceable thing in the system: every future
extractor is a function of those bytes. Phase 0B is the implementation of
invariant 4 — originals are immutable once stored — and nothing more.

**Role in the flow.** A capture yields bytes; those bytes are stored once, up
front, and everything downstream refers to them. Storing is not ingestion:
nothing here parses, extracts, or interprets what it is given.

**Identity is the content.** An object is addressed by the SHA-256 of its
bytes. MIME type, filename, source URL, and capture time describe a capture and
never take part in identity — the same bytes offered as `image/png` and as
`application/octet-stream` are one object. See
[ADR-003](ADR/ADR-003-content-addressed-raw-storage.md).

**Deduplication is exact.** Same digest, same object. Perceptual, semantic, and
near-duplicate matching are different problems, solved elsewhere if at all.

**RawObject identity is not capture identity.** These are different layers.
Storing identical bytes yields one RawObject identity and one physical stored
object; it does *not* yield one `CaptureRecord`.

```
capture event  -> references    -> RawObject
many captures  -> may reference -> one RawObject
```

A capture is an event in a context, and two captures of identical bytes may
legitimately differ in source, URL, capture timestamp, device, application,
intent, provenance, and future capture-specific metadata. A RawObject SHA-256
must therefore never be used as `CaptureRecord` identity or as implicit
capture-submission idempotency; if such idempotency is introduced later it needs
an explicit request identity defined at the capture layer. See
[ADR-003](ADR/ADR-003-content-addressed-raw-storage.md).

**References stay storage-neutral.** The store returns the Phase 0A
`RawObjectRef` with `ref = "sha256:<digest>"`, and `id` and `sha256` set to the
digest. No local path, drive letter, `file://` URL, or bucket name ever appears
in a canonical reference. The local layout —
`<root>/sha256/<d0:2>/<d2:4>/<digest>` — is an implementation detail, derived
only from a validated digest. Arbitrary `ref` text is never turned into a path:
a strict parser validates the scheme and digest first, so a reference like
`sha256:../../etc/passwd` is rejected rather than resolved.

**Streaming ingestion.** Sources are consumed in bounded chunks from their
current position, hashing and staging in the same pass. The stream is never
rewound and seek support is not required, so a multi-gigabyte video never has
to fit in memory.

**Atomic finalization.** Staging happens in a temporary file inside the store
root — same filesystem, name from `tempfile`, never from caller input. Once the
digest is known, the object is published with a single `os.link` into its
content-addressed path. A reader therefore never observes a half-written
object, and `os.link` failing with `FileExistsError` *is* the deduplication
path: the bytes are already stored, the existing object stands untouched, and
the staged copy is discarded. Concurrent writers of identical bytes converge on
one file with no locking. On any failure the staging file is removed and no
finalized object appears. This assumes a single filesystem supporting hard
links; durability across power loss is not claimed, as staged data is not
fsynced.

**Immutability is structural.** There is no update, overwrite, or delete
operation. Retention and deletion policy is a later decision. Modification of
files inside the storage root by something other than the store is outside the
Phase 0B trust boundary.

**Out of scope here:** capture record persistence (Phase 0E, and in its own
layer — nothing about a capture record is stored beside the bytes), metadata
storage or sidecars, extraction and processors, renderers, HTTP, queues and
workers, remote or cloud backends, and encryption at rest.

## Processing (Phase 0C)

A capture that has been accepted and whose bytes are stored is not yet usable:
nothing downstream wants to know that this one was text and that one was a
video. Processing is the step that removes that difference.

```
RawObject          (immutable bytes, Phase 0B)
    |
CaptureRecord      (what was captured, and its reference to those bytes)
    |
ProcessorRouter    (capability-based selection — exactly one match)
    |
Processor          (normalization for one modality)
    |
ContentObject      (canonical: Segments + Provenance + Assets + ProcessingRecord)
```

**What a processor consumes.** A `CaptureRecord` — the accepted capture context
— plus whatever explicit dependency it needs to reach the immutable original.
For `TextProcessor` that dependency is the `RawObjectStore` port, handed to it
at construction. The raw object is the source of truth for processing, not the
envelope's payload: the bytes that were stored are the bytes that get
normalized.

**What a processor produces.** One valid `ContentObject`, with segments, their
mandatory provenance, the assets they reference, and a `ProcessingRecord`
describing the run.

**What a processor does not do.** It does not persist a capture or a content
object, does not advance `CaptureRecord.status`, does not mutate the capture
record or the raw original, does not render, and does not call an AI service.
Persistence and orchestration live outside the processor, and do not exist yet.
A processor therefore accepts a capture in any lifecycle state: whether the
record says `stored`, `queued`, or `processing` is not its concern.

**Routing is capability-based and requires exactly one match.** `Processor
.supports()` is a pure question about the capture record — no storage reads, no
mutation, no failure when the original is missing. The router asks every
registered processor and then insists on a single yes:

- one match — that processor is selected;
- no match — `NoProcessorError`;
- several matches — `AmbiguousProcessorError`.

**First-match precedence is rejected.** With one processor, first-match and
exact-match routing behave identically; with a web, image, and document
processor in the same list, first-match turns the order of a list into
undeclared precedence, and an overlap gets resolved silently by whoever
happened to register first. Priorities are not introduced here either: if
overlapping processors ever need an order, that is an explicit decision to
make then. See [ADR-004](ADR/ADR-004-processing-boundary-and-routing.md).

**Registration is explicit.** `ProcessorRouter([...])` takes the processors it
routes to. There is no registry, no decorator, no entry point, no plugin
discovery, and no configuration-driven class loading.

**The first implementation is `TextProcessor`.** It handles
`CapturePayloadType.TEXT` only, decodes the stored bytes as **strict UTF-8**,
and emits one text `Segment` carrying the decoded string exactly: no encoding
detection, no Unicode normalization, no line-ending rewriting, no whitespace
trimming, and no chunking. Chunking is a retrieval concern and belongs to the
phase that needs it. Bytes that are not valid UTF-8 raise `TextDecodingError`,
and material a canonical segment cannot be built from — zero bytes, or nothing
but whitespace — raises `ProcessingInputError` rather than surfacing as a
validation traceback from inside `Segment`.

**Failures are typed, and storage failures stay storage failures.**
`ProcessingError` covers input problems, decoding problems, and routing
problems. Errors from the raw object store are *not* folded into it: a
`RawObjectNotFoundError` or `InvalidRawObjectRefError` propagates with its own
type, because "the store could not give me the bytes" and "this capture will
never process" are different situations for a caller deciding whether to retry.
No `OSError` escapes a processor — the storage port does not raise one.

**Failed runs raise; they do not return.** A processor never returns a partial
or invalid `ContentObject` in order to carry a `FAILED` `ProcessingRecord`.
There is nowhere to persist such a record today, and inventing a place for it
is orchestration's problem, not the contract's.

**Identifiers.** `ContentObject`, `Segment`, and `Asset` ids are minted as
UUID4 strings. That is this producer's implementation choice, not a new contract
rule: Phase 0A deliberately leaves identifier format open, and ids stay opaque
to consumers. None of them is derived from the raw SHA-256 — that address
identifies bytes, and two captures of the same bytes are two content objects.

**The original stays reachable from the content object.** `OriginalReference`
carries identity (`asset_id`, `sha256`, and the declared MIME type, preserved as
declared) but has nowhere to put a reference. So the raw original is also
recorded as one `Asset` with role `original`, whose `ref` is the storage-neutral
handle the store returned. Without it, retrieving the original from a content
object would require every consumer to know that raw storage happens to be
content-addressed — knowledge that belongs to `core.storage`, not to a
consumer. `Asset.mime_type` is required by the contract, so a text capture that
declared no MIME type gets the documented `text/plain` fallback *on the asset
only*; `ContentObject.original.mime_type` stays `None`, so an undeclared MIME
type still looks undeclared.

**Asset identity is not raw object identity.** An `Asset` is a record *inside
one content object* that points at an original; it is not the original. Its id
is minted per processing run, while the raw object's identity travels on `ref`
and `sha256`, where it belongs. So processing the same stored bytes twice
produces two content objects whose original assets carry the same `ref` and the
same digest under two different asset ids — the same layering as ADR-003's
`RawObject` identity versus capture identity, one level further down. Reusing
the raw id as the asset id would make a SHA-256 name a record within a content
object as well as the bytes it addresses, and that overload is exactly what
ADR-003 rules out. Storage deduplication is unaffected: it is decided by the
digest, which nothing here changes.

**Rendering is deliberately absent from processing.** "How do we derive
readable representations from a `ContentObject`?" is a separate question with a
separate answer, and mixing it into normalization would make the canonical
object optional. No `Renderer`, Markdown, or JSON view lives in
`core.processing`; rendering is Phase 0D, below, and it consumes a finished
content object without knowing which processor produced it.

**Out of scope here:** capture record persistence (Phase 0E — a processor
still neither loads nor saves the record it is handed), capture intake and
idempotency, HTTP, queues and workers, every non-text modality (HTML, images,
OCR, documents, video, transcription), chunking, embeddings, AI analysis, and
renderers.

## Derived representations (Phase 0D)

A canonical `ContentObject` is precise and unreadable from outside the process.
Phase 0D answers one question — how do we derive an external readable
representation from an object that already exists? — and answers nothing else.

```
ContentObject
    |
    +-- JsonRenderer     -> derived JSON      (json/0.1/application/json)
    |
    +-- MarkdownRenderer -> derived Markdown  (markdown/0.1/text/markdown)
```

**The canonical object comes first, always.** A renderer consumes a
`ContentObject` that has already been produced and validated. Rendering is
never a step on the way to the canonical object, and no rendered string is ever
read back as domain state: nothing in the system parses JSON or Markdown output
into contracts. See [ADR-005](ADR/ADR-005-derived-representations.md).

**Renderers are pure projections.** A renderer reads the content object and
nothing else — no `RawObjectStore`, no `CaptureRecord`, no filesystem, no
network, no clock, no environment — and mutates nothing, including the object
it was handed. The same validated `ContentObject` rendered by the same renderer
version produces the same string, which is what makes invariant 3
(reproducibility) checkable rather than aspirational.

**The port is three attributes and one method.** `Renderer` is a `Protocol`
with `name`, `version`, `media_type`, and `render(content) -> str`. Those three
values are the renderer's stable identity and describe rendering semantics, not
deployment: `version` changes when the output for an unchanged object changes.
There is no base class, no options or configuration framework, and no async
surface.

**JSON is the full-fidelity representation.** `JsonRenderer` serializes the
whole object — source, original, metadata, segments, assets, derived, and
processing — through Pydantic's own `model_dump(mode="json")` rather than a
hand-maintained field mapping, so a new contract field appears without anyone
remembering it. Output is a bare compact document with no wrapper key, emitted
with `ensure_ascii=False` (Unicode stays readable) and `sort_keys=True`, and it
round-trips through `ContentObject.model_validate_json(...)` to an equal
object. Mapping keys are sorted so objects built in different insertion orders
render identically; **list order is never touched**, because segment, asset,
topic and processing order is domain state rather than spelling. Nothing is
generated during rendering — no timestamps, no ids. `NaN` and the infinities
are refused rather than written, since they are not JSON.

This is deterministic; it is deliberately **not** RFC 8785 canonical JSON and
does not claim to be.

**Markdown is a deliberately lossy readable projection.** `MarkdownRenderer`
exists for humans and for language models reading like humans, and its whole
specification is:

- a `title`, when present, becomes `# <title>`;
- every segment whose `text is not None` follows, in the order the content
  object stores them;
- blocks are joined by exactly one blank line;
- segment text is reproduced **exactly** — no trimming, Unicode normalization,
  CRLF rewriting, escaping, splitting, chunking, or summarizing;
- segments are *not* re-sorted by `position`: list order is the canonical
  order, and `position` is optional metadata;
- no title is fabricated, from the content id or anything else;
- ids, digests, asset references, provenance, processing records, metadata,
  topics, entities, and JSON never appear, and there is no YAML frontmatter;
- no title and no textual segment renders as the empty string, and no trailing
  newline is appended beyond what the last block already carries.

**Markdown is not made reversible, on purpose.** A consumer that needs
provenance, assets, or exact machine state reads the `ContentObject` or its
JSON projection. Pushing metadata into the Markdown to close that gap would
produce a second, worse serialization that has to be kept in sync with the
first, degrade the output for the reader it exists to serve, and invite
somebody to parse it back — which is exactly how a readable view becomes an
accidental source of truth. Invariant 2 forbids it.

**Nothing is persisted or exported.** A renderer returns a string. No file is
written, no path is chosen, no export service exists, and no artifact is
recorded. Where output goes is a separate decision with its own trade-offs, and
no caller needs it yet.

**Selection is explicit.** There is no renderer registry, router, discovery
mechanism, or cache: a caller that wants Markdown constructs
`MarkdownRenderer`. The processor router exists because *the system* must pick
exactly one processor for a capture; nothing forces a rendering choice on the
system, so there is nothing to route.

**Out of scope here:** writing files, export or sync services, artifact
records, HTML/PDF/image/video renderers, templates and themes, YAML
frontmatter, chunking for retrieval, embeddings, and any renderer that consults
something other than the content object it was given.

## Capture record persistence (Phase 0E)

A `CaptureRecord` has existed since Phase 0A and has never had anywhere to
live: processors take one as an argument and tests build it by hand. Phase 0E
answers one question — how is a valid `CaptureRecord` durably stored and
retrieved without coupling domain code to a database? — and answers nothing
else.

```
CaptureRecord
    |
CaptureRecordStore        (port: create / get / replace)
    |
SqliteCaptureRecordStore  (adapter)
    |
SQLite file               (one table: capture_records)
```

**The port is three synchronous methods.**

```python
class CaptureRecordStore(Protocol):
    def create(self, record: CaptureRecord) -> None: ...
    def get(self, capture_id: str) -> CaptureRecord: ...
    def replace(self, record: CaptureRecord) -> None: ...
```

No `save`, `upsert`, `delete`, `list`, `search`, `filter`, transaction handle,
lifecycle-transition call, or async surface. Domain code depends on this
protocol and on nothing beneath it, so invariant 9 holds: no contract gains a
table, a column, a session, or a database path. See
[ADR-006](ADR/ADR-006-capture-record-persistence.md).

**`create` and `replace` are explicit.** `create` inserts one snapshot and
raises `CaptureRecordAlreadyExistsError` if the id is taken, leaving the stored
record untouched. `replace` swaps the snapshot under `record.id` and raises
`CaptureRecordNotFoundError` if there is nothing there — replace never creates.
There is deliberately no `save` that picks between them by looking at the
database: that turns an id-collision bug into a silent overwrite, which is the
one failure a capture store exists to catch.

**A record is stored as a whole validated snapshot.** The payload is the
contract's own `model_dump_json()`, read back with
`CaptureRecord.model_validate_json(...)`. There is no field-by-field mapping to
keep in step with the contract, so a new contract field is persisted without
anyone remembering it. `JsonRenderer` is *not* used and
`core.persistence` does not import `core.rendering`: rendering is an external
representation layer with its own audience and version, and letting the
database read it back would make a projection the storage format.

**Persistence is snapshot-based, not live-object tracking.** `create`
serializes immediately, so mutating the caller's object afterwards cannot reach
the database; `get` returns a newly validated object, so mutating *it* changes
nothing until an explicit `replace`. There is no identity map, dirty tracking,
session, unit of work, or lazy loading.

**The store stores; it decides nothing.** It never mints an id, sets or
advances `updated_at`, chooses a `status`, or invents an `error`. `updated_at`
is orchestration-owned — a persistence layer that stamped it would make every
stored timestamp a fact about when a write happened rather than about the
capture — and the store never mutates the record it is handed.

**Persistence holds no lifecycle policy.** Any valid `CaptureRecord` may be
stored in any status, and any valid snapshot may replace any other. Whether
`stored -> processing -> complete` is a legal move is orchestration's decision,
and orchestration does not exist yet.

**Capture identity is the record's own `id`.** Duplicate detection is that and
only that. Two capture records with different ids referencing the same
`RawObjectRef` and the same SHA-256 are two independent records; nothing here
deduplicates by digest, URL, source, or payload. This is ADR-003's layering
carried into the one place it was most likely to be lost.

**The schema is one table of two columns.**

```sql
CREATE TABLE IF NOT EXISTS capture_records (
    id      TEXT PRIMARY KEY,
    payload TEXT NOT NULL
)
```

A key and a payload. No status, timestamp, source, or digest column is
duplicated out of the payload: nothing in this phase queries, sorts, or
filters, so nothing would read one, and a duplicated column is a second
definition of the contract to keep in step with the first. The database file is
an adapter argument and never appears in a domain contract.

**Each operation is one transaction, and connections are per-operation.**
`create` is a single `INSERT` and `replace` a single `UPDATE` on the primary
key, each committed through the connection's context manager, so a failed write
rolls back and the previous valid snapshot stands. Values are always bound as
parameters — no id or payload is formatted into SQL text. Each call opens its
own connection and closes it, so the store owns no resource a caller must
release, holds no lock between calls, is not bound to a thread, and two
instances over one file are interchangeable and see the same records. There is
no distributed locking, retry loop, WAL tuning, connection pool, or
cross-process coordination beyond what a SQLite transaction gives, and no
optimistic version check — two callers that read, modify, and replace the same
record can still lose one of the updates.

**Errors are typed, and no backend leaks.** `CaptureRecordStoreError` is the
base, with `CaptureRecordAlreadyExistsError`, `CaptureRecordNotFoundError`,
`CaptureRecordCorruptError`, and `CaptureRecordPersistenceError` beneath it.
`sqlite3` exceptions, `OSError`, and Pydantic's `ValidationError` do not cross
the port, and chaining is preserved so the original stays reachable. A stored
payload that is not text, is not JSON, is not a valid record, carries an
unsupported `schema_version`, or embeds an id disagreeing with the key it was
filed under surfaces as corruption, never as a raw `ValidationError`.
Classification runs both ways: a duplicate id is never reported as a generic
failure, and a constraint failure that is not a primary-key conflict is never
reported as a duplicate.

**No migration strategy is claimed.** The adapter runs `CREATE TABLE IF NOT
EXISTS` at construction and stops. There is no migration framework, version
table, or upgrade path, because there is no deployed data and no second schema.
`schema_version` stays `0.1`, and a payload declaring anything else is refused
rather than reinterpreted. SQLite is the first local durable adapter, not a
permanent database choice — that is what the port is for.

**Out of scope here:** capture intake and envelope acceptance, orchestration of
the capture lifecycle, persistence of `ContentObject`s, `Asset`s, or rendered
output, delete and retention policy, listing, querying, filtering, and search,
submission idempotency, transactions spanning more than one record, migrations,
remote or hosted databases, connection pooling, and async I/O.

## Text capture intake (Phase 0F)

Everything before this phase was a component. Phase 0F is the first code that
owns a *sequence* — and, with it, the first code that has to decide what
happens when one step of that sequence fails.

```
CaptureEnvelope(TEXT)
    |
CaptureIntake
    |
    +--> CaptureRecordStore   CaptureRecord(RECEIVED)   the receipt, written first
    |
    +--> RawObjectStore       the immutable original
    |
    +--> CaptureRecordStore   CaptureRecord(STORED)     replaced, with the reference
```

**The API is one class and one method.**

```python
CaptureIntake(
    raw_store: RawObjectStore,
    record_store: CaptureRecordStore,
    *,
    now: Callable[[], datetime] = utc_now,
)
accept(envelope: CaptureEnvelope) -> CaptureRecord
```

Both stores arrive as their ports, so intake names no backend. The clock is a
plain callable because a test needs to control time and a function already does
that — there is no clock class, service hierarchy, container, DI framework,
registry, or router. Phase 0F accepts one payload type, so there is nothing to
route. See [ADR-007](ADR/ADR-007-capture-intake.md).

**The receipt is written before the bytes.** `create` the `RECEIVED` record,
then store the bytes, then `replace` with `STORED`. This ordering is the
decision the phase exists to make: a failure after the receipt leaves a durable,
truthful record that a capture was accepted and is incomplete, which is
recoverable. Storing bytes first would leave orphaned content in a
content-addressed store with nothing anywhere saying a capture was ever
submitted.

**Two snapshots are built, never one mutated.** The `STORED` record is
constructed and validated fresh, not produced by editing the `RECEIVED` one or
by `model_copy(update=...)` — neither re-runs cross-field validators, so an
"updated" record would not be a checked record. Nested models are copied rather
than aliased, so nothing intake returns shares state with the caller's
envelope.

**Identity comes from the envelope.** `CaptureRecord.id` *is*
`CaptureEnvelope.id`: a capture is an event its submitter named, and intake does
not rename it. No capture id is ever minted from, seeded by, or compared against
a raw SHA-256. Two envelopes with different ids and identical text produce two
capture records referencing one deduplicated raw object — ADR-003's layering,
now exercised end to end.

**A duplicate capture id is an error, not idempotency.**
`CaptureRecordAlreadyExistsError` propagates unchanged, and it is raised before
any byte is written. There are no idempotency keys, retry tokens, replay, or
resume in this phase; submission idempotency needs an explicit request identity
defined with a real client in hand.

**Time is intake's, not the envelope's.** `received_at` is the intake clock —
when UniMem accepted the capture — and never `context.captured_at`, which is
when the *user* captured it and which a client supplies. `updated_at` is a
second clock reading taken after the bytes are safely stored. Persistence still
creates no timestamps; intake supplies both and the store transcribes them.

**Text is encoded and nothing else.** The stored bytes are exactly
`envelope.payload.text.encode("utf-8")` — no trimming, Unicode normalization,
line-ending rewriting, BOM insertion, charset detection, or alternate encoding.
`payload.mime_type` passes through as declared, and an absent MIME type stays
absent: intake invents no `text/plain`, and Phase 0C's asset-level fallback
stays where it is. UTF-8 is not a preference here — Phase 0C decodes strict
UTF-8, so both ends must name the same encoding for a capture to survive its
own pipeline.

**Failures keep their own types, and nothing is fabricated.** Intake defines
`CaptureIntakeError` with two subclasses, separating the two reasons it refuses
an envelope: `UnsupportedCapturePayloadError` for a non-`TEXT` payload — a valid
envelope naming a capability this phase lacks, which a later phase will accept
unchanged — and `InvalidCaptureEnvelopeError` for a `TEXT` payload carrying no
text, an envelope that contradicts its own contract and that no phase will
accept. A caller waits for one and fixes the other, so they are not one error.
Both are raised before the clock is read or a store is touched. Store errors are
never wrapped — `RawObjectWriteError` and `CaptureRecordPersistenceError` reach
the caller as themselves, because "the bytes did not get written" and "the
database is down" lead to different decisions. No `FAILED` status is ever
invented from an infrastructure error: a timeout is not a failed capture.

**Cross-store atomicity is a documented gap, not a solved problem.** The raw
store and the record store are separate transactions. If raw storage fails the
record stays `RECEIVED`; if the final `replace` fails the record stays
`RECEIVED` while the raw object already exists. Nothing is rolled back — the raw
store has no delete by design, and adding one would trade immutability for the
*appearance* of atomicity that still would not hold, while risking a digest
another capture shares. Phase 0F records recoverable incompleteness instead of
claiming consistency it cannot provide, and a real deployment will need a
resume or reconciliation pass that this phase does not provide.

**Capture metadata is durable, from the receipt onward.** Intake copies the
envelope's `context`, `intent`, and `payload.title` into the **`RECEIVED`**
snapshot — before raw storage, not after — so a capture stranded by a failure
still knows when, where, and why it was taken. Each snapshot gets its own deep
copies: a `CaptureIntent` holds a mutable `tags` list, and in-place mutation of
a list never reaches a validator, so an alias shared with the envelope or with
the other snapshot would be a way to change a stored capture by appending to a
list somebody else holds. None of it is smuggled into `error`, `source`, or the
stored bytes — the fields are real, validated, and versioned (Phase 0G, below).

**Out of scope here:** every non-text payload type (no `file_ref` resolution,
URL fetching, HTML parsing, file reading, images, documents, or video), any
connector, processing (no `ProcessorRouter`, no `TextProcessor`, no
`ContentObject`), rendering and export, `ContentObject` persistence, HTTP, CLI,
browser surfaces, queues and workers, idempotency and replay, and recovery and
reconciliation.

## Durable capture metadata and schema 0.2 (Phase 0G)

A capture is an event in a context, and until Phase 0G the system threw the
context away. `CaptureEnvelope` had carried it since Phase 0A — a
`CaptureContext`, an optional `CaptureIntent`, a `payload.title` — and
`CaptureRecord` had nowhere to put any of it. Phase 0G answers one question:
how does that metadata survive intake durably, without being smuggled into raw
bytes or unrelated fields?

**`CaptureRecord` gains three fields, reusing models the system already had.**

```python
context: CaptureContext | None = None  # required from 0.2
intent: CaptureIntent | None = None  # genuinely optional
title: NonBlankStr | None = None  # the submitter's title, never a generated one
```

No envelope blob, no generic metadata dict, no sidecar, no second table.
`CaptureContext` and `CaptureIntent` were designed for exactly these facts, and
a second representation of them would be one more thing to keep in step. See
[ADR-008](ADR/ADR-008-durable-capture-metadata-and-schema-0.2.md).

**`context` is required at 0.2; `intent` and `title` are optional.** A capture
that cannot say when it was taken is worth much less later, and `captured_at`
is the one thing every client has. Nothing is fabricated: an absent title stays
absent, and no title is inferred from the id, the text, or the URL.

**`received_at` and `context.captured_at` stay different facts** — when the
system accepted the capture, and when the user took it — and neither is ever
substituted for the other.

**Content stays out.** `payload.text`, HTML, and file bytes are never copied
into the record. Content lives in the raw object store, reached through
`raw_object`. What became durable is metadata *about* the capture.

**The canonical contract set advances to `0.2`.**

```python
SCHEMA_VERSION = "0.2"
SchemaVersion = Literal["0.1", "0.2"]
SUPPORTED_SCHEMA_VERSIONS == {"0.1", "0.2"}
```

`0.3` and every other unrecognized value stay rejected — the literal was
widened to admit a version this build can actually read, and no tolerance was
added. One version continues to name the whole set: `CaptureEnvelope` and
`ContentObject` changed no field and their version advances anyway, because
these contracts are designed, reviewed, and released together.

**`0.1` documents remain readable, and remain writable as `0.1`.** Two rules on
`CaptureRecord` do it:

- a `0.1` record may omit the three new fields and **may not carry them** — a
  `0.1` document with a `title` is wrong about its own shape, and reading it as
  `0.1` would lose that data on the way back out; a `0.2` record must carry
  `context`;
- a `0.1` record serializes **without the three keys at all**, not as nulls.
  Every contract sets `extra="forbid"`, so a reader built against `0.1` rejects
  an unknown key rather than ignoring it, and `"context": null` would be as
  fatal to it as a populated one.

The second rule is the easy one to skip: reading an old document is worth
little if writing it back breaks the build that wrote it. It is a version-aware
wrap serializer on the one model that changed, not a migration framework.

**No database migration was needed.** The SQLite table stores whole
`CaptureRecord` JSON in `(id, payload)`, so a contract that grows fields grows
its payload; no column was added and no DDL ran. `CaptureRecordStore` is
untouched, and an unsupported version in a stored payload is still
`CaptureRecordCorruptError` at the boundary. This is Phase 0E's schema decision
paying for itself the first time it was tested.

**Nothing propagates into `ContentObject` yet.** `title` now exists on a capture
record and `TextProcessor` still does not read it. Whether a processor should
seed `ContentObject.title` from the capture — and what happens when a submitted
title and an extracted one disagree — is a processing decision with its own
trade-offs, not a side effect of the field becoming reachable.

**Out of scope here:** any connector, processing and rendering changes,
`ContentObject` persistence, a migration framework, SQL columns or indexes for
metadata, backfilling or fabricating context for legacy records, and per-contract
version numbers.

## Processing orchestration (Phase 0H)

Phase 0C built the processing parts and deliberately left out the thing that
calls them in order. Phase 0H is that thing, and only that thing.

```
capture_id
    |
CaptureRecordStore.get      the authoritative snapshot, loaded by id
    |
require STORED
    |
ProcessorRouter.select      routing is pure: no state written yet
    |
CaptureRecord(PROCESSING)   durable before any processor I/O
    |
Processor.process           the one selected processor, once
    |
ContentObject
    |
CaptureRecord(COMPLETE)     durable before the caller is handed anything
```

**The API is one class and one method.**

```python
ProcessingOrchestrator(
    router: ProcessorRouter,
    record_store: CaptureRecordStore,
    *,
    now: Callable[[], datetime] = utc_now,
)
process(capture_id: str) -> ContentObject
```

No worker, queue, task system, scheduler, retry engine, registry, DI container,
or async surface. See [ADR-009](ADR/ADR-009-processing-orchestration.md).

**Processors stay pure.** Nothing in ADR-004 is walked back: a processor is
handed a record, returns content or raises, and writes no status and no bytes.
*Every* lifecycle write in the system happens in the orchestrator.

**The capture is loaded by id, never accepted as an object.** A lifecycle
decision taken against a snapshot the caller has been holding is a decision
about the past.

**Exactly `stored` may begin.** Any other status raises
`InvalidCaptureProcessingStateError` before the router is consulted, the clock
is read, or anything is written. Reprocessing a `complete` or `failed` capture
is a decision with its own questions and is not made here.

**Routing happens before `processing` is written.** `select` is pure, so a
`NoProcessorError` or `AmbiguousProcessorError` leaves the capture exactly
`stored`, unmarked. A wiring problem should not leave a trace on the capture.

**`processing` is durable before the processor does any I/O**, so a run that
dies mid-processor leaves evidence it was started — Phase 0F's
receipt-before-bytes ordering, one state later. The selected processor is then
called **directly and once**, with its own deep copy of the snapshot;
`router.process` is not used, because routing again across a durable state
change could run something other than what the record was marked for.

**The output must belong to this capture.** `content.source.capture_id` is
checked against the record, and a mismatch is `ProcessingOutputError`. The
`ContentObject` contract already validates its own internal consistency; this
is the one thing it cannot check.

**The failure split is the heart of the phase.**

| what happened | durable result | what the caller sees |
| --- | --- | --- |
| not `stored` | unchanged | `InvalidCaptureProcessingStateError` |
| no / ambiguous processor | still `stored` | the routing error, unchanged |
| `ProcessingError` from the processor | `failed`, `error = str(exc)` | the original error, unchanged |
| `RawObjectStoreError`, `RuntimeError`, anything else | still `processing` | the error, unchanged |
| `failed` write itself fails | still `processing` | the persistence error, chained to the processing error |
| `complete` write fails | still `processing` | the persistence error; no content returned |

A `ProcessingError` is a verdict about the capture — not UTF-8, no raw object,
content for someone else — and repeating the run changes nothing, so it is
durably `failed`. Anything else describes the *run*, so the record stays
`processing`, which is true. Nothing terminal is fabricated and nothing rolls
back to `stored`: erasing the evidence would make "never attempted" and
"attempted and lost" indistinguishable. This is Phase 0F's rule against
inventing terminal state from infrastructure failure, one layer up.

**Snapshot lineage: the last durable snapshot is the authority for the next.**
`stored -> processing` derives from `stored`; `processing -> complete` and
`processing -> failed` derive from `processing`. Nothing is re-read from a
caller object or an earlier snapshot across a side-effect boundary. Snapshots
are constructed and revalidated, never mutated in place, and nested models are
deep-copied so no two share a mutable `CaptureIntent.tags` list — the rule
intake settled in Phase 0G, applied to a three-step lifecycle.

**A legacy record keeps its version.** A `stored` schema-0.1 capture processes
normally and its `processing`/`complete`/`failed` snapshots stay `0.1`;
upgrading it would require inventing the `context` that 0.2 demands. The
`ContentObject` it produces is a new document and carries the current version.

**`TextProcessor` 0.2 uses the capture's title.** `ContentObject.title =
capture.title`, exactly, or `None` when the capture carried none — no
first-line heuristic, no heading parsing, no filename or URL derivation.
Because output changed for an unchanged input, `version` moved `0.1 -> 0.2`,
and that value travels onto every `Provenance` and `ProcessingRecord` the
processor emits. Plain text has no competing extracted title, so no precedence
framework was built. Orchestration never edits the returned content object.

**The orchestration clock owns lifecycle timestamps only** — read once entering
`processing` and once writing `complete` or `failed`, and not at all for a
missing capture, a wrong starting status, a routing failure, or a `processing`
write that fails. `TextProcessor`'s internal `ProcessingRecord` timestamps are
untouched.

**`COMPLETE` means the content object is durable** — since Phase 0I, below.
The orchestrator stores the canonical object before it writes `complete`, so
the word means normalization succeeded, the content exists, and the lifecycle
completion was recorded. It still says nothing about derived Markdown,
embeddings, or indexes, which are reproducible from the object.

**Concurrent processing of one capture is not safe.** `CaptureRecordStore` has
no compare-and-swap, version, or lease, so two workers can both read `stored`
before either writes `processing`, and both will run. Phase 0H does not solve
this and deliberately reaches for none of the available answers — locks,
leases, optimistic version fields, queues, advisory locks, worker-ownership
columns, or SQLite-specific transactions inside orchestration. Each implies a
model of ownership and of how a dead worker is detected, and that needs its own
decision.

**Out of scope here:** `ContentObject` persistence, reprocessing, resume,
reconciliation, retry, queues and workers, locks/leases/compare-and-swap,
scheduling, batch or scanning APIs, HTTP and CLI surfaces, renderer changes,
and every non-text modality.

## Canonical content persistence (Phase 0I)

Phase 0H had to end with an admission: `COMPLETE` recorded that normalization
had happened, and the `ContentObject` it produced was handed to the caller and
stored nowhere. Phase 0I closes that, and closes the foundation with it.

```
processor.process
    |
validate content.source.capture_id == capture id
    |
ContentObjectStore.create        the canonical object, durable first
    |
[completion clock]
    |
CaptureRecord(COMPLETE)          written only once the content exists
    |
return the ContentObject
```

**A second port, sibling to the first.**

```python
class ContentObjectStore(Protocol):
    def create(self, content: ContentObject) -> None: ...
    def get(self, content_id: str) -> ContentObject: ...
    def get_for_capture(self, capture_id: str) -> ContentObject: ...
```

No `replace`, `save`, `upsert`, `delete`, `list`, `search`, pagination, or
caller-visible transaction. Two lookups, because there are two questions worth
asking: "give me this object" and "what did this capture normalize into?". See
[ADR-010](ADR/ADR-010-canonical-content-persistence.md).

**There is no `replace`, deliberately.** The system produces one canonical
result per capture and has no reprocessing, so an operation that overwrote
canonical content could only be an accident. Superseding it is a decision
reprocessing will have to make explicitly.

**Whole-contract JSON, never a rendering.** `content.model_dump_json()` in,
`ContentObject.model_validate_json(...)` out. `JsonRenderer` is *not* used and
`core.persistence` does not import `core.rendering`: rendering is a derived
representation with its own audience and version, and letting the database read
it back would make a renderer change a data migration.

**Identity stays layered.** `ContentObject.id` is canonical object identity,
`source.capture_id` is the association to the capture event, and a raw SHA-256
identifies bytes. No content id is derived from a capture id, a digest, a
title, or the text — so two captures of identical bytes have two content
objects.

**One canonical object per capture, enforced by the database.**

```sql
CREATE TABLE IF NOT EXISTS content_objects (
    id         TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE,
    payload    TEXT NOT NULL
)
```

Two key columns, each earning its place: `id` addresses an object, and
`capture_id` — being `UNIQUE` — makes the one-per-capture rule a constraint
rather than a convention. Nothing else is lifted out of the payload. This is
also why no `content_object_id` column was added to `CaptureRecord`, and
therefore why **`schema_version` stays `0.2`**: no canonical contract changed.

`SqliteContentObjectStore` mirrors the capture record adapter exactly — table
created at construction, parent directories not fabricated, one connection and
one transaction per operation, no WAL tuning, retry, pooling, or ORM. It may
share a database file with `SqliteCaptureRecordStore`, which leaves
`capture_records` untouched.

**A sibling error hierarchy.** `ContentObjectStoreError` with
`ContentObjectAlreadyExistsError`, `ContentObjectNotFoundError`,
`ContentObjectCorruptError`, and `ContentObjectPersistenceError` — not
subclasses of `CaptureRecordStoreError`, and not of `ProcessingError`. Failing
to *store* content says nothing about whether the capture could be normalized,
and the orchestrator acts on that difference. Constraint classification is
exact: a primary-key conflict is a taken content id, the one `UNIQUE` column is
a capture that already has content, and any other constraint failure is a
backend failure rather than a duplicate. Corruption covers a non-text payload,
malformed JSON, an invalid object, an unsupported version, and — on *both*
lookups — an embedded `id` or `source.capture_id` disagreeing with the row.

**Content is durable before `COMPLETE` is.** The completion clock is not read
until the content write succeeds, so a completion timestamp always describes a
capture whose content exists. A content-store failure needs no new rule: it is
not a `ProcessingError`, so the capture stays `processing`, the error
propagates unchanged, nothing is marked `failed` or rolled back, and no content
is returned. A duplicate for the capture propagates too — nothing loads and
returns the existing object, and nothing marks the capture complete, because
"concurrent worker", "stale retry", and "drifted lifecycle" cannot be told
apart here.

**The new cross-store gap, stated rather than papered over.** If the `COMPLETE`
write fails after content was stored, the content is durable while the capture
still says `processing`. The persistence error propagates; the content object
is **not** deleted or overwritten and the capture is **not** rolled back.
`get_for_capture` exists in part to make that state findable by a
reconciliation pass that does not exist yet.

**No cross-store transaction, and no unit of work.** Even where both adapters
point at one file, orchestration never opens a transaction across them: that
would couple lifecycle orchestration to one adapter's backend and quietly make
the ports un-swappable. No connection is exposed.

**`UNIQUE(capture_id)` is integrity, not mutual exclusion.** Two canonical
objects can never both become durable for one capture. Two workers can still
both process one — the loser simply fails at the content write, having burned
the work. Phase 0H's concurrency limitation is unchanged.

**Out of scope here:** reprocessing and supersession, reconciliation of the
gap above, retries, worker ownership, query and search over content, rendered
output persistence, and any contract or schema change.

## Phase 0 is closed

Phase 0I is the last foundation microphase. End to end, the system can now
accept a text capture, store its bytes immutably, register it durably with its
capture-time metadata, normalize it into canonical content, store that content,
and record the whole lifecycle truthfully — every boundary behind a port, every
failure mode named, and everything up to canonical content surviving a restart.

The concerns still open — reconciliation, retries, same-capture worker
ownership, reprocessing and supersession, query and search, connectors — are
**not** blockers for closing Phase 0. Each needs a real requirement to be
designed against, and building any of them now would be adding foundation for a
product that has not asked for it. They become concrete work when a vertical
product phase requires them.

## The HTTP capture surface (Phase 1)

The first product boundary, and an adapter over the finished core. It lives in
`src/unimem_api/` — **outside `core`** — and `core` neither imports it nor knows
it exists. FastAPI, uvicorn, status codes, routing, and the command line are all
on this side of the line, and the kernel's runtime dependencies are unchanged.

```
POST /v1/captures        CaptureEnvelope
                             -> CaptureIntake.accept        RECEIVED -> STORED
                             -> ProcessingOrchestrator      PROCESSING -> COMPLETE
                             -> 201 {capture_id, content_id, status}

GET  /v1/captures/{id}           the authoritative CaptureRecord
GET  /v1/captures/{id}/content   the canonical ContentObject
GET  /health                     {"status": "ok"}
```

The decisions that shape it:

- **The POST body is `CaptureEnvelope` itself.** No HTTP-specific request model
  exists. The contract's own rules — `extra="forbid"`, `AwareDatetime`, the
  payload cross-field validators, the closed `SchemaVersion` literal — are what
  validate a request, because there is one definition of a capture.
- **`201` means the whole synchronous pipeline finished**, never that intake
  reached `STORED`. The status is written after the orchestrator returns, which
  means the canonical content and the `COMPLETE` snapshot are both durable.
- **The two reads return the canonical contracts as their own JSON**, the same
  `model_dump_json()` the stores persist through. No renderer is called:
  `core.rendering` is a derived-representation layer and this is not it.
- **Lifecycle stays core-owned.** No handler advances, repairs, retries, or rolls
  back a status. `GET /v1/captures/{id}` is what makes Phase 0's truthful
  non-terminal states — `received`, `stored`, `processing`, `failed` —
  observable, and that is the whole recovery story this phase offers.
- **Typed core failures are translated only at the delivery boundary.** Core's
  error hierarchies are untouched; a table maps concrete types to a stable
  `{"error": {"code", "message"}}` envelope. An unmapped error is not adopted by
  a nearby base class — it becomes an ordinary 500.
- **A 4xx explains the request; a 5xx explains nothing.** A 4xx passes core's own
  message through. Every 5xx carries a fixed public message, because core's
  storage errors name the database file and the staging directory on purpose and
  those must not reach a caller.
- **No idempotency.** A duplicate capture id is `409`. Two different ids carrying
  identical text both succeed, sharing one deduplicated raw object. *(Narrowed by
  Phase 1 PR 3, below: the identical request against an already-complete capture
  is answered `200` with that capture's existing result. Every other duplicate is
  still `409`, and two different ids still make two captures.)*
- **No authentication, authorization, API keys, TLS, or CORS.** The CLI therefore
  binds `127.0.0.1` by default, and that default is a control rather than a
  convenience. **Do not expose this server to an untrusted network.**
- **The wired pipeline still supports inline `TEXT` only.** A structurally valid
  webpage, image, or document envelope is accepted by HTTP, refused by intake's
  existing capability check, and returned as `422 unsupported_payload`. Nothing
  fakes support.

`create_app(intake, orchestrator, record_store, content_store)` takes every
dependency as a parameter — no module-level app, registry, settings framework, or
container — and `build_local_app(data_dir)` is the one concrete composition:

```
<data-dir>/
    raw/              LocalRawObjectStore
    unimem.sqlite3    capture_records + content_objects (ADR-010: one file, two tables)
```

The composition root creates `data_dir`, because preparing an application's
workspace is a deployment decision. `SqliteCaptureRecordStore` still refuses to
fabricate a missing parent directory, and `LocalRawObjectStore` is unchanged.

**No connector exists yet.** The browser extension and the Obsidian connector are
later work in this phase; today the callers are `curl` and scripts.


## The browser selection connector (Phase 1)

The first real client of the HTTP surface, and the first end-to-end product
path. It lives in `clients/browser-extension/` — outside both `core` and
`unimem_api` — and it is an ordinary API consumer: it holds no server code, gets
no special endpoint, and gained the server no feature.

```
Browser page
   |
explicit action click            chrome.action.onClicked — the user gesture
   |
activeTab + scripting            granted by that click; no static content script
   |
exact selected text              window.getSelection(), submitted untouched
   |
MV3 service worker               the privileged extension origin
   |
CaptureEnvelope                  canonical, schema 0.2, built client-side
   |
localhost HTTP API               http://127.0.0.1:8765 — a fixed constant
   |
existing core
   |
durable canonical state          COMPLETE CaptureRecord + ContentObject
```

The decisions that shape it:

- **The selection is read in the page; the request is made by the service
  worker.** The injected function reads `window.getSelection()` and knows nothing
  about UniMem — no fetch, no URL. The extension origin holds the loopback host
  permission and is what performs the POST. This split is why the API needs no
  CORS middleware, and `unimem_api` is unchanged by that PR.
- **Minimum permissions: `activeTab` and `scripting`, plus
  `http://127.0.0.1/*`.** No `<all_urls>`, no `tabs`, no `storage`, and no static
  `content_scripts` — nothing touches a page until the user clicks. Chrome match
  patterns cannot pin a port, so the connector's own fixed constant does.
- **The page never chooses the destination.** The page URL and title are capture
  metadata and the selection is payload data; none of them influences where the
  request goes.
- **The selection is submitted exactly as the page returned it.** `trim()` is
  used once, to decide whether a selection is blank, and never to transform. A
  blank selection is refused locally: no capture id, no request, no fabricated
  server-side failure.
- **The capture id is a client-generated opaque UUID**, minted before the POST
  and derived from nothing — not the URL, the text, a digest, or the clock.
- **No retry policy, and one bounded resend.** A real HTTP error is a definite
  answer and is reported as one, never repeated; `409` is a conflict, not
  idempotent success. *(Phase 1 PR 3: a POST that fails at the network layer —
  and only that — is followed by exactly one resend of the byte-identical
  envelope under the same capture id, which the server answers as a completed
  replay when it can.)*
- **Network ambiguity is resolved by observation, last.** When no POST produced
  an answer the connector can act on, it performs at most one read-only
  `GET /v1/captures/{id}` on the id it already minted and reports what it finds —
  complete, some other durable state, not found, or unknown. It never mutates
  anything: lifecycle stays core-owned. The hard bound for one user action is two
  POSTs and one GET.
- **No persistent client state**, no telemetry, and a badge plus a title as the
  entire UI, scoped to the clicked tab so one page's result never becomes every
  tab's badge. The selected text never appears in that UI or in a log, and
  neither does an exception message or stack — the click path ends in safe `!`
  feedback for any failure, because Chrome does not await the action listener
  and a rejection escaping it would be unhandled.

Scope, stated as scope rather than omission: Chromium MV3 only, top-level
document selection only, `http(s)` pages only, a fixed API address, and selection
only — no whole-page or HTML capture, which needs a webpage processor that does
not exist yet.


## Completed-capture replay (Phase 1)

Phase 1 PR 3, and the first idempotency in the system. It exists because the
connector produced a concrete requirement the earlier phases correctly declined
to guess at: **a client that loses the response to its POST holds the identical
envelope and needs to resend it, without creating a second capture and without
the server mistaking a different request for the original one.**

```
POST /v1/captures  ──► intake refuses: the id is taken
                        │
                        ├─ COMPLETE + provably the same request ──► 200 + existing result
                        └─ anything else ────────────────────────► 409 capture_already_exists
```

- **The client-generated `CaptureEnvelope.id` is the replay identity.** No
  `Idempotency-Key` header, request token, nonce, fingerprint, idempotency table,
  second store, new contract field, or schema `0.3`. The client already mints one
  opaque id per capture before the POST, and the `CaptureRecord` primary key
  remains the only authority on who won creation.
- **The status code carries the distinction.** `201` — this attempt created and
  completed the capture. `200` — this was an equivalent replay of one already
  complete. The body is identical in both, deliberately: no `replayed` field
  restates the status line.
- **Resolved only after intake reports a duplicate.** No preflight read on the
  ordinary path, so no check-then-create window is opened and a normal POST costs
  what it always did.
- **Equivalence is proven, never assumed.** Every observable semantic fact must
  match the durable record — id, schema version, `source`, `context`, `intent`,
  `title`, `mime_type` — and the exact submitted bytes are verified by hashing
  `payload.text.encode("utf-8")` against the raw object's SHA-256. Nothing is
  trimmed, case-folded, Unicode-normalized, or line-ending rewritten first.
  `captured_at` is compared as an instant rather than as a string.
- **What the record cannot represent refuses replay.** A `TEXT` payload carrying
  `html` or `file_ref` is not replayable: the durable text record stores neither,
  so equivalence cannot be proven, and unprovable is not equivalent.
- **Server-generated lifecycle facts are not request identity.** `received_at`,
  `updated_at`, and processing timestamps are not compared.
- **Completed only.** `RECEIVED`, `STORED`, `QUEUED`, `PROCESSING`, `PARTIAL`,
  and `FAILED` duplicates all stay `409`. Nothing is resumed, retried, marked
  complete, polled, or reconciled — stranded captures remain Phase 1's open
  problem, and `GET /v1/captures/{id}` remains how they are observed.
- **A replay writes nothing.** No processing run, no second `ContentObject`, no
  re-stored bytes, no rewritten record, no touched timestamp. The helper is
  read-only by construction.
- **`COMPLETE` with no canonical content is a server integrity failure** —
  Phase 0I's invariant violated — and answers `500 data_integrity_error` with the
  existing fixed public message. Not a replay success, not a `404`, not a `409`.
- **No concurrency claim.** A duplicate arriving while the first request is still
  `PROCESSING` gets `409`, and that is acceptable. There are no locks, leases,
  condition variables, waiting, or server-side polling. The guarantee is only:
  once a capture is durably complete, an equivalent resubmission can be answered
  with its result.
- **The policy is delivery-layer.** `src/unimem_api/replay.py`, plus one API-layer
  error type. `src/core/` is unchanged — intake still treats a duplicate id as an
  error — and no route was added.

See [ADR-013](ADR/ADR-013-completed-capture-replay.md).


## HTML webpage ingestion (Phase 2)

Phase 2 PR 1, and the first non-plain-text ingestion capability in the system.

```
CaptureEnvelope(payload.type = webpage, payload.html = ...)
  -> CaptureIntake          ambiguous shapes refused before any side effect
  -> RawObjectStore         the EXACT submitted HTML, UTF-8, immutable
  -> CaptureRecord(STORED)
  -> ProcessorRouter        exactly one match
  -> WebpageProcessor       deterministic text extraction
  -> ContentObject(web)     one TEXT segment, one ORIGINAL asset
  -> ContentObjectStore     durable BEFORE complete
  -> CaptureRecord(COMPLETE)
  -> HTTP 201               the existing route, the existing response
```

- **The contracts already said all of this.** `CapturePayloadType.WEBPAGE`,
  `ContentType.WEB`, `ProvenanceSourceType.HTML`, and `CapturePayload.html` have
  existed since Phase 0A. No enum member, contract field, or schema version
  changed; the schema stays `0.2`. Adding the first webpage capability without
  touching the contracts is the evidence that Phase 0A modelled the domain
  rather than the phase.
- **The supported form is HTML-backed only.** `webpage` + `html` alone is
  ingested. `html` alongside `text` or `file_ref`, and `text` with no `html`,
  are **refused** — a `CaptureRecord` holds one raw original, so choosing
  between submitted representations would durably discard one and answer `201`.
  The refusal is an unsupported-*capability* error, not a validation one: the
  canonical contract still permits those shapes and a later phase will accept
  the identical envelopes. Refusals happen before the clock, the record, and any
  byte, so nothing durable is left behind, and they name field *names* only.
- **The raw original is the exact submitted HTML.** `payload.html` encoded
  UTF-8, with no normalization, DOM re-serialization, whitespace or entity
  rewriting, charset detection, or title/URL injection. Intake does not parse
  HTML at all. Identical bytes still deduplicate; different capture ids remain
  different captures.
- **The canonical text is derived, and the two are never confused.** The segment
  is a reproducible extraction; the original is immutable and reachable through
  the content object's `ORIGINAL` asset. `ProvenanceSourceType.HTML` — rather
  than `ORIGINAL` — is what carries that distinction on the wire.
- **Extraction is deterministic and small**, using `html.parser.HTMLParser` from
  the standard library. `script`, `style`, `noscript`, `template`, and `svg` are
  discarded whole; ordinary `<head>` text, comments, doctypes and processing
  instructions are excluded; a fixed list of block tags separates paragraphs and
  `br` breaks a line; ASCII formatting whitespace is collapsed and trimmed while
  entity meaning (`&nbsp;` included) is preserved.
- **It is not Readability.** No article extraction, boilerplate removal, text
  density, CSS evaluation, JavaScript, or DOM fidelity — navigation and footers
  are text on the page and come out as text. The limitations are recorded as
  tests rather than left implied.
- **Strict UTF-8, because UniMem chose the encoding.** The HTML arrived as an
  already-decoded JSON string and intake wrote its UTF-8 encoding, so the
  processor is decoding its own encoding, not detecting a website's transport
  charset. Undecodable bytes reuse the existing `TextDecodingError`.
- **Title precedence, the first in the system:** submitted capture title → the
  first nonblank HTML `<title>` → none. Never an `<h1>`, a first line, a
  hostname, or a model. The extracted title reaches `ContentObject` only;
  `CaptureRecord.title` still means what the submitter provided.
- **Empty visible content is a processing failure.** A `COMPLETE` object with an
  empty segment would report remembering something when nothing was remembered,
  so `ProcessingInputError` is raised and the existing lifecycle marks the
  capture `FAILED` — with the submitted bytes still in raw storage.
- **The server never fetches `source.url`.** No fetcher, redirects, DNS, or
  remote resource loading, and therefore no SSRF surface. HTML is inert
  submitted text: nothing is executed, and no page content reaches an error body.
- **Nothing else moved.** No new route or response shape, no lifecycle state,
  no orchestrator change; `TextProcessor` remains `text@0.2`; completed replay
  remains TEXT-only, so a duplicate webpage id is still `409`; the browser
  connector was unchanged by this PR and still selection-only at the end of it.
  Whole-page browser capture was the next product work, and is Phase 2 PR 2 —
  see *Browser whole-page capture (Phase 2)*, below.

See [ADR-014](ADR/ADR-014-html-webpage-ingestion.md).


## Browser whole-page capture (Phase 2)

Phase 2 PR 2, and the acquisition half of what PR 1 made processable. PR 1 gave
the server a webpage capability; this gives the browser a way to feed it.

```
right-click the toolbar icon  ->  "Save whole page to UniMem"
  -> the menu activation is the explicit user gesture that grants activeTab
  -> chrome.scripting.executeScript, once, into the top-level document
  -> document.documentElement.outerHTML
  -> schema-0.2 WEBPAGE CaptureEnvelope (payload.html, no text, no file_ref)
  -> POST http://127.0.0.1:8765/v1/captures    the existing route
  -> WebpageProcessor                          the existing processor
  -> ContentObject(type = web)                 durable, COMPLETE
  -> badge OK
```

- **The gesture is the permission.** Executing a context-menu item grants
  `activeTab` for that tab and that gesture, exactly as a toolbar click does.
  Permissions become `activeTab`, `scripting`, `contextMenus`; `contextMenus`
  grants access to no website and is the only one added. `host_permissions`
  stays `http://127.0.0.1/*` — the extension can serialize a whole document and
  still has standing access to one host. No `<all_urls>`, `tabs`, `storage`,
  `notifications`, `webRequest`, `cookies`, clipboard, `pageCapture`,
  `tabCapture`, or `content_scripts`.
- **Left click still means selection.** The popup ADR-012 rejected is still
  rejected: a `default_popup` would replace `chrome.action.onClicked` and take
  the gesture with it. The two flows are separate modules sharing only the URL
  policy and the HTTP client; neither calls the other's reader.
- **One menu item, on the `action` context only.** Registered once from
  `chrome.runtime.onInstalled`, not on every service-worker wake, so a woken
  worker does not duplicate it. No page-wide, selection, link, or image entry.
- **The snapshot is a capture-time serialization of the live top-level DOM**,
  not the page's network source, not "view source", and not an exact server
  response. It includes mutations page script had already made. It excludes the
  doctype (`document.documentElement` is an element, and none is synthesized),
  shadow DOM, iframe documents, canvas pixels, stylesheet contents, image bytes,
  and network responses. No `allFrames`; nothing is fetched, and no cookie or
  storage is read.
- **The submitted string is preserved exactly** — not trimmed, Unicode
  normalized, line-ending rewritten, or re-serialized. `trim()` is used once, as
  a blankness question whose result is never submitted. The server's "exact raw
  HTML" invariant means *the string this connector submitted*, and nothing more.
- **The capture id is minted last**, after the URL is accepted, injection
  succeeds, and the returned HTML is a nonblank string. A page that could not be
  read leaves no capture on the server and sends no request; one new outcome,
  `page_capture_failed`, says so honestly.
- **The tab title is submitted metadata when nonblank**, preserved exactly and
  omitted otherwise, at which point the server's existing precedence rule reads
  the document's own `<title>`. The extension extracts no title and infers none.
  `source.url` remains metadata the extension never fetches.
- **The network story is reused whole.** The same `sendCapture`, the same fixed
  loopback destination that no page can influence, the same bounded semantics:
  an explicit HTTP response is never blindly retried, one network failure buys
  one identical resend, and the hard bound for one gesture is two POSTs and one
  GET.
- **Webpage completed replay remains absent.** `replay.py` was not generalized,
  so a lost webpage response resolves as `409` on the resend plus one
  observational GET that finds `COMPLETE` — a confirmed success the connector
  reads from the server rather than assumes from the conflict. Proven end to end
  against a live Uvicorn server.
- **Nothing else moved.** No popup, side panel, history UI, `chrome.storage`,
  persistent queue, or background sync; no server, contract, route, processor, or
  schema change; `TextProcessor` remains `text@0.2` and `WebpageProcessor`
  remains `webpage@0.1`.

See [ADR-015](ADR/ADR-015-browser-whole-page-capture.md).


## PDF document ingestion (Phase 3)

The first modality whose material is not a string, and the reason the system
grew an acquisition step.

**Binary cannot honestly live inside a JSON envelope.** `payload.text` means
*the text the user submitted*; a base64 blob in it is a recorded fact that is
false, and every later stage would read it as true. `payload.html` is the same
mistake one modality over, and `source.url` would turn a local file into a fetch
this build does not do. `CapturePayload.file_ref` has meant "an opaque handle to
bytes held outside this contract" since Phase 0A, which is exactly the shape of
the problem — so nothing in the contracts changed.

**`POST /v1/uploads` stages bytes and is not a capture.** It mints no capture id,
creates no `CaptureRecord`, advances no lifecycle, runs no processor, produces no
`ContentObject`, and records no intent, context, or source metadata. It answers
`200` rather than `201` because the raw store deduplicates by content and does
not know whether this request created a file or found identical bytes already
there — claiming "created" would be a guess dressed as a fact. The route is
generic on purpose: images, audio, and arbitrary files stage through it later
without a redesign.

**The uploaded filename has no authority anywhere.** It picks no storage path —
the layout comes from a digest the store computed itself — is not part of raw
identity, is never persisted onto a `CaptureRecord`, never becomes a title, and
is not returned. Traversal sequences in it are therefore not dangerous input
that has been sanitized; they are input that is never read.

**Only UniMem raw references are resolved.** A `file_ref` must be
`sha256:<64 lowercase hex>`. A filesystem path, `file://` URL, HTTP URL, or S3
URL is refused and — the part that matters — is never opened, resolved, or
fetched. Accepting a path would move authority from the upload boundary into
`core` and hand every client of a localhost API a local-file-read primitive.

**The reference is settled before any lifecycle write.** Intake checks the shape,
then that the reference is one this build understands, then that the raw store
actually holds it — three reads — and only then looks at its clock and creates
`RECEIVED`. A malformed, unresolvable, or missing reference leaves no `RECEIVED`
record, no `STORED` record, no raw write, and no content. A well-formed reference
naming bytes nobody staged is a third intake error,
`CaptureMaterialUnavailableError` → `422 capture_material_unavailable`,
deliberately not a `503`: the store answered correctly, and "retry later" would
be advice that cannot terminate.

**Intake writes no bytes on the document path.** The original was immutable and
content-addressed before the capture existed; the capture records a reference to
it rather than a second copy.

**One nonblank page, one canonical segment.** Pages are read in physical order.
A page whose extracted text is absent, empty, or whitespace-only emits nothing —
`strip` decides that and nothing else, so the *stored* text is the parser's
string exactly, with no Unicode normalization, whitespace collapsing,
line-ending rewriting, hyphen repair, de-columnization, or header removal.

**Physical location and canonical order are different facts.**
`Segment.spatial.page` is the 1-based PDF page; `Segment.position` is contiguous
reading order. A blank page leaves a gap in the first and none in the second:

```
PDF page 1 -> text      position=0  spatial.page=1
PDF page 2 -> blank     (no segment)
PDF page 3 -> text      position=1  spatial.page=3
```

**Provenance is `ORIGINAL`, not `OCR`.** The text was embedded in the document
and read straight out of it, which is precisely what a later OCR-capable build
must not be able to be confused with.

**Title precedence** is the webpage rule with `/Title` where `<title>` stood:
the submitted capture title, else a nonblank PDF metadata `/Title`, else none.
Never the uploaded filename, the `file_ref`, the digest, the first page's text,
or a heading. Extracted metadata reaches the `ContentObject` only and never
rewrites `CaptureRecord.title`.

**A textless PDF fails.** A scan parses perfectly and yields nothing a segment
could be built from, so the processor raises `ProcessingInputError` and the
existing orchestrator marks the capture `failed`. Returning `COMPLETE` with zero
segments would be the system reporting that it remembered something when it
remembered nothing. There is no OCR in this build, and the refusal says so. The
exact PDF stays in raw storage, so an OCR-capable build can read those very
bytes later. Encryption is refused too, checked before any page is touched
because the parser would otherwise open an empty-password document silently.

**`pypdf` is `core`'s first non-pydantic runtime dependency**, confined to
`core.processing.pdf` and held to an exact allowlist by test. Unlike HTML, the
standard library has no answer here; the alternatives were a rendering engine
binding, a much larger layout-inference stack, or a subprocess.

**Documents have no completed replay.** `replay.py` is semantically unchanged
and still `TEXT`-only, so a resent document capture id is `409`. Document
idempotency has not been asked for by a connector yet, and a guarantee designed
without a requirement is one nobody can check.

**An unclaimed upload stays on disk.** No garbage collection, lease, expiry,
upload table, or cleanup worker — every one of those needs a policy that would
be invented rather than derived, and an immutable object taking up disk is the
cheapest wrong answer to defer.

**Nothing under `clients/browser-extension/` changed.** No file picker, popup,
PDF button, drag-and-drop, download interception, or new permission.

See [ADR-016](ADR/ADR-016-pdf-document-ingestion.md).


## DOCX document ingestion (Phase 3)

The second document format, and the proof that the first one's generalizations
were real.

**Acquisition was reused, not redesigned.** DOCX bytes stage through the
unchanged `POST /v1/uploads`, get the same `sha256:<64 lowercase hex>`
reference, and are named by the same canonical `document` envelope with a
different `mime_type`. There is no `/v1/docx`, no second staging mechanism, no
second raw store, and no second lifecycle. The upload route is not told that
DOCX exists and gains no validation: **acquisition stores bytes, the capture
declares what those bytes mean, and the processor validates that declaration.**

**Intake generalized by one value.** The supported document MIME set went from a
single string to a two-entry tuple and the equality check became a membership
check. The ordering guarantee, the reference rules, the refusal of paths and
URLs, the no-second-write rule, and every message shape are untouched. No
registry: two entries do not justify one, and intake does not import the
processors — what it decides is which declarations this deployment accepts,
which is a fact about the build.

**The supported type is exactly
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`.**
Matched exactly, never inferred — not from a filename, an extension,
`source.url`, or the ZIP's contents. A DOCX *is* a ZIP and sniffing it would be
easy; sniffing would move the decision about what a capture is from the
submitter to a guess made by the server. Legacy `application/msword`, `.docm`,
ODT, RTF, EPUB, and a bare `application/zip` are refused at intake rather than
stranded.

**The router stays explicit.** `TEXT` reaches `TextProcessor`, `WEBPAGE` reaches
`WebpageProcessor`, a `DOCUMENT` declared `application/pdf` reaches
`PdfProcessor`, and one declared the DOCX type reaches `DocxProcessor` — each
because of what it claims, never because of where it sits in a list. No
first-match routing, no generic `DOCUMENT` fallback, no precedence, no
ambiguity. `DocxProcessor` shares no parsing code with `PdfProcessor`: the
formats have nothing in common below the surface, their canonical outputs differ
in exactly the way that matters, and a common ancestor is how a change to one
silently becomes a change to the other.

**Scope is the main document body, in order.** Body paragraphs and body tables,
walked through the reader's public body-order iteration, so a table between two
paragraphs produces segments between those two paragraphs' segments. Headers,
footers, footnotes, endnotes, comments, tracked-change history, text boxes,
embedded files, images, charts, equations, macros, and custom XML are all
outside this build. Nothing is executed, and an external relationship never
causes a network fetch.

**Paragraph text reaches the segment exactly as the reader returned it.** `strip`
decides blankness and nothing else; the stored value is never the stripped one.
No Unicode normalization, no whitespace collapsing, no heading inference. A Word
`Heading 1` is a paragraph of `TEXT` — style-to-structure mapping is a real
design question and every answer to it is a guess until something downstream
needs the structure.

**A table becomes one `TEXT` segment per nonblank row**, tab-joined from
`cell.text` with the cell strings unstripped, and no wrapper segment for the
table itself. The tab is an explicit canonical flattening boundary, not a
character claimed to have been in the document. `Segment.metadata` carries
`docx_block` (`"paragraph"` or `"table_row"`) plus `table_index` and `row_index`
for a row — coordinates into the document, counting blank rows that emitted
nothing. `position` is one contiguous sequence across paragraphs and table rows
alike. Merged cells repeat across the grid columns they span, which is a
documented limitation pinned down by test rather than a target.

**There are no DOCX page numbers, and their absence is the central decision.** A
PDF page is a fact recorded in the file. A DOCX page is a *result* — of fonts,
page size, printer driver, and renderer — so two machines opening the same file
can legitimately disagree about what is on page four. A page number computed
here would be this server's rendering opinion presented as a property of the
client's document. `spatial` is `None` on every DOCX segment; nothing estimates,
counts, or renders to paginate, and `SpatialLocation.heading` is not a
substitute. One canonical `document` type therefore gives two honest answers:
a PDF segment says which page it was on, and a DOCX segment says it does not
know.

**Provenance is `ORIGINAL`** on every segment, paragraph and table row alike.
The processor rearranged structure into canonical segments; it did not author,
translate, or infer the words. Not `PROCESSOR`, not `OCR`, not `VISION`, not
`HTML`.

**Title precedence is submitted title, else a nonblank core-properties `title`,
else none.** Never the uploaded filename, the `file_ref`, the digest, the first
paragraph, or a heading — and never `subject`, `author`, or `keywords`, which
are a metadata design of their own. Extracted metadata reaches the
`ContentObject` only and never rewrites `CaptureRecord.title`.

**A textless or image-only DOCX fails.** The package parses and its body says
nothing, so the processor raises `ProcessingInputError` and the existing
orchestrator marks the capture `failed`. Returning `COMPLETE` with zero segments
would be the system reporting that it remembered something when it remembered
nothing. No OCR, no image extraction, and no vision model runs to avoid saying
so. A corrupt package and an encrypted one fail the same safe way — an encrypted
Word document is a compound file rather than a ZIP, and no password is
attempted. Only known container, package, and parser failures are translated;
there is no bare `except`, and no reader message, archive member, XML line
number, or path reaches the HTTP response.

**`python-docx` is `core`'s second document parser**, confined to
`core.processing.docx` and held there by test exactly as `pypdf` is held to
`core.processing.pdf`. A further test asserts that nothing in `core` imports a
converter, a renderer, `subprocess`, an image library, or an OCR engine — those
are how fictional page numbers get invented.

**Nothing else moved.** No route, no contract field, no enum member, no lifecycle
state, no persistence migration; the schema stays `0.2`. PDF semantics are
frozen. `replay.py` is semantically unchanged and still `TEXT`-only, so a resent
document capture id is `409` for DOCX as for PDF. Nothing under
`clients/browser-extension/` changed.

See [ADR-017](ADR/ADR-017-docx-document-ingestion.md).


## Opt-in local PDF OCR (Phase 3)

The first optional capability, and the first alternative implementation of an
existing claim.

**Recognition is a deployment decision.** `--pdf-ocr` is the whole of the
configuration surface. There is no request field, no metadata convention, no
intent value, no MIME type, and no header that can turn recognition on, off, or
sideways; the same capture request body works in either deployment. The two PDF
processors are **mutually exclusive** — both claim `DOCUMENT` +
`application/pdf`, `build_local_app` registers exactly one, and registering both
is an `AmbiguousProcessorError` rather than a precedence rule. Registration order
is still not precedence.

**The page policy is embedded-text-first.** A page with nonblank embedded text
becomes a `TEXT` segment with `ORIGINAL` provenance and is never rasterized. A
page with none is rasterized once, recognized once, and becomes an `OCR` segment
with `OCR` provenance — or no segment at all, if recognition returned nothing
usable. `strip()` decides blankness and never rewrites a stored string; there is
no normalization, hyphen repair, spell correction, or model anywhere near the
text.

**A page with any embedded text is covered whole**, so words inside images on such
a page are not read. There is no region-level OCR and no text-layer quality
assessment. `COMPLETE` therefore means this policy finished and its content was
persisted — not that recognition was accurate, and not that every visible word was
captured.

**`core` gains no machinery.** The recognizer arrives as a
`core.processing.ocr.PdfPageOcr` port taking a binary stream and the set of
already-covered pages, and returning small frozen value types. No rasterizer,
imaging library, `subprocess`, native object, bitmap, or temporary path crosses
into `core`, and a result that is not a consistent description of the document —
a duplicate page, an out-of-range page, an excluded page, a **missing** page — is
rejected as an execution failure rather than believed.

**`PdfOcrExecutionError` is deliberately not a `ProcessingError`.** That one fact
carries the lifecycle: `ProcessingOrchestrator` is unchanged, so an engine that is
missing, crashes, times out, or answers inconsistently leaves the capture
`PROCESSING` behind a fixed `503 ocr_unavailable`, with nothing persisted, while
an input verdict — encrypted, malformed, over a limit, or nothing readable — is
the existing `422 processing_failed` and a durable `FAILED`.

**A rasterizer failure is classified by its reason, not by where it happened.**
`PdfiumError.err_code` is populated for document loading and nowhere else, and an
allowlist of exactly three codes — `FPDF_ERR_FORMAT`, `FPDF_ERR_PASSWORD`,
`FPDF_ERR_SECURITY` — is what makes a load failure a verdict about the document.
`FPDF_ERR_UNKNOWN`, `FPDF_ERR_FILE`, `FPDF_ERR_PAGE`, an absent code, and any
unrecognized code are execution failures, because "the renderer failed while
opening this" is not evidence that the document is bad. The exception's message is
never parsed and the original is kept as the cause. All of it sits behind the
pypdf-first refusal, which already stops encrypted and unreadable PDFs before the
rasterizer sees a stream.

**A page raster never outlives the bitmap it was read from.** `to_pil()` returns an
image sharing PDFium's memory, so the PNG is encoded and copied out while the
bitmap is still valid and only then are the image, bitmap and page closed — in that
order, asserted as a sequence of events rather than assumed. The bytes handed to
the subprocess own nothing native.

**The adapter is local, bounded, and serialized.** `src/unimem_ocr/` renders with
PDFium and recognizes with a system Tesseract at a fixed policy: `eng+rus`, 300
DPI onto opaque white, OEM 1, PSM 3, one page at a time, no retry, no orientation
guessing, no forms or annotations, no network. The engine is a fixed argument list
with `shell=False`, the image travels on stdin and the text comes back on stdout,
and no request-derived string becomes an argument or a path. Every call into
PDFium — creation and destruction included — is inside one process-wide lock, and
that lock is never held while the subprocess runs. Named limits bound the work: 50
pages, 20,000,000 raster pixels per page, 30 s per page, 120 s of recognition per
document, on a monotonic clock. **They are limits, not a sandbox**: they do not
bound this process's memory or CPU, they cannot preempt an in-process native
render, and the HTTP request itself has no deadline.

**Nothing existing moved.** No route, no contract field, no enum member, no
lifecycle state, no persistence migration; the schema stays `0.2`.
`core/processing/pdf.py` is byte-for-byte unchanged, and `docx.py`, `text.py`,
`webpage.py`, `replay.py`, the stores, and everything under
`clients/browser-extension/` are untouched. `src/unimem_api/app.py` is unchanged
too: the only delivery-side edit is one row in the error translation table
`create_app` already installed. Enabling OCR revisits no existing record: a capture that already `FAILED` as
a scan stays `FAILED`, and the same bytes are ingested under a **new** capture id,
sharing the raw object and sharing no canonical identity.

See [ADR-018](ADR/ADR-018-opt-in-local-pdf-ocr.md).


## Architectural invariants

1. `ContentObject` is the canonical normalized representation.
2. Markdown is not a source of truth. Implemented in Phase 0D: the Markdown
   projection is lossy by design and nothing parses it back.
3. Derived representations must be reproducible from a `ContentObject`.
   Implemented in Phase 0D: renderers are pure functions of the object, and
   JSON round-trips back to an equal object.
4. Raw originals are immutable once stored; contracts reference them, they do
   not embed or mutate them. Implemented in Phase 0B: content-addressed, with
   no mutation API.
5. Every `Segment` carries `Provenance`. Within a `ContentObject`, every
   segment's `provenance.capture_id` must match the object's
   `source.capture_id`, and any `provenance.asset_id` must resolve to an asset
   of that object.
6. Domain contracts are explicitly schema-versioned
   ([ADR-002](ADR/ADR-002-versioned-domain-contracts.md)).
7. Processors must produce `ContentObject`s, and nothing else may. Implemented
   in Phase 0C: the `Processor` port returns a `ContentObject` or raises.
8. `CaptureEnvelope` describes input, not analysis results.
9. The canonical contracts do not depend on PostgreSQL, HTTP, filesystem
   storage, AI providers, or browser APIs. Reaffirmed in Phase 0E: capture
   records are persisted behind the `CaptureRecordStore` port, and no contract
   gains a table, column, session, or database path.
10. Phase 0A contains domain semantics, not infrastructure.
11. `RawObject` identity is not capture identity. Implemented in Phase 0B for
    raw storage, Phase 0E for capture persistence, and Phase 0F at intake: a
    capture record is keyed by its own opaque `id` — the envelope's — never by a
    raw SHA-256, and records sharing a digest coexist.
12. An accepted capture is registered before its bytes are stored, and no
    lifecycle status is fabricated from an infrastructure failure. Implemented
    in Phase 0F: `RECEIVED` is durable before raw storage, `STORED` replaces it
    afterwards, and a failure in between leaves the truthful `RECEIVED` record
    rather than an invented `FAILED` one.
13. Capture-time metadata is durable and typed, and content is not metadata.
    Implemented in Phase 0G: `context`, `intent`, and `title` are validated
    fields on `CaptureRecord`, written into the first durable snapshot, and
    never smuggled into `error`, `source`, or the raw bytes — which continue to
    hold the captured content exactly and nothing else.
14. A document written by an older supported version stays readable *and*
    rewritable by this build. Implemented in Phase 0G: a `0.1` `CaptureRecord`
    loads, may not claim `0.2` fields, and serializes back out with no `0.2`
    keys — not even null ones, which `extra="forbid"` would reject.

15. Lifecycle state is written only by orchestration, and only a status the
    system has evidence for. Implemented in Phase 0H: processors write no
    status, a `ProcessingError` becomes a durable `failed`, and an
    infrastructure failure leaves the capture `processing` rather than being
    given a terminal state it has not earned.

16. A capture is complete only when its canonical content is durable.
    Implemented in Phase 0I: the `ContentObject` is stored before the
    `COMPLETE` snapshot is written, a content-store failure leaves the capture
    `processing`, and one capture can have at most one durable canonical
    object.

17. Delivery is an adapter, and the kernel never learns about it. Implemented
    in Phase 1: `core` imports no web framework and no `unimem_api` module, HTTP
    translation of typed core failures happens only at the delivery boundary
    without altering a core error, a 5xx body carries no backend path or
    identifier, and no route writes lifecycle state.

18. A connector submits captures and observes them; it never owns lifecycle.
    Implemented in Phase 1's browser connector: it builds the canonical
    envelope, POSTs it exactly once, resolves an ambiguous network outcome with
    one read-only probe rather than a retry, keeps no durable state of its own,
    and never converts a server failure into a local success. Captured content
    reaches it exactly as the source produced it.

Invariants 1, 4, 7 and 10 are design commitments; 2, 3, 5, 6, 8, 9, 11, 12, 13,
14, 15, 16, 17 and 18 are enforced by the models, renderers, stores, intake,
orchestration, the delivery adapter and the connector, and covered by tests.

## Contract rules

- `extra="forbid"` everywhere: unknown fields are errors. This is what keeps
  derived data (summaries, OCR text, embeddings) out of `CaptureEnvelope`.
- `validate_assignment=True`: assigning to a field re-runs that model's
  validators. See *Mutation semantics* below for what this does and does not
  guarantee.
- Datetimes are timezone-aware (`AwareDatetime`); naive input is rejected.
- Identifiers are non-blank strings. No UUID/ULID format is imposed by the
  contracts, and none is planned: how a producer mints ids is the producer's
  choice (Phase 0C's `TextProcessor` uses UUID4), and consumers treat them as
  opaque.
- `metadata` fields are `dict[str, JsonValue]`, so contracts cannot hold
  values that do not survive JSON.
- Enum values are lowercase, stable, and part of the wire format.
- `schema_version` is `0.2` today, and `0.1` remains readable. One version
  names the whole canonical contract set, not one model.
- Serialization uses plain Pydantic: `model_dump(mode="json")` and
  `model_validate`. There is no custom serialization framework.

## Mutation semantics

Canonical contract instances are **validated snapshots**: they are checked when
constructed and when a field is assigned. They are not continuously enforced
objects, and callers must not treat them as such.

What is guaranteed:

- construction through `Model(...)` or `Model.model_validate(...)` runs every
  validator, including the cross-field ones;
- direct assignment to a field of that model — `content.segments = [...]` —
  re-runs the same validators, so a replacement list that breaks the
  provenance, uniqueness, or asset-reference invariants is rejected.

What is **not** guaranteed:

- **In-place mutation of a mutable field.** `content.segments.append(segment)`
  and `content.metadata["k"] = value` mutate the container directly and never
  reach a validator. A segment from a foreign capture, a duplicate id, or a
  non-JSON metadata value can all be introduced this way.
- **Assignment on a nested model.** `segment.provenance.capture_id = "other"`
  revalidates `Provenance` on its own; the enclosing `ContentObject` does not
  recheck that the segment still traces back to its capture.
- **Rollback of a rejected assignment.** A value that violates a field's own
  rules (a blank `title`) is never written. A value that passes those and is
  then rejected by a model-level validator (a segment list that breaks the
  provenance invariant) has already been assigned by the time the error is
  raised — so an instance whose `ValidationError` was caught and swallowed may
  be holding the rejected value. Discard such an instance rather than
  continuing to use it.

The rule for callers: build a new instance rather than editing one in place. If
an object has been mutated and the invariants matter, re-validate it explicitly
with `Model.model_validate(instance.model_dump())`, which runs the full
validator set again.

This is a deliberate boundary, not a defect to design around. Closing it would
mean frozen models, custom collection types, or a mutation API — machinery that
Phase 0A does not need, since contracts are built once at ingestion time and
then serialized.

## Tooling

- Python 3.13+, Pydantic v2. `core`'s runtime dependencies are Pydantic plus one
  document parser per format it reads — `pypdf`, confined by test to
  `core.processing.pdf`, and `python-docx` (with the `lxml` it brings), confined
  the same way to `core.processing.docx`. That list is an exact allowlist a test
  enforces, not a trend. FastAPI and uvicorn are dependencies of the Phase-1
  delivery adapter alone, and no `core` module imports either.
- One **optional** extra, `[ocr]`: `pypdfium2` and `Pillow`, used only by
  `src/unimem_ocr/` and imported only when a deployment asks for recognition. A
  test asserts that no `core` module imports a rasterizer, an imaging library,
  `subprocess`, or the adapter package, and that importing the application loads
  no rasterizer even where the extra is installed. Tesseract and its `eng`/`rus`
  language data are **system** prerequisites this project never installs.
- **mypy** in `strict` mode is the type checker (chosen over pyright because
  Pydantic ships a first-party mypy plugin, and one tool configured in
  `pyproject.toml` is enough for a package this size).
- ruff for lint and formatting, pytest + pytest-cov for tests.
- The browser connector is plain ES modules with **no dependencies** and is
  tested with Node's built-in runner (`node --test`, Node 22 in CI). No bundler,
  transpiler, or JavaScript test framework is used, and the Python package
  depends on none of it.

## Structure

```
src/core/contracts/
  base.py         shared model config, schema version, scalar value types
  enums.py        the closed sets of the domain language
  capture.py      CaptureEnvelope, CaptureRecord, RawObjectRef
  content.py      ContentObject and its parts
  segment.py      Segment, TemporalLocation, SpatialLocation
  provenance.py   Provenance
  asset.py        Asset
  processing.py   ProcessingRecord
src/core/storage/
  raw.py          RawObjectStore port, sha256:<digest> reference format
  local.py        LocalRawObjectStore, the content-addressed local backend
  errors.py       typed storage errors
src/core/processing/
  base.py         the Processor port
  router.py       ProcessorRouter, exactly-one-match routing
  text.py         TextProcessor, UTF-8 text normalization
  webpage.py      WebpageProcessor and the deterministic HTML text extractor
  pdf.py          PdfProcessor, page-aware embedded text from a PDF original
  pdf_ocr.py      PdfOcrProcessor, the opt-in embedded-text-first OCR policy
  ocr.py          the PdfPageOcr port, its value types, and its execution error
  docx.py         DocxProcessor, body-ordered text from a DOCX original
  service.py      ProcessingOrchestrator, the stored-to-complete lifecycle
  errors.py       typed processing, routing and lifecycle errors
src/core/rendering/
  base.py         the Renderer port
  json.py         JsonRenderer, the full-fidelity projection
  markdown.py     MarkdownRenderer, the lossy readable projection
src/core/persistence/
  base.py             the CaptureRecordStore port
  sqlite.py           SqliteCaptureRecordStore, the file-backed SQLite adapter
  content_base.py     the ContentObjectStore port
  content_sqlite.py   SqliteContentObjectStore, its file-backed adapter
  errors.py           typed persistence errors, one hierarchy per port
src/core/intake/
  service.py      CaptureIntake, the envelope-to-stored-capture orchestration
  errors.py       typed intake errors
src/unimem_api/   the HTTP delivery adapter — outside core (Phase 1)
  app.py          create_app, the four capture routes, and the upload route
  models.py       the HTTP response DTOs (there is no request DTO)
  errors.py       the core-failure-to-status translation table
  wiring.py       build_local_app, the local composition root
  __main__.py     python -m unimem_api, and the --pdf-ocr composition
src/unimem_ocr/   the optional local recognizer — outside core (Phase 3, PR 3)
  __init__.py     build_tesseract_ocr, the startup gate and the only entry point
  policy.py       the fixed recognition policy and OcrLimits
  prerequisites.py the engine and language probes, and what must be installed
  tesseract.py    TesseractPdfPageOcr: PDFium for pixels, Tesseract for words
  errors.py       OcrPrerequisiteError, raised only while composing an app
clients/browser-extension/   Chromium MV3 selection connector (Phase 1, PR 2)
  manifest.json     MV3 manifest: activeTab, scripting, loopback host only
  service-worker.js the extension origin: chrome wiring, and where fetch happens
  lib/envelope.js   builds the canonical CaptureEnvelope from a selection
  lib/api.js        the HTTP client: one POST, one optional observational GET
  lib/capture.js    the click-to-capture flow, and the injected selection reader
  lib/feedback.js   outcome -> badge and title
  lib/action.js     applies that feedback to the clicked tab, and never throws
  lib/outcomes.js   the connector's closed set of results
tests/unit/contracts/
tests/unit/storage/
tests/unit/processing/
tests/unit/rendering/
tests/unit/persistence/
tests/unit/intake/
tests/integration/storage/
tests/integration/processing/
tests/integration/rendering/
tests/integration/persistence/
tests/integration/intake/
tests/unit/api/
tests/unit/ocr/
tests/integration/api/
tests/integration/ocr/
clients/browser-extension/tests/
docs/
```

Schema versions live in `src/core/contracts/base.py`: `SCHEMA_VERSION` is the
current one, and `SUPPORTED_SCHEMA_VERSIONS` every version this build can read.
