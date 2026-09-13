"""Local PDF page recognition: the optional adapter, outside ``core``.

This package is the concrete half of :mod:`core.processing.ocr`. It is installed
by an optional extra, imported by nothing that runs in a default deployment, and
reached only by an application that was explicitly composed with recognition
enabled::

    python -m unimem_api --data-dir ./data --pdf-ocr

It depends on ``core`` and never the other way round. ``core`` does not import
it, does not know its name, and cannot be made to load it; the only thing that
crosses between them is the
:class:`~core.processing.ocr.PdfPageOcr` protocol and the plain value types in
that module.

**Importing this package costs nothing.** :mod:`unimem_ocr.errors`,
:mod:`unimem_ocr.policy`, and :mod:`unimem_ocr.prerequisites` import no native
library, and this module imports none either — the rasterizer and the imaging
package are imported by :mod:`unimem_ocr.tesseract`, which
:func:`build_tesseract_ocr` loads at the moment it is called. That ordering is
what turns "pypdfium2 is not installed" from a traceback during startup into a
sentence explaining which extra to install.

Two systems are prerequisites and neither is installed, downloaded, or vendored
by this package: the Tesseract executable, and the ``eng`` and ``rus`` language
data files. See the README.
"""

from typing import Final

from core.processing.ocr import PdfPageOcr
from unimem_ocr.errors import OcrPrerequisiteError
from unimem_ocr.policy import (
    DEFAULT_LIMITS,
    PAGE_IMAGE_FORMAT,
    RECOGNITION_LANGUAGES,
    RENDER_DPI,
    RENDER_SCALE,
    TESSERACT_EXECUTABLE,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
    OcrLimits,
)
from unimem_ocr.prerequisites import (
    available_languages,
    describe_prerequisites,
    engine_version,
    require_engine,
    require_rasterizer,
)

#: The processor name an OCR-enabled deployment registers, restated here so the
#: composition layer can name it without importing the processor.
PDF_OCR_PROCESSOR_NAME: Final = "pdf-ocr"

__all__ = [
    "DEFAULT_LIMITS",
    "PAGE_IMAGE_FORMAT",
    "PDF_OCR_PROCESSOR_NAME",
    "RECOGNITION_LANGUAGES",
    "RENDER_DPI",
    "RENDER_SCALE",
    "TESSERACT_EXECUTABLE",
    "TESSERACT_LANGUAGE_ARGUMENT",
    "TESSERACT_OEM",
    "TESSERACT_PSM",
    "OcrLimits",
    "OcrPrerequisiteError",
    "available_languages",
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
