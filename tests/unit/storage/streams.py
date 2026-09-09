"""Stream doubles for exercising the streaming contract.

The store promises to work with a source that cannot seek and to read in
bounded chunks. These make a test able to prove that from the outside, without
reaching into the implementation.
"""

import io
from types import TracebackType


class NonSeekableStream(io.RawIOBase):
    """A read-only stream that refuses to seek or report its position."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        raise io.UnsupportedOperation("stream is not seekable")

    def tell(self) -> int:
        raise io.UnsupportedOperation("stream is not seekable")

    def read(self, size: int | None = -1, /) -> bytes:
        return self._buffer.read(size)


class RecordingStream(io.RawIOBase):
    """Records the read sizes it is asked for and rejects unbounded reads."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)
        self.requested_sizes: list[int] = []

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1, /) -> bytes:
        if size is None or size < 0:
            raise AssertionError("the store must not read the whole stream at once")
        self.requested_sizes.append(size)
        return self._buffer.read(size)


class FailingStream(io.RawIOBase):
    """Yields a few chunks and then fails, like a truncated upload."""

    def __init__(self, chunk: bytes, *, chunks_before_failure: int) -> None:
        self._chunk = chunk
        self._remaining = chunks_before_failure

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1, /) -> bytes:
        if self._remaining <= 0:
            raise OSError("source went away mid-stream")
        self._remaining -= 1
        return self._chunk

    def __enter__(self) -> "FailingStream":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
