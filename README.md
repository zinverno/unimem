# capture-core

Core domain contracts, immutable raw-object storage, capture-record
persistence, and text capture intake for a universal multimodal capture and
ingestion layer. Canonical contracts are at schema version **0.2**; `0.1`
documents remain readable and are rewritten as `0.1`.

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

There is no HTTP, queueing, or AI code, and no ORM.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for scope and invariants.

## Layout

```
src/core/contracts/   canonical domain contracts (Pydantic v2 models)
src/core/storage/     raw object store port and local backend
src/core/processing/  processor port, router, and the UTF-8 text processor
src/core/rendering/   renderer port and the JSON and Markdown projections
src/core/persistence/ capture record store port and the SQLite adapter
src/core/intake/      capture intake, the envelope-to-stored-capture flow
tests/                unit and integration tests
docs/                 architecture notes and ADRs
```

## Development

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/pytest --cov=core --cov-report=term-missing   # tests + coverage
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                          # type checking
```

Runtime dependencies: **pydantic** only. Persistence uses the standard
library's `sqlite3`.
