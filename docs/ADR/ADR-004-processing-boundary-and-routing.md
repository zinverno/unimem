# ADR-004: Processors produce canonical ContentObjects, and routing requires one explicit match

Status: accepted (Phase 0C); the orchestration it excluded arrived in Phase 0H
([ADR-009](ADR-009-processing-orchestration.md)).

## Context

UniMem will take in text, webpages, images, documents, video, code, and files.
Each of those needs genuinely different normalization logic — an HTML reader has
nothing in common with a transcription pipeline — and each will be rewritten
over time as better tools appear.

Everything downstream wants the opposite: one shape. Retrieval, rendering,
context allocation, and agents should not care whether a piece of text arrived
as a pasted note, the body of an article, or a caption burned into a video
frame. `ContentObject` already exists for exactly this
([ADR-001](ADR-001-canonical-content-object.md)), and the raw originals every
processor reads are already immutable and content-addressed
([ADR-003](ADR-003-content-addressed-raw-storage.md)).

So Phase 0C has to answer three questions. Where does modality-specific logic
live, and what is it allowed to touch? What does it produce? And how does the
system decide, for a given capture, which of several processors runs?

The third question looks trivial with one processor and is not. Whatever is
decided now becomes the rule for the day a `WebProcessor` and a
`DocumentProcessor` both plausibly claim an HTML file saved to disk.

## Decision

**The processor is the normalization boundary.** One processor handles one
modality, and modality-specific logic exists nowhere else.

- A processor consumes a `CaptureRecord` — the accepted capture context —
  plus whatever explicit dependencies it needs to reach the immutable original.
  `TextProcessor` takes a `RawObjectStore` in its constructor. That is a
  concrete dependency, passed directly; there is no container, no factory, and
  no application context.
- A processor returns a valid `ContentObject`, with segments, mandatory
  provenance, assets, and a `ProcessingRecord` for the run.
- A processor does **not** persist a capture or a content object, does **not**
  advance `CaptureRecord.status`, does **not** mutate the capture record or the
  raw original, does **not** render, and does **not** call an AI service.
  Persistence and orchestration sit outside it and are not built yet. A
  processor therefore accepts a capture in any lifecycle state.
- When a processor cannot produce a valid content object it raises a typed
  `ProcessingError`. It never returns a partial object in order to carry a
  `FAILED` `ProcessingRecord`: there is nowhere to persist such a record today,
  and failure bookkeeping is orchestration's job.
- Errors from the raw object store keep their own types and propagate. "The
  store could not give me the bytes" is a different situation from "this
  capture will never process", and a caller deciding whether to retry needs to
  tell them apart.

**`supports()` is a pure capability check.** It answers, from the capture record
alone, whether this processor handles this kind of capture. It performs no I/O,
reads no stored bytes, mutates nothing, and does not fail because the original
is missing — a capture this processor handles but cannot currently read is
still a capture it handles. The router asks every processor, so the answer has
to be cheap.

**`ProcessorRouter` requires exactly one match.** It is constructed with an
explicit collection of processors, asks all of them, and then:

- one match — that processor is selected;
- no match — `NoProcessorError`;
- several matches — `AmbiguousProcessorError`.

**Registration order is not precedence.** Every processor is asked even after
one has matched, so an overlap is detected rather than hidden. No priority
numbers are introduced: if overlapping capabilities ever need an order, that is
an explicit decision made then, with the overlap in hand.

**The first implementation is `TextProcessor`,** handling
`CapturePayloadType.TEXT` and reading the stored original as strict UTF-8.

**The API is synchronous,** because every concrete dependency it has today is
synchronous.

## Consequences

Positive:

- **Processors stay isolated by modality.** Adding OCR does not touch the HTML
  reader, and rewriting the HTML reader does not touch anything else.
- **Consumers see one shape.** Everything downstream reads `ContentObject`, so
  a new modality is invisible to it.
- **Routing ambiguity fails loudly.** Two processors claiming one capture is a
  design question, and it surfaces as an error at the moment it becomes true
  rather than as a silent behaviour difference discovered later in production.
- **New processors need no changes elsewhere.** They are added to the list the
  router is built with.
- **Storage semantics survive the boundary.** A missing object is still a
  missing object by the time a caller sees it.
- **Processors are trivially testable.** A processor is a function of a capture
  record and a store, so a small in-memory store double is a complete
  environment — no filesystem, no database, no orchestration.

Costs:

- **The router must be constructed explicitly.** Nothing discovers processors,
  so wiring is a place in the application that has to be kept current. This is
  the intended trade: an explicit list is greppable, a registry is not.
- **Overlapping capabilities need a future policy.** Today the router refuses
  to choose. The first genuine overlap — an HTML file that is both a webpage
  and a document — forces a decision about precedence, narrower `supports()`
  predicates, or a merged processor. That work is deferred, not avoided.
- **The synchronous interface may need deliberate evolution.** A processor that
  genuinely blocks on the network (fetching a page, calling a transcription
  service) will want an async surface, and introducing one later means changing
  the port and its callers. That is accepted: an async API today would be
  guessing at a shape nothing needs.
- **Callers catch two exception hierarchies.** Keeping storage errors distinct
  from processing errors is the point, but it does mean a caller that wants to
  treat everything as "this capture did not process" writes two `except`
  clauses.

## Alternatives considered

- **One `if payload_type == ... elif ...` function.** Rejected: it grows a new
  branch per modality, forces every modality's dependencies into one place, and
  makes every processor's tests carry the others' setup.
- **First-match routing.** Rejected: it silently converts the order of a list
  into a precedence rule that is written down nowhere. With one processor it is
  indistinguishable from exact-match routing, which is precisely why it must be
  rejected now — by the time it matters, the behaviour is already relied upon.
- **Priority numbers on processors.** Rejected for now: a priority is a way to
  express a precedence decision, and there is no such decision yet. Adding the
  mechanism first would invite arbitrary numbers to settle overlaps nobody has
  examined.
- **A global registry, decorators, or entry-point discovery.** Rejected: the
  set of processors running in a deployment becomes invisible, import order
  starts affecting behaviour, and a dependency can inject a processor by being
  installed. Explicit construction costs one line.
- **An async `Processor` protocol.** Rejected: the only dependency today is a
  synchronous store, and the first processor does CPU-bound decoding. Async
  now would mean an event loop, async test plumbing, and awaited calls
  throughout, all to wrap synchronous work.
- **Processors that persist their output or advance capture lifecycle.**
  Rejected: it would make every processor depend on a repository that does not
  exist, and would put lifecycle rules in as many places as there are
  modalities.
- **Returning a result object instead of raising.** Rejected: it would require
  either a partial `ContentObject` or a new result type, and Phase 0C has
  nowhere to record a failed run. Raising a typed error loses nothing while the
  orchestration layer is unwritten.

## Amendment (Phase 0H): the missing orchestration now exists

This decision drew the processor boundary by saying what a processor does *not*
do — persist, advance `CaptureRecord.status`, mutate the capture or the
original, render — and noted that "persistence and orchestration live outside
the processor, and do not exist yet". Phase 0H supplied them. See
[ADR-009](ADR-009-processing-orchestration.md).

Nothing above is walked back. `ProcessingOrchestrator` takes a capture id,
loads the authoritative record, requires `stored`, calls
`ProcessorRouter.select`, writes `processing` durably, runs the one selected
processor, and records `complete` — or `failed`, but only for a
`ProcessingError`, which is a verdict about the capture rather than about the
run. Processors remain exactly as pure as this ADR made them: every lifecycle
write in the system happens in the orchestrator, and a processor is still
testable with no database in sight.

Two points here are worth re-reading in that light. The router's insistence on
*exactly one* match is what lets orchestration select before writing any state,
so a wiring mistake leaves the capture untouched at `stored`. And
`ProcessorRouter.process` — described here as "convenience only" — is
deliberately *not* used by the orchestrator: selection has already happened, and
routing a second time across a durable state change could run something other
than what the record was marked `processing` for.

`TextProcessor` moved to version 0.2 in the same phase, for a semantic reason
this ADR anticipated: it now copies the capture's submitted title onto the
content object, so its output changed for an unchanged input.
