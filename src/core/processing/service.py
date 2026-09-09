"""Processing orchestration: driving one capture through its lifecycle.

Phase 0C built the parts — a :class:`~core.processing.base.Processor` that
normalizes, a :class:`~core.processing.router.ProcessorRouter` that picks
exactly one — and deliberately left out the thing that calls them in order and
records what happened. This is that thing, and only that thing::

    capture_id
        -> CaptureRecordStore.get          the authoritative snapshot
        -> require STORED
        -> ProcessorRouter.select          routing is pure: no state written yet
        -> CaptureRecord(PROCESSING)       durable before any processor I/O
        -> Processor.process
        -> ContentObject
        -> CaptureRecord(COMPLETE)         durable before the caller sees it
        -> return the ContentObject

The processor stays exactly as pure about lifecycle as ADR-004 made it: it is
handed a record, it returns content or raises, and it never writes a status.
Every status write in the system happens here.

Two rules shape the failure behaviour, and they are not the same rule:

* A **``ProcessingError``** is a verdict about the capture — these bytes are not
  UTF-8, this record has no raw object, this output belongs to someone else.
  Trying again changes nothing, so the capture is durably marked ``failed`` and
  the original error is re-raised untouched.
* **Anything else** — a raw-store failure, a bug, an interrupted process — says
  something about the run, not about the capture. The record stays
  ``processing``, which is true, and nothing terminal is invented. This is
  Phase 0F's rule against fabricating terminal state from infrastructure
  failure, one layer up.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from core.contracts import CaptureRecord, CaptureStatus, ContentObject
from core.persistence import CaptureRecordStore
from core.processing.errors import (
    InvalidCaptureProcessingStateError,
    ProcessingError,
    ProcessingOutputError,
)
from core.processing.router import ProcessorRouter

#: The one status a capture may be in when processing begins.
STARTING_STATUS = CaptureStatus.STORED


def utc_now() -> datetime:
    """The default clock: the current time, timezone-aware, in UTC.

    Deliberately a local twin of intake's clock rather than an import of it:
    processing does not depend on intake, and a two-line default is a smaller
    price than a dependency between two layers that otherwise share nothing.
    """
    return datetime.now(UTC)


class ProcessingOrchestrator:
    """Takes a capture from ``stored`` to ``complete`` — or to a truthful failure.

    The router and the record store arrive as their existing types, and the
    clock as a plain callable, for the same reason as in intake: a test needs
    to control time, and a function already does that. There is no worker,
    queue, task system, scheduler, retry engine, registry, DI container, or
    async surface. One capture, one call, synchronously.
    """

    def __init__(
        self,
        router: ProcessorRouter,
        record_store: CaptureRecordStore,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._router = router
        self._record_store = record_store
        self._now = now

    def process(self, capture_id: str) -> ContentObject:
        """Normalize one stored capture and record the lifecycle around it.

        The capture is loaded **by id**, never accepted as an object from the
        caller: a lifecycle decision taken against a snapshot somebody has been
        holding is a decision about the past. What the store returns now is the
        authority.

        Raises :class:`~core.processing.errors.InvalidCaptureProcessingStateError`
        if the capture is not ``stored``, a routing error if the set of
        processors does not resolve to exactly one, and the processor's own
        errors as described in the module docstring. Store errors keep their
        own types *and their own causes* throughout: nothing here re-chains
        one, so a persistence error still names the backend failure beneath it.
        """
        stored = self._record_store.get(capture_id)
        if stored.status is not STARTING_STATUS:
            raise InvalidCaptureProcessingStateError(
                f"capture {capture_id!r} is {stored.status.value!r}; "
                f"processing starts from {STARTING_STATUS.value!r}"
            )

        # Routing first, and before the clock is read. `select` is pure by the
        # router's contract, and a capture nothing can handle — or that two
        # processors claim — is a wiring problem, not a processing attempt. It
        # leaves the capture exactly as it found it.
        processor = self._router.select(stored)

        processing = self._advance(stored, CaptureStatus.PROCESSING)
        self._record_store.replace(processing)

        try:
            # The already-selected processor, called directly. Routing again
            # through `router.process` would re-run selection on the far side
            # of a durable state change, so a processor set that changed in
            # between could run something other than what this record was
            # marked processing for.
            #
            # It is handed its own copy. `processing` is the snapshot that was
            # durably written and is therefore the authority for whatever
            # snapshot comes next; a processor is third-party code, and a
            # record is a validated snapshot rather than a frozen one, so
            # nothing but a copy stops one from editing the lineage out from
            # under the completion record.
            content = processor.process(processing.model_copy(deep=True))
            if content.source.capture_id != processing.id:
                raise ProcessingOutputError(
                    f"processor {processor.name}@{processor.version} returned content for "
                    f"capture {content.source.capture_id!r} while processing {processing.id!r}"
                )
        except ProcessingError as processing_error:
            # Recorded, then re-raised untouched. If this write itself fails,
            # its error propagates as it is: no wrapping, and deliberately no
            # `raise ... from processing_error`, which would overwrite the
            # `__cause__` the record store already set to name the backend
            # failure underneath it. Python links the two on its own — the
            # store error surfaces with its own cause intact and this
            # processing error reachable as its `__context__` — and the capture
            # is then left `processing`, which is at least true, because
            # `failed` was never durably recorded.
            failed = self._advance(processing, CaptureStatus.FAILED, error=str(processing_error))
            self._record_store.replace(failed)
            raise

        complete = self._advance(processing, CaptureStatus.COMPLETE)
        self._record_store.replace(complete)
        return content

    def _advance(
        self, previous: CaptureRecord, status: CaptureStatus, *, error: str | None = None
    ) -> CaptureRecord:
        """Build the next lifecycle snapshot from the last durable one.

        The previous snapshot is the only source. Nothing is re-read from the
        caller or from an earlier snapshot across a side-effect boundary, which
        is what keeps two records describing one capture from disagreeing — the
        lineage rule intake settled in Phase 0G.

        Every capture fact is carried over, deep-copied so that no two
        snapshots share mutable nested state (a ``CaptureIntent`` holds a
        ``tags`` list, and in-place list mutation never reaches a validator).
        ``schema_version`` travels with the rest: a legacy 0.1 record stays 0.1,
        because 0.2 requires a ``context`` that cannot be invented for a capture
        that never recorded one. Only ``status``, ``updated_at``, and ``error``
        are new, and the result is a freshly validated instance rather than an
        edit of the old one.
        """
        raw_object = previous.raw_object
        context = previous.context
        intent = previous.intent
        return CaptureRecord(
            schema_version=previous.schema_version,
            id=previous.id,
            status=status,
            received_at=previous.received_at,
            updated_at=self._now(),
            source=previous.source.model_copy(deep=True),
            payload_type=previous.payload_type,
            raw_object=None if raw_object is None else raw_object.model_copy(deep=True),
            error=error,
            context=None if context is None else context.model_copy(deep=True),
            intent=None if intent is None else intent.model_copy(deep=True),
            title=previous.title,
        )
