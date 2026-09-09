# ADR-010: Canonical content is durable before a capture is complete

Status: accepted (Phase 0I). Closes the Phase-0 foundation.

## Context

[ADR-009](ADR-009-processing-orchestration.md) built the lifecycle
orchestrator and had to name a limitation in the same breath: `COMPLETE` meant
normalization had succeeded and the *capture record* said so, but the
`ContentObject` itself was returned to the caller and stored nowhere. A crash
between the two lost the normalized representation, and the starting-state rule
refused to reprocess, so such a capture sat at `complete` with nothing to show.

That ADR declined to fix it in place, on the grounds that bolting on a store to
make one sentence sound better is how a boundary gets designed in a hurry. This
is that boundary, designed on purpose, and it asks one question: **how does a
successfully normalized canonical `ContentObject` become durable before a
capture is allowed to become `COMPLETE`?**

The traps are the same shape as every previous persistence question, plus one
new one.

Persist the `JsonRenderer` output, because it is already a full-fidelity
projection and it is right there. Then a renderer can no longer change without
a data migration, and a derived representation has quietly become the system's
storage format — invariant 2's failure mode, three layers from where it was
expected.

Put the content object inside `CaptureRecord`, because the capture already has
a home. Then the lifecycle record grows without bound, a snapshot that is
`replace`d on every status change carries the normalization result, and every
capture read drags a content object along with it.

Add a `content_object_id` column to `CaptureRecord` so content can be found.
That is a contract change, schema `0.3`, and every producer touched — to record
a pointer the content object itself can already provide.

Mark `COMPLETE` first and store content after, because the lifecycle write is
the cheaper call. Then `complete` means exactly what it meant before: nothing
about durability.

And the new one: with two stores and no shared transaction, one of the two
writes must go first, and whichever goes second can fail. That is not avoidable
here — it is a question of which inconsistent state is the *useful* one.

## Decision

**A second persistence port, sibling to the first.**

```python
class ContentObjectStore(Protocol):
    def create(self, content: ContentObject) -> None: ...
    def get(self, content_id: str) -> ContentObject: ...
    def get_for_capture(self, capture_id: str) -> ContentObject: ...
```

No `replace`, `save`, `upsert`, `delete`, `list`, `search`, pagination, query
DSL, or caller-visible transaction. Two lookups, because there are two
questions worth asking: "give me this object" and "what did this capture
normalize into?".

**There is no `replace`, deliberately.** The system produces exactly one
canonical normalization result per capture and has no reprocessing, so an
operation that overwrote canonical content could only ever be an accident.
*Superseding* canonical content is a real decision about identity, history, and
what already read the old object; it gets made when reprocessing does.

**Whole-contract JSON, not a rendering.** `content.model_dump_json()` in,
`ContentObject.model_validate_json(...)` out — the same mechanism ADR-006 chose
for capture records and ADR-002 mandates everywhere. `JsonRenderer` is **not**
used: rendering is an external representation layer with its own audience and
its own version, and letting the database read it back would tie the renderer
to a data migration. `core.persistence` does not import `core.rendering`.

**Identity stays layered.** `ContentObject.id` is canonical object identity;
`source.capture_id` is the association to the capture event; a raw SHA-256
identifies bytes and nothing else. No content id is derived from a capture id,
a digest, a title, or the text. Two captures of identical bytes produce two
content objects, and the store proves it.

**One canonical object per capture, enforced by the database.**

```sql
CREATE TABLE IF NOT EXISTS content_objects (
    id         TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE,
    payload    TEXT NOT NULL
)
```

Two key columns, each buying something the payload alone cannot: `id` is how an
object is addressed, and `capture_id` — being `UNIQUE` — makes "one canonical
object per capture" a rule the database enforces rather than a convention
orchestration hopes for. Nothing else is lifted out of the payload.

This is also what makes a `content_object_id` column on `CaptureRecord`
unnecessary, and therefore makes schema `0.3` unnecessary. **`SCHEMA_VERSION`
stays `0.2`** and no canonical contract changes.

**`SqliteContentObjectStore`**, stdlib `sqlite3`, mirroring the capture record
adapter exactly: table created at construction, parent directories not
fabricated, one connection per operation, one transaction per operation, no WAL
tuning, retry, pooling, or ORM. It may share a database file with
`SqliteCaptureRecordStore` — which leaves `capture_records` untouched — but
sharing a file is a deployment convenience, never a shared transaction.

**A sibling error hierarchy**, `ContentObjectStoreError` with
`ContentObjectAlreadyExistsError`, `ContentObjectNotFoundError`,
`ContentObjectCorruptError`, and `ContentObjectPersistenceError`. Not
subclasses of `CaptureRecordStoreError`, and emphatically not of
`ProcessingError`: failing to *store* canonical content says nothing about
whether the capture could be normalized, and in the orchestrator the two lead
to different outcomes.

Constraint classification is exact: a primary-key conflict means the content id
is taken, the one `UNIQUE` column means the capture already has content, and
each is reported with its own message. **Any other constraint failure is a
backend failure**, not a duplicate.

Corruption covers a non-text payload, malformed JSON, an invalid content
object, an unsupported `schema_version`, and — checked on **both** lookups —
an embedded `id` or `source.capture_id` that disagrees with the columns the row
was filed under. Without the second check, `get_for_capture` could answer with
content belonging to someone else.

**Orchestration stores content before it writes `COMPLETE`.**

```
processor.process -> validate capture association
                  -> ContentObjectStore.create
                  -> [completion clock]
                  -> CaptureRecord(COMPLETE)
                  -> return the ContentObject
```

The completion clock is not read until the content write has succeeded, so a
completion timestamp always describes a capture whose content exists.

**From Phase 0I onward, `COMPLETE` means:** normalization succeeded, the
canonical content object is durable, and the completion snapshot is durable. It
still does not mean derived Markdown, embeddings, indexes, or any downstream
projection is durable — those are derived from the object and reproducible from
it.

**A content-store failure leaves the capture `processing`.** The error
propagates unchanged; nothing is marked `failed`, nothing rolls back to
`stored`, the completion clock is not read, and no content is returned. Not
being able to *store* a result is a statement about the run, exactly like a
database timeout — it is no more a verdict on the captured bytes than
ADR-007 and ADR-009 allowed such failures to be.

**A duplicate for the capture is not idempotent success.** If `create` reports
that the capture already has canonical content, that propagates too: nothing
loads and returns the existing object, and nothing marks the capture complete.
Such a duplicate means something believes this capture needs normalizing again
— a concurrent worker, a retry, drifted lifecycle state — and which of those it
is cannot be decided here.

**The new cross-store gap, stated rather than papered over.** Content can be
durable while the capture record still says `processing`, if the `COMPLETE`
write fails after the content write succeeded. In that case the persistence
error propagates, the content object is **not** deleted or overwritten, the
capture is **not** rolled back, and no success is returned.
`get_for_capture(capture_id)` is what makes the state findable, deliberately:
Phase 0I creates the observability, and reconciliation is a later decision.

**No cross-store transaction, and no unit of work.** Even where both adapters
point at one physical file, orchestration must not reach into a backend to open
a transaction spanning both. That would couple domain orchestration to one
adapter and make the ports un-swappable. No connection is exposed.

**Nothing else changes.** `TextProcessor` stays `text`/`0.2` with the same
title semantics. Renderers are untouched, and no rendered output is persisted.
`ContentObject`'s structural fields did not change between `0.1` and `0.2`, so
a valid `0.1` object stores and loads unchanged; new output is `0.2`. Zero new
runtime dependencies.

## Consequences

Positive:

- **`COMPLETE` finally means what the word suggests.** The one limitation
  ADR-009 could not close is closed, and closed in the ordering that makes the
  claim true rather than convenient.
- **The database enforces the invariant that matters.** "One canonical object
  per capture" is a constraint, not a convention, so no orchestration bug can
  produce two.
- **No contract change was needed.** Keying content by `capture_id` instead of
  pointing at it from `CaptureRecord` avoided schema `0.3` entirely — the
  cheapest possible version of this feature.
- **The bad state is observable.** Content durable with a `processing` capture
  is exactly the kind of inconsistency that is invisible in most systems;
  `get_for_capture` makes it a query.
- **The two ports stay swappable.** Neither store knows the other exists, and a
  future Postgres content store is a class, not a migration of orchestration.
- **Whole-contract JSON absorbed another feature.** Both stores now demonstrate
  that a contract that grows fields grows its payload, with no DDL.

Costs:

- **A capture can have content it does not admit to.** The `COMPLETE`-write
  failure leaves durable content behind a `processing` record. Nothing
  reconciles it, so until a reconciliation pass exists it needs a human.
- **A duplicate content error has no automatic answer.** A caller that hits it
  learns something is wrong and must decide what; the store cannot say whether
  it was a race or a stale retry.
- **Canonical content cannot be corrected.** With no `replace`, a content
  object written from a buggy processor version is durable and immovable until
  reprocessing and supersession are designed.
- **`UNIQUE(capture_id)` is integrity, not mutual exclusion.** It guarantees
  two canonical objects cannot both become durable for one capture. It does
  **not** stop two workers from both processing, both burning the work, or
  racing on lifecycle writes — the loser simply fails at the content write.
  ADR-009's concurrency limitation stands unchanged.
- **One more table to keep in step at deployment.** Two adapters, two
  `CREATE TABLE IF NOT EXISTS` calls, and no migration story for either.

## Alternatives considered

- **Persist `JsonRenderer` output instead of contract JSON.** Rejected: it ties
  the renderer's version to stored data, so a rendering change becomes a
  migration, and it makes a derived representation the system's source of
  truth. ADR-005 and ADR-006 both drew this line; this is the third place it
  could have been crossed.
- **Store the `ContentObject` inside `CaptureRecord`.** Rejected: the capture
  record is a lifecycle snapshot replaced on every status change, and embedding
  the normalization result would rewrite the whole content object on every
  transition, drag it into every capture read, and grow a record that ADR-006
  deliberately kept small.
- **Add `content_object_id` to `CaptureRecord`** (and with it schema `0.3`)
  just to locate content. Rejected: a contract change, a version bump, and
  every producer touched — to record a pointer the content object already
  carries in reverse. `UNIQUE(capture_id)` gives the same lookup for one column
  in a table that had to exist anyway.
- **Mark `COMPLETE` before storing content.** Rejected: it is the whole
  decision inverted. `complete` would keep meaning what ADR-009 had to admit it
  meant, and the failure window would move to the side where nothing is
  recoverable.
- **Silently upsert canonical content**, or let `create` replace an existing
  object. Rejected for ADR-006's reason one layer up: a hidden upsert turns the
  one thing this store should catch — two normalizations for one capture — into
  a silent overwrite of the first.
- **Treat a duplicate capture content as idempotent success**, loading and
  returning the existing object and marking the capture complete. Rejected: it
  guesses. A duplicate can mean a concurrent worker mid-flight, a retry after a
  timeout, or a lifecycle that drifted, and completing the capture on the
  strength of content somebody else wrote assumes the benign case.
- **Delete the content object when the `COMPLETE` write fails.** Rejected: it
  would trade the one durable artifact of a successful run for the *appearance*
  of consistency that still would not hold — the delete can fail too. The
  content is correct; only the record is behind.
- **Expose a SQLite transaction through the ports, or add a `UnitOfWork`.**
  Rejected: it couples orchestration to one adapter's backend and only works
  while both stores happen to share a file. The moment content lives elsewhere
  the abstraction is a lie, and the code that depended on it is the code that
  breaks.
- **Solve reprocessing, supersession, or concurrency now.** Rejected: each is a
  real design with its own questions (what happens to readers of the old
  object? how is a dead worker detected?), and none of them blocks closing the
  foundation.

## Phase 0 is closed

This is the last foundation microphase. The system can now accept a capture,
store its bytes immutably, register it durably with its metadata, normalize it,
store the canonical result, and record the whole lifecycle truthfully — with
every boundary behind a port and every failure mode named.

The concerns left open — reconciliation, retries, same-capture worker
ownership, reprocessing and supersession, query and search, connectors — are
**not** blockers for that. Each of them needs a real requirement to be designed
against, and inventing one now would be building the next layer of foundation
for a product that has not asked for it. They become concrete work when a
vertical product phase requires them.
