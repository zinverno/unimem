# ADR-006: Capture records are persisted as whole snapshots behind a port

Status: accepted (Phase 0E)

## Context

By the end of Phase 0D the system can accept a capture as a contract, store its
bytes immutably ([ADR-003](ADR-003-content-addressed-raw-storage.md)),
normalize them into a `ContentObject`
([ADR-004](ADR-004-processing-boundary-and-routing.md)), and project that
object outward ([ADR-005](ADR-005-derived-representations.md)). One thing has
never existed: anywhere to put the `CaptureRecord` itself. Phase 0A defined the
lifecycle record and said outright that nothing persists it; Phase 0C's
processors take one as an argument and are handed it by a test.

So the question is narrow and overdue: **how is a valid `CaptureRecord`
durably stored and retrieved without coupling domain code to a database?**

Everything about that question is a trap in a different direction.

Reach for an ORM and the contracts acquire a base class, a metadata registry,
and a mapper; `CaptureRecord` stops being a Pydantic model that happens to be
saved and becomes a row that happens to validate. Reach for a repository with a
`save()` and creating a capture and mutating one become the same call, so a
duplicate id — the one thing a capture store should catch — turns into a silent
overwrite. Key on the raw SHA-256 because it is right there and ADR-003's
layering collapses: two captures of one file become one record. Write the
`JsonRenderer` output into the column and the readable projection quietly
becomes the database format, which is the exact accident invariant 2 exists to
prevent, one layer down from where it was expected.

None of the requirements that would justify the heavy answers exist yet. There
is no query, no listing, no filter, no search, no reporting, no second writer,
no schema history, and no deployment. Choosing a persistence framework now
would be choosing it against imagined requirements.

## Decision

**Persistence lives behind a port.** `CaptureRecordStore` is a `Protocol` in
`core.persistence`. Domain code depends on it and on nothing beneath it: no
contract gains a table, a column, a primary key, a session, an id strategy, or
a database path, and `core.contracts` does not import `core.persistence`.

```python
class CaptureRecordStore(Protocol):
    def create(self, record: CaptureRecord) -> None: ...
    def get(self, capture_id: str) -> CaptureRecord: ...
    def replace(self, record: CaptureRecord) -> None: ...
```

Three synchronous methods. No `save`, `upsert`, `delete`, `list`, `search`,
`filter`, `count`, transaction handle, lifecycle-transition call, or async
surface.

**`create` and `replace` are explicit, and there is no `save`.** "This capture
is new" and "this capture's snapshot has moved on" are different intentions
with different failure modes. `create` fails with
`CaptureRecordAlreadyExistsError` if the id is taken and leaves the stored
snapshot untouched. `replace` fails with `CaptureRecordNotFoundError` if the id
is absent and creates nothing. A single method that picks between them by
looking at whether a row exists silently converts an id-collision bug into a
lost record, and hidden upsert is rejected for that reason alone.

**A record is stored as a whole validated snapshot.** The payload is
`record.model_dump_json()` — the contract's own serialization, the same
mechanism ADR-002 mandates everywhere else — and it is read back with
`CaptureRecord.model_validate_json(...)`. There is no field-by-field mapping to
keep in step with the contract, so a new contract field is persisted without
anyone remembering it, and a removed one leaves nothing behind.

**Persistence is snapshot-based, not live-object tracking.** `create`
serializes immediately, so mutating the caller's object afterwards cannot reach
the database. `get` returns a newly validated object, so mutating *it* changes
nothing until it is passed to `replace`. There is no identity map, dirty
tracking, session, unit of work, or lazy loading — the round trip is a copy in
and a copy out, which is what makes "what is stored is what the caller wrote"
checkable.

**The store stores; it decides nothing.** It never mints an id, sets or
advances `updated_at`, chooses a `status`, or invents an `error`. `updated_at`
in particular is orchestration-owned: a persistence layer that stamps it would
make every stored timestamp a fact about when a write happened rather than
about the capture.

**Persistence holds no lifecycle policy.** Any valid `CaptureRecord` may be
stored in any status, and any valid snapshot may replace any other. Whether
`stored -> processing -> complete` is a legal move, and what may follow
`failed`, is orchestration's decision — and orchestration does not exist yet.
Encoding a guess about it in the storage layer would put the rule in the one
place that cannot see the workflow.

**Capture identity is the record's own `id`, and duplicate detection is that
and only that.** A raw SHA-256 identifies bytes and nothing else (ADR-003). Two
capture records with different ids that reference the same `RawObjectRef` are
two independent records, and nothing here deduplicates by digest, URL, source,
payload, or any combination of them. Submission idempotency, if it is ever
wanted, needs an explicit request identity defined at the capture layer, not an
accident of what a storage backend happened to key on.

**SQLite is the first local durable adapter, not the database choice.**
`SqliteCaptureRecordStore` uses the standard library's `sqlite3` against a file
path. Its whole schema is:

```sql
CREATE TABLE IF NOT EXISTS capture_records (
    id      TEXT PRIMARY KEY,
    payload TEXT NOT NULL
)
```

A key and a payload. No status, timestamp, source, or digest column is
duplicated out of the payload: a duplicated column is a second definition of
the contract to keep in step with the first, and nothing in this phase queries,
sorts, or filters, so nothing would read one. Columns get added when a query
needs them, and they will be derived from the payload rather than written
alongside it by hand.

**Rendering is not the persistence format.** `JsonRenderer` renders a
`ContentObject` for consumers outside the process; it is a representation layer
with its own versioning and its own audience. `core.persistence` does not
import `core.rendering`, and the two are free to change for their own reasons.

**Each operation is one transaction.** `create` is a single `INSERT` and
`replace` a single `UPDATE` on the primary key, each committed through the
connection's context manager, so a failed write rolls back and the previous
valid snapshot stands. Values are always bound as parameters — no id or payload
is ever formatted into SQL text. There is no distributed locking, retry loop,
WAL tuning, connection pool, or cross-process coordination beyond what a SQLite
transaction already gives.

**Connections are per-operation.** Each call opens a connection and closes it
before returning. The store therefore owns no resource a caller has to release,
holds no lock between calls, is not bound to the thread that built it, and two
instances over one file are interchangeable — which is what makes this durable
storage rather than an in-process cache with a file behind it.

**Errors are typed, and no backend leaks.** `CaptureRecordStoreError` is the
base;
`CaptureRecordAlreadyExistsError`, `CaptureRecordNotFoundError`,
`CaptureRecordCorruptError`, and `CaptureRecordPersistenceError` are the four
outcomes a caller acts on differently. `sqlite3` exceptions, `OSError`, and
Pydantic's `ValidationError` do not cross the port, and chaining is preserved
so the original stays reachable for a human. A payload that is not text, is not
JSON, is not a valid record, carries an unsupported `schema_version`, or
embeds an id disagreeing with the key it was filed under is one thing —
corruption — and never a raw `ValidationError`. Classification runs both ways:
a duplicate id is never reported as a generic failure, and a constraint failure
that is not a primary-key conflict is never reported as a duplicate.

**No migration strategy is claimed.** The adapter runs `CREATE TABLE IF NOT
EXISTS` at construction and stops there. There is no migration framework, no
version table, and no upgrade path, because there is no deployed data and no
second schema. `schema_version` stays `0.1`; a payload declaring anything else
is refused rather than reinterpreted.

**Zero new runtime dependencies.** `sqlite3` is in the standard library.
Pydantic remains the only runtime dependency: no SQLAlchemy, Alembic, or
aiosqlite.

## Consequences

Positive:

- **The database is replaceable.** Postgres, a document store, or a remote
  service is a new class satisfying `CaptureRecordStore`; nothing in
  `core.contracts`, `core.processing`, or `core.rendering` changes.
- **Contracts stay contracts.** No base class, mapper, registry, or column
  annotation reaches a domain model, and invariant 9 holds by construction.
- **New contract fields persist for free.** Serialization goes through
  Pydantic, so there is no mapping to update and no field to silently drop.
- **Duplicate ids are caught, not absorbed.** The database enforces one
  snapshot per capture id, and a refused write leaves the stored one intact.
- **Snapshot semantics are simple to reason about.** No object is live, so
  "when does this hit the database?" always has the same answer: on the
  `create` or `replace` you wrote.
- **Testing needs no infrastructure.** A store is a file in a temporary
  directory; the tests run in milliseconds with no service, container, or
  fixture database.
- **Capture identity survives contact with storage.** Two captures of one file
  stay two records, which is the layering ADR-003 established and the place it
  was most likely to be lost.

Costs:

- **No way to find a record you cannot name.** Without listing or query, a
  caller must already hold the id. That is honest for this phase — nothing
  browses captures yet — and the first real query is what should shape the
  query API and any index that serves it.
- **Whole-record reads and writes.** Every `get` parses a full document and
  every `replace` writes one. At this size that is irrelevant; at a size where
  it is not, that is the evidence for columns or a different backend.
- **No concurrent-update protection.** Two callers that `get`, modify, and
  `replace` the same record can lose one of the updates. Each write is atomic,
  but there is no optimistic version check, because there is no concurrent
  writer and a version field is a contract change that should be made with a
  real requirement in hand.
- **SQLite's limits are inherited.** One writer at a time, one filesystem, and
  no network access. Fine for local durability; the reason the port exists is
  that this will not always be enough.
- **A schema change will need a migration story.** `CREATE TABLE IF NOT EXISTS`
  covers exactly one schema. The moment a second exists, this becomes a real
  decision — and this ADR deliberately does not pretend to have made it.
- **Corruption is detected, not repaired.** The store reports an unreadable row
  and stops. Repair, quarantine, and backup are operational concerns with no
  operator yet.

## Alternatives considered

- **A `save(record)` that inserts or updates as needed.** Rejected: it is the
  hidden upsert. The two intentions have opposite failure modes — a duplicate
  id must fail loudly, and a replace of nothing must fail loudly — and one
  method that infers the intent from current database state cannot express
  either. It would make the most damaging bug in a capture pipeline (two
  captures minted with one id) invisible.
- **Keying capture records by the raw SHA-256.** Rejected outright: it is
  ADR-003's layering violation with a database behind it. A digest addresses
  bytes; a capture is an event in a context, and two captures of identical
  bytes legitimately differ in source, URL, time, device, and intent. Keying on
  the digest silently discards one of them and turns raw deduplication into
  capture deduplication.
- **Storing `JsonRenderer` output as the payload.** Rejected: rendering is an
  external representation layer with its own audience and its own version. If
  the database read it back, the renderer could no longer change without a data
  migration, and a projection built for consumers would have quietly become the
  system's storage format — invariant 2's failure mode, one layer down.
- **A file per record on disk** (a JSON file per capture, or a sidecar next to
  the raw object) as the primary metadata store. Rejected: no atomic
  replacement without a rename dance, no uniqueness guarantee, no transaction,
  and the first `get` by anything other than id becomes a directory walk. Raw
  bytes are content-addressed and immutable, so the filesystem suits them;
  capture records are mutable snapshots keyed by an opaque id, which is what a
  database is for.
- **An ORM (SQLAlchemy) with a migration framework (Alembic).** Rejected as
  premature: both earn their keep on relational schemas, joins, and deployed
  data with history, and this phase has one table, no joins, and no deployment.
  The cost now is a mapping layer over contracts that are already validated,
  two dependencies, and a declarative base with opinions about identity and
  sessions — all to persist a document by key.
- **An async store** (`aiosqlite`, or `async def` on the port). Rejected:
  nothing calls this from an event loop, because there is no transport,
  scheduler, or worker yet. An async surface would add an execution model to
  every caller and every test to wrap work that is a single local file write.
- **Exposing transactions or a unit of work.** Rejected: a caller-visible
  transaction is a promise about a backend, and it only pays when several
  writes must commit together. Nothing writes two records at once, and the
  operation that eventually needs it — "store the raw object, record the
  capture, enqueue the work" — spans more than this store.
- **`get` returning `None` for a missing record.** Rejected: it makes the
  common path (`store.get(id).status`) fail with an `AttributeError` several
  frames from the cause, and it gives absence and corruption the same
  vocabulary. A typed error says which happened.
- **Idempotency keys or content hashing to collapse resubmissions.** Rejected
  here: submission idempotency is a capture-intake decision that needs an
  explicit request identity, and inferring it from stored fields would
  reintroduce digest-based capture identity through the back door.
- **Indexed or duplicated columns** (`status`, `received_at`, `sha256`)
  alongside the payload. Rejected for now: they are a second copy of the
  contract with no reader. The first query that needs one is the argument for
  adding it, and then it should be derived from the payload rather than
  maintained by hand.
