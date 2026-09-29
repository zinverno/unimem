"""Safe, actionable errors; never include upstream responses or signed URLs."""


class AcquisitionError(Exception):
    """A failed acquisition, before a capture has been accepted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExportError(Exception):
    """Export failed; the canonical capture is unaffected."""


class CapturePipelineError(Exception):
    """An identified capture attempt did not confirm completion; no invented status."""

    def __init__(self, capture_id: str, code: str) -> None:
        super().__init__("Capture pipeline did not complete; stored state remains authoritative.")
        self.capture_id = capture_id
        self.code = code
