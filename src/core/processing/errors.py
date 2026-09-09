"""Errors raised across the processing boundary.

Processing failures are expected outcomes with names, not incidental
``ValueError``s. A caller can tell a malformed capture from a byte sequence
that is not text, and both from a routing problem, without reading messages.

Storage failures are deliberately *not* part of this hierarchy. The raw object
store already raises its own typed, storage-neutral errors
(:class:`~core.storage.errors.RawObjectStoreError` and its subclasses), and a
processor lets those propagate unchanged rather than flattening them — see
:mod:`core.processing.text`.
"""


class ProcessingError(Exception):
    """Base class for every error raised by the processing layer."""


class ProcessingInputError(ProcessingError):
    """The capture cannot be processed as presented.

    Raised when the capture does not carry a usable raw original, or when the
    material behind it holds nothing a canonical segment could be built from.
    """


class TextDecodingError(ProcessingError):
    """The stored bytes are not valid text in the expected encoding.

    Phase 0C decodes strict UTF-8 only. Nothing is guessed, replaced, or
    dropped, so undecodable bytes stop processing instead of silently becoming
    replacement characters.
    """


class InvalidCaptureProcessingStateError(ProcessingError):
    """The capture is not in a state from which processing may begin.

    Phase 0H starts processing from ``stored`` and from nothing else. A capture
    that is still ``received`` has no bytes to read yet; one that is already
    ``processing``, ``complete``, ``partial``, or ``failed`` has been through
    here, and starting again would be reprocessing — a decision with its own
    questions (is the old content object superseded? was the failure
    transient?) that this phase does not answer.
    """


class ProcessingOutputError(ProcessingError):
    """A processor returned content that does not belong to the capture.

    Raised when the returned ``ContentObject.source.capture_id`` is not the
    capture that was handed in. The object may be perfectly valid on its own —
    the contract checks its internal consistency — but it is not *this*
    capture's content, and recording the capture complete on the strength of it
    would attribute one capture's normalization to another.
    """


class ProcessorRoutingError(ProcessingError):
    """A capture could not be assigned to exactly one processor."""


class NoProcessorError(ProcessorRoutingError):
    """No registered processor handles this capture."""


class AmbiguousProcessorError(ProcessorRoutingError):
    """More than one registered processor handles this capture.

    Registration order is not precedence, so the router refuses to pick rather
    than resolving the overlap on its own.
    """
