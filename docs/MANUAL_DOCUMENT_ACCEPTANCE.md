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
шаге 6:

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

Необязательный набор `[ocr]` (шаг 3) и системный Tesseract с языками (шаг 6) —
это **отдельные вещи**, и каждая появляется ровно там, где впервые нужна.
Обычная установка выше не требует ни одной из них.

---

## Шаг 1. Свежий изолированный каталог для этого прогона

Каждый **полный** прогон чек-листа получает свой каталог. Он изолирован от
обычной установки UniMem: её база и raw-store не читаются, не меняются и никогда
не удаляются. Сервер приёмки слушает отдельный порт (`8791`), чтобы не
конфликтовать с обычным UniMem на `8765`.

Каталог создаётся **без** `-p` на последнем сегменте: `mkdir` на уже
существующем каталоге завершается ошибкой, и именно это — проверка. Всё
остальное выполняется через `&&`, то есть **только** если каталог создан этой
командой:

```bash
RUNS="$HOME/unimem-acceptance"            # можно любое другое место
mkdir -p "$RUNS"
CANDIDATE="$RUNS/run-$(date -u +%Y%m%d-%H%M%S)"

mkdir "$CANDIDATE" &&
  mkdir "$CANDIDATE"/{bin,in,out,data} &&
  export ACC="$CANDIDATE" &&
  echo "СОЗДАН новый каталог прогона: $ACC" ||
  echo "ОТКАЗ: каталог $CANDIDATE не создан этой командой. Внутри него ничего
не тронуто, подкаталоги не заведены, ACC=${ACC:-<не задан>} не изменён.
Повторите команду (метка времени будет другой) или задайте своё имя."
```

Продолжайте **только** если последней строкой было `СОЗДАН новый каталог
прогона` и `$ACC` указывает на этот путь. Строка `ОТКАЗ` означает, что каталог с
таким именем уже есть: в нём могут лежать доказательства прошлого прогона, и
ничего в нём не создано и не изменено. Просто выполните команду ещё раз —
метка времени в имени будет другой.

Отдельной проверки «а пуст ли каталог» здесь **нет намеренно**. Существующий
пустой каталог — это тоже не свежий прогон: он мог остаться от прерванной
попытки или быть чужим. Отказ даёт сам `mkdir`, а не сравнение содержимого, —
поэтому «уже существует» и «непуст» не могут разойтись в разные стороны и
пропустить вас дальше.

**Ничего не удаляйте и не очищайте, чтобы проверка прошла.** Правильная реакция
на `ОТКАЗ` — новый каталог, а не освобождение старого.

Путь нужен в двух терминалах, поэтому он записывается в файл **внутри самого
каталога**. Запустите это из корня checkout — путь к репозиторию берётся у git, а
не угадывается:

```bash
cat > "$ACC/env.sh" <<EOF
export REPO="$(git rev-parse --show-toplevel)"
export ACC="$ACC"
export PY="\$REPO/.venv/bin/python"
export PYTHONPATH="\$REPO:\$ACC/bin"
export API="http://127.0.0.1:8791"
export DOCX="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EOF
cat "$ACC/env.sh"
echo
echo "Строка для второго терминала (скопируйте её целиком):"
echo "source \"$ACC/env.sh\"; cd \"\$REPO\""
```

Последняя команда печатает точную строку с **конкретным путём этого прогона**.
Скопируйте её — ею начинается каждый новый терминал, включая тот, в котором
будет жить сервер. Никакой подстановки `$HOME` на глаз: путь содержит метку
времени и должен совпадать посимвольно, иначе второй терминал будет работать с
другим каталогом данных.

`PYTHONPATH` здесь задан один раз, поэтому дальше вспомогательные скрипты
запускаются без префиксов.

### Три разные вещи, которые легко спутать

| Что вы делаете | Что берёте | Что происходит с данными |
| --- | --- | --- |
| **Продолжаете прогон** (новый терминал, перерыв, следующий сценарий) | `source` того же `env.sh` — блок создания каталога **не** повторяете | ничего не меняется; сохранённые ответы и `in/` на месте |
| **Перезапускаете сервер этого прогона** (в том числе DOC-D и DOC-F) | тот же `$ACC`, тот же `--data-dir "$ACC/data"` | база и raw-store те же; это и есть проверка долговечности |
| **Начинаете новый полный прогон** | выполняете блок создания каталога заново | метка времени другая, поэтому каталог новый и пустой; прошлый прогон остаётся нетронутым |

Разница между первой и третьей строкой — это ровно то, выполняете вы блок
создания каталога или нет. Он всегда делает **новый** каталог и никогда не
подхватывает существующий, поэтому продолжать прогон надо через `source
"$ACC/env.sh"`, а не повторным запуском шага 1.

Ничего удалять и ничего сбрасывать не нужно ни в одном из трёх случаев. В
частности: внутри одного прогона все перезапуски сервера обязаны использовать
**тот же** `--data-dir`, иначе проверяется не долговечность, а пустая база.

---

## Шаг 2. Вспомогательные скрипты

Они нужны, чтобы не требовать `jq`, Node или офисный пакет, и чтобы JSON строился
безопасно — заголовок с кавычкой или переводом строки не должен ломать тело
запроса. Скрипты живут **вне** checkout, в `$ACC/bin`, и ничего в репозитории не
меняют.

### `rawref.py` — как получить ссылку на сохранённые байты

Идентичность сохранённых байтов — это логический `ref`, а **не** `id` записи об
ассете: у `Asset.id` своя роль, и подставлять его как id raw-объекта нельзя.

```bash
cat > "$ACC/bin/rawref.py" <<'PY'
"""Построить ссылку на raw-объект из asset'а контента или raw_object записи.

Логический `ref` -- это идентичность сохранённых байтов; `id` ассета -- это id
*записи об ассете*, и как id raw-объекта он не годится. Дайджест разбирается из
ref продуктовым валидатором, присланный sha256 обязан с ним согласиться (а не
быть тихо "исправлен"), и ссылка собирается продуктовым конструктором, который
делает дайджест id раw-объекта. Id записи возвращается отдельным слоем.
"""

from typing import Any

from core.contracts import RawObjectRef
from core.storage.raw import parse_raw_ref, raw_object_ref


def raw_handle(source: dict[str, Any]) -> tuple[RawObjectRef, str, str | None]:
    """Вернуть (ссылка, дайджест, id записи) для asset'а или raw_object."""
    ref = source.get("ref")
    if not ref:
        raise SystemExit("the reference carries no 'ref'; nothing can be resolved from it")

    digest = parse_raw_ref(ref)          # только sha256:<64 hex>, любая иная форма -- отказ
    supplied = source.get("sha256")
    if supplied is not None and supplied != digest:
        raise SystemExit(
            "MISMATCH: ref digest and recorded sha256 disagree -- "
            f"ref says {digest!r}, sha256 says {supplied!r}. "
            "This is a failure, not something to repair silently."
        )

    handle = raw_object_ref(digest, mime_type=source.get("mime_type"))
    return handle, digest, source.get("id")
PY
```


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

Usage: show.py ref            <upload.json>   -- только file_ref, для следующего шага
       show.py record         <record.json>
       show.py content        <content.json>  -- включая ТЕКСТ сегментов
       show.py content-fields <content.json>  -- только технические поля, БЕЗ текста

Читает сохранённое тело HTTP-ответа; сам никаких запросов не делает.

`content` печатает извлечённый текст целиком. Для своих документов это их
содержимое: смотрите такой вывод локально, а для отправки используйте
`content-fields`, который текст не печатает вовсе.
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
elif mode in ("content", "content-fields"):
    with_text = mode == "content"
    print("content id  :", body.get("id"))
    print("type        :", body.get("type"))
    if with_text:
        print("title       :", repr(body.get("title")))
    else:
        title = body.get("title")
        print("title       :", "<присутствует, скрыт>" if title else repr(title))
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
        if with_text:
            print("    text    :", json.dumps(segment.get("text"), ensure_ascii=False))
        else:
            text = segment.get("text") or ""
            print("    text    : <скрыт> длина=%d символов" % len(text))
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

> `content` печатает **весь** извлечённый текст. Для контрольных файлов это
> ровно то, что нужно проверить. Для **своих** документов это их содержимое:
> такой вывод остаётся у вас локально, а для отправки есть `content-fields`,
> который текста не печатает вовсе. Подробнее — в разделе
> [Свои документы](#свои-документы).


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
"""Read stored original bytes back through the raw store and compare them.

Usage: original.py <data_dir> <content-или-record.json> <исходный_файл>

Принимает и ContentObject (его asset с role=original), и CaptureRecord (его
raw_object), поэтому staged-оригинал *упавшего* capture тоже проверяется.
Работает через `core.storage.LocalRawObjectStore` -- тот же store, куда пишет
сервер: раскладка на диске не угадывается по имени файла и путь не собирается
руками. Идентичность байтов -- это `ref`, а не id записи об ассете (см. rawref).
"""

import hashlib
import json
import sys
from pathlib import Path

from core.storage.local import LocalRawObjectStore
from rawref import raw_handle
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

reference, digest, record_id = raw_handle(source)
store = LocalRawObjectStore(data_dir / RAW_DIRNAME)

print("read through   :", label)
print("record id      :", record_id, "(id записи; НЕ идентичность байтов)")
print("ref            :", source["ref"])
print("digest from ref:", digest)
print("raw handle id  :", reference.id)
print("exists in store:", store.exists(reference))

stored = store.read_bytes(reference)
expected = expected_path.read_bytes()
print("sha256 of file :", hashlib.sha256(expected).hexdigest())
print("sha256 stored  :", hashlib.sha256(stored).hexdigest())
print("bytes stored   :", len(stored), "| bytes submitted:", len(expected))
print("BYTES IDENTICAL:", stored == expected)
PY
```

### `restart_check.py` — проверить, что перезапуск ничего не изменил

Эта проверка читает снимки **обоих** ответов — и `CaptureRecord`, и
`ContentObject` — вместе с их HTTP-кодами, и только потом сравнивает. Она
существует потому, что сравнения одних тел `ContentObject` недостаточно:
запись могла перейти из `complete` в `failed`, а содержимое при этом
осталось бы побайтово тем же.

```bash
cat > "$ACC/bin/restart_check.py" <<'PY'
"""Проверить, что перезапуск процесса ничего не изменил.

Usage: restart_check.py <data_dir> <snapshots_dir> <спецификация>...
  спецификация: complete:<capture_id>:<исходный_файл>
                failed:<capture_id>:<исходный_файл>

Читает сохранённые до/после снимки ОБОИХ ответов -- и CaptureRecord, и
ContentObject -- вместе с их HTTP-кодами, и только потом сравнивает. Два
одинаковых ответа 404 никогда не считаются доказательством долговечности:
отсутствующий снимок -- это BLOCKED, пропавший capture -- это FAIL.
Таблицу результатов владельца этот скрипт не заполняет.
"""

import hashlib
import json
import sys
from pathlib import Path

from core.storage.local import LocalRawObjectStore
from rawref import raw_handle
from unimem_api.wiring import RAW_DIRNAME

data_dir, snapshots = Path(sys.argv[1]), Path(sys.argv[2])
store = LocalRawObjectStore(data_dir / RAW_DIRNAME)


def read(phase: str, capture_id: str, kind: str) -> tuple[int | None, object]:
    """(HTTP-код, разобранный JSON) одного снимка; (None, None) если его нет."""
    status_path = snapshots / f"{phase}_{capture_id}_{kind}.status"
    body_path = snapshots / f"{phase}_{capture_id}_{kind}.json"
    if not status_path.is_file() or not body_path.is_file():
        return None, None
    try:
        code = int(status_path.read_text(encoding="utf-8").strip())
        return code, json.loads(body_path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None, None


def check(expected_state: str, capture_id: str, source_file: Path) -> tuple[str, list[str]]:
    notes: list[str] = []
    want_status = "complete" if expected_state == "complete" else "failed"

    before_code, before_record = read("before", capture_id, "record")
    if before_code != 200 or not isinstance(before_record, dict) or "status" not in before_record:
        return "BLOCKED", ["нет годного снимка CaptureRecord до перезапуска"]
    if before_record.get("status") != want_status:
        return "BLOCKED", [
            f"до перезапуска статус был {before_record.get('status')!r}, "
            f"а сценарий заявлен как {want_status!r} -- сценарий не выполнялся так, как описано"
        ]

    after_code, after_record = read("after", capture_id, "record")
    if after_code != 200 or not isinstance(after_record, dict):
        return "FAIL", [f"после перезапуска GET record вернул {after_code} -- capture пропал"]
    if after_record.get("id") != capture_id:
        return "FAIL", [f"после перезапуска id = {after_record.get('id')!r}, ожидался {capture_id!r}"]
    if after_record.get("status") != want_status:
        return "FAIL", [
            f"статус изменился: {before_record.get('status')!r} -> {after_record.get('status')!r}"
        ]
    if before_record != after_record:
        differing = sorted(
            k for k in set(before_record) | set(after_record)
            if before_record.get(k) != after_record.get(k)
        )
        return "FAIL", [f"снимок CaptureRecord изменился в полях: {', '.join(differing)}"]
    notes.append(f"CaptureRecord: id и {want_status} на месте, снимок не изменился")

    before_content_code, before_content = read("before", capture_id, "content")
    after_content_code, after_content = read("after", capture_id, "content")

    if expected_state == "complete":
        if before_content_code != 200 or not isinstance(before_content, dict) \
                or "segments" not in before_content:
            return "BLOCKED", notes + ["нет годного снимка ContentObject до перезапуска"]
        if after_content_code != 200 or not isinstance(after_content, dict) \
                or "segments" not in after_content:
            return "FAIL", notes + [
                f"после перезапуска GET content вернул {after_content_code} -- содержимое пропало"
            ]
        if (after_content.get("source") or {}).get("capture_id") != capture_id:
            return "FAIL", notes + ["ContentObject связан с другим capture"]
        if before_content.get("id") != after_content.get("id"):
            return "FAIL", notes + [
                f"content id изменился: {before_content.get('id')} -> {after_content.get('id')}"
            ]
        if before_content != after_content:
            differing = sorted(
                k for k in set(before_content) | set(after_content)
                if before_content.get(k) != after_content.get(k)
            )
            return "FAIL", notes + [f"снимок ContentObject изменился в полях: {', '.join(differing)}"]
        notes.append(
            f"ContentObject: id {after_content.get('id')}, связь с capture верна, снимок не изменился"
        )
        holder, layer = after_content, "asset role=original"
    else:
        # Ожидаемое отсутствие содержимого проверяется как код и код ошибки,
        # а НЕ сравнением двух тел ошибок между собой.
        if after_content_code != 404:
            return "FAIL", notes + [
                f"у упавшего capture GET content вернул {after_content_code}, ожидался 404"
            ]
        code = (after_content.get("error") or {}).get("code") if isinstance(after_content, dict) else None
        if code != "not_found":
            return "FAIL", notes + [f"404 без ожидаемого error.code=not_found (получено {code!r})"]
        notes.append("содержимого по-прежнему нет: 404 not_found, как и требуется")
        holder, layer = after_record, "record raw_object"

    originals = [a for a in holder.get("assets") or [] if a["role"] == "original"] \
        if "assets" in holder else []
    source = originals[0] if originals else holder.get("raw_object")
    if not source:
        return "FAIL", notes + ["не найден ни original asset, ни raw_object"]

    reference, digest, record_id = raw_handle(source)
    if not store.exists(reference):
        return "FAIL", notes + [f"оригинал {digest} отсутствует в raw-store после перезапуска"]
    stored = store.read_bytes(reference)
    expected = source_file.read_bytes()
    if stored != expected:
        return "FAIL", notes + ["байты оригинала после перезапуска отличаются от отправленных"]
    if hashlib.sha256(stored).hexdigest() != digest:
        return "FAIL", notes + ["сохранённые байты не соответствуют своему же дайджесту"]
    notes.append(f"оригинал ({layer}) побайтово тот же, digest {digest[:12]}…")
    return "PASS", notes


worst = "PASS"
for spec in sys.argv[3:]:
    state, capture_id, source_file = spec.split(":", 2)
    verdict, notes = check(state, capture_id, Path(source_file))
    print(f"{capture_id:22s} {verdict}")
    for note in notes:
        print(f"    - {note}")
    if verdict == "FAIL" or (verdict == "BLOCKED" and worst == "PASS"):
        worst = verdict

print()
print(f"ИТОГ ПРОВЕРКИ: {worst}")
print("Это проверка долговечности, а не запись в таблицу результатов владельца.")
raise SystemExit(0 if worst == "PASS" else 1)
PY
```

---


## Шаг 3. Python-зависимости для контрольных файлов

Контрольные файлы строятся **на вашей машине** из построителей фикстур самого
репозитория. Что для этого нужно, зависит от того, какие контроли вы делаете:

| Контроли | Что нужно | Откуда |
| --- | --- | --- |
| `text.pdf`, `doc.docx`, `untitled.docx` | обычная установка проекта | шаг 0, `-e ".[dev]"` |
| `scan_en.pdf`, `scan_ru.pdf`, `mixed.pdf` | **плюс** необязательный набор `[ocr]` (он приносит Pillow) | команда ниже |

Контроли-сканы рисуются в растр через Pillow — иначе они несли бы текстовый слой
и не были бы сканами. Pillow приходит с набором `[ocr]`:

```bash
uv pip install --python .venv/bin/python -e ".[dev,ocr]"
"$PY" -c "import PIL.Image, pypdfium2; print('Pillow и pypdfium2 доступны')"
```

> **Это чисто Python-зависимости, и они не имеют отношения к движку
> распознавания.** Установка `[ocr]` даёт возможность *сгенерировать*
> image-only PDF — и ничего больше. Она **не** устанавливает Tesseract, **не**
> включает OCR и **ничего не подтверждает** о распознавании. Системный движок и
> его языки — это шаг 6, и до него ни один шаг их не требует.

Если вы не собираетесь проверять OCR вообще, набор `[ocr]` можно не ставить: тогда
пропустите контроли-сканы, а DOC-C, DOC-D и DOC-E останутся `NOT_RUN`. DOC-A,
DOC-B, DOC-F и DOC-G от него не зависят.

---

## Шаг 4. Контрольные документы

Контроли берутся из **уже существующих** построителей фикстур репозитория
(`tests/pdfs.py`, `tests/docxs.py`, `tests/ocr_fixtures.py`). Новый фикстурный
фреймворк не вводится, и ничего не записывается внутрь checkout.

Генератор **никогда не перезаписывает существующий файл**. Это важно: байты
`doc.docx` и сканов не воспроизводимы между запусками, а их дайджест мог быть уже
загружен на сервер — перегенерация сделала бы сохранённые `file_ref` ссылками на
файлы, которых больше нет на диске.

```bash
cat > "$ACC/bin/make_controls.py" <<'PY'
"""Генерирует контрольные документы приёмки из фикстур самого репозитория.

Usage: make_controls.py <каталог> [--scans]

Запускать из корня репозитория интерпретатором проекта. Пишет в каталог,
переданный первым аргументом; внутрь checkout ничего не записывается.

НИКОГДА не перезаписывает существующий файл. Байты `doc.docx` и сканов не
воспроизводимы между запусками, а их дайджест уже мог быть загружен на сервер --
поэтому повторный запуск (например, чтобы добавить `--scans`) оставляет всё
готовое как есть и создаёт только отсутствующее.
"""

import sys
from pathlib import Path

from tests import docxs, pdfs

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
created, kept = 0, 0


def write(name: str, build) -> None:
    """Создать файл, если его ещё нет; иначе оставить существующий."""
    global created, kept
    path = OUT / name
    if path.exists():
        kept += 1
        print(f"{name:22s} {path.stat().st_size:9d} bytes  СОХРАНЁН (уже существует)")
        return
    data = build()
    path.write_bytes(data)
    created += 1
    print(f"{name:22s} {len(data):9d} bytes  создан")


# DOC-A: текст на страницах 1 и 3, физически пустая страница 2, /Title в метаданных.
write("text.pdf", lambda: pdfs.blank_middle_pdf(title=pdfs.METADATA_TITLE))

# DOC-B: абзац A, таблица 2x2, абзац B, с заголовком в core properties.
write("doc.docx", lambda: docxs.paragraph_table_paragraph_docx(title=docxs.CORE_TITLE))
# DOC-B (контроль приоритета заголовка): то же тело без заголовка в core properties.
write("untitled.docx", lambda: docxs.paragraph_table_paragraph_docx())

if "--scans" in sys.argv[2:]:
    from tests import ocr_fixtures

    write("scan_en.pdf", ocr_fixtures.english_scan)
    write("scan_ru.pdf", ocr_fixtures.russian_scan)
    write("mixed.pdf", ocr_fixtures.mixed_document)
else:
    print()
    print("Сканы не запрашивались. Добавьте --scans, когда установите набор [ocr];")
    print("повторный запуск не перезапишет уже созданные файлы выше.")

print()
print(f"создано: {created}, сохранено без изменений: {kept}")
print()
print("expected text of text.pdf page 1:", repr(pdfs.TWO_PAGE_TEXT_EXTRACTED))
print("expected text of text.pdf page 3:", repr(pdfs.TWO_PAGE_SECOND_EXTRACTED))
print("expected /Title of text.pdf     :", repr(pdfs.METADATA_TITLE))
print("expected docx paragraphs        :", repr(docxs.PARAGRAPH_A), repr(docxs.PARAGRAPH_B))
print("expected docx table rows        :", repr(docxs.TABLE_ROW_ONE), repr(docxs.TABLE_ROW_TWO))
print("expected docx title             :", repr(docxs.CORE_TITLE))
PY
```

Если набор `[ocr]` не установлен — только PDF и DOCX:

```bash
"$PY" "$ACC/bin/make_controls.py" "$ACC/in"
```

Если установлен — сразу всё, включая сканы:

```bash
"$PY" "$ACC/bin/make_controls.py" "$ACC/in" --scans
```

Обе команды можно выполнить по очереди: вторая допишет только сканы и напечатает
`СОХРАНЁН (уже существует)` для трёх уже готовых файлов. Так добавление сканов
позже не ломает уже загруженные байты и не требует возврата к предыдущим шагам.

Сканы рендерятся из системного шрифта с кириллицей. Если такого шрифта нет,
генерация откажет и назовёт причину — тогда установите, например,
`fonts-dejavu-core` (Debian/Ubuntu) и повторите. Шрифт — это шрифт, а не движок
распознавания.

Из этих шести файлов побайтово воспроизводим между запусками только
`text.pdf`: он собран байт за байтом и не содержит даты. `doc.docx` и
`untitled.docx` штампуют время модификации в ZIP, а сканы получают дату создания
от PDF-писателя, поэтому их sha256 у каждого прогона свой. Это нормально — ни
один шаг ниже не сверяется с дайджестом из документации, все сверки идут с
дайджестом **вашей собственной** загрузки, который печатается рядом.


### Доказать, что сканы действительно без текстового слоя

Это **обязательный** шаг: без него DOC-C и DOC-D проверяют обычное извлечение
текста, а не отказ и не распознавание.

```bash
"$PY" "$ACC/bin/embedded_text.py" "$ACC/in/scan_en.pdf"
"$PY" "$ACC/bin/embedded_text.py" "$ACC/in/scan_ru.pdf"
"$PY" "$ACC/bin/embedded_text.py" "$ACC/in/mixed.pdf"
"$PY" "$ACC/bin/embedded_text.py" "$ACC/in/text.pdf"
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

## Шаг 5. Запустить сервер приёмки (обычный режим)

**Откройте второй терминал** и держите сервер в нём на переднем плане. Так его
останавливают одним `Ctrl+C` — не нужен ни `pkill`, ни поиск PID, и невозможно
случайно убить чужой процесс.

Второй терминал. Первая строка — **та самая, которую напечатал шаг 1**: она
содержит путь этого прогона с меткой времени, и подставлять его по памяти нельзя,
иначе сервер поднимется над другим каталогом данных.

```bash
source "<путь, напечатанный на шаге 1>/env.sh"
cd "$REPO"
echo "каталог данных этого прогона: $ACC/data"     # сверьте с первым терминалом
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

**Нужно:** сервер из шага 5 (без `--pdf-ocr`), файл `$ACC/in/text.pdf`.

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
"$PY" "$ACC/bin/original.py" \
  "$ACC/data" "$ACC/out/a_content.json" "$ACC/in/text.pdf"
```

**Ожидается:** `exists in store: True` и одно и то же значение в четырёх строках —
`digest from ref`, `raw handle id`, `sha256 of file`, `sha256 stored` — плюс
`BYTES IDENTICAL: True`.

Отдельно обратите внимание на `record id`: это id **записи об ассете** (UUID), и
он совершенно законно отличается от дайджеста. Идентичность сохранённых байтов
несёт `ref`, а не id записи, — поэтому `original.py` берёт дайджест из `ref` и
требует, чтобы записанный `sha256` с ним совпадал. Расхождение — это
`MISMATCH` и отказ, а не повод что-то подправить.

**Провал, если:** capture не `201`; `status` не `complete`; сегментов не 2;
номера страниц не `1, 3`; `position` не `0, 1`; `source_type` не `original`;
текст отличается хотя бы одним символом (включая перевод строки на конце);
`BYTES IDENTICAL: False`; `original.py` сообщил `MISMATCH`.

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
"$PY" "$ACC/bin/original.py" \
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
на шаге 4 уже независимо доказано отсутствие текстового слоя.

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
"$PY" "$ACC/bin/original.py" \
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

## Шаг 6. Системный движок распознавания и запуск с `--pdf-ocr`

Здесь добавляется последняя из трёх разных вещей, и только она:

| | Что это | Где |
| --- | --- | --- |
| 1 | обычная установка проекта | шаг 0 |
| 2 | необязательный Python-набор `[ocr]` — Pillow и pypdfium2 | шаг 3 |
| 3 | **системный Tesseract и языковые данные `eng` и `rus`** | **этот шаг** |

Шаги 0–5 движок не требуют и ничего о нём не утверждают. Если вы дошли сюда, у
вас уже есть контроли-сканы и подтверждённое отсутствие в них текстового слоя —
это работа Pillow с шага 3, а не распознавания.

Tesseract и его языковые файлы — **не** Python-пакеты, и проект их не
устанавливает, не скачивает и не вендорит. Имена пакетов зависят от вашей системы
(вы смотрели `/etc/os-release` на шаге 0). README, раздел *Scanned PDFs: opt-in
local OCR*, приводит команды для Debian/Ubuntu, macOS и Fedora.

> **Этот чек-лист не устанавливает системные пакеты за вас.** Установите их
> сами, обычным менеджером пакетов вашей системы, и только потом продолжайте.
> Если вы не хотите их ставить — остановитесь здесь: DOC-D и DOC-E останутся
> `NOT_RUN`, а DOC-F выполняется в части без OCR.

Проверить движок до запуска сервера:

```bash
command -v tesseract && tesseract --version | head -1
tesseract --list-langs                 # в списке должны быть И eng, И rus
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

**Нужно:** сервер с `--pdf-ocr` из шага 6 на **том же** `$ACC/data`.

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

**Нужно:** сервер с `--pdf-ocr`, файл `$ACC/in/mixed.pdf`. По шагу 4 у него
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
останов и старт процесса. И тот же `--data-dir` — перезапуск на другом каталоге
проверял бы пустую базу.

### Ф1. Снять состояние до перезапуска

Снимаются **оба** ответа для каждого сценария — и `CaptureRecord`, и
`ContentObject` — вместе с HTTP-кодом каждого. Код сохраняется отдельным файлом,
потому что тело `404` само по себе ничего не доказывает:

```bash
snap() {
  local phase=$1
  for id in doc_a_pdf doc_b_docx doc_c_scan_default doc_d_scan_ocr doc_e_mixed; do
    for kind in record content; do
      case $kind in
        record)  url="$API/v1/captures/$id" ;;
        content) url="$API/v1/captures/$id/content" ;;
      esac
      curl -sS "$url" -o "$ACC/out/${phase}_${id}_${kind}.json" \
        -w '%{http_code}' > "$ACC/out/${phase}_${id}_${kind}.status"
    done
    printf '%-20s record %s  content %s\n' "$id" \
      "$(cat "$ACC/out/${phase}_${id}_record.status")" \
      "$(cat "$ACC/out/${phase}_${id}_content.status")"
  done
}

snap before
```

Сценарии, которые вы не выполняли, дадут здесь `404` — так и должно быть, и
проверка ниже превратит это в `BLOCKED`, а не в успех.

### Ф2. Остановить и запустить процесс заново

Остановите сервер: `Ctrl+C` во втором терминале. Убедитесь, что он действительно
остановлен:

```bash
curl -sS --max-time 3 "$API/health" || echo "сервер остановлен, как и ожидалось"
```

Запустите заново в том же втором терминале, на **том же** каталоге данных и
**в обычном режиме, без `--pdf-ocr`**:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8791
```

Это важная часть сценария: **чтение сохранённого содержимого не должно требовать
повторного запуска OCR-движка.** Объект, полученный распознаванием в DOC-D и
DOC-E, обязан читаться сервером без флага.

### Ф3. Снять состояние после и проверить

```bash
snap after
```

Теперь проверка. Она разбирает JSON, сверяет коды, идентификаторы, статусы и
связь content↔capture, и только потом сравнивает снимки:

```bash
"$PY" "$ACC/bin/restart_check.py" "$ACC/data" "$ACC/out" \
  complete:doc_a_pdf:"$ACC/in/text.pdf" \
  complete:doc_b_docx:"$ACC/in/doc.docx" \
  failed:doc_c_scan_default:"$ACC/in/scan_en.pdf" \
  complete:doc_d_scan_ocr:"$ACC/in/scan_en.pdf" \
  complete:doc_e_mixed:"$ACC/in/mixed.pdf"
echo "код возврата: $?"
```

Что проверяется для каждого завершённого сценария (`complete:`):

| | Проверка |
| --- | --- |
| 1 | до перезапуска `GET record` был `200` и это разобранный `CaptureRecord` со статусом `complete` — иначе `BLOCKED` |
| 2 | после перезапуска `GET record` снова `200`, `id` тот, что ожидался, статус снова `complete` |
| 3 | снимок `CaptureRecord` не изменился ни в одном поле |
| 4 | до и после `GET content` вернул `200` с `segments` |
| 5 | `content.source.capture_id` — это тот же capture, и `content id` не изменился |
| 6 | снимок `ContentObject` не изменился ни в одном поле |
| 7 | оригинал побайтово тот же: ссылка из asset'а `role=original`, чтение через raw-store, сверка с отправленным файлом |

Для намеренно упавшего DOC-C (`failed:`) — отдельно и иначе:

| | Проверка |
| --- | --- |
| 1 | до и после перезапуска `GET record` = `200`, статус `failed`, снимок записи не изменился |
| 2 | `GET content` вернул именно `404` **и** `error.code = not_found` — это проверка кода и кода ошибки, а **не** сравнение двух тел ошибок друг с другом |
| 3 | staged-оригинал по-прежнему читается из raw-store через `raw_object` записи и побайтово совпадает с отправленным файлом |

Три вердикта, и ни один из них не пишется в вашу таблицу автоматически:

- **PASS** — всё перечисленное выполнено.
- **FAIL** — что-то изменилось: пропал capture, сменился статус, разошёлся снимок,
  отличаются байты оригинала. Код возврата `1`.
- **BLOCKED** — проверять нечего: нет годного снимка до перезапуска, или сценарий
  не выполнялся (например, DOC-D и DOC-E без движка). Код возврата `1`.
  **`BLOCKED` — это не успех**, и в таблице такой сценарий остаётся `NOT_RUN`.

Ожидается, что при выполненных DOC-A, DOC-B и DOC-C первые три строки дадут
`PASS`. Если DOC-D и DOC-E не выполнялись, они дадут `BLOCKED`, и общий итог тоже
будет `BLOCKED` — это правильный результат, а не провал продукта.

**Провал, если:** любой сценарий дал `FAIL`; выполненный ранее сценарий
неожиданно дал `BLOCKED`; чтение OCR-содержимого потребовало `--pdf-ocr`.

**Что прислать:** вывод `snap before`, `snap after`, полный вывод
`restart_check.py` и его код возврата.


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
```

**Смотрите локально** (эти команды печатают содержимое вашего документа):

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/own_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/own_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/own_content.json" "$MY"
```

На что смотреть в своём документе:

- весь ли ожидаемый текст на месте, и на тех ли страницах (`page`);
- у извлечённого текста `source_type=original`, у распознанного — `ocr`;
- `BYTES IDENTICAL: True` — оригинал не переписан;
- скан отказан в обычном режиме, а не сохранён пустым `complete`.

Полезно сначала посмотреть, есть ли у вашего PDF текстовый слой вообще (эта
команда тоже печатает текст — тоже только локально):

```bash
"$PY" "$ACC/bin/embedded_text.py" "$MY"
```

### Что именно отправлять по своим документам

Ваш документ никуда наружу не уходит: всё выполняется на вашей машине против
локального сервера, и ни один шаг не обращается к внешнему сервису. Но
**отправка вывода — это отдельный вопрос**, и здесь легко ошибиться:

> `show.py content` печатает **весь извлечённый текст целиком**. Для вашего
> документа это и есть его содержимое. Такой вывод — не «обезличенный протокол»
> и пересылать его нельзя.

Для отправки есть режим, который текста не печатает вовсе — только технические
поля, типы сегментов, provenance, номера страниц, дайджесты и длины текста:

```bash
"$PY" "$ACC/bin/show.py" content-fields "$ACC/out/own_content.json"
```

Отправляйте:

- вывод `show.py record` и `show.py content-fields`;
- строку `BYTES IDENTICAL` из `original.py` (при желании — без дайджеста, если
  дайджест документа сам по себе для вас чувствителен);
- HTTP-коды;
- и, **только если это нужно для объяснения дефекта**, короткую выдержку текста,
  которую вы отредактировали вручную и осознанно.

Не отправляйте: сам документ, полный вывод `show.py content`, вывод
`embedded_text.py`. Если дефект нельзя показать без содержимого — скажите об этом
словами, и решение о том, что раскрывать, останется за вами.

---

## Таблица результатов

Заполняется **вами**. Все статусы стартуют как `NOT_RUN`. Допустимые значения:
`NOT_RUN`, `PASS`, `FAIL`, `BLOCKED`.

| Сценарий | Статус владельца | Доказательство | Заметки |
| --- | --- | --- | --- |
| Шаг 4 — контроли без текстового слоя | NOT_RUN | | |
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
PR #18) в контейнере Ubuntu 24.04, Python 3.13.12, установка `-e ".[dev,ocr]"`.
Ниже строго разделены **выполнение** (команда была запущена, результат наблюдался)
и **осмотр** (код и тесты прочитаны, команда не запускалась).

### Выполнено — реальный HTTP к реальному процессу сервера

- создание свежего изолированного каталога прогона и отказ повторного `mkdir`;
- генерация контролей и её неперезаписывающее поведение: повторный запуск с
  `--scans` дописал три скана и оставил `text.pdf`, `doc.docx`, `untitled.docx`
  нетронутыми;
- доказательство отсутствия текстового слоя у `scan_en.pdf` и `scan_ru.pdf` и
  наличия текста только на странице 1 у `mixed.pdf` — через продуктовый
  `extract_pages`;
- **DOC-A** целиком, включая сверку оригинала через raw-store;
- **DOC-B** целиком, включая оба подшага приоритета заголовка;
- **DOC-C** целиком, включая сохранность staged-оригинала упавшего capture;
- **DOC-F** в части без OCR: снимки до, реальный останов и старт процесса на том
  же каталоге данных, снимки после, и `restart_check.py` — `PASS` по DOC-A, DOC-B
  и DOC-C, `BLOCKED` по невыполненным DOC-D и DOC-E;
- **DOC-G** целиком (G1–G4);
- режим `show.py content-fields` — текст не печатается;
- `original.py` на обоих поддерживаемых входах, включая `ContentObject`, у
  которого `asset.id` (UUID) отличается от дайджеста, и отказ при расхождении
  `ref` и `sha256`;
- четыре негативные пробы `restart_check.py` (изменившийся статус при неизменном
  содержимом; пропавший capture; два одинаковых тела `404`; неизменные снимки как
  положительный контроль);
- отказ запуска с `--pdf-ocr` без Tesseract: код возврата 1, предложение,
  называющее нехватку, порт не открыт.

### Не выполнено — только осмотр реализации

**В этом окружении нет системного Tesseract, и установка системных пакетов в нём
недоступна.** Поэтому целиком **не выполнялись**:

- **DOC-D** — ни одна команда сценария не запускалась;
- **DOC-E** — ни одна команда сценария не запускалась;
- **DOC-F** в части обратного чтения OCR-содержимого.

Ожидаемые результаты этих трёх пунктов получены **осмотром**
`src/core/processing/pdf_ocr.py`, `src/unimem_api/wiring.py` и
`tests/integration/ocr/test_real_pdf_ocr_http.py`. Это чтение кода, а не
наблюдение работы.

То, что похожие по форме команды DOC-A и DOC-C выполнялись успешно, **не
является** свидетельством в пользу DOC-D и DOC-E: последовательность этих
сценариев не запускалась ни разу, и ничто здесь не подтверждает ни их вывод, ни
их синтаксис. Мокированный движок за доказательство тоже не выдаётся.

### Прочее

Блоки `bash` для выполненных шагов извлекались **из этого файла** и запускались
дословно, а не переписывались по мотивам.

На этом коммите дополнительно запускались обычные проверки репозитория:
`ruff check` и `ruff format --check` (чисто), `mypy` в strict
(`no issues found in 168 source files`), и суиты
`tests/integration/api/test_pdf_document_capture.py`,
`test_docx_document_capture.py`, `test_pdf_ocr_document_capture.py`
(186 passed), `tests/integration/ocr -rs` (25 passed, 2 skipped — обе по причине
«tesseract не установлен»). **Это проверки репозитория, а не шаги этого
чек-листа**: они ничего не говорят о том, что описанные здесь команды у вас
пройдут, и не заменяют ни одного сценария DOC-A…DOC-G.


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
- Каталог прогона (`$ACC`) лучше оставить как есть: там лежат и доказательства, и
  входные файлы, и база этого прогона. Новый полный прогон берёт новый каталог и
  этому не мешает.
- Если вы всё же убираете за собой, удаляйте **только** каталог конкретного
  прогона (`$RUNS/run-…`) и никогда не `./data`, `unimem.sqlite3` или `raw/`
  обычной установки.
- Ничего сбрасывать в базе не нужно ни для повторного прогона, ни для
  перезапуска сервера.
- Контроли, базы и вывод приёмки в репозиторий не коммитятся: они живут в
  `$ACC`, вне checkout.
