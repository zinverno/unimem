"""Explicit downloads for the fixed evaluation; never imported by production."""

import argparse
import json
import subprocess
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from asr_quality import EVIDENCE, sha


def prepare_audio(directory):
    """Download individual assets only; reject changed corpus revisions/bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    for sample in json.loads((EVIDENCE / "samples.json").read_text()):
        target = directory / sample["file"]
        if target.exists():
            assert sha(target) == sample["sha256"]
            continue
        if sample["id"] == "silence":
            with wave.open(str(target), "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(bytes(sample["frames"] * 2))
        else:
            query = urllib.parse.urlencode({k: sample[k] for k in ("dataset", "config", "split")})
            with urllib.request.urlopen(
                "https://datasets-server.huggingface.co/first-rows?" + query, timeout=30
            ) as response:
                rows = json.load(response)["rows"]
            row = next(row["row"] for row in rows if row["row_idx"] == sample["row"])
            url = row["audio"][0]["src"]
            assert f"/--/{sample['revision']}/--/" in url, "dataset revision changed"
            reference = row.get("transcription", row.get("text", row.get("text_no_preprocessing")))
            # Historical Russian has both normalized and original annotation fields.
            assert sample["reference"] in (reference, row.get("text_no_preprocessing"))
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(2 * 1024**2 + 1)
            assert len(data) <= 2 * 1024**2
            source = directory / (sample["id"] + ".source")
            source.write_bytes(data)
            if "source_sha256" in sample:
                assert sha(source) == sample["source_sha256"]
            # The historic file is already the exact accepted PCM WAV.
            if sample["id"] == "ru-literary":
                target.write_bytes(data)
            else:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-i",
                        str(source),
                        "-c:a",
                        "pcm_s16le",
                        "-map_metadata",
                        "-1",
                        "-fflags",
                        "+bitexact",
                        "-flags:a",
                        "+bitexact",
                        str(target),
                    ],
                    check=True,
                    timeout=15,
                )
        assert sha(target) == sample["sha256"], "input changed; do not silently evaluate new bytes"
        print(target.name, sample["sha256"])


def prepare_small(directory):
    from huggingface_hub import snapshot_download

    manifest = json.loads((EVIDENCE / "models.json").read_text())["small"]
    print("Explicit preparation:", manifest["model"], manifest["revision"], "~486 MB; CPU/int8")
    snapshot_download(
        manifest["model"],
        revision=manifest["revision"],
        local_dir=directory,
        allow_patterns=list(manifest["sha256"]),
        token=False,
        max_workers=1,
    )
    assert {name: sha(directory / name) for name in manifest["sha256"]} == manifest["sha256"]
    (directory / "unimem-model.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["audio", "small"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    (prepare_audio if args.mode == "audio" else prepare_small)(args.directory)
