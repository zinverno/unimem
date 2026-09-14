# ADR-019: Still-image ingestion, and why an uninterpreted image is complete

Status: accepted (Macro Phase 4, PR 1). Macro Phase 3 is
[closed](#macro-phase-3-is-closed). Macro Phase 2 remains
[closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed). Macro
Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).
No contract, enum, lifecycle, persistence, route, replay, or browser change: the
schema stays `0.2`. No new `core` runtime dependency.

**This ADR and the implementation it governs are one change.** The decision was
reviewed and accepted before any of it was written, which is why the document
reads as a contract rather than as a description; `ImageProcessor` and the
intake capability now exist in `src/`, built to it. **Macro Phase 4 remains open
after this PR.**

## Macro Phase 3 is closed

Phase 3 taught UniMem to ingest material that is not a string. PDF
([ADR-016](ADR-016-pdf-document-ingestion.md)) brought the staging boundary —
`POST /v1/uploads`, `sha256:<digest>` references, and the rule that uploading is
not capturing. DOCX ([ADR-017](ADR-017-docx-document-ingestion.md)) proved that
boundary was genuinely format-independent. Opt-in local PDF OCR
([ADR-018](ADR-018-opt-in-local-pdf-ocr.md)) added the first optional capability
and fixed, permanently, how extraction and recognition stay distinguishable. The
owner then ran the manual acceptance checklist by hand and all nine rows passed.

Phase 3 is closed and stays closed. Phase 4 does not reopen document ingestion,
PDF or DOCX extraction semantics, page segmentation, title precedence, the
upload or staging architecture, the raw store's identity or layout, OCR
semantics, the recognition port, replay, persistence, or the lifecycle. **The
behaviour and semantics of every processor Phase 3 shipped are unchanged by
Phase 4A** — no extraction rule, refusal, segment shape, provenance, processor
name or version moves — and no existing ADR is rewritten by this one. The one
edit Phase 4A makes inside a Phase 3 file is a docstring: `pdf_ocr.py` described
storage failures as propagating "exactly as it does from the other four
processors", and a hard-coded count of processors is a sentence that goes stale
every time one is added, so it now says "the other processors". No code, no
behaviour, no test.

Macro Phase 4 asks the next question about modality, and it is the first one
where the material is not a carrier for text at all:

> Every kind of material UniMem ingests today is something it reads *words* out
> of. What does it take to ingest something whose whole content is what it looks
> like?

## Context

The question this PR answers, exactly:

> How does a staged still image become a canonical `IMAGE` `ContentObject`
> through the existing capture lifecycle, without pretending that any
> interpretation of it happened?

The staging half is already solved. ADR-016 built a format-blind acquisition
route and said so at the time — *"images, audio, and arbitrary files stage
through it later without a redesign"* — and ADR-017 demonstrated it by adding a
second format that touched the route not at all. An image reaches `STORED`
through machinery that needs no change.

Everything hard is in the second half of the sentence.

### The honest answer for an image is not the honest answer for a document

ADR-016 decided, and ADR-018 reaffirmed, that a PDF yielding no text is a
*failure* rather than an empty success:

> It must not return `COMPLETE` with zero segments. That would be the system
> reporting that it remembered something when it remembered nothing, which is
> the one answer a memory system must never give.

That is correct, and this ADR does not weaken it by a word. But it is a
statement about *documents*, and the reason it holds is a property documents
have rather than a general rule about segments.

**For a document, the text is the artifact and the bytes are a container for
it.** A PDF is a transport for prose; a DOCX is a transport for a manuscript. A
content object holding the container and none of the prose has recorded the
wrapping paper. When `PdfProcessor` finds no embedded text, the thing the user
was trying to remember is genuinely absent from the result, and saying
`COMPLETE` would be a silent lie discovered months later by someone searching
for a document they were told was saved.

**For a still image, the pixels are the artifact.** There is no inner payload
the image is a container for. An image that has been staged, stored immutably,
content-addressed, described structurally, and tied to its capture *has been
remembered* — completely, and in the only form that is canonical for it. Nothing
about the artifact is missing from the result. What is missing is an
*interpretation* of it, which is a different thing, was never promised, and is
optional enrichment by design.

This is not the document rule bent to fit. It is the document rule's own
premise, applied to material that does not share it.

### Three ways to be dishonest about it

Each alternative to a zero-segment `COMPLETE` is *less* truthful, not more:

* **Emit a placeholder `VISUAL` segment.** `SegmentType.VISUAL` is deliberately
  not in `segment.py`'s `_TEXT_REQUIRED_TYPES`, so a text-less `VISUAL` segment
  *is* constructible — which makes this the tempting wrong answer rather than an
  impossible one. Such a segment would carry no fact that the `Asset` and
  `OriginalReference` do not already carry, and downstream it would be
  indistinguishable from a segment a vision model actually produced. That is
  fabricated visual understanding, wearing the exact contract shape real
  understanding will wear later. It is the single worst option available.
* **Fail the capture.** A photograph is valid content. Refusing it would mean
  UniMem declines to hold images, which inverts the system's purpose, and the
  refusal would be unactionable: a PDF refusal tells the submitter something
  true and useful ("no embedded text; enable OCR"), whereas an image refusal
  would say "this is a perfectly good PNG and we will not keep it."
* **Report `PARTIAL`.** `PARTIAL` describes a run that attempted something and
  achieved part of it. Nothing was attempted and missed here: structural
  extraction succeeded completely, and interpretation was never in scope for
  this processor. Marking it partial would burn a first-class lifecycle state on
  a run that was not degraded, and would be its own kind of false claim.

### `COMPLETE` was never a claim about understanding

The word is load-bearing and it is worth being exact about what it means, since
this is the first phase where someone could plausibly misread it.

`ProcessingRecord.status` describes *a run of a named processor*. `Processor.name`
and `version` describe processing semantics — `base.py` is explicit that they are
"not the package version and not a commit SHA, and they change when what the
processor produces changes" — and the same pair is recorded on every
`ProcessingRecord` and every `Provenance` the processor emits. `image@0.1`
therefore declares a fixed, published meaning: *the original is held and its
structure is described; nothing is interpreted.* A run that does that has
completed, exactly and verifiably.

`CaptureStatus.COMPLETE` means what
[ADR-009](ADR-009-processing-orchestration.md) and
[ADR-010](ADR-010-canonical-content-persistence.md) made it mean: exactly one
processor ran, it returned a canonical object for this capture, and that object
is durable. It has never meant "an AI understood this," and no phase has ever
been allowed to make it mean that.

## Decision

### The flow is the existing one, unchanged

```
image bytes
    -> POST /v1/uploads          (multipart)   -> sha256 file_ref
    -> POST /v1/captures         (CaptureEnvelope, payload.type = image)
    -> CaptureIntake             -> RECEIVED -> STORED
    -> ProcessorRouter           -> ImageProcessor
    -> ProcessingOrchestrator    -> a durable IMAGE ContentObject -> COMPLETE
```

No new route, no new request field, no new response field, no new schema
version, no new lifecycle state, and no new capture status. `POST /v1/uploads`
is reused byte-for-byte as ADR-016 built it: it mints no capture id, creates no
`CaptureRecord`, advances no lifecycle, runs no processor, produces no content,
reads no filename, and still answers `200` because the store deduplicates by
content and cannot honestly claim "created". `sha256:<digest>` remains the only
reference form intake resolves, and a path or URL is still refused without being
opened.

### A zero-segment `IMAGE` content object is a valid, complete result

An image that no interpreter has looked at normalizes to:

* `type = ContentType.IMAGE`;
* `source = ContentSource(capture_id, provider, url)` — provenance and source
  identity, carried from the capture;
* exactly one `Asset(role=AssetRole.ORIGINAL, mime_type=<as declared>,
  ref=sha256:<digest>, sha256=<digest>)`;
* `original = OriginalReference(asset_id, mime_type, sha256)`;
* `title` — the submitter's capture title, or none;
* `metadata` — one namespaced mapping of structural observations (below);
* **`segments = []`**;
* one `ProcessingRecord(processor="image", status=ProcessingStatus.COMPLETE)`.

The contracts already permit this and already prove it. `ContentObject.segments`
defaults to an empty list, and `_check_internal_consistency` only forbids
duplicate ids, foreign capture lineage, and unresolvable asset references —
every one of which is vacuously satisfied by an empty list. Invariant 5 ("every
`Segment` carries `Provenance`") constrains the segments that exist; it has
never required one to exist. `MarkdownRenderer` already renders a segment-less
object as its title or the empty string and invents nothing, and the JSON
renderer round-trips it. The orchestrator inspects `source.capture_id` and
nothing else about the shape of what a processor returns.

**Nothing in `src/core/contracts/` changes, and the schema stays `0.2`.** The
enums have contained `CapturePayloadType.IMAGE`, `ContentType.IMAGE`,
`AssetRole.ORIGINAL`, `SegmentType.VISUAL` and `ProvenanceSourceType.VISION`
since Phase 0A. `CapturePayload` has required `file_ref` for an `IMAGE` payload
since Phase 0A. This phase consumes vocabulary that was already there, exactly
as Phases 2 and 3 did.

### ADR-016 and ADR-018 are narrowed in scope, not contradicted

Both remain correct and both remain in force **for documents**. `PdfProcessor`
still refuses a textless PDF. `PdfOcrProcessor` still refuses a document from
which neither extraction nor recognition read anything. Neither file is edited
by this phase, neither ADR is rewritten, and no document capture behaves
differently after Phase 4A than before it.

What changes is the reach of the sentence, not its truth. "A `COMPLETE` object
with zero segments would be a lie" was written about material whose content is
text, and it stays true there. It does not extend to material whose content is
pixels, and it was never tested against such material because none existed in
the build. Recording that boundary here — rather than letting a future reader
find `pdf.py` and conclude `image.py` is a bug — is the whole reason this ADR
exists.

### Placeholder content is forbidden, and the rule is now an invariant

`ImageProcessor` must not invent:

* text of any kind, including a filename, a digest, a dimension string, or a
  description of the file;
* an empty or text-less `VISUAL` segment, or a segment of any other type;
* a caption, tag, label, topic, entity, or semantic class;
* a `DerivedContent.summary`;
* `ProvenanceSourceType.OCR` or `VISION` provenance when no recognizer and no
  model ran;
* a title read out of the image, inferred from the filename, or derived from the
  reference or digest;
* `ProcessingStatus.PARTIAL` to signal that interpretation was not attempted.

Absence of interpretation is represented by absence. `docs/ARCHITECTURE.md`
gains architectural invariant 19 saying so generally, because the rule is not
about images — every processor Phases 0–3 shipped already honours it — and
because this is the first phase where breaking it would have been convenient.

### Structural observations live in `ContentObject.metadata`

One namespaced mapping under the key `image`, following the `pdf_ocr` precedent
in `core/processing/pdf_ocr.py`: one key named for the processor rather than a
scattering of top-level names that the next modality will collide with.

Three entries are required, and they are the whole of the required vocabulary:

```
metadata = {
    "image": {
        "encoded_format": "png" | "jpeg",   # what the header bytes showed
        "encoded_width":  <positive int>,   # pixels, exactly as encoded
        "encoded_height": <positive int>,   # pixels, exactly as encoded
    }
}
```

Three things about those names. `encoded_format` records what the *header* was
observed to be, which is a different fact from the `mime_type` the submitter
*declared* and which travels on the asset; in a stored 4A object they always
agree, because a mismatch is refused, and recording the observation rather than
echoing the declaration is the point. `encoded_width` and `encoded_height` are
named for what was measured and not for what a viewer would see: 4A performs no
orientation correction and reads no orientation tag, so an image whose metadata
asks for a rotation has encoded dimensions that are not its display dimensions.
Calling these keys `width` and `height` would quietly assert otherwise. This is
the same discipline ADR-018 applied when it named a key
`ocr_pages_without_text` rather than `blank_pages`: name the observation, never
the conclusion.

A further key may be added **only** when it is cheap, deterministic, and
literally present in the header bytes the parser already read — PNG's bit depth
and colour type, which sit in the same fixed IHDR read, and a JPEG SOF's
component count are the admissible examples. Anything requiring a second pass,
a chunk walk, a decode, or an inference is not admissible.

Never in this mapping, and never anywhere else on the object: EXIF or XMP of any
kind, GPS coordinates, capture device or camera identity, lens or exposure
data, embedded colour profiles, timestamps read out of the file, inferred
orientation, thumbnails, captions, tags, or semantic classes.

### OCR and vision are later, optional, and separate processors

Phase 4A contains no recognizer and no model. It imports no imaging library, no
rasterizer, no OCR engine, and no `subprocess`, and it adds no `core` runtime
dependency of any kind.

When image OCR arrives it takes the shape ADR-018 already settled: a narrow
typed port in `core`, the canonical policy in `core`, the machinery in an
adapter outside `core` behind an optional extra, and the choice made at the
composition root by an explicit deployment flag that no request can reach. The
OCR-enabled image processor **replaces** the default one rather than standing
beside it — both would claim the same MIME types, and the router's
exactly-one-match rule makes a deployment that registers both fail loudly rather
than silently picking one.

**One forward constraint is fixed now, because 4A is what creates it: enabling
OCR must never turn an image that ingests successfully today into a failure.**
For PDF, "nothing could be read" is a verdict about the document and is
correctly fatal. For an image it is a fact about the recognizer, never about the
artifact. An OCR-enabled image build therefore adds `OCR` segments with
`ProvenanceSourceType.OCR` when the recognizer returns words, and returns the
same zero-segment `COMPLETE` object described above when it does not. A
photograph of a sunset must ingest identically with OCR on and OCR off.
Discovering this during 4B would be discovering it too late, which is why it is
written down in 4A.

Vision is further out still and is not designed here. `SegmentType.VISUAL` and
`ProvenanceSourceType.VISION` stay unused and unsquatted in 4A precisely so they
remain available to mean what they say.

### Phase 4A reads `image/png` and `image/jpeg`, and nothing else

Intake gains one capability, shaped exactly like `_staged_document`: an `IMAGE`
payload must carry a `file_ref` naming a raw object this store already holds,
must declare a `mime_type`, and that type must be a member of a two-entry tuple.
Everything the document path refuses, the image path refuses identically — a
`file_ref` alongside `text` or `html` (a capture stores one raw original, and
this build will not choose between submitted representations), a missing
`mime_type`, a reference that is not `sha256:<64 lowercase hex>`, and material
that is not staged. No submitted string is echoed into a refusal message.

Two formats, and the reason is the dependency wall rather than taste. `core` may
not import an imaging library — `CLAUDE.md` states it, a test enforces it, and
the quality-gates job runs deliberately on a machine with no imaging stack
installed so that the claim stays testable. `Pillow` lives in the `[ocr]` extra
and may not move. `imghdr` was removed from the standard library in Python 3.13
and this project's floor is 3.13, so there is no stdlib option either. Phase 4A's
structural reader is therefore a small, hand-written, header-only parser in pure
Python, and the supported list is exactly what such a parser can read correctly
and cheaply:

* **`image/png`** — the dimensions sit at a fixed offset inside a mandatory
  first chunk. A correct reader is a single bounded read.
* **`image/jpeg`** — the dimensions sit in an `SOF` marker reached by a bounded
  marker walk. More work than PNG, well specified, and the format people
  actually have.

Deferred, with reasons rather than dismissals: **WebP** encodes dimensions three
different ways across `VP8`, `VP8L` and `VP8X`, which is three parsers rather
than one. **GIF** has a trivially readable header but is an animation container.
**TIFF** requires IFD traversal with variable endianness. **HEIC** and **AVIF**
require an ISO-BMFF parser and carry codec baggage. **Animated formats are
excluded from 4A as a class** — animated GIF and WebP, and APNG — because
ingesting a moving picture and recording it as a still is a false claim made by
the content type itself.

**SVG is excluded on different grounds, and the distinction matters.** The
others are deferred; SVG is not part of this slice at all. It is not a raster
still image but an active XML document with a materially different security
model — scripts, external entity references, and elements that reference network
resources — and admitting it would make "nothing is executed, fetched, or
opened" false for this modality. Any future decision to support it is a security
decision about a document format, not a widening of a still-image format list.

**This list is a scope boundary, not a permanent verdict.** It is a membership
test and a refusal message, exactly as `DOCUMENT_MIME_TYPES` is; a registry or
plugin system for two entries would be architecture standing in for a
requirement. Additional formats are deferred to a later explicit decision, and
specifically are *not* assigned to 4C.

### The declared MIME type routes; the header bytes are verified against it

These are two different jobs and conflating them is how a system starts
guessing.

**Routing is by declaration.** Intake does not sniff, read magic numbers, or
infer a format, and `ImageProcessor.supports` stays pure and storage-free —
payload type is `IMAGE`, a raw object exists, and its declared `mime_type` is one
this processor claims. This is ADR-016's rule and it is not reopened.

**Verification is by observation.** Before parsing anything, the processor
checks that the leading bytes are consistent with the declared type, and refuses
a contradiction with a `ProcessingInputError`. A file declared `image/png` whose
first eight bytes are not the PNG signature is refused; it is *not* re-examined
to discover what it "really" is, and it is *not* parsed as a JPEG because it
happens to start with `FF D8`.

Refusing a contradiction is not inferring a format. The system never searches
for what a file might be; it declines to proceed when a declaration and the
bytes disagree, which is the only way a declared-type architecture can stay
honest about material it cannot read.

### PNG: one fixed read of a complete IHDR chunk, and no decompression

The parser must read a single fixed-size prefix of **exactly 33 bytes**, which
is the whole of the PNG signature plus the whole of the mandatory `IHDR` chunk:

```
  8 bytes   PNG signature        89 50 4E 47 0D 0A 1A 0A
  4 bytes   IHDR length          big-endian, must be 13
  4 bytes   IHDR type            must be b"IHDR"
 13 bytes   IHDR data            width, height, bit depth, colour type,
                                 compression, filter, interlace
  4 bytes   IHDR CRC             big-endian CRC-32, validated
 ---------
 33 bytes
```

**The CRC is part of the required prefix, and reading 29 bytes is not
sufficient.** A 29-byte contract — signature plus length, type and data, with
the CRC treated as optional trailing bytes — is satisfied by a file whose bytes
stop immediately after the `IHDR` data, so a truncated PNG carrying an
incomplete mandatory chunk would be accepted and its dimensions recorded as
though the header were whole. The chunk is not present until its CRC is present.
Requiring all 33 bytes makes truncation at that boundary a refusal rather than a
silent success, and validating the CRC additionally catches a header that is
complete but corrupt.

The parser must:

* read exactly the 33-byte prefix above, and refuse any file shorter than it;
* require the signature to be exactly `89 50 4E 47 0D 0A 1A 0A`;
* require the first chunk's declared length to be exactly `13` and its type to
  be exactly `IHDR` — the PNG specification makes `IHDR` mandatory and first, so
  a file where it is neither is not a PNG this build reads;
* take width and height as big-endian unsigned 32-bit integers from the first
  eight bytes of the `IHDR` data, and require both to be nonzero, which the
  specification also requires;
* **validate the `IHDR` CRC-32**, computed over `b"IHDR"` followed by the 13
  `IHDR` data bytes — chunk type and chunk data, per the PNG specification, and
  not over the length field — against the 4-byte big-endian value that follows,
  and refuse a mismatch;
* **validate the structural legality of the `IHDR` fields already in those 13
  bytes**: compression method must be `0`, filter method must be `0`, interlace
  method must be `0` or `1`, and the bit depth must be legal for the colour
  type —

  | colour type | legal bit depths |
  | ----------- | ---------------- |
  | `0` greyscale | 1, 2, 4, 8, 16 |
  | `2` truecolour | 8, 16 |
  | `3` indexed | 1, 2, 4, 8 |
  | `4` greyscale + alpha | 8, 16 |
  | `6` truecolour + alpha | 8, 16 |

  Any other colour type, or a bit depth not paired with its colour type above,
  is refused. These fields cost nothing to check — they are bytes the parser has
  already read — and checking them is what makes "this is a structurally legal
  PNG header" a verified statement rather than an assumption drawn from a
  signature match;
* refuse, as `ProcessingInputError`, any file failing any check above.

**The CRC check is header integrity, not decompression.** It is a checksum over
33 bytes the parser is already holding, computed with the standard library's
CRC-32 (`zlib.crc32`, equivalently `binascii.crc32`) — a hash function that
happens to live in the `zlib` module, not an invocation of inflate. Nothing is
decompressed at any point.

The parser must **not** walk beyond that prefix to any further chunk, read
`IDAT`, read ancillary chunks (`tEXt`, `zTXt`, `iTXt`, `eXIf`, `iCCP` among
them), decompress anything, or parse EXIF, XMP or ICC data. The bound therefore
needs no policy: one read of a fixed 33 bytes, regardless of file size. This
also disposes of compressed-ancillary-chunk and chunk-flood concerns by
construction — the parser never reaches them.

Bit depth, colour type, compression, filter and interlace are **validated but
not promoted**: they gate acceptance and they do not become required canonical
metadata. The required `image` mapping stays exactly the three keys fixed above.
A later implementation may surface bit depth and colour type under the
cheap-and-header-present rule, but Phase 4A does not require it to.

### JPEG: a bounded marker walk, and no entropy decoding

The parser must:

* require the first two bytes to be `FF D8` (SOI);
* walk marker segments from there, each introduced by one or more `FF` fill
  bytes followed by a marker byte, and — for markers that carry one — a 2-byte
  big-endian length that includes its own two bytes, so the payload is
  `length - 2`;
* handle standalone markers explicitly: `TEM` (`01`) and `RST0`–`RST7`
  (`D0`–`D7`) carry no length and are skipped as single bytes; fill `FF` bytes
  are consumed;
* stop and take dimensions at the first **supported** `SOF`: `SOF0`–`SOF3`
  (`C0`–`C3`), `SOF5`–`SOF7` (`C5`–`C7`), `SOF9`–`SOF11` (`C9`–`CB`), and
  `SOF13`–`SOF15` (`CD`–`CF`). The markers interleaved in that numeric range
  are **not** frame headers and must not be treated as such: `C4` is `DHT`,
  `C8` is the reserved `JPG`, and `CC` is `DAC`;
* read the `SOF` payload as precision (1 byte), height (2 bytes big-endian),
  width (2 bytes big-endian), component count (1 byte), and require height and
  width to be nonzero;
* terminate the walk at `SOS` (`FF DA`) or `EOI` (`FF D9`) and refuse if no
  supported `SOF` was found before either — entropy-coded data begins at `SOS`,
  and this build does not enter it;
* refuse, as `ProcessingInputError`, a declared segment length below `2`, a
  segment that extends past the end of the file, a byte where an `FF` marker
  introducer was required, or any truncation.

**The explicit finite bounds on the walk are: at most 256 marker segments
examined, and at most 1 MiB (1 048 576 bytes) of the file consumed.** Exceeding
either is a refusal. Both are generous by orders of magnitude — a real JPEG's
`SOF` follows its `APPn`, `DQT` and `DHT` segments within a few kilobytes — and
both exist to make the walk provably finite over a crafted file, which is a
parser-termination concern and nothing else. A refusal on these grounds says
this build's parser declined to keep looking; it is not a claim that the file is
invalid JPEG.

The parser must **not** Huffman- or arithmetic-decode, dequantize, inverse-DCT,
colour-convert, or in any other way process entropy-coded image data, and must
not read `APPn` payloads for their content.

### Encoded dimensions are recorded truthfully, and are not capped in 4A

A large encoded width or height is **not** malformed and must not be treated as
such. A header declaring 100 000 × 100 000 is a header stating a fact about how
the image is encoded; the parser reads that fact, records it, and moves on.
Because 4A never decodes, a declared dimension is an integer and costs nothing
to hold.

Phase 4A therefore imposes **no encoded-dimension ceiling**. Validation is
structural legality per the format specification — signature, chunk or marker
structure, mandatory fields, nonzero dimensions, no truncation — and nothing
else. Refusing a legal image because a hypothetical future decoder would find it
expensive would be this build declining to record something true about a file it
can read perfectly well, and it would put a decode-safety policy in a phase that
performs no decode.

**The obligation this creates lands squarely on whoever adds decoding.** Any
future path that rasterizes, decodes, thumbnails, or recognizes — Phase 4B's
image OCR first among them — **must** enforce a pixel and allocation budget
computed from the encoded dimensions **before** allocating or decoding, and must
refuse rather than attempt an image that exceeds it. That is where a
decompression bomb becomes real and that is where the limit belongs. Note that
`Pillow`'s `MAX_IMAGE_PIXELS` and `DecompressionBombWarning` are relevant
controls there, and that `pyproject.toml`'s `-W error` makes such a warning
fatal under test, which is a property worth keeping rather than suppressing.

### The upload route has no size limit, and Phase 4A does not add one

`POST /v1/uploads` accepts a multipart part of any size and streams it into the
raw store in bounded chunks. There is no request-size limit, no quota, no
per-client budget, and no disk-usage policy anywhere in the build.

This is recorded here as a real, pre-existing resource and availability
limitation. It is not introduced by images and it is not specific to them: it
has applied to every PDF and DOCX since ADR-016, alongside the unclaimed-object
accumulation that ADR already recorded.

**Phase 4A does not fix it, and deliberately does not.** A generic upload-size
policy is cross-modality hardening of a shared route: it affects documents and
every future modality equally, it needs limits derived from a real deployment
requirement rather than invented to round out a slice, and putting it inside a
still-image vertical slice would both widen this PR and bury the decision
somewhere nobody will look for it. The route is unchanged by this phase. The
server also remains unauthenticated and localhost-bound, which is the control
that currently bounds who can reach the route at all.

### Everything else is unchanged

No contract, enum, or validator. No schema version. No lifecycle state, capture
status, or orchestration rule. No route, request field, response field, or error
mapping. No persistence change. No renderer change — a 4A image object's
Markdown projection is its title or the empty string, which is the correct lossy
result and the cheapest available proof that nothing was fabricated. No replay
change: completed replay stays as narrow as ADR-013 and ADR-016 left it, and
images get no replay guarantee. No browser-extension change; there is no
screenshot or clipboard connector in this phase. No CI, tooling, or dependency
change.

## Scope

### In scope for Phase 4A

* `CapturePayloadType.IMAGE` accepted at intake: `file_ref`-backed, declared
  `mime_type` in exactly `{image/png, image/jpeg}`, resolved through the
  existing `sha256:<digest>` staging path.
* `ImageProcessor` in `core`, `name="image"`, `version="0.1"`: header-only
  structural inspection, one `ORIGINAL` asset, `OriginalReference`, the
  submitter's title or none, the three-key `image` metadata mapping,
  **`segments = []`**, and one `COMPLETE` `ProcessingRecord`.
* Verification that header bytes are consistent with the declared MIME type, and
  a typed refusal when they are not.
* Bounded, deterministic PNG and JPEG header parsing to the limits fixed above.
* Typed `ProcessingInputError` for malformed, truncated, structurally illegal,
  or type-mismatched input.
* One name added to the composition root's processor list.
* Tests: intake capability, processor behaviour including the refusal paths, an
  end-to-end API integration through `COMPLETE` and `GET /v1/captures/{id}/content`,
  and a Markdown-projection assertion proving nothing is invented.
* This ADR, architectural invariant 19, and the matching `ARCHITECTURE.md`
  section.

### Out of scope for Phase 4A

* Any contract, enum, schema, lifecycle, capture-status, route, replay, or
  persistence change.
* OCR, vision, captioning, classification, tagging, description, or any model of
  any kind.
* Any `VISUAL` or `OCR` segment. Phase 4A emits **zero** segments, always.
* EXIF, XMP, ICC, GPS, device identity, timestamps read from the file, and
  orientation correction.
* Thumbnails, resizing, transcoding, re-encoding, normalization, and any second
  asset.
* Any pixel decode, and therefore any decode-time resource budget — that
  obligation belongs to the phase that introduces decoding.
* WebP, GIF, TIFF, HEIC, AVIF, SVG, and animated formats.
* A generic upload-size limit or any change to `POST /v1/uploads`.
* Any new `core` runtime dependency; any imaging library, rasterizer, OCR
  engine, or `subprocess` in `core`.
* Embeddings, retrieval, `ctxalloc`, and agent routing.
* Browser screenshot or clipboard capture.
* Handwriting and layout-understanding claims of any kind.

## Security

* **No decode, so no decompression bomb.** The parser reads headers and stops.
  Declared dimensions are integers, not allocations.
* **Bounded parsing by construction.** PNG is one fixed 33-byte read — a
  complete, CRC-validated, structurally checked `IHDR` — with no chunk walk and
  no decompression. JPEG is a marker walk with explicit finite limits —
  256 segments, 1 MiB — that terminates at `SOS` and never enters entropy-coded
  data.
* **Nothing is executed, fetched, or opened.** The only input is the byte stream
  the raw object store hands over. A file that is simultaneously a valid PNG and
  a valid archive or HTML document is inert here, because nothing renders,
  extracts, or follows anything — and the content object claims only "declared
  X, header consistent with X", never "this is an X".
* **SVG is excluded**, so no active document reaches a parser in this phase.
* **No metadata harvesting.** EXIF and XMP are not parsed, so GPS coordinates,
  device serials, owner names, and EXIF thumbnails — which can preserve a region
  cropped out of the visible image — are never read into canonical content. They
  remain in the immutable original, where a later phase can decide about them
  deliberately rather than by omission.
* **No arbitrary file read and no network**, unchanged from ADR-016: only
  `sha256:<digest>` is resolved, and a path or URL is refused without being
  opened.
* **The filename is still never read**, so it reaches no path, identity, title,
  or record.
* **Nothing is echoed.** A refusal names the payload type, the field names, and
  the MIME types this build supports; it repeats no `file_ref`, no declared
  type, and no parser detail, and no stack trace or temporary path reaches the
  wire.
* **Recorded, not fixed:** no generic upload-size limit exists, and the server
  remains unauthenticated and localhost-bound.

## Consequences

* UniMem ingests its first modality that is not a carrier for text, and does so
  without claiming to understand it.
* The system gains a written, general rule — invariant 19 — separating what a
  processor observed from what it produced from what it did not do.
* A zero-segment content object becomes a normal, expected result rather than an
  edge case, which every consumer of `ContentObject` must now handle. The
  contracts and both renderers already do.
* An image object's Markdown projection is usually empty. That is correct and is
  the visible consequence of refusing to fabricate.
* Image captures get no replay guarantee, so a lost response costs a `409` and
  an observational `GET`, exactly as documents do.
* The format list is narrow enough that common images — WebP and HEIC
  especially — will be refused at intake with an actionable message. That is the
  honest cost of not guessing, and the clearest possible signal about which
  format is worth adding next.
* The absent upload-size limit is now recorded in an accepted decision rather
  than being an unstated gap, which is what makes it schedulable.

## Alternatives rejected

* **A placeholder `VISUAL` segment for an uninterpreted image.** Fabricated
  visual understanding in the exact contract shape real understanding will use.
* **Failing a valid image because no text was found.** Inverts the system's
  purpose and produces an unactionable refusal.
* **`PARTIAL` for a run that was not degraded.** Burns a first-class lifecycle
  state to express "a capability we never claimed did not run".
* **A new `ContentType`, segment type, provenance source, or schema version.**
  The vocabulary has existed since Phase 0A; a bump here would be making the
  phase look substantial rather than making it correct.
* **An image-specific upload route or an HTTP-specific image contract.**
  `CaptureEnvelope` already expresses the operation exactly, and a format in a
  URL guarantees a second identical route for the next modality.
* **Putting `Pillow` in `core` to read dimensions.** A native imaging dependency
  in the kernel, breaking a stated invariant and the gates that prove it, to
  learn two integers that sit at a fixed offset.
* **Sniffing the format instead of trusting the declaration.** Moves format
  authority from the boundary into the parser and makes the system guess; the
  narrow verification above gets the safety without the guessing.
* **Capping encoded dimensions in 4A as a decode-safety measure.** Refuses
  legal images this build can read, and puts a decode policy in a phase that
  performs no decode. The budget belongs where the allocation happens.
* **A generic upload-size limit in this slice.** Cross-modality hardening of a
  shared route, with limits that would be invented rather than derived, buried
  inside a still-image PR.
* **Supporting every common raster format now.** Four more parsers, an
  animation-versus-still decision, and an ISO-BMFF implementation, in the slice
  whose job is to establish what an uninterpreted image *is*.
* **OCR in 4A, or OCR as a prerequisite for image ingestion.** A large optional
  dependency and a materially different provenance claim, smuggled in behind a
  capture that was about holding a picture.
