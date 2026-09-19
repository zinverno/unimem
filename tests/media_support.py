"""Prerequisites, skip policy, and plumbing shared by the native media tests.

Local media probing has one prerequisite no machine can be assumed to have: a
system ``ffprobe``. The native suites additionally need ``ffmpeg`` — **only** to
*create* the small test files they probe, because no binary fixture is committed
to this repository and nothing is fetched at test time. Nothing under ``src/``
ever invokes ffmpeg, and these tests are where that stays visible: the
production path sees only the bytes ffmpeg produced, exactly as it would see a
submitted upload.

This module is the one place that decides what a missing prerequisite means, and
it decides it the way :mod:`tests.ocr_support` and
:mod:`tests.integration.api.connector_support` already do:

* on a developer's machine running only the baseline suite, a missing
  prerequisite is a **skip** — nothing is gained by failing someone's test run
  over a system package their change does not touch;
* wherever :data:`REQUIRE_MEDIA_ENV` is set, every one of those reasons is a
  **failure** instead, because a skipped acceptance test and a passing one are
  indistinguishable in a green CI summary.

It is deliberately self-contained rather than sharing :mod:`tests.ocr_support`'s
server plumbing. The two capabilities are independent deployments with different
prerequisites and different required-modes, and a module that served both would
make one capability's suite able to break the other's.

Nothing here is production infrastructure. It builds nothing the application
could use.
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

#: Set this in any environment where a media test is not allowed to quietly not
#: happen. It turns "run this if the machine can" into "run this, or fail".
REQUIRE_MEDIA_ENV: Final = "UNIMEM_REQUIRE_MEDIA_INTEGRATION"

#: The probe the production path uses. Restated rather than imported so that a
#: change to the adapter's constant is noticed here rather than silently
#: followed.
FFPROBE: Final = "ffprobe"

#: The encoder these tests use to *build* fixtures, and which production never
#: invokes.
FFMPEG: Final = "ffmpeg"

#: How long a fixture build or a probe may take before the test fails.
TOOL_TIMEOUT: Final = 120.0

#: How long a server subprocess gets to answer ``/health`` before the test fails.
#: Never a skip: a server that will not come up is the thing under test failing.
SERVER_START_TIMEOUT: Final = 60.0


def media_integration_required() -> bool:
    """Must the native media tests actually run here?

    Unset, empty, and ``"0"`` all mean no; anything else means yes. Read at call
    time rather than at import, so one session can exercise both policies.
    """
    return os.environ.get(REQUIRE_MEDIA_ENV, "") not in ("", "0")


def unavailable(reason: str, *, module_level: bool = False) -> NoReturn:
    """Refuse to run, as a skip or as a failure depending on the environment."""
    if media_integration_required():
        pytest.fail(
            f"{reason}. {REQUIRE_MEDIA_ENV} is set, so the local media test must run "
            f"rather than be skipped."
        )
    pytest.skip(
        f"{reason} (set {REQUIRE_MEDIA_ENV}=1 to require it)",
        allow_module_level=module_level,
    )


def probe_report() -> str | None:
    """The installed ffprobe's version, or ``None`` when it cannot be used.

    Asked through the project's own prerequisite surface rather than by running a
    shell command, so a green run is evidence about the code path the adapter
    takes — including the ``fd`` protocol check, which a bare ``ffprobe -version``
    would not exercise.
    """
    from unimem_media import MediaPrerequisiteError, require_engine

    try:
        return require_engine()
    except MediaPrerequisiteError:
        # Which of absent, unrunnable, or missing-the-protocol it is matters to
        # whoever installs it and not to the skip decision. The probe's own
        # message says so when the required mode turns this into a failure.
        return None


def encoder_is_available() -> bool:
    """Can this machine *build* the fixtures? A test-only question."""
    try:
        completed = subprocess.run(
            [FFMPEG, "-version"],
            capture_output=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def require_probe(*, module_level: bool = True) -> None:
    """Refuse a suite up front unless a usable ffprobe is installed."""
    if probe_report() is None:
        unavailable(
            "ffprobe is not installed, not on PATH, or cannot open the 'fd' input protocol",
            module_level=module_level,
        )


def require_encoder(*, module_level: bool = True) -> None:
    """Refuse a suite up front unless ffmpeg can build the fixtures."""
    if not encoder_is_available():
        unavailable(
            "ffmpeg is not installed or not on PATH, so the test media cannot be built",
            module_level=module_level,
        )


def require_media_tooling(*, module_level: bool = True) -> None:
    """Both halves, in the order whose message is most useful."""
    require_probe(module_level=module_level)
    require_encoder(module_level=module_level)


def free_port() -> int:
    """A port the kernel says is free right now."""
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
    media: bool = True,
    port: int | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    """Run ``python -m unimem_api`` as a real process and yield its base URL.

    A **subprocess**, not a thread and not ``TestClient``. Two things need that:
    ``--media`` is a command-line flag, so exercising it means exercising a
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
    if media:
        command.append("--media")

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


def _await_health(server: "subprocess.Popen[str]", base: str) -> None:
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


def start_without_ffprobe(data_dir: Path, empty_dir: Path) -> "subprocess.CompletedProcess[str]":
    """Try to start a media deployment on a machine where ffprobe is absent.

    Absence is arranged the honest way: the child is given a ``PATH`` holding one
    empty directory, so the bare executable name the adapter looks up resolves to
    nothing. Nothing is monkeypatched, no constant is overridden, and no private
    seam is used — there is deliberately no command-line option for the engine
    name, and inventing one for a test would be testing something this build does
    not ship.

    The server is a no-op, so a start that *succeeded* would return cleanly and
    the test would see that rather than hanging on a bound port.
    """
    empty_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "import sys;"
        "import unimem_api.__main__ as cli;"
        "sys.exit(cli.main(sys.argv[1:], server=lambda app, *, host, port: None))"
    )
    return subprocess.run(
        [sys.executable, "-c", script, "--data-dir", str(data_dir), "--media"],
        capture_output=True,
        text=True,
        timeout=TOOL_TIMEOUT,
        check=False,
        env=os.environ | {"PATH": str(empty_dir)},
    )
