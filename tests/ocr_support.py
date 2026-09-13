"""Prerequisites, skip policy, and plumbing shared by the PDF OCR tests.

Local OCR has prerequisites no machine can be assumed to have: two native Python
wheels from an optional extra, a Tesseract executable, and two language data
files. This module is the one place that decides what their absence means, and it
decides it the same way :mod:`tests.integration.api.connector_support` does for
the browser acceptance tests:

* on a developer's machine running only the baseline suite, a missing
  prerequisite is a **skip** — nothing is gained by failing someone's test run
  over a system package their change does not touch;
* wherever ``UNIMEM_REQUIRE_PDF_OCR_INTEGRATION`` is set, every one of those
  reasons is a **failure** instead, because a skipped acceptance test and a
  passing one are indistinguishable in a green CI summary.

That is also why no module in this repository uses ``pytest.importorskip`` for
the OCR packages. ``importorskip`` skips unconditionally, at import time, with no
way to ask whether skipping is allowed here — which is exactly the hole the
required mode exists to close. :func:`require_rasterizer` and
:func:`require_engine` are the replacement, and they obey the policy.

Nothing here is production infrastructure. It builds nothing the application
could use and imports nothing that is not already a test dependency.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Final, NoReturn

import pytest

#: Set this in any environment where a PDF OCR test is not allowed to quietly not
#: happen. It turns "run this if the machine can" into "run this, or fail".
REQUIRE_PDF_OCR_ENV: Final = "UNIMEM_REQUIRE_PDF_OCR_INTEGRATION"

#: The languages the product commits to. Restated here rather than imported, so
#: that a test asserting "both language packs are required" fails if the policy
#: constant changes without anyone revisiting this list.
REQUIRED_LANGUAGES: Final = ("eng", "rus")

#: How long a server subprocess gets to answer ``/health`` before the test fails.
#: Never a skip: a server that will not come up is the thing under test failing.
SERVER_START_TIMEOUT: Final = 60.0


def pdf_ocr_integration_required() -> bool:
    """Must the OCR tests actually run here?

    Unset, empty, and ``"0"`` all mean no; anything else means yes. Read at call
    time rather than at import, so one session can exercise both policies.
    """
    return os.environ.get(REQUIRE_PDF_OCR_ENV, "") not in ("", "0")


def unavailable(reason: str, *, module_level: bool = False) -> NoReturn:
    """Refuse to run, as a skip or as a failure depending on the environment."""
    if pdf_ocr_integration_required():
        pytest.fail(
            f"{reason}. {REQUIRE_PDF_OCR_ENV} is set, so the local PDF OCR test "
            f"must run rather than be skipped."
        )
    pytest.skip(
        f"{reason} (set {REQUIRE_PDF_OCR_ENV}=1 to require it)",
        allow_module_level=module_level,
    )


def rasterizer_is_available() -> bool:
    """Are the optional rasterization packages importable?"""
    try:
        import PIL.Image  # noqa: F401 - imported for its availability only
        import pypdfium2  # noqa: F401 - imported for its availability only
    except ImportError:
        return False
    return True


def require_rasterizer(*, module_level: bool = True) -> None:
    """Refuse a suite up front if the optional OCR extra is not installed."""
    if not rasterizer_is_available():
        unavailable(
            "the optional OCR extra is not installed (pip install 'capture-core[ocr]')",
            module_level=module_level,
        )


def engine_report() -> tuple[str, frozenset[str]] | None:
    """``(version, languages)`` for the installed engine, or ``None`` if absent."""
    from unimem_ocr.errors import OcrPrerequisiteError
    from unimem_ocr.prerequisites import available_languages, engine_version

    try:
        return engine_version(), available_languages()
    except OcrPrerequisiteError:
        # The engine is absent, unrunnable, or answering nonsense. Which of those
        # it is matters to whoever installs it and not to the skip decision, and
        # the probe's own message says so when the required mode turns this into a
        # failure.
        return None


def require_engine(*, module_level: bool = True) -> None:
    """Refuse a suite up front unless Tesseract and both language packs are present."""
    report = engine_report()
    if report is None:
        unavailable("tesseract is not installed or not on PATH", module_level=module_level)
    _, languages = report
    missing = [language for language in REQUIRED_LANGUAGES if language not in languages]
    if missing:
        unavailable(
            f"tesseract has no language data for {', '.join(missing)}",
            module_level=module_level,
        )


def require_local_ocr(*, module_level: bool = True) -> None:
    """Both halves, in the order whose message is most useful."""
    require_rasterizer(module_level=module_level)
    require_engine(module_level=module_level)


def free_port() -> int:
    """A port the kernel says is free right now.

    Dynamically allocated rather than fixed: these tests run their own server and
    have no client with a hard-coded destination, so there is no reason to collide
    with whatever else is on the machine — or with a parallel run of themselves.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        chosen: int = probe.getsockname()[1]
    return chosen


def read_json(url: str) -> dict[str, Any]:
    """Read one JSON document over a live socket, with no client library."""
    with urllib.request.urlopen(url, timeout=30) as response:
        parsed: dict[str, Any] = json.loads(response.read())
    return parsed


@contextmanager
def serve_process(
    data_dir: Path,
    *,
    pdf_ocr: bool = True,
    port: int | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    """Run ``python -m unimem_api`` as a real process and yield its base URL.

    A **subprocess**, not a thread and not ``TestClient``. Two things need that:
    ``--pdf-ocr`` is a command-line flag, so exercising it means exercising a
    command line; and "stop the server, start it again over the same directory"
    is only a durability claim if the first process is genuinely gone.

    Failing to come up is always a failure, never a skip, in either mode: a
    server that will not start is not a missing prerequisite.
    """
    chosen = port if port is not None else free_port()
    command = [
        sys.executable,
        "-m",
        "unimem_api",
        "--data-dir",
        str(data_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(chosen),
    ]
    if pdf_ocr:
        command.append("--pdf-ocr")

    server = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ | (env or {}),
    )
    base = f"http://127.0.0.1:{chosen}"
    try:
        _await_health(server, base)
        yield base
    finally:
        server.terminate()
        try:
            server.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a broken host
            server.kill()
            server.communicate()


def _await_health(server: subprocess.Popen[str], base: str) -> None:
    """Block until ``/health`` answers, or fail saying what the server printed."""
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    while True:
        if server.poll() is not None:
            out, err = server.communicate()
            pytest.fail(
                f"the server exited with status {server.returncode} before serving "
                f"{base}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
            )
        try:
            if read_json(f"{base}/health").get("status") == "ok":
                return
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            pass
        if time.monotonic() > deadline:  # pragma: no cover - only on a broken host
            server.kill()
            out, err = server.communicate()
            pytest.fail(
                f"the server did not answer {base}/health within "
                f"{SERVER_START_TIMEOUT:.0f}s\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
            )
        time.sleep(0.05)
