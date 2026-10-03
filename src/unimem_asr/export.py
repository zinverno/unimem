"""Offline Markdown, derived entirely from persisted canonical content."""

import json
import re

from core.contracts import ContentObject
from unimem_asr.policy import AsrError
from unimem_asr.result import Transcript


class AudioMarkdownRenderer:
    name = "audio-transcription-markdown"
    version = "1"
    media_type = "text/markdown"

    def render(self, content: ContentObject) -> str:
        facts = content.metadata.get("audio_transcription")
        if not isinstance(facts, dict):
            raise AsrError("markdown_unavailable")
        try:
            Transcript.model_validate(
                {
                    **{key: facts[key] for key in Transcript.model_fields if key != "segments"},
                    "segments": [
                        {"text": cue.text, "start": cue.temporal.start, "end": cue.temporal.end}
                        for cue in content.segments
                        if cue.temporal is not None
                    ],
                }
            )
            if any(
                cue.temporal is None or cue.position != i for i, cue in enumerate(content.segments)
            ):
                raise ValueError
        except (ValueError, KeyError):
            raise AsrError("markdown_unavailable") from None
        metadata = {
            "export_format": f"{self.name}/{self.version}",
            "source": "local audio upload",
            "capture_id": content.source.capture_id,
            "content_id": content.id,
            "original_sha256": content.original.sha256,
            **{
                key: facts[key]
                for key in (
                    "operation_id",
                    "captured_at",
                    "container",
                    "codec",
                    "duration",
                    "selected_language",
                    "detected_language",
                    "language_probability",
                    "outcome",
                    "engine",
                    "model",
                    "model_revision",
                    "model_sha256",
                    "versions",
                    "parameters",
                )
            },
        }
        lines = [
            "---",
            *[
                f"{key}: {json.dumps(value, ensure_ascii=False, allow_nan=False)}"
                for key, value in metadata.items()
            ],
            "---",
            "",
            "# Аудиозапись — автоматическая расшифровка",
            "",
            "Автоматическая расшифровка; возможны ошибки.",
            "",
            "Исходное аудио сохранено в UniMem. Obsidian получает только текст; "
            "аудиофайл в vault не переносился.",
            "",
        ]
        if facts["outcome"] == "no_speech":
            lines.extend(
                ["Механизм распознавания не обнаружил речь. Это не доказывает её отсутствие.", ""]
            )
        for cue in content.segments:
            assert cue.temporal is not None
            assert cue.text is not None
            lines.append(f"[{cue.temporal.start:.2f} - {cue.temporal.end:.2f} sec]")
            fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", cue.text)), default=0))
            lines.extend(["", f"{fence}text", cue.text, fence, ""])
        return "\n".join(lines)
