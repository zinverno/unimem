"""Build one immutable compatible kit from committed sources, using existing builders."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def archive(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as output:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            output.writestr(info, content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output must be a new directory; prior kits are never overwritten.")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        parser.error("Commit tracked changes first; the kit always builds the named source SHA.")
    sha = run("git", "rev-parse", "HEAD")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="unimem-build-") as work:
        source = Path(work) / "source"
        source.mkdir()
        tar = Path(work) / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(tar), sha], cwd=ROOT, check=True
        )
        with tarfile.open(tar) as stream:
            stream.extractall(source, filter="data")
        run("uv", "build", "--wheel", "--no-create-gitignore", "--out-dir", str(output), cwd=source)
        for component in ("browser-extension", "obsidian-plugin"):
            run("npm", "ci", "--prefix", f"clients/{component}", cwd=source)
            run("npm", "run", "build", "--prefix", f"clients/{component}", cwd=source)
        browser = source / "clients/browser-extension"
        connector = source / "clients/obsidian-plugin"
        browser_version = json.loads((browser / "manifest.json").read_text())["version"]
        connector_version = json.loads((connector / "manifest.json").read_text())["version"]
        browser_zip = f"unimem-browser-{browser_version}-dev.zip"
        shutil.copyfile(browser / "dist" / browser_zip, output / browser_zip)
        archive(
            output / f"unimem-connector-{connector_version}.zip",
            {
                "unimem-connector/" + name: (
                    connector / "dist/unimem-connector" / name
                ).read_bytes()
                for name in ("main.js", "manifest.json")
            },
        )
        tracked = run(
            "git", "ls-tree", "-r", "--name-only", sha, "clients/browser-extension"
        ).splitlines()
        archive(
            output / f"unimem-browser-{browser_version}-source.zip",
            {
                str(Path(name).relative_to("clients/browser-extension")): (
                    source / name
                ).read_bytes()
                for name in tracked
            },
        )
        for name in (
            "FIRST_RUN.md",
            "docs/MOZILLA_SIGNING.md",
            "packaging/unimem.service",
            "scripts/verify_signed.py",
        ):
            shutil.copyfile(source / name, output / Path(name).name)
        versions = {
            "source_sha": sha,
            "service": tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"],
            "browser": browser_version,
            "connector": connector_version,
            "delivery_protocols": ["1", "2", "3"],
            "addon_id": "unimem@zinverno.github.io",
            "signed_runtime": "BLOCKED until owner supplies matching Mozilla-signed XPI",
            "native_acceptance": "See separate evidence keyed to this kit's SHA256SUMS",
        }
        (output / "COMPATIBILITY.json").write_text(json.dumps(versions, indent=2) + "\n")
    sums = []
    for file in sorted(output.iterdir()):
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        sums.append(f"{digest}  {file.name}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(f"Kit: {output}\nSource: {sha}\nVerify: sha256sum -c SHA256SUMS")


if __name__ == "__main__":
    main()
