"""Errors raised across the capture record store boundary.

A caller catches these and nothing else. ``sqlite3.IntegrityError``,
``sqlite3.OperationalError``, ``OSError``, and Pydantic's ``ValidationError``
are all implementation detail of whichever backend is in use, and none of them
is allowed to escape the port.

The distinctions here are the ones a caller acts on differently: "this id is
already taken", "there is nothing under this id", "what is stored cannot be
read back as a capture record", and "the backend failed".
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
