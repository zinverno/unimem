"""The fixed recognition policy, and the limits it runs inside.

Everything here is a named constant or a field of one frozen dataclass. There is
no settings framework, no configuration file, no environment lookup, no profile,
and no per-request override — the whole point is that a deployment either runs
this policy or does not run OCR, and a client cannot reach any of it. Nothing in
an HTTP request, a ``CaptureEnvelope``, a ``CaptureIntent``, a filename, or a
``file_ref`` is consulted by any value below.

**The numbers are initial product limits, not measurements.** They were chosen
to bound the work a single scanned document can cause on a laptop, not derived
from a benchmark, and none of them is claimed to be optimal. They are written
down, named, and easy to find precisely so that changing one is a deliberate act
with a reason attached.

**The policy is deliberately unclever.** Two languages in a fixed order, one
resolution, one engine mode, one page segmentation mode, one page at a time, no
retry, and no orientation-detection pass. Every one of those is a knob that
could be turned per document by something that guessed — and a guess that
changes what text a document is remembered as carrying is not a knob, it is a
second unreviewable policy.
"""

from dataclasses import dataclass
from typing import Final

#: The languages recognition is performed with, in this order.
#:
#: English and Russian, because those are the two the product commits to and the
#: two whose data files a deployment must install. The order is passed through to
#: the engine as given: Tesseract treats the first language as primary, so
#: ``eng+rus`` and ``rus+eng`` are genuinely different configurations and the one
#: in force is recorded on every content object.
#:
#: There is no automatic script detection and no "try English, fall back to
#: Russian" behaviour. A missing language pack fails startup rather than quietly
#: reducing this tuple — see :mod:`unimem_ocr.prerequisites`.
RECOGNITION_LANGUAGES: Final = ("eng", "rus")

#: The ``-l`` argument built from :data:`RECOGNITION_LANGUAGES`.
TESSERACT_LANGUAGE_ARGUMENT: Final = "+".join(RECOGNITION_LANGUAGES)

#: Nominal rasterization resolution, in dots per inch.
#:
#: 300 DPI is the resolution scanned-document OCR is conventionally tuned for and
#: the floor Tesseract's own guidance treats as adequate for body text. It is
#: *nominal*: the pixel dimensions of a page follow from its declared size, so a
#: page declaring an unusual media box does not silently get a different
#: effective resolution — it gets the same scale factor applied to a different
#: canvas, and the resulting pixel count is checked against
#: :attr:`OcrLimits.max_raster_pixels` before anything is allocated.
RENDER_DPI: Final = 300

#: PDF canvas units per inch. A PDF point is 1/72 inch by definition.
CANVAS_UNITS_PER_INCH: Final = 72

#: The scale factor handed to the rasterizer: pixels per PDF canvas unit.
RENDER_SCALE: Final = RENDER_DPI / CANVAS_UNITS_PER_INCH

#: Tesseract's OCR engine mode. ``1`` is the LSTM neural engine only — the
#: modern one — rather than the legacy engine or the combined mode.
TESSERACT_OEM: Final = 1

#: Tesseract's page segmentation mode. ``3`` is fully automatic page
#: segmentation *without* orientation and script detection, which is the
#: engine's own default and the right one for a page whose orientation the PDF
#: already declares. Modes that add OSD would have this build second-guess the
#: renderer about which way up a page is.
TESSERACT_PSM: Final = 3

#: The executable looked up on ``PATH``. A name, not a path: which Tesseract
#: runs is the machine's business, and this package neither installs, downloads,
#: nor vendors one.
TESSERACT_EXECUTABLE: Final = "tesseract"

#: The image format page rasters are encoded in before being handed to the
#: engine. Lossless, because a lossy encode would introduce artifacts into the
#: only thing the recognizer ever sees.
PAGE_IMAGE_FORMAT: Final = "PNG"

#: The opaque white background every page is rendered onto, as RGBA.
#:
#: Opaque matters. A PDF page has no background of its own, so rendering onto
#: transparency would hand the engine an image whose "paper" is undefined —
#: black text on nothing, flattened by whatever encodes it next.
RENDER_FILL_COLOR: Final = (255, 255, 255, 255)


@dataclass(frozen=True, slots=True)
class OcrLimits:
    """The bounds one document's recognition runs inside.

    **These are limits, not a sandbox, and the difference is worth being exact
    about.** They bound how many pages are rasterized, how large a single page
    raster may be, how long one engine invocation may run, and how long
    recognition of one document may take in total. They do **not** limit the
    memory or CPU of this process, they do not preempt the pure-Python PDF
    parser that ran before recognition started, and they cannot interrupt an
    in-process native rendering call once it has begun. A page whose *declared*
    size passes the pixel check and whose content is pathological can still
    occupy the renderer for an unbounded time, because the only thing capable of
    stopping that would be a separate process, and this build does not have one.
    Nor does any of this put a deadline on the enclosing HTTP request: the
    request has no timeout at all, and the budget below covers recognition
    alone.

    What they do buy is real: a 900-page scan is refused instead of accepted,
    a page declaring a 40,000-inch media box is refused instead of allocated,
    and a wedged engine is killed instead of waited on forever.
    """

    #: The most physical pages an OCR-enabled deployment will accept in one PDF.
    #:
    #: Checked *before* anything is rasterized, and a document over the limit is
    #: refused whole. Deliberately not "recognize the first fifty pages": a
    #: content object holding half a document, with nothing in it saying the
    #: other half exists, is the kind of silent truncation this system exists to
    #: avoid.
    max_pages: int = 50

    #: The most pixels one page raster may contain, width times height.
    #:
    #: Checked against the dimensions computed from the page's declared size and
    #: :data:`RENDER_SCALE`, *before* a bitmap is allocated. A page over the
    #: limit is refused rather than quietly rendered at a lower resolution,
    #: because silently downscaling would change what the engine read without
    #: changing anything a reader of the result can see.
    max_raster_pixels: int = 20_000_000

    #: How long one engine invocation — one page — may run.
    page_timeout_seconds: float = 30.0

    #: How long recognition of one document may take in total, measured on a
    #: monotonic clock from the moment recognition begins.
    #:
    #: The remaining budget is recomputed before every page and the smaller of
    #: it and :attr:`page_timeout_seconds` is what the next invocation gets, so
    #: fifty pages cannot each take their own thirty seconds. When it is spent,
    #: no further page is begun.
    document_budget_seconds: float = 120.0


#: The limits every deployment gets unless it says otherwise in its own code.
DEFAULT_LIMITS: Final = OcrLimits()


# --- Direct still-image recognition -------------------------------------------
#
# A separate section, and separate constants, because the PDF values above
# describe a *rasterization this build performs* and none of that happens here.
# `RENDER_DPI`, `RENDER_SCALE`, `CANVAS_UNITS_PER_INCH`, `PAGE_IMAGE_FORMAT` and
# `RENDER_FILL_COLOR` are all statements about turning a page into pixels; a
# submitted PNG or JPEG arrives as pixels already and is handed to the engine
# exactly as it was staged.
#
# What *is* shared is shared honestly: `RECOGNITION_LANGUAGES`,
# `TESSERACT_LANGUAGE_ARGUMENT`, `TESSERACT_OEM`, `TESSERACT_PSM` and
# `TESSERACT_EXECUTABLE` are properties of the engine and of the product's
# language commitment, not of either input format, so both paths use the same
# ones rather than each declaring its own copy to drift from.

#: Whether a ``--dpi`` argument is sent for a direct image. It is not.
#:
#: The PDF path passes ``--dpi 300`` and that is *true*: it rendered the page at
#: 300 DPI and is telling the engine the resolution it produced. A submitted
#: image was not rasterized by this build, and Phase 4A parses no physical-density
#: metadata at all — not PNG ``pHYs``, not JFIF density, not EXIF — so there is no
#: observed resolution to report. Passing a number would assert a physical fact
#: nobody measured, and would override a density the file may genuinely carry.
#:
#: The consequence is recorded rather than hidden: Tesseract applies its own
#: input-resolution behaviour instead. That behaviour is the engine's, and the
#: content object records ``dpi_supplied: false`` rather than any claim about the
#: picture.
IMAGE_DPI_SUPPLIED: Final = False

#: What the content object records about how the engine was fed.
#:
#: The original encoded bytes, unmodified. No decode, no re-encode, no resize, no
#: colour conversion, and no temporary file: whatever the submitter staged is
#: byte-for-byte what the recognizer read.
IMAGE_ENGINE_INPUT: Final = "original_encoded_bytes"

#: How much of the original the adapter reads at a time while measuring it
#: against :attr:`ImageOcrLimits.max_encoded_bytes`.
#:
#: Only a chunk size, never a bound: the bound is the limit, and the reader
#: narrows its final request so that crossing the limit costs one byte of
#: overshoot rather than a whole chunk.
IMAGE_READ_CHUNK_SIZE: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class ImageOcrLimits:
    """The bounds one direct-image recognition runs inside.

    A separate type from :class:`OcrLimits`, and deliberately not a reuse of it.
    Two of that class's four fields — ``max_pages`` and
    ``document_budget_seconds`` — are meaningless for a single image, and its
    ``max_raster_pixels`` is defined as the pixels of a page *rendered* at
    :data:`RENDER_SCALE`, which is a different measurement from the pixels a
    header declares. Sharing the dataclass would quietly re-badge a rasterization
    prediction as a structural observation.

    **These are limits, not a sandbox**, and the distinction is worth being exact
    about because three numbers can look like isolation.

    They bound: a preflight structural pixel count, checked before any input is
    read and before any child process exists; the encoded bytes this adapter will
    accumulate in memory for one invocation; and the wall-clock life of that one
    invocation.

    They do **not** impose a resident-set-size limit on the subprocess, an
    OS-level memory cgroup or ``rlimit``, or a CPU quota beyond the timeout. They
    do not guarantee that Tesseract or Leptonica cannot be killed by the operating
    system for memory pressure, and they do not guarantee that pathological but
    under-limit input cannot cause large transient allocations *inside* the child.
    :attr:`max_encoded_bytes` in particular bounds the adapter's encoded input
    payload — not total Python resident memory, and not the child's:
    :func:`subprocess.run`, the operating system's pipe buffers, and the child's
    own decoding all add overhead this number says nothing about. Nothing here
    puts a deadline on the enclosing HTTP request, which has none.

    A child that dies, or that cannot return a result this build can trust,
    becomes an
    :class:`~core.processing.image_recognition.ImageOcrExecutionError` and leaves
    the capture non-terminal. Process isolation, cgroups, worker pools and
    containers are not introduced by this phase.

    **The numbers are initial product limits, not measurements.** They bound the
    work one image can cause on a laptop; none is claimed to be optimal, and they
    are named and easy to find precisely so that changing one is a deliberate act
    with a reason attached.
    """

    #: The most encoded pixels — width times height, as the header declares them
    #: — this build will hand to a recognizer.
    #:
    #: Checked *before* the recognition stream is read at all, from integers
    #: :mod:`core.processing.image` already validated, so an over-budget image
    #: costs one opened-and-unread handle rather than a decode. Refusal, not
    #: downscaling: silently recognizing a shrunken copy would change what the
    #: engine read without changing anything a reader of the result can see.
    #:
    #: An image over this bound is still ingested, still stored, and still
    #: canonical. The bound refuses the enrichment, never the artifact.
    max_encoded_pixels: int = 20_000_000

    #: The most encoded bytes this adapter will hold in memory for one
    #: recognition.
    #:
    #: It exists because the engine is fed through ``subprocess.run(input=...)``,
    #: which needs the bytes in the process. Encoded pixels do not bound the
    #: encoded file — a small image can carry megabytes of ancillary data, and a
    #: JPEG's frame header says nothing about the size of the scan behind it — so
    #: the memory held is bounded by a named number rather than by whatever a
    #: client uploaded.
    #:
    #: It is **not** an upload limit. ``POST /v1/uploads`` still accepts a part of
    #: any size, no PDF or DOCX capture is affected, and no image capture is
    #: refused for crossing it.
    max_encoded_bytes: int = 64 * 1024 * 1024

    #: How long the one engine invocation may run.
    #:
    #: There is deliberately no second, document-style total budget: a document is
    #: *N* invocations competing for one envelope of time, and an image is exactly
    #: one, so a second bound would be a strictly larger duplicate of this one.
    recognition_timeout_seconds: float = 30.0


#: The image limits every deployment gets unless it says otherwise in its own code.
DEFAULT_IMAGE_LIMITS: Final = ImageOcrLimits()
