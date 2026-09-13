"""A fake ``tesseract`` on disk, for the tests that must not run the real one.

The adapter's contract with the engine is a *command line*: a fixed argument
list, an image on stdin, text on stdout, a status code, and a wall-clock bound.
All five of those are observable from a shell script, and a shell script can do
things a real engine will not do on demand — exit nonzero, hang past a timeout,
print to stderr, emit bytes that are not UTF-8, or report only one language.

So this builds one. It is deliberately not a Python stub of the adapter: what is
under test includes ``subprocess`` itself — that the arguments are a list, that no
shell interprets them, that stdout and stderr stay separate, and that a timed-out
child is killed — and a monkeypatched ``run`` would prove none of that.
"""

import stat
from pathlib import Path
from typing import Final

#: What ``tesseract --version`` prints on its first line, for a fake that has one.
FAKE_VERSION: Final = "5.3.4"

#: Where a fake records the argument vector it was invoked with, one per line.
ARGV_RECORD: Final = "argv.txt"

#: Where a fake records the bytes it read from stdin.
STDIN_RECORD: Final = "stdin.bin"

#: A file a fake writes *after* its sleep finishes. Its absence after a timeout is
#: how a test proves the child was killed rather than merely abandoned.
COMPLETION_MARKER: Final = "finished.txt"


def write_fake_engine(
    directory: Path,
    *,
    name: str = "tesseract",
    version: str | None = FAKE_VERSION,
    languages: tuple[str, ...] = ("eng", "osd", "rus"),
    version_exit: int = 0,
    list_exit: int = 0,
    recognize_exit: int = 0,
    stdout: str = "recognized text\n",
    stdout_bytes: bytes | None = None,
    stderr: str = "",
    sleep: float = 0.0,
) -> Path:
    """Write an executable fake engine and return its path.

    ``directory`` also becomes the fake's record directory: it writes
    :data:`ARGV_RECORD` and :data:`STDIN_RECORD` there on every recognition call,
    so a test reads what the adapter actually sent rather than what it meant to.
    """
    executable = directory / name
    banner = "" if version is None else f"tesseract {version}"
    listing = "\n".join(languages)
    body = _SCRIPT.format(
        banner=banner,
        version_exit=version_exit,
        listing=listing,
        language_count=len(languages),
        list_exit=list_exit,
        record_dir=directory,
        argv_record=ARGV_RECORD,
        stdin_record=STDIN_RECORD,
        marker=COMPLETION_MARKER,
        sleep=sleep,
        stderr=stderr,
        recognize_exit=recognize_exit,
    )
    executable.write_text(body, encoding="utf-8")
    if stdout_bytes is not None:
        (directory / "stdout.bin").write_bytes(stdout_bytes)
    else:
        (directory / "stdout.bin").write_bytes(stdout.encode("utf-8"))
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return executable


def recorded_argv(directory: Path) -> list[str]:
    """The argument vector the fake was last invoked with, excluding argv[0]."""
    return (directory / ARGV_RECORD).read_text(encoding="utf-8").splitlines()


def recorded_stdin(directory: Path) -> bytes:
    """The bytes the fake last read from its standard input."""
    return (directory / STDIN_RECORD).read_bytes()


def was_invoked(directory: Path) -> bool:
    return (directory / ARGV_RECORD).exists()


def completed(directory: Path) -> bool:
    """Did a sleeping fake survive long enough to finish?"""
    return (directory / COMPLETION_MARKER).exists()


#: ``/bin/sh`` rather than Python, so nothing about the child's behaviour depends
#: on this project's interpreter or its installed packages.
_SCRIPT: Final = """#!/bin/sh
set -e
RECORD="{record_dir}"

if [ "$1" = "--version" ]; then
    if [ -n "{banner}" ]; then printf '%s\\n' "{banner}"; fi
    printf 'leptonica-1.82.0\\n'
    exit {version_exit}
fi

if [ "$1" = "--list-langs" ]; then
    printf 'List of available languages in "/fake/tessdata" ({language_count}):\\n'
    if [ -n "{listing}" ]; then printf '%s\\n' "{listing}"; fi
    exit {list_exit}
fi

for argument in "$@"; do
    printf '%s\\n' "$argument" >> "$RECORD/{argv_record}.partial"
done
mv "$RECORD/{argv_record}.partial" "$RECORD/{argv_record}"
cat > "$RECORD/{stdin_record}"
sleep {sleep}
printf '%s' "{stderr}" >&2
cat "$RECORD/stdout.bin"
: > "$RECORD/{marker}"
exit {recognize_exit}
"""
