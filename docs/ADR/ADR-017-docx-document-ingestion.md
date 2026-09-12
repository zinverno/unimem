# ADR-017: DOCX document ingestion, and the proof the acquisition boundary is generic

Status: accepted (Macro Phase 3, PR 2). Phase 3 PR 1 is
[closed](#phase-3-pr-1-is-closed). Macro Phase 2 remains
[closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed). Macro
Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, enum, lifecycle, persistence, route, replay, or browser change: the
schema stays `0.2`. **Macro Phase 3 remains open after this PR.**

## Phase 3 PR 1 is closed

[ADR-016](ADR-016-pdf-document-ingestion.md) added `POST /v1/uploads`, the
`file_ref` staging flow, `PdfProcessor`, and the one widened intake capability
that let a `DOCUMENT` envelope name already-immutable bytes. It shipped as PR
#15 and is closed. This PR does not reopen the upload or staging architecture,
the raw store's identity or layout, PDF extraction semantics, PDF page
segmentation, PDF title precedence, webpage or browser semantics, replay
architecture, persistence, or the lifecycle.

ADR-016 made two decisions whose value was, at the time, entirely prospective:

* it made acquisition **format-independent** — `POST /v1/uploads` stores bytes
  and knows nothing about what they are;
* it made `PdfProcessor` claim **one MIME type** rather than the whole
  `DOCUMENT` payload type, explicitly so that a second document processor could
  arrive without either of them having to know the other exists.

Both were arguments. Neither was evidence. **This PR is the evidence**, and it
is the whole reason DOCX is the next thing built rather than, say, OCR: a second
format is the cheapest honest test of whether the first one's generalizations
were real or decorative.

They were real. The cost of the second document format, in the parts ADR-016
designed, was: **one tuple entry in intake, one name in the composition root's
processor list, and nothing else.** No route, no contract field, no enum, no
lifecycle state, no migration, no registry. Everything else in this PR is the
DOCX reader itself — which is work that could not have been avoided by any
design, because somebody has to know how to read an OOXML package.

## Context

The question this PR answers, exactly:

> How does a previously staged DOCX become a canonical `DOCUMENT`
> `ContentObject` through the same immutable-original and capture lifecycle
> introduced for PDF, while preserving document body order without pretending
> DOCX has stable physical page numbers?

The first two thirds of that question have an unexciting answer, and that is the
point. The last third does not, and it is where the design work in this PR
actually is.

## Decision

### The flow is the existing one, unchanged

```
DOCX bytes
  -> POST /v1/uploads      the same route, unchanged, still format-blind
  -> file_ref              "sha256:<64 lowercase hex>", the same reference form
  -> POST /v1/captures     the same canonical envelope, a different mime_type
  -> CaptureIntake         resolve + verify, then RECEIVED, then STORED
  -> ProcessorRouter       exactly one match
  -> DocxProcessor         ordered TEXT segments from the main document body
  -> ContentObject(document)
  -> COMPLETE
```

There is no `/v1/upload-docx`, no `/v1/docx`, no second staging mechanism, no
second raw store, and no second lifecycle. The upload route is not told that
DOCX exists and gains no validation: **acquisition stores bytes, the capture
declares what those bytes mean, and the processor validates that declaration.**
A client that stages a text file and declares it a DOCX gets a stored capture
and a truthful processing failure, which is the right place for that to happen —
the original is already immutable, and the failure is recorded against a real
capture rather than lost in a request that never became one.

### The supported DOCX MIME type is exactly one string

```
application/vnd.openxmlformats-officedocument.wordprocessingml.document
```

Matched exactly. Not a prefix, not case-insensitively, not with parameters, and
never inferred — not from a filename, not from a file extension, not from
`source.url`, and **not from looking inside the ZIP**. A client has to say.

That last one deserves stating plainly, because a DOCX *is* a ZIP and sniffing
it would be easy. Sniffing would move the decision about what a capture is from
the submitter to the server, on the strength of a guess, at a boundary whose
whole job is to record what somebody asserted. It would also make
`application/zip` mean "whatever this happens to contain", which is not a
capability anyone asked for.

Unsupported in this PR, and refused at intake rather than stranded:
`application/msword` (legacy binary `.doc`, a different format needing a
different reader), `.docm`, ODT, RTF, Pages, EPUB, and a bare `application/zip`.

### Intake generalized by exactly one value

`DOCUMENT_MIME_TYPE` became `DOCUMENT_MIME_TYPES`, a two-entry tuple, and the
equality check became a membership check. Everything else in
`CaptureIntake._staged_document` is untouched: the same ordering guarantee
(resolve and verify the staged object *before* the clock is read or `RECEIVED`
is written), the same `sha256:<digest>`-only reference resolution, the same
refusal of paths and URLs, the same refusal of `file_ref` alongside `text` or
`html`, the same `422 capture_material_unavailable` for material nobody staged,
the same no-second-write rule, and the same rule that no submitted value is
echoed into an error message.

**No registry, and no plugin system.** Two entries do not justify one. A
registry would be an abstraction invented ahead of the thing that would shape
it; the moment a format needs intake to treat it *differently* rather than
merely to allow it, that difference is what will say what the abstraction should
be. Nor does intake import the processors: what it decides is which declarations
this deployment accepts at its boundary, which is a fact about the build rather
than about any one parser, and the composition root is where the two lists are
kept honest.

### `DocxProcessor` shares no parsing logic with `PdfProcessor`

They have the same *shape* — `supports` reads three facts off the capture record,
`process` re-checks them, opens the original through `RawObjectStore`, extracts,
and assembles a `ContentObject` — and they share no code that reads a document.
That is deliberate rather than an oversight:

* **The formats have nothing in common below the surface.** One is a graph of
  indirect objects with a cross-reference table and per-page content streams;
  the other is a ZIP of XML parts with a relationship graph. A shared "document
  parser" abstraction over those two would be a shape neither of them has.
* **Their canonical outputs differ in a way that matters.** A PDF segment
  carries a physical page; a DOCX segment carries a body-block coordinate and no
  page at all. A base class factoring out "make a segment" would have to make
  that difference a parameter, which is a way of hiding the one thing a reader
  of this code most needs to see.
* **Sharing would couple two things that must be free to change apart.** PDF
  extraction semantics are frozen by this PR. DOCX semantics are at `0.1` and
  will move. A common ancestor is how a change to one silently becomes a change
  to the other.

What they share is the *port* — `Processor` — and that is the right amount.

### The router stays explicit, and there is no generic `DOCUMENT` handler

```
TEXT     -> TextProcessor      only
WEBPAGE  -> WebpageProcessor   only
PDF      -> PdfProcessor       only
DOCX     -> DocxProcessor      only
```

No first-match routing, no fallback processor, no precedence, and no ambiguity.
The router asks every processor and requires exactly one yes; registration order
decides nothing, and a test reverses the list to prove it.

This is where ADR-016's MIME-type claim pays off concretely. Had `PdfProcessor`
claimed `CapturePayloadType.DOCUMENT`, every DOCX capture would now be claimed
by two processors, and the router would report an ambiguity that was really a
design mistake one PR earlier. Instead the two claims are disjoint by
construction, and neither processor's code mentions the other.

### Scope: the main document body, in order

`DocxProcessor` reads the main document body of an ordinary unencrypted `.docx`,
walking it through python-docx's **public** body-order iteration
(`Document.iter_inner_content()`, which yields `Paragraph` and `Table` in
document order). A public API for body order was a requirement, not a
convenience: reaching into the OOXML tree to learn where a table sat relative to
the paragraphs around it would have made this build depend on a private
implementation detail to express its single most important guarantee.

Extracted:

1. body paragraphs;
2. body tables;

in the order they occur.

**Not** extracted in this PR: headers, footers, footnotes, endnotes, comments,
tracked-change history, deleted revision text, text boxes and drawing-layer
text, embedded files, embedded images, charts, equations as structured maths,
macros, document variables, and custom XML. Nothing is executed, and an external
relationship never causes a network fetch. The exact original is kept, so a
richer build can read precisely these bytes later.

### Paragraphs

One nonblank body paragraph becomes one `TEXT` segment carrying
`paragraph.text` **exactly as the reader returned it**. `strip` is used to decide
whether a paragraph is blank and for nothing else: the stored value is never the
stripped one. No Unicode normalization, no whitespace collapsing, no line-ending
rewriting, no punctuation repair, no paragraph joining, no sentence inference.

A blank or whitespace-only paragraph emits no segment and leaves no gap in
`position`.

### Tables, and a deterministic flattening

Tables are body text and must not silently disappear, and where they sit
relative to the surrounding paragraphs is part of what the document says. Each
body table is processed in row order, and **each nonblank row becomes one
`TEXT` segment** whose text is:

```
"\t".join(cell.text for cell in row.cells)
```

Cell strings are **not** stripped before joining; a row is blank only if every
cell is blank. No wrapper segment is emitted for the table itself — a table is a
container, and a segment for it would be text nobody wrote.

**The tab is an explicit canonical flattening boundary, not a claim about the
document.** A row is two-dimensional and a `Segment` carries a string, so the row
has to be flattened somehow; saying which character does it, and saying that it
is this build's choice rather than the document's content, is the difference
between a documented convention and a silent one.

Structural provenance is kept in `Segment.metadata`, which needed no contract
change:

```json
{"docx_block": "table_row", "table_index": 0, "row_index": 1}
{"docx_block": "paragraph"}
```

`table_index` counts body tables and `row_index` counts every row in its table,
including blank rows that emitted nothing — they are coordinates into the
document, not positions in the output. Together with `docx_block` they are what
let a consumer tell a flattened row from a paragraph that happens to contain
tabs.

`Segment.position` remains **one contiguous sequence across paragraphs and table
rows alike**, because they are one body and the order is the body's:

```
paragraph A   position 0
table row 1   position 1
table row 2   position 2
paragraph B   position 3
```

**Merged cells are a documented limitation.** `row.cells` reports a horizontally
merged cell once per grid column it spans, so its text repeats across those
positions, and this build takes that as given. Grid-span reconstruction is real
work with real ambiguities and no downstream requirement yet; a half-done
reconstruction would be worse than an honest flattening, and a test pins the
current behaviour down so the limitation is known rather than discovered.

### Provenance is `ORIGINAL`

Every segment — paragraph and table row alike — carries
`ProvenanceSourceType.ORIGINAL`, plus the capture id, the original asset's id,
and `docx`/`0.1`.

Not `PROCESSOR`: the processor rearranged structure into canonical segments, but
it did not author, translate, infer, or guess the words. Not `OCR` or `VISION`:
nothing looked at a pixel. Not `HTML`: nothing was converted. The material
originates directly in the immutable original, and a consumer needs to be able to
tell that from text a model produced — which is the entire reason
`ProvenanceSourceType` distinguishes them.

### There are no DOCX page numbers, and this is the central decision

`spatial` is `None` on every segment this processor emits.

A PDF page is a fact recorded in the file: the document says "these operators
draw on page three", and `SpatialLocation.page` reports what the document said.
**A DOCX page is not a fact; it is a result.** A `.docx` holds flow content, and
where a page break lands depends on the fonts installed, the page size, the
printer driver, the locale's line-breaking rules, and the specific renderer.
Two machines opening the same file can legitimately disagree about what is on
page four.

So a page number computed here would be *this server's rendering opinion
presented as a property of the client's document* — the same category of
falsehood as a base64 blob in `payload.text`, and worse for being plausible. The
options were:

1. **invent page numbers by estimation** — rejected: confidently wrong, and
   indistinguishable downstream from the PDF's real ones;
2. **render with LibreOffice or Word to paginate** — rejected: a subprocess, a
   system dependency, a renderer in the ingestion path, and *still* only one
   renderer's opinion;
3. **record no page** — chosen.

`SpatialLocation.heading` is not a substitute, either. It is not there to hold a
Word style name, and semantic heading hierarchy is a different question from
physical layout.

Consequently, one canonical `document` type gives two honest answers: a PDF
segment says which page it was on, and a DOCX segment says it does not know —
because it genuinely does not, and the file does not either.

### Heading styles are not interpreted

A Word `Heading 1` is a paragraph of `TEXT` in this build. It does not become a
`SECTION` segment, does not create a hierarchy, does not populate
`SpatialLocation.heading`, and does not become the title.

Style-to-structure mapping is a real design question — which styles count, what
a nesting looks like in a flat segment list, what happens to documents that fake
headings with bold text, how custom style names are handled — and every answer to
it is a guess until something downstream needs the structure for a stated
purpose. Nothing does yet. Storing the text truthfully now costs nothing later;
storing an invented hierarchy now would have to be undone.

### Title precedence

1. the submitted `CaptureRecord.title`, exactly as given;
2. otherwise a nonblank core-properties `title`;
3. otherwise none.

A person who titled their capture said what they wanted it called, and no
extraction outranks that. The document's own core properties are its claim about
itself and are the obvious second. There is no third source: **not** the
uploaded filename (untrusted client text this build never even records), not the
`file_ref` or digest (which name bytes, not a document), not the first paragraph
or the first heading (which invent a name the document never carried), and not
`subject`, `author`, or `keywords` — a document-metadata design of its own that
this PR does not open.

A whitespace-only core title is absent. Nothing is trimmed or Unicode
normalized. An extracted title reaches the `ContentObject` only and never
rewrites `CaptureRecord.title`, which means *what the submitter said*, always.

### A textless or image-only DOCX fails

If the package parses and no nonblank body paragraph or table row yields text,
the processor raises `ProcessingInputError` and the existing orchestrator marks
the capture `failed`. This covers an image-only document, a document whose only
text lives in unsupported headers or footers, and a body of nothing but blank
paragraphs.

Returning `COMPLETE` with zero segments would be the system reporting that it
remembered something when it remembered nothing. **No OCR runs, no image
extraction runs, and no vision model is called** to avoid saying so — the
refusal names the capability boundary instead. The exact `.docx` stays in raw
storage, so a richer build can read precisely those bytes later.

### Malformed and encrypted packages fail safely

A DOCX can fail at three layers, and each speaks its own vocabulary: the ZIP
container, the OPC package of parts and relationships, and the XML inside those
parts. All of them are translated to `ProcessingInputError`. A
password-protected Word document lands in the first layer — Office packages an
encrypted document inside a compound file rather than a ZIP, so the reader never
reaches any OOXML — and **no password is attempted**.

Only known package and parser input failures are translated. There is
deliberately no bare `except`: an `AttributeError` or `TypeError` from a defect
in this module is a bug, and dressing it up as "your document is malformed"
would hide it and lie to the client at the same time. The block that is guarded
is kept to parsing and iteration, with none of this module's own arithmetic or
lookups inside it.

No reader message reaches the HTTP response: archive member names, XML line and
column numbers, relationship URIs, filesystem paths, stack traces, and — for a
package of the wrong content type — the `repr` of the stream, which describes
this server rather than anyone's file.

**A known testing limitation, stated rather than glossed:** no test in this
repository feeds the reader a *genuine* encrypted OOXML package. Producing one
needs a compound-file writer and an AES implementation, which is a second
substantial dependency added to generate input this build's whole intent is to
refuse. What is tested is the part that decides the outcome — that a compound
file is not a ZIP and is refused at the container layer, with no password tried.
The claim about real encrypted packages therefore follows from the format rather
than from an executed example.

### Dependency

One new runtime dependency: **python-docx** (with the **lxml** it brings). Pure
Python, no system word processor, no subprocess. Held by test to
`core.processing.docx` alone, exactly as `pypdf` is held to
`core.processing.pdf`, and a further test asserts that nothing anywhere in
`core` imports `mammoth`, `docx2txt`, `pandoc`, `subprocess`, an image library,
or an OCR engine.

`mammoth` and `docx2txt` convert to formats this build does not want — one
produces HTML, which would mean ingesting a DOCX by pretending it is a webpage,
and the other flattens away exactly the structure this PR exists to preserve.
LibreOffice, Word automation, a headless browser, and pandoc are all renderers,
and a renderer in the ingestion path is how fictional page numbers get invented.

### Nothing else moved

* **No schema bump.** `SCHEMA_VERSION` stays `0.2`. Every contract this PR needs
  — `CapturePayloadType.DOCUMENT`, `ContentType.DOCUMENT`,
  `CapturePayload.file_ref`, `CapturePayload.mime_type`, `SegmentType.TEXT`,
  `ProvenanceSourceType.ORIGINAL`, `AssetRole.ORIGINAL`, and free-form
  `Segment.metadata` — already existed. No enum member was added, no lifecycle
  state was added, and there is no persistence migration.
* **PDF semantics are frozen.** Page-per-segment behaviour, blank-page
  behaviour, `SpatialLocation.page`, PDF title precedence, PDF provenance, the
  parser, and PDF error semantics are all untouched. A PDF capture serializes
  the same before and after this PR, modulo the fresh opaque ids and timestamps
  the existing code already mints per run.
* **No replay change.** `replay.py` is semantically unchanged and still
  `TEXT`-only, so a resent document capture id is `409` for DOCX exactly as for
  PDF. Document idempotency has not been asked for by a connector, and a
  guarantee designed without a requirement is one nobody can check.
* **No browser change.** Nothing under `clients/browser-extension/` moved: no
  file picker, no DOCX UI, no drag-and-drop, no new permission, no content
  script, no download interception.

## Consequences

**The generic staging boundary is now demonstrated rather than asserted**, which
is the result this PR was built to produce. A third document format — EPUB, say
— costs the same: one tuple entry, one processor, one name in the composition
root.

**One canonical `document` type now covers two formats with genuinely different
notions of location.** Consumers that assumed `spatial.page` is present on every
`document` segment are wrong, and were always wrong: `spatial` has been optional
since Phase 0A. A consumer that wants "where in the document" should read
`position`, which every segment has.

**Table text is flattened, and flattening loses structure.** A row's column
boundaries survive as tabs and its coordinates survive in metadata, but the
table as a grid does not, and merged cells repeat. Anything that needs real
table structure will need a richer segment type, which is a contract question.

**DOCX extraction is narrow and will grow.** Headers, footnotes, comments, and
revision history are all real content that this build does not read, and a
document whose value is in them fails rather than half-succeeding. That is the
intended trade: a truthful failure with the original preserved is recoverable,
and a confident half-answer is not.

**Macro Phase 3 remains open.** This PR closes only Phase 3 PR 2.

See [ADR-016](ADR-016-pdf-document-ingestion.md) for the acquisition step this
one reuses, and [ADR-004](ADR-004-processing-boundary-and-routing.md) for the
routing rule it depends on.
