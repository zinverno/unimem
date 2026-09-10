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
  identical text both succeed, sharing one deduplicated raw object.
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
- **POST exactly once, and no automatic retry.** A real HTTP error is a definite
  answer and is reported as one; `409` is a conflict, not idempotent success.
- **Network ambiguity is resolved by observation.** If the POST never reaches an
  HTTP response, the connector performs at most one read-only
  `GET /v1/captures/{id}` on the id it already minted and reports what it finds —
  complete, some other durable state, not found, or unknown. It never re-POSTs
  and never mutates anything: lifecycle stays core-owned.
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

- Python 3.13+, Pydantic v2. Pydantic is `core`'s only runtime dependency, and
  stays so: FastAPI and uvicorn are dependencies of the Phase-1 delivery
  adapter alone, and no `core` module imports either.
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
  app.py          create_app and the four routes
  models.py       the HTTP response DTOs (there is no request DTO)
  errors.py       the core-failure-to-status translation table
  wiring.py       build_local_app, the local composition root
  __main__.py     python -m unimem_api
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
tests/integration/api/
clients/browser-extension/tests/
docs/
```

Schema versions live in `src/core/contracts/base.py`: `SCHEMA_VERSION` is the
current one, and `SUPPORTED_SCHEMA_VERSIONS` every version this build can read.
