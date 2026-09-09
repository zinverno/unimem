"""A local filesystem raw object store.

Layout under the configured root::

    <root>/sha256/<d0:2>/<d2:4>/<digest>   finalized objects
    <root>/staging/<random>                in-flight writes

The layout is derived entirely from a validated digest and is an
implementation detail: it never appears in a ``RawObjectRef``.
"""

import hashlib
import io
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Final

from core.contracts import RawObjectRef
from core.storage.errors import RawObjectNotFoundError, RawObjectWriteError
from core.storage.raw import ReadableBinaryStream, raw_object_ref, resolve_digest

#: Bounded read size. Large enough to keep syscall overhead down, small enough
#: that a multi-gigabyte video never has to fit in memory.
_CHUNK_SIZE: Final = 1024 * 1024

_OBJECTS_DIRNAME: Final = "sha256"
_STAGING_DIRNAME: Final = "staging"


class LocalRawObjectStore:
    """Stores immutable raw originals in a content-addressed directory tree.

    The root is created on demand, so a fresh store can point at a path that
    does not exist yet.

    Atomicity rests on ``os.link``: an object becomes visible at its final path
    in one filesystem operation, and linking onto an existing path fails rather
    than replacing it. Nothing is ever written through a path a caller supplied
    — only through a path derived from a digest this store computed or
    validated. The root must therefore live on a single filesystem that
    supports hard links. Durability across power loss is not claimed: staged
    data is not fsynced.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def store_bytes(self, data: bytes, *, mime_type: str | None = None) -> RawObjectRef:
        """Store bytes already held in memory."""
        return self.store_stream(io.BytesIO(data), mime_type=mime_type)

    def store_stream(
        self, stream: ReadableBinaryStream, *, mime_type: str | None = None
    ) -> RawObjectRef:
        """Hash and stage a stream in bounded chunks, then finalize it.

        The stream is read from its current position and never rewound, so a
        non-seekable source works. On any failure the staging file is removed
        and no finalized object appears.
        """
        staging_path = self._create_staging_file()
        try:
            digest = self._drain_into(stream, staging_path)
            self._finalize(staging_path, digest)
        except BaseException:
            staging_path.unlink(missing_ok=True)
            raise
        return raw_object_ref(digest, mime_type=mime_type)

    @contextmanager
    def open(self, raw_object: RawObjectRef) -> Iterator[BinaryIO]:
        """Open a stored raw object for reading."""
        path = self._stored_path(raw_object)
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise RawObjectNotFoundError(f"raw object {path.name!r} could not be opened") from exc
        try:
            yield handle
        finally:
            handle.close()

    def read_bytes(self, raw_object: RawObjectRef) -> bytes:
        """Return the exact bytes of a stored raw object."""
        with self.open(raw_object) as handle:
            return handle.read()

    def exists(self, raw_object: RawObjectRef) -> bool:
        """Report whether the referenced object is present.

        A malformed reference raises ``InvalidRawObjectRefError`` rather than
        reporting ``False``: a reference the store cannot parse is a caller
        error, not an absent object.
        """
        return self._object_path(resolve_digest(raw_object)).is_file()

    def _create_staging_file(self) -> Path:
        """Create an empty staging file beside the objects, on the same filesystem.

        The name comes from :mod:`tempfile`, never from caller input.
        """
        staging_dir = self._root / _STAGING_DIRNAME
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=staging_dir, prefix="incoming-")
        except OSError as exc:
            raise RawObjectWriteError(f"could not stage a raw object under {staging_dir}") from exc
        os.close(descriptor)
        return Path(name)

    def _drain_into(self, stream: ReadableBinaryStream, staging_path: Path) -> str:
        """Copy the stream into staging in bounded chunks, hashing as it goes."""
        digest = hashlib.sha256()
        try:
            with staging_path.open("wb") as staging_file:
                while chunk := stream.read(_CHUNK_SIZE):
                    digest.update(chunk)
                    staging_file.write(chunk)
        except Exception as exc:
            raise RawObjectWriteError("failed while reading the raw object stream") from exc
        return digest.hexdigest()

    def _finalize(self, staging_path: Path, digest: str) -> None:
        """Link staging into its content-addressed home, then drop staging.

        ``os.link`` raising ``FileExistsError`` is the deduplication path: the
        identical bytes are already stored, so the existing object stands and
        the staged copy is discarded. An object is therefore never rewritten,
        and two writers of identical bytes converge on one file.
        """
        final_path = self._object_path(digest)
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RawObjectWriteError(
                f"could not prepare the location for raw object {digest!r}"
            ) from exc
        try:
            os.link(staging_path, final_path)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RawObjectWriteError(f"could not finalize raw object {digest!r}") from exc
        finally:
            staging_path.unlink(missing_ok=True)

    def _object_path(self, digest: str) -> Path:
        """Map a validated digest to its deterministic location."""
        return self._root / _OBJECTS_DIRNAME / digest[:2] / digest[2:4] / digest

    def _stored_path(self, raw_object: RawObjectRef) -> Path:
        digest = resolve_digest(raw_object)
        path = self._object_path(digest)
        if not path.is_file():
            raise RawObjectNotFoundError(f"raw object {digest!r} is not present in this store")
        return path
