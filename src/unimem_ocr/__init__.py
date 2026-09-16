"""Local recognition: the optional adapters, outside ``core``.

This package is the concrete half of two ports —
:mod:`core.processing.ocr` for the pages of a scanned PDF, and
:mod:`core.processing.image_recognition` for a staged PNG or JPEG. Neither is
imported by anything that runs in a default deployment, and each is reached only
by an application explicitly composed with that capability enabled::

    python -m unimem_api --data-dir ./data --pdf-ocr
    python -m unimem_api --data-dir ./data --image-ocr
    python -m unimem_api --data-dir ./data --pdf-ocr --image-ocr

The two are **independent**. They share an engine, an engine runner, a language
policy and a prerequisite probe; they share no limits, no data path, no result
type, and no failure semantics. Enabling one never enables or requires the other.

It depends on ``core`` and never the other way round. ``core`` does not import
it, does not know its name, and cannot be made to load it; the only things that
cross between them are the two protocols and the plain value types beside them.

**Importing this package costs nothing, and the two capabilities cost different
things.** :mod:`unimem_ocr.errors`, :mod:`unimem_ocr.policy`,
:mod:`unimem_ocr.engine` and :mod:`unimem_ocr.prerequisites` are pure Python and
import no native library, and neither does this module. The rasterizer and the
imaging package are imported by :mod:`unimem_ocr.tesseract`, which
:func:`build_tesseract_ocr` loads at the moment it is called — which is what turns
"pypdfium2 is not installed" from a traceback during startup into a sentence
explaining which extra to install. :mod:`unimem_ocr.image` imports **neither**,
and does not import :mod:`unimem_ocr.tesseract` either, so
:func:`build_tesseract_image_ocr` loads no native code at all.

The prerequisites are system software and none of it is installed, downloaded, or
vendored by this package. PDF OCR needs the ``[ocr]`` extra plus a Tesseract
executable with the ``eng`` and ``rus`` language data. **Direct-image OCR needs
the executable and the two language files and nothing else** — no extra, no
rasterizer, no imaging library. See the README.
"""

from typing import Final

from core.processing.image_recognition import ImageOcr
from core.processing.ocr import PdfPageOcr
from unimem_ocr.errors import OcrPrerequisiteError
from unimem_ocr.policy import (
    DEFAULT_IMAGE_LIMITS,
    DEFAULT_LIMITS,
    IMAGE_DPI_SUPPLIED,
    IMAGE_ENGINE_INPUT,
    PAGE_IMAGE_FORMAT,
    RECOGNITION_LANGUAGES,
    RENDER_DPI,
    RENDER_SCALE,
    TESSERACT_EXECUTABLE,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
    ImageOcrLimits,
    OcrLimits,
)
from unimem_ocr.prerequisites import (
    IMAGE_CAPABILITY,
    available_languages,
    describe_prerequisites,
    engine_version,
    require_engine,
    require_rasterizer,
)

#: The processor name a PDF-OCR-enabled deployment registers, restated here so the
#: composition layer can name it without importing the processor.
PDF_OCR_PROCESSOR_NAME: Final = "pdf-ocr"

#: The processor name an image-OCR-enabled deployment registers, for the same
#: reason.
IMAGE_OCR_PROCESSOR_NAME: Final = "image-ocr"

__all__ = [
    "DEFAULT_IMAGE_LIMITS",
    "DEFAULT_LIMITS",
    "IMAGE_DPI_SUPPLIED",
    "IMAGE_ENGINE_INPUT",
    "IMAGE_OCR_PROCESSOR_NAME",
    "PAGE_IMAGE_FORMAT",
    "PDF_OCR_PROCESSOR_NAME",
    "RECOGNITION_LANGUAGES",
    "RENDER_DPI",
    "RENDER_SCALE",
    "TESSERACT_EXECUTABLE",
    "TESSERACT_LANGUAGE_ARGUMENT",
    "TESSERACT_OEM",
    "TESSERACT_PSM",
    "ImageOcrLimits",
    "OcrLimits",
    "OcrPrerequisiteError",
    "available_languages",
    "build_tesseract_image_ocr",
    "build_tesseract_ocr",
    "describe_prerequisites",
    "engine_version",
    "require_engine",
    "require_rasterizer",
]


def build_tesseract_ocr(
    *,
    executable: str = TESSERACT_EXECUTABLE,
    limits: OcrLimits = DEFAULT_LIMITS,
) -> PdfPageOcr:
    """Validate every prerequisite and return a recognizer, or refuse to build one.

    This is the whole startup gate, in the order that produces the most useful
    message:

    1. the optional Python packages must import;
    2. the engine must run and report a version;
    3. **both** language data files must be present.

    Any failure is an :class:`~unimem_ocr.errors.OcrPrerequisiteError` and
    nothing is returned. Nothing is installed, downloaded, or worked around, and
    the language set is never narrowed to what happens to be available — a
    deployment that asked for ``eng+rus`` and got English is not the deployment
    that was asked for.

    The version the engine reports is passed into the adapter, so the identity
    recorded on every content object it contributes to is the identity of the
    software that actually ran.
    """
    require_rasterizer()
    version = require_engine(executable)

    from unimem_ocr.tesseract import TesseractPdfPageOcr

    return TesseractPdfPageOcr(engine_version=version, executable=executable, limits=limits)


def build_tesseract_image_ocr(
    *,
    executable: str = TESSERACT_EXECUTABLE,
    limits: ImageOcrLimits = DEFAULT_IMAGE_LIMITS,
) -> ImageOcr:
    """Validate the image-OCR prerequisites and return a recognizer, or refuse.

    The whole startup gate, and it is deliberately shorter than
    :func:`build_tesseract_ocr`'s:

    1. the engine must run and report a version;
    2. **both** language data files must be present.

    **There is no third step, and its absence is the point.**
    :func:`~unimem_ocr.prerequisites.require_rasterizer` is not called, so
    ``pypdfium2`` and ``Pillow`` are never imported by this path — a deployment
    that wants only image recognition does not install, load, or need the
    optional extra. Direct-image OCR hands the submitted bytes to the engine and
    decodes nothing, so a rasterizer and an imaging library would be dependencies
    it has no use for.

    The probe names the *image* capability, so a machine missing Tesseract is
    told that local image OCR needs it rather than being sent after a PDF
    renderer. When a deployment enables both capabilities this probe runs twice —
    two fixed-argument subprocess calls at startup — and that is accepted rather
    than cached: a module-level memo would make two independent gates depend on
    each other and on the order they happen to run in.

    Any failure is an :class:`~unimem_ocr.errors.OcrPrerequisiteError` and nothing
    is returned. Nothing is installed, downloaded, or worked around, and the
    language set is never narrowed to what happens to be available.

    The version the engine reports is passed into the adapter, so the identity
    recorded on every content object it contributes to is the identity of the
    software that actually ran.

    The import is inside the function, exactly as the PDF factory's is. It costs
    nothing native here, but it keeps the rule uniform: nothing in a default
    deployment imports an adapter.
    """
    version = require_engine(executable, capability=IMAGE_CAPABILITY)

    from unimem_ocr.image import TesseractImageOcr

    return TesseractImageOcr(engine_version=version, executable=executable, limits=limits)
