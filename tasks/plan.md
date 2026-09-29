# YouTube CLI capture, then Zen/Linux delivery

Updated: 2026-09-29. Block A is implemented on `feat/youtube-transcript-markdown`;
verification and handoff evidence are tracked in [todo.md](todo.md) and the
[CLI guide](../docs/YOUTUBE_CAPTIONS.md). Blocks B-D remain future work.
Tasks and acceptance gates: [todo.md](todo.md).

## Baseline and scope

Inspected local and remote `main` at
`582f4594ad4ee1d1282c52154c20b4017884e406`; GitHub returned no open PRs.
There was no earlier plan in this checkout. The owner confirmed there are no
additional requirements beyond the supplied addendum. That was the planning
baseline; the owner subsequently authorized block A implementation and a PR.
The implementation retains that documentation and adds only the authorized CLI slice.

The order is:

1. **YouTube captions → durable capture → Markdown through a thin CLI.**
2. **Zen Browser on Linux → explicit action on a YouTube page → local UniMem →
   understandable processing status and result.** Zen is the required primary
   browser target; Firefox is a development/checking platform. Preserve existing
   Chromium behaviour where possible, but it cannot substitute for Zen acceptance.
3. **Zen → UniMem → agreed note import into Obsidian**, using the existing
   Veynrel/Companion boundaries. Preview/approval and synthetic-vault acceptance
   are part of this milestone; direct vault writes and bypasses are not.
4. Further sources and processing: audio/voice, images, the visual part of video,
   and GitHub. GitHub neither replaces multimedia nor makes UniMem a code-only app.

No paid calls, real-vault writes, Companion bypass, automatic merge or release,
or claims of unverified support. Markdown output uses a temporary directory or
explicit non-vault destination; later Obsidian delivery respects Companion.
Existing Phase 5A owner acceptance remains open; this plan does not close it or
silently supersede accepted ADRs.

## Boundaries to preserve in the first implementation

| Concern | Existing owner / planned boundary |
| --- | --- |
| Source acquisition | A YouTube-specific adapter outside `core`; validates the selected URL and fetches available captions without a browser |
| Application operation | Callable service composes acquisition and the existing intake/processing services; no CLI, HTTP, WebExtensions or Chromium types in its interface |
| Durable capture | `CaptureIntake`, immutable raw storage, `CaptureRecordStore`, `ProcessingOrchestrator`, `ContentObjectStore` remain the owners |
| Normalization | A processor produces the `ContentObject`; delivery code does not construct canonical results or write lifecycle state |
| Export | Pure rendering of persisted canonical content; filesystem/stdout delivery belongs outside the renderer |
| CLI / later browser HTTP route | Thin delivery adapters calling the same application operation; no duplicated caption workflow |

Source, format/modality, processing method, and delivery are separate choices.
For example, GitHub may supply Markdown, code, or PDF; YouTube captions are
extracted text, not evidence that video pixels or audio were processed.
OCR, transcription, and visual description stay separate opt-in capabilities.

Reuse `CaptureSource.provider/url`, `ContentSource.provider/url`, segment
`Provenance`, optional `TemporalLocation`, and existing metadata where their
semantics fit. `CaptureSource.type` currently means entry channel (`api`,
`browser`, `filesystem`, `upload`), not platform: do not add YouTube/GitHub to
that enum just to name a provider. A caption track's video ID, language and cue
times belong to that source/result; they are never mandatory for other sources
or for general Markdown export. Do not put derived transcript results in an
input envelope or pretend a stored caption document is the original video.

The concrete format and mapping are decided in
[ADR-024](../docs/ADR/ADR-024-youtube-caption-artifact-and-cli.md):
`unimem.youtube-captions/1`, a JSON serialization of selected track metadata
and the fetched UTF-8 XML caption body. It is external input and its stored
original is this artifact, not video bytes or a byte-exact network response.
Future UniMem speech recognition instead derives text from a separate audio
original. Retrieval uses optional `youtube-transcript-api==1.2.4` with a bounded,
cookie-free transport outside core; normalization uses the existing Processor
port and opt-in staged FILE intake. Schema 0.3 is unchanged.

Ordinary retrieval needs neither CDP, a Chrome profile, Chromium startup,
browser cookies, hidden credentials, nor a paid provider. Caption absence,
access blocks, timeouts, language mismatch and malformed responses have distinct
error codes. There is no transcription or browser-automation fallback.

CLI scope is argument parsing, service construction/call, status/exit code,
and output delivery. Preserve source/track evidence durably before reporting
success. Export must work again after reopening the stores, without network or
a second acquisition. An export failure must leave the saved capture intact and
allow export by its ID. A minimal function is sufficient; no service registry,
generic connector framework, new job framework or interface family is justified.

The existing `MarkdownRenderer` (`markdown/0.1`, ADR-005) emits only title and
segment text; it deliberately omits source links, metadata and timestamps.
That contract is unchanged. The new pure `youtube-caption-markdown/1` projection
includes source/capture/track metadata and timed cue text, without requiring
YouTube fields in general renderers. Markdown stays derived, never the system's
source of truth. The CLI writes exclusively into the explicit output directory
and refuses an existing file; offline `render` reads persisted content by ID.

## Extension audit at the inspected commit

These are source observations and documented compatibility constraints, not a
runtime result. The AST graph was refreshed locally without LLM/network calls;
it covered 279 code files. Its bounded query was only a navigation aid; the
manifest, adapter, relevant `lib/`, contracts, rendering and API source were read.

| Area | Current evidence | Work for the Zen slice |
| --- | --- | --- |
| Manifest/startup | `clients/browser-extension/manifest.json`: MV3, only `background.service_worker`, `type: module`; no Gecko settings | Provide a Firefox-compatible event-page entry; verify module imports and startup in real Zen before claiming support |
| API boundary | `service-worker.js` directly uses `chrome.action.onClicked`, `runtime.onInstalled`, `contextMenus.onClicked/create`, `scripting.executeScript`; `lib/action.js` receives action methods | Reuse the existing injected dependencies and shared business logic; normalize only actual API differences |
| Promise/errors | Script injection is awaited; handlers catch terminal rejection; feedback absorbs throws/rejections | Verify actual Promise/callback contracts. `lib/menu.js` catches only synchronous throws from `create`, omitting the callback/`runtime.lastError` error path |
| User gesture / URL | Action and menu handlers receive the acted-on tab; selection/DOM reads use top-frame `executeScript` after the gesture | Freeze the chosen tab/URL before asynchronous work; test multiple tabs and YouTube SPA navigation. Never later substitute whichever tab became active |
| Popup | No `default_popup`; left-click saves selection, action-context menu saves the whole page | Preserve both behaviours. A popup would replace `onClicked` and needs an explicit UX/ADR change; popup lifetime must never own processing |
| Permissions | `activeTab`, `scripting`, `contextMenus`; host permission `http://127.0.0.1/*`; no persistent site access, cookies, storage or static content scripts | Check permission grant/denial in Zen and packaging. Keep localhost and website privileges narrow; justify any added permission with a concrete need |
| Local service | `lib/api.js` fetches from the privileged extension context to fixed `http://127.0.0.1:8765`; page content cannot set the destination | Keep this boundary. `unimem_api/app.py` has no authentication/authorization or Host/Origin enforcement; loopback and lack of CORS middleware do not replace API protection |
| Status/recovery | `submitCapture` accepts only `200/201` plus matching ID and `complete`; `202` is a protocol error. One bounded resend and one read resolve ambiguity while the context lives | A later accepted/processing contract needs its own explicit handling. Do not reuse “saved” for a merely accepted request |
| Context lifetime | No persisted capture ID or history; ID/envelope exist only in a running call. Server records/content are durable, but there is no resumable queue or restart reconciliation | Define acceptance, server ownership and status rediscovery; test background unload and popup/tab closure. Do not confuse durable records with automatic work recovery |
| Existing tests | Node tests pin the Chromium manifest, no popup and no storage; browser source currently has no YouTube action | Update assertions deliberately alongside the future design, preserving permission/behaviour guarantees. Node/Python tests are not Zen acceptance |

Relevant files: [manifest](../clients/browser-extension/manifest.json),
[browser entry](../clients/browser-extension/service-worker.js),
[HTTP client](../clients/browser-extension/lib/api.js),
[menu](../clients/browser-extension/lib/menu.js),
[feedback adapter](../clients/browser-extension/lib/action.js),
[HTTP surface](../src/unimem_api/app.py).

## Official documentation checked on 2026-09-29

- [Zen extensions](https://docs.zen-browser.app/user-manual/extensions): Zen is
  Firefox-based and installs extensions from Mozilla Add-ons. This does not
  establish compatibility of UniMem's current manifest.
- [MDN background manifest](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background):
  Firefox uses background scripts/pages, not extension service workers. MDN
  describes a combined `scripts` + `service_worker` MV3 manifest for compatible
  versions, and event-page globals do not survive unloading.
- [MDN cross-browser guide](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Build_a_cross_browser_extension):
  namespace, async contracts, API coverage, background environments and packaging
  are separate portability concerns. Renaming `chrome` to `browser` is insufficient.
- [MDN menus.create](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/menus/create):
  returns an item ID, not a Promise; completion errors use a callback and
  `runtime.lastError`. Stable IDs registered in `onInstalled` fit event pages.
- [MDN activeTab](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/permissions#activetab_permission):
  toolbar/menu gestures grant temporary tab access. The current source's claim
  that a popup necessarily requires standing site permission is too broad;
  verify the chosen interaction instead of adding blanket host access.

Preferred candidate: one compatible manifest and shared native ES modules,
with an event-page entry for Zen/Firefox and the existing service-worker entry
for Chromium. Validate that candidate against the actual installed browsers.
If runtime or packaging constraints require different manifests, keep small
static manifests over the same source tree. Do not maintain two extension
implementations or add a framework/bundler merely to promise portability.
Documentation version thresholds are not tested-browser versions.

## Next browser slice: required gates, not current implementation

First review and protect the local API before adding any browser YouTube route:
explicit local pairing/authorization, safe credential storage, Host/Origin and
request validation, bounded bodies/requests, and rejection of unauthorized
webpage callers. Choose the smallest mechanism supported by a concrete threat
model; CORS alone is insufficient. Keep any pairing secret out of injected
scripts, URLs, page DOM and logs. Check existing upload/read/write routes too;
protecting only the new route would leave the stored material exposed.

The subsequent action sends the exact user-selected video's URL to the shared
application service. Server acceptance is durable before the UI reports
“accepted”; accepted work must survive popup closure and background unload.
Status remains server-owned. Provide a way to rediscover the accepted capture
and show pending, processing, complete, failed, partial (if supported), and
unknown/unreachable truthfully. An ambiguous connection failure is not success.
Do not invent `202` or a queue as a side effect of porting the browser.

The smallest candidate UI is an explicit YouTube action in the existing action
menu plus persistent access to status, preserving left-click selection and
whole-page capture. Decide whether that is sufficient before adding a popup.
If a popup is used, it only submits/observes; closing it never cancels accepted
server work. Keeping a minimal capture reference locally may be necessary for
rediscovery, but it is not a second lifecycle database. Such a change needs an
explicit extension to ADR-012 and invariant 18's current no-local-state rule;
HTTP/auth/acceptance changes similarly require an ADR against ADR-011/013.

### Local installation and reproducible manual smoke

**Current result: Zen runtime acceptance NOT RUN.** `zen`, `zen-browser`, and
`firefox` were not found on this environment's `PATH`. Chromium is on `PATH`
but was not launched or used for acceptance. No tested browser version is
recorded. If Zen cannot be provided for the next slice, mark its runtime gate
`BLOCKED` and leave support unclaimed; Firefox results remain separate evidence.

After the browser implementation exists, run locally on Linux:

1. Record OS, exact commit, extension version, Zen version from About/support,
   its Firefox/Gecko base, and installation mode. Use a separate test profile,
   synthetic captures and a non-vault data directory. Start the documented
   local server and pair it using the implemented procedure.
2. **Development only:** try `about:debugging` → this browser → **Load Temporary
   Add-on**, selecting the compatible `manifest.json`. This is Mozilla's
   [temporary installation procedure](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/),
   to verify in Zen, not an instruction claiming the current manifest works.
   Temporary installation ends at browser restart and does not reproduce all
   installation-time permission prompts. Record actual Zen UI differences.
3. **Daily use, separate gate:** prepare and test a Mozilla-signed extension
   through AMO or a signed self-distributed XPI, with the required add-on
   identity/metadata. See [signing and distribution](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/).
   Verify installation, permissions, restart persistence and update in Zen.
   Do not disable signature protection or call an unsigned temporary load a
   distribution. Publishing/signing submission is separate from this plan.
4. Execute the matrix below against the same commit/server data; record capture
   IDs and sanitized observed statuses, never cookies or secrets.

| Scenario | Required observation | Current result |
| --- | --- | --- |
| Load/reload/start browser | Background modules start; action/menu register once; no unhandled API errors | NOT RUN |
| Capture video A with video B in another tab; switch tabs during work; navigate within YouTube | Only the URL selected at the gesture is captured | NOT RUN |
| Available captions | Accepted → truthful status → persisted result and readable Markdown | NOT RUN |
| Captions unavailable, invalid URL, API stopped, authorization/permission denied | Understandable failure/unknown state; no false “saved”, no credential fallback | NOT RUN |
| Close popup if present, close source tab, unload background after server acceptance | Server work remains; reopen status using the same capture identity, no duplicate submission | NOT RUN |
| Lose response; restart server | Durable state remains inspectable; recovery follows the declared contract, not automatic-success assumptions | NOT RUN |
| Existing selected text and whole-page capture; internal/restricted page | Original flows work; prohibited reads fail locally | NOT RUN |
| Unpaired caller, disallowed Host/Origin, malformed/oversized request | Local API denies safely; no new work or stored content disclosure | NOT RUN |
| Signed install and browser restart | Daily-use artifact remains installed with working permissions/action/status | NOT RUN |

Run separate Firefox and Chromium regression rows with their actually tested
versions. They cannot change a Zen `NOT RUN`/`BLOCKED` row into `PASS`.

## Later GitHub scenario

Open a public repository → invoke UniMem → choose README, the open file, a
selected code fragment, or explicitly selected documents/files → receive
Markdown usable in Obsidian and by models. A generic DOM snapshot remains a
separate **page capture** mode; it is not repository import.

Prefer original file content over GitHub UI HTML. Resolve the chosen ref once
to a commit SHA; read every selected file from that fixed commit. Preserve
owner/repository, file path, original ref, actual SHA, source/permalink and,
for a fragment, its selected range. Preserve code indentation/language fences
and Markdown structure; route PDF or other formats to their appropriate
processing capabilities. Record unavailable content, skips and partial results.

Bound file count, total bytes and network requests before acquisition. Never
download the entire repository without explicit selection. A Markdown index
linking notes for selected files is a valid output, without a giant monolith.
Start with public repositories; private access requires a separate explicit
connection with minimal read-only rights, never browser cookies or covert
credential extraction. Imported README, AGENTS.md, CLAUDE.md and all other files
are untrusted source content, not converter/model instructions. Never execute
their code, install dependencies, or run commands found in them.

No GitHub connector, API calls, authorization implementation, repository
ingestion, registry, or GitHub-only schema fields belong in the current PR.

## Verification and handoff

The first PR must prove browser-free acquisition/normalization with offline
fixtures; immutable original, source and provenance persistence; restart and
re-export; explicit failures and no false completion; and preservation of
existing non-YouTube rendering/contracts. Run focused checks first, then the
repository's lint, formatting, strict typing and coverage gates (90% floor).
Any live caption smoke is separate, public, explicitly invoked and reported as
observed evidence; fixture tests do not prove live availability.

Final implementation reports must separately state: (1) browser-independent
services, persistence and CLI/export results; (2) remaining Zen extension work;
(3) what was actually checked in Zen, including version, or `NOT RUN`/`BLOCKED`.
Review scope before handoff: first PR contains no extension port or GitHub
implementation. No auto-merge and no unverified readiness claims.
