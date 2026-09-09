# ADR-011: A local HTTP capture surface, as an adapter around the canonical envelope

Status: accepted (Phase 1, PR 1). Opens **Macro Phase 1 — Capture Surface**.
[Phase 0 remains closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed);
nothing in it is reopened, revised, or renegotiated here.

## Context

Phase 0 ended with a system that can do the whole job and has no way to be
asked. Every boundary is behind a port, every failure mode is named, and a text
capture survives a restart — but the only caller that has ever existed is a
test. The foundation was deliberately closed with connectors listed as work that
needed a real requirement to be designed against.

Phase 1 supplies the requirement: **something outside this process has to be able
to hand UniMem a capture.** A browser extension will, an Obsidian connector will,
and a shell script should be able to today. They differ in everything except the
one thing that matters — they all have a capture and want it stored, normalized,
and retrievable.

So the question this ADR answers is narrow: *how does a capture reach the core
over a network protocol, and what does the caller learn?* Not how it is queued,
retried, authenticated, deduplicated, or reconciled — each of those is a real
design that a real connector has still not asked for.

The traps are the familiar ones, plus two that only appear once a delivery
protocol exists.

**Put the framework in `core`.** A route decorator on an orchestration method is
two lines and saves a package. Then the kernel imports FastAPI, `core` cannot be
used without a web framework, and invariant 9 — the contracts depend on no HTTP
framework — is broken from the inside by the one layer that was supposed to
protect it.

**Write an HTTP request model.** Every framework tutorial does: a
`CaptureEnvelopeRequest` with the same fields, "just for the API". Then there
are two definitions of what a capture is, kept in step by hand, and the first
field that drifts drifts silently. `CaptureEnvelope` is *already* the ingress
contract — that is what it was built to be.

**Return `201` when intake succeeds**, because that is when the resource was
created and it is what REST suggests. Then `201` means "the bytes are stored and
something may or may not have normalized them", a client that immediately
requests the content gets a `404`, and the status code's one job — telling the
caller what happened — is done badly.

**Let HTTP tidy up the lifecycle.** A capture left `PROCESSING` by an
infrastructure failure looks untidy from a route handler that has the record
right there. Marking it `FAILED` would make the API's story consistent and the
system's story false: Phase 0H and 0I chose truthful non-terminal states
precisely because an infrastructure failure is not a verdict on a capture.

**Make a duplicate `POST` idempotent**, since retries are what networks do. Then
the system guesses. A repeated capture id can be a retry after a timeout, a
buggy client looping, or two connectors that picked the same id — and returning
the first result assumes the benign case, which is exactly what ADR-010 refused
one layer down.

And the new one: **a 5xx that explains itself.** Core's storage errors name the
SQLite file and the staging directory, deliberately, because an operator reading
a log needs them. Passing that message to a caller publishes the server's layout
to anyone who can provoke a failure.

## Decision

**A new top-level package, `src/unimem_api/`, outside `core`.**

```
src/unimem_api/
    app.py        create_app(...) and the four routes
    models.py     the HTTP response DTOs
    errors.py     the core-failure-to-status translation table
    wiring.py     build_local_app(data_dir), the composition root
    __main__.py   python -m unimem_api
```

FastAPI, uvicorn, status codes, routing, and the command line all live here.
`core` does not import this package, does not know it exists, and gained no
runtime dependency — a property enforced by a test that walks every `core`
module's imports rather than by a convention. `core` is not moved, renamed, or
restructured.

**The POST body is `CaptureEnvelope` itself.** Not a wrapper, not a subset, not
an HTTP mirror. FastAPI validates the canonical contract directly, so
`extra="forbid"`, `AwareDatetime`, the payload cross-field rules, and the closed
`SchemaVersion` literal all apply at the network edge because they are the
contract's rules and there is only one contract. The client supplies the capture
id, exactly as the domain has required since Phase 0F.

There is deliberately **no request DTO of any kind**. The only HTTP-shaped models
are three responses — `HealthResponse`, `CaptureAcceptedResponse`, and the error
envelope — and each exists because it has no canonical contract behind it.

**Four routes, and no fifth.**

```
GET  /health                     {"status": "ok"}
POST /v1/captures                CaptureEnvelope -> 201
GET  /v1/captures/{id}           the authoritative CaptureRecord
GET  /v1/captures/{id}/content   the canonical ContentObject
```

No list, search, batch, delete, reprocess, or export endpoint. Each is a design
with its own questions, and none is needed to hand over a capture.

**`POST` runs intake and processing synchronously, and `201` means the pipeline
finished.**

```
CaptureEnvelope
    -> CaptureIntake.accept(envelope)          RECEIVED -> STORED
    -> ProcessingOrchestrator.process(id)      PROCESSING -> content -> COMPLETE
    -> 201 {capture_id, content_id, status: "complete"}
```

The status code is written only after the orchestrator returns, which by its own
contract means the canonical content and the `COMPLETE` snapshot are both
durable. A client that receives `201` can request the content in its next call
and find it. `201` is never returned because intake reached `STORED`.

Synchronous is a choice with a cost and a reason. The cost: the request is held
for the whole pipeline, and a slow processor is a slow request. The reason: a
queue means job ids, polling, worker ownership, and a second lifecycle — and the
honest version of "we accepted it" is `202` with a completely different client
contract. There is one processor, it reads a local file and decodes UTF-8, and
inventing a worker for it would be building the next layer of foundation for a
product that has not asked for it.

**The two `GET`s return the canonical contracts as their own JSON.**
`model_dump_json()` — the same call the SQLite stores persist through — sent as
the response body. Routing them through FastAPI's `response_model` would
re-derive that JSON from a second description of the same contract, which is the
duplicate definition ADR-002 and ADR-006 refused; `response_model` is still
declared, for the OpenAPI schema only.

**No renderer is called.** `GET .../content` returns the canonical
`ContentObject`, not `JsonRenderer`'s projection of it and certainly not
Markdown. A rendering is a derived representation with its own audience and its
own version (ADR-005); a client that wants one derives it from this.
`unimem_api` does not import `core.rendering`, and a test asserts it.

**`GET /v1/captures/{id}` is the recovery story, and the whole of it.** It exists
because Phase 0 chose truthful non-terminal states, and that choice is only worth
something if someone can look. A capture may read `received` (raw storage failed
after the receipt), `stored` (routing failed), `processing` (an infrastructure
failure mid-run, or content durable ahead of the lifecycle write), `failed` (a
processing verdict), or `complete`. Each is a true statement about how far it
got.

**HTTP repairs none of it.** No handler advances a status, retries a step, rolls
one back, or deletes a partial result. Lifecycle is core's, including its
half-states.

**A stable typed error envelope, translated at the delivery boundary only.**

```json
{"error": {"code": "capture_already_exists", "message": "..."}}
```

| Core error | Status | Code |
| --- | --- | --- |
| `CaptureRecordAlreadyExistsError` | 409 | `capture_already_exists` |
| `ContentObjectAlreadyExistsError` | 409 | `content_conflict` |
| `InvalidCaptureProcessingStateError` | 409 | `invalid_capture_state` |
| `CaptureRecordNotFoundError`, `ContentObjectNotFoundError` | 404 | `not_found` |
| `UnsupportedCapturePayloadError` | 422 | `unsupported_payload` |
| `InvalidCaptureEnvelopeError` | 422 | `invalid_capture_envelope` |
| `ProcessingInputError`, `TextDecodingError`, `ProcessingOutputError` | 422 | `processing_failed` |
| `ProcessorRoutingError` (`NoProcessorError`, `AmbiguousProcessorError`) | 500 | `processing_configuration_error` |
| `CaptureRecordCorruptError`, `ContentObjectCorruptError` | 500 | `data_integrity_error` |
| `CaptureRecordPersistenceError`, `ContentObjectPersistenceError`, `RawObjectStoreError` | 503 | `storage_unavailable` |

A malformed body FastAPI rejects before any route runs keeps its 422 and is
wrapped in the same envelope under `invalid_request`, so a client parses one
shape rather than two. The summary names the fields that failed and never echoes
the values that were submitted.

**Core's hierarchies are untouched.** Nothing is caught and re-raised as
something else, no two error types are collapsed, and no base class is widened.
The table maps concrete types; Starlette resolves a raised exception by walking
its MRO, so a type not in the table is *not* adopted by a base class that is. A
bare `ProcessingError`, or a bug, propagates to an ordinary unhandled 500.
`BaseException` is never caught.

**A 4xx explains the request; a 5xx explains nothing.** A 4xx passes core's own
message through, because core built it from what the client submitted and the
client can act on it. Every 5xx carries a fixed public message written in
`errors.py`, and the underlying error's text — which names the SQLite file, the
staging directory, or the internal shape of a stored snapshot — never reaches the
wire. Tests plant a path inside every injected 5xx and assert it does not come
back.

**No idempotency.** A duplicate capture id is `409`, and nothing loads the
previous result, returns a replayed success, mints a replacement id, or treats
the request as retry-safe. Two different ids carrying identical text both succeed
and get two content objects, one deduplicated raw object between them — the
Phase 0F identity rule, visible over HTTP.

**One local composition, `build_local_app(data_dir)`.**

```
<data-dir>/
    raw/              LocalRawObjectStore
    unimem.sqlite3    capture_records + content_objects
```

Both SQLite adapters open one file, as ADR-010 allows: each owns its own table
and nothing spans a transaction. The composition root creates `data_dir`, because
choosing and preparing an application's workspace is a deployment decision and
this is the deployment. `SqliteCaptureRecordStore`'s rule that *it* does not
fabricate a missing parent directory is unchanged — satisfying the precondition
beforehand is how that rule was always meant to be met.
`LocalRawObjectStore` is unchanged.

**`create_app` takes every dependency as a parameter.** No module-level app, no
registry, no settings framework, no environment lookup, no DI container, no lazy
singleton. Two apps in one process share nothing, and a test supplies doubles by
calling the function.

**`python -m unimem_api --data-dir ./data`, binding `127.0.0.1` by default.**
Three options — `--data-dir`, `--host`, `--port` (8765) — and uvicorn invoked
programmatically on the app object, so composition stays in `wiring.py`. No
reload mode, service manager, systemd unit, Docker image, or config file.

**There is no authentication in this phase.** No authorization, API keys, TLS, or
CORS middleware. That is precisely why the default bind is loopback: every caller
that can reach the port can submit captures and read everything stored, and
loopback is the only interface on which that posture is acceptable. A `--host`
that widens it is an explicit choice made by whoever types it, and the README
says so.

**Supported payloads are unchanged.** The HTTP contract accepts `CaptureEnvelope`
generally, because that is the canonical ingress contract. The wired pipeline
still supports inline `TEXT` only, so a structurally valid webpage, image, or
document envelope passes HTTP validation, reaches intake's existing capability
check, and comes back `422 unsupported_payload`. Nothing pretends otherwise, and
a later phase will accept those identical documents unchanged.

**Nothing canonical changed.** `SCHEMA_VERSION` stays `0.2`, `TextProcessor`
stays `text`/`0.2`, no contract gained a field, no renderer changed, and no
Phase-0 lifecycle semantics were altered.

## Consequences

Positive:

- **The system is runnable.** One command starts a real server that a real client
  can call — the first time anything outside a test can use it.
- **The next connector needs no new API.** A browser extension, an Obsidian
  plugin, and a script all POST the same `CaptureEnvelope`. The surface was
  designed once, for all of them.
- **`201` is worth trusting.** It reports the pipeline, so a client can act on it
  without a polling loop.
- **Failure is observable rather than merely truthful.** Phase 0's half-states
  stopped being a documented property and became a `GET`.
- **The boundary is checked, not asserted.** A test walks every `core` module's
  imports; another walks every `unimem_api` module's for a renderer.
- **The kernel is still frameworkless.** `core` depends on pydantic and the
  standard library, exactly as before, and a test proves it.

Costs:

- **A slow capture is a slow request.** Synchronous means the client waits for
  the whole pipeline, and a large capture holds a connection. Acceptable for
  local text; the first processor that does real work will make this a real
  question.
- **There is no authentication at all.** The mitigation is a loopback default and
  a documented warning, not a control. Anything beyond localhost is unsafe today.
- **A retrying client will see 409s.** Without idempotency, an interrupted POST
  that actually succeeded looks like a conflict on retry, and the client has to
  `GET` to find out what happened. This is the sharpest edge in the PR, and it is
  the one a real connector will define the fix for.
- **A partial state still needs a human.** ADR-010's cross-store gap is now
  *visible* over HTTP and still unreconciled.
- **The error table has to be maintained.** A new core error type that nobody
  adds to it becomes a bare 500. That is the safe failure — nothing is silently
  adopted — but it is a maintenance obligation.
- **One more package, one more coverage target, two more dependencies.** FastAPI
  and uvicorn are now in the install; `core` is unaffected but the project is not.

## Alternatives considered

- **Route decorators on `core` orchestrations.** Rejected: it puts a web
  framework inside the kernel and breaks invariant 9 from the inside.
- **A separate `CaptureEnvelopeRequest` HTTP model.** Rejected: two definitions of
  a capture, hand-synchronized, with silent drift as the failure mode. The
  canonical contract already *is* the ingress contract.
- **`201` on intake, processing in the background.** Rejected: it makes the status
  code a claim about storage rather than about the outcome, and the honest
  version of that contract is `202` plus a job resource — a second lifecycle
  nothing has asked for.
- **`202 Accepted` with a queue and a worker.** Rejected for the same reason, one
  step further along: job ids, polling, worker ownership, retry policy, and dead
  workers, in service of one processor that decodes UTF-8.
- **Idempotent `POST` on a duplicate id.** Rejected: it guesses which of three
  different situations produced the repeat, and returning the first result
  assumes the benign one. ADR-010 refused this at the content store; refusing it
  here is the same decision.
- **Repairing lifecycle state in the handler** — marking a stranded `PROCESSING`
  capture `FAILED` so the API's account is tidy. Rejected: it would make the API
  consistent and the system untruthful, undoing the central choice of Phases 0F,
  0H, and 0I.
- **Rendering Markdown or `JsonRenderer` output from `GET .../content`.**
  Rejected: it makes a derived representation the thing clients read, ties the
  renderer's version to the API contract, and re-crosses the line ADR-005 drew.
  The canonical object is what a client should hold.
- **Passing core error messages through on 5xx.** Rejected: it publishes the
  database path and the staging directory to anyone who can provoke a failure.
- **One `500` for every failure, with details in the logs.** Rejected: a client
  that cannot tell "your envelope is wrong" from "the disk is full" cannot behave
  correctly, and both of those have obvious different actions.
- **Adding auth now** — an API key header, at least. Rejected: a single shared
  secret with no rotation, no scoping, and no revocation is security theatre that
  invites exposing the port. Loopback plus an honest warning is the smaller lie.
  Auth becomes real work when something needs to reach this server from
  elsewhere.
- **CORS middleware, pre-emptively**, so a browser extension will work later.
  Rejected: no test requires it, extensions do not necessarily need it, and the
  connector that does will say exactly what it needs. Guessing produces a
  permissive policy nobody revisits.
- **A settings framework, Docker image, or systemd unit.** Rejected: three CLI
  flags and one command are the entire operational surface this phase has.
- **Building the browser extension in this PR.** Rejected: it is the next PR, and
  the API had to exist and be checkable first.
