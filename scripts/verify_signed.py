"""Compare extension payloads; Mozilla signature trust is checked by Zen, not here."""

import argparse
import zipfile
from pathlib import Path


def payload(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        return {
            name: archive.read(name)
            for name in names
            if not name.endswith("/") and not name.startswith("META-INF/")
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dev_zip", type=Path)
    parser.add_argument("signed_xpi", type=Path)
    args = parser.parse_args()
    if payload(args.dev_zip) != payload(args.signed_xpi):
        parser.error("Payload differs: do not treat this XPI as the accepted kit.")
    with zipfile.ZipFile(args.signed_xpi) as archive:
        if not any(n.startswith("META-INF/") for n in archive.namelist()):
            parser.error("No signature metadata; this is not a signed artifact.")
    print("Payload matches. Signature validity and full restart still require Zen acceptance.")


if __name__ == "__main__":
    main()
