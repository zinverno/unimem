"""Proving the machine can actually probe, before the server accepts anything.

Two things are checked, in the order they can fail usefully: that the configured
``ffprobe`` can be launched and reports a recognizable version, and that the
build which answered supports the input protocol this adapter feeds its bytes
through. Both happen while the application is being assembled, so a deployment
that asked for media and cannot probe it never reaches a request.

**The second check is not ceremony.** :mod:`unimem_media.engine` hands the child
an inherited descriptor and names it ``fd:``; a build compiled without the
``fd`` protocol would accept that command line and fail on the *first capture* —
a server that starts, accepts audio at the door, and then answers 503 to every
one of them. FFmpeg builds are configurable and stripped-down ones exist, so
whether this particular executable has the protocol is a real question with a
cheap answer, and asking it at startup is the difference between a deployment
that refuses to start and one that silently cannot do its job.

**Nothing here repairs anything.** No package is installed, no binary is
downloaded, no ``apt`` is invoked, no cloud service is contacted, and no
alternative engine is substituted. A machine without ffprobe is told so.

These probes run the executable twice with fixed argument lists and read its
standard output. They are the only place this package inspects the engine rather
than using it, and — unlike the runtime path, which discards standard error
unread — they are allowed to read what the engine printed, because a version
banner and a protocol listing are answers to questions this module asked rather
than prose about somebody's file.
"""

import subprocess
from typing import Final

from unimem_media.errors import MediaPrerequisiteError
from unimem_media.policy import (
    FFPROBE_EXECUTABLE,
    INPUT_PROTOCOL,
    PREREQUISITE_TIMEOUT_SECONDS,
)

#: ``ffprobe -version`` prints ``ffprobe version <version> Copyright ...`` on its
#: first line. This is the word the version follows.
_VERSION_MARKER: Final = "version"

#: Headings in ``ffprobe -protocols`` output. Only the input list matters: this
#: adapter reads and never writes, and a build that could *write* ``fd`` but not
#: read it would pass a check that did not distinguish them.
_INPUT_HEADING: Final = "Input:"
_OUTPUT_HEADING: Final = "Output:"

#: What a message calls this capability, so an operator who typed ``--media`` is
#: never sent after a dependency something else needs.
CAPABILITY: Final = "media probing"

#: Named once, because three messages say it and a drifting copy would send
#: somebody to install the wrong thing.
_INSTALL_HINT: Final = (
    "Local media probing needs FFmpeg's ffprobe installed on this machine and "
    "reachable on PATH; this build does not install it."
)


def _run(executable: str, argument: str) -> str:
    """Run one fixed probe argument against the engine and return its stdout.

    A two-element argument list with ``shell=False``: there is no shell, no
    string to quote, and nothing in either element that came from anywhere but
    this module and the deployment's own configuration.
    """
    try:
        completed = subprocess.run(
            [executable, argument],
            capture_output=True,
            timeout=PREREQUISITE_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except OSError as exc:
        raise MediaPrerequisiteError(
            f"the media probe {executable!r} could not be run ({exc.strerror or exc}). "
            f"{_INSTALL_HINT}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaPrerequisiteError(
            f"the media probe {executable!r} did not answer {argument} within "
            f"{PREREQUISITE_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    if completed.returncode != 0:
        raise MediaPrerequisiteError(
            f"the media probe {executable!r} exited with status {completed.returncode} "
            f"for {argument}"
        )
    # Decoded permissively on purpose: a version banner and a protocol listing
    # are diagnostic text, and a startup gate must not fail because a build
    # stamped a non-UTF-8 byte into one. Engine *output* on the capture path is
    # decoded strictly, which is a different decision made in a different place.
    return completed.stdout.decode("utf-8", errors="replace")


def engine_version(executable: str = FFPROBE_EXECUTABLE) -> str:
    """The version the installed ffprobe reports for itself.

    Taken from the engine rather than assumed, because a constant written here
    would eventually be a lie. It is read for an operator's log line and for the
    sentence a failed gate prints — and for nothing else. **It is deliberately
    not passed to the probe and never reaches canonical metadata**: ADR-021 fixed
    that the probing engine is not part of what a container says, unlike an OCR
    engine, whose identity is part of what its output means.
    """
    banner = _run(executable, "-version").strip()
    first = banner.splitlines()[0] if banner else ""
    _, marker, rest = first.partition(f"{_VERSION_MARKER} ")
    reported = rest.split()[0] if marker and rest.split() else ""
    if not reported:
        raise MediaPrerequisiteError(
            f"the media probe {executable!r} did not report a version; its -version output "
            f"was not recognizable"
        )
    return reported


def input_protocols(executable: str = FFPROBE_EXECUTABLE) -> frozenset[str]:
    """Every input protocol the installed build can open.

    ``ffprobe -protocols`` prints a heading, an ``Input:`` section of indented
    protocol names, then an ``Output:`` section. Only the input names are
    collected: this adapter reads and never writes, so a build listing ``fd``
    under output alone would not be one that can serve a capture.
    """
    listed = _run(executable, "-protocols")
    collecting = False
    found: set[str] = set()
    for line in listed.splitlines():
        stripped = line.strip()
        if stripped == _INPUT_HEADING:
            collecting = True
            continue
        if stripped == _OUTPUT_HEADING:
            collecting = False
            continue
        if collecting and stripped and " " not in stripped:
            found.add(stripped)
    return frozenset(found)


def require_engine(executable: str = FFPROBE_EXECUTABLE) -> str:
    """Insist ffprobe runs and speaks the required protocol; return its version.

    The whole startup gate, in the order that produces the most useful message:

    1. the engine must run and report a version;
    2. that build must list :data:`~unimem_media.policy.INPUT_PROTOCOL` among the
       protocols it can open.

    Any failure is a :class:`~unimem_media.errors.MediaPrerequisiteError` and
    nothing is returned. Nothing is installed, downloaded, or worked around, and
    there is no fallback to a different input protocol: a deployment that would
    have to name a filesystem path to probe is not the deployment this adapter
    is, and quietly becoming it would put a caller-influenced string where this
    package promises there is none.
    """
    version = engine_version(executable)
    available = input_protocols(executable)
    if INPUT_PROTOCOL not in available:
        raise MediaPrerequisiteError(
            f"the media probe {executable!r} (version {version}) cannot open the "
            f"{INPUT_PROTOCOL!r} input protocol, which this adapter uses to hand it the "
            f"submitted bytes without naming a path. Install an FFmpeg build that supports "
            f"it; this build neither substitutes another protocol nor falls back to passing "
            f"a filesystem path."
        )
    return version


def describe_prerequisites(executable: str = FFPROBE_EXECUTABLE) -> str:
    """One human-readable line per prerequisite, for an operator or a CI log.

    Exercises the same probes the startup check uses, so a log line saying the
    engine is present is evidence about the code path that will run rather than
    about a separate shell command.
    """
    available = input_protocols(executable)
    return "\n".join(
        [
            f"engine: {executable} {engine_version(executable)}",
            f"input protocol required: {INPUT_PROTOCOL}",
            f"input protocol available: {INPUT_PROTOCOL in available}",
        ]
    )
