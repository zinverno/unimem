# ADR-013: Completed-capture replay, so a lost response can be resent

Status: accepted (Phase 1, PR 3). Phase 0 remains
[closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, lifecycle, or persistence change: `src/core/` is untouched, and the
schema stays `0.2`.

## Context

[ADR-011](ADR-011-local-http-capture-surface.md) built the HTTP surface with no
idempotency at all and said why: idempotency needs a real requirement to design
against, and `curl` had not produced one. [ADR-012](ADR-012-browser-selection-connector.md)
built the connector that produces it, and hit the wall from the other side —
it wrote down "the client cannot tell those apart from the outside" and shipped
a read-only probe as the only honest thing left to do.

This is that requirement, and it is one concrete question:

> A client mints a capture id, POSTs the envelope, and never sees the response.
> How can it resend the **same** capture — same id, same request — without
> creating a second capture, and without the server mistaking a *different*
> request for the original one?

Every part of that is real. The id exists before the request, because the
connector mints it at the click. The response can be lost, because a laptop
sleeps and a loopback connection still drops. And the client genuinely holds the
identical envelope, because one user action builds exactly one.

Today it cannot resend. A same-id POST is a flat `409` whether the first attempt
completed or never arrived, and a fresh-id POST is a duplicate capture the user
never asked for. So the connector probes with a `GET`, learns whether *something*
is there, and cannot ask the only question it actually has: **is this the capture
I sent?**

The traps around this are well populated, and most of them are much larger than
the problem.

**Add an `Idempotency-Key` header.** It is the industry-standard answer, and it
is the answer to a different question. It exists for clients that do *not* name
their resource — the key is a separate identity minted only so a retry can be
recognized. This client already mints one opaque id per capture, before the
request, and the server already stores it under a primary key. A second identity
would be a second thing to keep, expire, and reconcile, describing the identity
we already have.

**Add an idempotency table** — a nonce store, a request-fingerprint store, Redis,
a cache of responses by key. That is a persistent subsystem with its own
lifetime, eviction policy, and failure modes, added in a phase whose entire
storage story is two SQLite tables and a directory of bytes.

**Treat any duplicate id as success.** It is one line, and it is the bug: an id
is a claim the client makes, not evidence about content. A colliding, reused, or
guessed id would be answered with somebody else's capture.

**Compare the request to the record loosely** — trim the text, normalize
Unicode, ignore the title, "timestamps are close enough". Each is a small
kindness that makes the server report a capture as saved that is not the capture
the user took.

**Reprocess, resume, or reconcile an incomplete duplicate.** A capture stuck in
`processing` looks exactly like something a retry should fix. It is the start of
a reconciliation subsystem, and it needs a real design against real evidence
about how captures actually get stranded — which we do not have.

**Make it a concurrency guarantee.** "Two requests with the same id converge on
one response" is the natural generalization, and it needs locks, leases, or
waiting inside the server. That is a distributed-systems feature, not a fix for
a dropped reply.

## Decision

**The client-generated `CaptureEnvelope.id` is the replay identity.** There is no
`Idempotency-Key` header, no request token, no nonce, no fingerprint table, no
second database, no Redis, no new `CaptureRecord` or `ContentObject` field, and
no schema `0.3`. A replay is literally a resubmission of the id the client
already minted, and the `CaptureRecord` primary key remains the only authority on
who won creation.

**The first submission is unchanged.** `POST /v1/captures` still runs intake and
the orchestrator synchronously and still answers `201` with
`{capture_id, content_id, status: "complete"}` once both are durable. Phase 0
lifecycle semantics are untouched.

**The status code carries the whole distinction.**

| Code | Meaning |
| --- | --- |
| `201` | this HTTP attempt created and completed the capture |
| `200` | this was an equivalent replay of an already-completed capture |
| `409` | everything else a duplicate id can be |

The body is byte-identical in the two success cases, deliberately. No `replayed`,
`duplicate`, or `idempotent` field was added: the response already has a field
for "what happened to this request", and it is the status line.

**Replay is resolved only after intake refuses a duplicate.** The ordinary path
does no preflight read, so no check-then-create window is opened and the normal
POST costs exactly what it did before:

```python
try:
    intake.accept(envelope)
except CaptureRecordAlreadyExistsError:
    replay = resolve_completed_replay(...)
    if replay is None:
        raise
    return 200, replay
content = orchestrator.process(envelope.id)
return 201, ...
```

**The policy lives in the delivery layer,** in `src/unimem_api/replay.py`, and
not in `core`. Intake's contract is unchanged — a duplicate id is still an error
— and replay is one read-only question asked afterwards by the code that owns
HTTP semantics. The helper performs **zero writes**.

**A duplicate is replayable only if all four hold**, checked in this order:

1. the existing `CaptureRecord` loads;
2. its status is `COMPLETE`;
3. the incoming request is demonstrably equivalent to it;
4. its canonical `ContentObject` loads.

The order is part of the contract. A capture that is still `PROCESSING` is
answered without the content store being consulted at all, so a duplicate
arriving mid-run can never be mistaken for a capture whose content has gone
missing.

**Equivalence is proven from durable facts, for the one materialization this
build supports** — an inline `TEXT` payload. Every observable semantic fact must
match: the capture id, the schema version, `source`, `context`, `intent`, the
submitted `title`, and the `mime_type` the raw object was stored under.
`source`, `context`, and `intent` are compared as the validated domain models
they already are, so the comparison is the contract's own notion of equality
rather than a hand-maintained field list that could fall behind it.

**The exact submitted bytes are verified by SHA-256.** The incoming
`payload.text.encode("utf-8")` — the very transformation intake applies — is
hashed and compared to the raw object's stored digest. Nothing is trimmed,
case-folded, Unicode-normalized, BOM-stripped, or line-ending rewritten first, so
a leading space, a `\r\n`, a combining mark, and a byte order mark each make two
texts different, exactly as the pipeline already treats them.

**`captured_at` is compared as an instant, not as a string.** The same moment
written in two UTC offsets is the same capture-time fact, and the contract's own
datetime equality says so. Building replay identity around JSON formatting would
refuse a client that serialized its own timestamp differently on the resend — a
formatting difference, not a capture difference.

**Server-generated lifecycle facts are not request identity.** `received_at`,
`updated_at`, and processing timestamps describe what the server did, and a
client resending an identical request has no way to reproduce them. They are not
compared.

**Payload fields the durable record cannot represent refuse replay.** A `TEXT`
payload may legally carry `html` or `file_ref`, and a text `CaptureRecord`
stores neither. If either is populated, the server cannot prove the original
request carried it too — so it must not report a replay. Unprovable is not
equivalent, and the digest speaks for the text and for nothing else.

**Same id plus any difference remains `409 capture_already_exists`.** Different
selected text, source URL, provider, source type, title, `captured_at`, device,
intent, tags, tag order, MIME type, an added `html` or `file_ref`, a different
schema version — all conflicts. The incoming request is not processed, the
existing content is not returned, and no second capture id is minted on the
client's behalf.

**Any non-`COMPLETE` duplicate remains `409`.** `RECEIVED`, `STORED`, `QUEUED`,
`PROCESSING`, `PARTIAL`, `FAILED` — none is a completed replay. Nothing is
resumed, retried, marked complete, polled, or reconciled, and
`GET /v1/captures/{id}` stays how a client observes lifecycle. **This PR does not
solve stranded captures.**

**Legacy `0.1` duplicates are not made replayable.** Intake writes every record
at the current schema version, so a `0.1` submission produces a `0.2` record that
cannot vouch for a `0.1` request's metadata. Rather than build migration
machinery to bridge that, such a duplicate keeps the `409` it would have received
anyway — not a regression, because a duplicate was always a conflict.

**`COMPLETE` with no content is a server integrity failure.**
[ADR-010](ADR-010-canonical-content-persistence.md) established that `COMPLETE`
implies durable canonical content, because the orchestrator writes the content
first and the snapshot second. So an equivalent replay of a complete capture with
nothing to return is the server contradicting itself — not a replay success, not
a `404`, and not a duplicate `409`. A delivery-layer `CaptureReplayIntegrityError`
maps to `500 data_integrity_error` with the same fixed public message every other
unreadable-stored-data failure carries. It is declared where it is raised rather
than smuggled in as a core persistence error for the sake of a convenient status
code. `ContentObjectCorruptError` keeps its existing mapping. No path, id, store
name, or exception text reaches the wire.

**No concurrency claim is made.** If request A is still `PROCESSING` when an
identical request B arrives, B sees a non-`COMPLETE` record and gets `409`. That
is acceptable and documented. There are no locks, leases, condition variables,
waiting on another request, or server-side polling. The guarantee is exactly:

> once a capture is durably `COMPLETE`, an equivalent resubmission can be
> answered with its existing result.

It is **not**: "all concurrent duplicate requests wait and converge onto one
response."

**No new route.** No `POST /retry`, no `POST /replay`, no `PUT /captures`, no
idempotency endpoint. The OpenAPI document declares `200` alongside `201` on the
existing POST, with the same response model, so a generated client does not treat
a perfectly good replay as an undocumented status.

### What the browser connector does with it

**One bounded resend, and only after a network-layer failure.** The first POST is
exactly as before. If it produces an HTTP response — success or an explicit
`4xx`/`5xx` — that is the answer, and it is never retried: the server has judged
the request, and resending would ask the same question.

If the first POST fails at the network layer, the connector sends **the same
envelope, with the same id, to the same endpoint, exactly once more.** It does
not call `crypto.randomUUID()` again, does not restamp `captured_at`, and does
not re-read the selection, the title, or the URL. One user action is one
`CaptureEnvelope` is one logical capture. The resend exists *only* because the
server now answers it.

```
POST ──► an HTTP response ──────────────────► that is the answer
  │
  └─ no response at all
         │
       POST (same envelope, same id)
         ├─ 201 ─► created: the first request never landed
         ├─ 200 ─► replayed: the first request had completed
         ├─ 409 capture_already_exists ─► one GET, and report what is there
         ├─ another HTTP error ─────────► that is the answer
         └─ no response again ──────────► one GET, and report what is there
```

The `409` after the resend is the one conflict worth looking into: the id is
taken, almost certainly by this client's own first request, but the server could
not answer it as a completed replay — so the capture may still be processing, or
may have failed. That is worth one read-only `GET`, which reports whatever
durable state it finds and never turns a conflict into a success.

**The hard bound for one user action is two POSTs and one GET.** Never more. No
loop, no backoff, no timers, no retry library, and no queue.

**Both success codes are validated identically** before being believed: the
`capture_id` must be the one submitted, `content_id` must be a non-blank string,
and `status` must be `complete`. No other 2xx is success — a `202` and a `204`
are protocol failures, because "accepted" and "nothing" are not "durable". The
result records `confirmedBy` as `post`, `replay`, or `probe`, which is how the
connector distinguishes the three without the response carrying a field that
restates its own status code.

**Both successes show `OK`.** `post` reads `UniMem: saved`, `replay` reads
`UniMem: saved (confirmed retry)`, and a `GET`-confirmed capture keeps
`UniMem: saved (confirmed after a network error)`. No new badge. The selected
text, capture ids, content ids, and raw error messages appear in none of them.

**No persistent retry state.** No `chrome.storage`, no queue, no local database.
If the service worker dies between the two attempts there is no durable retry in
this PR, and that is acceptable: the server is authoritative, and a replay is a
property of the durable capture rather than of a live process — a fresh
application over the same data directory answers a resend the same way.

**Manifest permissions are unchanged**: `activeTab` and `scripting`, host
`http://127.0.0.1/*`. No storage permission, no notifications, no new host
access, and no CORS.

## Consequences

A client that loses a response can now ask the only question it has, and get an
answer it can act on. The connector's ambiguous path shrinks from "we went and
looked, and something is there" to "we resent it and the server handed back the
capture we made" — with one capture, one content object, and one raw object on
disk, which is what the acceptance test counts.

The cost is a policy that must be kept honest. Every fact the durable record
carries has to be compared, and every fact it does not carry has to refuse
replay. That bias is deliberate: a false negative costs a client one spurious
`409` on a duplicate that was a conflict before this phase existed, while a false
positive reports a capture as saved that is not the capture the user took. When
`WEBPAGE` or `DOCUMENT` materialization arrives, equivalence has to be defined
for it explicitly — the predicate refuses anything it was not written for, so the
failure mode of forgetting is a conflict rather than a wrong success.

`409` now means strictly less than it did: it no longer covers the identical
resend of a completed capture. Nothing that was a conflict for a *substantive*
reason changed, and the tests that previously asserted "an identical duplicate
conflicts" were rewritten to assert it of duplicates that genuinely differ.

Stranded captures are still stranded. A duplicate of a `PROCESSING` or `FAILED`
capture gets a conflict and a client that wants to know more calls `GET`. That is
the same answer ADR-011 gave, and reconciliation still needs its own evidence and
its own decision.

## Alternatives rejected

- **An `Idempotency-Key` header.** Rejected: it mints a second identity for a
  resource the client already names, and the whole mechanism exists for clients
  that cannot.
- **A separate idempotency database, table, nonce store, or Redis.** Rejected: a
  persistent subsystem with its own lifetime and eviction policy, to record what
  the capture record's primary key already records.
- **Persisting a request fingerprint on the `CaptureRecord`.** Rejected: a schema
  change (`0.3`) and a stored derivative of the request, when the raw object's
  SHA-256 already proves the bytes and the record already carries the metadata.
- **Treating any duplicate id as success.** Rejected: an id is a claim, not
  evidence. A colliding or reused id would be answered with another capture's
  content — worse than any spurious conflict.
- **Minting a fresh capture id on the client's behalf when one is taken.**
  Rejected: it silently creates the duplicate capture the user did not ask for,
  which is the exact failure this design exists to prevent.
- **Loose equivalence** — trimming text, normalizing Unicode, ignoring the title,
  tolerating near timestamps. Rejected: each makes the server report a capture as
  saved that is not the one that was taken.
- **Comparing the serialized JSON instead of the models.** Rejected: it would
  make key order and timestamp formatting part of capture identity, refusing
  clients that are resending exactly the right thing.
- **Replaying a duplicate whose payload carries `html` or `file_ref`.** Rejected:
  the durable text record does not represent them, so equivalence cannot be
  proven and must not be assumed.
- **Reprocessing, resuming, or reconciling an incomplete duplicate.** Rejected:
  that is a reconciliation subsystem, and it needs real evidence about how
  captures get stranded before it gets a design.
- **Waiting for a concurrent request to finish, with a lock, lease, or poll.**
  Rejected: a distributed-systems feature for a dropped reply, and this PR makes
  no concurrency claim.
- **A new `replayed` or `duplicate` response field.** Rejected: `201` versus
  `200` already says it, and a field restating the status line is a second thing
  to keep true.
- **A new route** — `POST /retry`, `POST /replay`, `PUT /captures`, an
  idempotency endpoint. Rejected: the question is about one POST, and it is
  answered where that POST is.
- **Migration machinery so `0.1` duplicates can replay.** Rejected: version
  translation for a case that was a conflict before and stays one.
- **Unlimited, looping, or exponential-backoff retries in the connector.**
  Rejected: there is no general retry policy here. One ambiguous network failure
  buys one repeat of the identical request, and nothing else does.
- **A persistent client-side retry queue in `chrome.storage`.** Rejected for
  ADR-012's reason: it makes the extension a second, weaker record of what the
  server holds authoritatively — and it would need a storage permission this
  connector does not ask for.
- **Playwright, to drive a real browser for the acceptance test.** Rejected
  again: the connector's own JavaScript is run against a real server on a real
  socket from the integration suite, with the first response genuinely thrown
  away after the server processed it. That proves the requirement without a
  browser automation dependency.
