# ADR-008: Capture metadata is durable, and the contract set moves to 0.2

Status: accepted (Phase 0G)

## Context

`CaptureEnvelope` has carried capture-time metadata since Phase 0A: a
`CaptureContext` (when it was captured, on what device, in what application), an
optional `CaptureIntent` (what the user asked for — save or analyze, into what
collection, under what tags), and `payload.title`. All of it validated, all of
it modelled.

`CaptureRecord` had nowhere to put any of it, so Phase 0F accepted it and threw
it away. ADR-007 said so out loud, and named it the most pressing gap in the
system: *"Durable capture metadata is a required boundary before any real
connector — a browser extension's whole value is context."* A browser extension
that captures a page and loses the URL's device, the moment, and the user's
collection has captured a blob.

So Phase 0G asks: **how does capture-time metadata survive intake durably,
without being smuggled into raw bytes or unrelated fields?**

The failure modes are the familiar ones and they are all cheap to reach.

Serialize the whole envelope into `CaptureRecord.error` or a free-form
`metadata` dict, and the version-guessing that
[ADR-002](ADR-002-versioned-domain-contracts.md) exists to prevent arrives
through the back door: an untyped bag nothing validates, whose shape is
whatever the last writer happened to put there.

Prepend a header to the raw bytes, or write a JSON sidecar next to them, and
[ADR-003](ADR-003-content-addressed-raw-storage.md) breaks: the digest stops
addressing the content, two identical captures with different devices stop
deduplicating, and every future processor has to know to strip a header it did
not write.

Add a column per field to the SQLite table and
[ADR-006](ADR-006-capture-record-persistence.md)'s reason for storing whole
records evaporates — a second definition of the contract, maintained by hand.

Keep `schema_version` at `0.1` because "it is only an additive change", and
every stored document starts lying about its own shape.

## Decision

**`CaptureRecord` gains three fields, and they are the models the system
already had.**

```python
context: CaptureContext | None = None
intent: CaptureIntent | None = None
title: NonBlankStr | None = None
```

No envelope blob, no generic metadata dict, no duplicated payload object, no
sidecar, no second table. `CaptureContext` and `CaptureIntent` are reused
verbatim — they were designed for exactly these facts, and a second
representation of them would be one to keep in step.

**`context` is required from 0.2; `intent` and `title` are optional.** A
capture that cannot say when it was taken is worth much less later, and
`captured_at` is the one thing every client has. Intent is genuinely optional —
plenty of captures are just "save this" with nothing declared. `title` means
*the title the submitter provided*, never a generated or inferred one; nothing
fabricates it from the id, the text, or the URL, and an absent title stays
absent.

**`received_at` and `context.captured_at` remain different facts.** One is when
the system accepted the capture, the other when the user took it. They are
recorded separately and neither is ever substituted for the other — a
distinction ADR-007 already drew for intake and which now survives into storage.

**Content stays out.** `payload.text`, HTML, and file bytes are not copied into
the record. Content lives in the raw object store and the record reaches it
through `raw_object`. What became durable is metadata *about* the capture, not
the capture itself.

**The canonical contract set advances to `0.2`.**

```python
SCHEMA_VERSION = "0.2"
SchemaVersion = Literal["0.1", "0.2"]
SUPPORTED_SCHEMA_VERSIONS == {"0.1", "0.2"}
```

This is a typed change to a canonical contract, so it gets a version. `0.3` and
every other unrecognized value stay rejected. One version continues to name the
whole contract set: `CaptureEnvelope` and `ContentObject` changed no field and
their version advances anyway, because these contracts are designed, reviewed,
and released together. See the Phase 0G amendment to
[ADR-002](ADR-002-versioned-domain-contracts.md).

**`0.1` documents remain readable — and remain writable as `0.1`.** Two rules
do it, both on `CaptureRecord`:

- *Validation.* A `0.1` record may omit the three new fields, and **may not
  carry them**. A `0.1` document with a `title` is not a lenient old record; it
  is a document wrong about its own shape, and reading it as `0.1` would then
  lose that data on the way back out. Conversely a `0.2` record must carry
  `context`.
- *Serialization.* A `0.1` record is emitted **without the three keys at all** —
  not as nulls. Every contract sets `extra="forbid"`, so a reader built against
  `0.1` rejects an unknown key rather than ignoring it, and `"context": null`
  would be as fatal to it as a populated one. A `0.1` record therefore goes back
  out shaped exactly as it came in.

That second rule is the part that is easy to skip and expensive to omit.
Reading an old document is worth little if writing it back breaks the build
that wrote it. It is implemented as a version-aware wrap serializer on the one
model that changed — roughly ten lines — not as a migration framework.

**No database migration is needed.** ADR-006 stores whole `CaptureRecord` JSON
in `capture_records (id TEXT PRIMARY KEY, payload TEXT NOT NULL)`. A contract
that grows fields grows its payload; the table does not change, no column is
added, and no DDL runs. This is the schema decision paying for itself the first
time it was tested. `CaptureRecordStore` is untouched — `create`, `get`,
`replace`, and the same typed errors — and an unsupported version in a stored
payload is still `CaptureRecordCorruptError` at the boundary.

**Intake writes the metadata into the `RECEIVED` snapshot, before raw storage.**
Not into `STORED` afterwards. ADR-007 ordered the receipt first so a failure
mid-flight leaves a truthful, recoverable record; a receipt that knows nothing
about the capture would waste most of that. So a capture stranded by a raw-store
or `replace` failure still knows when, where, and why it was taken.

Both snapshots get their **own deep copies** of `context` and `intent`. A
`CaptureIntent` holds a mutable `tags` list, and in-place mutation of a list
never reaches a validator (the Phase 0A mutation semantics), so an alias shared
between the envelope and a record — or between the two snapshots — would be a
way to change a stored capture by appending to a list somebody else still holds.
Snapshots are rebuilt and revalidated, never mutated in place or produced by an
unchecked `model_copy(update=...)`.

**A legacy `0.1` envelope still produces a current `0.2` record**, carrying its
context, intent, and title. The envelope is not rewritten; it stays the `0.1`
document it was.

**Nothing propagates into `ContentObject` yet.** `title` now exists on a capture
record, and `TextProcessor` still does not read it. Whether a processor should
seed `ContentObject.title` from the capture — and what happens when they
disagree, or when an extractor finds a better title in the content — is a
processing decision with its own trade-offs. It is not made here just because
the field became reachable.

**Zero new runtime dependencies.**

## Consequences

Positive:

- **A capture is finally a capture.** Device, application, time, intent,
  collection, tags, and title survive, which is the difference between a
  capture system and a bytes-with-ids system.
- **The metadata is durable at the earliest possible moment.** It is in the
  first record ever written, so no failure mode loses it that does not also lose
  the record.
- **Old data stays valid in both directions.** `0.1` documents load, and what
  this build writes back is still a `0.1` document an older reader accepts.
- **The versioning decision proved it works.** ADR-002 was written for this
  moment; exercising it required widening a `Literal` and adding two rules to
  one model.
- **The persistence decision proved it works too.** Whole-record JSON absorbed
  a contract change with no DDL, no migration, and no downtime story.
- **The fields are validated, not a bag.** Aware datetimes, a closed
  `IntentAction` enum, and non-blank strings — the same guarantees as everything
  else in the domain.

Costs:

- **`context` is now required at 0.2.** Every producer of a `CaptureRecord` must
  supply one. That is the intent, and it did mean touching the test builders and
  the processing and rendering fixtures — exactly the friction ADR-002 predicted
  when it said "adding a version means touching every producer; that is the
  point".
- **Two representations of a version now coexist in one type.** `CaptureRecord`
  carries fields that mean different things depending on `schema_version`, and
  every future version-conditional rule lands on the same model. One more of
  these and it will be worth asking whether a versioned reader/writer pair beats
  conditional rules on a single class.
- **Version-aware serialization is easy to forget for the next change.** The
  rule "a `0.1` document must not gain `0.2` keys" is enforced by tests, not by
  the type system, and a third version will need the same care.
- **A `0.1` record can never be enriched in place.** Adding context to one means
  writing a `0.2` record, which is a real decision about fabricating history —
  and the answer here is that nothing fabricates it.
- **The metadata is only as good as the client.** `captured_at` comes from the
  submitter's clock and may be wrong or hostile. `received_at` remains the
  system's own fact, which is exactly why both are kept.

## Alternatives considered

- **Keep `schema_version` at `0.1`.** Rejected: a typed field added to a
  canonical contract *is* a schema change, and a document whose version does not
  describe its own shape is precisely what ADR-002 exists to prevent. The
  version field is worth nothing if it does not move when the schema does.
- **Drop `0.1` compatibility entirely** and require every stored record to be
  `0.2`. Rejected: captured data is expected to outlive the code that wrote it,
  and there is no migration story yet. Refusing to read yesterday's records to
  save ten lines of serializer is the wrong trade.
- **Emit `"context": null` in `0.1` documents** rather than omitting the keys.
  Rejected, and it is the subtle one: every contract sets `extra="forbid"`, so a
  reader built against `0.1` rejects the document outright. "Readable by an old
  build" has to mean the whole round trip, not just our own parser.
- **Store the entire `CaptureEnvelope` inside `CaptureRecord`.** Rejected: it
  would drag `payload` — text, HTML, `file_ref` — into the record, duplicating
  content that belongs in the raw store and making the record grow without
  bound. The record holds facts *about* the capture; the envelope is a request.
- **Put the metadata in the raw bytes** (a header, a front-matter block, a
  wrapping JSON envelope). Rejected: it destroys content addressing. Two
  identical captures from different devices would produce different digests and
  stop deduplicating, and every future processor would have to strip a header to
  find the content it was asked to normalize. ADR-003's invariant is that the
  digest addresses the content, exactly.
- **Put it in `error`, in `source`, or in a free-form `metadata` dict.**
  Rejected: `error` is for errors; `source` describes where a capture came from,
  not when or why; and an untyped bag is an unversioned contract that nothing
  validates and everything eventually depends on. ADR-007 refused to smuggle
  this metadata anywhere, and the answer was always going to be real fields.
- **A JSON sidecar per record on the filesystem.** Rejected for the reasons
  ADR-006 already gave: no atomicity, no uniqueness, no transaction, and a
  second metadata store to keep consistent with the first.
- **Add SQLite columns for `captured_at`, `device`, `title`, and the rest.**
  Rejected: no query needs them yet, and a duplicated column is a second
  definition of the contract maintained by hand. When a query does need one, it
  should be derived from the payload. ADR-006 made this call; nothing here
  changes it.
- **Fabricate a `context` for legacy `0.1` records** so everything could be
  `0.2`. Rejected outright: there is no honest value. `received_at` is not
  `captured_at`, and writing it there would manufacture a fact about the user's
  past. A record that does not know when it was captured must keep saying so.
- **Introduce a migration framework** now that a second version exists.
  Rejected: nothing is being migrated. A `0.1` record stays a `0.1` record, and
  the whole compatibility story is two validation rules and one serializer. A
  framework belongs to the first *structural* database migration, which this is
  not.
- **A version per contract** (`CaptureRecord` at `0.2`, the others at `0.1`).
  Rejected again, as in ADR-002: it turns "which versions work together" into a
  matrix that grows with every release, and buys only the cosmetic saving of not
  bumping two unchanged models.
- **Propagate `title` into `ContentObject` in this phase.** Rejected as a
  separate decision: it needs answers about precedence between a submitted title
  and an extracted one, and about what a processor does when they conflict.
  Making it a side effect of "the field now exists" is how undesigned behaviour
  gets in.
