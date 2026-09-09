"""Errors raised across the persistence boundaries.

A caller catches these and nothing else. ``sqlite3.IntegrityError``,
``sqlite3.OperationalError``, ``OSError``, and Pydantic's ``ValidationError``
are all implementation detail of whichever backend is in use, and none of them
is allowed to escape a port.

Two hierarchies live here, one per port, and they are deliberately siblings
rather than a shared tree. A capture record and a canonical content object are
different things with different lifecycles, and a caller that can only ask "did
persistence fail?" cannot tell "the capture lifecycle could not be recorded"
from "the normalized content could not be stored" — which, in the processing
orchestrator, lead to different outcomes.

Within each hierarchy the distinctions are the ones a caller acts on
differently: "this id is already taken", "there is nothing under this id",
"what is stored cannot be read back", and "the backend failed".
"""


class CaptureRecordStoreError(Exception):
    """Base class for every error a capture record store raises."""


class CaptureRecordAlreadyExistsError(CaptureRecordStoreError):
    """A record with this id is already stored.

    Raised by ``create`` only. It means the id is taken, not that the write
    failed for some unknown reason, and the stored snapshot is untouched.
    """


class CaptureRecordNotFoundError(CaptureRecordStoreError):
    """No record with this id is stored.

    Raised by ``get`` and by ``replace``: replacing something that is not there
    is a caller error, never an implicit create.
    """


class CaptureRecordCorruptError(CaptureRecordStoreError):
    """The stored payload could not be read back as a valid capture record.

    Raised when the payload is not text, is not JSON, is not a valid
    ``CaptureRecord`` (including an unsupported ``schema_version``), or carries
    an id that disagrees with the key it was stored under. The record was
    written by something that did not honour this store's contract, so it is a
    data-integrity problem rather than a missing record.
    """


class CaptureRecordPersistenceError(CaptureRecordStoreError):
    """The backend could not carry out the operation.

    The catch-all for a failing store — an unreadable database file, a locked
    database, a disk error. Deliberately *not* used for a duplicate id or a
    missing record, which are expected outcomes with their own types.
    """


class ContentObjectStoreError(Exception):
    """Base class for every error a content object store raises.

    A sibling of :class:`CaptureRecordStoreError`, not a subclass of it, and
    emphatically not a ``ProcessingError``: failing to *store* canonical
    content says nothing about whether the capture could be normalized.
    """


class ContentObjectAlreadyExistsError(ContentObjectStoreError):
    """Canonical content is already stored under this id, or for this capture.

    Raised by ``create`` only, for either uniqueness rule — a content id that
    is taken, or a capture that already has canonical content. The stored
    object is untouched.

    The second case is the interesting one. It means something believes this
    capture needs normalizing again: a concurrent worker, a retry, or a
    lifecycle that drifted. Which of those it is cannot be decided here, so the
    error is raised rather than resolved.
    """


class ContentObjectNotFoundError(ContentObjectStoreError):
    """No canonical content is stored under this content id or capture id."""


class ContentObjectCorruptError(ContentObjectStoreError):
    """The stored payload could not be read back as a valid content object.

    Raised when the payload is not text, is not JSON, is not a valid
    ``ContentObject`` (including an unsupported ``schema_version``), or carries
    an id or a ``source.capture_id`` that disagrees with the columns it was
    filed under. The row was written by something that did not honour this
    store's contract, so it is a data-integrity problem rather than absence.
    """


class ContentObjectPersistenceError(ContentObjectStoreError):
    """The backend could not carry out the operation.

    The catch-all for a failing store. Deliberately *not* used for a duplicate
    or a missing object, which are expected outcomes with their own types.
    """
