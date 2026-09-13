# ADR-018: Opt-in local OCR for scanned PDF pages

Status: accepted (Macro Phase 3, PR 3). Phase 3 PR 1 and PR 2 are
[closed](#phase-3-pr-1-and-pr-2-are-closed). Macro Phase 2 remains
[closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed). Macro
Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, enum, lifecycle, persistence, route, replay, or browser change: the
schema stays `0.2`. **Macro Phase 3 remains open after this PR.**

## Phase 3 PR 1 and PR 2 are closed

[ADR-016](ADR-016-pdf-document-ingestion.md) added `POST /v1/uploads`, the
`file_ref` staging flow, and `PdfProcessor`. [ADR-017](ADR-017-docx-document-ingestion.md)
added `DocxProcessor` and demonstrated that the staging boundary was genuinely
format-independent. Both are closed. This PR does not reopen the upload or
staging architecture, the raw store's identity or layout, PDF or DOCX extraction
semantics, page segmentation, title precedence, webpage or browser semantics,
replay, persistence, or the lifecycle.

`src/core/processing/pdf.py` is **byte-for-byte unchanged** by this PR, and that
is a deliberate constraint rather than an accident. Its public
`extract_pages()` is the only thing this PR reuses from it, called as the
published function it already was — not copied, not forked, and not reached into
for a private helper. A page that carries embedded text therefore reaches a
segment through *exactly* the code path it reaches one through in the default
build, which is what makes "enabling OCR changes nothing about pages that already
worked" a fact rather than an intention.

ADR-016 said, of a scan:

> the exact PDF stays in raw storage either way, so nothing submitted is lost by
> refusing, and an OCR-capable build can process the very same bytes later.

This PR is that build. It is opt-in, and the refusal ADR-016 designed remains the
default behaviour on every deployment that does not ask for it.

## Context

The question this PR answers, exactly:

> How can an explicitly OCR-enabled deployment ingest scanned PDF pages into
> page-aware canonical content, while preserving embedded text, exact originals,
> and the distinction between extraction and recognition?

Three things in that sentence do the work. *Explicitly* — because recognition has
system prerequisites and a quality profile that the default build must not
acquire by accident. *Preserving embedded text* — because a recognizer is strictly
worse than a document's own text stream, and a build that ran OCR over a
searchable PDF would be replacing good data with a guess. *The distinction* —
because a consumer must always be able to tell text a document carried from text
a model inferred from pixels, and no amount of accuracy makes those the same
fact.

## Decision

### The flow is the existing one, unchanged

```
PDF bytes
    -> POST /v1/uploads          (multipart)   -> sha256 file_ref
    -> POST /v1/captures         (CaptureEnvelope naming that ref)
    -> CaptureIntake             -> STORED
    -> ProcessorRouter           -> the one PDF processor this deployment runs
    -> ProcessingOrchestrator    -> a durable DOCUMENT ContentObject -> COMPLETE
```

No new route, no new request field, no new MIME type, no new schema version, no
new lifecycle state, and no new capture status. A client that can submit a PDF
today can submit a scan to an OCR-enabled deployment with the same request body.

### Recognition is a deployment capability, not a request field

One CLI flag:

```
python -m unimem_api --data-dir ./data --pdf-ocr
```

Without it: `PdfProcessor` is registered as `pdf@0.1`, a textless PDF is refused
exactly as before, the application imports and starts with no OCR extras and no
Tesseract on the machine, and **no dependency check or subprocess occurs at all**.

With it: `PdfOcrProcessor` is registered as `pdf-ocr@0.1`, **instead of**
`PdfProcessor`. `TextProcessor`, `WebpageProcessor`, and `DocxProcessor` are
untouched.

This is the central architectural choice, and the alternatives were worse in ways
worth naming:

* **A field on `CaptureEnvelope`** (`"ocr": true`) would make recognition a
  property of a *submission*, which it is not: whether a machine can read pixels
  is a property of the machine. It would also mean two captures of the same
  document could be normalized by different policies with nothing in the
  contract explaining why, and it would put a capability switch inside a document
  clients are encouraged to store and resend.
* **A `metadata` or `intent` convention** would be the same thing with less
  validation. `intent` says what the user wants done with a capture; it has never
  said which code runs.
* **A new MIME type** (`application/pdf+scanned`) would ask clients to classify
  documents they cannot inspect, and would be wrong for the mixed case this PR
  exists to handle.
* **Always-on OCR** would give `core` a native rendering library, an imaging
  library, and a system executable as unconditional dependencies, and would make
  every text PDF's processing depend on software that has nothing to do with it.

### Both PDF processors claim the same MIME type, and that is intentional

`PdfProcessor.supports()` and `PdfOcrProcessor.supports()` ask the same three
questions: `DOCUMENT`, a raw original, and exactly `application/pdf`. Both stay
pure and storage-free — neither sniffs bytes, neither reads the store, and neither
tries to detect "is this a scan" while routing.

So the two overlap, and **the mutual exclusion lives in composition**:
`build_local_app` takes an optional `pdf_ocr` port and registers one processor or
the other. Registering both is an `AmbiguousProcessorError` on every PDF capture,
and that is the correct outcome: registration order is not precedence
([ADR-004](ADR-004-processing-boundary-and-routing.md)), and a deployment that
registered two implementations of one claim has a wiring bug with two plausible
resolutions. A router precedence rule would turn that bug into a silently
working deployment whose behaviour depended on list order.

`build_local_app` receives the **port**, already constructed. The concrete
PDFium/Tesseract adapter is built one layer out, in `unimem_api.__main__`, so the
composition root imports no native library and a default deployment never
executes the import at all. There is no DI container, no registry, no
module-level service instance, and no `app.state` lookup — the same rule every
phase so far has kept.

### Embedded text first, and recognition only for what is left

For each physical page, in physical PDF order:

| page has                        | becomes                                     |
| ------------------------------- | ------------------------------------------- |
| nonblank embedded text          | `TEXT` segment, `ORIGINAL` provenance       |
| no embedded text, nonblank OCR  | `OCR` segment, `OCR` provenance             |
| no embedded text, blank OCR     | no segment                                  |

A page with embedded text is **not rasterized and not recognized**. That is not
an optimization; it is the policy. Extraction is never scored against
recognition, never "improved" by it, and never blended with it.

`strip()` appears only to decide blankness. Stored strings are exactly what the
parser or the engine returned: no Unicode normalization, no line-ending
rewriting, no whitespace collapsing, no hyphen repair, no paragraph joining, no
spell correction, no summarization, and no model anywhere near the text. An
engine that returns `Th1s` gets `Th1s` stored, because a correction nobody can
audit is worse than a visible error.

**The mixed-page limitation, stated plainly.** A page with *any* nonblank
embedded text is treated as embedded-text-covered, whole. If such a page also
contains a photograph of a sign, a scanned figure with a caption, or a stamped
signature, the words in those images are not read and no segment reports them.
There is no region-level OCR, no assessment of whether an existing text layer is
good or complete, and no blending of two sources on one page. Doing better needs
a layout model and a conflict policy, and a half-built version of either would
produce interleaved text whose order nobody could explain. **A `COMPLETE`
document from this processor does not mean every visible word was captured.**

### Provenance keeps extraction and recognition apart forever

An extracted page is `SegmentType.TEXT` with `ProvenanceSourceType.ORIGINAL`. A
recognized page is `SegmentType.OCR` with `ProvenanceSourceType.OCR`. Both enum
members already existed; no contract changed. Every segment also carries
`processor="pdf-ocr"` and `processor_version="0.1"`, so a stored document from
this build is distinguishable from one `pdf@0.1` produced even where the segment
types happen to match.

A recognized segment's `provenance.asset_id` is the **original PDF**, together
with `spatial.page`. That is not a dangling reference: the page image was derived
from that PDF at that page, the PDF is the only artifact that still exists, and
the page number says which part of it was read. Minting an asset id for a bitmap
that was freed before the content object was built would be a pointer to nothing.

### A page raster never outlives the bitmap it was read from

`PdfBitmap.to_pil()` returns an image built with `Image.frombuffer` over PDFium's
own memory, so the image shares pixels rather than copying them. Both reads of that
memory — encoding the PNG and copying the bytes out of the `BytesIO` — therefore
complete while the bitmap is still valid, which the code achieves by computing the
`return` expression before the enclosing `with` blocks unwind:

```
page.render(...)  ->  bitmap          native memory allocated
bitmap.to_pil()   ->  image           a view over that memory
image.save(buffer, "PNG")             reads the shared memory
buffer.getvalue() ->  bytes           an independent copy
image.close() / bitmap.close() / page.close()
```

What leaves the adapter is a plain `bytes` object owning nothing native; by then the
image, the bitmap and the page are closed, and the Tesseract subprocess that
consumes those bytes runs later and outside the lock. A conversion failure in
`to_pil`, an encoding failure in `save`, and a raster-size refusal all unwind the
same way. Pre-commit review asked for this to be proven rather than asserted: the
order above is checked as a sequence of recorded events in
`tests/unit/ocr/test_tesseract_adapter.py`, and the PNG is re-decoded in full after
every close to show it stands alone. No test dereferences freed memory to make the
point.

### The original is the only asset, and page images are not persisted

Exactly one `ORIGINAL` asset, with a fresh asset id, the original raw ref, its
SHA-256, and `application/pdf`. The bytes are unchanged. Page rasters are
temporary computation: never stored, never given an asset record, never
referenced from a segment, and freed before the next page is rendered — one page
of pixels in memory at a time, not fifty.

Title precedence is unchanged and was deliberately **not** extended:

1. the submitted `CaptureRecord.title`, exactly as given;
2. otherwise a nonblank `/Title` in the PDF's own metadata;
3. otherwise none.

Never from OCR text, the filename, the first page, the digest, or the URL. A
title read off the first page of a scan is a guess dressed as a fact, and
downstream it would be indistinguishable from one the document actually carried.
`CaptureRecord.title` is never mutated.

### What ran is recorded; how well it ran is not claimed

`ContentObject.metadata["pdf_ocr"]` records the physical page count, which pages
came from embedded text, which pages were attempted with OCR, which of those
returned nothing usable, and the **actual** engine name and version, rasterizer
name and version, and effective settings — all read from the installed software
at run time rather than from constants. Each recognized segment carries the
engine identity that produced it. `ProcessingRecord` has no metadata field and
was not given one; it records `pdf-ocr@0.1` and the existing outcome and
timestamps.

The key named for empty results is `ocr_pages_without_text`, and the name is the
decision. **An empty OCR result does not prove a page was visually blank.** A
faint scan, an unsupported script, a rotated photograph, and a genuinely empty
sheet all land there and this build cannot tell them apart, so the key is named
after what was observed rather than after a conclusion. There is no confidence
score: the engine is not asked for one, and an invented number would be read as a
measurement.

### A document nothing could be read from fails

If no page yields usable embedded or recognized text, processing raises
`ProcessingInputError` — the existing 422 `processing_failed` and the existing
durable `FAILED`. Not an empty `COMPLETE` content object, for the reason ADR-016
gave: the one thing a memory system must never do is report that it remembered
something when it remembered nothing.

### An OCR execution failure is not a verdict about the document

A new typed error, `core.processing.ocr.PdfOcrExecutionError`, which
**deliberately does not subclass `ProcessingError`**. That single fact carries the
whole lifecycle behaviour, because `ProcessingOrchestrator` is unchanged: it
marks a capture `FAILED` for a `ProcessingError` and leaves it `PROCESSING` for
anything else.

| failure                                                          | error                  | HTTP                      | capture      |
| ---------------------------------------------------------------- | ---------------------- | ------------------------- | ------------ |
| encrypted or malformed PDF                                       | `ProcessingInputError` | 422 `processing_failed`   | `FAILED`     |
| over the page or pixel limit                                     | `ProcessingInputError` | 422 `processing_failed`   | `FAILED`     |
| no usable text after successful extraction and recognition       | `ProcessingInputError` | 422 `processing_failed`   | `FAILED`     |
| engine missing or unusable during a run                          | `PdfOcrExecutionError` | 503 `ocr_unavailable`     | `PROCESSING` |
| engine crash or nonzero exit                                     | `PdfOcrExecutionError` | 503 `ocr_unavailable`     | `PROCESSING` |
| page timeout or exhausted document budget                        | `PdfOcrExecutionError` | 503 `ocr_unavailable`     | `PROCESSING` |
| inconsistent adapter result                                      | `PdfOcrExecutionError` | 503 `ocr_unavailable`     | `PROCESSING` |
| rasterizer load failure: `FPDF_ERR_FORMAT` / `PASSWORD` / `SECURITY` | `ProcessingInputError` | 422 `processing_failed`   | `FAILED`     |
| rasterizer load failure: any other or absent `err_code`           | `PdfOcrExecutionError` | 503 `ocr_unavailable`     | `PROCESSING` |
| raw-store failure                                                | `RawObjectStoreError`  | 503 `storage_unavailable` | `PROCESSING` |

The 422/503 split is the point. A 422 tells a client their document is the
problem; a 503 says the server could not do the work. An engine that crashed has
told us *nothing* about the document, so marking the capture `FAILED` would record
a judgement the system never made. The 503's public message is fixed and names
nothing about the machine — no engine version, no exit status, no language path,
no subprocess stderr, no local file name — and no content is returned or
persisted.

**Only known failures are translated, and the reason decides — not where it
happened.** An earlier draft of this decision classified PDFium failures by
*location*: load-time meant "input verdict", post-load meant "execution failure".
That was wrong, and pre-commit review caught it. Loading can fail for reasons that
say nothing about the document at all — an I/O error reading the stream, or a
failure PDFium itself declines to explain — and calling those malformed input
would durably mark somebody's capture `FAILED` on a guess.

What the adapter actually consults is `PdfiumError.err_code`, which PDFium
populates for document loading and for nothing else (`None` on every other API).
Three codes are an allowlist of genuine statements about the submitted bytes:

* `FPDF_ERR_FORMAT` — not a PDF this renderer can parse; malformed or truncated.
* `FPDF_ERR_PASSWORD` — needs a password, which this build never attempts.
* `FPDF_ERR_SECURITY` — uses a security scheme this build cannot read, which is a
  capability limit stated as one.

Everything else is an execution failure, deliberately: `FPDF_ERR_UNKNOWN` (PDFium
declined to say), `FPDF_ERR_FILE` (an access failure, which describes the stream
or the machine rather than the document), `FPDF_ERR_PAGE` (a page-loading code
whose meaning at *document* load this build cannot justify without guessing),
`FPDF_ERR_SUCCESS`, an absent `err_code`, and any code a future PDFium adds.
Widening that list is a reviewed edit with a justification attached, not a default.

Two constraints on how the classification is made. The exception's **message is
never parsed** — it is another library's prose, it is not a stable interface, and
it can carry byte offsets and local paths. And the original exception is always
preserved as `__cause__`, while the public text of both branches is fixed here
rather than copied from PDFium.

Because only loading reports a code, a post-load failure has nothing to classify
and lands on the same answer an absent code gets: an execution failure. The
document opened, so failing to produce pixels for one of its pages is this run not
delivering rather than a proven verdict.

Everything else — a `TypeError` from a defect in this code — propagates as itself
and reaches the ASGI server's ordinary 500, because a bug dressed up as a bad
document is a bug that never gets fixed.

The whole rule sits behind the pypdf-first checks and does not replace them: an
encrypted or unreadable PDF is refused by `extract_pages()` before the rasterizer
is handed a stream at all, so these codes are reached only for a document pypdf
already accepted.

Raw-store and persistence errors keep their own types and their own causes;
nothing here re-chains one.

**A capture left `PROCESSING` is not automatically resumable, and this PR does
not change that.** `GET /v1/captures/{id}` makes the state observable and that is
the entire recovery story, exactly as in [ADR-009](ADR-009-processing-orchestration.md)
and [ADR-011](ADR-011-local-http-capture-surface.md). Resubmitting the same id is
still `409`. Reconciliation and reprocessing need a real requirement to be
designed against, and a connector has not asked for one.

### Startup validates everything, or the server does not start

`--pdf-ocr` validates, before any request is served:

1. **both** optional Python packages import;
2. the Tesseract executable runs and reports a version;
3. **both** `eng` and `rus` language data files are present.

The first item says "both" for a reason branch review had to point out. Importing
the adapter proves PDFium is loadable, but the adapter names no PIL symbol and
`pypdfium2` defers loading Pillow until `PdfBitmap.to_pil()` is actually called —
so a machine with `pypdfium2` installed and `Pillow` missing passed the gate and
then failed on the first scanned page, which is the precise failure this section
claims cannot happen. `import PIL.Image` is now performed explicitly alongside the
adapter import, inside the OCR-only prerequisite path and nowhere in ordinary
`core` or `unimem_api` startup.

Any failure exits with a sentence naming what is missing and how to provide it.
It does **not** silently disable OCR and start the refusing build, does **not**
fall back to English alone, and does **not** install software, download language
data, or call a cloud service. A deployment that asked for `eng+rus` and got
English is not the deployment that was asked for, and the difference would show
up only in what Russian documents were remembered as saying.

### A narrow typed port, not a provider framework

One Protocol, in `core.processing.ocr`:

```python
recognize_missing_pages(stream, *, embedded_pages) -> PdfOcrResult
```

The input is a **binary stream**, never a caller-controlled path, so a path is not
expressible across the boundary. `embedded_pages` is the set of 1-based physical
pages the caller has already covered. The result reports the document's actual
physical page count, one entry for **every** remaining page — including an empty
string where recognition succeeded and found nothing — and the actual engine and
rasterizer identities plus the effective settings. The types are small frozen
dataclasses holding strings and integers; no native object, bitmap, PIL image,
file handle, or temporary path crosses into canonical processing.

`validate_ocr_result()` rejects a result that is not a consistent description of
the document: a non-positive or out-of-range page number, a duplicate, a page the
caller excluded, or — most importantly — a **missing** page. Without that last
rule an adapter that quietly dropped half a document would be indistinguishable
from a document whose second half is blank, and the result would be a `COMPLETE`
content object missing content nobody knows is missing. Every rejection is a
`PdfOcrExecutionError`, never a `ProcessingInputError`: malformed provider output
is an execution failure, not a blank document.

This is deliberately **not** a universal vision or provider abstraction. One
method, one format, one question. A second engine would implement the same
Protocol; a second *modality* would be a different design conversation.

### Core imports no machinery

`core` imports no rasterizer, no imaging library, no `subprocess`, no FastAPI,
and no concrete adapter, and it reads original material only through
`RawObjectStore`. The import boundary is checked in
`tests/unit/api/test_app_factory.py`, and the stronger claim — that *importing*
the application loads no rasterizer, and that a default deployment works in an
interpreter where the native wheels are genuinely unimportable — is checked in
real subprocesses in `tests/unit/api/test_pdf_ocr_composition.py`.

### The local adapter, and its fixed recognition policy

`unimem_ocr` is a separate package, outside `core`, shipped in the wheel but with
its native imports confined to `unimem_ocr.tesseract`, which
`build_tesseract_ocr()` loads at call time. `import unimem_ocr` therefore costs
nothing on a machine without the extra, which is what turns a missing wheel into
a sentence naming the extra rather than a traceback.

The policy is fixed and uncleverly so:

* languages `eng+rus`, in that order, in one pass;
* nominal rasterization at 300 DPI, onto an **opaque white** background;
* Tesseract OEM 1 (the LSTM engine), PSM 3 (automatic segmentation, no OSD);
* one page at a time, no retry, no orientation-detection or deskew pipeline;
* page rotation is whatever the renderer honours from the PDF's `/Rotate`, with
  no orientation guessing added;
* no PDF JavaScript, XFA, or form environment is initialized, and annotations and
  form fields are not rendered;
* no external resource is fetched, no model is downloaded, and no cloud service
  is contacted.

Every one of those is a knob that could have been turned per document by
something that guessed. A guess that changes what text a document is remembered
as carrying is not a knob; it is a second unreviewable policy.

**Dependencies.** `pypdfium2>=5.0` rasterizes and `Pillow>=11.0` encodes, both
under an optional `[ocr]` extra rather than as runtime dependencies. Tesseract
and its `eng`/`rus` traineddata are **system** prerequisites this project never
installs. Tested against pypdfium2 5.13.0 (pdfium 153.0.7999.0), Pillow 12.3.0,
and Tesseract 5.3.4 (leptonica 1.82.0). `pypdfium2` is a thin ctypes binding over
the renderer Chromium uses, with no imaging stack of its own and no subprocess;
the 5.x floor is the API this adapter is written against. PyMuPDF was rejected
for its AGPL licence, poppler and Ghostscript for being system installations with
their own CLIs, pdf2image for wrapping one of those, and `pytesseract` for being a
dependency whose whole job is to build one command line this adapter already
builds correctly.

**The engine is invoked as a fixed argument list with `shell=False`.** No shell
string is constructed. The page image goes in on the child's stdin and the text
comes back on its stdout, so no path is named on either side and there is no
temporary file to collide, leak, or be guessed. No filename, URL, document
metadata, capture title, or request field becomes an executable, an option, an
output path, or a configuration path. Standard output is decoded as UTF-8
strictly and returned unchanged; standard error is captured separately, never
mixed into the text, and never returned over HTTP.

### PDFium is not thread-safe, so one lock guards all of it

A single process-wide `threading.Lock` in the adapter module guards **every** call
into PDFium, including opening and closing documents and pages: destruction is a
native call too, and a bitmap released while another thread is rendering is the
same bug as two threads rendering at once. Handles are closed explicitly in
`finally` blocks rather than left to a finalizer, precisely so that destruction
happens on this thread and inside the lock.

The lock is **not** held while Tesseract runs. The subprocess is where the
wall-clock time goes, and holding a native-library lock across it would serialize
an entire server behind one OCR run for no safety benefit. Nothing assumes the
FastAPI server is single-threaded — Uvicorn runs synchronous handlers on a thread
pool, so two captures really can be inside the adapter at once.

The lock is a native-library mutual-exclusion boundary and nothing else. It is
not capture ownership, not idempotency, not a worker lease, and not a queue.

### Bounded work, and no false sandbox claims

Named defaults, in a small frozen dataclass:

| limit                         | default    | checked                                  |
| ----------------------------- | ---------- | ---------------------------------------- |
| physical pages per PDF        | 50         | before any rasterization                 |
| raster pixels per page        | 20,000,000 | before bitmap allocation                 |
| Tesseract timeout per page    | 30 s       | passed to the subprocess                 |
| recognition budget per document | 120 s    | monotonic clock, re-read before each page *and again once the native lock is held* |

These are **initial product limits, not benchmark-derived optimal values**. A
document over the page limit is refused **whole** rather than partly recognized:
a content object holding half a document, with nothing in it saying the other half
exists, is the silent truncation these limits exist to prevent. A page over the
pixel limit is refused rather than quietly downscaled, because a silently
lower-resolution render changes what the engine read without changing anything a
reader of the result can see. The remaining budget is recomputed before every
page and the smaller of it and the per-page timeout is what the next invocation
gets, so fifty pages cannot each take thirty seconds; a timed-out child is killed
and reaped before the error leaves the adapter, and no further page is begun once
the budget is spent.

**The budget is checked twice per page, and the second check is the one branch
review found missing.** The first is before the render is attempted. The second is
the instant the process-wide PDFium lock is held and *before* the page is opened, a
bitmap allocated, or a PNG encoded — because between those two moments a thread can
block for an unbounded time waiting for another capture to finish inside PDFium. A
budget checked only before that wait is not a budget: the original code rasterized
the page anyway and refused on the far side of the work it had no budget for. The
absolute deadline is passed into the rendering boundary so both checks measure the
same instant. It is a narrow check and deliberately not preemption: a native render
already under way is not interrupted, and nothing here is a queue, a worker lease,
or an HTTP deadline.

**Be precise about what these are not.** They are not a memory or CPU sandbox.
They do not limit this process's memory, they do not preempt the pure-Python PDF
parser that ran before recognition started, and they cannot interrupt an
in-process native rendering call once it has begun — a page whose *declared* size
passes the pixel check and whose content is pathological can still occupy the
renderer for an unbounded time, and the only thing that could stop that is a
separate process, which this build does not have. **The HTTP request has no hard
120-second deadline**: the budget covers recognition alone, and the request has no
timeout at all. No process pool, queue, or isolation framework is built here.

### Nothing else moved

* **No contract change.** Schema stays `0.2`. `SegmentType.OCR` and
  `ProvenanceSourceType.OCR` already existed and no enum gained a member.
* **No route, and `src/unimem_api/app.py` is unchanged.** The only delivery-side
  edit is one row in `unimem_api/errors.py`'s translation table, which
  `create_app` installs exactly as it installed every other row.
* **No intake change.** A scan is a `DOCUMENT` capture naming staged
  `application/pdf` bytes, which intake already accepted.
* **No persistence change.** Same tables, same snapshot semantics.
* **No replay change.** `replay.py` is untouched and still `TEXT`-only, so a
  resent document capture id is `409` exactly as before.
* **No browser change.** Nothing under `clients/browser-extension/` moved: no
  file picker, no OCR UI, no new permission.
* **No change to `PdfProcessor`, `DocxProcessor`, `TextProcessor`,
  `WebpageProcessor`, rendering, or lifecycle orchestration.**

### Existing data is not revisited

Enabling OCR does not reopen, reprocess, or migrate anything. A capture that
already `FAILED` as a scan stays `FAILED` with no content; the same bytes are
ingested under a **new capture id**, which shares the raw object (identical bytes
deduplicate) and shares no canonical identity — a fresh content id, a fresh asset
id, and fresh segment ids. There is no same-id reprocessing and no migration
step.

## Consequences

**A whole class of document becomes readable, and its text is honestly labelled.**
Scans, photographed pages, and image-only exports can enter canonical content in
a deployment that asked for it, and every consumer can tell that text apart from
text a document carried. Nothing downstream has to trust this build's recognition
in order to trust the pages it did not need to recognize.

**Two deployments of the same commit now behave differently on one input.** That
is the intended cost of making recognition explicit, and it is bounded: the
difference is confined to PDFs with pages carrying no embedded text. Every other
capture — text, webpage, DOCX, searchable PDF — is byte-for-byte the same in both.
Content an OCR-enabled deployment wrote is readable by a default one, because a
stored `ContentObject` is just canonical content; what a default deployment cannot
do is *process* the scan that produced it.

**`COMPLETE` now means less than it did.** It has always meant "this policy
finished and its content was persisted". For a `pdf-ocr@0.1` document it still
means exactly that — and it does not mean the recognition was accurate, that
every visual element was understood, or that every visible word was captured. A
page with embedded text plus an image full of words produces a document that
looks complete and is missing the image's words. Consumers that need to know read
`provenance.source_type` and the `pdf_ocr` metadata.

**The build now has system prerequisites it can be run without.** The optional
extra and the system engine are a real operational burden on anyone who wants
recognition, and a real absence of burden on anyone who does not. Keeping that
boundary honest is why the default CI jobs deliberately run on an installation
with neither, and why a fourth job exists that installs both and refuses to skip.

**Recognition runs inside the request, and a scan is slow.** A 300 DPI page
through a real engine takes seconds, and a fifty-page scan can take the full
two-minute budget. There is no queue, no worker, and no async surface, exactly as
in every phase so far; the cost is a slow synchronous POST, and the moment
something needs better than that it needs a queue designed against a real
requirement.

**Macro Phase 3 remains open.** This PR closes only Phase 3 PR 3. Image upload
OCR, DOCX OCR, handwriting, layout and table understanding, searchable-PDF
rewriting, OCR correction, cloud providers, queues, recovery, and reprocessing
are all explicitly outside it.

See [ADR-016](ADR-016-pdf-document-ingestion.md) for the acquisition step and the
extraction semantics this one reuses unchanged,
[ADR-004](ADR-004-processing-boundary-and-routing.md) for the routing rule the
mutual exclusion depends on, and
[ADR-009](ADR-009-processing-orchestration.md) for the lifecycle rule that makes
a non-terminal infrastructure failure the correct outcome.
