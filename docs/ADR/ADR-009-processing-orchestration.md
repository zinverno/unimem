# ADR-009: Orchestration owns the capture lifecycle; processors stay pure

Status: accepted (Phase 0H); the content-durability limitation below was closed
in Phase 0I ([ADR-010](ADR-010-canonical-content-persistence.md)).

## Context

[ADR-004](ADR-004-processing-boundary-and-routing.md) built the processing
parts and said plainly what it was leaving out: a processor "does not persist a
capture or a content object, does not advance `CaptureRecord.status` ...
Persistence and orchestration live outside the processor, and do not exist
yet." Phase 0E gave capture records a home, Phase 0F wrote the first two
lifecycle states, Phase 0G made capture metadata durable. What has never
existed is the step that takes a stored capture and runs it.

So Phase 0H asks: **how does a durably `stored` CaptureRecord become a
`processing` capture, get normalized by exactly one processor, and end as
`complete` or a truthful incomplete state?**

The happy path is four calls. The decisions are all in the failure behaviour
and the ordering, and each has a tempting wrong answer.

Take a `CaptureRecord` as the argument, because the caller usually has one.
Then the lifecycle decision is made against a snapshot that was true when the
caller fetched it, and a capture already marked `complete` by someone else gets
processed again on the strength of a stale object.

Write `processing` first and route afterwards, because "we're starting now"
feels like the first thing to record. Then a capture no processor handles — a
deployment wired without the right processor — is left stuck in `processing`
by a configuration mistake that never touched it.

Catch every exception and write `failed`. Then a database timeout, a
half-deployed release, or a bug in a processor all produce a record that says
this capture will never work, and a later reprocessing pass has no way to tell
the doomed captures from the interrupted ones.

Roll back to `stored` when something goes wrong, so it can be retried. Then the
one honest signal that a run was started and did not finish is erased, and
nothing distinguishes "never attempted" from "attempted and lost".

Call `router.process()` after selecting, because it is right there. Then
routing happens twice across a durable state change, and a processor set that
changed in between can run something other than what the record was marked
`processing` for.

## Decision

**A small synchronous orchestrator, and nothing else.**

```python
class ProcessingOrchestrator:
    def __init__(
        self,
        router: ProcessorRouter,
        record_store: CaptureRecordStore,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None: ...

    def process(self, capture_id: str) -> ContentObject: ...
```

No worker, queue, task system, scheduler, retry engine, registry, DI container,
or async surface. One capture, one call.

**Processors stay pure with respect to lifecycle and persistence.** Nothing in
ADR-004 is walked back: a processor is handed a record, returns a
`ContentObject` or raises, and writes no status and no bytes. *Every* lifecycle
write in the system happens in the orchestrator. That is what keeps a processor
testable without a database and reusable by whatever drives it later.

**The capture is loaded by id, never accepted as an object.** `process` takes a
`capture_id` and calls `record_store.get` itself. A lifecycle decision taken
against a snapshot the caller has been holding is a decision about the past.

**Exactly `stored` may begin.** Any other status raises
`InvalidCaptureProcessingStateError` before the router is consulted, before the
clock is read, and before anything is written. `received` has no stored bytes
yet; `processing`, `complete`, `partial`, and `failed` have been through here,
and starting again is *reprocessing* — a decision with its own questions (is
the old content object superseded? was that failure transient?) that this phase
does not answer.

**Route before writing `processing`.** `ProcessorRouter.select` is pure by its
own contract, so a `NoProcessorError` or `AmbiguousProcessorError` leaves the
capture exactly `stored`, with no clock read and no write. A routing failure is
a statement about how the system is wired, not a processing attempt, and it
should not leave a mark on the capture.

**Persist `processing` before the processor does any I/O.** The record is
durable first, so a run that dies mid-processor leaves evidence that it was
started. This is Phase 0F's receipt-before-bytes ordering applied one state
later.

**Call the selected processor directly, once, with its own copy.**
`processor.process(...)`, not `router.process(...)`: selection already happened,
on the near side of the state change. The processor receives a deep copy of the
`processing` snapshot, because a `CaptureRecord` is a validated snapshot rather
than a frozen one — the durable record must stay the authority for what comes
next even if a processor edits what it was handed.

**The output must belong to this capture.** Before recording `complete`, the
orchestrator checks `content.source.capture_id == processing.id` and raises
`ProcessingOutputError` if not. The `ContentObject` contract already validates
its own internal consistency; this is the one thing it cannot check, because it
does not know which capture was asked for. No broader validation is invented
here.

**A `ProcessingError` becomes a durable `failed`, and is re-raised unchanged.**
Including `ProcessingOutputError`. These are verdicts about the capture — not
UTF-8, no raw object, content for someone else — and repeating the run changes
nothing. The `failed` record carries `str(exc)` in `error`, preserves every
capture fact, and the original exception propagates with its own type. No
partial `ContentObject` is ever returned.

If writing `failed` itself fails, the persistence error surfaces — chained to
the processing error, which would otherwise be lost — and the durable record
stays `processing`. Reporting `failed` that was never written would be worse
than reporting nothing.

**Anything that is not a `ProcessingError` leaves the capture `processing`.** A
`RawObjectStoreError`, a `RuntimeError`, an interrupted process: these describe
the run, not the capture. The error propagates unchanged, no terminal status is
fabricated, and there is no rollback to `stored` and no retry. This is Phase
0F's rule — a timeout is not a failed capture — one layer up.

**Success is `complete`, written before the caller sees anything.** A new
validated record, then `record_store.replace`, then the `ContentObject` is
returned. Only `status`, `updated_at`, and `error` differ between `processing`
and `complete`.

**Snapshot lineage: the last durable snapshot is the authority for the next.**
`stored → processing` derives from `stored`; `processing → complete` and
`processing → failed` derive from `processing`. Nothing is re-read from a
caller object or an earlier snapshot across a side-effect boundary. Every
snapshot is constructed and revalidated — never mutated in place, never an
unchecked `model_copy(update=...)` — and nested models are deep-copied so no
two snapshots share a mutable `CaptureIntent.tags` list. This is the rule
intake settled in Phase 0G, now applied to a three-step lifecycle.

**A legacy record keeps its version.** A `stored` schema-0.1 `CaptureRecord`
processes normally, and its `processing`, `complete`, and `failed` snapshots
stay at `0.1` — silently upgrading it would require inventing the `context` 0.2
demands, and there is no honest value. The `ContentObject` it produces is a new
document written by this build, so it carries the current schema version. The
0.1 serialization guarantee from ADR-008 survives the whole lifecycle.

**`TextProcessor` moves to 0.2 and uses the capture's title.** Phase 0G made
`CaptureRecord.title` durable; this is the first thing that reads it.
`ContentObject.title = capture.title`, exactly, or `None` when the capture
carried none. No first-line heuristic, no heading parsing, no filename or URL
derivation, and no title minted from nothing. Because the output changed for an
unchanged input, `version` moves `0.1 → 0.2`, and that value travels
automatically onto every `Provenance` and `ProcessingRecord` the processor
emits.

Plain text has no competing extracted title, so no precedence rule is needed
and none is invented. A webpage or document processor that really does extract
titles will need one — that is its decision to make, with a real conflict in
hand. **Orchestration never edits the returned content object**, including its
title.

**The orchestration clock owns lifecycle timestamps only.** It is read once
entering `processing` and once writing `complete` or `failed`, and not at all
for a missing capture, a wrong starting status, a routing failure, or a
`processing` write that fails before the processor runs. `TextProcessor`'s
internal `ProcessingRecord` timestamps are left exactly as they were.

**Zero new runtime dependencies.**

## Consequences

Positive:

- **The system finally runs end to end.** An envelope becomes bytes, a record,
  and a canonical `ContentObject`, driven by code rather than by a test.
- **Every failure mode says something true.** `failed` means the capture cannot
  be normalized; `processing` means a run started and did not finish. A later
  recovery pass can act on that difference, which is the whole reason for
  keeping it.
- **Processors did not have to change to be orchestrated.** `TextProcessor`
  moved for a title, not for the lifecycle. ADR-004's boundary held.
- **Ordering is testable rather than asserted.** Routing before the write, the
  write before the processor, the completion before the return — all checked
  through one shared journal across the store, router, and processor.
- **Legacy records still flow.** Nothing about the 0.1/0.2 compatibility work
  had to be special-cased here beyond carrying the version.

Costs:

- **The `ContentObject` is not durable, and `complete` does not claim it is.**
  *(Closed in Phase 0I — see the amendment at the end.)* `complete` means
  normalization succeeded and the *capture lifecycle* was durably recorded. The
  object is returned synchronously and stored nowhere, so a crash after
  `complete` but before the caller does something with it loses the normalized
  representation. The immutable raw bytes make it reproducible in principle —
  but reprocessing is refused by the starting-state rule, so in practice such a
  capture is stuck at `complete` with nothing to show. This is named rather
  than hidden: adding a `ContentObjectStore` to make the sentence sound better
  would be building the next boundary badly, in a hurry.
- **Concurrent processing of one capture is not safe.** `CaptureRecordStore`
  has no compare-and-swap, version, or lease, so two workers can both read
  `stored` before either writes `processing`, and both will run. Phase 0H does
  not solve this and deliberately does not reach for locks, leases, optimistic
  version fields, queues, advisory locks, worker-ownership columns, or
  SQLite-specific transactions in orchestration — every one of those is a real
  design decision about ownership and failure detection, and smuggling one in
  here would settle it by accident.
- **A `complete` write that fails leaves a normalized capture marked
  `processing`.** The two stores share no transaction (ADR-007's gap, still
  open), so the work was done and the record does not say so.
- **One capture per call, synchronously.** There is no batch, no scan for
  `stored` captures, and nothing that runs on its own.
- **No retry, resume, or reconciliation.** Captures stranded in `processing`
  accumulate with nothing to sweep them up.

## Alternatives considered

- **`process(capture: CaptureRecord)`.** Rejected: it makes the lifecycle
  decision against a snapshot the caller fetched at some earlier point, so a
  capture already advanced by someone else can be processed again on stale
  evidence. Loading by id costs one read and makes the authority unambiguous.
- **Persist `processing` first, then route.** Rejected: a capture nothing can
  handle would be left in `processing` by a wiring mistake that says nothing
  about the capture. Routing is pure, so doing it first costs nothing and keeps
  configuration errors off the record.
- **`router.process()` after already selecting.** Rejected: it routes a second
  time across a durable state change. Cheap today with one processor, wrong the
  moment the processor set can differ between the two calls — and it would make
  the record's `processing` marker refer to a selection that no longer holds.
- **Mark every exception `failed`.** Rejected: it converts transient
  infrastructure trouble into a terminal verdict about the capture, which is
  exactly what ADR-007 refused at intake. A database timeout is not a capture
  that cannot be normalized, and a record that says otherwise is a lie later
  phases would act on.
- **Roll back to `stored` on infrastructure failure**, so it looks retryable.
  Rejected: it erases the only evidence that a run was started, making "never
  attempted" and "attempted and lost" indistinguishable — and it would let two
  workers ping-pong a capture between states. `processing` is the honest
  record; deciding what to do about it is recovery's job.
- **Orchestration setting or overriding `ContentObject.title`** (from the
  capture, the first line, the URL). Rejected: it would make the orchestrator a
  second, invisible producer of canonical content, so the same object would
  have two authors and `ProcessingRecord` would name only one. Title is
  processor semantics, which is why `TextProcessor`'s version moved.
- **A title-precedence framework** (submitted vs extracted, with rules).
  Rejected as premature: plain text has no extracted title, so there is no
  conflict to resolve and any rule written now would be guessing at what a
  webpage processor will need.
- **Silently upgrading a 0.1 record to 0.2** while processing it. Rejected: 0.2
  requires `context`, and the only values available — `received_at`, the
  processing time — are facts about the system, not about when the user
  captured. Fabricating one to tidy the version is exactly what ADR-008
  refused.
- **Adding a `ContentObjectStore` in this phase** so `complete` could mean the
  content is durable. Rejected: canonical content persistence is its own
  boundary with its own questions (identity, supersession, reprocessing,
  querying), and bolting on a store to make one sentence in this ADR sound
  better is how those questions get answered by default.
- **Locks, leases, optimistic versions, or a queue** to make concurrent
  processing safe. Rejected here, not forever: each implies a model of
  ownership and of how a dead worker is detected, and that deserves its own
  decision rather than being a side effect of the first orchestrator.
- **Retry inside the orchestrator.** Rejected: retry policy needs to know what
  is transient, how many attempts, and with what backoff — none of which this
  phase has evidence for. The typed error split is what a retry layer will need
  when it exists, and it is now in place.

## Amendment (Phase 0I): `COMPLETE` now implies durable content

The limitation this ADR named — "`COMPLETE` does not imply `ContentObject`
durability" — was closed in Phase 0I, as its own designed boundary rather than
as a store bolted on here. See
[ADR-010](ADR-010-canonical-content-persistence.md).

The orchestrator now takes a third dependency, a `ContentObjectStore`, and the
successful path gained one step in the one position that makes the claim true:

```
processor.process -> validate capture association
                  -> ContentObjectStore.create      <- new
                  -> [completion clock]
                  -> CaptureRecord(COMPLETE)
                  -> return the ContentObject
```

**From Phase 0I onward, `COMPLETE` is written only after canonical content is
durable**, and the completion clock is not read until that write succeeds. It
still says nothing about derived Markdown, embeddings, or indexes.

A content-store failure follows this ADR's existing rule without needing a new
one: it is not a `ProcessingError`, so the capture stays `processing`, the
error propagates unchanged, nothing is marked `failed` or rolled back to
`stored`, and no content is returned. Every other row of the failure matrix is
unchanged, including the `FAILED`-write exception chaining.

One new gap arrives with the second store, and Phase 0I states it rather than
hiding it: if the `COMPLETE` write fails *after* content was stored, the
content is durable while the capture still says `processing`. Nothing is
deleted and nothing rolls back — `ContentObjectStore.get_for_capture` exists in
part to make that state findable by a reconciliation pass that does not exist
yet.

The concurrency limitation stands exactly as written. `UNIQUE(capture_id)` in
the content table means two canonical objects can never both become durable for
one capture, which is useful integrity protection — but it does not stop two
workers from both processing, and the loser simply fails at the content write.
