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
and two pure projections — JSON and Markdown — in `src/core/rendering/`. Still
not implemented: capture persistence, transport, persistence or export of
rendered output, and every non-text modality.

## Future data flow

```
Source
  -> Capture            (CaptureEnvelope, CaptureRecord)
  -> Raw Original       (immutable bytes, stored by RawObjectStore — Phase 0B)
  -> Processing         (processors; recorded as ProcessingRecord — Phase 0C)
  -> ContentObject      (canonical, with Segments + Provenance + Assets)
  -> Representations    (derived JSON / derived Markdown — Phase 0D)
  -> later: retrieval
  -> later: ctxalloc
  -> later: agents
```

The capture, raw-original, processing, content, and representation steps exist
today for UTF-8 text; everything below them is future work.

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

**Out of scope here:** capture record persistence, metadata storage or
sidecars, extraction and processors, renderers, HTTP, queues and workers,
remote or cloud backends, and encryption at rest.

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

**Out of scope here:** capture record persistence, capture intake and
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
   storage, AI providers, or browser APIs.
10. Phase 0A contains domain semantics, not infrastructure.

Invariants 1, 4, 7 and 10 are design commitments; 2, 3, 5, 6, 8 and 9 are
enforced by the models and renderers and covered by tests.

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

- Python 3.13+, Pydantic v2. Pydantic is the only runtime dependency.
- **mypy** in `strict` mode is the type checker (chosen over pyright because
  Pydantic ships a first-party mypy plugin, and one tool configured in
  `pyproject.toml` is enough for a package this size).
- ruff for lint and formatting, pytest + pytest-cov for tests.

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
  errors.py       typed processing and routing errors
src/core/rendering/
  base.py         the Renderer port
  json.py         JsonRenderer, the full-fidelity projection
  markdown.py     MarkdownRenderer, the lossy readable projection
tests/unit/contracts/
tests/unit/storage/
tests/unit/processing/
tests/unit/rendering/
tests/integration/storage/
tests/integration/processing/
tests/integration/rendering/
docs/
```
