# capture-core

Core domain contracts and immutable raw-object storage for a universal
multimodal capture and ingestion layer.

Implemented so far:

- **Phase 0A — domain contracts.** The stable domain language:
  `CaptureEnvelope`, `CaptureRecord`, `ContentObject`, `Segment`, `Provenance`,
  `Asset`, `ProcessingRecord`, and the validation rules the rest of the system
  depends on.
- **Phase 0B — local immutable raw-object storage.** A `RawObjectStore` port
  and a local content-addressed backend that persists original bytes, addressed
  by SHA-256 and never modified once stored.

There is no HTTP, database, queueing, extraction, or AI code.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for scope and invariants.

## Layout

```
src/core/contracts/   canonical domain contracts (Pydantic v2 models)
src/core/storage/     raw object store port and local backend
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

Runtime dependencies: **pydantic** only.
