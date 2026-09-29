# B2/B3 browser delivery — verification record

Checked 2026-09-29/30 on Linux. B2/B3 implementation is complete; B4 is partial.
No production Python changes, signature bypass, AMO submission, publication,
merge, Obsidian import or paid provider call belongs to this change.

## Base and reproducible artifact

PR #32 was **merged**, with head
`88be1d80c8b3adbc642d9c5cabd4d9a952b78765`.
The clean branch `feat/zen-browser-youtube-capture` started from fetched main
`23fdf168529fb3f07c134e285c1b337f662e1e3a`. The PR base is `main`.
Final commit/PR/hosted CI status is recorded in the PR handoff, not inferred from
local tests.

Version: **0.3.0**. Dev ZIP:
`clients/browser-extension/dist/unimem-browser-0.3.0-dev.zip`.

SHA256:

```text
84dbe8a5e36af386c4cdbe7faabf8d43e619704964fc87a23bf8ff78bf6a39b7
```

`npm run build --prefix clients/browser-extension` creates the ZIP, SHA sidecar
and `dist/unpacked`. Two builds must have identical hashes. The allowlist check
compares all 19 shipped files to source and verifies the unpacked tree too.
No tests, token, profile, server data, dependency tree or absolute source path is
in the package. This is an unsigned **development** artifact.

## Automated checks

- Node: **590 tests, 69 suites passed**, zero failures/skips, using ordinary
  per-file Node test isolation. Existing envelope, capture, HTML and bounded
  replay suites remain. Added credential, exact read-only probe, pre-submit
  storage, double-click/concurrent actions, recovery, result validation,
  response size/deadline, denied host permission, menu callback/lastError,
  secret boundaries, Markdown and safe download tests.
- Protected TCP/cross-language/regression checks: **85 existing tests passed**;
  the added browser recovery test passed after fixing a test-only fixed-port
  probe to use `SO_REUSEADDR` (the initial restart hit TIME_WAIT). No server
  security or lifecycle workaround was made.
- The new test imports shipped JS and runs the actual B1 server and worker with
  a synthetic caption provider. Lost POST and GET responses, background
  recreation, explicit retry, 200 replay, 409 conflict, server restart and
  Markdown retrieval are exercised. First phase: **5 POSTs**, comprising two
  original captures, one YouTube submission, same-ID replay and conflict;
  **3 captures and 1 acquisition**. Restored phase: **0 POSTs**, unchanged IDs,
  capture count and acquisition count; Markdown comes from persisted content.
- Ruff, formatting and strict mypy passed. No production Python changes;
  existing full Python/coverage/native CI jobs remain mandatory.
- `web-ext@10.7.0 lint`: **0 errors, 0 notices, 2 warnings**. Gecko intentionally
  ignores `background.service_worker` and uses `scripts`; Android metadata has
  a minimum-version warning (Android is outside this desktop acceptance).
  No lint rule or existing coverage gate was disabled.

Commands for the focused checks are in [BROWSER_DELIVERY.md](BROWSER_DELIVERY.md).
CI additionally builds twice, compares SHA256, verifies package contents, and
requires the new Python/Node acceptance test to run without skips.

## Zen/Linux runtime

Actual installed application: Flatpak `app.zen_browser.zen`, **Zen 1.21.8b**,
Gecko **152.0.6**. Discovery included Flatpak/launcher paths, not only PATH.
Marionette controlled the **real installed temporary WebExtension**, using
separate profiles under `/tmp/unimem-zen/` and synthetic B1 data. This is not
Playwright Firefox emulation and not merely launching a browser.

| Check | Observed evidence |
| --- | --- |
| Temporary ZIP installation; ES modules | PASS: stable add-on ID `unimem@zinverno.github.io`, active extension, shared module background and management page |
| Background/menu lifecycle | PASS: three action-menu items; stopped background resumes; ordinary wake does not duplicate menus |
| Credential UI/read-only connection | PASS: password field clears; server/token/YouTube API individually confirmed; no capture created by check |
| Wrong token | PASS: native UI shows 401 and requests replacement; previous operation survives |
| Denied localhost permission | PASS: native revoke causes visible permission error; access restored through the browser permission prompt |
| Text/page | PASS: actual toolbar selection and whole-page menu each produced authenticated **201** and a persisted capture |
| YouTube action/status | PASS: authenticated **202**, then complete receipt with operation/capture/content IDs |
| Switch tabs and YouTube SPA | PASS: switching after action retains video A; `history.pushState` to video B followed by another menu action records B |
| Close source/UI; unload background | PASS: `backgroundState=stopped` after 35 seconds while add-on remains installed; reopening reads the same operation and result |
| Markdown | PASS: authenticated result GET, text preview, no new acquisition |
| File bytes through downloads API | PASS **with controlled file picker**: native `browser.downloads`, Blob, actual file, complete state and exact equality to B1 bytes |
| Unmodified Linux system save chooser | **NOT VERIFIED**: native saveAs request remained pending in the system chooser; automation did not complete its interaction |
| Restricted-page scripting refusal | PASS: internal page is refused locally; focused automated suites also cover rejected scripting promises |
| Ordinary signed install + full browser restart | **NOT RUN**: no signed package; temporary installation cannot establish this gate |

Detailed synthetic run: operation `8941fe15-c690-4acd-8b9d-a9e55fc12526`, capture
`b9fd6a99-2373-4f6a-bc06-189622f39cc3`, content
`d5783c2d-de35-4716-8a6d-025df73e33eb` remained identical after background stop.
The exported file had **1205 bytes**, SHA256
`fe86957839c6fee8b706be97ea8a16646cbf0e37a87f17a2660aa368d7cb777d`, and equalled the
complete server response. The controlled chooser replaces only the test
browser's path-selection component; HTTP, extension code, Blob, downloads and
filesystem write are real. It does not prove interaction with the normal GUI
chooser. No chooser override is shipped.

The final ZIP with the SHA256 above was installed again and passed protected
connection and actual text/page actions. Reusing an older accepted reference
against a different test data directory showed not-found without a new POST.
Only the explicit **Сохранить заново** action created operation
`dced2c32-1fd4-48e1-a17b-95dd2b1c5616`. Its final-package preview/download passed:
1205 bytes, exact B1 equality, SHA256
`9aa35f9b51e5f951689187d7dc7cea5220e7646939844a8af54267eaf1f874df`.
The source/recovery counts below belong to the earlier isolated source-switch
run; later deliberate smoke captures are not folded into those counts.

Source-switch run ended with two deliberate YouTube operations (A complete,
B failed against the single-video synthetic fixture), **2 acquisition calls**,
and **3 total captures** (text, page, A). Reopening A and downloading did not
submit it again. B's failure was not automatically retried.

The real B1 ASGI app was additionally wrapped in test-only metadata tracing:
POSTs carried the extension Origin and bearer authorization; reads were also
authorized. Four intentional POSTs returned 201, 201, 202, 202. **No OPTIONS
preflight occurred in that Zen run**. Auth/CORS/Origin policy was not changed;
B1 security tests cover rejected origins and preflight policy. Trace omitted
header values, bodies, credentials and upstream responses.

## Other browsers and live source

| Platform | Version | Evidence |
| --- | --- | --- |
| Chromium/Linux | 150.0.7871.128 | PASS for actual unpacked installation via official CDP `Extensions.loadUnpacked`, module service worker, Russian management UI, session credential, protected readiness and rejection of malformed internal message. Full interactive toolbar/export acceptance NOT RUN. |
| Standalone Firefox/Linux | Not installed in checked PATH, common installation/launcher locations or Flatpak list | Runtime **NOT RUN**. Gecko manifest lint and Zen runtime are separate evidence, not a standalone Firefox pass. |
| Live YouTube captions | — | **NOT RUN in this browser slice**. Browser source URLs were real YouTube pages, but acquisition used an explicit synthetic provider. Previous A4 live evidence is historical and is not relabelled as a browser live test. |
| Signed Gecko distribution | — | **NOT RUN**. Stable ID and metadata prepared; signing/publication not requested or performed. |

The first Chromium command-line load did not expose an add-on worker; that
launch was not counted as acceptance. The subsequent official CDP load actually
returned an enabled extension and a running extension service-worker target.
See [CDP Extensions](https://chromedevtools.github.io/devtools-protocol/tot/Extensions/).

## Remaining acceptance and operating limits

B4 stays open for the normal Linux file chooser, full standalone Firefox and
Chromium user flows, fresh live-caption browser evidence, and signed installation,
restart/update testing. Follow the explicit separate procedures in the
[browser guide](BROWSER_DELIVERY.md); do not disable signature verification.

Credential defaults to the current browser session. Opt-in permanent storage is
unencrypted; Chromium's access-level API is not claimed to exist identically in
Gecko. Local history is capped at 50 references with explicit deletion and no
automatic eviction. YouTube recovery is by saved operation ID; text/page capture
retains its existing in-flight bounded replay, without new unload recovery.
Responses are limited to 8 MiB and 20 seconds; oversized results fail explicitly.
There is manual refresh and no polling/keepalive. No server cancellation/deletion
API or Obsidian import is invented.
