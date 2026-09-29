# Implementation tasks

Requirements, evidence and manual acceptance: [plan.md](plan.md).
Unchecked items are future work, not claims that the existing implementation
already provides the behaviour. Implement A before B; C is later scope.

## Completed planning

- [x] Verify current main/PR state and inspect the existing service boundaries.
- [x] Read official Zen/MDN documentation and audit the extension's startup,
  APIs, async errors, permissions, local service, state and UI lifetime.
- [x] Record Zen/Linux as primary browser target and GitHub as a later source.
- [x] Separate temporary installation, distribution and actual runtime evidence.

## A — First PR: browser-free YouTube captions to Markdown

### A1 — Caption acquisition and artifact contract

Dependencies: none. Likely scope: source adapter, its tests, focused ADR.

- [ ] Select and document a retrieval mechanism using current primary docs;
  no browser/profile/CDP, paid call or credential extraction required.
- [ ] Specify the immutable caption artifact and track/cue metadata mapping
  into existing source/provenance concepts; justify any necessary contract
  extension with actual fixtures, not future GitHub needs.
- [ ] Verify URL/redirect validation and bounded network operations, with
  deterministic fixtures for unavailable/malformed/rate-limited tracks.

### A2 — Durable application operation

Dependencies: A1. Likely scope: application composition, caption normalization
processor/intake path if required, persistence integration checks.

- [ ] Compose acquisition → intake → processing → canonical persistence using
  existing owners; callable without HTTP or CLI and without browser types.
- [ ] Preserve actual source/track evidence and timed cues where supplied;
  record only observed work, with no audio/video interpretation claims.
- [ ] Verify original bytes, provenance, read-after-restart and failure-state
  truthfulness using temporary stores; no route/CLI lifecycle writes.

### A3 — Thin CLI and reproducible Markdown export

Dependencies: A2. Likely scope: CLI entry, pure export boundary, focused tests/docs.

- [ ] Expose capture/status/result identifiers and explicit exit codes; export
  durable content to stdout or a selected non-vault path.
- [ ] Decide required source links/cue presentation without silently changing
  ADR-005's existing renderer contract or requiring YouTube fields everywhere.
- [ ] Verify end-to-end fixture capture, restart, network-free re-export by ID,
  export failure without capture loss, and ordinary non-YouTube export.

### A4 — First PR acceptance and handoff

Dependencies: A1–A3. Likely scope: tests, instructions, PR evidence.

- [ ] Run focused suites, then `ruff check .`, `ruff format --check .`, `mypy`,
  and `pytest --cov=core --cov=unimem_api`; retain the 90% coverage floor.
- [ ] Provide a reproducible CLI smoke using temporary storage; report fixture
  vs live evidence separately, and any external prerequisite as unverified.
- [ ] Review diff/ADRs; hand off the three-part browser-independence/Zen-work/
  actual-Zen-evidence report. No extension port, GitHub ingestion or merge.

## B — Next PR: Zen/Linux browser delivery

### B1 — Local API protection and acceptance contract

Dependencies: A4. Likely scope: delivery boundary, auth/validation tests, ADR.

- [ ] Review all existing local routes and protect pairing/authorization,
  Host/Origin, input and resource bounds before exposing new browser routes.
- [ ] Define durable acceptance, server-owned work, status lookup and ambiguous
  request handling; use the A2 service and keep current capture semantics intact.
- [ ] Verify unauthorized webpage/caller denial and accepted-work survival;
  specify any restart recovery honestly rather than inferring it from storage.

### B2 — Compatible extension startup and original captures

Dependencies: B1. Likely scope: manifest/browser entry, small API adapter, tests.

- [ ] Validate one combined MV3 manifest in Zen first; use small static variants
  only for demonstrated constraints, sharing all business logic without a new
  framework or build infrastructure.
- [ ] Verify module loading, menu registration, Promise/callback error paths,
  denied scripting/host permission and background unload/restart.
- [ ] Preserve text and whole-page gestures, least privileges and loopback
  destination; run `npm test --prefix clients/browser-extension` plus real Zen
  checks. Record Firefox/Chromium regressions independently.

### B3 — Explicit YouTube action and recoverable status

Dependencies: B1–B2. Likely scope: action/menu or justified popup, delivery/status
client, minimal capture-reference storage if needed, relevant ADR updates/tests.

- [ ] Freeze selected tab/video URL at the gesture; send it through the protected
  local API and show accepted/processing/result/error truthfully.
- [ ] Preserve accepted work across UI closure/background unload; reopen status
  for the same capture. Any local reference storage explicitly updates the
  former no-state policy without moving lifecycle ownership into the extension.
- [ ] Verify two tabs, SPA navigation, lost responses, denied access, unavailable
  captions, closed tab/popup and no duplicate work. Update affected assertions
  deliberately; never weaken them just to pass.

### B4 — Zen acceptance and install documentation

Dependencies: B3. Likely scope: installation guide, smoke/evidence record,
distribution metadata/artifact preparation.

- [ ] Run every [manual smoke row](plan.md#local-installation-and-reproducible-manual-smoke)
  in real Zen/Linux; record exact browser/base versions, commit and result.
- [ ] Separate temporary development install from signed daily-use installation;
  test real permission prompts and persistence across browser restart. Keep
  publication separate from preparation.
- [ ] Mark Zen `NOT RUN`/`BLOCKED` if unavailable; provide the local procedure
  and do not claim support from Firefox, Chromium or Python/Node tests.

## C — Later sources and processing

- [ ] Plan audio/voice transcription, image processing and video visual
  interpretation as distinct capabilities, preserving existing originals.
- [ ] Plan public GitHub file/fragment selection with one pinned commit,
  source links, faithful Markdown/code, request/size/file bounds and explicit
  partial results; no repository execution or implicit full download.
- [ ] Treat private GitHub access and Obsidian/Companion delivery as separate
  explicit integrations. Do not implement C as part of A or B.
