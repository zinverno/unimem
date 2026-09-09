# capture-core

Core domain contracts for a universal multimodal capture and ingestion layer.

This repository is at **Phase 0A**: it defines the stable domain language —
`CaptureEnvelope`, `CaptureRecord`, `ContentObject`, `Segment`, `Provenance`,
`Asset`, `ProcessingRecord` — and the validation rules the rest of the system
will depend on. It contains **no** storage, HTTP, queueing, or AI code.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for scope and invariants.

## Layout

```
src/core/contracts/   canonical domain contracts (Pydantic v2 models)
tests/unit/contracts/ unit tests for those contracts
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
