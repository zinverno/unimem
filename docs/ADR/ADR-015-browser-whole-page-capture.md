# ADR-015: Whole-page browser capture, on a right-click and one new permission

Status: accepted (Macro Phase 2, PR 2). Macro Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No change under `src/`: no contract, enum, route, response shape, lifecycle,
processor, replay, or persistence change. The canonical schema stays `0.2`; the
*extension* moves to `0.2.0`, which is an unrelated number that happens to look
alike.

## Context

[ADR-014](ADR-014-html-webpage-ingestion.md) made HTML ingestion real. A
`webpage` `CaptureEnvelope` carrying `payload.html` becomes an immutable HTML
original and a deterministic canonical `web` `ContentObject`, through the
existing lifecycle, on the existing route. It closed by saying the obvious next
move was deliberately not in it:

> Whole-page browser capture is the obvious next product move and is deliberately
> *not* in this PR. This one proves the server-side capability first; a connector
> that captures pages nothing can process would be the wrong order.

This is that next move, and it is only about **acquisition**. The server already
accepts and processes exactly the envelope this PR learns to produce, so the
whole question is one the browser asks:

> How can the existing browser extension let the user deliberately save the
> current page as HTML while preserving the existing one-click selection
> capture, without gaining persistent access to every website?

The three constraints in that sentence are what make it interesting.

**Preserving one-click selection capture** rules out the obvious UI. A popup is
the standard way to offer an extension two actions, and
[ADR-012](ADR-012-browser-selection-connector.md) already rejected it once: a
declared `default_popup` *replaces* `chrome.action.onClicked`, so adding a popup
would delete the working selection gesture and, with it, the click that grants
`activeTab`.

**No persistent website access** rules out the other obvious answer. Reading a
page's HTML looks like it needs `<all_urls>`, or a static content script, or
`tabs` — each of which is standing read access to every page the user ever
visits, granted permanently, in exchange for something they asked for twice a
week.

## Decision

**Left-click the toolbar icon saves the selection. Right-click it and choose
"Save whole page to UniMem" saves the page.** One `chrome.contextMenus` item on
the `action` context, and `contextMenus` is the only permission added.

```
left click the icon                  ->  window.getSelection()
                                     ->  TEXT envelope     (payload.text)
                                            \
right click the icon                         \
  -> "Save whole page to UniMem"              >-> POST http://127.0.0.1:8765/v1/captures
  -> document.documentElement.outerHTML      /        -> modality-specific processor
  -> WEBPAGE envelope (payload.html)        /         -> ContentObject
```

### The gesture is the permission

Executing a context-menu item is an explicit Chrome user gesture on the
extension's own icon, and it grants `activeTab` for that tab, for that gesture —
exactly as a toolbar click does. That is the whole security argument, and it is
why the manifest's permissions become:

```json
"permissions": ["activeTab", "scripting", "contextMenus"],
"host_permissions": ["http://127.0.0.1/*"]
```

`contextMenus` grants the right to put an item in a menu. It grants access to no
website, no host, and no page content; it is the cheapest permission in the
manifest and the only one this PR spent. `host_permissions` is **unchanged** —
the extension can now serialize an entire document and still holds standing
access to exactly one host: the local API it posts to. There is no `<all_urls>`,
no `tabs`, no `storage`, no `notifications`, no `webRequest`, no `cookies`, no
clipboard, no `pageCapture`, no `tabCapture`, and no `content_scripts`.

The item is registered on the **`action`** context and nothing else. A `page` or
`selection` context entry would put UniMem in the right-click menu of every page
the user ever opens — a much louder presence than this connector has earned, and
not what makes the capture possible.

Registration happens once, from `chrome.runtime.onInstalled`. Chrome keeps
registered menu items for the life of an installation, so creating the item when
the service worker *wakes* would try to re-create an id that is already taken.
`createWholePageMenu` is also terminal by construction: nothing awaits an
`onInstalled` listener, so a refusal is absorbed rather than left as an unhandled
rejection.

### What the snapshot is — and what it is not

The injected function is one line, and it is the entire acquisition:

```js
export function readPageHtml() {
  return document.documentElement?.outerHTML;
}
```

**This is a capture-time serialization of the browser's current top-level light
DOM.** It is what the document looked like at the moment the user chose the menu
item, including every mutation page script had already made, and it differs from
the bytes that came off the network because the browser parsed the document and
then serialized it back.

It is therefore **not**:

- the original HTTP response body,
- "page source",
- an exact server response.

It does **not** include:

- a doctype, unless one is naturally part of what this serialization returns —
  and `document.documentElement` is an *element*, so it is not. **No doctype is
  synthesized.** Prepending a guessed `<!doctype html>` would make the submitted
  snapshot claim to be a document it never received, and building a serializer to
  do it properly is not this PR's job;
- shadow DOM,
- canvas pixels,
- iframe document contents (no `allFrames`, and no frame is entered),
- external stylesheet contents,
- image bytes,
- network responses,
- browser-generated visual state.

The injected function does not fetch `location.href` or `document.URL`, call any
network API, read cookies, `localStorage`, or `sessionStorage`, inspect the
selection, read forms specially, walk shadow roots, gather resources, or fetch
CSS, images, or scripts. It returns HTML and nothing else, and it knows nothing
about UniMem — everything it can reach is already the page's own.

**The server's "exact raw HTML" invariant is narrower than it sounds, and this
ADR states the true version:** *exactly the HTML string this connector submitted
is what is preserved afterwards.* It is not a claim about the page's original
source, and nothing here attempts to reconstruct one.

### Exactness, from the page to the store

The string `executeScript` returns travels to `payload.html`, through
`JSON.stringify`, to the server, unchanged. It is not trimmed, Unicode
normalized, line-ending normalized, re-serialized, or bracketed with markup.
`trim()` appears exactly once, as a *question* — is this snapshot blank? — and
its result is never what gets submitted. The acceptance test hashes the
submitted string's UTF-8 and compares it to the digest the durable
`CaptureRecord` names.

### The capture id is minted last

Three refusals, in this order, all local and all before any request:

1. **The URL is judged first.** Only `http:` and `https:` pages, reusing the
   existing `isCapturablePage` rather than inventing a second URL policy —
   "which pages will UniMem touch?" is one question with one answer.
   `chrome://`, `edge://`, extension pages, `file:`, and a missing or malformed
   URL fail locally: no injection, no capture id, no API request.
2. **The page is read second**, and injection failure is refused here.
3. **The snapshot is checked third.** `undefined`, `null`, a non-string, an empty
   string, and a whitespace-only string all mean the snapshot is unavailable.

**Only then** are `crypto.randomUUID()` and the clock read. A page that could not
be read leaves no capture on the server that the user never got, and no malformed
`WEBPAGE` envelope is ever sent.

One new outcome, `page_capture_failed`, covers both ways a page read comes back
useless. It exists because the existing vocabulary cannot express this honestly:
`blank_selection` renders as "select some text first", which would be a lie about
what the user did.

### The envelope

Schema `0.2`, and every field of it already existed:

```json
{
  "schema_version": "0.2",
  "id": "<crypto.randomUUID()>",
  "source": { "type": "browser", "provider": "unimem-browser-extension", "url": "<tab URL>" },
  "payload": {
    "type": "webpage",
    "mime_type": "text/html",
    "html": "<exact document.documentElement.outerHTML>",
    "title": "<tab title, only when nonblank>"
  },
  "context": { "captured_at": "<UTC ISO timestamp>", "application": "unimem-browser-extension" },
  "intent": { "action": "save" }
}
```

`payload.text` and `payload.file_ref` are **absent**, deliberately rather than by
omission: `CaptureIntake` refuses a webpage envelope carrying `html` alongside
either of them rather than choosing which original to store, so a connector that
"helpfully" also sent the visible text would make every page capture a `422`.

**The tab title is submitted metadata, and only when the tab has one.** It is
preserved exactly — `trim()` is only the blank question again — and omitted
entirely when blank. The extension never extracts `<title>` from the HTML, never
infers a title from the URL, hostname, or an `<h1>`, and never fabricates one.
When the payload carries no title the server's existing precedence rule takes
over and reads the document's own `<title>`; doing it in two places would mean
two answers to the same question.

**`source.url` remains metadata.** The extension never fetches it, and neither
does the server.

### Nothing about the network changed

`sendCapture` is reused as-is. There is no second HTTP client, and the fixed
destination is still `http://127.0.0.1:8765` — a module constant that is never
derived from the page URL, the tab, the title, or the snapshot. A page that could
redirect the capture somewhere else would turn this connector into an
exfiltration tool.

All of [ADR-012](ADR-012-browser-selection-connector.md)'s and
[ADR-013](ADR-013-completed-capture-replay.md)'s bounded semantics hold
unchanged: an explicit HTTP response is never blindly retried, one network-layer
failure buys one resend of the identical envelope, the hard bound for one gesture
is **two POSTs and one GET**, and there is no loop, no backoff, no timer, no
persistent retry state, no new UUID, and no restamped `captured_at`.

### Webpage replay is still absent, and that is survivable

Completed replay remains `TEXT`-only. **`replay.py` was not generalized**, and
generalizing it is a separate requirement with its own question about what an
"equivalent" resubmission of a page even is.

So the lost-response path for a page resolves differently from a selection's, and
honestly:

```
POST  -> reaches the server, which completes the capture
      -> the reply is lost; the client knows nothing
POST  (same envelope, same id)
      -> 409 capture_already_exists, because webpage replay is not implemented
GET   (observational, reads and never writes)
      -> COMPLETE
      -> confirmed success: OK, "saved (confirmed after a network error)"
```

That is acceptable, and it is proven end to end against the real server rather
than asserted. **The `409` itself is never treated as success** — the connector
goes and looks, and reports what the server says. If the first attempt never
reached the server at all, the second POST wins normally and returns `201`.

Changing server semantics to turn this into a `200` was rejected: it would be
shipping an idempotency claim nobody designed, in a PR about the browser.

### Selection capture is untouched

The left-click path is functionally identical. It still reads
`window.getSelection()?.toString()`, still refuses blank selections locally, still
preserves the selected text exactly, still builds a `TEXT` schema-`0.2` envelope,
still mints one id per click, still uses the same bounded network handling, and
still scopes its feedback to the clicked tab. Selection capture does not call the
page reader, and whole-page capture does not call the selection reader; the flows
live in separate modules (`lib/capture.js`, `lib/page.js`) sharing only the URL
policy and the HTTP client.

### Feedback

The existing badge system, unchanged: `...` while sending, `OK` only for a
confirmed `COMPLETE`, `!` for everything else, always scoped to the tab that was
acted on. A successful page capture reads `UniMem: saved`, exactly as a selection
does. A page that could not be read reads `UniMem: could not read this page` —
generic on purpose. Page HTML, selected text, stack traces, and internal
exception messages never reach the badge or the title.

## Consequences

Positive:

- **The product's second capture is real.** Right-click, choose, and a durable
  canonical `web` `ContentObject` is on disk, through the same pipeline as
  everything else.
- **It cost one permission, and that permission grants no page access.** The
  extension can serialize a whole document and still has standing access only to
  loopback.
- **The server needed no change at all.** ADR-014 built the capability first, and
  this PR is the evidence that it was built at the right shape.
- **The generic bounded recovery survived a modality it was not written for.** A
  lost webpage response resolves through `409` + one GET, with one capture, one
  content object, and one raw original — proven against a live server.
- **The snapshot is described honestly.** Nothing in the code, the docs, or the
  tests calls it page source.

Costs:

- **The right-click gesture itself is not covered by an automated test.** The
  flow, the builder, the client, the menu registration, and the extension's live
  registration in Chromium are all verified; dispatching a real toolbar
  right-click needs browser automation this PR deliberately does not add. It is a
  manual check — see the checklist in the README — and it is not claimed as
  passing here.
- **A JS-heavy page is captured as it currently *is*.** For a single-page app
  this is usually what the user wants and occasionally a surprise; it is
  documented rather than hidden.
- **A page whose content lives in iframes captures only the shell.** Reaching
  into frames needs permissions this connector will not take.
- **No styling, images, or scripts are preserved as resources.** The stored
  original references them; it does not contain them.
- **A lost webpage response costs an extra round trip** compared to a selection's,
  because replay does not cover it yet.

## Alternatives rejected

- **A popup with two buttons.** Deletes `chrome.action.onClicked`, and with it the
  gesture that grants `activeTab`. It would trade a working one-click capture for
  a two-click one *and* need standing page access.
- **A second toolbar action.** MV3 allows one action per extension.
- **A page-context or selection-context menu item.** Puts UniMem in the
  right-click menu of every page the user opens, for no capability the action
  context does not already provide.
- **`<all_urls>`, `tabs`, or a static content script.** Permanent read access to
  every page, to replace a gesture that already grants exactly enough.
- **A keyboard shortcut instead.** Not discoverable, and `commands` would be
  another permission for a strictly worse discovery story.
- **Synthesizing a doctype.** Makes the snapshot claim to be a transport document
  it is not.
- **Building a real DOM serializer** (doctype, shadow roots, inlined resources).
  A whole subsystem, in a PR whose job is acquisition; `outerHTML` is what the
  browser already computed.
- **Capturing iframes with `allFrames`.** Several documents, one raw original,
  and a permissions story this connector will not buy.
- **Fetching the page URL from the extension.** The connector would become an
  HTTP client for arbitrary origins, and the result would be a *different*
  document from the one the user is looking at.
- **A screenshot.** A different modality with no processor behind it.
- **Also sending the extracted text alongside the HTML.** `CaptureIntake` refuses
  it, correctly: one capture holds one raw original.
- **Extracting `<title>` in the extension.** Two answers to a question the server
  already answers.
- **Generalizing replay to webpage in this PR.** A separate design with its own
  equivalence question; the `409` + GET path is honest and bounded meanwhile.
- **Schema `0.3`.** Nothing changed shape.
- **A queue, history UI, `chrome.storage`, or background sync.** The server is
  still the only record of what was captured.
