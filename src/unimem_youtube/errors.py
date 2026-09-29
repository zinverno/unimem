"""Safe, actionable errors; never include upstream responses or signed URLs."""


class AcquisitionError(Exception):
    """A failed acquisition, before a capture has been accepted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExportError(Exception):
    """Export failed; the canonical capture is unaffected."""
