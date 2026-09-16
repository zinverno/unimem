"""Running a local Tesseract, once, safely. No domain, no format, no opinion.

Both recognition adapters in this package end in the same five lines of
security-sensitive work: build a fixed argument list, hand the child some bytes
on stdin, read its stdout, bound it with a wall clock, and refuse anything that
is not a clean success. Two independent copies of that is a real hazard rather
than a tidiness complaint — the timeout that kills and reaps, the strict decode,
the suppressed stderr, and the "nonzero is never a verdict" rule all have to stay
right in both, forever — so it lives here once and each adapter wraps it.

**This module is deliberately ignorant.** It is stdlib-only: it imports no
rasterizer, no imaging library, and nothing from ``core``. It does not know what
a page is, what an image is, or what a capture is, and its errors say nothing
about any of them. The domain wording belongs to the wrappers, which is what lets
the PDF adapter keep saying "page 4" and the image adapter say nothing of the
kind.

The dependency direction matters and is not an accident: :mod:`unimem_ocr.image`
must not import :mod:`unimem_ocr.tesseract`, because that module imports PDFium
at module scope and would drag a rasterizer into a deployment that asked only for
image OCR. Both import *this*, and this imports neither.

**Failures are structured.** :class:`EngineInvocationError` carries a
:attr:`~EngineInvocationError.reason` and, where one exists, the child's exit
status — so a wrapper branches on attributes rather than on prose. Nothing here
is written for a client: the messages are for a server log, and no wrapper passes
one to the wire.
"""

import subprocess
from dataclasses import dataclass
from typing import Final, Literal

#: The engine could not be started at all: it is missing, not executable, or the
#: operating system refused for some other reason.
LAUNCH_ERROR: Final = "launch_error"

#: The engine was started and did not finish inside its wall-clock bound. The
#: child has been killed and reaped before the error is raised.
TIMEOUT: Final = "timeout"

#: The engine ran to completion and reported failure.
NONZERO_EXIT: Final = "nonzero_exit"

#: The engine succeeded and printed something that is not valid UTF-8.
INVALID_UTF8: Final = "invalid_utf8"

#: Every way one invocation can fail to produce trustworthy text.
InvocationReason = Literal["launch_error", "timeout", "nonzero_exit", "invalid_utf8"]


class EngineInvocationError(Exception):
    """One engine invocation did not produce trustworthy text.

    An *internal* type. It never crosses into ``core``, never reaches an HTTP
    mapping, and never reaches a client: each adapter catches it at its own
    boundary and re-raises the domain error its port documents, so that the PDF
    path keeps raising :class:`~core.processing.ocr.PdfOcrExecutionError` and the
    image path raises
    :class:`~core.processing.image_recognition.ImageOcrExecutionError`.

    :attr:`reason` and :attr:`returncode` exist so those wrappers can decide what
    happened without reading :func:`str`. The message is a server-log sentence and
    carries no engine output: the child's standard error is captured and dropped
    unexamined, because it is another program's prose, it is not a stable
    interface, and on some builds it names paths on this machine.
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
        #: The operating system's own explanation, for :data:`LAUNCH_ERROR`.
        #: Kept for a wrapper's log message, never for a client.
        self.detail = detail


@dataclass(frozen=True, slots=True)
class EngineInvocation:
    """Everything one recognition run needs from its caller.

    A plain value, so an adapter's policy is visible at the call site rather than
    hidden in keyword defaults here. Every field is supplied by the adapter from
    its own constants; nothing is read from an environment variable, a
    configuration file, or a request.

    ``dpi`` is the one genuinely optional knob, and it exists because the two
    callers honestly differ. The PDF adapter rasterizes a page at a known
    resolution and tells the engine so, which is a true statement about work that
    actually happened. The image adapter hands over a submitted file that this
    build never rasterized and whose physical density it never read, so it passes
    ``None`` and **no ``--dpi`` argument is emitted at all** — claiming a
    resolution there would assert a fact nobody observed and could override one
    the file itself carries.
    """

    executable: str
    languages: str
    oem: int
    psm: int
    timeout: float
    dpi: int | None = None


def build_arguments(invocation: EngineInvocation) -> list[str]:
    """The exact argument vector for one invocation.

    Separate from :func:`run_engine` so a test can assert on the command line
    without starting a process, and so that "the PDF argv did not change" is a
    claim about a pure function.

    ``stdin`` and ``stdout`` are Tesseract's own literals for "read the image
    from standard input" and "write the text to standard output", so no path is
    named on either side. Every element comes from this package's constants by
    way of the caller: no filename, URL, document metadata, capture title, digest
    or request field ever appears here, as an argument, as an option value, or as
    a path.

    The order is fixed and is part of the contract with both adapters: executable,
    ``stdin``, ``stdout``, ``-l``, ``--oem``, ``--psm``, then ``--dpi`` if and only
    if one was supplied.
    """
    arguments = [
        invocation.executable,
        "stdin",
        "stdout",
        "-l",
        invocation.languages,
        "--oem",
        str(invocation.oem),
        "--psm",
        str(invocation.psm),
    ]
    if invocation.dpi is not None:
        arguments += ["--dpi", str(invocation.dpi)]
    return arguments


def run_engine(image: bytes, invocation: EngineInvocation) -> str:
    """Run the engine over one image and return exactly what it printed.

    ``shell=False`` and a *list* of arguments: no shell string is constructed, so
    there is nothing to quote and nothing to interpret. The image travels in on
    the child's stdin and the recognized text comes back on its stdout, so there
    is no temporary file and therefore no temporary path to collide, leak, or be
    guessed.

    Standard error is captured separately and never mixed into the returned text.
    Standard output is decoded as UTF-8 **strictly** and handed back unchanged —
    every byte the engine produced, whitespace and line endings included. Nothing
    is trimmed here; deciding what counts as blank belongs to canonical policy,
    not to a process runner.

    A timeout kills and reaps the child before the exception leaves this function:
    :func:`subprocess.run` sends ``SIGKILL`` and then waits on the process, so no
    orphan is left holding the pipe.

    A nonzero exit is **not** treated as a verdict about the image. It can mean a
    corrupt input, a missing data file, a build that crashed, or an out-of-memory
    kill, and this runner cannot tell those apart — so it reports that recognition
    did not happen rather than claiming anything about what it was given. Its
    wrappers must preserve that, and neither of them may turn it into an input
    error.

    Raises :class:`EngineInvocationError` for every failure, carrying a structured
    reason. Nothing broader is caught: a ``TypeError`` from a defect in this
    module is a bug, not a recognition outcome.
    """
    arguments = build_arguments(invocation)
    try:
        completed = subprocess.run(
            arguments,
            input=image,
            capture_output=True,
            timeout=invocation.timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineInvocationError(
            f"the OCR engine did not finish within {invocation.timeout:.1f}s and was killed",
            reason=TIMEOUT,
        ) from exc
    except OSError as exc:
        raise EngineInvocationError(
            f"the OCR engine {invocation.executable!r} could not be run",
            reason=LAUNCH_ERROR,
            detail=exc.strerror or str(exc),
        ) from exc

    if completed.returncode != 0:
        raise EngineInvocationError(
            f"the OCR engine exited with status {completed.returncode}",
            reason=NONZERO_EXIT,
            returncode=completed.returncode,
        )
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EngineInvocationError(
            "the OCR engine's output was not valid UTF-8",
            reason=INVALID_UTF8,
        ) from exc
