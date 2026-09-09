# ADR-001: ContentObject is canonical; other representations are derived

Status: accepted (Phase 0A)

## Context

The system ingests heterogeneous content — text, webpages, images, documents,
video — and must present it to later stages (retrieval, context allocation,
agents) in one shape.

Markdown is the obvious tempting choice: it is human-readable, diffable, and
easy to feed to a model. Systems that take it as their storage format,
however, end up with a lossy funnel. Markdown cannot express a transcript cue
at 302.1–309.4 seconds, an OCR box at (120, 400, 180, 35), a page number, or
the fact that one paragraph came from HTML and the next from a vision model.
Once that information is flattened into prose, re-deriving it means parsing
prose, and the provenance is gone for good.

## Decision

`ContentObject` is the canonical normalized representation. It is structured,
segment-based, provenance-bearing, and storage-neutral.

Markdown, JSON views, and any other rendering are **derived representations**.
They are produced from a `ContentObject` and are never read back as a source of
truth. No component may reconstruct domain state by parsing a derived
representation.

Consequently:

- Processors produce `ContentObject`s, not text.
- Every `Segment` carries `Provenance`, so any rendered fragment can be traced
  back to the capture and asset it came from.
- Raw originals stay immutable and are referenced (`Asset`, `RawObjectRef`,
  `OriginalReference`), never absorbed into the canonical object.
- `derived` (summary, topics, entities) is an *output* slot on the canonical
  object; it is not accepted as capture input.

## Consequences

Positive:

- Multimodal content with timelines, layout, and pages is representable
  without inventing Markdown conventions.
- Rendering can change without a data migration: representations are
  regenerable from the canonical object.
- Provenance is structural rather than a convention in a comment.

Negative / costs:

- More upfront modelling than "just store the text", and processors must fill
  in structure rather than emitting prose.
- Human-readable output requires a rendering step that does not exist yet.
- Renderers must be kept faithful; that is future work, not Phase 0A work.

## Alternatives considered

- **Markdown as storage.** Rejected: lossy for temporal/spatial data and
  destroys provenance.
- **Per-modality models with no shared object.** Rejected: every later stage
  would need to know about every modality.
- **A generic untyped blob with metadata.** Rejected: nothing could be
  validated, and invalid states would be discovered at retrieval time.
