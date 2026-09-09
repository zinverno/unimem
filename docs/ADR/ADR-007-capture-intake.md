# ADR-007: Intake registers the capture before it stores the bytes

Status: accepted (Phase 0F)

## Context

Phases 0A–0E built components. Contracts describe what flows through the system
([ADR-001](ADR-001-canonical-content-object.md),
[ADR-002](ADR-002-versioned-domain-contracts.md)), the raw object store persists
immutable bytes ([ADR-003](ADR-003-content-addressed-raw-storage.md)),
processors normalize ([ADR-004](ADR-004-processing-boundary-and-routing.md)),
renderers project ([ADR-005](ADR-005-derived-representations.md)), and the
capture record store persists lifecycle snapshots
([ADR-006](ADR-006-capture-record-persistence.md)). Every one of them is called
by a test and by nothing else. `CaptureEnvelope` — the contract for what a
submitter actually hands over — has never been consumed by anything at all.

Phase 0F asks the first question whose answer is a *sequence*: **how does a
valid inline-text `CaptureEnvelope` become an immutable raw object plus a
durably registered `CaptureRecord`?**

The interesting part is not the happy path, which is four calls. It is that
this is the first place two independent stores are written in one logical
operation, and they have no shared transaction. Something will fail between
them. What the system does at that moment is the decision.

The tempting answers are all wrong in a way that is hard to undo later.

Store the bytes first, because they are the irreplaceable thing, and record the
capture afterwards. Then a crash in between leaves bytes in a content-addressed
store with nothing anywhere saying a capture was ever submitted — unreferenced,
undiscoverable, and indistinguishable from garbage.

Catch the failure and write `status = FAILED`. Then the record says the capture
is finished and doomed, when in fact the bytes may already be stored and the
whole thing is one retry away from complete. A terminal status invented by an
error handler is a lie that later phases will believe.

Treat a duplicate capture id as success, since "the capture is already there".
That is idempotency, and idempotency needs a request identity that says two
submissions are the *same* submission. There isn't one. Silently swallowing a
duplicate id turns an id-minting bug into missing data.

Delete the raw object to undo a failed record write. There is no delete — by
ADR-003's design — and adding one to fake a rollback across two stores would
trade a real invariant (originals are immutable) for the appearance of
atomicity that still would not hold.

## Decision

**Intake is the first orchestration boundary, and it is small.**

```python
class CaptureIntake:
    def __init__(
        self,
        raw_store: RawObjectStore,
        record_store: CaptureRecordStore,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None: ...

    def accept(self, envelope: CaptureEnvelope) -> CaptureRecord: ...
```

Both stores arrive as their ports, so intake depends on no backend. The clock
arrives as a plain callable, because a test needs to control time and a
function already does that: there is no `Clock` class, no service hierarchy, no
container, no registry, and no router. Phase 0F accepts one payload type, so
there is nothing to route.

**The receipt is written before the bytes.**

1. Refuse anything that is not an inline `TEXT` payload, before any side effect.
2. Read the clock for `received_at`.
3. Build a valid `CaptureRecord` — same id, source, and payload type as the
   envelope — with status `RECEIVED`, `updated_at=None`, `raw_object=None`,
   `error=None`.
4. `record_store.create(...)`. **This happens before raw storage.**
5. Store the exact UTF-8 bytes through `raw_store.store_bytes(...)`.
6. Read the clock again for `updated_at`.
7. Build a *new* valid `CaptureRecord` with the same identity, source, payload
   type, and `received_at`, now with status `STORED` and the returned
   `RawObjectRef`.
8. `record_store.replace(...)` and return that snapshot.

Ordering the receipt first is the whole decision. A failure after step 4 leaves
a durable, truthful statement that a capture was accepted and is incomplete —
which is recoverable. The reverse order leaves orphaned bytes, which is not.

**Two snapshots are built, never one mutated.** Step 7 constructs and validates
a new `CaptureRecord` rather than editing the first. ADR-002 and the Phase 0A
mutation-semantics rules already say why: in-place mutation and
`model_copy(update=...)` do not re-run a model's cross-field validators, so an
"updated" record is not a checked record. Nested models are copied rather than
aliased, so nothing intake returns shares state with the envelope the caller
still holds.

**The envelope's id becomes the capture record's id.** A capture is an event
that its submitter named; intake does not rename it. This is capture-event
identity, and it is emphatically not raw-object identity: the SHA-256 addresses
bytes (ADR-003), and it never becomes, seeds, or derives a capture id. Two
envelopes with different ids and identical text produce two capture records
referencing one deduplicated raw object — the layering ADR-003 and ADR-006
established, now exercised end to end.

**A duplicate capture id is an error.** `record_store.create` raises
`CaptureRecordAlreadyExistsError` and it propagates unchanged, before any byte
is written. Phase 0F has no idempotency keys, retry tokens, replay, or resume.
Submission idempotency needs an explicit request identity defined with a real
client in hand; inferring it from a repeated id would be exactly the hidden
upsert ADR-006 rejected.

**`received_at` comes from the intake clock, not from the envelope.**
`context.captured_at` is when the *user* captured something, which a client
supplies and may get wrong; `received_at` is when UniMem accepted it, which
only UniMem can know. `updated_at` is the second clock reading, taken after the
bytes are safely stored. Persistence itself still creates no timestamps
(ADR-006) — intake supplies both, and the store transcribes them.

**Text is encoded and nothing else.** The bytes are exactly
`envelope.payload.text.encode("utf-8")`: no trimming, Unicode normalization,
line-ending rewriting, BOM insertion, charset detection, or alternate encoding.
`payload.mime_type` is passed through as declared, and an absent MIME type
stays absent — intake does not invent `text/plain`. Phase 0C's `text/plain`
fallback lives on the *asset* it builds, and stays there.

**Failures keep their own types.** Intake adds `CaptureIntakeError` with two
subclasses, and they draw the one distinction intake is entitled to make about
an envelope it will not accept:

- `UnsupportedCapturePayloadError` — the payload is not `TEXT`. The envelope is
  perfectly valid; this phase simply has no capability for it, and a later
  phase will accept the identical envelope unchanged.
- `InvalidCaptureEnvelopeError` — a `TEXT` payload carrying no text. `TEXT`
  *is* supported, so this is not an unsupported payload: the envelope
  contradicts its own contract, and no future phase will accept it as it
  stands. It is reachable only because Phase 0A documents that an assignment
  rejected by a model-level validator has already been written, so a caller can
  hold an envelope that no longer satisfies its own invariants.

The difference is what a caller does next — wait for a later phase, or fix the
submission — which is why one error for both would be the wrong shape. Both are
raised before the clock is read or a store is touched.

Store errors are not wrapped. `RawObjectWriteError` and
`CaptureRecordPersistenceError` reach the caller as themselves, because "the
bytes did not get written" and "the database is down" lead to different
decisions, and flattening them into one intake error destroys the only
information a caller has.

**Nothing is rolled back, and nothing is fabricated.** If raw storage fails, the
record stays `RECEIVED` and the error propagates. If the final `replace` fails,
the record stays `RECEIVED` while the raw object already exists — the bytes are
stored and the record does not yet say so. No `FAILED` status is invented, and
no raw object is deleted. **This is a real, documented atomicity gap:** the two
stores are separate transactions, and Phase 0F does not close it. It records
recoverable incompleteness instead of pretending to a consistency it cannot
provide.

**Intake stops when the `STORED` record is durable.** No processor runs, no
`ProcessorRouter` is consulted, no `ContentObject` is built or persisted,
nothing is rendered or exported, and there is no HTTP, CLI, or browser surface.

**Envelope-only metadata is dropped, visibly.** `CaptureRecord` has no field for
`context` (captured_at, device, application), `intent` (action, collection,
tags), or `payload.title`. Phase 0F durably retains only what the current
contract can represent — id, source, payload type, status, timestamps, raw
reference — plus the raw content bytes. That metadata is **not** smuggled into
`error`, `source`, the stored bytes, or any improvised field, and the contract
is not changed here to make room for it.

**Zero new runtime dependencies.** Pydantic remains the only one.

## Consequences

Positive:

- **A failure is recoverable rather than invisible.** Every accepted capture has
  a durable record from the first moment, so an incomplete one can be found and
  finished later by whatever does recovery.
- **The system is honest about its own state.** `RECEIVED` means what it says.
  Nothing writes a terminal status it does not know to be true.
- **Identity survives the boundary.** The one place a SHA-256 could have become
  a capture id is now the place with tests saying it does not.
- **Backends stay replaceable.** Intake is exercised against both fakes and the
  real local/SQLite pair, and it never names either.
- **Time is testable.** An injected clock makes `received_at` and `updated_at`
  assertions exact, with no sleeping, freezing, or patching.
- **The next phase has an obvious shape.** A stored `RECEIVED`-or-`STORED`
  record is exactly what a processing orchestrator will pick up.

Costs:

- **Cross-store atomicity is not solved.** A crash between the raw write and the
  final `replace` leaves a `RECEIVED` record and stored bytes that nothing
  points at. It is recoverable — the digest is recomputable from the same text,
  if the text is resubmitted — but Phase 0F provides no resume, sweeper, or
  reconciliation, and a real deployment will need one.
- **Envelope metadata is lost today.** Device, application, capture time,
  intent, collection, tags, and title are accepted and then dropped. This is the
  most pressing gap in the whole system: it must be solved — as a deliberate
  contract change with durable fields — **before any real connector**, because a
  browser extension's whole value is context, and a lossy intake would silently
  discard it at scale.
- **Two writes per capture.** `create` then `replace` costs a second round trip
  compared with writing `STORED` once. That is the price of the receipt, and it
  is the right trade at any volume this system will see before it has a queue.
- **Text only.** Every other payload type is refused. That is the phase, not a
  defect — but no image, document, or webpage can be captured yet.
- **No idempotency.** A client that retries a submission after a timeout gets
  `CaptureRecordAlreadyExistsError` and must decide what that means. Giving it a
  better answer needs a request identity that does not exist yet.
- **Two clock reads.** `received_at` and `updated_at` come from separate
  readings, so they differ by the duration of the raw write. That is accurate,
  not sloppy, but it does mean the two are never equal in practice.

## Alternatives considered

- **Store the raw bytes first, then create the record.** Rejected: a crash
  between them leaves bytes with no receipt — unreferenced, undiscoverable, and
  impossible to distinguish from garbage without scanning every capture. The
  reverse order fails into a state that names itself.
- **Write one `STORED` record after the bytes land, skipping the receipt.**
  Rejected for the same reason, with the added loss that there is then no
  moment at which the system can say "we accepted this and are working on it" —
  which is precisely what an API caller is told.
- **Catch infrastructure errors and persist `status = FAILED`.** Rejected: it
  invents a terminal state from a transient condition. A database timeout is not
  a failed capture, and a record saying otherwise is a lie later phases would
  act on. Intake propagates the typed error and leaves the truthful `RECEIVED`.
- **Delete the raw object when the final `replace` fails.** Rejected twice over:
  the raw store has no delete by ADR-003's design, and adding one would sacrifice
  immutability to *simulate* a rollback that still would not be atomic. Worse,
  the digest may be shared with another capture, so deleting it could destroy a
  different capture's original.
- **Treat a duplicate capture id as idempotent success.** Rejected: idempotency
  requires a request identity asserting that two submissions are the same
  submission, and a repeated capture id does not assert that — it is just as
  likely a client minting collisions. Swallowing it turns an id bug into missing
  data, which is ADR-006's hidden-upsert failure mode one layer up.
- **Derive the capture id from the raw SHA-256** (or from the URL, text, or
  source). Rejected outright: ADR-003 exists to keep byte identity and capture
  identity apart, and this is the boundary where they meet. Deriving one from
  the other silently merges two captures of the same text into one.
- **Run the processor inside intake**, producing a `ContentObject` in the same
  call. Rejected: it would fuse "the capture is safe" with "the capture is
  understood", so a processor bug would make intake fail and lose the
  submission. They are different concerns with different failure semantics and
  different retry stories, and the whole reason the bytes are stored first is
  that processing can happen later, repeatedly, from the original.
- **Carry envelope metadata in `CaptureRecord.error`, a metadata dict, or a
  sidecar next to the raw bytes.** Rejected: `error` is for errors, an
  improvised dict is an unversioned contract nobody validates, and a sidecar
  makes the filesystem a second metadata store that ADR-006 already declined.
  The honest answer is that the contract has no place for it yet, said out loud
  here rather than worked around in code.
- **Change `CaptureRecord` now to carry context and intent.** Rejected *for this
  phase* only: it is a contract change that deserves its own decision about what
  is durable, what is versioned, and what a connector actually needs. Bundling
  it into the first orchestration would mean designing the durable capture
  metadata model in a hurry, while also proving the ordering semantics above.
- **A clock service, a time provider interface, or freezing the global clock in
  tests.** Rejected: `Callable[[], datetime]` is the entire requirement. A class
  hierarchy adds a type to import and mock for no capability, and patching a
  global clock makes tests order-dependent.
- **An intake error type that wraps every store failure.** Rejected: it would
  destroy the distinction between "the disk failed", "the database failed", and
  "this id is taken", which is the only thing a caller can act on. Intake
  defines errors for its own failures and lets the others through.
