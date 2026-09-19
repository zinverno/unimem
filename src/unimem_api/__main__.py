"""``python -m unimem_api`` — the local capture server, made runnable.

::

    python -m unimem_api --data-dir ./data

Six options and no more: where the data lives, what to bind, and which of the
three optional local capabilities this deployment has — recognizing scanned PDF
pages, recognizing text in staged images, and probing the structure of audio and
video. There is no config file, environment lookup, settings framework, profile,
service manager, systemd unit, Docker image, or reload mode. Starting the server
is one command and stopping it is Ctrl-C.

**``--pdf-ocr`` and ``--image-ocr`` are the whole of the OCR configuration
surface, and they are independent.** All four combinations are valid deployments;
neither flag implies, enables, or configures the other. With neither, the server
is byte-for-byte the deployment it was: the default
:class:`~core.processing.PdfProcessor` and
:class:`~core.processing.ImageProcessor` are registered, a PDF carrying no
embedded text is refused exactly as before, an image is stored and described
without being interpreted, no optional package is imported, and no engine is
probed or executed.

With ``--pdf-ocr`` this module builds the concrete PDFium/Tesseract adapter; with
``--image-ocr`` it builds the direct-image one. Either way it *validates that
capability's prerequisites before the socket is bound* and hands the port to
:func:`~unimem_api.wiring.build_local_app`, which registers the OCR-enabled
processor in the default one's place.

**Their prerequisites are not the same, and the CLI does not pretend otherwise.**
PDF recognition needs the optional ``ocr`` extra — a rasterizer and an imaging
library — plus a system Tesseract with the ``eng`` and ``rus`` data. Image
recognition hands the submitted bytes to the engine and decodes nothing, so it
needs the executable and the two language files and no Python extra at all. A
deployment that wants only image OCR installs no native wheel.

**``--media`` is a third, independent capability, and it differs from both in
what it switches on.** The two OCR flags choose *which* processor handles a
capture this build already accepts; ``--media`` decides whether audio and video
are accepted at all. Without it the server is byte-for-byte the deployment it
was: intake refuses ``AUDIO`` and ``VIDEO`` at the door, no media processor is
registered, :mod:`unimem_media` is never imported, and no ``ffprobe`` is probed
or executed. With it, this module builds the concrete
:class:`~unimem_media.ffprobe.FfprobeMediaProbe`, *validates that capability's
prerequisites before the socket is bound*, and hands the port to
:func:`~unimem_api.wiring.build_local_app`, which derives both intake's
``media_enabled`` and the media processor pair from it.

Its prerequisite is a system ``ffprobe`` and **no Python extra at all** — there
is deliberately no ``[media]`` extra, because the whole adapter is standard
library plus ``core`` and the one thing that is genuinely needed is a program
``pip`` cannot install. What ``--media`` buys is structural: container family,
declared duration, and the streams a container declares. It is not transcription,
not segmentation, not thumbnails or keyframes, not extracted audio, and not
``ffmpeg`` — nothing under ``src/`` ever invokes that.

Constructing the adapters here rather than in :mod:`unimem_api.wiring` is
deliberate. This is the delivery layer — the module that already knows about
argv, uvicorn, and exit codes — so it is the right place for the imports that may
need native libraries on the machine, and it keeps the composition root free of
them. A default installation never executes either import at all.

**The default bind is 127.0.0.1, and that is a security decision rather than a
convenience.** This phase has no authentication, no authorization, no API keys,
and no TLS: every caller that can reach the port can submit captures and read
back everything stored. Loopback is the only interface on which that is an
acceptable posture, so it is the default, and a ``--host`` that widens it is an
explicit choice made by whoever types it.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import uvicorn
from fastapi import FastAPI

from core.processing.image_recognition import ImageOcr
from core.processing.media_probe import MediaProbe
from core.processing.ocr import PdfPageOcr
from unimem_api.wiring import build_local_app

#: Loopback. See the module docstring — this is not a placeholder.
DEFAULT_HOST: Final = "127.0.0.1"

#: A high port, unlikely to collide with something already on the machine.
DEFAULT_PORT: Final = 8765


class Server(Protocol):
    """What :func:`main` needs from a server: take this app and run it."""

    def __call__(self, app: FastAPI, *, host: str, port: int) -> None: ...


@dataclass(frozen=True)
class Options:
    """Everything the command line decides."""

    data_dir: Path
    host: str
    port: int
    pdf_ocr: bool
    image_ocr: bool
    media: bool


def build_parser() -> argparse.ArgumentParser:
    """The command line, in full."""
    parser = argparse.ArgumentParser(
        prog="python -m unimem_api",
        description="Run the local UniMem capture API.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="directory holding the raw object store and the SQLite database; created if missing",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            f"interface to bind (default: {DEFAULT_HOST}). This server has no "
            f"authentication — do not expose it to an untrusted network."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to bind (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--pdf-ocr",
        action="store_true",
        help=(
            "recognize PDF pages that carry no embedded text, using local "
            "Tesseract. Requires the optional 'ocr' extra and a system Tesseract "
            "with the eng and rus language data; startup fails if any of those is "
            "missing. Embedded text is always preferred and is never re-recognized. "
            "Without this flag a PDF with no embedded text is refused, as before."
        ),
    )
    parser.add_argument(
        "--image-ocr",
        action="store_true",
        help=(
            "recognize text in staged PNG and JPEG images, using local Tesseract. "
            "Requires a system Tesseract with the eng and rus language data, and "
            "no Python extra: this path hands the submitted bytes to the engine "
            "and decodes nothing itself. Startup fails if the engine or either "
            "language pack is missing. Independent of --pdf-ocr. An image too "
            "large for this build's recognition budget is still captured, with "
            "the skip recorded; without this flag an image is captured and "
            "described but never interpreted, as before."
        ),
    )
    parser.add_argument(
        "--media",
        action="store_true",
        help=(
            "accept audio and video captures and describe their container "
            "structure, using a local system ffprobe. Requires FFmpeg's ffprobe "
            "on PATH, built with the 'fd' input protocol, and no Python extra: "
            "the adapter is standard library only. Startup fails if ffprobe is "
            "missing or cannot open that protocol. Independent of --pdf-ocr and "
            "--image-ocr. This reads container structure only — no transcription, "
            "no segments, no thumbnails, no extracted audio, and no ffmpeg. "
            "Without this flag audio and video captures are refused at the door, "
            "as before."
        ),
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Turn an argument vector into the options, or exit with usage."""
    namespace = build_parser().parse_args(argv)
    return Options(
        data_dir=namespace.data_dir,
        host=namespace.host,
        port=namespace.port,
        pdf_ocr=namespace.pdf_ocr,
        image_ocr=namespace.image_ocr,
        media=namespace.media,
    )


def serve(app: FastAPI, *, host: str, port: int) -> None:
    """Hand the app to uvicorn and block until it stops.

    Programmatic, with the app object already built — not an import string, which
    would make uvicorn construct the application itself and take the composition
    decision away from :mod:`unimem_api.wiring`. No reload, no worker count, no
    lifespan hooks, no logging configuration: whatever uvicorn does by default is
    what this phase wants.
    """
    uvicorn.run(app, host=host, port=port)


def build_pdf_ocr() -> PdfPageOcr:
    """Build the local recognizer, or exit with a sentence saying why not.

    The import is here, inside the function, and that placement is the optional
    dependency boundary: a default start never runs this line, so a machine with
    no rasterizer installed has nothing to fail at. ``unimem_ocr`` itself is pure
    Python and ships with this distribution; it is
    :func:`unimem_ocr.build_tesseract_ocr` that imports the native packages,
    probes the executable, and insists on both language data files.

    A missing prerequisite becomes a ``SystemExit`` carrying the explanation the
    prerequisite check wrote. Three things this deliberately does not do: it does
    not disable OCR and start anyway, which would silently hand a scanned-document
    deployment the build that refuses scans; it does not fall back to English
    alone, which would silently change what Russian documents are remembered as
    saying; and it does not install software, download language data, or contact a
    service to make up the difference.
    """
    from unimem_ocr import OcrPrerequisiteError, build_tesseract_ocr

    try:
        return build_tesseract_ocr()
    except OcrPrerequisiteError as exc:
        raise SystemExit(f"--pdf-ocr was requested but local OCR is unavailable: {exc}") from exc


def build_image_ocr() -> ImageOcr:
    """Build the local image recognizer, or exit with a sentence saying why not.

    The same shape as :func:`build_pdf_ocr` and deliberately not the same gate.
    :func:`unimem_ocr.build_tesseract_image_ocr` probes the executable and insists
    on both language data files — and stops there. It does not call
    ``require_rasterizer``, so ``pypdfium2`` and ``Pillow`` are never imported by
    this path and a machine without them starts an image-OCR deployment
    perfectly well.

    The import is inside the function for the same reason the PDF one is: a
    default start never runs this line. Here it costs nothing native either way,
    which is the point — but the rule stays uniform.

    A missing prerequisite becomes a ``SystemExit`` carrying the explanation the
    prerequisite check wrote. It does not disable image OCR and start anyway,
    which would silently hand a deployment that asked for recognition the build
    that never interprets an image; it does not fall back to whichever language
    happens to be installed; and it installs nothing.
    """
    from unimem_ocr import OcrPrerequisiteError, build_tesseract_image_ocr

    try:
        return build_tesseract_image_ocr()
    except OcrPrerequisiteError as exc:
        raise SystemExit(
            f"--image-ocr was requested but local image OCR is unavailable: {exc}"
        ) from exc


def build_media_probe() -> MediaProbe:
    """Build the local media probe, or exit with a sentence saying why not.

    The same shape as the two OCR builders and, again, deliberately not the same
    gate. :func:`unimem_media.build_ffprobe_media_probe` proves the configured
    ``ffprobe`` can be launched and reports a version, and then proves that the
    build which answered can open the ``fd`` input protocol the adapter hands it
    bytes through. The second half matters because a build without that protocol
    would accept the command line and fail on the *first capture*, which is
    exactly the "starts, then cannot do its job" outcome a startup gate exists to
    prevent.

    The import is inside the function for the reason the OCR ones are: a default
    start never runs this line, so a machine with no FFmpeg installed has nothing
    to fail at, and ``unimem_media`` is genuinely not imported by a deployment
    that did not ask for it. Nothing native is loaded either way — the package is
    standard library plus ``core`` — and it ships with this distribution, because
    there is no Python extra to install and nothing ``pip`` could supply.

    A missing prerequisite becomes a ``SystemExit`` carrying the explanation the
    prerequisite check wrote. It does not disable media and start anyway, which
    would hand a deployment that asked for audio and video the build that refuses
    both at the door; it does not fall back to naming a filesystem path so that
    some other protocol would do; and it installs nothing, downloads nothing, and
    contacts no service.
    """
    from unimem_media import MediaPrerequisiteError, build_ffprobe_media_probe

    try:
        return build_ffprobe_media_probe()
    except MediaPrerequisiteError as exc:
        raise SystemExit(
            f"--media was requested but local media probing is unavailable: {exc}"
        ) from exc


def main(
    argv: Sequence[str] | None = None,
    *,
    server: Server = serve,
) -> int:
    """Parse, wire, and serve.

    ``server`` is a parameter so the argument handling and the composition can be
    exercised without binding a TCP port. It is the only seam, and it exists for
    testability rather than for configuration — nothing reads it from anywhere.

    Each capability that was asked for is built *before*
    :func:`~unimem_api.wiring.build_local_app` runs — Python evaluates the
    arguments first — so a deployment whose prerequisites are missing exits before
    a data directory is created, let alone a port bound. The three gates are
    independent and each runs when its own flag is given, which means an engine
    may be probed more than once in a session. That costs a few fixed-argument
    subprocess calls at startup and is deliberately not cached: a shared memo
    would make each capability's gate depend on whether another happened to run
    first.
    """
    options = parse_args(argv)
    app = build_local_app(
        options.data_dir,
        pdf_ocr=build_pdf_ocr() if options.pdf_ocr else None,
        image_ocr=build_image_ocr() if options.image_ocr else None,
        media_probe=build_media_probe() if options.media else None,
    )
    server(app, host=options.host, port=options.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m unimem_api`
    raise SystemExit(main())
