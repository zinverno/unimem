"""Test doubles for the local media adapter, and nothing production could use.

Two of these exist to observe something the adapter promises but a real ffprobe
would hide: :class:`RecordingStream` proves the submitted bytes are copied in
fixed-size pieces rather than read whole, and :class:`FakeCompleted` lets a test
stand in for one subprocess call without a subprocess.
"""

from typing import IO, Any, Final

#: An arbitrary capture-shaped payload. Its *content* is never parsed by these
#: tests — only its size and how it was read matter.
PAYLOAD: Final = b"\x00\x01media bytes\xff" * 1000


class RecordingStream:
    """A binary stream that remembers the size of every read it was asked for.

    The point is :attr:`read_sizes`. ``stream.read()`` with no argument holds a
    whole media file in memory, and ``stream.read(n)`` in a loop does not; the
    difference is invisible in the result and visible here.
    """

    def __init__(self, data: bytes = PAYLOAD) -> None:
        self._data = data
        self._position = 0
        #: Every argument :meth:`read` was called with, in order. ``None`` means
        #: an unbounded read, which the adapter must never perform.
        self.read_sizes: list[int | None] = []
        self.closed = False

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        if size is None or size < 0:
            chunk = self._data[self._position :]
        else:
            chunk = self._data[self._position : self._position + size]
        self._position += len(chunk)
        return chunk

    def close(self) -> None:
        # Recorded, never expected: the port says the caller owns the handle.
        self.closed = True


class FailingStream:
    """A stream whose first read fails the way a broken file handle does."""

    def __init__(self, error: OSError | None = None) -> None:
        self._error = error or OSError(5, "Input/output error")

    def read(self, size: int | None = None) -> bytes:
        raise self._error


class FakeCompleted:
    """What :func:`subprocess.run` returns, for a runner that never starts one."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def write_stdout(text: bytes) -> Any:
    """A fake ``subprocess.run`` that writes ``text`` to the sink it was handed.

    The real runner gives the child a temporary file as its standard output and
    reads it back afterwards, so a double has to write there too — returning
    bytes would exercise a code path that does not exist.
    """

    def run(arguments: list[str], **kwargs: Any) -> FakeCompleted:
        sink: IO[bytes] = kwargs["stdout"]
        sink.write(text)
        return FakeCompleted()

    return run


#: Dotted paths for :meth:`pytest.MonkeyPatch.setattr`.
#:
#: Named by string rather than by reaching through a module's own imports,
#: which is both what the type checker wants and the more honest statement:
#: what is being replaced is the standard-library call these modules make, not
#: an attribute they chose to publish.
RUN: Final = "unimem_media.engine.subprocess.run"
TEMPORARY_FILE: Final = "unimem_media.engine.tempfile.TemporaryFile"
PROBE_RUN: Final = "unimem_media.prerequisites.subprocess.run"
