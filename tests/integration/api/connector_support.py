"""Prerequisites and plumbing shared by the cross-language acceptance tests.

Test-only, and deliberately small. Two acceptance suites now drive the real
browser connector against a real Uvicorn server — the selection lost-response
test in :mod:`test_completed_capture_replay`, and the whole-page tests in
:mod:`test_browser_whole_page_capture` — and both need the same four things: a
``node`` runtime, the connector's one fixed port, a way to run a driver script,
and a way to read the database the server actually wrote.

The **skip policy** is the reason this module exists rather than the plumbing.
Those prerequisites cannot be assumed of every machine: on a laptop running only
the Python suite, a missing ``node`` is a skip, because nothing is gained by
failing a developer's test run over a runtime their change does not touch. In CI
it must be the opposite, because a test that silently skips proves nothing and a
green run would be a lie. :func:`unavailable` is that decision, in one place, so
that every prerequisite in every acceptance suite obeys it and none can be added
later that skips unconditionally.

Nothing here is production infrastructure. It builds no abstraction the
connector or the server could use, and imports nothing that is not already a
test dependency.
"""

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, NoReturn

import pytest

#: The repository root, for locating the connector's sources.
REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: The connector's own directory. Its modules are imported by absolute path, so
#: a driver script can live in a scratch directory with no copy beside it.
CONNECTOR_DIR: Final = REPO_ROOT / "clients" / "browser-extension"

#: The connector's documented destination. Its origin is a constant it will not
#: take from anywhere, so a test that drives the real client has to meet it here.
CONNECTOR_PORT: Final = 8765

#: Set this in any environment where a cross-language acceptance test is not
#: allowed to quietly not happen. It turns "run this if the machine can" into
#: "run this, or fail" — see :func:`unavailable`.
REQUIRE_CONNECTOR_INTEGRATION_ENV: Final = "UNIMEM_REQUIRE_CONNECTOR_INTEGRATION"


def connector_module(*parts: str) -> str:
    """A ``file:`` URI for one of the connector's real modules.

    Driver scripts import through this rather than through a relative path, so
    what they exercise is unambiguously the shipped file in this repository.
    """
    return (CONNECTOR_DIR.joinpath(*parts)).as_uri()


def connector_integration_required() -> bool:
    """Must the cross-language acceptance tests actually run here?

    Unset, empty, and ``"0"`` all mean no; anything else means yes. The variable
    is read at call time rather than at import, so a test can exercise both
    policies in one session without a subprocess.
    """
    return os.environ.get(REQUIRE_CONNECTOR_INTEGRATION_ENV, "") not in ("", "0")


def unavailable(reason: str) -> NoReturn:
    """Refuse to run, as a skip or as a failure depending on the environment.

    A skipped acceptance test and a passing one are indistinguishable in a green
    CI summary, which is exactly the failure mode the required mode removes.
    """
    if connector_integration_required():
        pytest.fail(
            f"{reason}. {REQUIRE_CONNECTOR_INTEGRATION_ENV} is set, so the "
            "cross-language acceptance test must run rather than be skipped."
        )
    pytest.skip(f"{reason} (set {REQUIRE_CONNECTOR_INTEGRATION_ENV}=1 to require it)")


def node_is_available() -> bool:
    """Can this machine run the connector's own JavaScript?"""
    try:
        found = subprocess.run(["node", "--version"], capture_output=True, check=False)
    except OSError:
        return False
    return found.returncode == 0


def require_node() -> None:
    """Refuse a suite up front if the connector cannot be run at all."""
    if not node_is_available():
        unavailable("node is not available")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@contextmanager
def serve(data_dir: Path) -> Iterator[str]:
    """The real application, on a real socket, at the connector's own address.

    ``TestClient`` is not enough for these suites: the client under test is
    JavaScript in another process, and it will only ever talk to
    ``http://127.0.0.1:8765`` because its destination is a constant it refuses to
    take from anywhere else. Meeting it there is part of what they prove.
    """
    import uvicorn

    from unimem_api import build_local_app

    if not port_is_free(CONNECTOR_PORT):
        unavailable(f"port {CONNECTOR_PORT} is already in use")

    config = uvicorn.Config(
        build_local_app(data_dir), host="127.0.0.1", port=CONNECTOR_PORT, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Never a skip, in either mode. Being unable to start the server is not a
    # missing prerequisite — it is the thing under test failing to come up.
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - only on a broken host
            server.should_exit = True
            thread.join(timeout=10)
            pytest.fail(f"the API did not start on 127.0.0.1:{CONNECTOR_PORT}")
        if not thread.is_alive():  # pragma: no cover - only on a broken host
            pytest.fail(f"the API process died while starting on port {CONNECTOR_PORT}")
        time.sleep(0.02)

    try:
        yield f"http://127.0.0.1:{CONNECTOR_PORT}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def run_driver(script: str, arguments: list[str], workspace: Path) -> dict[str, Any]:
    """Run one Node driver script and read the JSON document it prints.

    The script is written to ``workspace`` and invoked with ``node``; a non-zero
    exit is a failure carrying the child's stderr, because a driver that crashed
    proves nothing and its silence must not read as an empty result.
    """
    driver = workspace / "drive-connector.mjs"
    driver.write_text(script, encoding="utf-8")
    run = subprocess.run(
        ["node", str(driver), *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ | {"NO_COLOR": "1"},
    )
    assert run.returncode == 0, run.stderr
    printed: dict[str, Any] = json.loads(run.stdout)
    return printed


def feedback_for(result: dict[str, Any]) -> str:
    """The badge the connector's feedback mapper would show for this result.

    Run through the connector's own module rather than restated here, so a test
    cannot claim ``OK`` for a result the extension would badge ``!``.
    """
    # `node -e` drops its own script from argv, so the two arguments after `--`
    # land at indices 1 and 2 rather than at 2 and 3.
    script = (
        "const { feedbackFor } = await import(process.argv[1]);"
        "console.log(feedbackFor(JSON.parse(process.argv[2])).badge);"
    )
    run = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            "--",
            connector_module("lib", "feedback.js"),
            json.dumps(result),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return run.stdout.strip()


def read_json(url: str) -> dict[str, Any]:
    """Read one JSON document over the live socket, with no client library.

    ``TestClient`` would run the app in this process instead of talking to the
    one the connector talked to, which is the whole point of these assertions.
    """
    with urllib.request.urlopen(url, timeout=10) as response:
        parsed: dict[str, Any] = json.loads(response.read())
    return parsed


def row_count(database: Path, table: str, capture_id: str, column: str) -> int:
    """Count rows for one capture, read straight from SQLite.

    Going around both stores is the point. "There is exactly one capture" is a
    claim about the database, and asking the store that wrote it would be asking
    the same code whether it did the right thing.
    """
    with sqlite3.connect(database) as connection:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (capture_id,)
        ).fetchone()
    connection.close()
    return int(found[0])


def raw_files(data_dir: Path) -> list[Path]:
    return sorted(path for path in (data_dir / "raw").rglob("*") if path.is_file())
