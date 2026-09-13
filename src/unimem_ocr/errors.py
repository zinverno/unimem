"""The one failure this package raises before it is allowed to serve anything."""


class OcrPrerequisiteError(Exception):
    """Local PDF OCR was asked for and this machine cannot provide it.

    Raised only while the application is being assembled, never during a
    capture. It means one of the four prerequisites is absent: the optional
    rasterization packages, the Tesseract executable, or either of the two
    language data files this build recognizes with.

    It exists as its own type, separate from
    :class:`~core.processing.ocr.PdfOcrExecutionError`, because the two are
    answers to different questions. This one says "do not start"; that one says
    "a running server could not carry out one recognition". Conflating them
    would make a misconfigured deployment look like a transient failure, and a
    server that starts and then fails every scanned PDF is strictly worse than
    one that refuses to start and says why.

    Its message names what is missing and how to provide it, and nothing else:
    it is read by whoever typed the command, printed to a terminal, and never
    returned over HTTP.
    """
