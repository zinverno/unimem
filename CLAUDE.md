# UniMem — working agreement for coding agents

UniMem is a multimodal capture and ingestion layer: heterogeneous content in,
one versioned canonical `ContentObject` out.

## Source of truth, and precedence

Read these before proposing a design. They outrank anything below, and they
outrank every generic third-party skill or plugin configured in this repo:

1. **`docs/ARCHITECTURE.md`** — scope, phase boundaries, the 18 architectural
   invariants, contract rules, mutation semantics, and the file-by-file map.
2. **`docs/ADR/ADR-0XX-*.md`** — 18 accepted decisions. An ADR is binding until
   a new ADR supersedes it.
3. **`README.md`** — operator-facing behaviour, install, and the OCR
   prerequisites this project deliberately does not install.

**Precedence rule.** The `ponytail` and `agent-skills` plugins are advisory
process tools. Where their generic advice conflicts with an invariant, an ADR,
a public contract, a compatibility guarantee, or this project's required
validation, **this repository wins**. Minimalism is never a reason to skip a
required test, widen a `core` dependency, drop a schema-compatibility path, or
quietly revise an accepted decision. If a rule here looks wrong, say so and
propose an ADR — do not route around it.

## Architecture in one screen

```
src/core/          the kernel. Depends on pydantic + one parser per format.
  contracts/       the domain language. No storage, transport, or AI.
  storage/         RawObjectStore port + content-addressed local backend.
  processing/      Processor port, router, per-modality processors, orchestrator.
  rendering/       Renderer port; JSON (full fidelity) and Markdown (lossy).
  persistence/     CaptureRecordStore + ContentObjectStore ports, SQLite adapters.
  intake/          CaptureIntake: envelope -> raw object + registered record.
src/unimem_api/    FastAPI delivery adapter. OUTSIDE core.
src/unimem_ocr/    optional Tesseract/PDFium recognizer. OUTSIDE core, [ocr] extra.
clients/browser-extension/   Chromium MV3 connector. Plain ES modules, no deps.
```

Load-bearing rules these encode — all of them enforced by tests:

- `core` imports no web framework, no rasterizer, no imaging library, no
  `subprocess`, and no `unimem_api` or `unimem_ocr` module. Its runtime
  dependency list is an exact allowlist, not a trend.
- Delivery translates core failures into status codes at the boundary only. No
  route writes lifecycle state; no 5xx body leaks a backend path.
- Lifecycle state is written only by orchestration, and only a status the system
  has evidence for. An infrastructure failure leaves a truthful non-terminal
  state rather than an invented `failed`.
- Contracts are `extra="forbid"` and schema-versioned. `SCHEMA_VERSION` is `0.2`
  and `0.1` stays readable *and* rewritable.
- Contract instances are validated snapshots, not continuously enforced objects.
  Build a new instance rather than mutating one in place.
- **A closed phase stays closed.** Phases 0, 1 and 2 are closed. Build around
  the foundation, do not reopen it.

## Repository exploration

A knowledge graph is available via the `graphify` CLI. It is an optimization,
not a substitute for reading the code you are about to change.

- Build or refresh it with `graphify update .` (AST-only, no API key, no network).
  Output lands in `graphify-out/`, which is gitignored — never commit it.
- If `graphify` is not on `PATH` (a fresh cloud session may not have it), install
  it with `uv tool install graphifyy`, or just skip it — the graph hooks no-op
  when the CLI is absent, and normal search still works.
- For structural questions — architecture, dependencies, call paths, ownership,
  blast radius — query the graph before broad source scans:
  - `graphify query "<question>"` — scoped subgraph for a question
  - `graphify affected "<symbol>"` — reverse traversal, i.e. blast radius
  - `graphify path "<A>" "<B>"` — how two things connect
  - `graphify explain "<symbol>"` — a node and its neighbours
  - `graphify god-nodes` — the architectural hubs
- Then **read the actual source** for every file you intend to edit. The graph
  tells you where to look; it does not tell you what the code does.
- Search narrowly before reading whole directory trees. Do not re-read unchanged
  files without a reason.

## Implementation

- Understand the affected path before modifying it, including its tests and the
  ADR that governs it.
- Prefer the smallest change that **fully** satisfies the requirement.
- Do not refactor unrelated working code, and do not introduce abstractions for
  hypothetical future needs. One ported behaviour, one reason.
- Preserve existing architecture and ADR decisions. A change that contradicts
  one needs a new ADR in the same PR, not a silent edit.
- New architectural decisions get an ADR in `docs/ADR/`, and `ARCHITECTURE.md`
  gains the matching section.

For a non-trivial change: clarify the requirement, identify the affected
surface, plan, implement in thin slices, run focused tests, then read the final
diff before proposing it. A trivial fix does not need that ceremony — skip
straight to the change and its test.

## Validation

Focused checks first; widen only when the risk warrants it. Do not re-run the
full suite repeatedly without a reason.

```bash
ruff check .                       # lint
ruff format --check .              # formatting
mypy                               # strict, src + tests
pytest tests/unit/<area>           # the focused loop while working
pytest --cov=core --cov=unimem_api # the gate CI runs; coverage floor is 90%
npm test --prefix clients/browser-extension   # only if the connector changed
```

Warnings are errors (`-W error`), markers and config are strict, and mypy runs
in `strict` mode. The OCR suites need the `[ocr]` extra plus a system Tesseract
with `eng` and `rus` data; without them they skip locally and CI requires them.
