"""The one failure this package raises before it is allowed to serve anything."""


class OcrPrerequisiteError(Exception):
    """A local OCR capability was asked for and this machine cannot provide it.

    Raised only while the application is being assembled, never during a
    capture. For PDF OCR it means one of the four prerequisites is absent: the
    optional rasterization packages, the Tesseract executable, or either of the
    two language data files this build recognizes with. For direct-image OCR the
    list is shorter by the packages — that capability loads no rasterizer and no
    imaging library — and the message names the capability that asked, so an
    operator is never sent after a dependency the thing they enabled never uses.

    It exists as its own type, separate from
    :class:`~core.processing.ocr.PdfOcrExecutionError` and from
    :class:`~core.processing.image_recognition.ImageOcrExecutionError`, because
    those are answers to a different question. This one says "do not start"; they
    say "a running server could not carry out one recognition". Conflating them
    would make a misconfigured deployment look like a transient failure, and a
    server that starts and then fails every scanned PDF — or every photograph —
    is strictly worse than one that refuses to start and says why.

    Its message names what is missing and how to provide it, and nothing else:
    it is read by whoever typed the command, printed to a terminal, and never
    returned over HTTP.
    """
