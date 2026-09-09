"""The raw object store port and its reference format.

Raw originals are content-addressed: identity is the SHA-256 of the bytes and
nothing else. MIME type, filename, capture source, URL, and timestamps
describe a capture — they never take part in raw object identity.

The logical reference is ``sha256:<digest>``. It is storage-neutral by
construction: it names the content, not where a particular backend happens to
keep it.
"""

import re
from contextlib import AbstractContextManager
from typing import BinaryIO, Final, Protocol

from core.contracts import RawObjectRef
from core.storage.errors import InvalidRawObjectRefError

#: The only reference scheme this phase understands. A future digest algorithm
#: would be a new scheme and an explicit migration decision, not a silent swap.
RAW_REF_SCHEME: Final = "sha256"

_DIGEST_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


def build_raw_ref(digest: str) -> str:
    """Build the logical reference for a digest."""
    return f"{RAW_REF_SCHEME}:{digest}"


def parse_raw_ref(ref: str) -> str:
    """Return the digest carried by a logical reference.

    Strict on purpose: this is the only thing standing between an arbitrary
    caller-supplied string and a filesystem path.
    """
    scheme, separator, digest = ref.partition(":")
    if not separator:
        raise InvalidRawObjectRefError(f"raw object ref {ref!r} has no scheme")
    if scheme != RAW_REF_SCHEME:
        raise InvalidRawObjectRefError(
            f"unsupported raw object ref scheme {scheme!r}; expected {RAW_REF_SCHEME!r}"
        )
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise InvalidRawObjectRefError(
            f"raw object ref {ref!r} does not carry a lowercase sha-256 hex digest"
        )
    return digest


def resolve_digest(raw_object: RawObjectRef) -> str:
    """Return the digest a reference identifies, rejecting anything ambiguous.

    A reference carrying both a ``ref`` and a ``sha256`` that disagree is an
    error: the store does not get to pick one.
    """
    if raw_object.ref is None:
        raise InvalidRawObjectRefError(f"raw object {raw_object.id!r} has no ref")
    digest = parse_raw_ref(raw_object.ref)
    if raw_object.sha256 is not None and raw_object.sha256 != digest:
        raise InvalidRawObjectRefError(
            f"raw object {raw_object.id!r} has ref digest {digest!r} "
            f"but sha256 {raw_object.sha256!r}"
        )
    return digest


def raw_object_ref(digest: str, *, mime_type: str | None = None) -> RawObjectRef:
    """Build the canonical reference a store returns for stored bytes.

    The digest is the object's identity, so it is also its ``id``. This applies
    to raw binary objects only — it is not a general identifier policy.
    """
    return RawObjectRef(
        id=digest,
        mime_type=mime_type,
        sha256=digest,
        ref=build_raw_ref(digest),
    )


class ReadableBinaryStream(Protocol):
    """The only capability a source must provide: bounded reads of bytes.

    Deliberately narrower than ``BinaryIO``. Seeking and telling are not
    required, so a non-seekable source — a socket, a pipe, an upload still in
    flight — is a perfectly good input.
    """

    def read(self, size: int = ..., /) -> bytes:
        """Return at most ``size`` bytes, or empty bytes at end of stream."""
        ...


class RawObjectStore(Protocol):
    """Persists immutable raw originals and hands back logical references.

    There is deliberately no update or delete: raw originals are immutable, and
    a lifecycle policy is a later architectural decision.
    """

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        """Store bytes already held in memory."""
        ...

    def store_stream(
        self, stream: ReadableBinaryStream, *, mime_type: str | None = None
    ) -> RawObjectRef:
        """Store a stream, consumed in bounded chunks from its current position.

        The stream is never rewound and seek support is not required.
        """
        ...

    def open(self, raw_object: RawObjectRef) -> AbstractContextManager[BinaryIO]:
        """Open a stored raw object for reading."""
        ...

    def read_bytes(self, raw_object: RawObjectRef) -> bytes:
        """Return the exact bytes of a stored raw object."""
        ...

    def exists(self, raw_object: RawObjectRef) -> bool:
        """Report whether the referenced object is present in this store."""
        ...
