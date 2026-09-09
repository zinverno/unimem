"""Capability-based routing to exactly one processor.

The router asks every registered processor whether it handles a capture and
requires precisely one answer of yes. Zero matches and several matches are both
errors, and neither is resolved silently.

This is the whole point of the router: with one processor, first-match routing
and exact-match routing are indistinguishable; with a web, image, and document
processor in the same list, first-match routing turns registration order into
undeclared precedence, and the day two processors both claim a capture the
system quietly picks one. Failing loudly now costs an explicit decision later,
which is the cheaper of the two.
"""

from collections.abc import Iterable

from core.contracts import CaptureRecord, ContentObject
from core.processing.base import Processor
from core.processing.errors import AmbiguousProcessorError, NoProcessorError


def _describe(processor: Processor) -> str:
    return f"{processor.name}@{processor.version}"


class ProcessorRouter:
    """Selects the one processor that handles a capture.

    Processors are supplied explicitly at construction. There is no registry,
    no discovery, no entry points, and no configuration loading: the set of
    processors an application runs with is written down where the application
    is wired together.
    """

    def __init__(self, processors: Iterable[Processor]) -> None:
        self._processors: tuple[Processor, ...] = tuple(processors)

    def select(self, capture: CaptureRecord) -> Processor:
        """Return the single processor that handles ``capture``.

        Every processor is asked, including the ones after a match, so an
        overlap is detected rather than hidden by the order of the list.
        """
        matches = [processor for processor in self._processors if processor.supports(capture)]
        if not matches:
            raise NoProcessorError(
                f"no processor handles capture {capture.id!r} "
                f"of type {capture.payload_type.value!r}"
            )
        if len(matches) > 1:
            claimed = ", ".join(_describe(processor) for processor in matches)
            raise AmbiguousProcessorError(
                f"capture {capture.id!r} of type {capture.payload_type.value!r} "
                f"is claimed by several processors: {claimed}"
            )
        return matches[0]

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Select a processor and run it. Convenience only — it adds no policy."""
        return self.select(capture).process(capture)
