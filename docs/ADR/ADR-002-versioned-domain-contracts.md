# ADR-002: Domain contracts are explicitly schema-versioned from the start

Status: accepted (Phase 0A); amended in Phase 0G, which advanced the current
version to `0.2` (see [ADR-008](ADR-008-durable-capture-metadata-and-schema-0.2.md)).
The decision below stands as written; the amendment at the end records what
exercising it for the first time actually looked like.

## Context

The contracts defined here will be produced and consumed by components that
are not deployed together: capture clients (browser extension, companion app),
ingestion processors, and later retrieval and agent layers. Those components
will be updated on different schedules, and captured data is expected to
outlive the code that wrote it.

Version fields are cheap to add now and expensive to retrofit: without one, a
consumer meeting an unfamiliar document has to guess a version from which
fields happen to be present.

## Decision

Every primary contract (`CaptureEnvelope`, `CaptureRecord`, `ContentObject`)
carries an explicit `schema_version` field. The current value at the time of
this decision was `"0.1"`.

The version is validated, not merely recorded. `schema_version` is typed as a
`Literal`, and `SUPPORTED_SCHEMA_VERSIONS` names what this build accepts, so
unknown versions — including *newer* ones — are rejected at validation time
rather than being interpreted with today's assumptions.

Nested objects (`Segment`, `Provenance`, `Asset`, `ProcessingRecord`, and the
value objects) do not carry their own version; they participate in the version
of the contract that contains them. One version per document keeps the
serialized form readable and avoids independently drifting sub-versions.

Forward extensibility within a version goes through `metadata` fields rather
than through speculative typed fields.

No migration engine is built in Phase 0A. Future versions are expected to
progress `0.1 -> 0.2 -> 1.0`, with conversion implemented when a second
version actually exists.

## Consequences

Positive:

- A stored document always states how to interpret itself.
- A component reading data it does not understand fails loudly instead of
  silently mis-reading it.
- Contract changes become a visible, deliberate act.

Negative / costs:

- Adding a version means touching every producer; that is the point, but it is
  friction.
- Rejecting newer versions means an old consumer stops working against new
  data rather than degrading gracefully. This is the intended trade-off:
  correctness over optimistic tolerance.
- Migration code will eventually have to be written, and per-document
  versioning offers no way to migrate a sub-object independently.

## Alternatives considered

- **No version field.** Rejected: version guessing from field presence.
- **Accept any version, ignore unknown fields.** Rejected: `extra="forbid"`
  and silent tolerance are contradictory, and mis-reading data is worse than
  refusing it.
- **Version every nested object.** Rejected as premature; one version per
  document is enough until a real migration exists.

## Amendment (Phase 0G): the current version is `0.2`

Phase 0G made the first real contract change — `CaptureRecord` gained
`context`, `intent`, and `title` — and so became the first exercise of this
decision. Nothing above was reversed. What changed:

- `SCHEMA_VERSION` is `"0.2"`, and `SchemaVersion` is
  `Literal["0.1", "0.2"]`, so `SUPPORTED_SCHEMA_VERSIONS` is
  `{"0.1", "0.2"}`. Newly built contracts carry `0.2`.
- `"0.3"` and every other unrecognized value are still rejected. Widening the
  literal to admit a version this build can actually read is the whole
  mechanism; tolerance was not added.
- **One version still names the whole contract set.** `CaptureEnvelope` and
  `ContentObject` changed no field in `0.2`, and their version advanced anyway,
  because these contracts are designed, reviewed, and released together. The
  alternative — a version per contract — was considered and declined again in
  [ADR-008](ADR-008-durable-capture-metadata-and-schema-0.2.md): three
  independently drifting version lines is a combinatorial compatibility matrix
  bought for a cosmetic saving.
- **A listed old version is a promise this build can read it.** `0.1` stays in
  the literal for exactly as long as `0.1` documents remain readable, and a
  `0.1` `CaptureRecord` is written back out as a `0.1` document — with no `0.2`
  keys, not even null ones, because `extra="forbid"` means an old reader would
  reject them. Reading an old document is worth little if writing it back
  breaks the build that wrote it.
- **Still no migration engine**, as this decision anticipated. Version-aware
  validation and serialization on the one model that changed was enough. The
  prediction that "conversion [is] implemented when a second version actually
  exists" held in a weaker form than expected: a second version exists, and
  conversion still is not needed, because a `0.1` record is not converted — it
  stays a `0.1` record.

The note about `metadata` fields as the path for forward extensibility is
worth re-reading in this light. Phase 0G deliberately did **not** take that
path for capture metadata: `context` and `intent` are validated models the
system already had, and burying them in an untyped bag to avoid a version bump
would have been the version-guessing this ADR exists to prevent.
