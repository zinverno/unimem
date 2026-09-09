"""The processor port.

A processor is the normalization boundary: it turns an accepted capture plus
its immutable original into a canonical
:class:`~core.contracts.content.ContentObject`. Everything downstream reads one
shape, whatever modality it came from.

What a processor is not: it does not persist anything, does not advance the
capture lifecycle, does not mutate the capture record or the raw original, and
does not render. Those belong to orchestration and to later phases.

The API is synchronous on purpose. The one dependency a processor has today —
:class:`~core.storage.raw.RawObjectStore` — is synchronous, and adding an
awaitable surface for processors that do not exist yet would buy nothing. See
`ADR-004 <../../docs/ADR/ADR-004-processing-boundary-and-routing.md>`_.
"""

from typing import Protocol

from core.contracts import CaptureRecord, ContentObject


class Processor(Protocol):
    """Turns one kind of capture into canonical content.

    ``name`` and ``version`` are the processor's stable identity. They describe
    *processing semantics*, not deployment: they are not the package version
    and not a commit SHA, and they change when what the processor produces
    changes. The same two values are recorded on every ``Provenance`` and
    ``ProcessingRecord`` the processor emits, so any segment can be traced back
    to the code that produced it.
    """

    name: str
    version: str

    def supports(self, capture: CaptureRecord) -> bool:
        """Report whether this processor handles this capture.

        A pure capability check over the capture record. It performs no I/O,
        reads no stored bytes, mutates nothing, and does not fail because the
        raw original is missing — a capture this processor handles but cannot
        currently read is still a capture it handles. Answering the question
        must be cheap, because the router asks every processor.
        """
        ...

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Normalize the capture into a canonical content object.

        Raises :class:`~core.processing.errors.ProcessingError` when the
        capture cannot be normalized. A partial or invalid content object is
        never returned in place of an error.
        """
        ...
