"""Proving the machine can actually recognize, before the server accepts anything.

Three things are checked, in the order they can fail usefully: the optional
Python packages, the Tesseract executable, and the language data files. All of
it happens while the application is being assembled, so a deployment that asked
for OCR and cannot perform it never reaches a request.

**The three are not one gate, and the split is load-bearing.** PDF OCR needs all
three; direct-image OCR needs only the last two. :func:`require_rasterizer` is
therefore reached from the PDF factory alone, and an ``--image-ocr`` startup never
imports ``pypdfium2`` or ``Pillow`` — which is what makes "image OCR requires no
optional Python extra" a fact about the code path rather than a claim in a
README. The engine probes below serve both, and say which capability asked so
that an image-only deployment is never told it needs a PDF rasterizer.

**Nothing here repairs anything.** No package is installed, no model is
downloaded, no ``apt`` is invoked, no cloud service is contacted, and the
language set is never narrowed to whatever happens to be present. Silently
falling back to English on a machine missing the Russian data would mean two
deployments running the same code recognize Russian scans differently, and the
one that got it wrong would say ``COMPLETE`` either way.

These probes run the executable twice with fixed argument lists and read its
standard output. They are the only place this package inspects the engine rather
than using it.
"""

import subprocess
from typing import Final

from unimem_ocr.errors import OcrPrerequisiteError
from unimem_ocr.policy import RECOGNITION_LANGUAGES, TESSERACT_EXECUTABLE

#: How long a probe may take. Generous for a version banner, and finite so a
#: broken executable cannot hang startup forever.
PROBE_TIMEOUT_SECONDS: Final = 30.0

#: The optional packages local recognition needs, named here for the message a
#: missing one produces. Both are genuinely checked by :func:`require_rasterizer`;
#: this tuple is the human-readable half and the imports there are the real test.
OPTIONAL_PACKAGES: Final = ("pypdfium2", "Pillow")

#: The extra that installs them.
OPTIONAL_EXTRA: Final = "capture-core[ocr]"

#: How the engine probes name the capability that asked for them.
#:
#: It defaults to ``"PDF"`` everywhere so that every message this module produced
#: before direct-image OCR existed is byte-for-byte what it produces now. The
#: image path passes its own word, because telling an operator who typed
#: ``--image-ocr`` that "Local PDF OCR needs Tesseract" would send them looking
#: for a rasterizer this capability never loads.
PDF_CAPABILITY: Final = "PDF"

#: What :func:`require_engine` is called for by the direct-image startup gate.
IMAGE_CAPABILITY: Final = "image"


def _run(executable: str, argument: str, *, capability: str = PDF_CAPABILITY) -> str:
    """Run one fixed probe argument against the engine and return its stdout.

    A two-element argument list with ``shell=False``: there is no shell, no
    string to quote, and nothing in either element that came from anywhere but
    this module and the deployment's own configuration.
    """
    try:
        completed = subprocess.run(
            [executable, argument],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except OSError as exc:
        raise OcrPrerequisiteError(
            f"the OCR engine {executable!r} could not be run ({exc.strerror or exc}). Local "
            f"{capability} OCR needs Tesseract installed on this machine and reachable on "
            f"PATH; this build does not install it."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrPrerequisiteError(
            f"the OCR engine {executable!r} did not answer {argument} within "
            f"{PROBE_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    if completed.returncode != 0:
        raise OcrPrerequisiteError(
            f"the OCR engine {executable!r} exited with status {completed.returncode} for "
            f"{argument}"
        )
    # Tesseract prints its version banner on stdout and its language list on
    # stdout too, with the "List of available languages in ..." header. Decoded
    # permissively on purpose: a version banner is diagnostic text, and a probe
    # must not fail because a build stamped a non-UTF-8 byte into it. Recognized
    # *text* is decoded strictly, which is a different decision made in a
    # different place.
    return completed.stdout.decode("utf-8", errors="replace")


def engine_version(
    executable: str = TESSERACT_EXECUTABLE, *, capability: str = PDF_CAPABILITY
) -> str:
    """The version the installed engine reports for itself.

    The first line of ``tesseract --version`` is ``tesseract <version>``; the
    lines after it name the imaging libraries it was linked against. Only the
    version is taken, and it is taken from the engine rather than assumed,
    because it is recorded on every recognized segment's content object and a
    constant written here would eventually be a lie.
    """
    banner = _run(executable, "--version", capability=capability).strip()
    first = banner.splitlines()[0] if banner else ""
    _, _, version = first.partition(" ")
    reported = version.strip()
    if not reported:
        raise OcrPrerequisiteError(
            f"the OCR engine {executable!r} did not report a version; its --version output "
            f"was not recognizable"
        )
    return reported


def available_languages(
    executable: str = TESSERACT_EXECUTABLE, *, capability: str = PDF_CAPABILITY
) -> frozenset[str]:
    """Every language data file the installed engine can load.

    ``tesseract --list-langs`` prints a header line naming the ``tessdata``
    directory and then one language code per line. The header is dropped by
    taking only lines with no whitespace in them, which is what a bare language
    code looks like and what the header is not.
    """
    listed = _run(executable, "--list-langs", capability=capability)
    return frozenset(
        line.strip() for line in listed.splitlines() if line.strip() and " " not in line.strip()
    )


def require_rasterizer() -> None:
    """Insist **both** optional rasterization packages are importable, or explain.

    The check *is* the real import rather than a spec lookup, because a spec can
    resolve for a package that then fails to load its native library.

    Two imports, and neither is redundant. Importing
    :mod:`unimem_ocr.tesseract` proves PDFium is loadable, but it does **not**
    prove Pillow is: the adapter names no PIL symbol, and ``pypdfium2`` defers
    loading Pillow until :meth:`PdfBitmap.to_pil` is actually called. So a machine
    with ``pypdfium2`` installed and ``Pillow`` missing passed this gate and then
    failed on the first scanned page — a server that starts and then cannot do the
    one thing it was started for, which is exactly what this function exists to
    prevent. ``import PIL.Image`` is therefore performed explicitly, naming the
    submodule the adapter's encode path really needs.

    Both imports happen **here**, inside the OCR-only prerequisite path, and
    nowhere in ordinary ``core`` or ``unimem_api`` startup: this function is
    reached only from :func:`unimem_ocr.build_tesseract_ocr`, which is reached only
    from ``--pdf-ocr``. A default deployment never executes either line.

    Nothing is installed and there is no fallback. A missing package becomes an
    :class:`~unimem_ocr.errors.OcrPrerequisiteError` naming what is absent and the
    extra that provides it, with the original ``ImportError`` kept as the cause.
    """
    try:
        # Pillow first only because the import sorter says so; neither import is
        # conditional on the other and either failing is the same refusal.
        import PIL.Image  # noqa: F401 - pypdfium2 loads Pillow lazily, so prove it here

        import unimem_ocr.tesseract  # noqa: F401 - imported for its side effect
    except ImportError as exc:
        missing = getattr(exc, "name", None) or "a required package"
        raise OcrPrerequisiteError(
            f"local PDF OCR needs the optional rasterization packages "
            f"{', '.join(OPTIONAL_PACKAGES)}, and {missing!r} is not importable. Install them "
            f"with: pip install '{OPTIONAL_EXTRA}'"
        ) from exc


def require_engine(
    executable: str = TESSERACT_EXECUTABLE, *, capability: str = PDF_CAPABILITY
) -> str:
    """Insist the engine exists and carries every language, and return its version.

    Both language packs, not one and not "at least one". A deployment that
    installed Tesseract and only English is a deployment that would silently
    stop reading Russian scans, and it is told so here rather than discovered
    later by whoever reads the results.

    **This is the whole of the direct-image prerequisite.** Image OCR calls it
    with ``capability="image"`` and calls nothing else — no
    :func:`require_rasterizer`, no ``pypdfium2``, no ``Pillow``. When a deployment
    enables both capabilities this probe simply runs twice, which costs two fixed
    subprocess calls at startup and is deliberately not cached: a module-level
    memo would make two independent gates secretly depend on each other and on
    the order they happen to run in.
    """
    version = engine_version(executable, capability=capability)
    present = available_languages(executable, capability=capability)
    missing = [language for language in RECOGNITION_LANGUAGES if language not in present]
    if missing:
        raise OcrPrerequisiteError(
            f"the OCR engine {executable!r} (version {version}) is missing language data for "
            f"{', '.join(missing)}. Local {capability} OCR recognizes "
            f"{'+'.join(RECOGNITION_LANGUAGES)} and this build neither downloads language data "
            f"nor falls back to the languages that happen to be installed."
        )
    return version


def describe_prerequisites(executable: str = TESSERACT_EXECUTABLE) -> str:
    """One human-readable line per prerequisite, for an operator or a CI log.

    Exercises the same probes the startup check uses, so a log line saying the
    engine is present is evidence about the code path that will run rather than
    about a separate shell command.
    """
    from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

    return "\n".join(
        [
            f"rasterizer: pypdfium2 {PYPDFIUM_INFO.version} (pdfium {PDFIUM_INFO.version})",
            f"engine: {executable} {engine_version(executable)}",
            f"languages required: {'+'.join(RECOGNITION_LANGUAGES)}",
            f"languages available: {' '.join(sorted(available_languages(executable)))}",
        ]
    )
