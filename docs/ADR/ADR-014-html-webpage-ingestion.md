# ADR-014: HTML webpage ingestion, and the first derived canonical text

Status: accepted (Macro Phase 2, PR 1). Macro Phase 1 is
[closed](#macro-phase-1-is-closed). Phase 0 remains
[closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, enum, lifecycle, or persistence change: the schema stays `0.2`,
and no route, request shape, or response shape moved.

## Macro Phase 1 is closed

Phase 1 built the capture *surface*: an HTTP delivery adapter
([ADR-011](ADR-011-local-http-capture-surface.md)), a browser selection
connector ([ADR-012](ADR-012-browser-selection-connector.md)), and the narrow
completed-capture replay the connector's lost-response problem asked for
([ADR-013](ADR-013-completed-capture-replay.md)). It is closed and stays closed.
Phase 2 does not reopen HTTP transport design, browser selection capture
architecture, or replay.

Macro Phase 2 asks a different question, and it is the first one about
*modality*: **UniMem has ingested exactly one kind of material since Phase 0F —
plain text. What does it take to ingest a second?**

## Context

The system has been able to *describe* a webpage since Phase 0A and has been
unable to *ingest* one ever since. Every piece of vocabulary was already
written down and unused:

```
CapturePayloadType.WEBPAGE  = "webpage"
ContentType.WEB             = "web"
ProvenanceSourceType.HTML   = "html"
CapturePayload.html         : str | None
```

That is not an accident, and it is the reason this PR bumps nothing. Phase 0A
modelled the domain rather than the phase, so the contracts have been sitting
there waiting for a processor. If adding the first real webpage capability
required a new enum member, a new field, or schema `0.3`, the original modelling
would have been wrong. It was not, so **nothing in `src/core/contracts/` changes
here** — the question is entirely one of capability.

The question this PR answers, exactly:

> How does an HTML-backed `webpage` `CaptureEnvelope` become an immutable HTML
> original and a deterministic canonical web `ContentObject`, using the existing
> lifecycle?

The traps around this one are unusually well populated, because "ingest a
webpage" sounds like a much bigger feature than it is allowed to be.

**Fetch the URL.** The envelope carries `source.url`, so the server could go and
get the page. That single line would add an SSRF surface, DNS, redirects, TLS
decisions, timeouts, retries, robots policy, and a fetcher's entire failure
taxonomy — to a build whose whole network story is "a client POSTs to
loopback". It would also be a different feature: fetching is *acquisition*, and
this is *ingestion of something already acquired*.

**Add Readability, trafilatura, or a reader mode.** The tempting answer to "what
is the text of this page?" is "the article, without the chrome". Every one of
those libraries works by heuristic — text density, tag ratios, class-name
denylists — and every heuristic silently *deletes content it guessed wrong
about*. Silent deletion is the worst possible failure mode for a memory system:
the user is told their page was saved, and the part they wanted is gone with no
trace and no error.

**Add a real HTML parser** (lxml, BeautifulSoup, html5lib) or a browser engine.
A dependency is a permanent cost, and it needs a concrete capability the
standard library cannot supply. `html.parser` parses HTML, tolerates malformed
markup, and decodes character references. Nothing in the semantics defined below
needs more than that.

**Store the extracted text as the original.** This is the quiet one, and it
would be the real mistake. If the raw object held the *extracted* text, the
markup would be gone forever, every future improvement to extraction would be
unable to re-run, and the system's central promise — that the original is
immutable and retrievable — would be false for every webpage.

**Widen `CapturePayload` so one processor's limits become the contract's.** The
canonical contract permits a `webpage` payload backed by `text`. It should keep
permitting it. Narrowing the contract to match what one implementation happens
to support would burn a permanent decision to buy a temporary convenience.

## Decision

Add one processor, one intake capability, and nothing else.

```
CaptureEnvelope(payload.type = webpage, payload.html = ...)
    -> CaptureIntake             refuses ambiguous shapes before any side effect
    -> RawObjectStore            the EXACT submitted HTML, UTF-8, immutable
    -> CaptureRecord(STORED)
    -> ProcessorRouter           exactly one match
    -> WebpageProcessor          deterministic text extraction
    -> ContentObject(type=web)   one TEXT segment, one ORIGINAL asset
    -> ContentObjectStore        durable BEFORE complete
    -> CaptureRecord(COMPLETE)
    -> HTTP 201                  the existing response, on the existing route
```

### The supported form is HTML-backed, and the rest are refused, not dropped

This build ingests exactly one webpage materialization:

| shape | result |
| --- | --- |
| `webpage` + `html` only | **supported** |
| `webpage` + `html` + `text` | refused — `UnsupportedCapturePayloadError` → `422` |
| `webpage` + `html` + `file_ref` | refused — `UnsupportedCapturePayloadError` → `422` |
| `webpage` + `text`, no `html` | refused — `UnsupportedCapturePayloadError` → `422` |
| `webpage` with neither | already rejected by the contract's own validator |

The restriction is arithmetic, not squeamishness. **A `CaptureRecord` holds one
raw original reference.** An envelope carrying HTML *and* `text` or `file_ref`
offers more material than the record can hold, so something has to give. The
options were:

1. silently store the HTML and drop the rest — durably discarding submitted
   material while answering `201`;
2. store several originals — a real feature (multi-representation captures) with
   its own contract, identity, and dedup questions, which nobody has asked for;
3. refuse, and say why.

Only the third is honest at this size. It also costs nothing later: the refusal
is an **unsupported-capability** error, not a validation error, so the identical
envelope will be accepted unchanged by a phase that can represent more than one
representation. Nothing about the canonical contract was narrowed to make this
true — `CapturePayload` still permits every one of those shapes, and always
will.

The refusals are settled **before the clock is read, before
`CaptureRecordStore.create`, and before any raw write**, so a refused envelope
leaves no durable state at all — no record, no bytes, no timestamp.

Refusal messages name payload types and *field names*, never field values, so no
submitted markup can reach an error body or a log through this path.

### The raw original is the exact submitted HTML

Intake stores `envelope.payload.html.encode("utf-8")` and nothing else.

No HTML normalization, DOM serialization, whitespace rewriting, entity
rewriting, newline rewriting, charset detection, tidying, title injection, or
URL injection. Not a re-serialized parse tree — the bytes of the string the
client sent. Intake does not parse HTML at all, which is precisely why the
original stays trustworthy: code that never looks cannot "helpfully" alter
anything.

Everything Phase 0B already decided continues to apply unchanged. Identical HTML
bytes deduplicate at the raw-object layer regardless of capture id or payload
type — identity is the SHA-256 of the bytes and nothing else — while different
capture ids remain different `CaptureRecord`s with different `ContentObject`s.

The submitted `payload.mime_type` is passed through to the raw object verbatim,
including `None`. Nothing is sniffed and nothing is invented.

### The canonical text is derived, and never confused with the original

**These are two different things and the system says so at every layer.**

| | raw original | canonical segment |
| --- | --- | --- |
| what | exact submitted HTML bytes | extracted page text |
| mutable | never | regenerable |
| lives in | `RawObjectStore`, reachable via the `ORIGINAL` asset | `ContentObject.segments[0]` |
| provenance | — | `source_type = HTML`, `asset_id` → the original |

`ProvenanceSourceType.HTML` — rather than `ORIGINAL`, which `TextProcessor`
uses — is what carries this distinction on the wire. A consumer reading a
segment knows it is an *extraction whose source markup is still available*,
not the stored bytes themselves. That is what makes the derived text safe to
have: it is reproducible, and the thing it was derived from never went away.

### Extraction semantics, in full

Standard library only: `html.parser.HTMLParser` with `convert_charrefs=True`.
No BeautifulSoup, lxml, html5lib, Readability, trafilatura, browser engine, or
JS DOM.

1. Discard `script`, `style`, `noscript`, `template`, `svg` entirely — start tag
   to matching end tag, nested markup included.
2. Discard ordinary `<head>` text. `<title>` is the one exception and is
   collected separately, never joined into the body. Meta description, keywords,
   link tags, OpenGraph, JSON-LD, and favicon are not read at all.
3. Discard comments, doctypes, and processing instructions.
4. Collect every other text node, collapsing each node's internal runs of ASCII
   formatting whitespace to a single space as it arrives.
5. Emit a blank-line boundary at both the start and the end of any of:
   `article aside blockquote div footer header main nav section p h1 h2 h3 h4 h5
   h6 li ul ol table tr td th pre`. `br` emits a single line break.
6. Normalize by line: collapse whitespace once more across node boundaries, trim
   each line, collapse runs of blank lines to at most one, drop leading and
   trailing blank lines.

Only **ASCII** whitespace is collapsed or trimmed — deliberately not `\s` and
not a bare `str.strip()`, both of which also match U+00A0. A no-break space is a
character the page asked for, usually written `&nbsp;`, not indentation the
author left in the file. Character and entity *meaning* is preserved; source
formatting is not content. There is no Unicode normalization and no case
conversion.

**This is HTML text extraction, not article extraction.** It is explicitly not
reader mode, boilerplate removal, semantic content detection, or DOM fidelity.
Navigation, menus, cookie banners, and footers are text on the page and come out
as text. That is the honest transcription, and it is strictly better than a
heuristic that silently deletes the wrong paragraph.

Deliberate limitations, each recorded as a test rather than left implied:

- **No CSS.** `display: none`, `visibility: hidden`, `content:`
  pseudo-elements, and the cascade are not evaluated. Hidden-by-CSS text is
  extracted like any other text — there is no stylesheet engine and there will
  not be one, because it would need CSS fetched, cascaded and resolved, which is
  a browser.
- **No JavaScript**, so no script-generated content and no shadow DOM. The
  submitted HTML string is the entire source.
- **No `<pre>` whitespace preservation.** `pre` is a block boundary and nothing
  more; its text is collapsed like all other text. Preserving it would mean two
  whitespace policies, and the exact bytes are one asset reference away.

### Strict UTF-8, because UniMem chose the encoding

The processor decodes the stored original as strict UTF-8. It does not inspect
`<meta charset>`, sniff bytes, retry Latin-1 or Windows-1251, or call chardet.

This is not optimism about the web. A page fetched from an arbitrary server
genuinely is a charset problem — but that is not what happened. The HTML arrived
as a JSON string, already Unicode, already decoded by whoever submitted it, and
intake wrote its UTF-8 encoding. **The processor is decoding the encoding UniMem
itself chose**, a closed loop with exactly one right answer.

Undecodable bytes raise the existing `TextDecodingError`. It is reused rather
than twinned: the category is "stored bytes are not valid text in the expected
encoding", the encoding is the same UTF-8, and the caller's decision is the same
one. A parallel `HtmlDecodingError` would be a second name for one fact and a
second row in every error table.

### Title precedence — the first such rule in the system

`TextProcessor` copies the submitted capture title and needs no precedence rule,
because plain text has no competing title source. A webpage does, so this PR
defines the rule:

1. `capture.title` is present → `ContentObject.title = capture.title`, exactly.
2. else the HTML has a nonblank `<title>` → the extracted title.
3. else `None`.

A person who titled their capture said what they wanted it called, and no
extraction outranks that. The `<title>` is the page's own claim about itself and
is the obvious second. There is no third source: **not** the first `<h1>`,
**not** the first line of content, **not** the hostname or URL slug, and
certainly not a model — each of those invents a name the document never carried.

Several `<title>` elements resolve to the **first nonblank one**. It needs no
tie-breaking, documents are read top to bottom, and skipping blanks means an
empty `<title></title>` cannot shadow a real title further down. Surrounding and
internal formatting whitespace is collapsed and trimmed as body text is;
character references are already decoded by the parser.

**The extracted title reaches `ContentObject` and stops there.**
`CaptureRecord.title` means *the title the submitter provided* — Phase 0G's rule,
unchanged — so it is never overwritten with one found in markup. The two fields
mean different things: submitted capture metadata, and canonical content.

### The canonical output

`ContentObject` with `type = ContentType.WEB`, carrying:

- `source` — capture id, and the provider and URL from `CaptureRecord.source`;
- `original` — the original asset's id, the **raw** MIME type as declared
  (including `None`), and the raw SHA-256;
- `assets` — exactly one, `AssetRole.ORIGINAL`, `ref` pointing at the raw store
  reference, `sha256` preserved;
- `segments` — exactly one, `SegmentType.TEXT`, `position = 0`, holding the
  extracted text;
- `provenance` — capture id, `source_type = HTML`, `asset_id` = the original
  asset, `processor = "webpage"`, `processor_version = "0.1"`;
- `processing` — one `COMPLETE` `ProcessingRecord` for `webpage@0.1`.

Content, segment, and asset ids are fresh UUID4s and are **never** derived from
the raw SHA-256: that address identifies bytes, and two captures of identical
HTML are two content objects holding two asset records.

`Asset.mime_type` is required by the contract, so when the raw object declared
none, the asset gets a processor-local `text/html`. That fallback is **not**
written back onto `ContentObject.original`, `RawObjectRef`, or `CaptureRecord` —
a capture that declared no MIME type still looks like one that declared no MIME
type everywhere the submitter's own words are recorded.

### Empty visible content is a processing failure

HTML that decodes cleanly but yields no nonblank text — only `<script>`, only
`<style>`, an empty `<body>`, comments only, whitespace only — raises
`ProcessingInputError`.

A `COMPLETE` content object with an empty segment would be the system reporting
that it remembered something when it remembered nothing, which is the one answer
a memory system must never give. The existing orchestrator rules then apply
unchanged: a `ProcessingError` is a verdict about the capture, so it is durably
`FAILED`, observable at `GET /v1/captures/{id}`, and the exact submitted bytes
remain in raw storage. Nothing submitted is lost by refusing.

### Everything else is unchanged

- **Lifecycle.** `RECEIVED → STORED → PROCESSING → COMPLETE`, or the existing
  truthful failure states. No new status, no new step, no orchestrator change.
- **Routing.** `ProcessorRouter` still requires exactly one match. With two real
  processors registered it is finally doing visible work: `TEXT` reaches
  `TextProcessor` and `WEBPAGE` reaches `WebpageProcessor` because each claims
  its own payload type and refuses the other's — not because of list order.
  There is no first-match behaviour and no fallback processor.
- **`TextProcessor` is untouched**, still `text@0.2`, with identical semantics.
  The small amount of shared shape between the two processors (a UUID minter, an
  original-asset builder, an encoding constant) is deliberately duplicated
  rather than extracted: the codebase already keeps deliberate local twins of
  the clock and the encoding constant across layers, and a shared helper module
  would couple two processors that should be free to diverge.
- **HTTP.** The existing `POST /v1/captures`, the existing `CaptureEnvelope`
  body, the existing `201 {capture_id, content_id, status}`. No `/webpages`
  route, no multipart route, no upload route, no second request DTO.
- **Renderers.** `MarkdownRenderer` and `JsonRenderer` are typed against
  `ContentObject`, not against a modality, so a web object renders under their
  existing generic semantics. A compatibility test records this; no HTML
  renderer was added and no derived Markdown is persisted.

### Completed replay stays TEXT-only

`src/unimem_api/replay.py` is untouched. So:

- the first supported webpage `POST` → `201`;
- an identical same-id webpage `POST` after completion → `409`.

That is intentional. Replay equivalence must be *proven* against the durable
record ([ADR-013](ADR-013-completed-capture-replay.md)), and proving it for a
webpage means comparing the submitted **HTML** against the raw digest — a
different comparison from the text one, with its own question about what an
"equivalent" resubmission of a page even is. Smuggling that into the first
webpage processor would be shipping an idempotency claim nobody designed.

### The browser connector is unchanged

`clients/browser-extension/` has **zero** behavioural changes. It remains
selection-only and continues to emit `TEXT`: no whole-page action, no popup, no
context menu, no second action, no `outerHTML` capture, no new permissions, and
an unchanged manifest.

Whole-page browser capture is the obvious next product move and is deliberately
*not* in this PR. This one proves the server-side capability first; a connector
that captures pages nothing can process would be the wrong order.

> **Since Phase 2 PR 2** ([ADR-015](ADR-015-browser-whole-page-capture.md)): the
> browser can now supply HTML to this pipeline. Right-clicking the extension's
> toolbar icon and choosing "Save whole page to UniMem" submits the current
> top-level document's `outerHTML` as exactly the HTML-backed `webpage` envelope
> described here, on the existing route, with **no change to anything in this
> ADR** — not the intake rule, the extraction semantics, the title precedence,
> the raw-original guarantee, or the `TEXT`-only replay. What that connector
> submits is a capture-time serialization of the live DOM, not the page's
> network source; the invariant this ADR states still holds exactly as written,
> over the bytes the client submitted.

## Security

**This server does not fetch the webpage URL.** `source.url` is metadata that is
recorded and never dereferenced. There is no `requests`, no `httpx`, no fetcher,
no redirect handling, no DNS, no remote resource loading, and therefore no SSRF
surface. The only bytes in the pipeline are the ones the client submitted.

HTML is treated as inert submitted text throughout. Nothing is executed —
scripts, CSS, embedded resources, iframes, and event handlers are text that is
parsed for structure and, in the case of `script` and `style`, discarded
unread. No image, stylesheet, font, or iframe is requested.

Error bodies never carry page content. Intake refusals name field names, the
extraction failure names the capture, and the existing 5xx rows keep their fixed
public messages.

## Consequences

- UniMem ingests a second modality, and the contracts absorbed it without a
  single change — which is the strongest available evidence that Phase 0A
  modelled the domain rather than the phase.
- The raw/derived distinction is now load-bearing rather than theoretical: for
  the first time the canonical segment is *not* the stored bytes, and
  `ProvenanceSourceType.HTML` is what keeps the two from being confused.
- `ProcessorRouter`'s exactly-one-match rule stops being a formality.
- Extraction quality is now a real, versioned surface. `webpage@0.1` is
  deliberately simple; improving it changes the version, and because the
  immutable original is always retrievable, any future version can reprocess
  what this one saw. That reprocessing path is not built here.
- A webpage carrying multiple representations is refused today. That is a known
  gap with a known shape, not an oversight.
- Webpage captures have no replay, so a client that loses a response to a
  webpage POST is in the position the connector was in before ADR-013.

## Alternatives rejected

- **Fetch `source.url` server-side.** A different feature (acquisition, not
  ingestion) and an SSRF surface, in a build whose network story is loopback.
- **Readability / trafilatura / article extraction.** Heuristics that silently
  delete content they guessed wrong about — the worst failure mode available to
  a memory system.
- **BeautifulSoup / lxml / html5lib / a browser engine.** A permanent dependency
  with no capability the standard library lacks for these semantics.
- **Store extracted text as the raw original.** Destroys the markup forever and
  makes the immutability promise false for every webpage.
- **Accept `webpage` + `html` + `text` and keep the HTML.** Durably discards
  submitted material while answering `201`.
- **Restrict `CapturePayload` so the contract matches this processor.** Burns a
  permanent contract decision for a temporary implementation limit.
- **Schema `0.3`.** Nothing changed shape. A version bump with no shape change
  is a migration everyone pays for and nobody needs.
- **Generalize replay to webpage.** A separate requirement with its own
  equivalence question; see above.
- **A `/webpages` route or a second request DTO.** The canonical envelope
  already describes a webpage. A second ingress would be a second contract.
- **Ship the browser whole-page action in this PR.** A connector emitting
  captures the server cannot process is the wrong order.
