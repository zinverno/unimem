# ADR-020: Opt-in local image OCR, and why an enrichment budget is not a validity rule

Status: accepted (Macro Phase 4, PR 2). Phase 4 PR 1 is
[closed as an implementation slice](#phase-4-pr-1-is-closed-as-a-slice). Macro
Phase 3 remains [closed](ADR-019-still-image-ingestion.md#macro-phase-3-is-closed).
Macro Phase 2 remains
[closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed). Macro Phase 1
remains [closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed). No
contract, enum, lifecycle, persistence, route, replay, or browser change: the
schema stays `0.2`. No new `core` runtime dependency and no new Python
dependency of any kind. **Macro Phase 4 remains open after this PR.**

**This ADR and the implementation it governs are one change.** The decision was
reviewed and accepted in full *before* any of it was written — first as an
architecture memo, then as this document, then as three rounds of owner
clarification — which is why it reads as a contract rather than as a description.
The code now exists in `src/`, built to it. Every statement below is binding, and
a divergence is a new ADR rather than a silent edit.

## Phase 4 PR 1 is closed as a slice

[ADR-019](ADR-019-still-image-ingestion.md) taught UniMem to ingest a still
image: `IMAGE` intake for exactly `image/png` and `image/jpeg`, `ImageProcessor`
at `image@0.1`, the immutable original as the one canonical asset, three
header-only structural observations under `metadata["image"]`, and — the
decision the slice turned on — **`segments = []` with `ProcessingStatus.COMPLETE`
for a valid image nothing has interpreted**.

That slice is closed. This PR does not reopen the supported format list, the PNG
or JPEG header parsers, the intake capability, the structural metadata
vocabulary, the zero-segment result, the declared-type routing rule, the
header-verification rule, or the absence of a generic upload-size limit.
`src/core/processing/image.py` gains **one additive public wrapper over its own
existing parsers** and nothing else; no parser byte, no refusal, no metadata key
and no emitted object changes for a capture that ingests today.

Phase 4C — owner manual acceptance and Macro Phase 4 closure — is still not
designed.

## Context

The question this PR answers, exactly:

> What are the failure and resource semantics of an OCR-enabled image processor
> when the underlying image is *already* valid canonical content?

ADR-019 fixed one half of the answer in advance, because 4A is what created the
obligation:

> **enabling OCR must never turn an image that ingests successfully today into a
> failure.** For PDF, "nothing could be read" is a verdict about the document and
> is correctly fatal. For an image it is a fact about the recognizer, never about
> the artifact.

and, in the same breath, the other half:

> Any future path that rasterizes, decodes, thumbnails, or recognizes — Phase 4B's
> image OCR first among them — **must** enforce a pixel and allocation budget
> computed from the encoded dimensions **before** allocating or decoding, and must
> refuse rather than attempt an image that exceeds it.

Read carelessly those two sentences collide: the first forbids a failure, the
second demands a refusal. Reconciling them is the whole of this decision, and the
reconciliation is one sentence:

**The budget refuses the *decode*. It does not refuse the *capture*.**

### The asymmetry that drives everything

Every OCR decision in this build so far was made for documents, where recognition
is the *only* path to content. `PdfOcrProcessor` exists because a scanned PDF that
nobody recognizes yields nothing at all; if recognition fails there, the capture
has no content and
[ADR-016](ADR-016-pdf-document-ingestion.md)'s rule applies — a `COMPLETE` object
with nothing in it would be the system reporting that it remembered something
when it remembered nothing.

For an image none of that holds. ADR-019 established that the pixels *are* the
artifact, so an image that is staged, stored immutably, content-addressed,
described structurally and tied to its capture **has already been remembered
completely** before any recognizer is consulted. Recognition adds a reading of
whatever words happen to be in the picture. It is enrichment over content that is
already canonical.

That single asymmetry decides every row of the matrix below, and it decides them
in different directions — which is why this ADR refuses to collapse them into one
generic "OCR failed".

### Six situations, and they are six different facts

A design that answers "the OCR did not produce a segment" uniformly is a design
that has thrown away information the system genuinely has. These are the six, and
no two of them mean the same thing:

1. The engine ran and read words.
2. The engine ran and read nothing.
3. The engine was never asked, because this deployment's enrichment budget said
   not to.
4. The engine was not present when the server was asked to start.
5. The engine was asked and **no trusted result came back** — it was missing,
   killed, timed out, exited nonzero, printed something undecodable, answered
   inconsistently, **or could not decode the encoded image data behind the header
   Phase 4A read**.
6. The image is not the image it says it is, as Phase 4A's header and MIME
   validation can prove.

(2) is a fact about the recognizer. (3) is a fact about this deployment's policy.
(6) is a fact about the submitted material that this build can actually
demonstrate. **(5) is the one the system cannot classify**, and pretending
otherwise is the mistake this ADR is most concerned to avoid: it mixes machine
conditions with a genuine inability to decode these particular bytes, and the
adapter has no stable typed signal that separates them. Anything that reports (5)
as (2) is claiming a recognizer read a picture it never successfully opened;
anything that reports (3) as (6) is calling a perfectly good photograph invalid.
Both are lies the durable record would carry forever.

## Decision

### The flow is the existing one, unchanged

```
image bytes
    -> POST /v1/uploads          (multipart)   -> sha256 file_ref
    -> POST /v1/captures         (CaptureEnvelope, payload.type = image)
    -> CaptureIntake             -> RECEIVED -> STORED
    -> ProcessorRouter           -> ImageOcrProcessor    (OCR-enabled build)
                                 -> ImageProcessor       (default build)
    -> ProcessingOrchestrator    -> a durable IMAGE ContentObject -> COMPLETE
```

No new route, no new request field, no new response field, no new schema version,
no new lifecycle state, and no new capture status. Intake is untouched: an
OCR-enabled deployment accepts exactly the `IMAGE` payloads a default one accepts,
declaring exactly the same two MIME types, resolved through the same
`sha256:<digest>` staging path.

### Recognition is a deployment capability, and `--image-ocr` is the whole of it

One new flag on the composition root, and nothing else:

```
python -m unimem_api --data-dir ./data                            # neither
python -m unimem_api --data-dir ./data --pdf-ocr                  # PDF OCR only
python -m unimem_api --data-dir ./data --image-ocr                # image OCR only
python -m unimem_api --data-dir ./data --pdf-ocr --image-ocr      # both
```

All four combinations are valid and the two capabilities are **independent**.
`--image-ocr` is deliberately *not* folded into `--pdf-ocr`: the two have
different prerequisites (image OCR needs no rasterizer and no imaging library),
different limits, different data paths, and — as this ADR spends most of its
length establishing — different failure semantics. One flag governing both would
force a deployment that wants only image recognition to install and load PDFium
and Pillow it never executes.

**No request can reach any of it.** Not a `CaptureEnvelope` field, not a
`CaptureIntent`, not a MIME variant, not an HTTP header, not a router precedence
rule, not a query parameter. The same capture body produces the same *acceptance*
in every deployment; what differs is what the deployment is able to read out of
the pixels afterwards. This is
[ADR-018](ADR-018-opt-in-local-pdf-ocr.md)'s rule, applied to a second capability
without weakening it.

### Both image processors claim the same capability, and exactly one is registered

`ImageOcrProcessor.supports()` asks **byte-for-byte the same three questions**
`ImageProcessor.supports()` asks: the payload type is `IMAGE`, a raw object
exists, and its declared `mime_type` is one of `IMAGE_MIME_TYPES`. It does not
claim a wider set, does not sniff bytes to decide, and does not read storage to
route.

The two are therefore *alternatives*, exactly as the two PDF processors are.
`build_local_app` returns one or the other from a single `if`, because which
implementation of a claim runs is a composition decision; registering both is an
`AmbiguousProcessorError` on every image capture rather than a silent precedence.
Registration order is still not precedence, and there is still no fallback
processor.

```python
def _image_processor(raw_store, image_ocr: ImageOcr | None) -> Processor:
    if image_ocr is None:
        return ImageProcessor(raw_store)
    return ImageOcrProcessor(raw_store, image_ocr)


def build_local_app(
    data_dir: Path,
    *,
    pdf_ocr: PdfPageOcr | None = None,
    image_ocr: ImageOcr | None = None,
) -> FastAPI: ...
```

`ImageOcrProcessor` takes the recognizer as a constructor argument, so a
deployment that supplied none cannot end up holding a processor with a `None`
engine that fails on the first photograph. `wiring.py` still imports no native
library and no `subprocess`: the concrete adapter is built one layer out, in
`unimem_api.__main__`, behind a function-local import that a default start never
executes.

### Resource limits bound the enrichment, not the artifact

This is the central decision of this ADR and every consequence below follows from
it, so it is stated once without hedging:

> **Image OCR resource limits are limits on *enrichment*. They are not validity
> constraints on the canonical image.**

A structurally valid PNG or JPEG that exceeds this deployment's OCR budget **is
still a valid image, and is still remembered completely.** The limit governs how
much work this deployment will spend *interpreting* a picture. It says nothing
about whether the picture is well-formed, whether it is worth keeping, or whether
UniMem can hold it — all three of which ADR-019 already answered, for these exact
bytes, in the affirmative.

So the truthful result of an over-budget image is a **successful capture with the
enrichment skipped and the skip recorded durably**: `HTTP 201`, a persisted
`ContentObject` carrying the same original, the same `OriginalReference`, the same
title semantics and the same `metadata["image"]` a default build would have
produced, `segments = []`, and one `image-ocr@0.1` `ProcessingRecord` with status
`COMPLETE`.

The three alternatives were considered and each is *less* truthful:

* **Durable `FAILED` / `422`.** Directly violates ADR-019's forward constraint, in
  the words it was written to prevent. It also makes a deployment budget speak as
  a verdict about the submitted material: a 422 tells a client their image is the
  problem, which is the one claim this condition cannot support. The same bytes
  would be accepted by the build next door.
* **Non-terminal `PROCESSING` / `503`.** A 503 means "come back later". The bound
  is deterministic and fixed at composition time, so retrying produces the
  identical refusal forever — the unterminating loop
  [ADR-016](ADR-016-pdf-document-ingestion.md) already refused to create when it
  made an unstaged `file_ref` a 422 rather than a 503. It would additionally
  strand a capture non-terminal for a condition the system understands completely.
* **`PARTIAL`.** ADR-019 already rejected `PARTIAL` for the adjacent case, and the
  mechanics make it worse than wrong: `ProcessingOrchestrator` never reads
  `ProcessingRecord.status`, so a `PARTIAL` record would sit underneath a
  `COMPLETE` `CaptureRecord` and the object would say two things at once. Making
  it reach `CaptureStatus.PARTIAL` requires a new orchestration rule, which means
  reopening Phase 0H — a closed phase — from inside a still-image slice.

**The reconciliation with ADR-019, stated exactly.** Both of its obligations are
met literally and neither is bent. The budget is computed from the
already-validated encoded dimensions and enforced before any allocation, any
decode, and any subprocess — so "refuse rather than attempt" is honoured to the
letter. And the capture succeeds — so "enabling OCR must never turn an image that
ingests successfully today into a failure" is honoured to the letter. The word
**refuse** in ADR-019 governs the decode it appears in the same sentence as. It
was never an instruction to propagate that refusal into a lifecycle verdict, and
everything else ADR-019 says about images points the other way.

ADR-019's parenthetical note that `Pillow`'s `MAX_IMAGE_PIXELS` and
`DecompressionBombWarning` are "relevant controls there" is moot rather than
contradicted: this build performs no in-process decode at all, so there is no
Pillow call to warn (see *The adapter hands original bytes to the engine*).

### An execution failure is an untrusted result, not a proven outage

The reasoning that produced the skip above does **not** extend to a recognizer
that was asked and did not deliver, and must not be allowed to. A budget is a
decision this build made before looking; an execution failure is the absence of
an answer it can trust.

**`ImageOcrExecutionError` means exactly one thing:**

> the OCR execution did not produce a trusted recognition result.

It does **not** mean, and must never be written or read as meaning, "this has been
proven to be a transient infrastructure outage."

That narrowing is not caution for its own sake; it follows from what Phase 4A
actually validates. **4A validates only the structural portion it deliberately
reads** — a PNG signature and one complete, CRC-checked, structurally legal
`IHDR`, or a JPEG `SOI` and the first fully present supported frame header. It
reads no `IDAT`, enters no entropy-coded scan, and decodes no pixel. **It
therefore does not prove that the encoded pixel stream behind that header is
decodable at all.** A file can pass every 4A check and still carry a truncated,
corrupt, or otherwise undecodable body.

So an invocation of a local Tesseract may fail because:

* the executable is missing or unusable after startup;
* the invocation timed out;
* the child crashed, was killed, or died to the OS out-of-memory killer;
* the engine exited nonzero;
* its stdout was not valid UTF-8;
* the adapter answered inconsistently with its own contract; **or**
* the encoded image data beyond Phase 4A's structural boundary is something
  Tesseract and Leptonica cannot decode.

The first six are conditions of this machine or this build. **The last may be
perfectly deterministic for the same bytes**, and would fail identically against a
freshly repaired deployment.

**The adapter has no stable typed signal that separates them**, and it will not
invent one. Tesseract reports a nonzero exit or a dead child for all of these
alike; the only thing that might distinguish them is the prose on the child's
standard error, which is another program's diagnostic text, is not a stable
interface, can name paths on this machine, and is deliberately never parsed —
exactly as ADR-018 refused to parse `PdfiumError`'s message. **No decoder is added
merely to classify the failure**, either: pulling an imaging library into this path
to find out *why* recognition failed would give up the entire security and
dependency position of this slice to improve a status code.

So the outcome is chosen for what can be said truthfully rather than for what
happens to be true:

1. **A durable `422`/`FAILED` is unavailable**, because the system cannot blame the
   canonical image. It may be the image, and it may equally be the machine, and 4A
   already proved the image is valid as far as this build reads it.
2. **Recording "OCR ran and found no text" is unavailable**, because it is a claim
   about a recognition that never completed.
3. **Classifying by parsing external-program prose is unavailable**, because that
   is not a classification, it is a guess with a stable-looking shape.

What remains is to persist nothing, leave the capture **non-terminal
`PROCESSING`**, and answer a fixed `503 image_ocr_unavailable`. **The 503 is the
conservative answer, not a diagnosis.** Two further reasons confirm it rather than
establish it: succeeding would degrade a broken deployment silently, producing an
endless stream of `201`s with no operator signal — the failure mode
`OcrPrerequisiteError` exists to prevent one layer down — and nothing is lost by
refusing, since the raw object is already staged, the bytes deduplicate, and
resubmitting is cheap.

**Retry semantics, stated precisely.** A retry against a repaired deployment **may**
succeed. It is **not guaranteed** to succeed for identical bytes: if the cause was
undecodable image data, the same submission will fail the same way indefinitely,
and this build cannot tell the client which case it is in. That is a real, recorded
limitation of the 503 and not a defect in it.

**This is still not the failure ADR-019 forbids.** Its constraint is about what the
system *says* about a picture: a durable `FAILED`, a 422, a verdict. A 503 over a
non-terminal capture asserts nothing about the image at all — which is the entire
reason `PdfOcrExecutionError` was placed outside the `ProcessingError` hierarchy
in ADR-018, and the reason `ImageOcrExecutionError` is placed outside it here. The
constraint forbids a verdict. Declining to reach one is the opposite of breaking
it.

The honest cost is recorded rather than hidden: a deployment whose engine is
permanently broken will `503` every image capture that a default deployment would
have accepted, and so will a single image whose body no local engine can decode.
Both are visible; neither is a claim.

### The complete failure matrix

| # | situation | engine invoked | decode / subprocess | content persisted | segments | `ProcessingRecord` | `CaptureRecord` | HTTP | what a retry means |
| - | --------- | -------------- | ------------------- | ----------------- | -------- | ------------------ | --------------- | ---- | ------------------ |
| A | valid image, recognition returns nonblank text | yes | yes | yes | exactly one `OCR` segment | `image-ocr@0.1` `COMPLETE` | `COMPLETE` | `201` | nothing to retry |
| B | valid image, recognition succeeds and returns empty or whitespace-only text | yes | yes | yes | `[]` — **no empty OCR segment** | `image-ocr@0.1` `COMPLETE` | `COMPLETE` | `201` | nothing to retry |
| C | valid image over a deterministic OCR resource bound | **no** | **no** | yes | `[]` | `image-ocr@0.1` `COMPLETE` | `COMPLETE` | `201` | the same skip, forever — which is why this is not a 503 |
| D | an OCR prerequisite is missing at startup | no | no | — | — | — | no capture exists | none — the process exits before the socket is bound | fix the machine and restart |
| E | no trusted recognition result — engine missing, killed, timed out, nonzero exit, undecodable output, inconsistent answer, **or encoded image data the engine cannot decode** | attempted, did not deliver | attempted; may not have started at all | **no** | — | none written | **`PROCESSING`** (non-terminal) | `503 image_ocr_unavailable` | **may** succeed against a repaired deployment; **not guaranteed** for identical bytes, since the cause may be the image data and this build cannot tell |
| F | malformed image, or bytes contradicting the declared MIME type | no — header validation fails first | no | no | — | none written | **`FAILED`** (durable) | `422 processing_failed` | pointless; the submission is the problem |

Row by row, in the wording that binds the implementation:

* **A** — one segment for the whole image, shaped as fixed below.
* **B** — the engine ran and found no nonblank text. **The image is not called
  blank.** An empty recognition result is evidence about the recognizer: a faint
  photograph, an unsupported script, a rotated sign, a picture of a sunset with no
  words in it, and a genuinely empty frame all land here and this build cannot
  tell them apart. `metadata` records what was observed — that the engine was
  invoked — and never the conclusion.
* **C** — examples are `encoded_width * encoded_height > max_encoded_pixels` and
  an original whose encoded byte count crosses `max_encoded_bytes`. Not a 422, not
  a 503, not `PARTIAL`. "Refuse the enrichment, not the artifact."
* **D** — startup fails before the socket is bound and before the data directory
  is touched, exactly as `--pdf-ocr` does. **Image OCR is never silently
  disabled**, never narrowed to whichever language pack happens to be installed,
  and nothing is installed, downloaded, or worked around to make up the difference.
* **E** — this must remain distinguishable, forever, from B. They are different
  facts and the durable record reflects that by having no durable record at all
  for E. The row is deliberately **not** titled "infrastructure failure": it
  covers a missing or broken engine *and* encoded image data no local engine can
  decode, and this build has no stable typed signal that tells them apart. The
  503 therefore says "no trusted recognition result", not "come back later and it
  will work".
* **F** — unchanged from Phase 4A. Header and MIME validation run *before* the
  recognizer is handed anything, so OCR is never attempted as a repair for an
  image the parser refused — the exact analogue of ADR-018's rule that extraction
  runs first and its failures are final. A recognizer pointed at bytes the parser
  rejected would either fail the same way or, far worse, read *something* out of a
  file nobody could open.

Raw-store failures are unchanged and keep their own type: `RawObjectStoreError` →
`503 storage_unavailable`, capture left `PROCESSING`. A defect in this code —
a `TypeError` from a bug — propagates as itself to the ASGI server's ordinary 500,
because a bug dressed up as a bad photograph is a bug that never gets fixed.

**A capture left `PROCESSING` is not automatically resumable, and this PR does not
change that.** Processing starts from `STORED` and from nothing else,
`GET /v1/captures/{id}` makes the state observable, and resubmitting the same id
is still `409`. That is the entire recovery story, unchanged from
[ADR-009](ADR-009-processing-orchestration.md), ADR-011 and ADR-018.

### The resource-limit signal is structured, and processor logic never reads a message

`ImageOcrLimitExceeded` is **not** a bare exception whose text has to be parsed.
It carries its meaning in attributes, because that meaning is control flow — the
processor branches on it to decide that a capture succeeds — and control flow that
depends on prose is control flow that breaks the first time a message is reworded
for clarity.

```python
class ImageOcrLimitExceeded(Exception):
    reason: Literal["encoded_pixel_limit", "encoded_byte_limit"]
    limit: int
```

* `reason` names which bound was crossed. It is a `Literal`, deliberately **not** a
  new member of any canonical contract enum: this is an internal control signal
  between an adapter and a processor, it never appears on a contract field, and
  minting a durable enum value for it would put a wire-format commitment behind a
  private arrangement.
* `limit` is the bound that was in force, as an integer, so the processor can
  record the number that actually applied rather than re-deriving it from a policy
  object it does not own.
* Further diagnostic attributes may be added when a concrete need justifies one.
  **What may never happen is a branch on `str(exc)`.**

The division of ownership is exact:

* **The adapter owns detection.** It knows the bound, it measures against it, and
  it raises.
* **`ImageOcrProcessor` owns the canonical meaning.** It catches the signal and
  renders it as what it actually means: a successful image ingestion with OCR
  skipped, and a durable record of which bound stopped it.

`ImageOcrLimitExceeded` sits **outside `ProcessingError`** — so the orchestrator
never sees it as a verdict — and also **outside `ImageOcrExecutionError`**, so no
`except` clause and no exception-handler registration can adopt one into the
other's behaviour by inheritance. They are siblings because they carry opposite
control-flow meaning: one ends in `201`, the other in `503`.

### A narrow sibling port, and `PdfPageOcr` is not generalized

New module `src/core/processing/image_recognition.py`:

```python
class ImageOcrExecutionError(Exception): ...


class ImageOcrLimitExceeded(Exception):
    reason: Literal["encoded_pixel_limit", "encoded_byte_limit"]
    limit: int


@dataclass(frozen=True, slots=True)
class ImageOcrResult:
    text: str
    engine: str
    engine_version: str
    settings: Mapping[str, JsonValue]


class ImageOcr(Protocol):
    def recognize_image(
        self,
        stream: BinaryIO,
        *,
        mime_type: str,
        encoded_width: int,
        encoded_height: int,
    ) -> ImageOcrResult: ...


def validate_image_ocr_result(result: ImageOcrResult) -> None: ...
```

`ImageOcr` does **not** inherit from `PdfPageOcr`, `PdfPageOcr` is **not** renamed
or widened, and **no generic `Recognizer` protocol is introduced in `core`**. The
case for keeping them apart is not an aesthetic preference about duplication; it
is that their semantic surfaces barely overlap:

| `PdfPageOcr` | `ImageOcr` |
| ------------ | ---------- |
| accepts a PDF stream and answers about *pages* | accepts one image and answers about one image |
| takes `embedded_pages` to exclude work the caller has better text for | has nothing to exclude |
| reports `page_count`, so "recognized to nothing" is distinguishable from "never looked at" | has one subject; the distinction does not exist |
| `validate_ocr_result` enforces five structural rules, the load-bearing one being that a *missing* page must not look like a blank page | has no missing-entry failure mode to guard |
| reports `rasterizer` and `rasterizer_version` | nothing rasterizes |
| may raise `ProcessingInputError` — for a PDF, "cannot open" genuinely is a verdict and correctly fatal | **may not**; ADR-019 forbids that outcome for an image, and `core` has already validated the header before the adapter sees a stream |

The last row is decisive. The two ports do not share an error hierarchy, let alone
a method. A shared base would have exactly one honest member — hand these bytes to
an engine and give me back a string — and that is an *infrastructure* surface, not
a domain one. It belongs in the adapter package, and that is where this ADR puts
it (see *One shared engine runner*).

Two further notes on the seam, both carried over from ADR-018 unchanged:

* **What crosses is values, not objects.** The input is a binary stream, never a
  caller-controlled path, because a path is a filesystem instruction and this port
  must not be able to express one. What comes back is a frozen dataclass of plain
  strings. No bitmap, image object, file handle, native handle, or temporary path
  ever reaches canonical processing.
* **The encoded dimensions cross the seam as parameters** so the adapter can
  enforce ADR-019's before-decode budget **without parsing the header a second
  time**. A second parser in the adapter would be a second source of truth about
  the same bytes, and the two would eventually disagree.

**Validating an adapter result.** `validate_image_ocr_result` is small, and its
smallness is a finding rather than an oversight: for one image there is no
structural consistency to check. It requires `engine` and `engine_version` to be
nonblank — both land in durable metadata, where `JsonMapping` imposes no
non-blankness of its own and an empty string would become a permanent lie — and
requires `settings` to be JSON-representable. A violation is an
`ImageOcrExecutionError`, never an input verdict: an implementation that answers
inconsistently has told us nothing about the image, so the image must not be
blamed for it.

Everything the PDF port gets from `validate_ocr_result` and the image port cannot
therefore becomes an **adapter obligation, stated in the port docstring and
enforced by tests: the adapter must raise rather than return an empty string on
any failure.** An empty `text` produced by a bug is indistinguishable from an
empty `text` produced by a blank photograph, and `core` cannot tell them apart. The
strictness of the adapter's own classification — nonzero exit raises, a timeout
raises, undecodable stdout raises — is what keeps row E out of row B.

### The processor, and how it reuses Phase 4A without touching it

`src/core/processing/image_ocr.py`, `name = "image-ocr"`, `version = "0.1"`.

A separate name and its own version is what keeps the two tellable apart on a
`Provenance` and a `ProcessingRecord` years later: `image@0.1` says nothing was
interpreted, and `image-ocr@0.1` says a recognizer was configured and this is what
came of it. Any change to the rules in this ADR — the supported claim, the
blankness rule, the metadata vocabulary, the skip semantics — changes this value.

**Header parsing reuses the exact Phase 4A parsers.** Not a copy, not a fork, not a
variant with OCR-shaped tweaks. `read_png_header` and `read_jpeg_header` are
already public functions in `image.py`; only the `mime_type` → reader dispatch is
private, and this PR publishes it through one narrow additive wrapper —
`read_image_header(stream, mime_type)` — over those exact functions. It adds no
parsing behaviour, changes no refusal, and reads no additional byte. An image's
structural facts must reach a content object through *precisely* the code path
they reach one through in the default build, or "enabling OCR changes nothing
about an image that already worked" would be an aspiration rather than a fact.

**What the processor must not do**, each for a concrete reason:

* **Not call `ImageProcessor.process()`.** It stamps `image@0.1` on its
  `ProcessingRecord` and on the object, which would be a false identity for a run
  that also configured a recognizer.
* **Not subclass `ImageProcessor`.** Inheritance would make the default build's
  behaviour depend on a subclass's overrides, which is the opposite of the
  isolation this ADR promises.
* **Not build a 4A object and mutate it.** Contract instances are validated
  snapshots, not continuously enforced objects; the repository's mutation rule is
  to build a new instance rather than edit one in place.

So `ImageOcrProcessor` constructs its own `ContentObject` — the same relationship
`PdfOcrProcessor` has to `PdfProcessor`, and the same accepted duplication.

**The order of operations, in full:**

```
validate the capture record, its raw reference, and its declared MIME type
open the original  -> read_image_header()   encoded format, width, height
                                            (a refusal here is final: ProcessingInputError)
open the original AGAIN -> ImageOcr.recognize_image(...)
validate the recognizer's answer
build canonical content
```

**The recognizer gets its own `open()` rather than a rewind.** Two reads of an
immutable content-addressed object are two reads of identical bytes, and "rewind
whatever the parser left behind" is a promise about stream position that this
module cannot make. The practical consequence is the point: **nothing on this path
seeks**, so a non-seekable `RawObjectStore` backend is a non-event rather than a
supported special case.

### The canonical object, and the one segment

An OCR-enabled successful image carries **exactly what a 4A object carries**, plus
at most one segment:

* `type = ContentType.IMAGE`;
* the same single `Asset(role=ORIGINAL, mime_type=<as declared>, ref, sha256)` —
  the exact original, unmodified, still the only asset;
* the same `OriginalReference`;
* the same title semantics: the submitter's capture title, exactly as given, or
  none. Not a title read out of the recognized text, not the filename — which this
  build never records — not the digest, and not the URL;
* `metadata["image"]` with the same three structural keys, unchanged;
* `metadata["image_ocr"]`, described below;
* one `ProcessingRecord(processor="image-ocr", processor_version="0.1")`.

When `result.text.strip()` is nonempty, **exactly one segment for the whole
image**:

```python
Segment(
    id=<fresh opaque id>,
    type=SegmentType.OCR,
    text=result.text,              # exactly as returned; never trimmed or rewritten
    temporal=None,
    spatial=None,
    provenance=Provenance(
        capture_id=capture.id,
        source_type=ProvenanceSourceType.OCR,
        asset_id=<the ORIGINAL asset's id>,
        processor="image-ocr",
        processor_version="0.1",
    ),
    metadata={"image_ocr": {"engine": ..., "engine_version": ...}},
    position=0,
)
```

`strip()` decides blankness and **never rewrites the stored string**: leading
indentation, trailing newlines and internal whitespace are what the engine
produced. No Unicode normalization, no line-ending rewriting, no hyphen repair, no
paragraph joining, no spell correction, and no model anywhere near the text. An
engine that returns `"Th1s"` gets `"Th1s"` stored; correcting it would be inventing
a reading nobody can audit.

`provenance.asset_id` is the original image, and that is not a dangling reference:
the pixels the engine read *are* that asset, in full, and it is the only artifact
that still exists.

**Two contract facts make the "invent nothing" rule structural rather than
disciplinary**, and both are worth recording because they are easy to rediscover
the hard way:

* `SpatialLocation` rejects an instance that locates nothing. So "no fake page, no
  invented bounding box, no region" is not a style choice — `spatial` **must be
  omitted entirely**, because an empty `SpatialLocation` is not constructible.
* `SegmentType.OCR` requires text, and `Segment.text` must be nonblank. So an
  empty OCR segment **cannot be built at all**. The tempting wrong answer ADR-019
  had to argue against for `VISUAL` has no OCR-shaped equivalent; the contract
  already forbids it.

When `result.text.strip()` is empty, `segments = []`. No placeholder of any kind,
no `VISUAL` segment, no caption, no `DerivedContent.summary`, and no
`ProcessingStatus.PARTIAL` to signal that recognition found nothing. Absence of
interpretation is represented by absence — invariant 19, which already governs this
design and which this PR therefore does not extend or restate.

Nothing in this phase produces OCR boxes, layout reconstruction, region
segmentation, confidence scores, language detection, orientation detection,
deskew, handwriting claims, captions, or visual understanding of any kind.

### `metadata["image_ocr"]`, and why the key is `engine_invoked`

One namespaced top-level key, beside the Phase 4A `image` mapping, which stays
exactly as ADR-019 fixed it.

**The distinguishing fact is named `engine_invoked`, not `attempted`.** The
difference is not pedantry. An encoded-byte-limit skip has already read some of the
original — that is how the byte count was measured — so "attempted" would be
ambiguous about the very case it exists to describe. `engine_invoked` states
precisely one thing, and it is the thing that decides what the rest of the mapping
may contain: whether Tesseract was actually run.

**A — default, OCR-disabled build.** Unchanged from Phase 4A.

```jsonc
{
  "image": { "encoded_format": "...", "encoded_width": 0, "encoded_height": 0 }
}
// no "image_ocr" key at all; processing identity is image@0.1
```

**B — engine invoked, whether or not it returned text.**

```jsonc
{
  "image": { /* unchanged */ },
  "image_ocr": {
    "engine_invoked": true,
    "engine": "tesseract",
    "engine_version": "<probed from the installed executable at startup>",
    "settings": {
      "languages": "eng+rus",
      "language_order": ["eng", "rus"],
      "oem": 1,
      "psm": 3,
      "dpi_supplied": false,
      "input": "original_encoded_bytes",
      "orientation_detection": false,
      "deskew": false,
      "retries": 0,
      "max_encoded_pixels": 20000000,
      "max_encoded_bytes": 67108864,
      "recognition_timeout_seconds": 30.0
    }
  }
}
```

**There is deliberately no `returned_text` boolean.** Nonblank recognition is
represented by the OCR segment, and a successful empty recognition by
`engine_invoked: true` together with no OCR segment. A boolean restating what the
segments already say is a second place for the same fact to be wrong.

**C — resource-policy skip.** Two shapes, one per bound:

```jsonc
{
  "image_ocr": {
    "engine_invoked": false,
    "skipped_reason": "encoded_pixel_limit",
    "max_encoded_pixels": 20000000
  }
}
```

```jsonc
{
  "image_ocr": {
    "engine_invoked": false,
    "skipped_reason": "encoded_byte_limit",
    "max_encoded_bytes": 67108864
  }
}
```

`skipped_reason` carries the `ImageOcrLimitExceeded.reason` value verbatim, and the
single limit key carries its `limit`. **When `engine_invoked` is `false`, `engine`,
`engine_version` and `settings` are absent.** Nothing ran, so run facts must be
absent — the same rule, one level down, that makes a 4A image carry no segments.

**Never persisted anywhere on the object**, whatever the outcome: a confidence
score (the engine this build ships against is not asked for one, and an invented
number would be read as a measurement), an inferred language, a semantic label or
tag, EXIF, XMP, ICC, GPS, camera or device identity, timestamps read out of the
file, an orientation guess, and — specifically — **a `rasterizer` field**. There is
no rasterizer on the direct-image path, and carrying `"pypdfium2"` across because
`pdf_ocr` has one would be a fabricated fact wearing the shape of an observation.

The naming discipline is ADR-018's and is applied here without exception: name the
observation, never the conclusion. Hence `engine_invoked` rather than "ocr_ran",
`skipped_reason` naming the *bound that was crossed* rather than a property of the
picture, and nowhere in this vocabulary the word "blank".

### The resource policy: three numbers, and where each one is enforced

```python
@dataclass(frozen=True, slots=True)
class ImageOcrLimits:
    max_encoded_pixels: int = 20_000_000
    max_encoded_bytes: int = 64 * 1024 * 1024  # 67_108_864
    recognition_timeout_seconds: float = 30.0
```

**These are initial product limits, not measurements**, exactly as
`unimem_ocr.policy`'s existing numbers are. They bound the work one image can
cause on a laptop. None is claimed to be optimal, and they are named and easy to
find precisely so that changing one is a deliberate act with a reason attached.

**`OcrLimits` is not reused, and the type stays image-specific.** Two of its four
fields — `max_pages` and `document_budget_seconds` — are meaningless for one image,
and `max_raster_pixels` is defined as the pixels of a page *rendered* at
`RENDER_SCALE`, which is a different measurement from encoded pixels. Reusing the
field would quietly re-badge a rasterization prediction as a header fact.

**There is no document-style total budget and no max-pages concept.** A document
budget exists because a document is *N* invocations competing for one envelope of
time; an image is exactly one invocation, so a second budget would be a strictly
larger duplicate of the timeout, and dead policy invites future misreading.

| bound | enforced where | enforced when | on breach |
| ----- | -------------- | ------------- | --------- |
| `max_encoded_pixels` | the adapter, first thing in `recognize_image`, on the `encoded_width × encoded_height` the processor passed | **before one byte of the original is read for recognition, before any decode, before any subprocess exists** | `ImageOcrLimitExceeded(reason="encoded_pixel_limit", limit=...)` |
| `max_encoded_bytes` | the adapter, while reading the original forward in bounded chunks | at the moment the bound is crossed; still before any subprocess exists | `ImageOcrLimitExceeded(reason="encoded_byte_limit", limit=...)` |
| `recognition_timeout_seconds` | the subprocess call | during the one engine invocation; the child is killed and reaped before the exception leaves the adapter | `ImageOcrExecutionError` |
| **the meaning of a limit breach** | **`core`**, in `ImageOcrProcessor` | on catching `ImageOcrLimitExceeded` | a successful capture with the skip recorded |

The split is the decision: **the adapter owns the numbers, `core` owns the
meaning.**

**The pixel bound reads the encoded dimensions Phase 4A already validated.** It is
checked as `encoded_width * encoded_height > max_encoded_pixels`, from integers the
header parser produced and the contract-legal ranges ADR-019 fixed. A pixel-limit
skip therefore reads **no byte of the OCR input stream at all**: the processor
opens the original a second time to satisfy the port's signature and the handle is
closed unread, nothing is decoded, and Tesseract is never started. (Exposing the
number across the seam so that `core` could skip even the open was considered and
rejected: it would put the adapter's arithmetic in two places.)

**The encoded-byte bound exists for one reason and has one scope.** The chosen data
path hands the original to the child as an in-memory `bytes` object, so the memory
this adapter holds must be bounded by a named, reviewable number rather than by
whatever a client uploaded. It is an **image-OCR adapter memory bound**. It is
**not** a `POST /v1/uploads` limit, **not** a generic image-ingestion limit, and
**not** a rule for PDF or DOCX. ADR-019's recorded gap — the route has no upload
limit, quota, or disk-usage policy — stays recorded and stays unfixed, because
cross-modality hardening of a shared route needs limits derived from a real
deployment requirement and does not belong buried in an OCR slice.

It is measured by **reading the original forward in bounded chunks**, never by
trusting a declared size — `RawObjectRef` carries none, and the store port exposes
no size. The implementation must avoid a large transient overshoot: it must not
blindly request another full chunk when only one byte of headroom remains. A
strategy equivalent to requesting at most `remaining_limit + 1` bytes is required,
so that crossing the bound is detectable while the memory held stays bounded by
roughly the configured limit plus one sentinel byte. On crossing, the adapter
raises the structured signal, **starts no subprocess**, and discards the temporary
in-memory bytes; the processor then returns the successful skipped canonical
object.

**The timeout applies to the one Tesseract invocation.** `subprocess` sends
`SIGKILL` and then waits on the process, so no orphan is left holding a pipe.

#### These are limits, not a sandbox

The distinction matters as much here as it did in ADR-018, and it is stated in
full because a reader who mistakes these three numbers for isolation will draw
conclusions this build cannot support.

**What they do bound, exactly:**

* `max_encoded_pixels = 20_000_000` — a **preflight structural pixel count**,
  `encoded_width × encoded_height`, checked before any OCR input is read and
  before any child process is launched.
* `max_encoded_bytes = 64 MiB` — **the encoded input bytes UniMem is willing to
  accumulate for one direct-image OCR invocation.**
* `recognition_timeout_seconds = 30` — **the wall-clock duration allowed for the
  Tesseract subprocess invocation**, after which the child is killed and reaped.

**What they do not bound, and Phase 4B does not impose:**

* a resident-set-size limit on the subprocess;
* an OS-level memory cgroup or `rlimit` of any kind;
* a CPU quota beyond the timeout;
* any guarantee that Tesseract or Leptonica cannot be killed by the operating
  system for memory pressure;
* any guarantee that pathological but under-limit encoded input cannot cause high
  transient resource use *inside the child* — a file well under both bounds can
  still be expensive for a decoder to work through.

`max_encoded_bytes` in particular bounds **the adapter's encoded input payload,
not total Python or subprocess resident memory.** `subprocess.run`, the operating
system's pipe buffers, and the child's own decoding all incur additional overhead
that this number says nothing about.

They also put no deadline on the enclosing HTTP request, which has none, and they
cannot constrain what the child does with the bytes beyond killing it when the
clock runs out.

**If the child dies, or cannot return a result this build can trust, the outcome
is the one fixed above**: `ImageOcrExecutionError`, nothing persisted, the capture
left `PROCESSING`, and a fixed `503 image_ocr_unavailable`.

**This limitation is accepted for Phase 4B.** Process isolation, cgroups, `rlimit`
supervision, worker processes and containers are **not** introduced here. Real
isolation is a deployment-architecture decision with its own requirements, and
bolting a half-measure onto an image-OCR slice would buy a claim this build could
not honour.

### The adapter hands original bytes to the engine, and decodes nothing

`TesseractImageOcr`, in `src/unimem_ocr/image.py`. The original PNG or JPEG bytes
go to Tesseract **exactly as submitted**.

* **No Pillow. No pypdfium2. No decode, re-encode, resize, transcode or
  normalization in UniMem.** Tesseract links Leptonica, which reads PNG and JPEG
  natively, so the engine sees precisely the bytes the submitter staged.
* **This is the safer choice, not merely the simpler one.** Decoding in-process
  would put a decompression bomb's allocation inside the server, where — as
  `OcrLimits`' own docstring admits — the limits do not bound this process's memory
  or CPU. Letting the engine decode puts that allocation in a child process that is
  `SIGKILL`ed on timeout and whose memory the operating system reclaims on exit.
* **No re-encode means no artifacts** introduced into the only thing the recognizer
  ever looks at, which is the same reasoning that made the PDF path's page raster
  lossless.
* **No temporary file, so no temporary path** to collide, leak, or be guessed —
  the existing property, preserved verbatim.
* **Nothing a client sent becomes an instruction.** The argument list is fixed and
  built entirely from this package's constants, with `shell=False`. No filename,
  URL, capture title, `file_ref`, digest, or request field appears in `argv`, as an
  option, as an output path, or as a configuration path. The uploaded filename is
  never read anywhere in this system, so it cannot reach one.
* **No network, no cloud OCR service, no model download, no telemetry.**
* The image travels on the child's **stdin**; the recognized text comes back on its
  **stdout** and is decoded as UTF-8 **strictly**. Standard error is captured
  separately, never mixed into the returned text, never copied into canonical
  content, and never returned to a client — Tesseract writes warnings about the
  image there and, on some builds, paths from this machine.

Passing the open handle to the child as its stdin, and streaming into the child
through a pipe, were both considered. The first requires a real `fileno()`, which
only some stream implementations can provide, and branching the adapter on a stream
capability creates a second code path that CI would not exercise. The second
reimplements the timeout-kill-reap discipline by hand, which is where orphaned
children and pipe deadlocks come from. The bounded read above keeps one code path,
one bound, and the standard library's own process handling.

### One shared engine runner, and the PDF path is frozen

The Tesseract invocation logic is extracted from `unimem_ocr/tesseract.py` into a
new **stdlib-only** module, `src/unimem_ocr/engine.py`. This is approved because the
extracted code is security-sensitive common infrastructure: a fixed argument list
with no shell, a timeout that kills and reaps, strict decoding of stdout,
suppression of stderr, and refusal on a nonzero exit. Two divergent copies of that
is a real hazard, not a tidiness complaint.

The runner is **infrastructure-neutral**:

* it raises a structured internal `EngineInvocationError`; each wrapper translates
  it into its own domain type;
* its messages depend on neither "page" nor "image" — **domain wording belongs to
  the wrappers**;
* it accepts an **optional DPI argument**. The PDF adapter calls it with `dpi=300`;
  the image adapter calls it with `dpi=None`, and **no `--dpi` argument is emitted
  at all** in that case.

There is a structural reason this must be a third module rather than an import of
`tesseract.py`: that module imports `pypdfium2` at module scope, and
`require_rasterizer()` imports it for its side effect. An image adapter importing it
would drag PDFium into an image-only deployment. So `tesseract.py` imports
`engine.py`, never the reverse.

**The PDF path is frozen by this PR**, and the freeze is explicit and testable:

* `PdfOcrExecutionError` and every other PDF error type is unchanged;
* every PDF-facing message — server log text and the fixed public 503 text alike —
  is unchanged;
* the PDF `argv` is unchanged, **including `--dpi 300`**;
* `PdfPageOcr`, `PdfOcrResult`, `RecognizedPage` and `validate_ocr_result` are
  neither renamed nor generalized;
* `_PDFIUM_LOCK`, the load-error classification allowlist, the raster-lifetime
  sequence, `OcrLimits` and every rasterization constant are untouched;
* `pypdfium2` and `Pillow` imports stay isolated from image-only OCR startup.

`TesseractPdfPageOcr._recognize` becomes a thin wrapper that calls the runner and
re-raises `EngineInvocationError` as `PdfOcrExecutionError` with its existing text.
No PDF capture behaves differently after this PR than before it.

### The direct-image recognition policy, and the honest gap in it

Fixed, with no per-request and no per-image variation:

| setting | value | why it is the same as, or different from, the PDF path |
| ------- | ----- | ------------------------------------------------------ |
| languages | `eng+rus`, in that order | the same product commitment and the same startup gate. No script detection and no "try English, fall back to Russian" |
| `--oem` | `1` (LSTM only) | engine mode is a property of the engine, not of the input |
| `--psm` | `3` | the engine's own default; see the limitation below |
| `--dpi` | **not passed at all** | see below |
| orientation detection (OSD) | off | this build does not guess which way up a picture is |
| deskew | off | same |
| retries | `0` | a retry that produces different text is a second unreviewable policy |

**`--dpi` is omitted, and this is a deliberate departure from the PDF path.** For a
PDF, `--dpi 300` is *true*: this build rasterized the page at 300 DPI and is telling
the engine the resolution it actually produced. A direct PNG or JPEG **was not
rasterized by UniMem**, and Phase 4A parses no physical-density metadata at all —
not PNG `pHYs`, not JFIF density, not EXIF. Passing a number would assert a
physical fact this system never observed, and could override a density the file
itself genuinely carries.

Omitting it means Tesseract applies its own input-resolution behaviour. **That
behaviour is the engine's, and it is not recorded as an observed fact about the
image.** What the content object records is `"dpi_supplied": false` — a true
statement about the invocation, not a claim about the picture.

**PSM 3 is kept, and the limitation is recorded rather than engineered around.**
Mode 3 is fully automatic page segmentation tuned for document pages. A photograph
of a sign, a screenshot with scattered labels, or a scene with incidental text is
not a page, and a sparse-text mode would very likely read such material better.
**OCR quality on photographs and sparse-scene text may therefore be worse than a
specialized sparse-text policy would achieve.** This build accepts that, because
the alternative on offer is choosing a mode per image — and a guess that changes
what an image is remembered as saying is not a knob, it is a second unreviewable
policy. Changing the fixed mode later is a reviewed edit with a reason attached and
a new processor version; choosing it per image is not on the table.

Recognition quality is recorded nowhere and claimed nowhere. `COMPLETE` means this
policy finished and its content was persisted — not that recognition was accurate,
and not that every visible word was captured.

### Startup prerequisites, split by capability

`--image-ocr` validates, before any request is served:

1. the Tesseract executable runs and reports a version;
2. the `eng` language data file is present;
3. the `rus` language data file is present.

**And nothing else.** It does not call `require_rasterizer()`, does not import
`pypdfium2`, and does not import `Pillow` — it needs neither, and requiring them
would make a deployment install a PDF renderer and an imaging library for a
capability that executes neither. `--pdf-ocr` retains its existing prerequisites
unchanged, both optional packages included.

The version the engine reports is passed into the adapter, so the identity recorded
on every content object it contributes to is the identity of the software that
actually ran — never a constant compiled into this package.

A missing prerequisite is an `OcrPrerequisiteError` and the server does not start.
Nothing is installed, downloaded, or worked around; the language set is never
narrowed to whatever happens to be available; and image OCR is never silently
disabled so that the process can start anyway.

**When both flags are enabled, Tesseract is probed twice, and that is accepted.**
Two extra fixed-argument startup probes cost milliseconds. The alternative — a
module-level cache or a memoized probe — would introduce exactly the hidden global
state that makes two capabilities' startup gates secretly depend on each other and
on test ordering. **No global prerequisite cache and no singleton state is
introduced.**

**The `[ocr]` extra is unchanged.** It installs `pypdfium2` and `Pillow`, which are
what `--pdf-ocr` needs; image OCR needs no Python package beyond `unimem_ocr`
itself, which is pure Python and ships unconditionally. A machine with Tesseract
and no extra installed can run `--image-ocr`. Packaging is not redesigned here.

### Implementation acceptance: CI must prove the dependency boundary

Every claim above about `--image-ocr` needing neither PDFium nor Pillow is, until
something checks it, a comment. A single stray module-level import — in the new
adapter, in the shared runner, in the prerequisite gate, or in a test helper that
the production path quietly borrows — would make it false, and nothing in the
current workflow would notice: the one job that runs native OCR installs the
`[ocr]` extra first, so both packages are always importable while it runs.

**Phase 4B is not acceptable until CI proves the boundary by running real image
OCR on a machine where the `[ocr]` extra is not installed.** This is an
implementation-acceptance requirement of this ADR, not a nice-to-have, and it is
the only reason the dependency claims in this document can be stated as facts.

The preferred shape reuses the existing native-OCR job and simply does the
unextra'd work *first*:

1. install the ordinary package plus dev dependencies **only** — no `[ocr]`;
2. install system Tesseract with the `eng` and `rus` language data, exactly as the
   job already does;
3. verify that `pypdfium2` and `Pillow` are **not importable**, so that step 4 is
   evidence rather than coincidence, and that the image-OCR code path does not
   require them;
4. run at least one **real** image-OCR integration test end to end through
   `TesseractImageOcr` against the actual engine, with the extra still absent;
5. **prove that test really ran** — the same belt-and-braces the workflow already
   applies to the PDF suites, so a rename, a deselection or a silent skip cannot
   leave a green job that never touched the engine.

The implementation takes the separate-job form, because the two halves need
incompatible installations: the existing `Local PDF OCR` job is *defined* by
having the extra and this one by not having it, so interleaving them in one job
would mean uninstalling packages midway and leaving each half's guarantee
depending on the other's step order. `Local image OCR` therefore installs the
package with dev dependencies only, uninstalls `pypdfium2` and `Pillow` defensively
in case a future runner image ships them, asserts through `importlib` that neither
resolves, and only then runs the image suites with
`UNIMEM_REQUIRE_IMAGE_OCR_INTEGRATION=1`. The existing job is untouched.

That flag is deliberately separate from `UNIMEM_REQUIRE_PDF_OCR_INTEGRATION`.
One flag could not serve both: the image job would either have to install the
extra it exists to do without, or let the PDF suites skip silently in the one
place their absence would go unnoticed.

**The fixture for that test must not need Pillow to exist.** Pillow is precisely
what step 3 asserts is absent, so a fixture generated with it at run time would be
a circular proof. The implementation assembles a real greyscale PNG byte by byte
from the standard library — `zlib` supplies both the `IDAT` compression and the
chunk CRCs, which is the same `zlib` Phase 4A's parser already uses for header
integrity. Nothing is fetched at test time and no font file is committed, exactly
as the existing image-only PDF fixtures are rendered locally rather than shipped
as binaries.

**One finding from building it is worth recording, because it would otherwise be
rediscovered.** The obvious approach — a 5×7 bitmap alphabet scaled up — produces
letters whose strokes are a sixth of the glyph wide with perfectly square corners,
unlike any typeface an LSTM recognizer was trained on, and the real engine read
such a fixture as **Cyrillic**: with `eng+rus` in force, blocky Latin capitals and
their Cyrillic lookalikes are genuinely ambiguous. The fixture therefore draws
glyphs as strokes of a proportional width, and its token is built only from
letters with no Cyrillic homoglyph. Neither the recognition policy nor the
language set was adjusted to make the test pass; the fixture was made honest
instead.

What the acceptance proof must establish, in one sentence: **`--image-ocr` and
`TesseractImageOcr` neither import nor require `pypdfium2` or `Pillow`.**

**The existing PDF OCR native guard is not weakened by any of this.** Its required
test classes, its required-mode environment variable, its skip-is-a-failure policy
and its post-run count check all remain, and the `[ocr]`-installed half of the job
continues to run exactly what it runs today.

### Delivery gains one row, and no route changes

`ImageOcrExecutionError` maps to a fixed **`503 image_ocr_unavailable`** with a
fixed public message equivalent to:

> text recognition for this image could not be completed; the capture is stored and
> no content was produced

It is a separate row from the PDF path's `ocr_unavailable`, whose public text says
"for this document" and stays as it is.

The public text names nothing about this machine: no exit status, no executable
path, no subprocess stderr, no engine version, no language path, no local file
name, and no stack trace. All of those may appear in the underlying error's own
message, which is written for a server log and never reaches the wire.

`ImageOcrLimitExceeded` appears in **no** HTTP mapping, because it never escapes
`ImageOcrProcessor` — it is a control signal, and its outcome is a `201`.

`unimem_api/app.py` is unchanged; the only delivery-side edit is one constant and
one row in the error translation table `create_app` already installs. No route,
request field, response field, or response model changes.

### Everything else is unchanged

No contract, enum, or validator. No schema version — it stays `0.2`. No lifecycle
state, capture status, or orchestration rule; `ProcessingOrchestrator` is untouched
and the lifecycle behaviour above falls out of which exceptions subclass
`ProcessingError`, exactly as ADR-018 arranged. No persistence change. No renderer
change — an image object with one OCR segment projects to Markdown through the
existing renderer, and one with none projects to its title or the empty string as
it does today. No replay change: images still get no replay guarantee, so a lost
response costs a `409` and an observational `GET`. No browser-extension change;
there is still no screenshot or clipboard connector. `core/processing/pdf.py`,
`pdf_ocr.py`, `docx.py`, `text.py`, `webpage.py`, the stores, intake, and
`clients/browser-extension/` are untouched.

Enabling image OCR revisits no existing record. An image that already ingested as
`image@0.1` keeps that object and that identity; the same bytes recognized later
are a **new** capture id sharing the raw object and sharing no canonical identity.

## Scope

### In scope for Phase 4B

* `ImageOcr`, `ImageOcrResult`, `ImageOcrExecutionError`, the structured
  `ImageOcrLimitExceeded`, and `validate_image_ocr_result` in
  `core/processing/image_recognition.py`.
* `ImageOcrProcessor` in `core/processing/image_ocr.py`, `image-ocr@0.1`: the same
  capability claim as `ImageProcessor`, Phase 4A header parsing reused through a
  narrow additive public wrapper, at most one `OCR` segment, the `image_ocr`
  metadata vocabulary, and the skip semantics fixed above.
* One additive public header-reader wrapper in `core/processing/image.py`, over the
  existing parsers, changing no parsing behaviour.
* `TesseractImageOcr` in `unimem_ocr/image.py`: original bytes to a local engine,
  no imaging library, no rasterizer, no temporary file.
* `unimem_ocr/engine.py`: the shared, stdlib-only, infrastructure-neutral
  invocation runner, with the PDF path frozen behaviourally.
* `ImageOcrLimits` and the direct-image recognition constants, added to
  `unimem_ocr/policy.py` without touching `OcrLimits` or any rasterization
  constant.
* An image-OCR startup prerequisite gate that requires Tesseract and both language
  data files and nothing else.
* `--image-ocr`, an `image_ocr` parameter on `build_local_app`, and the single `if`
  that selects one image processor.
* One new fixed `503 image_ocr_unavailable` row in the delivery error table.
* Tests: the port and its validation, the processor including every matrix row, the
  routing ambiguity, the adapter's command line and bounds, composition and flag
  independence, prerequisite refusal, and an end-to-end HTTP path against a real
  engine.
* The CI acceptance proof above: real image OCR exercised against a real engine
  **without** the `[ocr]` extra installed, with `pypdfium2` and `Pillow` asserted
  absent and the test proven to have run, before the extra is installed and the
  existing PDF suites follow.
* This ADR and the matching `ARCHITECTURE.md` section.

### Out of scope for Phase 4B

* Any contract, enum, schema, lifecycle, capture-status, route, replay, or
  persistence change.
* Any new architectural invariant — invariant 19 already governs this design.
* New image formats: WebP, GIF, TIFF, HEIC, AVIF, SVG; `image/apng`; animation and
  frame extraction; any static-versus-animated classification.
* EXIF, XMP, ICC, GPS, device identity, timestamps read from the file, and
  orientation correction.
* OCR boxes, regions, layout analysis, reading order, confidence scores, inferred
  language, handwriting-specific modes, vision, captioning, and semantic
  classification.
* Per-image or per-request selection of any engine setting.
* Pillow, PDFium, or any pixel decode inside UniMem for the image path.
* A generic upload-size limit, a quota, or any change to `POST /v1/uploads`.
* Thumbnails, resizing, transcoding, and any second asset.
* Reprocessing, reconciliation, or resuming a capture left `PROCESSING`.
* Embeddings, retrieval, `ctxalloc`, agent routing, queues, workers,
  authentication, cloud OCR providers, and browser screenshot or clipboard capture.

## Security

* **No decode in this process, so no in-process decompression bomb.** UniMem reads
  headers and hands original bytes to a child. Whatever a crafted image costs to
  decode, it costs the child, which is killed on a 30-second timeout and whose
  memory the operating system reclaims.
* **Two bounds before any engine exists.** The pixel bound is checked on
  already-validated header integers before the input stream is read at all; the
  byte bound is checked while reading, with bounded overshoot, before any
  subprocess is started.
* **Bounded memory by a named number.** The adapter holds at most roughly
  `max_encoded_bytes` plus one sentinel byte, rather than however large a file a
  client uploaded.
* **Nothing is executed, fetched, or opened beyond the engine.** No shell, no
  network, no cloud service, no model download, no telemetry, and no path or URL
  resolved — only `sha256:<digest>` is resolved, unchanged since ADR-016.
* **No client value reaches `argv`.** A fixed argument list of package constants,
  `shell=False`, bytes on stdin, text on stdout. The uploaded filename is never
  read anywhere in this system.
* **No temporary artifact**, so no temporary path to collide, leak, or be guessed.
* **Nothing is echoed.** The 503's public text is fixed and names nothing about
  this machine; stderr from the engine is captured, never mixed into content, and
  never returned; a 422 from header validation names the payload type, the field
  names and the supported MIME types, and repeats no submitted value.
* **No metadata harvesting.** EXIF and XMP are still never parsed, so GPS
  coordinates, device serials, owner names and EXIF thumbnails are never read into
  canonical content. They remain in the immutable original.
* **The recognized text is content, not a command.** It is stored exactly as
  returned and is interpreted by nothing in this build.
* **The three limits are not a sandbox.** No RSS limit, no cgroup, no `rlimit`, no
  CPU quota beyond the timeout, and no guarantee that the child cannot be killed by
  the OS for memory or that under-limit input cannot be expensive inside it. A
  child that dies or cannot return a trusted result is an `ImageOcrExecutionError`
  and a 503, not a claim about the image. Accepted for 4B.
* **Recorded, not fixed:** `POST /v1/uploads` still has no size limit, quota, or
  disk-usage policy, and the server remains unauthenticated and localhost-bound.
  The encoded-byte bound above is an adapter memory bound and is not a substitute
  for either.

## Consequences

* UniMem gains its first optional capability over material whose canonical form
  needs no interpretation at all — and, with it, the first result in the system
  that is a deliberate, recorded *non*-run of a capability the deployment has.
* A third durable shape joins the two ADR-019 created: an image object may now
  carry one OCR segment, carry none because the engine read nothing, or carry none
  because the engine was never asked. The processor identity plus one boolean
  distinguishes all three permanently.
* `COMPLETE` continues to mean what it has always meant. `image-ocr@0.1` publishes
  semantics that include the budget, so a skipped run completed exactly what that
  version says it does.
* A deployment with a broken engine `503`s every image capture that a default
  deployment would accept — and so does a single image whose encoded body no local
  engine can decode, indefinitely, because this build declines to guess which of
  the two it is looking at. Both are visible and neither is recorded as a claim
  about the picture. The posture is the one `--pdf-ocr` has had since Phase 3,
  with the "may not be transient" caveat now written down rather than assumed
  away.
* Images above 20 megapixels — which includes a meaningful share of current phone
  cameras — will be ingested with recognition skipped rather than recognized. The
  capture is never lost and the skip is durably recorded; raising the number is a
  one-line deployment change with a reason attached.
* Recognition on photographs and sparse-scene text will be weaker than a
  specialized policy would manage, and the ADR says so rather than letting a reader
  discover it from disappointing results.
* Two OCR capabilities now exist side by side without a shared abstraction over
  them. That is deliberate, and the cost — two ports, two result types, two error
  pairs — is the price of not making a PDF-shaped contract answer image-shaped
  questions.
* The `[ocr]` extra's name is now slightly narrower than it reads: it installs the
  PDF rasterizer stack, and image OCR does not use it. Recorded here rather than
  repackaged.

## Alternatives rejected

* **Failing a valid image that exceeds the OCR budget.** Violates ADR-019's forward
  constraint verbatim, and lets a deployment-side budget speak as a verdict about
  someone's photograph.
* **A 503 for an over-budget image.** The bound is deterministic, so the retry it
  invites can never succeed — the unterminating loop ADR-016 already refused to
  create.
* **`PARTIAL` for a skipped enrichment.** Inert in this build (the orchestrator
  never reads it), contradictory if made durable, and it would require reopening
  closed Phase 0H orchestration from inside an image slice.
* **Succeeding with OCR skipped when the engine crashed.** Would require writing
  either a lie or a machine fault into permanent canonical content, and would
  degrade a broken deployment silently. "The engine crashed" and "the engine found
  no text" are different facts.
* **A bare `ImageOcrLimitExceeded` whose message must be parsed.** Control flow
  that depends on prose breaks the first time prose is improved.
* **A new contract enum for the limit reason.** A durable wire-format commitment
  minted for a private arrangement between an adapter and a processor.
* **Generalizing `PdfPageOcr`, or introducing a shared `Recognizer` protocol in
  `core`.** Their common surface is one infrastructure operation and their error
  vocabularies genuinely differ; the shared part belongs in the adapter package,
  and it is put there.
* **Reusing `OcrLimits`.** Half its fields are meaningless for one image and
  `max_raster_pixels` measures something else.
* **A whole-image budget in addition to the per-invocation timeout.** One
  invocation cannot exceed two identical deadlines.
* **Decoding with Pillow before handing pixels to the engine.** Moves a
  decompression bomb's allocation into the server process, adds a dependency image
  OCR does not need, and introduces re-encode artifacts into the only thing the
  recognizer sees.
* **Writing the image to a temporary file for the engine to open.** Creates a path
  where the design has carefully avoided having one.
* **Passing the open handle as the child's stdin.** Needs a real `fileno()`, so it
  branches on a stream capability and leaves one branch unexercised.
* **Streaming to the child through a pipe by hand.** Reimplements the standard
  library's timeout, kill and reap behaviour, which is where orphaned children come
  from.
* **Trusting a declared size instead of counting bytes.** There is no declared size:
  `RawObjectRef` carries none and the store port exposes none.
* **Passing `--dpi 300` for a direct image.** Asserts a physical density this build
  never observed, and can override one the file genuinely carries.
* **Choosing a page-segmentation mode per image.** A guess that changes what an
  image is remembered as saying is a second unreviewable policy.
* **Overloading `--pdf-ocr` to enable both.** Forces a PDF rasterizer and an imaging
  library onto a deployment that wants only image recognition, and welds together
  two capabilities with different prerequisites, limits, data paths and failure
  semantics.
* **A per-request, per-intent, or per-MIME switch.** ADR-018 settled this: a client
  cannot choose what a deployment is.
* **Caching the Tesseract prerequisite probe across capabilities.** Hidden global
  state, to save a few milliseconds once per process start.
* **A generic upload-size limit, introduced here as a byproduct.** Cross-modality
  hardening of a shared route, with limits that would be invented rather than
  derived, buried inside an image-OCR PR.
