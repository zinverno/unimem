# ruff: noqa: RUF001 -- Russian prose and time units.
"""Deterministic projection of saved VIDEO evidence; no decoder or model calls."""

import hashlib
import json

from core.contracts import AssetRole, ContentObject, ContentType, SegmentType
from core.storage import RawObjectStore
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_export import literal
from unimem_api.obsidian_contract import VideoAttachment
from unimem_video.policy import VideoError


def video_attachments(content: ContentObject) -> tuple[tuple[VideoAttachment, str], ...]:
    if content.type is not ContentType.VIDEO or "video_notes" not in content.metadata:
        raise VideoError("invalid_video_result")
    result = []
    for asset in content.assets:
        if asset.role is not AssetRole.KEYFRAME:
            continue
        facts = asset.metadata
        name = hashlib.sha256((content.source.capture_id + ":" + asset.id).encode()).hexdigest()
        manifest = VideoAttachment.model_validate(
            {
                "asset_id": asset.id,
                "mime_type": asset.mime_type,
                "sha256": asset.sha256,
                "size_bytes": facts.get("size_bytes"),
                "width": facts.get("width"),
                "height": facts.get("height"),
                "relative_name": f"unimem-{name}.png",
            }
        )
        if asset.ref != "sha256:" + manifest.sha256:
            raise VideoError("asset_mismatch")
        result.append((manifest, asset.ref))
    if len(result) > 3:
        raise VideoError("invalid_video_result")
    return tuple(result)


def read_frame(raw: RawObjectStore, a: VideoAttachment, ref: str) -> bytes:
    with raw.open(raw_object_ref(parse_raw_ref(ref))) as stream:
        data = stream.read(a.size_bytes + 1)
    if len(data) != a.size_bytes or hashlib.sha256(data).hexdigest() != a.sha256:
        raise VideoError("asset_mismatch")
    return data


class VideoMarkdownRenderer:
    name = "video-notes-markdown"
    version = "1"

    def render(self, content: ContentObject) -> str:
        attachments = video_attachments(content)
        facts = content.metadata["video_notes"]
        if not isinstance(facts, dict) or content.original is None:
            raise VideoError("invalid_video_result")
        lines = [
            "# Видео — речь и выбранные кадры",
            "",
            "Источник: локальный MP4.",
            f"Capture ID: `{content.source.capture_id}`",
            f"Content ID: `{content.id}`",
            f"Operation ID: `{facts.get('operation_id')}`",
            f"Original SHA-256: `{content.original.sha256}`",
            "",
            "Оригинальное видео осталось в UniMem. Obsidian получает заметку и выбранные PNG.",
            "",
            "## Выполненная обработка",
            "",
            literal(
                json.dumps(
                    {
                        k: facts.get(k)
                        for k in (
                            "request",
                            "speech_outcome",
                            "frames_outcome",
                            "vision_outcome",
                            "audio",
                            "stage_seconds",
                        )
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            "",
            "Выборка: ¼, ½ и ¾ длительности; первый кадр с PTS не раньше позиции, без дублей. "
            "Это не выбор самых важных кадров.",
            f"Общая шкала: исходные PTS минус {facts.get('origin_rational')} с; "
            f"длительность {facts.get('duration_seconds')} с.",
            "Метки речи переведены из PCM на эту же шкалу с сохранённым смещением аудио.",
            "",
            "## Расшифровка",
            "",
        ]
        outcomes = {
            "not_requested": "ASR не запускался: не запрошен.",
            "no_audio_track": "Нет аудиодорожки; ASR не запускался.",
            "no_speech": (
                "ASR выполнен: речь не обнаружена механизмом. "
                "Это не доказательство отсутствия речи."
            ),
            "transcribed": "ASR выполнен. Возможны ошибки распознавания.",
        }
        lines.append(outcomes[str(facts.get("speech_outcome"))])
        transcript = facts.get("transcript")
        speech = [s for s in content.segments if s.type is SegmentType.TRANSCRIPT]
        if isinstance(transcript, dict):
            saved = transcript.get("segments")
            if (
                not isinstance(saved, list)
                or len(saved) != len(speech)
                or any(
                    not isinstance(c, dict) or c.get("text") != s.text
                    for c, s in zip(saved, speech, strict=True)
                )
            ):
                raise VideoError("invalid_video_result")
            lines.append(
                literal(
                    json.dumps(
                        {
                            k: transcript.get(k)
                            for k in (
                                "model",
                                "model_revision",
                                "selected_language",
                                "detected_language",
                                "parameters",
                            )
                        },
                        ensure_ascii=False,
                    )
                )
            )
        for segment in speech:
            if segment.temporal is None or segment.text is None:
                raise VideoError("invalid_video_result")
            lines += [
                f"[{segment.temporal.start:.3f} – {segment.temporal.end:.3f} с]",
                literal(segment.text),
                "",
            ]
        lines += ["## Выбранные кадры", ""]
        for a, _ in attachments:
            asset = next(asset for asset in content.assets if asset.id == a.asset_id)
            t = asset.metadata.get("presentation_seconds")
            lines += [
                f"### Кадр: {t} с",
                "",
                f"![Кадр в {t} с](./{a.relative_name})",
                "",
                "Производный PNG; исходное видео не изменялось.",
                literal(json.dumps(asset.metadata, ensure_ascii=False, indent=2)),
            ]
            for segment in content.segments:
                if segment.type is SegmentType.VISUAL and segment.provenance.asset_id == a.asset_id:
                    description = segment.metadata.get("description")
                    answer = description.get("answer") if isinstance(description, dict) else None
                    if (
                        not isinstance(description, dict)
                        or not isinstance(answer, dict)
                        or answer.get("description") != segment.text
                    ):
                        raise VideoError("invalid_video_result")
                    lines += [
                        "Описание этого кадра моделью (не OCR):",
                        literal(segment.text or ""),
                        "",
                    ]
        lines += [
            "",
            "Визуальная выборка ограничена показанными статичными кадрами. Она не описывает "
            "все события ролика, движение между кадрами, скрытые действия или их причины. "
            "Машинные описания могут выдумывать и пропускать детали. "
            "ASR и vision приведены отдельно и не исправляют друг друга.",
            "",
        ]
        return "\n".join(lines)
