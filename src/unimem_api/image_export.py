"""Read-only image projection. Only a verified manifest can supply an embed."""

import hashlib
import re

from core.contracts import (
    CaptureRecord,
    ContentObject,
    ContentType,
    ProvenanceSourceType,
    SegmentType,
)
from core.storage import RawObjectStore
from core.storage.raw import parse_raw_ref, raw_object_ref
from unimem_api.image_service import MAX_IMAGE_BYTES, ImageError, ocr_status
from unimem_api.obsidian_contract import Attachment
from unimem_api.obsidian_store import suggested_filename


def image_attachment(content: ContentObject, raw_store: RawObjectStore) -> tuple[Attachment, str]:
    if content.type is not ContentType.IMAGE or content.original is None:
        raise ImageError("invalid_image")
    assets = [a for a in content.assets if a.id == content.original.asset_id]
    if len(assets) != 1:
        raise ImageError("invalid_image")
    asset = assets[0]
    if asset.ref is None or asset.mime_type not in {"image/png", "image/jpeg"}:
        raise ImageError("invalid_image")
    with raw_store.open(raw_object_ref(parse_raw_ref(asset.ref))) as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageError("input_size_limit")
    sha = hashlib.sha256(data).hexdigest()
    if sha != asset.sha256 or asset.ref != "sha256:" + sha:
        raise ImageError("asset_mismatch")
    return Attachment(
        asset_id=asset.id,
        mime_type="image/png" if asset.mime_type == "image/png" else "image/jpeg",
        size_bytes=len(data),
        sha256=sha,
        relative_name=suggested_filename(content.source.capture_id).removesuffix(".md")
        + (".png" if asset.mime_type == "image/png" else ".jpg"),
    ), asset.ref


def literal(text: str) -> str:
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


class ImageMarkdownRenderer:
    name = "image-markdown"
    version = "1.0"

    def render(self, content: ContentObject, capture: CaptureRecord, asset: Attachment) -> str:
        if (
            content.type is not ContentType.IMAGE
            or content.original is None
            or content.original.asset_id != asset.asset_id
        ):
            raise ImageError("invalid_image")
        status = ocr_status(content)
        labels = {
            "not_requested": "OCR не запрашивался. Смысловой анализ не выполнялся.",
            "text": "OCR выполнен: текст получен. Распознавание может содержать ошибки.",
            "empty": (
                "OCR выполнен: текст не найден. "
                "Это не доказательство отсутствия текста на изображении."
            ),
            "skipped": "OCR пропущен по ограничению ресурсов; распознавание не выполнено.",
        }
        source = "Локальная загрузка изображения."
        if content.source.url:
            source += "\n" + literal(content.source.url)
        when = capture.context.captured_at if capture.context else None
        lines = [
            "# Изображение",
            "",
            source,
            "",
            f"Capture ID: `{content.source.capture_id}`",
            f"Content ID: `{content.id}`",
        ]
        if when:
            lines.append(f"Время захвата: {when.isoformat()}")
        lines += [
            "",
            f"![Исходное изображение](./{asset.relative_name})",
            "",
            "Вложение — неизменный исходный файл, включая EXIF и другую metadata.",
            "",
            "## Обработка",
            "",
            labels[status],
        ]
        for p in content.processing:
            lines.append(literal(f"{p.processor}@{p.processor_version}"))
        meta = content.metadata.get("image_ocr")
        if isinstance(meta, dict):
            facts = {
                k: meta[k]
                for k in ("engine", "engine_version", "settings", "skipped_reason")
                if k in meta
            }
            lines.append(literal(str(facts)))
        lines += ["", "## OCR-текст", ""]
        if content.segments:
            lines.append(literal("\n".join(s.text or "" for s in content.segments)))
        else:
            lines.append("Распознанный текст отсутствует; статус обработки указан выше.")
        return "\n".join(lines) + "\n"


class ImageDescriptionMarkdownRenderer(ImageMarkdownRenderer):
    version = "1.1"

    def render(self, content: ContentObject, capture: CaptureRecord, asset: Attachment) -> str:
        meta = content.metadata.get("image_description")
        segments = [
            s
            for s in content.segments
            if s.type is SegmentType.VISUAL
            and s.provenance.source_type is ProvenanceSourceType.VISION
        ]
        if (
            content.original is None
            or content.original.asset_id != asset.asset_id
            or content.type is not ContentType.IMAGE
            or not isinstance(meta, dict)
            or len(segments) != 1
            or not segments[0].text
        ):
            raise ImageError("invalid_image_description")
        answer = meta.get("answer")
        if not isinstance(answer, dict) or answer.get("description") != segments[0].text:
            raise ImageError("invalid_image_description")
        when = capture.context.captured_at if capture.context else None
        lines = [
            "# Изображение — описание моделью",
            "",
            "Локальная загрузка изображения.",
            "",
            f"Capture ID: `{capture.id}`",
            f"Content ID: `{content.id}`",
        ]
        if when:
            lines.append(f"Время захвата: {when.isoformat()}")
        lines += [
            "",
            f"![Исходное изображение](./{asset.relative_name})",
            "",
            "Вложение — неизменный исходный файл, включая EXIF и другую metadata.",
            "",
            "## Обработка",
            "",
            "Локальное описание видимого содержания моделью. Отдельное OCR не выполнялось.",
            "Машинная интерпретация не подтверждает факты. Возможны выдуманные "
            "и пропущенные детали, ошибки связей и текста. Вход уменьшен без обрезки; "
            "мелкие детали могут быть потеряны.",
            literal(
                f"Status: {answer.get('status')}\nModel: {meta.get('model')}\n"
                f"Revision: {meta.get('revision')}\nRuntime: {meta.get('runtime')}\n"
                f"Prompt: {meta.get('prompt_version')}"
            ),
            "",
            "## Описание моделью",
            "",
            literal(segments[0].text),
        ]
        return "\n".join(lines) + "\n"


def image_renderer(content: ContentObject) -> ImageMarkdownRenderer:
    return (
        ImageDescriptionMarkdownRenderer()
        if "image_description" in content.metadata
        else ImageMarkdownRenderer()
    )
