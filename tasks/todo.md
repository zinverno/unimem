# Implementation tasks

Requirements, evidence and manual acceptance: [plan.md](plan.md).
Unchecked items are future work, not claims that the existing implementation
already provides the behaviour. A and B2/B3 are merged; C is the standalone
Obsidian delivery slice. Remaining B4 gates stay separate. D holds subsequent
sources and processors.

## Completed planning

- [x] Verify current main/PR state and inspect the existing service boundaries.
- [x] Read official Zen/MDN documentation and audit the extension's startup,
  APIs, async errors, permissions, local service, state and UI lifetime.
- [x] Record Zen/Linux as primary browser target and GitHub as a later source.
- [x] Separate temporary installation, distribution and actual runtime evidence.

## A — First PR: browser-free YouTube captions to Markdown

### A1 — Caption acquisition and artifact contract

Dependencies: none. Likely scope: source adapter, its tests, focused ADR.

- [x] Select and document a retrieval mechanism using current primary docs;
  no browser/profile/CDP, paid call or credential extraction required.
- [x] Specify the immutable caption artifact and track/cue metadata mapping
  into existing source/provenance concepts; justify any necessary contract
  extension with actual fixtures, not future GitHub needs.
- [x] Verify URL/redirect validation and bounded network operations, with
  deterministic fixtures for unavailable/malformed/rate-limited tracks.

### A2 — Durable application operation

Dependencies: A1. Likely scope: application composition, caption normalization
processor/intake path if required, persistence integration checks.

- [x] Compose acquisition → intake → processing → canonical persistence using
  existing owners; callable without HTTP or CLI and without browser types.
- [x] Preserve actual source/track evidence and timed cues where supplied;
  record only observed work, with no audio/video interpretation claims.
- [x] Verify serialized caption-artifact bytes, provenance, read-after-restart and failure-state
  truthfulness using temporary stores; no route/CLI lifecycle writes.

### A3 — Thin CLI and reproducible Markdown export

Dependencies: A2. Likely scope: CLI entry, pure export boundary, focused tests/docs.

- [x] Expose capture/status/result identifiers and explicit exit codes; export
  durable content to stdout or a selected non-vault path.
- [x] Decide required source links/cue presentation without silently changing
  ADR-005's existing renderer contract or requiring YouTube fields everywhere.
- [x] Verify end-to-end fixture capture, restart, network-free re-export by ID,
  export failure without capture loss, and ordinary non-YouTube export.

### A4 — First PR acceptance and handoff

Dependencies: A1–A3. Likely scope: tests, instructions, PR evidence.

- [x] Run focused suites, then `ruff check .`, `ruff format --check .`, `mypy`,
  and `pytest --cov=core --cov=unimem_api`; retain the 90% coverage floor.
- [x] Provide a reproducible CLI smoke using temporary storage; report fixture
  vs live evidence separately, and any external prerequisite as unverified.
- [x] Review diff/ADRs; hand off the three-part browser-independence/Zen-work/
  actual-Zen-evidence report. No extension port, GitHub ingestion or merge.

Evidence (2026-09-29): [PR #31](https://github.com/zinverno/unimem/pull/31) is merged.
Focused: 93 passed, new-package branch coverage 95.85%. Full local regression:
4,274 passed, combined core/API/YouTube branch coverage 97.35%. Ruff, formatting,
strict mypy, wheel/sdist and existing browser Node checks pass. A clean base
installation without retrieval dependencies passed 60 tests (two retrieval-only
modules skipped); the built wheel also rendered persisted content in a new
process without those dependencies. [Live evidence](../docs/YOUTUBE_CAPTIONS.md#opt-in-live-acceptance)
is a separate PASS on one public video. Zen runtime acceptance remains NOT RUN.
CI's final status is reported against the actual PR head at handoff, not inferred
from these local results. B2/B3 implementation, available B4 evidence and the
subsequent standalone C slice are recorded below; D remains future work.

## B — Next PR: Zen/Linux browser delivery

### B1 — Local API protection and acceptance contract

Dependencies: A4 (merged PR #31). Implemented server scope: delivery boundary,
auth/validation, durable operations, bounded worker and ADR-025.

- [x] Review all existing local routes and implement explicit local credentials/authorization,
  Host/Origin, input and resource bounds before exposing new browser routes.
- [x] Define durable acceptance, server-owned work, status lookup and ambiguous
  request handling; use the A2 service and keep current capture semantics intact.
- [x] Verify unauthorized webpage/caller denial and accepted-work survival;
  specify any restart recovery honestly rather than inferring it from storage.

B1 evidence: [setup and deterministic TCP/process acceptance](../docs/LOCAL_DELIVERY.md).
Mandatory bearer credentials cover old routes as well as new YouTube routes.
Durable submit/replay uses the existing SQLite file; recovery uses reserved capture
IDs and never blindly repeats acquisition. Zen UI/runtime and vault import are
not included. Local gates: 4,310 full-regression tests passed; core/API coverage 99.19%.
Dedicated B1: 36 passed / 94.79%; YouTube: 102 passed / 96.20%.
Ruff/format/strict mypy, 11 browser Node suites, wheel/sdist and base installed-wheel
checks passed. Base without retrieval: 42 passed / one retrieval-only module
skipped, plus fresh-process offline CLI render. Hosted CI is verified against
the exact PR head at handoff, separately from these local measurements.

### B2 — Compatible extension startup and original captures

- [x] Implement a shared MV3 event-page/service-worker manifest, native modules,
  stable Gecko ID and callback/lastError handling without a framework.
- [x] Restore protected text/page capture through the shared credential transport,
  retaining original envelopes, 200/201, bounded replay and HTML boundaries.
- [x] Add explicit credential/settings UI, session default, opt-in local storage,
  typed read-only readiness probe, removal/rotation and denied-permission handling.
- [x] Run Node, protected B1 cross-language and packaging checks; preserve CI gates.
- [ ] Complete all target-browser acceptance rows. See exact per-browser results
  in [BROWSER_VERIFICATION.md](../docs/BROWSER_VERIFICATION.md); no blanket PASS.

### B3 — Explicit YouTube action and recoverable status

- [x] Freeze URL and ordered languages; persist operation identity before POST.
  Coalesce concurrent actions and require explicit new operation for reacquisition.
- [x] Store bounded local references/observations, not a second lifecycle or
  transcript store. Record policy in ADR-026 and invariant 18.
- [x] Restore the same IDs after UI/background closure, manual refresh, explicit
  same-ID delivery, typed validation, and accepted-but-missing handling.
- [x] Authorize Markdown reads; safe text preview and explicit Blob download.
  Export failure does not change capture state; no Obsidian claim/import.
- [x] Add failure/storage/response/race tests and real protected B1 TCP acceptance
  with a synthetic caption provider and POST/acquisition/capture counts.

### B4 — Zen acceptance and install documentation

- [x] Locate actual Zen including Flatpak; use isolated profile and temporary data.
- [x] Provide reproducible dev ZIP, stable identity, metadata, SHA256 and install/
  connection/recovery instructions in [browser guide](../docs/BROWSER_DELIVERY.md).
- [x] Keep runtime, synthetic/live and signed-distribution evidence separate in
  [verification](../docs/BROWSER_VERIFICATION.md).
- [ ] Complete every native/browser-platform acceptance row that remains open in
  the verification record; partial evidence does not complete B4.
- [ ] Signed installation, permission prompts after normal installation, browser
  restart and updates. No signed package; no submission/publication authorized.

## C — Standalone UniMem Connector delivery

Depends on merged B2/B3 (PR #33). Owner explicitly authorized C while remaining
B4 gates stay open. ADR-027 supersedes the former Veynrel/Companion delivery plan.

- [x] Independent `clients/obsidian-plugin` package, build, settings and commands.
- [x] Destination UUID and separately scoped receiver credential; operator pairing.
- [x] Durable immutable snapshot, digest, idempotent submission, claim/lease and ACK.
- [x] Create-only Vault API importer with persistent journal, verification and safe paths.
- [x] Explicit browser send, destination/filename/status and restored server receipts.
- [x] Automated auth, lease, crash recovery and cross-language fixture checks.
- [x] Native synthetic Zen → API → standalone plugin → isolated vault acceptance.
- [x] Dev installation, protocol, boundaries and measured evidence documentation.
- [x] Fresh live-caption browser → Obsidian acceptance: [2026-10-03 repeat](../docs/YOUTUBE_LIVE_VERIFICATION_2026-10-03.md)
  passed without code changes; the historical timeout remains unexplained and
  its BLOCKED evidence is preserved separately.
- [ ] Native Windows/macOS filesystem validation (desktop manifest does not prove it).

Evidence and current limits: [OBSIDIAN_VERIFICATION.md](../docs/OBSIDIAN_VERIFICATION.md).
No Community publication, release or merge. B4 distribution and earlier Phase 5A
owner gates remain open.

## D1 — Explicit local audio transcription

- [x] Optional faster-whisper CPU/int8 multilingual base, explicit pinned model preparation.
- [x] Original-first WAV PCM / MP3 / OGG Opus, bounded decoder/worker, canonical ASR provenance.
- [x] Durable audio acceptance/replay/recovery; one active ASR child, real process termination.
- [x] Existing Zen page upload/language/status/recovery/preview/export/explicit Connector send.
- [x] Model-free canonical reads/Markdown; unchanged create-only Connector and 1-MiB delivery limit.
- [x] Real RU/EN/Opus/silence and near-limit resource measurements; native one-note acceptance.
- [x] Deterministic validation/failure/replay/recovery/offline/budget tests and CI coverage gate.
- [x] [Operator commands](../docs/LOCAL_AUDIO.md) and
  [dated evidence, ASR errors and remaining gates](../docs/AUDIO_VERIFICATION_2026-10-03.md).

No signed publication/release, microphone, messenger integration, diarization,
translation, summary or binary attachment delivery. Earlier B4 gates remain open.

### D1 quality follow-up — fixed base/small comparison

- [x] Four distinct Russian utterances (one held out), English prose and silence;
  independent corpus references and frozen hashes/settings before comparison.
- [x] Fourteen real sequential offline runs, fresh bounded processes; input and
  canonical/offline-render audit, WER and resource measurements.
- [x] [Measured decision and commands](../docs/asr-quality/README.md): retain base;
  small improves pooled WER and two short utterances but fails the predeclared
  discovery semantic gate. No production profile/UI change.
- [ ] Independent listening audit, spontaneous voice messages and broader
  noise/speaker coverage remain unverified; this tiny set does not close them.

Prior ASR/native evidence and older B4 gates remain unchanged.

## D2 — Later sources and processing

- [ ] Plan image processing and video visual interpretation as distinct
  capabilities, preserving existing originals.
- [ ] Plan public GitHub file/fragment selection with one pinned commit,
  source links, faithful Markdown/code, request/size/file bounds and explicit
  partial results; no repository execution or implicit full download.
- [ ] Treat private GitHub access as a separate explicit read-only connection.
  D2 remains outside the completed A/B/C and explicitly requested D1 slices.

## D3 — Bounded local video notes (dependent on open PR #39)

This explicitly requested slice supersedes the local-video portion of the D2
planning item above. Broader video interpretation and GitHub remain future work.

- [x] Verify main and PR #38/#39; branch from #39 HEAD `50b9081e5ebe8e7339b9e5f1f63447968f0b6558`.
- [x] Reuse intake/raw/canonical, durable operation recovery, ASR/vision and heavy slot.
- [x] Bounded MP4/H.264/AAC adapter; deterministic quarter sampling and shared PTS timeline.
- [x] Explicit independent speech/frame/description choices and recoverable existing Zen UI.
- [x] One VIDEO result with raw/derived/transcript/vision provenance and offline reads.
- [x] Strict v1/v2 preservation, distinct v3 manifest/ACK and explicit PNG-frame permission.
- [x] Connector bounded-list migration and create-only recovery, rollback/fences/partial-write tests.
- [x] Decoder/VFR/offset/refusal tests, real kill/reap/slot tests, fake-stage adapter tests.
- [x] One real ASR + three real vision calls through installed Zen and standalone Connector.
- [x] Separate no-audio clip, exact snapshot/PNG delivery, restart/poll deduplication and offline Obsidian.
- [x] ADR, operator instructions, dated quality/resource evidence, reproducible dev packages.
- [ ] Review and manual merge of the dependent PR chain; no auto-merge/release/publication.

Evidence: [2026-10-03](../docs/VIDEO_VERIFICATION_2026-10-03.md).
ASR comparison is not reopened. B4 signed install/update and other browser/platform
acceptance gates remain open; three stills are not full-video analysis.
