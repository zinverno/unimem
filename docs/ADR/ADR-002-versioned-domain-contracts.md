# ADR-002: Domain contracts are explicitly schema-versioned from the start

Status: accepted (Phase 0A)

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
carries an explicit `schema_version` field. The current value is `"0.1"`.

The version is validated, not merely recorded. `schema_version` is typed as a
`Literal`, and `SUPPORTED_SCHEMA_VERSIONS` names what this build accepts, so
unknown versions — including *newer* ones such as `"0.2"` — are rejected at
validation time rather than being interpreted with today's assumptions.

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
