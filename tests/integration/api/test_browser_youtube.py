"""Real shipped browser JS -> protected B1 TCP + synthetic provider, no mock API."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tests.api_auth import TEST_TOKEN
from tests.integration.api.connector_support import (
    CONNECTOR_DIR,
    port_is_free,
    require_node,
    unavailable,
)
from tests.integration.api.test_youtube_delivery import count_captures, server, wait_for


def drive(phase: str, data: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(CONNECTOR_DIR / "scripts/integration.mjs"), phase, str(data / "browser.json")],
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"UNIMEM_TEST_TOKEN": TEST_TOKEN},
    )
    assert TEST_TOKEN not in result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def test_protected_browser_recovery_and_export_without_reacquisition(tmp_path: Path) -> None:
    require_node()
    if not port_is_free(8765):
        unavailable("port 8765 is already in use")
    with server(tmp_path, port=8765) as (client, _):
        submitted = drive("submit", tmp_path)
        wait_for(client, submitted["id"], {"complete"})
        assert submitted["posts"] == 5
        assert count_captures(tmp_path) == 3  # text, page, one YouTube
        assert (tmp_path / "acquisitions").read_text().splitlines() == [submitted["id"]]
    # Reopen the actual stores/server and a new Node process, with acquisition
    # now configured to fail: successful Markdown must come from saved content.
    with server(tmp_path, "timeout", port=8765):
        restored = drive("restore", tmp_path)
        assert restored["id"] == submitted["id"]
        assert restored["posts"] == 0
        assert restored["markdown"] is True
    assert count_captures(tmp_path) == 3
    assert (tmp_path / "acquisitions").read_text().splitlines() == [submitted["id"]]
