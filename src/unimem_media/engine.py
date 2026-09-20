"""Running a local ffprobe, once, over bytes this process staged. No domain.

The security-sensitive half of this package, in one place: build a fixed
argument list, stage the caller's stream into a private temporary file, hand
that file to the child as its standard input, point ffprobe at the descriptor it
already holds, bound the whole thing with a wall clock and an output budget, and
refuse anything that is not a clean success.

**This module is deliberately ignorant.** It is stdlib-only: it imports nothing
from ``core`` and nothing from the rest of this package but
:mod:`unimem_media.policy`. It does not know what a capture is, what a container
is, or what a stream is, and its errors say nothing about any of them. Reading
the JSON and deciding what it means belongs to :mod:`unimem_media.ffprobe`.

**Why the bytes are staged rather than piped.** The port hands over a
``BinaryIO`` and ffprobe needs to *seek* — container headers live at both ends of
an MP4, and a demuxer that cannot rewind either fails or reports less than the
file says. A pipe is not seekable, so the bytes are copied into a
:func:`tempfile.TemporaryFile`, which is. That file is created by this process,
is never named on the command line, has no name a caller could supply or guess,
and is unlinked before it is ever handed over — so the child inherits an open
descriptor and no path at all. See ADR-023.

**Nothing is read whole.** The input is copied in fixed-size chunks, so a large
upload costs one buffer rather than its own size in resident memory, and the
child's standard output is written to a second temporary file and read back
under a byte budget rather than accumulated from a pipe.

**Standard error is discarded unread.** It is another program's prose, it is not
a stable interface, on some builds it names paths on this machine, and — most
importantly — it is not evidence. A sentence ffprobe printed is never parsed to
decide whether the submitted media is valid: that verdict belongs to ``core``,
reached from the structural result, and never to this module reading English.
Sending it to :data:`~subprocess.DEVNULL` rather than capturing it is also what
makes the child's output bounded by construction on that side.

**Failures are structured.** :class:`EngineInvocationError` carries a
:attr:`~EngineInvocationError.reason` and, where one exists, the child's exit
status, so the adapter branches on attributes rather than on prose. Nothing here
is written for a client.
"""

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, BinaryIO, Final, Literal

from unimem_media.policy import (
    DEFAULT_LIMITS,
    FFPROBE_EXECUTABLE,
    INPUT_URL,
    LOG_LEVEL,
    PRINT_FORMAT,
    PROTOCOL_WHITELIST,
    SHOW_ENTRIES,
    MediaProbeLimits,
)

#: The submitted stream could not be staged, or the child's output could not be
#: read back: a temporary file could not be created, written, rewound, or read.
#: A fact about this machine's temporary storage, never about the media.
STAGING_ERROR: Final = "staging_error"

#: The engine could not be started at all: it is missing, not executable, or the
#: operating system refused for some other reason.
LAUNCH_ERROR: Final = "launch_error"

#: The engine was started and did not finish inside its wall-clock bound. The
#: child has been killed and reaped before the error is raised.
TIMEOUT: Final = "timeout"

#: The engine ran to completion and reported failure.
NONZERO_EXIT: Final = "nonzero_exit"

#: The engine succeeded and wrote more than the adapter's output budget.
OUTPUT_TOO_LARGE: Final = "output_too_large"

#: The engine succeeded and printed something that is not valid UTF-8.
INVALID_UTF8: Final = "invalid_utf8"

#: Every way one invocation can fail to produce trustworthy output.
InvocationReason = Literal[
    "staging_error",
    "launch_error",
    "timeout",
    "nonzero_exit",
    "output_too_large",
    "invalid_utf8",
]


class EngineInvocationError(Exception):
    """One ffprobe invocation did not produce output this build can trust.

    An *internal* type. It never crosses into ``core``, never reaches an HTTP
    mapping, and never reaches a client:
    :class:`~unimem_media.ffprobe.FfprobeMediaProbe` catches it at its own
    boundary and re-raises
    :class:`~core.processing.media_probe.MediaProbeExecutionError`, which is the
    only failure the port documents.

    :attr:`reason` and :attr:`returncode` exist so that wrapper can say something
    specific without reading :func:`str`. The message is a server-log sentence
    and carries no engine output.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: InvocationReason,
        returncode: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        #: Which kind of failure this was.
        self.reason: InvocationReason = reason
        #: The child's exit status, for :data:`NONZERO_EXIT` and nothing else.
        self.returncode = returncode
        #: The operating system's own explanation, for :data:`LAUNCH_ERROR` and
        #: :data:`STAGING_ERROR`. Kept for a log message, never for a client.
        self.detail = detail


@dataclass(frozen=True, slots=True)
class FfprobeInvocation:
    """Everything one probe needs from its caller.

    A plain frozen value with two fields, both supplied by the adapter from this
    package's own constants and the deployment's configured executable name.
    Nothing is read from an environment variable, a configuration file, or a
    request, and there is deliberately nothing here that could carry a path, a
    filename, or a URL: what varies between two invocations is which ffprobe runs
    and how long it may run, and nothing else.
    """

    executable: str = FFPROBE_EXECUTABLE
    limits: MediaProbeLimits = DEFAULT_LIMITS


def build_arguments(invocation: FfprobeInvocation) -> list[str]:
    """The exact argument vector for one probe.

    Separate from :func:`run_ffprobe` so a test can assert on the command line
    without starting a process, and so that "the media argv did not change" is a
    claim about a pure function.

    Every element after the executable is a module constant from
    :mod:`unimem_media.policy`. **No filename, capture id, source URL, raw-store
    path, ``file_ref``, digest, declared MIME type, or any other
    request-controlled string appears here** — not as an argument, not as an
    option value, and not as a path. The input is named ``fd:``, which is a
    descriptor the child already inherited, so there is no name for one to
    become.

    The order is fixed and is part of the contract with the adapter: executable,
    ``-hide_banner``, ``-loglevel``, ``-protocol_whitelist``, ``-print_format``,
    ``-show_entries``, then ``-i``.
    """
    return [
        invocation.executable,
        "-hide_banner",
        "-loglevel",
        LOG_LEVEL,
        "-protocol_whitelist",
        PROTOCOL_WHITELIST,
        "-print_format",
        PRINT_FORMAT,
        "-show_entries",
        SHOW_ENTRIES,
        "-i",
        INPUT_URL,
    ]


def stage(stream: BinaryIO, destination: IO[bytes], *, chunk_size: int) -> int:
    """Copy ``stream`` into ``destination`` in fixed-size pieces, and rewind it.

    Returns the number of bytes copied, which the caller uses for nothing but a
    log message — it is deliberately not reported to ``core``, which asked about
    container structure and not about file size.

    ``stream.read(chunk_size)`` in a loop, never a bare ``stream.read()``. The
    difference is the whole memory story of this adapter: a bare read would hold
    an entire media file — which may be gigabytes — in this process, and the
    result would be discarded a moment later anyway. The destination is rewound
    before returning, because the child is going to read it from the start.
    """
    copied = 0
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        destination.write(chunk)
        copied += len(chunk)
    destination.seek(0)
    return copied


@contextmanager
def _temporary_files() -> Iterator[tuple[IO[bytes], IO[bytes]]]:
    """Two private temporary files, closed on every path out.

    :func:`tempfile.TemporaryFile` rather than ``NamedTemporaryFile``: on every
    platform this project supports it is unlinked immediately, so it has no name
    in the filesystem at all. That is not tidiness — a file with no name cannot
    be collided with, guessed at, raced, or leaked, and it cannot be handed to
    ffprobe as a path even by mistake. Each call to :func:`run_ffprobe` creates
    its own pair, which is what makes one probe instance safe to share between
    the audio and video processors and safe to call from two threads at once.
    """
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as sink:
        yield source, sink


def run_ffprobe(stream: BinaryIO, invocation: FfprobeInvocation) -> str:
    """Probe one stream's bytes and return exactly what ffprobe printed.

    The whole invocation, in order:

    1. two private temporary files are created, and both are closed on every path
       out of this function — success, refusal, timeout, and unexpected exception
       alike;
    2. the caller's stream is copied into the first in fixed-size chunks and the
       copy is rewound;
    3. ffprobe is launched with ``shell=False`` and a *list* of arguments, so no
       shell string is constructed, there is nothing to quote, and nothing to
       interpret;
    4. that temporary file is the child's standard input, its standard output is
       the second temporary file, and its standard error is discarded;
    5. the child is bounded by :attr:`~unimem_media.policy.MediaProbeLimits.timeout_seconds`;
    6. standard output is read back under
       :attr:`~unimem_media.policy.MediaProbeLimits.max_output_bytes` and decoded
       as UTF-8 strictly.

    The caller's stream is read and **not** closed: the port says the caller owns
    the handle, and this function closes only what it opened.

    A timeout kills and reaps the child before the exception leaves this
    function: :func:`subprocess.run` sends ``SIGKILL`` and then waits, so no
    orphan is left holding the descriptor.

    A nonzero exit is **not** treated as a verdict about the media. It can mean
    unreadable bytes, a refused protocol, a missing demuxer, a crash, or an
    out-of-memory kill, and this runner cannot tell those apart — so it reports
    that the probe did not happen rather than claiming anything about what it was
    given. Its wrapper must preserve that, and specifically must not turn it into
    an input error.

    Raises :class:`EngineInvocationError` for every failure, carrying a
    structured reason. Nothing broader is caught: a ``TypeError`` from a defect in
    this module is a bug, not a probe outcome.
    """
    arguments = build_arguments(invocation)
    limits = invocation.limits
    with _temporary_files() as (source, sink):
        try:
            stage(stream, source, chunk_size=limits.copy_chunk_size)
        except OSError as exc:
            raise EngineInvocationError(
                "the submitted media could not be staged for probing",
                reason=STAGING_ERROR,
                detail=exc.strerror or str(exc),
            ) from exc

        try:
            completed = subprocess.run(
                arguments,
                stdin=source,
                stdout=sink,
                stderr=subprocess.DEVNULL,
                timeout=limits.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineInvocationError(
                f"the media probe did not finish within {limits.timeout_seconds:.1f}s "
                f"and was killed",
                reason=TIMEOUT,
            ) from exc
        except OSError as exc:
            raise EngineInvocationError(
                f"the media probe {invocation.executable!r} could not be run",
                reason=LAUNCH_ERROR,
                detail=exc.strerror or str(exc),
            ) from exc

        if completed.returncode != 0:
            raise EngineInvocationError(
                f"the media probe exited with status {completed.returncode}",
                reason=NONZERO_EXIT,
                returncode=completed.returncode,
            )

        try:
            sink.seek(0)
            # One byte past the budget, so "too large" is distinguishable from
            # "exactly at the budget" without reading whatever the child actually
            # produced.
            printed = sink.read(limits.max_output_bytes + 1)
        except OSError as exc:
            raise EngineInvocationError(
                "the media probe's output could not be read back",
                reason=STAGING_ERROR,
                detail=exc.strerror or str(exc),
            ) from exc

    if len(printed) > limits.max_output_bytes:
        raise EngineInvocationError(
            f"the media probe printed more than its {limits.max_output_bytes} byte budget",
            reason=OUTPUT_TOO_LARGE,
        )
    try:
        return printed.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EngineInvocationError(
            "the media probe's output was not valid UTF-8",
            reason=INVALID_UTF8,
        ) from exc
