"""Opt-in live acceptance: one public URL, temporary stores, two CLI processes.

Run with an explicit URL. This never prints caption text, request URLs with
tokens, raw server errors or stack traces, and it never bypasses a block.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--languages", nargs="+", default=["en"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="unimem-youtube-live-") as directory:
        root = Path(directory)
        captured = subprocess.run(
            [
                sys.executable,
                "-m",
                "unimem_youtube",
                "capture",
                args.url,
                "--languages",
                *args.languages,
                "--data-dir",
                str(root / "data"),
                "--output-dir",
                str(root / "capture"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if captured.returncode:
            try:
                code = json.loads(captured.stderr).get("code", "capture_or_export_failed")
            except ValueError:
                code = "invalid_cli_output"
            blocked = code in {
                "request_blocked",
                "access_required",
                "network_error",
                "timeout",
                "redirect_denied",
                "dependency_missing",
            }
            print(json.dumps({"live_acceptance": "BLOCKED" if blocked else "FAIL", "code": code}))
            return 2 if blocked else 1
        result = json.loads(captured.stdout)
        rendered = subprocess.run(
            [
                sys.executable,
                "-m",
                "unimem_youtube",
                "render",
                result["capture_id"],
                "--data-dir",
                str(root / "data"),
                "--output-dir",
                str(root / "render"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if rendered.returncode:
            print(json.dumps({"live_acceptance": "FAIL", "code": "offline_render_failed"}))
            return 1
        second = json.loads(rendered.stdout)
        same = Path(result["path"]).read_bytes() == Path(second["path"]).read_bytes()
        print(
            json.dumps(
                {
                    "live_acceptance": "PASS" if same else "FAIL",
                    "capture_id": result["capture_id"],
                    "content_id": result["content_id"],
                    "identical_after_restart": same,
                }
            )
        )
        return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
