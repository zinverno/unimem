# ADR-016: PDF document ingestion, and the acquisition step binary made necessary

Status: accepted (Macro Phase 3, PR 1). Macro Phase 2 is
[closed](#macro-phase-2-is-closed). Macro Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, enum, lifecycle, or persistence change: the schema stays `0.2`.
One route is added — `POST /v1/uploads` — and it is deliberately not a capture.

## Macro Phase 2 is closed

Phase 2 answered the first question about modality. It taught UniMem to ingest
an HTML-backed webpage ([ADR-014](ADR-014-html-webpage-ingestion.md)) and taught
the browser extension to submit one ([ADR-015](ADR-015-browser-whole-page-capture.md)).

**The Phase-2 manual acceptance checklist A–G was run by the user, by hand,
against a real local Chromium installation and a real local UniMem server, and
every check passed.** That matters because the checklist exists precisely for
what CI cannot reach: a real toolbar click, a real right-click on the extension's
own context menu, a real restricted page, a real page mutated in DevTools first,
and a real server stopped mid-session. The automated suites cover the flows, the
envelopes, the network bounds, and the extension's registration in Chromium —
**they did not dispatch those gestures, and nothing here should be read as
claiming they did.** A person did, and reported the result.

Phase 2 is therefore closed and stays closed. Phase 3 does not reopen webpage
extraction semantics, browser selection semantics, browser whole-page semantics,
replay architecture, or Chrome permissions and UI.

Macro Phase 3 asks the next question, and it is the first one about *material
that is not text*:

> UniMem has ingested two kinds of material, and both of them were strings.
> What does it take to ingest something that is not?

## Context

The question this PR answers, exactly:

> How does a locally uploaded PDF become an immutable raw original and then a
> page-aware canonical `DOCUMENT` `ContentObject` through the existing capture
> lifecycle, without smuggling binary data into the JSON `CaptureEnvelope`?

Everything hard about it is in the second half of that sentence.

### Binary cannot honestly live inside a JSON envelope

`CaptureEnvelope` is JSON, and JSON has no bytes. The three fields that could be
made to *hold* a PDF are `payload.text`, `payload.html`, and `payload.title`, and
putting one in any of them would be a lie about what was captured:

* `payload.text` means *the text the user submitted*. A base64 blob is not text
  the user submitted; it is a transport encoding of something else. A capture
  that says "the user captured this text" and holds `JVBERi0xLjQK…` has recorded
  a fact that is false, and every later stage — the text processor, replay
  equivalence, rendering — would read it as if it were true.
* `payload.html` is worse in exactly the same way, one modality over.
* `source.url` would turn a local file into a fetch, which this build does not do
  and [ADR-014](ADR-014-html-webpage-ingestion.md) already refused for webpages.

Base64 is also expensive in a way that gets worse precisely where documents get
interesting: it inflates the payload by a third, forces the whole file through a
JSON parser and into memory twice, and turns a streaming write into a buffered
one. A capture surface whose cost model degrades with file size is a capture
surface that will fail on the first real thesis.

`CapturePayload.file_ref` has existed since Phase 0A, and Phase 0A wrote down
what it means: *an opaque handle to bytes held outside this contract*. That is
the field for this. It is not a workaround; it is the modelling that anticipated
this exact case, and it is why nothing in `src/core/contracts/` changes here.

### But a `file_ref` has to come from somewhere

`file_ref` names bytes. Something has to put the bytes there first, and until
this PR nothing could: `RawObjectStore` had no HTTP surface, so a client outside
the process had no way to reach it. That gap is the whole reason a new route
exists.

## Decision

### Acquisition and capture are two operations, and the split is load-bearing

```
PDF bytes
    -> POST /v1/uploads            immutable, content-addressed raw object
    -> file_ref  "sha256:<digest>"
    -> POST /v1/captures           payload.type = document
                                   payload.file_ref = that reference
                                   payload.mime_type = application/pdf
    -> CaptureIntake               resolve + verify, then RECEIVED, then STORED
    -> ProcessorRouter             exactly one processor
    -> PdfProcessor                one text segment per nonblank page
    -> ContentObject(document)     durable
    -> COMPLETE
```

**`POST /v1/uploads` is not a capture, and does not become one.** It mints no
capture id, creates no `CaptureRecord`, advances no lifecycle, runs no
processor, produces no `ContentObject`, and records no intent, context, or
source metadata. It puts immutable bytes in the raw store and returns the
content-addressed reference to them. That is the entire route.

Keeping the two apart is not fastidiousness. A capture is *an event a person
asked for* — it has a time, an intent, a source, a title, and a lifecycle. Bytes
arriving on a socket are none of those things, and conflating them would mean
that uploading a file, twice, by accident, created two memories. It also means
the same staging surface serves images, audio, video, and arbitrary files later
without being redesigned, and that a client can stage a large document once and
reference it from a capture minted in a different process — which the restart
test exercises directly.

The route is generic on purpose. `POST /v1/upload-pdf` or `POST /v1/pdfs` would
put a *format* in a URL that has nothing to do with formats: the raw store
neither knows nor cares what the bytes are, and the day an image or an audio
file needs staging there would be a second route doing the identical thing.

### The upload response, and why it is 200

```json
{
  "file_ref": "sha256:<digest>",
  "sha256": "<digest>",
  "mime_type": "application/pdf"
}
```

`file_ref` is exactly what goes into `CapturePayload.file_ref`; a client copies
one string. `sha256` is the same fact in a form the client can *check* against
the file it sent, which is what makes "the original is byte-exact" verifiable
rather than merely promised.

**200, not 201.** The raw object store deduplicates by content and deliberately
does not report whether a write created a new file or found the identical bytes
already there — `os.link` raising `FileExistsError` *is* the dedup path, and an
immutable content-addressed object is the same object either way. So the server
genuinely does not know whether it "created" anything. Answering 201 would be a
guess dressed as a fact; 200 says the true thing, which is that the object is
available.

`mime_type` is descriptive only. It echoes what the upload declared and takes no
part in identity: the same PDF uploaded as `application/pdf` and as
`application/octet-stream` deduplicates to one object with one reference.

### The uploaded filename has no authority anywhere

It does not determine a storage path — the layout is derived from a digest the
store computed itself, and nothing in `core.storage` ever writes through a
caller-supplied name. It is not part of raw identity. It is not persisted onto
the `CaptureRecord`. It never becomes a title. It is not returned.

The consequence worth stating plainly: traversal sequences, absolute paths, and
NUL bytes in a filename are *not dangerous input that has been sanitized*. They
are input that is never read. That is a much stronger property than escaping,
and it is why the tests assert a hostile filename produces byte-identical
results to a benign one rather than asserting that some sanitizer fired.

### Staging is exact, and unclaimed objects are allowed to exist

The exact uploaded bytes are stored: no PDF rewriting, metadata editing,
decompression, recompression, newline conversion, MIME sniffing, or filename
normalization. The same PDF uploaded twice yields the same digest and the same
`file_ref`.

An object that is never referenced by a `CaptureEnvelope` simply remains as an
unclaimed immutable raw object. **This phase adds no garbage collection, lease,
expiry, upload table, staging database, or cleanup worker**, and that is a
decision rather than an oversight. Every one of those needs a policy — how long
is an upload valid, what happens to a reference held by a client that is slow,
who owns an object two captures point at — and a policy invented before anything
needs it is a policy nobody can evaluate. An immutable object taking up disk is
the cheapest possible wrong answer to defer.

### Only UniMem raw references are resolved, and this is a security boundary

For the supported path, `file_ref` must be `sha256:<64 lowercase hex>` — the
reference `POST /v1/uploads` returned. It is **not** interpreted as a filesystem
path, a `file://` URL, an HTTP or HTTPS URL, an S3 URL, a browser path, or a user
home path, and none of those is opened, resolved, or fetched.

This is the single most important line in the PR. Accepting an arbitrary
filesystem path would move authority from the upload boundary into `core` and
hand every client of a localhost API a local-file-read primitive: `POST` a
capture naming `/etc/shadow` or `~/.ssh/id_rsa`, then `GET` the content object
and read it back as segments. Accepting a URL would add SSRF on top. The refusal
is therefore not a validation nicety — it is the reason the upload route has to
exist at all, because without it there would be pressure to let clients name
files they already have.

Intake settles the reference **before any lifecycle write**:

1. is the shape one this build ingests (document, `file_ref` alone,
   `application/pdf`)?
2. is the reference one this build understands?
3. does the raw store actually hold it?

All three are reads. Only then does intake look at its clock or create
`RECEIVED`. A malformed, unresolvable, or missing reference leaves no `RECEIVED`
record, no `STORED` record, no raw write, and no `ContentObject` — because a
receipt is durable evidence that a capture was accepted, and a capture whose
material cannot be found was never acceptable.

### A third intake error, because there is a third answer

`CaptureIntake` had exactly two failure modes, and neither fits:

* `UnsupportedCapturePayloadError` — the envelope is valid and this build lacks
  the capability. Wrong: this build *has* the capability.
* `InvalidCaptureEnvelopeError` — the envelope contradicts its own contract.
  Wrong: nothing about it is contradictory.

So `CaptureMaterialUnavailableError` joins them, for a well-formed reference
naming bytes that are not staged. It maps to `422 capture_material_unavailable`.

It is emphatically not a `RawObjectStoreError`. The store answered the question
it was asked — *is this here?* — correctly and completely. Reporting a truthful
"no" as a `503 storage_unavailable` would tell a client the server is degraded
and to retry later, sending them into a loop that cannot terminate. The right
advice is "stage the bytes, then resubmit the identical envelope", and a distinct
error is how that gets said.

A `file_ref` this build does not *understand* stays an
`UnsupportedCapturePayloadError`: that is a statement about capability, and a
later phase resolving more reference schemes would accept the identical envelope.

No refusal echoes the submitted `file_ref` or MIME type. A `file_ref` may be an
absolute path out of somebody's home directory, and a refusal is not a reason to
copy it into a message that will be logged.

### Document intake stores no second copy

For a valid staged PDF the sequence is: settle capability, reference, and
existence; create `RECEIVED`; take the reference; persist `STORED`. Step three
writes nothing. The bytes were immutable before the capture existed and are
addressed by their own SHA-256, so writing them again would produce the identical
object at the identical path — a no-op with a cost.

The `RawObjectRef` on the capture is minted fresh from the digest the submitted
reference names, carrying the MIME type the *submitter* declared for this
capture. Intake does not pass a caller's object through into durable state.

`TEXT` and `WEBPAGE` intake are untouched, and `CaptureEnvelope` is not mutated.

### The supported document shape, and what is refused

Supported in this PR:

```
payload.type      == document
payload.file_ref  is a staged sha256 reference
payload.mime_type == "application/pdf"
payload.text      is None
payload.html      is None
```

Refused: document + text only; document + `file_ref` + text; document +
`file_ref` + html; document without `file_ref`; a staged `file_ref` with no MIME
type; a staged `file_ref` with any other MIME type.

The reasoning is the one [ADR-014](ADR-014-html-webpage-ingestion.md) established
for webpages, plus one that is new to documents:

* **A capture stores one raw original.** More than one submitted representation
  is more material than the record can hold, and silently picking one would
  durably discard something the client sent while reporting success.
* **The canonical contract stays wider than this build.** `CapturePayload`
  permits a document backed by `text`, and it keeps permitting it. This is a
  capability refusal, not a contract narrowing.
* **A format with no processor must not be accepted as if it had one.** A DOCX
  arriving at intake would sail through to a router that cannot route it, and
  the capture would strand mid-lifecycle for a reason nobody can act on.
  Refusing at the boundary, while the client still holds the request, is where
  "not yet" can actually be said. The MIME match is exact — no prefix, no
  parameter tolerance, no case folding, and no byte sniffing.

### `PdfProcessor`, and why it claims a MIME type

`supports` matches `payload_type == DOCUMENT` **and** `raw_object.mime_type ==
"application/pdf"`. It is pure and storage-free, like every other `supports`.

Claiming the whole `document` payload type would have been the obvious thing and
would have been wrong. The day a DOCX or EPUB processor arrives, a
document-claiming PDF processor makes every DOCX capture ambiguous, and the
router — correctly — refuses to pick. Claiming one format means the next document
processor is appended to the composition root and nothing above it changes.

### pypdf, and the dependency boundary

`pypdf` is `core`'s first non-pydantic runtime dependency. That is a real cost
and it was weighed:

* it is pure Python, with no system libraries, no wheels with native
  dependencies, and no subprocess;
* it does one thing — read and write PDF structure — and reading PDF structure is
  emphatically not something the standard library does. Unlike HTML, where
  `html.parser` made a third-party parser unnecessary, there is no
  standard-library answer here at all;
* the alternatives are worse in kind, not degree. PyMuPDF is a binding to a
  rendering engine under a licence that infects. `pdfplumber` layers layout
  inference on top of `pdfminer`, which is a much larger surface for a feature
  this PR does not want. Poppler, LibreOffice, and a browser mean subprocesses,
  system packages, and a sandboxing problem.

It is confined to `src/core/processing/pdf.py`, and a test asserts that exactly
one module in `core` imports it. The kernel's third-party surface stays an exact
allowlist — `{pydantic, pypdf}` — so the next addition has to be made
deliberately, in a diff someone reviews.

### Scope: text-bearing, unencrypted PDFs

Supported: PDFs that carry embedded text and are not encrypted.

Not supported, and each an explicit non-goal: OCR of scanned or image-only PDFs,
password or otherwise encrypted PDFs, form interpretation, annotation
extraction, attachments and embedded files, image extraction, layout
reconstruction, tables as structured tables, font analysis, PDF repair,
JavaScript, and external references. Nothing is executed, fetched, or opened;
the byte stream from the raw store is the whole input.

Encryption is checked **before any page is touched**, and this ordering is not
incidental. `pypdf` will transparently decrypt a document whose user password is
empty, so without the explicit check an empty-password PDF would quietly succeed
while a real-password one failed — and "unencrypted" would mean two different
things depending on how the document happened to be locked. No password is ever
attempted.

A malformed or truncated document is a `ProcessingInputError`. Only the parser's
own error base is translated; a `TypeError` from a bug in the processor is a bug
and is not dressed up as a bad PDF. The parser's message is not carried forward
either — it names byte offsets and internal parser state, which a client cannot
act on and some of which describes this server.

### Page-per-segment, and two different kinds of position

Pages are read in physical PDF order. For each page the parser's ordinary text
extraction runs. A page whose text is `None`, empty, or whitespace-only emits no
segment; `strip` decides that question and nothing else — the *stored* text is
the parser's string, byte for byte.

Nothing is normalized: no Unicode normalization, whitespace collapsing,
line-ending rewriting, hyphen repair, de-columnization, paragraph joining, header
or footer removal, or model cleanup. If the parser emitted a trailing newline,
the segment carries a trailing newline. This is the same commitment
[ADR-014](ADR-014-html-webpage-ingestion.md) made about extracted page text and
the same one Phase 0F made about submitted bytes: what came out is what is
stored, and the immutable original is one asset reference away for anyone who
wants to do better later.

Each emitted page becomes one `Segment`:

* `type` — `TEXT`;
* `text` — the parser's string, exactly;
* `spatial` — `SpatialLocation(page=<1-based physical page number>)`;
* `position` — contiguous canonical reading order: 0, 1, 2, …

**A blank page therefore leaves a gap in `spatial.page` and no gap in
`position`**, and the distinction is the point:

```
PDF page 1 -> text        segment position=0  spatial.page=1
PDF page 2 -> blank       (no segment)
PDF page 3 -> text        segment position=1  spatial.page=3
```

`position` answers "what is the third thing in this document's content?"
`spatial.page` answers "where do I look in the PDF to find it?" Those are
different questions, they have different answers, and collapsing them would make
one of the two wrong. `SpatialLocation.page` has existed since Phase 0A for
exactly this, so no contract change is needed to say it.

### Provenance is `ORIGINAL`

Every segment's `Provenance` carries the capture id, the original asset id,
`processor = pdf`, `processor_version = 0.1`, and
`ProvenanceSourceType.ORIGINAL`.

`ORIGINAL` rather than `OCR` is the whole substance of the claim: this text was
embedded in the document and read straight out of it, not inferred from pixels.
The day an OCR-capable build exists, `OCR` will mean something precise, and a
consumer will be able to tell text a document carried from text a model guessed
at. No PDF-specific provenance member is added — the asset's MIME type already
says the original was a PDF, and an enum member restating it would be a second
place to keep the same fact.

### Title precedence

1. the submitted `CaptureRecord.title`, exactly as given;
2. otherwise a nonblank `/Title` in the PDF's document metadata;
3. otherwise none.

A person who titled their capture said what they wanted it called, and no
extraction outranks that. The document's own metadata is its claim about itself
and is the obvious second — the same shape as the webpage rule, with `/Title`
standing where `<title>` stands.

There is no third source, and each rejected candidate would have been a name the
document never carried: not the uploaded filename (untrusted client text this
build never records), not the `file_ref` or digest (which name bytes, not a
document), not the first page's text, not its largest heading, not a URL path.

A whitespace-only `/Title` is treated as absent. It is used verbatim otherwise —
not trimmed, not Unicode normalized — and it is only read at all if it is
genuinely a string, since a malformed document can put anything in that slot.

**Extracted metadata affects the `ContentObject` only.** `CaptureRecord.title`
means *the title the submitter provided* and is never rewritten. No wider PDF
metadata model is built here: no author, subject, keywords, or dates, because no
consumer has asked for one and a field nobody reads is a field nobody maintains.

### The canonical output

`ContentObject.type = DOCUMENT`; `source` carrying the capture id, provider, and
url from the `CaptureRecord`; `original` naming the PDF asset, its MIME type and
its SHA-256; exactly one `ORIGINAL` asset pointing at the staged raw object with
its digest unchanged; one `TEXT` segment per nonblank page; one `COMPLETE`
`ProcessingRecord` for `pdf` / `0.1`.

Content, asset, and segment ids are fresh opaque UUIDs and are explicitly **not**
derived from the PDF's digest. That address identifies bytes; two captures of the
identical PDF are two content objects holding two asset records, and deriving ids
from the digest would make them collide.

### A textless PDF is a failure, not an empty success

If the document parses and no page yields nonblank embedded text — in practice, a
scan — the processor raises `ProcessingInputError`. The existing orchestrator
then applies its existing rule: a `ProcessingError` is a verdict about the
capture, so the capture is durably `FAILED` and the error is re-raised untouched.

It must not return `COMPLETE` with zero segments. That would be the system
reporting that it remembered something when it remembered nothing, which is the
one answer a memory system must never give — and it would be *silently* wrong,
discovered only when someone searched for a document they were told was saved.
It must not silently run OCR either, because there is no OCR here to run and
inventing one is not a small feature.

The refusal says what the boundary is: no extractable embedded text, and this
build does not perform OCR. The exact PDF stays in raw storage, so nothing
submitted is lost by refusing and an OCR-capable build can read precisely these
bytes later.

Nothing about the orchestrator's failure semantics is modified.

### Completed replay stays TEXT-only

`src/unimem_api/replay.py` is semantically unchanged, and `is_equivalent_text_replay`
still requires `payload.type is TEXT` on both sides. So:

* the first PDF capture is `201`;
* an identical resubmission under the same capture id is `409`.

That is intentional. Replay's guarantee is *proof* of equivalence, and for a
document the proof would need its own design: the submitted `file_ref` is a
digest, so the bytes are trivially comparable, but nothing in this phase has
asked for document idempotency and a guarantee designed without a requirement is
a guarantee nobody can check. When a connector loses a response to a document
POST, that will be the requirement, and it will be its own decision.

### No browser-extension change

`clients/browser-extension/` is byte-for-byte unchanged. No file picker, popup,
PDF button, drag-and-drop, download interception, or new permission. The vertical
slice is exercised through the local upload and capture API — which is the honest
place for it, because whether a browser extension should be able to read local
files is a product and permissions question, not a consequence of adding a PDF
processor.

### Everything else is unchanged

No contract, enum, or schema change: `SCHEMA_VERSION` stays `"0.2"`. Every piece
of vocabulary this PR needed already existed —

```
CapturePayloadType.DOCUMENT   = "document"
ContentType.DOCUMENT          = "document"
CapturePayload.file_ref       : str | None
CapturePayload.mime_type      : str | None
Segment.spatial               : SpatialLocation | None
SpatialLocation.page          : PositiveInt | None
ProvenanceSourceType.ORIGINAL = "original"
AssetRole.ORIGINAL            = "original"
```

— which is the same test Phase 2 passed and for the same reason: Phase 0A
modelled the domain rather than the phase. If the first real document capability
had needed a new field or a new enum member, that modelling would have been
wrong.

No lifecycle state is added; no persistence schema or migration; no
`ProcessingOrchestrator`, `ProcessorRouter`, `TextProcessor`, `WebpageProcessor`,
or raw-store change; no queue, worker, or background processing; no auth or CORS
change.

`create_app` gains a `raw_store` parameter, because the upload route needs one
and every other dependency of that function is already a parameter. Hiding it in
`app.state`, a module global, or a lazy singleton would have concealed a real
dependency to avoid writing it down — and it must be *the same instance* intake
and the processors hold, or a client would stage bytes into one store and hand a
`file_ref` to another that had never heard of them.

## Security

* **No arbitrary file read.** Only `sha256:<digest>` is resolved. A path — even
  one that exists and holds a perfectly good PDF — is refused without being
  opened.
* **No network.** No URL scheme is fetched, `source.url` included. A `file_ref`
  of `http://169.254.169.254/…` is refused immediately rather than dialled.
* **No subprocess, and nothing executed.** A PDF's JavaScript, embedded files,
  and external references are inert data that is not followed.
* **The filename is never read**, so it can reach no path, identity, title, or
  record.
* **Nothing is echoed.** The upload response contains no byte of the file and no
  filename. A rejected `file_ref` is not repeated back. A parser error's own text
  does not reach the wire, and neither does a stack trace or a temporary path.
* **The store is still write-only-by-content.** Nothing is written through a
  caller-supplied path; the layout comes from a digest the store computed.
* The server remains unauthenticated and localhost-bound, exactly as before —
  and note that this route now accepts arbitrary bytes from anyone who can reach
  the port, which is one more reason the default binding is what it is.

## Consequences

* UniMem ingests its first non-text modality end to end, usable with `curl`.
* The system has an acquisition boundary, generic and reusable, that images,
  audio, video, and arbitrary files can stage through without redesign.
* `core` has one focused third-party dependency, in one module, under an exact
  allowlist.
* Unclaimed raw objects can accumulate. Deliberate, and the first thing a future
  lifecycle-policy decision will have to address.
* Scanned PDFs fail loudly. Users with scans get an error rather than an empty
  document, which is the correct outcome and also the clearest possible signal
  about what OCR would be worth.
* Documents have no replay, so a lost response to a document POST costs a `409`
  and an observational `GET`.

## Alternatives rejected

* **Base64 in `payload.text` or a new `payload.data` field.** Dishonest about
  what was captured, a schema bump for a transport concern, and a cost model that
  degrades exactly where documents get interesting.
* **A PDF-specific upload route.** Puts a format in a URL that has nothing to do
  with formats, and guarantees a second identical route for the next modality.
* **Letting `file_ref` name a local path.** The whole reason the upload boundary
  exists; it would hand every client a local-file-read primitive.
* **Making the upload create a capture.** Uploading a file twice by accident
  would create two memories, and the client would lose the ability to decide
  what a capture *is* after the bytes are safe.
* **`PdfProcessor.supports` claiming all documents.** Guarantees an ambiguous
  route the day a second document format arrives.
* **PyMuPDF, pdfplumber, Poppler, LibreOffice, or a browser.** Native
  dependencies, licence entanglement, subprocesses, or a much larger surface than
  reading embedded text needs.
* **OCR as a fallback for a textless PDF.** A large dependency, a
  materially different provenance claim, and a per-capture cost — all smuggled in
  behind a capture that looked like it was about parsing.
* **`COMPLETE` with zero segments for a textless PDF.** Reporting that something
  was remembered when it was not, silently.
* **Generalizing replay to documents now.** A guarantee designed without a
  requirement is one nobody can check.
* **Garbage collection for unclaimed uploads.** Every policy it needs would be
  invented rather than derived, and the cost of deferring is disk.
