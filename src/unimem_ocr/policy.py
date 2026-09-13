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
