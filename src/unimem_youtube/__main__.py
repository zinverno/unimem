"""Thin CLI: capture then export, or export an existing capture without network."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from core.intake.errors import CaptureIntakeError
from core.persistence import CaptureRecordStoreError, ContentObjectStoreError
from core.processing.errors import ProcessingError
from core.storage import RawObjectStoreError
from unimem_youtube.errors import AcquisitionError, CapturePipelineError, ExportError
from unimem_youtube.export import export_markdown
from unimem_youtube.service import YoutubeCaptureService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture YouTube captions or render saved content."
    )
    actions = parser.add_subparsers(dest="action", required=True)
    for name, argument in (("capture", "url"), ("render", "capture_id")):
        action = actions.add_parser(name)
        action.add_argument(argument)
        action.add_argument("--data-dir", required=True, type=Path)
        action.add_argument("--output-dir", required=True, type=Path)
        if name == "capture":
            action.add_argument("--languages", nargs="+", default=["en"])
    args = parser.parse_args(argv)
    try:
        service = YoutubeCaptureService(args.data_dir)
        content = (
            service.capture(args.url, tuple(args.languages))
            if args.action == "capture"
            else service.read(args.capture_id)
        )
    except CapturePipelineError as exc:
        print(
            json.dumps(
                {
                    "status": "capture_incomplete",
                    "capture_id": exc.capture_id,
                    "code": exc.code,
                    "message": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 3
    except AcquisitionError as exc:
        print(
            json.dumps({"status": "acquisition_failed", "code": exc.code, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2
    except (
        CaptureIntakeError,
        CaptureRecordStoreError,
        ContentObjectStoreError,
        ProcessingError,
        RawObjectStoreError,
        ExportError,
        OSError,
    ):
        print(
            json.dumps(
                {
                    "status": "capture_or_read_failed",
                    "message": "Could not obtain a completed stored capture.",
                }
            ),
            file=sys.stderr,
        )
        return 3
    result = {
        "capture_id": content.source.capture_id,
        "content_id": content.id,
        "capture_status": "complete",
    }
    try:
        path = export_markdown(content, args.output_dir)
    except ExportError as exc:
        print(
            json.dumps(
                {
                    **result,
                    "export_status": "failed",
                    "message": str(exc),
                    "recovery": "Use render with this capture_id and another output-dir.",
                }
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps({**result, "export_status": "complete", "path": str(path.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
