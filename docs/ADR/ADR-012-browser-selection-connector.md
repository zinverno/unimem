# ADR-012: A browser selection connector, as the first client of the capture API

Status: accepted (Phase 1, PR 2). Phase 0 remains
[closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed);
[ADR-011](ADR-011-local-http-capture-surface.md)'s HTTP surface is unchanged.

**Superseded in one respect by
[ADR-013](ADR-013-completed-capture-replay.md).** This ADR's "a capture is
POSTed exactly once, ever" rested on the server having no idempotency, which was
true when it was written. The server now offers a completed-capture replay, so
the connector makes **one** bounded resend of the identical envelope after a
network-layer failure. The sections below are marked where that applies;
everything else here still holds.

## Context

ADR-011 built an API and said, in as many words, that the callers were `curl` and
scripts. It also predicted what would settle the questions it left open: *a real
connector will give us the requirements*. This is that connector, and the first
end-to-end product path in the system:

```
browser selection -> connector -> CaptureEnvelope -> localhost API -> core
                  -> durable COMPLETE CaptureRecord + canonical ContentObject
```

The question is narrow: **how does text a person selected in a browser become a
durable canonical capture, without the connector acquiring powers it does not
need or inventing semantics the server declined to?**

The traps here are different from the server's, because an extension is code
that runs beside every page a person visits.

**Ask for `<all_urls>` and register a content script.** Every extension tutorial
does, and it makes selection capture trivial from any page including iframes.
Then the extension can read every page the user ever opens, whether or not they
ever click it, and the capability is permanent rather than granted per use.

**Fetch from the injected page code**, because that is where the selection
already is. Then the request originates in page-origin territory, where the
extension's host permission does not apply and page script sits next to it — and
the fix for the CORS failure that follows is to weaken the *server*.

**Let the page influence the destination.** A configurable endpoint read from the
page, or a "capture to" hint in a meta tag, is a small feature and turns the
connector into an exfiltration tool.

**Retry the POST.** Networks drop requests, retrying is what clients do. But the
server has no idempotency: a response lost *after* the server committed makes a
same-id retry a `409` and a fresh-id retry a duplicate capture the user never
asked for. The client cannot tell those apart from the outside. *(This is the
requirement ADR-013 was written to answer: the server now tells them apart, and
the connector resends once.)*

**Trim the selection.** It looks like tidying. It is the last place the text can
be silently altered before it becomes durable, and the canonical contracts
deliberately preserve what they are given.

## Decision

**A Chromium Manifest V3 extension in `clients/browser-extension/`,** outside
both `core` and `unimem_api`. No browser abstraction layer, no connector SDK, and
no Firefox or Safari claim: there is one browser, and an abstraction over one
implementation is a guess about the second.

**Capture happens only on an explicit click.** `chrome.action.onClicked` is the
user gesture that grants `activeTab` for the current page, so there is
deliberately **no popup** — a popup replaces that event and takes the gesture
with it, leaving the extension needing standing access instead.

**`activeTab` and `scripting`, and nothing else.**

```json
"permissions": ["activeTab", "scripting"],
"host_permissions": ["http://127.0.0.1/*"]
```

No `tabs`, `storage`, `notifications`, `webRequest`, `cookies`, `clipboardRead`,
`contextMenus`, or `declarativeNetRequest`. No `<all_urls>`. **No static
`content_scripts`** — nothing touches a page until the user asks, and injection
happens programmatically after the click.

Chrome match patterns cannot restrict a port, so `http://127.0.0.1/*` is as
narrow as the manifest can express. The connector's own code closes the gap: the
destination is a module constant, `http://127.0.0.1:8765`, never derived from the
tab, the page, or the selection.

**The service worker performs the fetch; the injected function only reads.**

```
page                selection text only, read by an injected function that
                    knows nothing about UniMem
  |
executeScript result
  |
service worker      the privileged extension origin: builds the envelope,
  |                 performs the fetch
fixed localhost API
```

This is the security decision the rest follows from. The injected function runs
in page-origin territory; the service worker is the extension origin, and it is
the component `host_permissions` was granted to. A test asserts the injected
function's own source contains no `fetch`, no `127.0.0.1`, and no UniMem concept
at all.

**The server's CORS configuration is therefore unchanged**, and
`unimem_api` is untouched by this PR. An extension service worker's fetch to a
host it holds permission for is not subject to the page's CORS rules. This was
not assumed: the extension loads in Chromium 141 with no manifest error and its
module service worker registers and resolves its whole import graph. If a real
end-to-end click on some Chrome version disproves it, that is a report and a
decision — not a speculative middleware added in advance.

**The selection is captured exactly as the page returned it.** `trim()` appears
once in the connector, to answer "is this blank?", and its result is never what
gets sent. Leading and trailing whitespace, tabs, CRLF, combining marks, and
astral-plane characters all survive. A blank or whitespace-only selection is
refused *locally*: no capture id is minted, nothing is POSTed, and no failed
capture is fabricated on the server for a click the user did not mean.

**Selection only.** No whole-page capture, no HTML, no Readability, no
screenshots, no images, no Markdown conversion, no inferred summaries or tags.
A webpage processor is a later phase, and this connector must not pretend one
exists.

**Top-level document only.** No `allFrames`, so a selection inside a cross-origin
iframe is out of scope. Reaching one costs `<all_urls>`, which is exactly the
trade this design refuses.

**Only `http://` and `https://` pages.** A click on `chrome://`, `edge://`, an
extension page, `file://`, or any page that refuses injection fails locally and
visibly, and sends no HTTP request.

**The connector builds the canonical `CaptureEnvelope` itself,** schema `0.2`,
with no contract change of any kind. Every value it uses already existed:
`browser` is a `CaptureSourceType`, `text` a `CapturePayloadType`, `captured_at`
is required, `title` belongs on `CapturePayload`, and `save` is an
`IntentAction`. **The page URL and title are capture metadata; the selection is
payload data.** Neither ever influences where the request goes.

A blank page title is **omitted**, never replaced with the URL, the hostname, or
the first line of the selection — a fabricated title is indistinguishable from
one the page actually had.

**The capture id is a client-generated opaque `crypto.randomUUID()`,** minted
before the POST. It is not derived from the URL, the text, a digest, or the
clock: those would make two deliberate captures of one passage collide, or leak
page content into an identifier that travels further than the capture does. Each
deliberate click is a new capture event with a new identity.

**A capture is POSTed once, and there is no retry policy.** *(Amended by
[ADR-013](ADR-013-completed-capture-replay.md): a POST that fails at the network
layer — and only that — buys exactly one resend of the identical envelope under
the identical id, because the server now answers such a resend with the capture
it already made. There is still no general retry, no loop, no backoff, and no
queue; the hard bound for one user action is two POSTs and one GET.)*

A success is checked against the contract before it is called success: a
non-JSON body, a `capture_id` that is not the one submitted, a blank
`content_id`, or a status other than `complete` are protocol failures. *(ADR-013:
`200` joined `201` as a success code — it means an equivalent capture was already
complete — and is validated identically. No other 2xx is success; quietly
accepting any 2xx is how a client reports "saved" on the strength of a response
that never said so.)*

A real HTTP failure (`409`, `422`, `500`, `503`) is a definite answer: it is
surfaced with its typed `code` and `message` retained internally, and it is not
retried and not probed. **`409 capture_already_exists` is a conflict, not
idempotent success.** *(ADR-013 adds one exception, and it is not a retry: a
`409 capture_already_exists` on the **resend** is followed by the same single
read-only probe described below, because the id being taken by this client's own
first request is worth one look. It is still never treated as success.)*

**The ambiguous case is resolved by observation, never by re-sending.**
*(ADR-013: the observation is now the **last** step rather than the only one —
the connector resends first, and probes only when the resend was itself lost or
was answered with a conflict it cannot interpret.)* When a POST fails at the
network layer, no HTTP response ever existed and the capture's fate is genuinely
unknown — the id was minted first, so the server may hold a finished capture the
client never heard about. The connector then performs **at most one** read-only
`GET /v1/captures/{id}` on that same id:

| Probe finds | Reported as |
| --- | --- |
| `complete` | success, confirmed |
| `received` / `stored` / `processing` / `failed` | that durable state, not success |
| `404` | could not be confirmed |
| another network failure | service unavailable, outcome unknown |

The probe reads. It never writes, never re-POSTs, and never turns a server
failure into a local success. **The server remains the sole authority on
lifecycle**, and nothing here reconciles, repairs, or mutates a capture — which
is the same rule ADR-011 applied to the HTTP handlers, one layer further out.

**No persistent client state.** No `chrome.storage`, no history UI, no queue, no
local database. The generated id lives long enough for the bounded attempts of a
single click and is then gone; the server is the record. If the service worker
dies in between, there is no durable retry — and none is wanted, because a
completed capture stays replayable across restarts of either side.

**A badge and a title are the entire UI** — `...` sending, `OK` confirmed
complete, `!` anything else. The selected text never appears in either: it is the
user's content, a title is visible to anyone looking at the screen and lands in
screenshots, and it is not logged to the console either. Neither do raw
exception messages or stacks. There is no telemetry and no analytics.

**That feedback is scoped to the clicked tab.** `chrome.action`'s badge and title
are *global* defaults unless a `tabId` is supplied, so one page's `OK` — or `!` —
would otherwise sit on every other tab the user has open, describing something
that never happened there. Both calls carry the clicked tab's id; the only case
that falls back to the global default is a click Chrome hands us no tab for, where
there is nothing to scope to and silence would be worse.

**The click path is terminal.** Chrome does not await the action listener, so
nothing above it can catch a rejection: an unexpected failure would be an
unhandled rejection *and* would strand the badge on `...`. The listener therefore
ends in a `catch` that reports a safe `!` with a generic title, and applying
feedback itself absorbs both the synchronous throw and the asynchronous rejection
that a tab closed mid-capture produces. Every failure — extraction, network,
protocol, or a bug — lands as `!` and a sentence, with no stack and no selected
text.

**The API address is fixed** at `http://127.0.0.1:8765`. No options page, no
settings UI, no environment substitution, no sync storage. The server's
`--host`/`--port` flags still exist; this connector targets the documented
default deployment, and configuration becomes a Phase-1 PR when something
actually needs it.

**Plain ES modules, zero dependencies, `node --test`.** No React, TypeScript
build, webpack, Vite, Rollup, Babel, Jest, Vitest, or ESLint. The directory is
loadable as an unpacked extension exactly as it sits in the repository, which is
also what makes "load unpacked" a two-step instruction rather than a build.

**The contract is checked across both languages by one file.**
`tests/fixtures/browser-envelopes.json` holds the inputs a selection arrives as
and the envelope the connector builds from them. The connector's own test asserts
`buildCaptureEnvelope` reproduces it; a Python test validates the same document
against the real `CaptureEnvelope` and POSTs it through the real local stack.
Neither side can change the shape without the other failing, and no ordinary
core test needs a browser or a Node runtime.

## Consequences

Positive:

- **The product exists end to end.** Select, click, and a durable canonical
  `ContentObject` is on disk. Everything before this was infrastructure.
- **The extension can read a page only when asked.** `activeTab` plus
  programmatic injection means no standing access to anything, and the manifest
  is asserted rather than reviewed.
- **The server needed no change.** No CORS, no idempotency endpoint, no
  reconciliation — the architecture that made those unnecessary is the one that
  puts the fetch in the service worker.
- **The ambiguous-network answer is honest.** The user is told "saved",
  "confirmed after a network error", "still processing", or "could not be
  confirmed" — four different truths, rather than one optimistic guess.
- **The selection survives byte for byte**, verified from the browser's own
  string all the way to the stored `ContentObject`.
- **ADR-011's open questions are now grounded.** Idempotency has a concrete
  motivating case (a lost response on a real user click), rather than a
  hypothetical one.

Costs:

- **A cross-origin iframe selection cannot be captured.** The user gets a blank
  selection and is told to select some text, which is confusing in exactly the
  case where they did.
- **The end-to-end click is not covered by an automated test.** The pure logic is
  fully tested and the extension is verified to load and register in Chromium,
  but dispatching a real toolbar click needs browser automation this PR
  deliberately does not add.
- **A lost response still needs a human sometimes.** When the probe returns `404`
  the user must decide whether to click again, and clicking again creates a
  second capture. Idempotency is what fixes this, and it is not in this PR.
- **The endpoint is hard-coded.** Anyone running the API on another port must
  edit a source constant.
- **One browser.** A Firefox port will need this code to change, and that is the
  accepted price of not abstracting before a second implementation exists.
- **A second runtime in CI.** The repository now needs Node as well as Python,
  though the Python package still depends on neither.

## Alternatives considered

- **A static content script with `<all_urls>`.** Rejected: permanent read access
  to every page in exchange for convenience the click already provides.
- **A popup instead of `action.onClicked`.** Rejected: the popup replaces the
  click event that grants `activeTab`, so the extension would need standing page
  access to do the same job.
- **Fetching from the injected page code.** Rejected: it runs in page-origin
  territory beside page script, does not carry the extension's host permission,
  and the natural "fix" is to add CORS middleware to the server.
- **Adding CORS to `unimem_api` pre-emptively**, so the connector will work
  whatever it does. Rejected: it weakens the server to accommodate a design
  choice already rejected, and no test requires it.
- **A configurable endpoint, or one hinted by the page.** Rejected: the first is
  scope this PR does not need; the second lets a web page redirect a user's
  captures.
- **Automatic retry on failure.** Rejected: without server idempotency, a retry
  is either a spurious `409` or a duplicate capture, and the client cannot tell
  which case it is in. Observing once is the only honest option. *(Revisited by
  [ADR-013](ADR-013-completed-capture-replay.md), which gave the server the
  idempotency this reasoning was missing. A general retry policy stays rejected;
  one bounded resend of the same request does not.)*
- **Treating `409 capture_already_exists` as success.** Rejected for ADR-010's
  reason, now one layer further out: it assumes the benign explanation for a
  conflict that has several.
- **Trimming or normalizing the selection**, or converting it to Markdown.
  Rejected: the connector is the last place the text can be altered before it is
  durable, and the canonical contracts exist to preserve it.
- **Capturing the whole page, or its HTML.** Rejected: that is a webpage
  processor, which does not exist yet, and shipping the capture side of it would
  produce envelopes the pipeline refuses.
- **`chrome.storage` for a capture history.** Rejected: it makes the extension a
  second, weaker record of what the server already holds authoritatively.
- **A browser abstraction layer, or a shared connector SDK.** Rejected: there is
  one browser and one connector. Both are guesses about a second one.
- **Playwright or Puppeteer, to force an end-to-end browser test.** Rejected: a
  browser automation dependency and a bundled browser download, for a connector
  whose every decision is already covered by fast tests. The click stays a
  documented manual verification until something needs more. *(Still rejected
  under ADR-013, which instead runs the connector's own HTTP client against a
  real server on a real socket — no browser required.)*
