"""Explicit preparation and offline render; recognition belongs to the worker."""

import argparse
from pathlib import Path

from unimem_asr.export import AudioMarkdownRenderer
from unimem_asr.model import prepare_model, require_engine
from unimem_asr.policy import MODEL_ID, MODEL_REVISION, AsrError
from unimem_asr.service import AudioCaptureService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--model-dir", required=True, type=Path)
    prepare.add_argument("--download", action="store_true")
    render = commands.add_parser("render")
    render.add_argument("--data-dir", required=True, type=Path)
    render.add_argument("--capture-id", required=True)
    render.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(f"Model: {MODEL_ID}@{MODEL_REVISION}; MIT; CPU/int8, 2 threads.", flush=True)
            print(
                "Download approximately 150 MB; allow 1 GiB free disk and 3 GiB address space. "
                "No CUDA. Requires pip install '.[asr]'.",
                flush=True,
            )
            if args.download:
                require_engine()
                prepare_model(args.model_dir)
                print("Local model prepared. Recognition will not download weights.")
            else:
                print("Review requirements, then repeat with --download to fetch the model.")
        else:
            text = AudioMarkdownRenderer().render(
                AudioCaptureService(args.data_dir).read(args.capture_id)
            )
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(text)
    except (AsrError, OSError) as exc:
        raise SystemExit(exc.code if isinstance(exc, AsrError) else "export_failed") from None


if __name__ == "__main__":
    main()
