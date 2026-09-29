# ADR-026: Protected browser delivery and local operation references

Status: accepted for B2/B3. Extends ADR-012/015 and invariant 18 only as stated
below; ADR-013's synchronous text/page semantics and ADR-025 remain unchanged.

## Decision

Zen/Linux is the primary target. One MV3 manifest loads the same native ES module
as a Gecko event page (`background.scripts`) and Chromium service worker.
Listeners register synchronously on every startup. `onInstalled` serially replaces
three stable action-menu IDs; normal background wake never recreates menus.
The only adapter difference is `browser ?? chrome` and the callback/lastError
contract of `contextMenus.create`. No framework, polyfill or runtime dependency.
Desktop minimums are Gecko 140 and Chromium 121 (tested versions are reported
separately). Stable Gecko ID: `unimem@zinverno.github.io`.

Left-click still saves the selection; the original whole-page menu saves the
unaltered top-level DOM serialization. Add “YouTube → Markdown” and “Открыть
UniMem”. The latter opens `manage.html`, also the options page. No popup is
needed. Earlier ADR wording that popups cannot receive activeTab is corrected:
keeping one-click selection is the reason for not adding a popup.

The fixed API origin is `http://127.0.0.1:8765`. Only background performs HTTP.
Both old capture flows use the same authenticated, bounded transport. No token
means settings, never anonymous delivery. Fetch omits ambient credentials and
referrer, rejects redirects, checks host permission, limits complete response
bytes to 8 MiB and requests to 20 seconds (including body consumption). Oversize
responses fail, never truncate. Old flows retain at most two identical POSTs and
one GET, require 200/201 plus complete evidence, and still reject 202.

A password field on the trusted options page writes the operator-entered B1 token
directly to extension storage; no token-bearing runtime message exists. Default
is `storage.session`; explicit “Запомнить” uses `storage.local`, with an unencrypted
storage explanation. Neither history nor token uses sync. Chromium's supported
`setAccessLevel(TRUSTED_CONTEXTS)` is awaited; Firefox's storage API does not
provide the same local restriction. Session storage is trusted-only by default.
No content scripts, external messages or web-accessible management page are
exposed. Internal messages check exact management URL, extension ID, top frame,
operation name, ID syntax and allowed fields. Requests cannot name arbitrary URLs.

Connection testing performs GET /health and a GET for a new probe operation ID.
Only the exact JSON `404 operation_not_found` proves authentication and YouTube
route availability. Generic 404/HTML/invalid JSON cannot pass. Probe does not
create records; it says nothing about live caption availability for a video.

## Local references, not lifecycle

`storage.local.youtubeJobs` holds at most 50 references: operation ID, canonical
URL frozen from the acted-on tab, ordered languages, fixed connection origin,
creation time, pre-submit delivery marker, confirmed acceptance, projected last
server receipt, observation time and safe observation error. No transcript,
Markdown, entire ContentObject or credential is stored in this history.

The background is the only history writer. Mutations serialize, and concurrent
identical actions share a promise. The operation ID and reference commit locally
before any POST; a second uncertainty-marker commit precedes the actual send.
Storage failure aborts submission. Nothing is automatically evicted. At capacity,
new captures stop; explicit local-link deletion explains that server work/data
are unaffected. Corrupt storage fails closed and is not silently reset.

Repeated menu selection of the same canonical URL/languages opens its existing
reference, including after background eviction. “Сохранить заново” is the explicit
new-ID action. An uncertain response triggers at most one read. Explicit “Повторить
доставку” first reads the same ID; only typed not-found on a never-confirmed record
permits resubmission with identical parameters. Previously accepted-but-missing
records never resubmit automatically (the server data directory may have changed).
Startup and reopened UI perform reads, never acquisition/replay. No alarms,
keepalive, polling, client worker, server lock manipulation or browser lifecycle
state is added. Manual status refresh is sufficient for this slice.

Validate 202 and 200 receipts identically: request identity/source/language order,
known states, timestamps, result availability, actual IDs and terminal-field
consistency. Network/401/offline/429 errors preserve the last successful receipt
and observation time. Failed/interrupted operations require an explicit new ID
for new acquisition. Completion and Markdown availability are independent.

The selected operation reads its authenticated `/markdown` endpoint. Display is
plain `textContent`, never HTML. Export creates a Blob in the trusted management
page, requests a save dialog with a safe operation-derived filename and
`conflictAction: uniquify`, and revokes the URL on completion/error. Capture state
is unaffected by failed/cancelled export. There is no vault destination or
Obsidian success claim. Text/page receipts remain transient across background
unload; recoverability added here applies to YouTube only.

## Distribution and evidence

`web-ext@10.7.0` is a pinned development dependency only. The deterministic ZIP
builder allowlists shipped files and fixes ZIP metadata; tests/secrets/profiles
are excluded. Temporary loading, background eviction while installed, and signed
installation plus browser restart are separate gates. No signing submission,
publication, signature-check bypass or release belongs here.

Primary sources checked 2026-09-29:
[Zen extensions](https://docs.zen-browser.app/user-manual/extensions),
[MV3 background](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background),
[cross-browser extensions](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Build_a_cross_browser_extension),
[storage](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/storage),
[menu callback](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/menus/create),
[data transmission metadata](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/),
[signing](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/).
Website content, selected source URLs and explicitly supplied authentication
information go to the user's local service, so metadata does not claim `none`.
There is no history tracking, telemetry or remote service connection.

Reproduction and separate runtime evidence: [browser guide](../BROWSER_DELIVERY.md).
