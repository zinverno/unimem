"""Verify exact shipped allowlist, archive bytes, and absence of dev/test files."""

import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "manifest.json").read_text())
archive = root / "dist" / f"unimem-browser-{manifest['version']}-dev.zip"
files = {"manifest.json", "service-worker.js", "manage.html", "manage.js", "manage.css"}
files.update(f"lib/{p.name}" for p in (root / "lib").glob("*.js"))
unpacked = root / "dist" / "unpacked"
assert {p.relative_to(unpacked).as_posix() for p in unpacked.rglob("*") if p.is_file()} == files
with zipfile.ZipFile(archive) as package:
    assert set(package.namelist()) == files
    for name in files:
        assert package.read(name) == (root / name).read_bytes()
        assert (unpacked / name).read_bytes() == (root / name).read_bytes()
        assert package.getinfo(name).date_time == (2026, 1, 1, 0, 0, 0)
assert (
    archive.with_suffix(".zip.sha256").read_text().split()[0]
    == hashlib.sha256(archive.read_bytes()).hexdigest()
)
print(f"PASS: {len(files)} shipped files; archive matches sources")
