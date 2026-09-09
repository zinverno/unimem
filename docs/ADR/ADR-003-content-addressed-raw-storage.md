# ADR-003: Raw originals are immutable and content-addressed with SHA-256

Status: accepted (Phase 0B)

## Context

Raw originals are the one thing in UniMem that cannot be regenerated. Every
extractor, transcriber, and renderer that will ever run is a function of those
bytes, and all of them will change: a better OCR engine, a smarter HTML reader,
a transcription model that did not exist when the capture happened. Whatever
was captured must still be reprocessable years later, byte for byte.

Originals are also the largest thing in the system. A screenshot is small; a
recorded meeting is not. They cannot be assumed to fit in memory, and copying
them around is expensive.

That leaves the question of what identifies a stored original. Filenames
collide and get renamed. URLs change and redirect. Capture timestamps differ
between two captures of the same file. MIME types are guesses: the same PNG
arrives as `image/png` from a browser and `application/octet-stream` from a
file upload. None of these describe the bytes.

## Decision

Raw originals are **immutable** and **content-addressed by SHA-256**. Identity
depends on the bytes and nothing else.

- Same bytes → same digest → same logical object → one physical object.
- Different bytes → different digest → a different object.
- The logical reference is `sha256:<digest>`, using the existing Phase 0A
  `RawObjectRef`. Its `id`, `sha256`, and `ref` all derive from the digest.
- Metadata — MIME type, filename, source URL, capture time — describes a
  *capture*, never the binary object. It may travel on the reference, but it
  never affects identity, and Phase 0B writes no sidecar metadata at all.
- The local filesystem layout (`<root>/sha256/ab/cd/<digest>`) is an
  implementation detail. It is derived only from a validated digest and never
  appears in a `RawObjectRef`; a different backend can store the same object
  anywhere it likes.
- There is **no update and no delete**. Nothing rewrites a finalized object.
  Retention and deletion policy is a separate decision, deliberately deferred.

Deduplication is exact binary deduplication only. Perceptual hashing,
near-duplicate text, normalized HTML, and URL identity are different problems
and are not solved here.

## Consequences

Positive:

- **Deterministic identity.** Two components that see the same bytes compute
  the same address without coordinating.
- **Exact deduplication.** Re-capturing the same file costs nothing; the
  duplicate write converges on the object already stored.
- **The address is an integrity check.** A stored object can always be verified
  against the name it is filed under.
- **A foundation for reprocessing.** A future extractor can be re-run over an
  original with certainty that the bytes have not drifted.
- **Backend independence.** Because the reference names content rather than
  location, moving to another backend does not invalidate stored references.

Costs:

- **The whole input must be hashed before the final address is known.** The
  address cannot be predicted from the first byte, so ingestion is
  hash-then-place rather than place-then-write.
- **Storage requires staging.** Bytes land in a temporary file within the store
  and are finalized once the digest is known, which briefly costs extra space.
- **No semantic duplicate detection.** Two encodings of the same image, or the
  same article fetched twice with a changed timestamp, are different objects.
  That is correct at this layer and must be solved above it.
- **Changing the digest algorithm is a migration.** SHA-256 is baked into the
  reference scheme. Moving to another algorithm means a new scheme and an
  explicit, versioned migration — not a silent swap. This is why the scheme is
  written into the reference rather than assumed.

## Alternatives considered

- **UUID or ULID object ids.** Rejected: two captures of identical bytes would
  produce two stored copies, and nothing would tie a reference to its content.
- **Filename or URL as identity.** Rejected: neither is stable, and both are
  attacker- or user-controlled input on a path.
- **Storing metadata alongside the bytes to form identity.** Rejected: it makes
  the same bytes storable twice under different descriptions, which defeats the
  purpose of a content address.
