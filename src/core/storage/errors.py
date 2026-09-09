"""Errors raised across the raw object store boundary.

Callers should never have to catch ``OSError``, ``ValueError``, or anything
else that leaks how a particular backend is implemented.
"""


class RawObjectStoreError(Exception):
    """Base class for every error a raw object store raises."""


class RawObjectNotFoundError(RawObjectStoreError):
    """The referenced raw object is not present in this store."""


class InvalidRawObjectRefError(RawObjectStoreError):
    """The reference is not a well-formed raw object reference.

    Raised for an unknown scheme, a malformed digest, or a reference whose
    ``ref`` and ``sha256`` disagree.
    """


class RawObjectWriteError(RawObjectStoreError):
    """The raw object could not be written.

    Raised when the source stream fails midway or the backend cannot stage or
    finalize the object. No finalized object is left behind.
    """
