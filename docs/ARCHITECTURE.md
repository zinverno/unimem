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
`src/core/storage/`. Nothing else is implemented: no capture persistence, no
extraction, no processors, no transport.

## Future data flow

```
Source
  -> Capture            (CaptureEnvelope, CaptureRecord)
  -> Raw Original       (immutable bytes, stored by RawObjectStore — Phase 0B)
  -> Ingestion          (processors; recorded as ProcessingRecord)
  -> ContentObject      (canonical, with Segments + Provenance + Assets)
  -> Representations    (JSON, Markdown, ... all derived)
  -> later: retrieval
  -> later: ctxalloc
  -> later: agents
```

Only the contract shapes exist today; every arrow is future work.

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

## Architectural invariants

1. `ContentObject` is the canonical normalized representation.
2. Markdown is not a source of truth.
3. Derived representations must be reproducible from a `ContentObject`.
4. Raw originals are immutable once stored; contracts reference them, they do
   not embed or mutate them. Implemented in Phase 0B: content-addressed, with
   no mutation API.
5. Every `Segment` carries `Provenance`. Within a `ContentObject`, every
   segment's `provenance.capture_id` must match the object's
   `source.capture_id`, and any `provenance.asset_id` must resolve to an asset
   of that object.
6. Domain contracts are explicitly schema-versioned
   ([ADR-002](ADR/ADR-002-versioned-domain-contracts.md)).
7. Processors added later must produce `ContentObject`s.
8. `CaptureEnvelope` describes input, not analysis results.
9. The canonical contracts do not depend on PostgreSQL, HTTP, filesystem
   storage, AI providers, or browser APIs.
10. Phase 0A contains domain semantics, not infrastructure.

Invariants 1–4, 7 and 10 are design commitments; 5, 6, 8 and 9 are enforced by
the models and covered by tests.

## Contract rules

- `extra="forbid"` everywhere: unknown fields are errors. This is what keeps
  derived data (summaries, OCR text, embeddings) out of `CaptureEnvelope`.
- `validate_assignment=True`: assigning to a field re-runs that model's
  validators. See *Mutation semantics* below for what this does and does not
  guarantee.
- Datetimes are timezone-aware (`AwareDatetime`); naive input is rejected.
- Identifiers are non-blank strings. No UUID/ULID format is imposed yet —
  how identifiers are minted is an open decision.
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
tests/unit/contracts/
tests/unit/storage/
tests/integration/storage/
docs/
```
