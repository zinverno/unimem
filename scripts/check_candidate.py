"""Clean wheel install, saved setup, TCP restart and unchanged data; no model downloads."""

import argparse
import hashlib
import http.client
import json
import os
import subprocess
import time
import venv
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    kit, work = args.kit.resolve(), args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    for line in (kit / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest
    wheel = next(kit.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "unimem_local/__main__.py" in archive.namelist()
        assert not any(
            ".gguf" in n or "api.token" in n or "node_modules" in n for n in archive.namelist()
        )
    venv.EnvBuilder(with_pip=True).create(work / "app")
    python = str(work / "app/bin/python")
    subprocess.run(
        [python, "-m", "pip", "install", str(wheel)], check=True, stdout=subprocess.DEVNULL
    )
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "VIRTUAL_ENV"}}
    env.update(XDG_CONFIG_HOME=str(work / "config"), XDG_DATA_HOME=str(work / "persistent"))
    launch = [str(work / "app/bin/unimem")]

    def command(*cmd: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        p = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=30)
        assert (p.returncode == 0) == ok, p.stderr
        return p

    command(*launch, "setup")
    pairing = json.loads(command(*launch, "destination").stdout)
    data = work / "persistent/unimem"
    token = (data / "api.token").read_text().strip()
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    command(*launch, "setup")
    assert pairing["receiver_token"] not in command(*launch, "destination").stdout
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before
    diagnostic = command(*launch, "diagnose").stdout
    assert token not in diagnostic
    assert pairing["receiver_token"] not in diagnostic
    # Import guard runs in the installed environment, outside the checkout.
    command(
        python,
        "-c",
        "import sys; from unimem_local.__main__ import main; "
        "main(['diagnose']); assert not any(n.startswith(('unimem_api', 'unimem_asr', "
        "'unimem_vision', 'faster_whisper', 'ctranslate2', 'PIL')) for n in sys.modules)",
    )

    def request(method: str, path: str, body: object = None) -> tuple[int, object]:
        conn = http.client.HTTPConnection("127.0.0.1", 8765, timeout=3)
        try:
            conn.request(
                method,
                path,
                body=json.dumps(body) if body is not None else None,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    capture = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "clients/browser-extension/tests/fixtures/browser-envelopes.json"
        ).read_text()
    )
    # The checked-in contract fixture carries a synthetic original, no personal material.
    envelope = capture[0]["envelope"]
    content = None
    for restart in range(2):
        with (work / f"server-{restart}.log").open("w") as log:
            server = subprocess.Popen([*launch, "start"], cwd=work, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert server.poll() is None, "Installed service exited"
                    try:
                        if request("GET", "/health")[0] == 200:
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("Installed service did not become ready")
                assert "owns this data directory" in command(*launch, "start", ok=False).stderr
                if restart == 0:
                    status, result = request("POST", "/v1/captures", envelope)
                    assert status == 201, result
                    content = request("GET", f"/v1/captures/{envelope['id']}/content")
                else:
                    assert request("GET", f"/v1/captures/{envelope['id']}/content") == content
                    assert request("POST", "/v1/captures", envelope)[0] == 200
                assert request("GET", "/v1/destinations")[0] == 200
            finally:
                server.terminate()
                server.wait(timeout=20)
        assert token not in (work / f"server-{restart}.log").read_text()
    config = work / "config/unimem/config.toml"
    config.write_text(config.read_text() + f'\naudio_model = "{work / "absent-model"}"\n')
    assert "ASR unavailable" in command(*launch, "start", ok=False).stderr
    assert not (work / "absent-model").exists()
    result = {
        "source_sha": json.loads((kit / "COMPATIBILITY.json").read_text())["source_sha"],
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "clean_wheel_install": "PASS",
        "repeat_setup": "PASS",
        "api_restart": "PASS",
        "read_only_diagnosis": "PASS",
        "missing_model_no_download": "PASS",
        "signed_runtime": "BLOCKED",
    }
    (work / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
