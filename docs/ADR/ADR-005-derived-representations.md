# ADR-005: External representations are derived projections of the ContentObject

Status: accepted (Phase 0D)

## Context

By the end of Phase 0C the system can turn captured bytes into a canonical
`ContentObject`: segments, mandatory provenance, assets, and a processing
record ([ADR-001](ADR-001-canonical-content-object.md),
[ADR-004](ADR-004-processing-boundary-and-routing.md)). Nothing outside the
process can read it. Phase 0D answers the next question, and only that
question: **how do we derive an external readable representation from an
existing canonical object?**

Two consumers want two different things, and conflating them is the trap.

A **machine** — a future exporter, a sync target, a test harness, another
service — wants everything: ids, digests, asset references, provenance,
processing history, metadata. Losing any of it means the representation cannot
stand in for the object.

A **human, or a language model reading like one** — wants the text and nothing
else. Ids and SHA-256 digests interleaved with prose are noise that costs
tokens and attention, and they degrade the thing the reader came for.

The failure mode this decision exists to prevent is the familiar one: Markdown
starts as a readable view, someone needs one more field, YAML frontmatter is
added to carry it, and within a few phases the Markdown file is what the system
actually reads back — the canonical object has become an implementation detail
of a text format. Invariant 2 ("Markdown is not a source of truth") was written
before any renderer existed precisely so this decision would not be made by
accretion.

## Decision

**Rendering is a pure projection of an existing `ContentObject`.** The object
is the source of truth; every rendered string is derived from it and
reproducible from it. No rendering output is ever read back as domain state,
and nothing in the system parses a rendering into contracts.

**A minimal `Renderer` port.**

```python
class Renderer(Protocol):
    name: str
    version: str
    media_type: str

    def render(self, content: ContentObject) -> str: ...
```

Three identity attributes and one synchronous method. `name`, `version`, and
`media_type` describe rendering semantics, not deployment: `version` changes
when the output for an unchanged object changes, which is what makes
"reproducible from the object plus the renderer version" a checkable claim. The
two implementations are `json/0.1/application/json` and
`markdown/0.1/text/markdown`.

**Renderers are pure functions.** A renderer consumes a `ContentObject` and
nothing else. It does not read `RawObjectStore` or a `CaptureRecord`, touch the
filesystem or the network, consult the clock, read the environment, or mutate
anything. Same validated object plus same renderer version produces the same
string, byte for byte.

**JSON is the full-fidelity projection.** It serializes the whole content
object through Pydantic's own `model_dump(mode="json")` — not a hand-maintained
field mapping — and emits it with `ensure_ascii=False`, `sort_keys=True`, and
compact separators. It is a bare document with no wrapper key, so it feeds
straight back into `ContentObject.model_validate_json(...)`. Mapping keys are
sorted so that objects built in different insertion orders render identically;
list order is left exactly as the content object holds it, because segment,
asset, topic, and processing order is domain state, not spelling. `NaN` and the
infinities are refused rather than written, since they are not JSON and would
not survive the round trip.

This is deterministic, but it is deliberately **not** RFC 8785 / JCS canonical
JSON and does not claim to be.

**Markdown is a deliberately lossy readable projection.** The rules are the
whole specification:

- an existing `title` becomes `# <title>`;
- every segment whose `text is not None` follows, in the order the object
  stores them;
- blocks are joined by exactly one blank line (`\n\n`);
- segment text is reproduced exactly — no trimming, Unicode normalization,
  line-ending rewriting, escaping, chunking, or summarizing;
- segments are **not** sorted by `position`;
- no title is ever fabricated, from the content id or anything else;
- no ids, digests, asset references, provenance, processing records, metadata,
  topics, entities, or JSON appear, and there is no YAML frontmatter;
- no title and no textual segment renders as `""`, and no trailing newline is
  appended beyond what the last block already carries.

**Markdown is not made reversible.** A consumer that needs provenance, assets,
or exact machine state reads the `ContentObject` or its JSON projection.
Stuffing metadata into Markdown to close the gap is explicitly rejected.

**Phase 0D renders and stops there.** No renderer writes a file, no export
service exists, nothing is persisted, and no artifact is recorded. There is no
renderer registry, router, discovery mechanism, cache, template engine, or
options framework: a caller picks a renderer and calls it.

**Zero new runtime dependencies.** The standard library's `json` module is
enough; Pydantic remains the only runtime dependency.

## Consequences

Positive:

- **The canonical object stays canonical.** Two representations exist, and
  neither can quietly become the thing the system reads back.
- **Reproducibility is testable, not aspirational.** Purity plus deterministic
  key ordering means "render it again" is a real assertion, and a rendered
  string never has to be stored to be trusted.
- **New contract fields appear in JSON for free.** Serialization goes through
  Pydantic, so nothing has to be remembered when a contract grows a field, and
  nothing lingers when one is removed.
- **Markdown stays cheap to read.** It carries text and a title, which is what
  a human or an LLM context window actually wants.
- **Renderers are trivially testable.** A renderer is a function of one object:
  no store, no filesystem, no clock, no fixtures beyond a content object.
- **Adding a renderer changes nothing else.** An HTML or plain-text renderer is
  a new class satisfying the same protocol.

Costs:

- **Markdown cannot be parsed back.** That is the point, but it does mean any
  future round-tripping workflow (editing a note in Obsidian and re-ingesting
  it) is a separate design problem about capture, not a rendering feature.
- **Two representations must be kept coherent.** They will not drift as long as
  both derive from the same object, but a future renderer that starts making
  its own decisions about content would break that, and only review prevents
  it.
- **Deterministic is not canonical.** Sorted-key JSON is reproducible for the
  objects this system produces. If output ever has to be signed or hashed as an
  interoperable canonical form, RFC 8785 becomes a real decision to make then.
- **Callers must place output themselves.** Nothing writes a file, so the first
  consumer that needs a file on disk brings its own I/O — deliberately, so that
  the path, naming, and overwrite policy get designed rather than defaulted.
- **The synchronous, option-free API may need deliberate evolution.** A
  renderer with genuine variants (heading offsets, an include-metadata mode)
  would need an options surface, and that is a decision to make with a real
  requirement in hand.

## Alternatives considered

- **Markdown as the canonical representation** (store Markdown, treat the
  object as a view). Rejected: it destroys provenance, segment boundaries,
  spatial and temporal locations, asset references, and processing history the
  moment it is written, and it makes every future consumer a Markdown parser.
  Invariants 1 and 2 exist to forbid exactly this.
- **Full metadata duplicated into Markdown** (YAML frontmatter, HTML comments,
  or an appended JSON block) to make it reversible. Rejected: it produces a
  second, worse serialization of the object that must be kept in sync with the
  first, it degrades the readable output for the reader it exists to serve, and
  it makes "parse the Markdown back" look supported. JSON already round-trips
  losslessly; a lossy projection that pretends otherwise is the worst of both.
- **A renderer registry, router, or discovery mechanism.** Rejected for the
  same reasons as ADR-004's processor registry: it hides which renderers a
  deployment has, lets import order affect behaviour, and solves a selection
  problem nobody has. There are two renderers and no ambiguity — a caller that
  wants Markdown constructs `MarkdownRenderer`. Note that the processor router
  exists because *the system* must pick one processor for a capture; nothing
  forces a rendering choice on the system, so there is nothing to route.
- **Renderers that write files** (`render_to(path)`, an export service, an
  artifact record). Rejected: it would make a renderer impure, drag in path
  layout, naming, overwrite and atomicity policy, and give the pure-function
  boundary away for a convenience. Where output goes is a separate decision
  with its own trade-offs, and no caller needs it yet.
- **A hand-built JSON schema mapping** (an explicit field-by-field
  serializer). Rejected: it is a second definition of every contract that must
  be updated in lockstep with the first, and the failure mode is silent — a new
  field is simply missing from exports until somebody notices. Pydantic already
  defines the serialization, and ADR-002's contract rules say serialization is
  plain `model_dump` / `model_validate`.
- **RFC 8785 canonical JSON.** Rejected for now: it is a cryptographic
  commitment (number formatting, string escaping, code-point ordering) that
  buys nothing until something is signed, and claiming it without implementing
  it would be worse than not claiming it.
- **A base `Renderer` ABC instead of a `Protocol`.** Rejected: it would force
  inheritance on every implementation for no checking a `Protocol` does not
  already give, and mypy verifies conformance structurally today.
- **An async `render`.** Rejected: rendering is CPU-bound string building with
  no I/O by construction. An awaitable surface would add an event loop and
  async test plumbing to wrap synchronous work.
