# Ручная приёмка документов (Macro Phase 3)

Этот документ — исполняемый чек-лист для владельца. Он проверяет **то, что уже
построено**: приём PDF с текстовым слоем, приём DOCX, отказ от сканов в обычном
режиме, необязательное локальное распознавание английских и русских сканов,
смешанный PDF, сохранность исходных байтов и долговечность после перезапуска
процесса.

Он ничего не добавляет к продукту. Ни один шаг ниже не требует нового формата,
нового маршрута и новой настройки: используются только `POST /v1/uploads`,
`POST /v1/captures`, `GET /v1/captures/{id}` и `GET /v1/captures/{id}/content`.

Поведение продукта описано в [README](../README.md) (разделы *Capturing a PDF
document*, *Capturing a DOCX document*, *Scanned PDFs: opt-in local OCR*) и в
[ADR-016](ADR/ADR-016-pdf-document-ingestion.md),
[ADR-017](ADR/ADR-017-docx-document-ingestion.md),
[ADR-018](ADR/ADR-018-opt-in-local-pdf-ocr.md). Здесь оно не пересказывается —
здесь его проверяют.

> **Сценарии называются DOC-A … DOC-G.** Это не те же A–G, что в браузерном
> чек-листе README. Результаты браузерной приёмки Macro Phase 2 сюда не
> переносятся.

## Как этим пользоваться

- Идите по шагам сверху вниз. Каждый шаг — отдельная короткая команда, а не один
  скрипт, который делает всё сразу: если что-то пойдёт не так, должно быть видно
  **где**.
- Каждый сценарий заканчивается разделом **«Провал, если»** и
  **«Что прислать»**. Присылайте именно это, а не весь вывод.
- Все статусы в [таблице результатов](#таблица-результатов) начинаются со
  `NOT_RUN` и меняются **только вами**. Пройденный синтетический контроль не
  означает, что прошёл ваш собственный документ.
- Ни один шаг здесь не удаляет и не перезаписывает ваши данные. Если шаг
  предлагает что-то, чего вы не понимаете, остановитесь и спросите, а не
  выполняйте.

---

## Шаг 0. Проверить checkout, ветку и окружение

Ничего не переключайте и ничего не выбрасывайте. Это только осмотр.

```bash
cd /путь/к/вашему/checkout/unimem     # ваш путь, здесь он не предполагается
git rev-parse --abbrev-ref HEAD       # на какой вы ветке
git log --oneline -1                  # на каком коммите
git status --short                    # есть ли незакоммиченные изменения
```

Если `git status --short` что-то показывает — это ваша работа. **Не** запускайте
`git clean`, `git reset --hard` и не переключайте ветку поверх грязного дерева.
Приёмку можно проводить как есть: она читает код, а не меняет его.

Посмотрите, какая это система — от этого зависят имена системных пакетов на
шаге 5:

```bash
cat /etc/os-release
```

Проверьте окружение проекта. Установка описана в README (раздел *Development*) и
здесь не изобретается заново:

```bash
ls -d .venv 2>/dev/null || echo "venv нет — создайте его по README > Development"
.venv/bin/python -V                   # проект требует Python >= 3.13
.venv/bin/python -c "import core, unimem_api; print('проект импортируется')"
```

Если venv нет, создайте его по README:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Дополнительный набор `[ocr]` и системный Tesseract — это **три отдельные вещи**,
и они нужны только с шага 5. Обычная установка выше их не требует.

---

## Шаг 1. Изолированный каталог приёмки

Приёмка работает в **отдельном** каталоге данных. Ваша обычная база UniMem и
ваш обычный raw-store не затрагиваются и никогда не удаляются.

```bash
export ACC="$HOME/unimem-acceptance"         # можно любой другой пустой каталог
mkdir -p "$ACC"/{bin,in,out,data}
```

Сервер приёмки слушает **отдельный порт** (`8791`), чтобы не конфликтовать с
обычным UniMem на `8765`.

Переменные понадобятся в двух терминалах, поэтому положим их в файл. Запустите
это **из корня checkout** — путь к репозиторию берётся у git, а не угадывается:

```bash
cat > "$ACC/env.sh" <<EOF
export REPO="$(git rev-parse --show-toplevel)"
export ACC="$ACC"
export PY="\$REPO/.venv/bin/python"
export API="http://127.0.0.1:8791"
export DOCX="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EOF
cat "$ACC/env.sh"
```

Дальше **в каждом новом терминале** первым делом:

```bash
source "$HOME/unimem-acceptance/env.sh"   # или ваш путь к env.sh
cd "$REPO"
```

---

## Шаг 2. Четыре маленьких вспомогательных скрипта

Они нужны, чтобы не требовать `jq`, Node или офисный пакет, и чтобы JSON строился
безопасно — заголовок с кавычкой или переводом строки не должен ломать тело
запроса. Скрипты живут **вне** checkout, в `$ACC/bin`, и ничего в репозитории не
меняют.

### `envelope.py` — собрать конверт capture

```bash
cat > "$ACC/bin/envelope.py" <<'PY'
"""Печатает канонический document CaptureEnvelope в JSON.

Usage: envelope.py <capture_id> <mime_type> <file_ref> [title]

Никакой интерполяции в написанный руками JSON: кавычки и экранирование делает
json.dumps, поэтому заголовок с кавычкой, обратным слэшем или переводом строки
не может испортить тело запроса.
"""

import json
import sys
from datetime import datetime, timezone

capture_id, mime_type, file_ref = sys.argv[1:4]
title = sys.argv[4] if len(sys.argv) > 4 else None

payload = {"type": "document", "mime_type": mime_type, "file_ref": file_ref}
if title is not None:
    payload["title"] = title

print(
    json.dumps(
        {
            "schema_version": "0.2",
            "id": capture_id,
            "source": {"type": "upload", "provider": "manual-acceptance"},
            "payload": payload,
            "context": {"captured_at": datetime.now(timezone.utc).isoformat()},
        },
        ensure_ascii=False,
    )
)
PY
```

### `show.py` — показать только те поля, которые нужно смотреть

```bash
cat > "$ACC/bin/show.py" <<'PY'
"""Печатает небольшой набор полей, который проверяет шаг приёмки.

Usage: show.py ref     <upload.json>    -- только file_ref, для следующего шага
       show.py record  <record.json>
       show.py content <content.json>
Читает сохранённое тело HTTP-ответа; сам никаких запросов не делает.
"""

import json
import sys

mode, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    body = json.load(handle)

if isinstance(body.get("error"), dict):
    print("error.code   :", body["error"].get("code"))
    print("error.message:", body["error"].get("message"))
    raise SystemExit(0)

if mode == "ref":
    print(body["file_ref"])
elif mode == "record":
    print("id          :", body.get("id"))
    print("status      :", body.get("status"))
    print("payload_type:", body.get("payload_type"))
    print("error       :", body.get("error"))
    raw = body.get("raw_object") or {}
    print("raw_object  : sha256=%s ref=%s" % (raw.get("sha256"), raw.get("ref")))
elif mode == "content":
    print("content id  :", body.get("id"))
    print("type        :", body.get("type"))
    print("title       :", repr(body.get("title")))
    original = body.get("original") or {}
    print("original    : sha256=%s mime=%s asset_id=%s"
          % (original.get("sha256"), original.get("mime_type"), original.get("asset_id")))
    for asset in body.get("assets") or []:
        print("asset       : role=%s ref=%s sha256=%s mime=%s"
              % (asset["role"], asset["ref"], asset.get("sha256"), asset.get("mime_type")))
    print("segments    :", len(body.get("segments") or []))
    for segment in body.get("segments") or []:
        provenance = segment.get("provenance") or {}
        spatial = segment.get("spatial") or {}
        print("  position=%s type=%-4s page=%-4s source_type=%-8s processor=%s@%s"
              % (segment.get("position"), segment["type"], spatial.get("page"),
                 provenance.get("source_type"), provenance.get("processor"),
                 provenance.get("processor_version")))
        print("    metadata:", json.dumps(segment.get("metadata") or {}, ensure_ascii=False))
        print("    text    :", json.dumps(segment.get("text"), ensure_ascii=False))
    metadata = body.get("metadata") or {}
    if "pdf_ocr" in metadata:
        print("metadata.pdf_ocr:", json.dumps(metadata["pdf_ocr"], ensure_ascii=False, indent=2))
    elif metadata:
        print("metadata    :", json.dumps(metadata, ensure_ascii=False))
else:
    raise SystemExit(f"unknown mode: {mode}")
PY
```

> Не пропускайте вывод `show.py` через `head` — обрыв потока даёт
> `BrokenPipeError`, который легко принять за отказ продукта. Смотрите вывод
> целиком.

### `embedded_text.py` — что читает продуктовый извлекатель текста

```bash
cat > "$ACC/bin/embedded_text.py" <<'PY'
"""Что читает из файла продуктовый путь извлечения текста из PDF.

Вызывается тот самый `core.processing.pdf.extract_pages`, которым пользуется
обычный PDF-процессор: утверждение «в этом контроле нет текстового слоя» делает
код, чья работа — этот слой найти, а не отдельная реализация рядом.
Набор [ocr] не нужен: путь извлечения не импортирует растеризатор.
"""

import sys
from pathlib import Path

from core.processing.pdf import extract_pages

path = Path(sys.argv[1])
with path.open("rb") as stream:
    pages, title = extract_pages(stream)

print(f"file       : {path.name}")
print(f"/Title     : {title!r}")
if not pages:
    print("embedded   : NONE -- the extraction path found no text on any page")
else:
    print(f"embedded   : text on {len(pages)} page(s)")
    for number, text in pages:
        print(f"  page {number}: {text!r}")
PY
```

### `original.py` — прочитать исходные байты обратно через raw-store

```bash
cat > "$ACC/bin/original.py" <<'PY'
"""Читает сохранённый оригинал обратно через raw-store и сверяет байты.

Usage: original.py <data_dir> <content-или-record.json> <исходный_файл>

Принимает и ContentObject (использует его asset с role=original), и
CaptureRecord (использует его raw_object) — поэтому staged-оригинал *упавшего*
capture тоже можно проверить. Работает через `core.storage.LocalRawObjectStore`,
то есть через тот же store, куда пишет сервер: раскладка файлов на диске не
угадывается по имени файла и путь не собирается руками.
"""

import hashlib
import json
import sys
from pathlib import Path

from core.contracts import RawObjectRef
from core.storage.local import LocalRawObjectStore
from unimem_api.wiring import RAW_DIRNAME

data_dir, body_path, expected_path = (Path(p) for p in sys.argv[1:4])

with body_path.open(encoding="utf-8") as handle:
    body = json.load(handle)

originals = [a for a in body.get("assets") or [] if a["role"] == "original"]
if originals:
    if len(originals) != 1:
        raise SystemExit(f"expected exactly one original asset, found {len(originals)}")
    source, label = originals[0], "content object asset (role=original)"
elif body.get("raw_object"):
    source, label = body["raw_object"], "capture record raw_object"
else:
    raise SystemExit("neither an original asset nor a raw_object is present")

store = LocalRawObjectStore(data_dir / RAW_DIRNAME)
reference = RawObjectRef(id=source["id"], ref=source["ref"], sha256=source.get("sha256"))
print("read through   :", label)
print("exists in store:", store.exists(reference))

stored = store.read_bytes(reference)
expected = expected_path.read_bytes()
print("ref            :", source["ref"])
print("recorded sha256:", source.get("sha256"))
print("sha256 of file :", hashlib.sha256(expected).hexdigest())
print("sha256 stored  :", hashlib.sha256(stored).hexdigest())
print("bytes stored   :", len(stored), "| bytes submitted:", len(expected))
print("BYTES IDENTICAL:", stored == expected)
PY
```

---

## Шаг 3. Контрольные документы

Контроли берутся из **уже существующих** построителей фикстур репозитория
(`tests/pdfs.py`, `tests/docxs.py`, `tests/ocr_fixtures.py`). Новый фикстурный
фреймворк не вводится, и ничего не записывается внутрь checkout.

```bash
cat > "$ACC/bin/make_controls.py" <<'PY'
"""Генерирует контрольные документы приёмки из фикстур самого репозитория.

Запускать из корня репозитория интерпретатором проекта. Пишет в каталог,
переданный первым аргументом; внутрь checkout ничего не записывается.
"""

import sys
from pathlib import Path

from tests import docxs, pdfs

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def write(name: str, data: bytes) -> None:
    (OUT / name).write_bytes(data)
    print(f"{name:22s} {len(data):9d} bytes")


# DOC-A: текст на страницах 1 и 3, физически пустая страница 2, /Title в метаданных.
write("text.pdf", pdfs.blank_middle_pdf(title=pdfs.METADATA_TITLE))

# DOC-B: абзац A, таблица 2x2, абзац B, с заголовком в core properties.
write("doc.docx", docxs.paragraph_table_paragraph_docx(title=docxs.CORE_TITLE))
# DOC-B (контроль приоритета заголовка): то же тело без заголовка в core properties.
write("untitled.docx", docxs.paragraph_table_paragraph_docx())

if len(sys.argv) > 2 and sys.argv[2] == "--scans":
    from tests import ocr_fixtures

    write("scan_en.pdf", ocr_fixtures.english_scan())
    write("scan_ru.pdf", ocr_fixtures.russian_scan())
    write("mixed.pdf", ocr_fixtures.mixed_document())

print()
print("expected text of text.pdf page 1:", repr(pdfs.TWO_PAGE_TEXT_EXTRACTED))
print("expected text of text.pdf page 3:", repr(pdfs.TWO_PAGE_SECOND_EXTRACTED))
print("expected /Title of text.pdf     :", repr(pdfs.METADATA_TITLE))
print("expected docx paragraphs        :", repr(docxs.PARAGRAPH_A), repr(docxs.PARAGRAPH_B))
print("expected docx table rows        :", repr(docxs.TABLE_ROW_ONE), repr(docxs.TABLE_ROW_TWO))
print("expected docx title             :", repr(docxs.CORE_TITLE))
PY
```

Сначала — только PDF и DOCX (сканы требуют Pillow, он приходит с набором
`[ocr]`; см. шаг 5):

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/make_controls.py" "$ACC/in"
```

Когда набор `[ocr]` установлен, добавьте контроли-сканы:

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/make_controls.py" "$ACC/in" --scans
```

Ожидаемо появятся: `text.pdf`, `doc.docx`, `untitled.docx`, а с `--scans` ещё
`scan_en.pdf`, `scan_ru.pdf`, `mixed.pdf`.

Из этих шести файлов побайтово воспроизводим между запусками только
`text.pdf`: он собран байт за байтом и не содержит даты. `doc.docx` и
`untitled.docx` штампуют время модификации в ZIP, а сканы получают дату создания
от PDF-писателя, поэтому их sha256 у каждого запуска свой. Это нормально — ни
один шаг ниже не сверяется с дайджестом из документации, все сверки идут с
дайджестом **вашей собственной** загрузки, который печатается рядом.

Сканы рендерятся из системного шрифта с кириллицей. Если такого шрифта нет,
генерация откажет и назовёт причину — тогда установите, например,
`fonts-dejavu-core` (Debian/Ubuntu) и повторите.

### Доказать, что сканы действительно без текстового слоя

Это **обязательный** шаг: без него DOC-C и DOC-D проверяют обычное извлечение
текста, а не отказ и не распознавание.

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/embedded_text.py" "$ACC/in/scan_en.pdf"
PYTHONPATH="$REPO" "$PY" "$ACC/bin/embedded_text.py" "$ACC/in/scan_ru.pdf"
PYTHONPATH="$REPO" "$PY" "$ACC/bin/embedded_text.py" "$ACC/in/mixed.pdf"
PYTHONPATH="$REPO" "$PY" "$ACC/bin/embedded_text.py" "$ACC/in/text.pdf"
```

Ожидается:

| файл | ожидание |
| --- | --- |
| `scan_en.pdf` | `embedded : NONE` |
| `scan_ru.pdf` | `embedded : NONE` |
| `mixed.pdf` | текст **только** на странице 1: `'This page carries its own embedded text.\n'` |
| `text.pdf` | текст на страницах 1 и 3, `/Title` = `'A PDF that names itself'` |

**Провал, если** у `scan_en.pdf` или `scan_ru.pdf` нашёлся хоть какой-то текст,
или у `mixed.pdf` текст есть не только на странице 1. В этом случае дальше не
идите: контроли негодные, и DOC-C/DOC-D ничего не докажут.

---

## Шаг 4. Запустить сервер приёмки (обычный режим)

**Откройте второй терминал** и держите сервер в нём на переднем плане. Так его
останавливают одним `Ctrl+C` — не нужен ни `pkill`, ни поиск PID, и невозможно
случайно убить чужой процесс.

Второй терминал:

```bash
source "$HOME/unimem-acceptance/env.sh"
cd "$REPO"
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8791
```

Первый терминал — проверка, что сервер жив:

```bash
curl -sS "$API/health"          # ожидается {"status":"ok"}
```

Останавливать этот сервер — только `Ctrl+C` во втором терминале. Никаких
широких `pkill`, никакого удаления `$ACC/data`, `unimem.sqlite3` или `raw/`.

---

## DOC-A. Обычный PDF, обычный режим

**Нужно:** сервер из шага 4 (без `--pdf-ocr`), файл `$ACC/in/text.pdf`.

Шаг первый — положить байты:

```bash
curl -sS -F "file=@$ACC/in/text.pdf;type=application/pdf" "$API/v1/uploads" \
  -o "$ACC/out/a_upload.json" -w 'upload HTTP %{http_code}\n'
cat "$ACC/out/a_upload.json"; echo
```

Шаг второй — отправить capture, назвав возвращённый `file_ref`:

```bash
REF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_a_pdf application/pdf "$REF" > "$ACC/out/a_envelope.json"

curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a_envelope.json" \
  -o "$ACC/out/a_capture.json" -w 'capture HTTP %{http_code}\n'
cat "$ACC/out/a_capture.json"; echo
```

Прочитать долговечную запись и канонический объект:

```bash
curl -sS "$API/v1/captures/doc_a_pdf" \
  -o "$ACC/out/a_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/doc_a_pdf/content" \
  -o "$ACC/out/a_content.json" -w 'content HTTP %{http_code}\n'

"$PY" "$ACC/bin/show.py" record "$ACC/out/a_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/a_content.json"
```

**Ожидается:** upload `200`, capture `201`, record `200`, content `200`.

| поле | ожидание |
| --- | --- |
| `record.status` | `complete` |
| `record.error` | `None` |
| `content.type` | `document` |
| `content.title` | `'A PDF that names itself'` (из `/Title`, не из имени файла) |
| сегментов | ровно **2** |
| сегмент 0 | `position=0`, `page=1`, `type=text`, `source_type=original`, `processor=pdf@0.1` |
| сегмент 1 | `position=1`, `page=3`, `type=text`, `source_type=original`, `processor=pdf@0.1` |
| текст сегмента 0 | `"The canonical object is not Markdown.\nIt is a ContentObject.\n"` |
| текст сегмента 1 | `"A second page, with its own text.\nAnd a second line on it.\n"` |

Ключевая вещь: **`page` идёт 1, 3 — а `position` идёт 0, 1.** Физически пустая
страница 2 оставляет разрыв в номерах страниц и не оставляет разрыва в порядке
чтения.

Сверить оригинал — дайджест и байты, через тот же raw-store, куда писал сервер:

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/original.py" \
  "$ACC/data" "$ACC/out/a_content.json" "$ACC/in/text.pdf"
```

**Ожидается:** `exists in store: True`, все четыре sha256 совпадают,
`BYTES IDENTICAL: True`.

**Провал, если:** capture не `201`; `status` не `complete`; сегментов не 2;
номера страниц не `1, 3`; `position` не `0, 1`; `source_type` не `original`;
текст отличается хотя бы одним символом (включая перевод строки на конце);
`BYTES IDENTICAL: False`.

**Что прислать:** вывод `show.py record`, `show.py content` и `original.py`.

---

## DOC-B. DOCX, обычный режим

**Нужно:** тот же сервер, файлы `$ACC/in/doc.docx` и `$ACC/in/untitled.docx`.
Контроль читается как *абзац A, таблица 2×2, абзац B*.

```bash
curl -sS -F "file=@$ACC/in/doc.docx;type=$DOCX" "$API/v1/uploads" \
  -o "$ACC/out/b_upload.json" -w 'upload HTTP %{http_code}\n'

REF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/b_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_b_docx "$DOCX" "$REF" > "$ACC/out/b_envelope.json"

curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/b_envelope.json" \
  -o "$ACC/out/b_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/doc_b_docx" -o "$ACC/out/b_record.json"
curl -sS "$API/v1/captures/doc_b_docx/content" -o "$ACC/out/b_content.json"

"$PY" "$ACC/bin/show.py" record "$ACC/out/b_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/b_content.json"
```

**Ожидается:** capture `201`, `status: complete`, `content.type: document`,
`content.title: 'A DOCX that names itself'`, ровно **4** сегмента в этом
порядке:

| position | text | metadata |
| --- | --- | --- |
| 0 | `"The canonical object is not Markdown."` | `{"docx_block": "paragraph"}` |
| 1 | `"Format\tPagination"` | `{"docx_block": "table_row", "table_index": 0, "row_index": 0}` |
| 2 | `"docx\trenderer-dependent"` | `{"docx_block": "table_row", "table_index": 0, "row_index": 1}` |
| 3 | `"It is a ContentObject."` | `{"docx_block": "paragraph"}` |

У **каждого** сегмента `page=None`: номера страниц у DOCX не придумываются.
`processor` — `docx@0.1`, `source_type` — `original`.

Приоритет заголовка. Два подшага:

```bash
# B2: ни заголовка в core properties, ни присланного -> title должен быть None
curl -sS -F "file=@$ACC/in/untitled.docx;type=$DOCX" "$API/v1/uploads" \
  -o "$ACC/out/b2_upload.json"
REF2=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/b2_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_b_untitled "$DOCX" "$REF2" > "$ACC/out/b2_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/b2_envelope.json" -o /dev/null -w 'capture HTTP %{http_code}\n'
curl -sS "$API/v1/captures/doc_b_untitled/content" -o "$ACC/out/b2_content.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/b2_content.json"
```

```bash
# B3: присланный payload.title должен победить заголовок из core properties
REF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/b_upload.json")   # те же байты, что в DOC-B
"$PY" "$ACC/bin/envelope.py" doc_b_titled "$DOCX" "$REF" 'Заголовок от клиента' \
  > "$ACC/out/b3_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/b3_envelope.json" -o /dev/null -w 'capture HTTP %{http_code}\n'
curl -sS "$API/v1/captures/doc_b_titled/content" -o "$ACC/out/b3_content.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/b3_content.json"
```

**Ожидается:** B2 — `title: None`. B3 — `title: 'Заголовок от клиента'`.
Ни в одном случае заголовком не становится имя файла.

Оригинал:

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/original.py" \
  "$ACC/data" "$ACC/out/b_content.json" "$ACC/in/doc.docx"
```

**Ожидается:** `BYTES IDENTICAL: True`.

> Дайджест DOCX **не** воспроизводим между запусками: запись ZIP штампует время
> модификации. Поэтому сверять надо с дайджестом вашей собственной загрузки — он
> и печатается рядом, — а не с каким-то литералом из документации.

**Провал, если:** сегментов не 4; порядок другой (например, строки таблицы
оказались после обоих абзацев); строки таблицы не разделены табуляцией; у любого
сегмента появился `page`; в B2 `title` не `None`; в B3 победил заголовок из
документа; `BYTES IDENTICAL: False`.

**Что прислать:** вывод `show.py content` для `doc_b_docx`, строки `title` для
B2 и B3, вывод `original.py`.

---

## DOC-C. Скан при выключенном OCR

**Нужно:** тот же сервер **без** `--pdf-ocr`; `$ACC/in/scan_en.pdf`, у которого
на шаге 3 уже независимо доказано отсутствие текстового слоя.

Это **ожидаемый отказ**, а не дефект продукта. Смысл в том, что пустой
`complete` был бы ложью: он утверждал бы, что документ запомнен, когда он не
запомнен.

```bash
curl -sS -F "file=@$ACC/in/scan_en.pdf;type=application/pdf" "$API/v1/uploads" \
  -o "$ACC/out/c_upload.json" -w 'upload HTTP %{http_code}\n'

REF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/c_upload.json")
echo "$REF" | tee "$ACC/out/scan_en.ref"        # понадобится в DOC-D

"$PY" "$ACC/bin/envelope.py" doc_c_scan_default application/pdf "$REF" \
  > "$ACC/out/c_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/c_envelope.json" \
  -o "$ACC/out/c_capture.json" -w 'capture HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/c_capture.json"

curl -sS "$API/v1/captures/doc_c_scan_default" \
  -o "$ACC/out/c_record.json" -w 'record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/c_record.json"

curl -sS "$API/v1/captures/doc_c_scan_default/content" \
  -o "$ACC/out/c_content.json" -w 'content HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/c_content.json"
```

**Ожидается:**

| что | ожидание |
| --- | --- |
| upload | `200` — байты положены, capture ещё не создан |
| capture | `422`, `error.code: processing_failed` |
| `GET record` | `200`, `status: failed`, `error` объясняет отсутствие извлекаемого текста |
| `GET content` | `404`, `error.code: not_found` — ContentObject не создан |

Сообщение об ошибке ничего не раскрывает о путях на диске и должно говорить, что
документ не содержит извлекаемого встроенного текста и что эта сборка не делает OCR.

Staged-оригинал остаётся на месте — это то, что позволит DOC-D прочитать те же
байты позже:

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/original.py" \
  "$ACC/data" "$ACC/out/c_record.json" "$ACC/in/scan_en.pdf"
```

**Ожидается:** `read through : capture record raw_object`,
`exists in store: True`, `BYTES IDENTICAL: True`.

**Провал, если:** capture вернул `201`; `status` стал `complete` (особенно с
нулём сегментов); `GET content` вернул `200`; сообщение об ошибке содержит путь
в файловой системе или трассировку; staged-оригинал исчез.

**Что прислать:** три HTTP-кода, вывод `show.py record` для записи и последние
две строки `original.py`.

---

## Шаг 5. Предпосылки OCR и запуск с `--pdf-ocr`

Три **разные** вещи, и их нужно различать:

1. **Обычная установка проекта** — уже сделана на шаге 0. OCR ей не нужен.
2. **Необязательный Python-набор `[ocr]`** — добавляет `pypdfium2` и `Pillow`:

   ```bash
   uv pip install --python .venv/bin/python -e ".[dev,ocr]"
   ```

3. **Системный Tesseract и языковые данные `eng` *и* `rus`** — это не
   Python-пакеты, и проект их не устанавливает. Имена пакетов зависят от вашей
   системы (вы смотрели `/etc/os-release` на шаге 0). README, раздел *Scanned
   PDFs: opt-in local OCR*, приводит команды для Debian/Ubuntu, macOS и Fedora.

> **Этот чек-лист не устанавливает системные пакеты за вас.** Установите их
> сами, обычным менеджером пакетов вашей системы, и только потом продолжайте.

Проверить предпосылки до запуска сервера:

```bash
command -v tesseract && tesseract --version | head -1
tesseract --list-langs                 # в списке должны быть И eng, И rus
"$PY" -c "import pypdfium2, PIL.Image; print('pypdfium2 и Pillow импортируются')"
```

Нужны **оба** языка. Распознавание идёт одним проходом `eng+rus`, и сборка,
которая попросила это и молча получила только английский, читала бы русские
документы иначе, ничего об этом не сообщая.

Теперь остановите обычный сервер (`Ctrl+C` во втором терминале) и запустите там
же **тот же каталог данных**, но с флагом:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8791 --pdf-ocr
```

`--data-dir` тот же самый — это принципиально: DOC-D читает байты, положенные в
DOC-C.

Если предпосылки не выполнены, сервер **не стартует**: он завершится с кодом 1 и
одним предложением, называющим нехватку, и порт не будет открыт. Тихого отката к
режиму без распознавания и тихого отката на один английский нет.

Проверка:

```bash
curl -sS "$API/health"          # ожидается {"status":"ok"}
```

---

## DOC-D. Тот же скан при включённом OCR

**Нужно:** сервер с `--pdf-ocr` из шага 5 на **том же** `$ACC/data`.

Тот же `file_ref`, что и в DOC-C, но **новый** capture id. Повторно положить
файл не нужно — байты уже в raw-store:

```bash
REF=$(cat "$ACC/out/scan_en.ref")
"$PY" "$ACC/bin/envelope.py" doc_d_scan_ocr application/pdf "$REF" \
  > "$ACC/out/d_envelope.json"

curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/d_envelope.json" \
  -o "$ACC/out/d_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/doc_d_scan_ocr" -o "$ACC/out/d_record.json"
curl -sS "$API/v1/captures/doc_d_scan_ocr/content" -o "$ACC/out/d_content.json"
"$PY" "$ACC/bin/show.py" record "$ACC/out/d_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/d_content.json"
```

То же для русского контроля:

```bash
curl -sS -F "file=@$ACC/in/scan_ru.pdf;type=application/pdf" "$API/v1/uploads" \
  -o "$ACC/out/d_ru_upload.json"
RREF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/d_ru_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_d_scan_ru application/pdf "$RREF" \
  > "$ACC/out/d_ru_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/d_ru_envelope.json" -o /dev/null -w 'capture HTTP %{http_code}\n'
curl -sS "$API/v1/captures/doc_d_scan_ru/content" -o "$ACC/out/d_ru_content.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/d_ru_content.json"
```

**Ожидается:** capture `201`, `status: complete`, один сегмент на страницу, на
которой что-то распознано, и у каждого такого сегмента:

| поле | ожидание |
| --- | --- |
| `type` | `ocr` |
| `source_type` | `ocr` |
| `processor` | `pdf-ocr@0.1` |
| `page` | физический номер страницы PDF (`1` у односторонних контролей) |
| `metadata` | содержит `pdf_ocr` с `engine` и `engine_version` |

На объекте целиком есть `metadata.pdf_ocr` с `page_count`,
`embedded_text_pages`, `ocr_attempted_pages`, `ocr_pages_without_text`,
`engine`, `engine_version`, `rasterizer`, `rasterizer_version` и `settings`
(в `settings.languages` — `eng+rus`).

В тексте английского контроля должны встречаться (регистр и переносы строк могут
отличаться):

`UniMem`, `scanned`, `acceptance`, `quick brown fox`, `lazy dog`

В русском:

`Отсканированная`, `страница`, `Проверка`, `распознавания`, `текста`

**Идеальной орфографии не требуется.** Пробелы и переводы строк зависят от
движка, отдельные символы могут быть распознаны неверно. Проверяется, что
распознавание действительно произошло и попало в канонический объект, а не
точность движка. `complete` здесь не утверждение о точности.

**Старый отказ не оживает.** Проверьте это явно:

```bash
curl -sS "$API/v1/captures/doc_c_scan_default" \
  -o "$ACC/out/c_record_after_ocr.json" -w 'record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/c_record_after_ocr.json"
curl -sS "$API/v1/captures/doc_c_scan_default/content" -o /dev/null -w 'content HTTP %{http_code}\n'
```

**Ожидается:** `doc_c_scan_default` по-прежнему `failed`, его content
по-прежнему `404`. Включение OCR ничего не переобрабатывает и не миграцию не
запускает: те же байты читаются под **новым** capture id, а старый остаётся тем,
чем был.

**Провал, если:** capture не `201`; `type`/`source_type` не `ocr`; `processor`
не `pdf-ocr@0.1`; нет `metadata.pdf_ocr`; в `settings.languages` не `eng+rus`;
в тексте нет ни одной из ожидаемых фраз; `doc_c_scan_default` сам собой стал
`complete`.

Отдельно: `503` с `error.code: ocr_unavailable` — это **не** приговор документу,
а отказ движка (нет движка, упал, таймаут, исчерпан бюджет). Capture в этом
случае остаётся `processing`, а не `failed`, и это правильное поведение.
Присылайте такое как результат DOC-D с пометкой «движок».

**Что прислать:** вывод `show.py content` для английского и русского контролей
(целиком), три HTTP-кода и `tesseract --version | head -1`.

---

## DOC-E. Смешанный PDF

**Нужно:** сервер с `--pdf-ocr`, файл `$ACC/in/mixed.pdf`. По шагу 3 у него
встроенный текст **только** на странице 1; страница 2 — скан; страница 3 —
контрольная пустая.

> Не отправляйте `mixed.pdf` capture'ом в обычном режиме: там он даёт `201` с
> одним сегментом со страницы 1, а страницы 2 и 3 просто не попадут в результат.
> Это не отказ и не дефект, но и не то, что проверяет DOC-E.

```bash
curl -sS -F "file=@$ACC/in/mixed.pdf;type=application/pdf" "$API/v1/uploads" \
  -o "$ACC/out/e_upload.json" -w 'upload HTTP %{http_code}\n'
REF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/e_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_e_mixed application/pdf "$REF" > "$ACC/out/e_envelope.json"

curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/e_envelope.json" \
  -o "$ACC/out/e_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/doc_e_mixed/content" -o "$ACC/out/e_content.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/e_content.json"
```

**Ожидается:** capture `201`, `status: complete`, ровно **2** сегмента:

| position | page | type | source_type | processor | текст |
| --- | --- | --- | --- | --- | --- |
| 0 | 1 | `text` | `original` | `pdf-ocr@0.1` | `"This page carries its own embedded text.\n"` |
| 1 | 2 | `ocr` | `ocr` | `pdf-ocr@0.1` | распознанное, содержит `middle page` и `scan` |

И в `metadata.pdf_ocr`:

| поле | ожидание |
| --- | --- |
| `page_count` | `3` |
| `embedded_text_pages` | `[1]` |
| `ocr_attempted_pages` | `[2, 3]` |
| `ocr_pages_without_text` | `[3]` |

Что здесь проверяется:

- **Физический порядок чтения.** `page` идёт `1, 2`; `position` идёт `0, 1` и
  **непрерывен**. Страница 3 не дала сегмента — разрыв виден в `page`, а не в
  `position`.
- **Встроенный текст остаётся встроенным.** У страницы 1 `type=text` и
  `source_type=original`: она прочитана обычным парсером и **не** растеризована.
  Фраза `middle page` не должна попасть в сегмент страницы 1.
- **Распознанный текст помечен как распознанный.** У страницы 2 `type=ocr` и
  `source_type=ocr`, чтобы извлечение и распознавание оставались различимы на
  каждом сегменте.
- **Пустой результат распознавания — не вывод о пустоте страницы.** Страница 3
  попала в `ocr_pages_without_text`, то есть «распознано ничего». Это
  **наблюдение**, а не утверждение «страница была визуально пустой». Поле так и
  названо.

**Принятое ограничение, а не дефект:** страница, на которой есть хоть какой-то
непустой встроенный текст, считается покрытой **целиком**. Если на такой
странице ещё и фотография вывески или скан рисунка с подписью — эти слова
**не** распознаются и ни в один сегмент не попадут. Пословного OCR по областям
нет, оценки качества текстового слоя нет, и смешивания извлечённого и
распознанного текста на одной странице нет.

**Провал, если:** сегментов не 2; у страницы 1 `type=ocr` или
`source_type=ocr`; у страницы 2 `source_type=original`; `position` не
`0, 1`; `ocr_pages_without_text` не `[3]`; `ocr_attempted_pages` не `[2, 3]`.

**Что прислать:** вывод `show.py content` целиком, включая блок
`metadata.pdf_ocr`.

---

## DOC-F. Настоящий перезапуск процесса и обратное чтение

Проверяется долговечность, а не повторная работа. Собрать другой объект
приложения внутри Python **не считается** перезапуском: нужен именно
останов и старт процесса.

Сначала запишите, что должно сохраниться:

```bash
for id in doc_a_pdf doc_b_docx doc_c_scan_default doc_d_scan_ocr doc_e_mixed; do
  curl -sS "$API/v1/captures/$id"         -o "$ACC/out/before_${id}_record.json"
  curl -sS "$API/v1/captures/$id/content" -o "$ACC/out/before_${id}_content.json" \
    -w "$id content HTTP %{http_code}\n"
done
```

Теперь **остановите сервер**: `Ctrl+C` во втором терминале. Убедитесь, что он
действительно остановлен:

```bash
curl -sS --max-time 3 "$API/health" || echo "сервер остановлен, как и ожидалось"
```

Запустите заново в том же втором терминале, **на том же каталоге данных** и
**в обычном режиме, без `--pdf-ocr`**:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8791
```

Это важная часть сценария: **чтение сохранённого содержимого не должно требовать
повторного запуска OCR-движка.** Объект, полученный распознаванием в DOC-D и
DOC-E, обязан читаться сервером без флага.

Прочитайте всё обратно и сравните:

```bash
for id in doc_a_pdf doc_b_docx doc_c_scan_default doc_d_scan_ocr doc_e_mixed; do
  curl -sS "$API/v1/captures/$id"         -o "$ACC/out/after_${id}_record.json" \
    -w "$id record HTTP %{http_code}  "
  curl -sS "$API/v1/captures/$id/content" -o "$ACC/out/after_${id}_content.json" \
    -w "content HTTP %{http_code}\n"
done

for id in doc_a_pdf doc_b_docx doc_d_scan_ocr doc_e_mixed; do
  if ! grep -q '"segments"' "$ACC/out/before_${id}_content.json"; then
    echo "$id: НЕЧЕГО СРАВНИВАТЬ -- до перезапуска содержимого не было"
  elif ! grep -q '"segments"' "$ACC/out/after_${id}_content.json"; then
    echo "$id: СОДЕРЖИМОЕ ПРОПАЛО после перезапуска"
  elif cmp -s "$ACC/out/before_${id}_content.json" "$ACC/out/after_${id}_content.json"; then
    echo "$id: содержимое побайтово совпадает"
  else
    echo "$id: СОДЕРЖИМОЕ ОТЛИЧАЕТСЯ"
  fi
done

"$PY" "$ACC/bin/show.py" content "$ACC/out/after_doc_e_mixed_content.json"
PYTHONPATH="$REPO" "$PY" "$ACC/bin/original.py" \
  "$ACC/data" "$ACC/out/after_doc_a_pdf_content.json" "$ACC/in/text.pdf"
```

**Ожидается:**

- `doc_a_pdf`, `doc_b_docx`, `doc_d_scan_ocr`, `doc_e_mixed` — record `200`
  `complete`, content `200`, и ответ **побайтово тот же**, что до перезапуска:
  те же `content id`, тот же текст, те же `provenance`, те же `page`, та же
  `metadata` (включая `metadata.pdf_ocr`).
- `doc_c_scan_default` — по-прежнему `failed`, content по-прежнему `404`.
- `original.py` — `BYTES IDENTICAL: True`, дайджест тот же.

> Проверка `cmp` осмысленна только там, где содержимое было и до, и после: два
> одинаковых ответа `404` тоже «совпадают побайтово». Поэтому цикл выше сначала
> убеждается, что в снимке вообще есть `segments`, и говорит
> `НЕЧЕГО СРАВНИВАТЬ`, если сценарий не выполнялся. Строку
> `НЕЧЕГО СРАВНИВАТЬ` нельзя считать успехом.

**Провал, если:** какой-либо `complete` стал другим статусом; идентификаторы
поменялись; `metadata.pdf_ocr` исчезла или изменилась; чтение
OCR-содержимого потребовало `--pdf-ocr`; дайджест оригинала отличается.

**Что прислать:** строки HTTP-кодов, четыре строки сравнения `cmp`, вывод
`show.py content` для `doc_e_mixed` после перезапуска и последние две строки
`original.py`.

---

## DOC-G. Идентичность и безопасные отказы

**Нужно:** сервер из DOC-F (обычный режим подойдёт).

Здесь важно не смешивать **идентичность загрузки** и **идентичность capture**.
`file_ref` адресуется содержимым: одни и те же байты — один `file_ref` и одно
хранимое тело. Capture id выбирает клиент, и это отдельная сущность: два
capture одного PDF — это два разных `ContentObject`, указывающих на одни байты.

### G1 — повторная отправка того же capture id

```bash
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a_envelope.json" \
  -o "$ACC/out/g1.json" -w 'HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/g1.json"
```

**Ожидается:** `409`, `error.code: capture_already_exists`. **Не** `200` и не
успешный «повтор»: воспроизведение завершённого capture реализовано только для
текста, и для документа идентичный повторный запрос — всё равно конфликт.

### G2 — те же байты под новым capture id

```bash
AREF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a_upload.json")
"$PY" "$ACC/bin/envelope.py" doc_g_again application/pdf "$AREF" \
  > "$ACC/out/g2_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/g2_envelope.json" \
  -o "$ACC/out/g2.json" -w 'HTTP %{http_code} '; cat "$ACC/out/g2.json"; echo
curl -sS "$API/v1/captures/doc_g_again/content" -o "$ACC/out/g2_content.json"

echo "--- новый capture ---"; "$PY" "$ACC/bin/show.py" content "$ACC/out/g2_content.json"
echo "--- DOC-A для сравнения ---"; "$PY" "$ACC/bin/show.py" content "$ACC/out/a_content.json"
```

**Ожидается:** `201`. У нового capture **другой** `content id`, **другой**
`asset_id` и другие идентификаторы сегментов, но **тот же**
`original.sha256` и тот же `asset.ref`. Байты дедуплицированы; канонические
личности — нет.

### G3 — правдоподобная, но никогда не положенная ссылка

```bash
"$PY" "$ACC/bin/envelope.py" doc_g_ghost application/pdf \
  "sha256:0000000000000000000000000000000000000000000000000000000000000001" \
  > "$ACC/out/g3_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/g3_envelope.json" \
  -o "$ACC/out/g3.json" -w 'HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/g3.json"

curl -sS "$API/v1/captures/doc_g_ghost" -o "$ACC/out/g3_record.json" \
  -w 'record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/g3_record.json"
```

**Ожидается:** `422`, `error.code: capture_material_unavailable`, и `GET record`
→ `404`: **CaptureRecord не создан**. Ссылка формально правильная, байтов нет.

### G4 — путь в файловой системе не является ссылкой

```bash
"$PY" "$ACC/bin/envelope.py" doc_g_path application/pdf "/etc/passwd" \
  > "$ACC/out/g4_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/g4_envelope.json" \
  -o "$ACC/out/g4.json" -w 'HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/g4.json"
curl -sS "$API/v1/captures/doc_g_path" -o /dev/null -w 'record HTTP %{http_code}\n'
```

**Ожидается:** `422`, `error.code: unsupported_payload`, `GET record` → `404`.
Сервер не открывает названный вами файл и не обращается ни к какому хосту.

**Провал, если:** G1 вернул `200`; G2 переиспользовал `content id` или
`asset_id`, или дал другой `original.sha256`; G3/G4 вернули что-то кроме `422`,
или оставили после себя CaptureRecord; в сообщениях об ошибке видны пути на диске.

> Ни один шаг DOC-G не правит базу руками. Все четыре случая создаются обычными
> HTTP-запросами.

**Что прислать:** четыре HTTP-кода с `error.code`, и для G2 — строки
`content id`, `original` и `asset` обоих capture рядом.

---

## Свои документы

Синтетические контроли проверяют продукт. Они **не** доказывают, что прошёл
конкретно ваш документ. Прогоните на своих файлах то же, что в DOC-A, DOC-B и
DOC-D: подставьте свой путь и свой новый capture id.

```bash
MY="/путь/к/вашему/файлу.pdf"           # или .docx с нужным $DOCX в -F и в envelope
MY_ID="doc_own_01"                      # каждый новый файл -- новый capture id

curl -sS -F "file=@$MY;type=application/pdf" "$API/v1/uploads" \
  -o "$ACC/out/own_upload.json" -w 'upload HTTP %{http_code}\n'
MYREF=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/own_upload.json")
"$PY" "$ACC/bin/envelope.py" "$MY_ID" application/pdf "$MYREF" > "$ACC/out/own_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/own_envelope.json" \
  -o "$ACC/out/own_capture.json" -w 'capture HTTP %{http_code}\n'
curl -sS "$API/v1/captures/$MY_ID"         -o "$ACC/out/own_record.json"
curl -sS "$API/v1/captures/$MY_ID/content" -o "$ACC/out/own_content.json"
"$PY" "$ACC/bin/show.py" record "$ACC/out/own_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/own_content.json"
PYTHONPATH="$REPO" "$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/own_content.json" "$MY"
```

На что смотреть в своём документе:

- весь ли ожидаемый текст на месте, и на тех ли страницах (`page`);
- у извлечённого текста `source_type=original`, у распознанного — `ocr`;
- `BYTES IDENTICAL: True` — оригинал не переписан;
- скан отказан в обычном режиме, а не сохранён пустым `complete`.

Полезно сначала посмотреть, есть ли у вашего PDF текстовый слой вообще:

```bash
PYTHONPATH="$REPO" "$PY" "$ACC/bin/embedded_text.py" "$MY"
```

> **Никуда наружу ваш документ не уходит.** Всё выполняется на вашей машине,
> против локального сервера. Не прикладывайте сам документ к отчёту о приёмке —
> достаточно вывода `show.py` и `original.py`, и при необходимости обезличенной
> выдержки текста.

---

## Таблица результатов

Заполняется **вами**. Все статусы стартуют как `NOT_RUN`. Допустимые значения:
`NOT_RUN`, `PASS`, `FAIL`, `BLOCKED`.

| Сценарий | Статус владельца | Доказательство | Заметки |
| --- | --- | --- | --- |
| Шаг 3 — контроли без текстового слоя | NOT_RUN | | |
| DOC-A — обычный PDF, обычный режим | NOT_RUN | | |
| DOC-B — DOCX, обычный режим | NOT_RUN | | |
| DOC-C — скан при выключенном OCR | NOT_RUN | | |
| DOC-D — тот же скан при включённом OCR (eng + rus) | NOT_RUN | | |
| DOC-E — смешанный PDF | NOT_RUN | | |
| DOC-F — перезапуск процесса и обратное чтение | NOT_RUN | | |
| DOC-G — идентичность и безопасные отказы | NOT_RUN | | |
| Свои документы | NOT_RUN | | |

Macro Phase 3 остаётся **открытой**, пока эта таблица не заполнена владельцем и
пока не принято отдельное явное решение о закрытии. Заполненная таблица — вход
для такого решения, а не само решение.

---

## Что уже проверено, а что нет

Проверялось в облачной сессии на коммите `00bcb67` (`main`, включает PR #17 и
PR #18) в контейнере Ubuntu 24.04, Python 3.13.12, установка
`-e ".[dev,ocr]"`. Ниже — то, что **действительно выполнялось**, а не то, что
ожидается.

**Выполнено по-настоящему, через реальный HTTP к реальному процессу сервера:**

- генерация контролей (`text.pdf`, `doc.docx`, `untitled.docx`, `scan_en.pdf`,
  `scan_ru.pdf`, `mixed.pdf`) и доказательство отсутствия текстового слоя у
  обоих сканов через продуктовый `extract_pages`;
- DOC-A целиком, включая сверку оригинала через raw-store;
- DOC-B целиком, включая оба подшага приоритета заголовка;
- DOC-C целиком, включая сохранность staged-оригинала упавшего capture;
- DOC-F в части без OCR: настоящий останов и старт процесса на том же каталоге
  данных, побайтово совпавшее обратное чтение;
- DOC-G целиком (G1–G4);
- отказ запуска с `--pdf-ocr` без Tesseract: код возврата 1, предложение,
  называющее нехватку, порт не открыт.

**Не проверено, потому что в контейнере нет системного Tesseract** (установка
системных пакетов там недоступна, и этот чек-лист её и не предполагает):

- **DOC-D** полностью — распознавание английского и русского контролей;
- **DOC-E** в части распознавания. Встроенный текст `mixed.pdf` (страница 1) и
  отсутствие текста на страницах 2–3 проверены; сами OCR-сегменты и блок
  `metadata.pdf_ocr` — нет;
- **DOC-F** в части обратного чтения OCR-содержимого.

Ожидания для этих шагов взяты из реализации (`src/core/processing/pdf_ocr.py`,
`src/unimem_api/wiring.py`) и из существующих тестов
(`tests/integration/ocr/test_real_pdf_ocr_http.py`), а не из наблюдения.
Мокированный движок здесь за доказательство не выдаётся.

Команды DOC-D и DOC-E по форме совпадают с DOC-C и DOC-A, которые выполнялись
по-настоящему, поэтому непроверенной остаётся именно работа движка, а не
синтаксис шагов.

Команды не переписывались «по мотивам»: блоки `bash` были извлечены **из этого
файла** и выполнены дословно — создание четырёх вспомогательных скриптов,
генерация контролей, доказательство отсутствия текстового слоя, DOC-A, DOC-B
(включая B2 и B3), DOC-C, проверка предпосылок OCR, отказ запуска с `--pdf-ocr`,
останов/старт и обратное чтение DOC-F, DOC-G G1–G4.

Дополнительно на этом коммите выполнялось: `ruff check` и `ruff format --check`
(чисто, 199 файлов), `mypy` в strict (`no issues found in 168 source files`);
`pytest` по
`tests/integration/api/test_pdf_document_capture.py`,
`test_docx_document_capture.py`, `test_pdf_ocr_document_capture.py` —
**186 passed**; `pytest tests/integration/ocr -rs` — **25 passed, 2 skipped**,
и обе пропущенные помечены как «tesseract не установлен». Это результат
**этого** прогона, а не перенесённые исторические числа.

---

## Известные ограничения

Они не считаются провалом приёмки.

- `complete` означает, что политика доработала и её результат сохранён. Это не
  утверждение о точности распознавания и не обещание, что прочитано каждое
  видимое слово.
- Страница с любым непустым встроенным текстом считается покрытой целиком:
  OCR по областям внутри такой страницы нет (см. DOC-E).
- Пустой результат распознавания фиксируется как `ocr_pages_without_text` —
  наблюдение, не вывод о визуальной пустоте страницы.
- Оценок уверенности нет, и они не выдумываются.
- Повтор завершённого capture для документов не реализован: идентичный
  повторный запрос всё равно `409` (DOC-G1).
- Capture, оставшийся в `processing` после `503 ocr_unavailable`, автоматически
  не восстанавливается.
- Распознавание идёт внутри запроса: у HTTP-запроса нет дедлайна, очереди и
  воркера нет. Длинный скан может занять весь бюджет документа.
- Ограничения по числу страниц, пикселям и времени — продуктовые лимиты, а не
  песочница по памяти и CPU.
- Локально и только локально: облачного OCR, загрузки моделей и сетевых
  обращений нет.

Полный перечень — в README, раздел *What this does not do*, и в ADR-016…018.

---

## Уборка

Ничего удалять не обязательно, и ничего удалять не нужно для следующего прогона.

- Остановите сервер приёмки `Ctrl+C` во втором терминале. Никаких широких
  `pkill`.
- `$ACC` можно оставить как есть: там лежат и доказательства, и входные файлы.
- Если вы всё же убираете за собой, удаляйте **только** свой каталог приёмки
  (`$ACC`) и никогда не `./data`, `unimem.sqlite3` или `raw/` обычной установки.
- Контроли, базы и вывод приёмки в репозиторий не коммитятся: они живут в
  `$ACC`, вне checkout.
