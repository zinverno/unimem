"""Pure caption Markdown projection and exclusive output delivery. ADR-024."""

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from core.contracts import ContentObject
from unimem_youtube.artifact import CaptionTrack
from unimem_youtube.errors import ExportError
from unimem_youtube.source import VIDEO_ID, source_url


class CaptionMarkdownRenderer:
    name = "youtube-caption-markdown"
    version = "1"
    media_type = "text/markdown"

    def render(self, content: ContentObject) -> str:
        facts = content.metadata.get("youtube_captions")
        if not isinstance(facts, dict):
            raise ExportError("This content is not a normalized YouTube caption capture.")
        video_id = facts.get("video_id")
        captured_at = facts.get("captured_at")
        if (
            not isinstance(video_id, str)
            or not VIDEO_ID.fullmatch(video_id)
            or content.source.url != source_url(video_id)
            or not isinstance(captured_at, str)
        ):
            raise ExportError("Stored caption source metadata is invalid.")
        try:
            track = CaptionTrack.model_validate(facts.get("track"))
            if datetime.fromisoformat(captured_at).tzinfo is None:
                raise ValueError
        except ValueError:
            raise ExportError("Stored caption track or capture time is invalid.") from None
        frontmatter = {
            "export_format": f"{self.name}/{self.version}",
            "source": content.source.url,
            "captured_at": captured_at,
            "capture_id": content.source.capture_id,
            "content_id": content.id,
            "video_id": video_id,
            "language": track.language,
            "language_code": track.language_code,
            "track_origin": "youtube_generated" if track.is_generated else "youtube_manual",
            "track_is_translatable": track.is_translatable,
            "retrieval_library": facts.get("retrieval_library"),
            "retrieval_version": facts.get("retrieval_version"),
            "unimem_audio_analysis": False,
            "unimem_visual_analysis": False,
        }
        # JSON string/scalar syntax is a YAML subset; quotes/control characters
        # and Unicode cannot break out into another frontmatter key or document.
        lines = (
            ["---"]
            + [
                f"{key}: {json.dumps(value, ensure_ascii=True, allow_nan=False)}"
                for key, value in frontmatter.items()
            ]
            + [
                "---",
                "",
                f"# YouTube captions: {video_id}",
                "",
                f"Source: <{content.source.url}>",
                "",
                "External captions supplied by YouTube. "
                "UniMem did not analyse the audio or images.",
                "The original video file was not downloaded. "
                "This heading is a technical identifier.",
                "",
            ]
        )
        for segment in content.segments:
            temporal = segment.temporal
            if temporal is not None and temporal.start is not None:
                start = temporal.start
                if not math.isfinite(start) or (
                    temporal.end is not None and not math.isfinite(temporal.end)
                ):
                    raise ExportError("Stored caption timing is invalid.")
                label = f"{start}s"
                if temporal.end is not None:
                    label += f" - {temporal.end}s"
                lines.append(f"[{label}]({content.source.url}&t={math.floor(start)}s)")
            else:
                lines.append("Time unavailable")
            text = segment.text
            if text is None:
                blank = segment.metadata.get("caption_text")
                if not isinstance(blank, str):
                    raise ExportError("Stored caption text is missing.")
                text = blank
            fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
            lines.extend(["", f"{fence}text", text, fence, ""])
        return "\n".join(lines)


def export_markdown(content: ContentObject, output_dir: Path) -> Path:
    """Publish a complete file atomically, without overwriting an existing file."""
    rendered = CaptionMarkdownRenderer().render(content)
    # IDs are opaque and may not be paths. A digest keeps even imported IDs
    # within the explicitly selected directory and makes re-render conflict clear.
    identity = hashlib.sha256(content.source.capture_id.encode()).hexdigest()
    path = output_dir / f"youtube-captions-{identity}.md"
    temporary: Path | None = None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_dir,
            prefix=".unimem-",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(rendered)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)
    except FileExistsError:
        raise ExportError("Output already exists; choose a different output directory.") from None
    except OSError:
        raise ExportError("Could not write Markdown to the requested output directory.") from None
    finally:
        if temporary is not None:
            # Cleanup cannot invalidate a published file or hide the original
            # export error. On failure the temp stays in the explicit output_dir.
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return path
