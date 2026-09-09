# Architecture

## Purpose

This project will become a universal multimodal capture and ingestion layer:
it takes heterogeneous digital content — text, webpages, images, documents,
video, code, files — and normalizes it into a versioned canonical
representation that a larger shared-memory / AI context system can build on.

## Current scope: Phase 0A

Phase 0A defines **domain contracts only**. It answers "what are the
fundamental objects flowing through this system?" and deliberately does not
answer how they are stored, processed, or exposed.

Everything in this repository today is in `src/core/contracts/`: Pydantic
models, enums, and validation rules. There is no storage, no transport, no
processing, and no AI code.

## Future data flow

```
Source
  -> Capture            (CaptureEnvelope, CaptureRecord)
  -> Raw Original       (immutable bytes, referenced by Asset / RawObjectRef)
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

## Architectural invariants

1. `ContentObject` is the canonical normalized representation.
2. Markdown is not a source of truth.
3. Derived representations must be reproducible from a `ContentObject`.
4. Raw originals are immutable once stored; contracts reference them, they do
   not embed or mutate them.
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
- `validate_assignment=True`: an object cannot be edited into an invalid state.
- Datetimes are timezone-aware (`AwareDatetime`); naive input is rejected.
- Identifiers are non-blank strings. No UUID/ULID format is imposed yet —
  how identifiers are minted is an open decision.
- `metadata` fields are `dict[str, JsonValue]`, so contracts cannot hold
  values that do not survive JSON.
- Enum values are lowercase, stable, and part of the wire format.
- Serialization uses plain Pydantic: `model_dump(mode="json")` and
  `model_validate`. There is no custom serialization framework.

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
tests/unit/contracts/
docs/
```
