# Ручная приёмка изображений (Macro Phase 4)

Этот документ — исполняемый чек-лист для владельца. Он проверяет **то, что уже
построено**: приём статичных PNG и JPEG без интерпретации (Phase 4A) и
необязательное локальное распознавание текста на изображении (Phase 4B).

Он ничего не добавляет к продукту. Ни один шаг ниже не требует нового формата,
нового маршрута, нового поля контракта и новой настройки: используются только
`POST /v1/uploads`, `POST /v1/captures`, `GET /v1/captures/{id}` и
`GET /v1/captures/{id}/content`, а из ключей командной строки — только
`--data-dir`, `--port` и `--image-ocr`.

Поведение продукта описано в [README](../README.md) (разделы *Quick start* и
*Images: opt-in local OCR*) и в
[ADR-019](ADR/ADR-019-still-image-ingestion.md),
[ADR-020](ADR/ADR-020-opt-in-local-image-ocr.md). Здесь оно не пересказывается —
здесь его проверяют.

> **Сценарии называются IMG-A … IMG-E.** Это не `DOC-A … DOC-G` из
> [ручной приёмки документов](MANUAL_DOCUMENT_ACCEPTANCE.md) и не браузерные
> A–G из README. Результаты приёмки Macro Phase 2 и Macro Phase 3 сюда не
> переносятся, а результаты этого прогона не переносятся туда.

> **Macro Phase 4 ОТКРЫТА.** Этот документ — только руководство. Он не
> утверждает, что что-либо уже пройдено: все пять строк
> [таблицы результатов](#таблица-результатов) стоят в `NOT_RUN`. Закрытие фазы —
> отдельное изменение, которое делается **после** прогона владельца и только по
> его результату.

## Что здесь проверяется, а что уже проверено автоматически

Автоматические наборы покрывают код: словарь полей контракта, отказы на
испорченных байтах и на противоречии между заявленным MIME-типом и заголовком,
форматы, которые эта сборка отклоняет, безопасность `file_ref`, приоритет
заголовка, фиксированный `503 image_ocr_unavailable`, а в отдельной задаче CI
`Local image OCR` — настоящий Tesseract с `eng` и `rus`, настоящий процесс
сервера и проверка того, что распознавание изображений не требует ни `pypdfium2`,
ни `Pillow`. **Всё это здесь не переписывается от руки.**

Эта приёмка проверяет другое — то, до чего автоматика не дотягивается:

- документированные команды оператора работают **на вашей машине**, а не внутри
  `pytest`;
- распознавание работает на **вашей** установке Tesseract, с её версией и её
  языковыми данными;
- ограничение по числу пикселей срабатывает у **настоящего** адаптера на
  **настоящем** сервере при **штатном** значении 20 000 000 — в автоматических
  тестах этот отказ всегда изображает подменный распознаватель или искусственно
  заниженный предел;
- сохранённое переживает **настоящий перезапуск процесса** над тем же каталогом
  данных;
- на **настоящем** изображении владельца результат оценивает **человек**, потому
  что другого судьи для этого нет.

## Как этим пользоваться

- Идите по шагам сверху вниз. Каждый шаг — отдельная короткая команда, а не один
  скрипт, который делает всё сразу: если что-то пойдёт не так, должно быть видно
  **где**.
- Каждый сценарий заканчивается разделом **«Провал, если»** и
  **«Что прислать»**. Присылайте именно это, а не весь вывод сервера.
- Статусы в [таблице результатов](#таблица-результатов) ставит **только
  владелец**. Допустимые значения: `NOT_RUN`, `PASS`, `FAIL`, `BLOCKED`.
- Ни один шаг здесь не удаляет и не перезаписывает ваши данные. Если шаг
  предлагает что-то, чего вы не понимаете, остановитесь и спросите, а не
  выполняйте.
- Ничего не подкручивайте, чтобы добиться прохождения. Политика распознавания в
  Phase 4B фиксированная — `eng+rus`, OEM 1, PSM 3, без `--dpi`, без коррекции
  ориентации, без deskew, без повторов. **Именно она и принимается.**

### Порядок выполнения: три жизни сервера над одним каталогом данных

```
① python -m unimem_api --data-dir "$ACC/data" --port 8792                → IMG-A
② остановить; тот же --data-dir, плюс --image-ocr                        → IMG-B, IMG-C, IMG-E
③ остановить; тот же --data-dir, СНОВА без --image-ocr                   → IMG-D
```

Перезапуск между ② и ③ — это и есть IMG-D, поэтому **в разделах ниже IMG-E идёт
раньше IMG-D**: IMG-E нужен сервер с `--image-ocr`, а перезапуск обязан быть
последним. В таблице результатов строки стоят в алфавитном порядке.

Внутри одного прогона **все** запуски сервера используют один и тот же
`--data-dir "$ACC/data"`. Иначе проверяется не долговечность, а пустая база.

---

## Шаг 0. Проверить checkout, ветку, коммит и окружение

Ничего не переключайте и ничего не выбрасывайте. Это только осмотр.

**Приёмка проводится на слитом `main`, в котором уже есть этот файл.** Не на
ветке, не на локальном черновике. Сначала убедитесь, что это так:

```bash
cd /путь/к/вашему/checkout/unimem     # ваш путь, здесь он не предполагается
git fetch origin main
git rev-parse --abbrev-ref HEAD       # на какой вы ветке
git rev-parse HEAD                    # ТОЧНЫЙ коммит -- ЗАПИШИТЕ ЕГО
git log --oneline -1
ls -l docs/MANUAL_IMAGE_ACCEPTANCE.md # этот файл должен существовать в checkout
```

### Рабочее дерево обязано быть чистым

Результат приёмки записывается **против точного коммита**. Поэтому изменённые
или неотслеживаемые файлы в репозитории делают checkout не тем коммитом, который
будет записан: `git rev-parse HEAD` по-прежнему назовёт официальный коммит, а
Python при этом может выполнять изменённый исходник. Такая запись была бы
неправдой.

```bash
git status --porcelain
```

**Если вывод не пуст — остановите приёмку.**

- **Не** запускайте `git clean`, `git reset --hard`, `git checkout -- .` и
  `git stash`.
- **Ничего** из вашей работы не удаляется, не откатывается и не прячется этим
  руководством: ни одна команда здесь не трогает содержимое вашего checkout.
- Разберитесь со своими изменениями так, как сочтёте нужным, — закоммитьте их в
  своей ветке, отложите, скопируйте в другое место, — и начните новый прогон
  приёмки заново, с шага 0.

Остановка на этом месте — это **`BLOCKED` как предусловие, а не `FAIL`
продукта**. Строки таблицы, до которых прогон не дошёл, остаются `NOT_RUN`, а
Macro Phase 4 закрыта быть не может, пока прогон не выполнен на чистом
checkout.

### Какой именно коммит

**Записанный `git rev-parse HEAD` — это базовая версия вашей приёмки.** Её
придётся указать при отправке результата: без неё непонятно, что именно было
проверено. Здесь этот SHA намеренно **не** вписан — он станет известен только
после того, как изменение с этим руководством будет слито в `main`.

Порядок такой:

1. изменение с этим руководством сливается в `main`;
2. координатор приёмки сообщает **точный SHA коммита слияния** — это и есть
   ожидаемый SHA приёмки;
3. вы сверяете свой `git rev-parse HEAD` с ним **до** начала IMG-A.

Продолжайте к шагу 1 только если выполнено **всё**:

- `git status --porcelain` пуст;
- `docs/MANUAL_IMAGE_ACCEPTANCE.md` существует в этом checkout;
- checkout — это тот самый слитый `main`, на котором задумана приёмка, а не
  ветка разработки и не локальный черновик;
- `git rev-parse HEAD` записан дословно;
- записанный `HEAD` **совпадает** с ожидаемым SHA приёмки, который сообщил
  координатор.

Если `HEAD` не совпадает с ожидаемым SHA — не начинайте IMG-A: подтяните нужный
`main` обычным образом и повторите этот шаг. Приёмка на другом коммите проверяет
другой продукт.

Посмотрите, какая это система:

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

### Системный движок распознавания

`--image-ocr` требует **системный Tesseract с языковыми данными `eng` и `rus`**.
Сервер с этим ключом не стартует, если чего-то из этого нет: он сообщает, чего
именно не хватает, и никогда не включается «тихо без распознавания».

```bash
tesseract --version | head -1
tesseract --list-langs
```

```bash
tesseract --list-langs 2>/dev/null | grep -qx eng && echo "eng: есть" || echo "eng: НЕТ"
tesseract --list-langs 2>/dev/null | grep -qx rus && echo "rus: есть" || echo "rus: НЕТ"
```

**Этот чек-лист не устанавливает системные пакеты за вас** и ничего не меняет в
вашей системе и в вашем venv. Если движка или языка нет, поставьте их сами
средствами своего дистрибутива (README, раздел *Images: opt-in local OCR*,
показывает команду для Debian/Ubuntu) и начните прогон заново.

> Если Tesseract, `eng` или `rus` недоступны и вы не хотите их ставить:
>
> - **IMG-A** выполним сам по себе — ему распознавание не нужно;
> - **IMG-B, IMG-C и IMG-E** получают `BLOCKED`;
> - **IMG-D** свою задачу выполнить не может и остаётся `NOT_RUN` или
>   `BLOCKED`. Он доказывает, что содержимое, созданное сборкой с
>   распознаванием, читается после перезапуска сборкой без него, — а такого
>   содержимого без IMG-B и IMG-C просто не появится. Перезапуск с одним лишь
>   IMG-A проверяет не то и `PASS` за IMG-D не даёт;
> - **Macro Phase 4 закрыта быть не может** — закрытие требует пяти `PASS`.

### Необязательный набор `[ocr]` здесь не нужен

Распознавание изображений отдаёт движку исходные байты и само ничего не
декодирует, поэтому ему не нужны ни `Pillow`, ни `pypdfium2`. Контрольные
изображения тоже строятся только из `struct` и `zlib`.

**Этот чек-лист не ставит набор `[ocr]` и не требует его отсутствия.** Если он
уже установлен от прошлых работ — это безвредно и ни на что здесь не влияет.
Просто запишите, как есть:

```bash
.venv/bin/python - <<'PY'
import importlib.util
for name in ("PIL", "pypdfium2"):
    print(f"{name:10s}", "установлен" if importlib.util.find_spec(name) else "отсутствует")
PY
```

> Итог по этому пункту формулируется ровно так: **«чек-лист не требовал и не
> устанавливал набор `[ocr]`»**. Если набор у вас оказался установлен, прогон
> **не** доказывает, что распознавание изображений работает без него — эту
> границу отдельно доказывает задача CI `Local image OCR`, которая удаляет
> `pypdfium2` и `Pillow` и проверяет через `importlib`, что их действительно
> нет.
>
> **Вывод этой команды — описание окружения, а не условие.** Установлен набор
> или нет, на `PASS`/`FAIL` любой строки это не влияет и закрытию фазы не
> мешает. Требования «`[ocr]` должен отсутствовать» здесь нет и быть не может.

---

## Шаг 1. Свежий изолированный каталог для этого прогона

Каждый **полный** прогон чек-листа получает свой каталог. Он изолирован от
обычной установки UniMem: её база и raw-store не читаются, не меняются и никогда
не удаляются. Сервер приёмки слушает порт `8792`, чтобы не конфликтовать ни с
обычным UniMem на `8765`, ни с приёмкой документов на `8791`.

Каталог создаётся **без** `-p` на последнем сегменте: `mkdir` на уже
существующем каталоге завершается ошибкой, и именно это — проверка. Всё
остальное выполняется через `&&`, то есть **только** если каталог создан этой
командой:

```bash
RUNS="$HOME/unimem-acceptance"            # можно любое другое место
mkdir -p "$RUNS"
CANDIDATE="$RUNS/image-run-$(date -u +%Y%m%d-%H%M%S)"

mkdir "$CANDIDATE" &&
  mkdir "$CANDIDATE"/{bin,in,out,snap} &&
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
попытки или быть чужим. Отказ даёт сам `mkdir`, а не сравнение содержимого.

**Ничего не удаляйте и не очищайте, чтобы проверка прошла.** Правильная реакция
на `ОТКАЗ` — новый каталог, а не освобождение старого. Ни один шаг этого
руководства ничего не удаляет и не перезаписывает, и рекурсивного удаления
здесь нет ни в одной команде.

Каталог `data` здесь намеренно **не** создаётся: его заведёт сам сервер на
шаге 4, и то, что до первого запуска его нет, — лишнее подтверждение, что прогон
действительно новый.

Путь нужен в двух терминалах, поэтому он записывается в файл **внутри самого
каталога**. Запустите это из корня checkout — путь к репозиторию берётся у git, а
не угадывается:

```bash
cat > "$ACC/env.sh" <<EOF
export REPO="$(git rev-parse --show-toplevel)"
export ACC="$ACC"
export PY="\$REPO/.venv/bin/python"
export PYTHONPATH="\$REPO:\$ACC/bin"
export API="http://127.0.0.1:8792"
export PNG="image/png"
export JPEG="image/jpeg"
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
| **Перезапускаете сервер этого прогона** (шаги 5 и 7) | тот же `$ACC`, тот же `--data-dir "$ACC/data"` | база и raw-store те же; это и есть проверка долговечности |
| **Начинаете новый полный прогон** | выполняете блок создания каталога заново | метка времени другая, поэтому каталог новый и пустой; прошлый прогон остаётся нетронутым |

Разница между первой и третьей строкой — это ровно то, выполняете вы блок
создания каталога или нет. Он всегда делает **новый** каталог и никогда не
подхватывает существующий.

---

## Шаг 2. Вспомогательные скрипты

Они нужны, чтобы не требовать `jq`, Node или графический пакет, и чтобы JSON
строился безопасно. Скрипты живут **вне** checkout, в `$ACC/bin`, и ничего в
репозитории не меняют.

Скрипты похожи на те, что использует
[приёмка документов](MANUAL_DOCUMENT_ACCEPTANCE.md), но упрощены под
изображения и повторены здесь целиком: это руководство должно быть исполнимо
само по себе, без чтения соседнего.

### `rawref.py` — как получить ссылку на сохранённые байты

Идентичность сохранённых байтов — это логический `ref`, а **не** `id` записи об
ассете: у `Asset.id` своя роль, и подставлять его как id raw-объекта нельзя.

```bash
cat > "$ACC/bin/rawref.py" <<'PY'
"""Построить ссылку на raw-объект из asset'а контента или raw_object записи.

Дайджест разбирается из ref продуктовым валидатором, присланный sha256 обязан с
ним согласиться (а не быть тихо "исправлен"), и ссылка собирается продуктовым
конструктором, который делает дайджест id raw-объекта.
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

### `envelope.py` — собрать конверт capture для изображения

```bash
cat > "$ACC/bin/envelope.py" <<'PY'
"""Печатает канонический image CaptureEnvelope в JSON.

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

payload = {"type": "image", "mime_type": mime_type, "file_ref": file_ref}
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

Два режима различаются ровно одним: печатается ли **текст** распознанного
сегмента. Для контрольных изображений это синтетический токен, и его видеть
нужно. Для **вашего** изображения это его содержимое — см.
[IMG-E](#img-e-одно-настоящее-изображение-владельца).

```bash
cat > "$ACC/bin/show.py" <<'PY'
"""Печатает небольшой набор полей, который проверяет шаг приёмки.

Usage: show.py ref            <upload.json>   -- только file_ref, для следующего шага
       show.py record         <record.json>
       show.py record-fields  <record.json>   -- то же, но БЕЗ дайджеста и ref
       show.py content        <content.json>  -- включая ТЕКСТ сегментов
       show.py content-fields <content.json>  -- только технические поля, БЕЗ текста

Читает сохранённое тело HTTP-ответа; сам никаких запросов не делает.

Отсутствие ключа metadata.image_ocr печатается явной строкой, а не молчанием:
"ключа нет" -- это утверждение сценария IMG-A, и его должно быть видно.

Режимы `-fields` -- это ЕДИНСТВЕННЫЙ вывод, который разрешено отправлять по
настоящему изображению владельца (IMG-E). Они печатают строго закрытый список
полей и ничего больше:

    capture id (его задаёт это руководство), status,
    число сегментов, а по каждому сегменту -- type, source_type, position,
    spatial и ДЛИНУ текста, затем image_ocr.engine_invoked и, если он есть,
    skipped_reason.

Всё остальное отсутствует по построению, а не вычёркивается вручную: ни текста,
ни выдержки, ни title, ни ref, ни дайджеста, ни mime-типа, ни encoded_format, ни
размеров, ни имени процессора, ни произвольной metadata, ни сообщений об
ошибках. Добавлять сюда поля нельзя: это не «сокращённый вывод», а граница
приватности.
"""

import json
import sys

mode, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    body = json.load(handle)

safe = mode.endswith("-fields")

if isinstance(body.get("error"), dict):
    # В безопасном режиме печатается только код: текст ошибки -- это строка,
    # за содержимое которой этот скрипт не отвечает.
    print("error.code   :", body["error"].get("code"))
    if not safe:
        print("error.message:", body["error"].get("message"))
    raise SystemExit(0)

if mode == "ref":
    print(body["file_ref"])
elif mode == "record-fields":
    # Закрытый список: capture id задан этим руководством, status -- разрешённое
    # поле. Больше отсюда не печатается ничего.
    print("capture id  :", body.get("id"))
    print("status      :", body.get("status"))
elif mode == "record":
    print("id          :", body.get("id"))
    print("status      :", body.get("status"))
    print("payload_type:", body.get("payload_type"))
    print("error       :", body.get("error"))
    raw = body.get("raw_object") or {}
    print("raw_object  : sha256=%s ref=%s" % (raw.get("sha256"), raw.get("ref")))
elif mode == "content-fields":
    # ЗАКРЫТЫЙ СПИСОК. Это вывод для отправки по изображению владельца, и он
    # состоит ровно из перечисленного ниже. Ничего не добавлять.
    segments = body.get("segments") or []
    print("segments    :", len(segments))
    for segment in segments:
        provenance = segment.get("provenance") or {}
        print("  position=%s type=%s source_type=%s spatial=%s"
              % (segment.get("position"), segment.get("type"),
                 provenance.get("source_type"), segment.get("spatial")))
        print("    text    : <скрыт> длина=%d символов" % len(segment.get("text") or ""))
    recognition = (body.get("metadata") or {}).get("image_ocr")
    if recognition is None:
        print("image_ocr   : КЛЮЧА НЕТ")
    else:
        print("image_ocr.engine_invoked:", recognition.get("engine_invoked"))
        if "skipped_reason" in recognition:
            print("image_ocr.skipped_reason:", recognition["skipped_reason"])
elif mode == "content":
    print("content id  :", body.get("id"))
    print("type        :", body.get("type"))
    print("title       :", repr(body.get("title")))
    original = body.get("original") or {}
    print("original    : sha256=%s mime=%s asset_id=%s"
          % (original.get("sha256"), original.get("mime_type"), original.get("asset_id")))
    assets = body.get("assets") or []
    print("assets      :", len(assets))
    for asset in assets:
        print("  role=%s ref=%s mime=%s" % (asset["role"], asset["ref"], asset.get("mime_type")))
    for run in body.get("processing") or []:
        print("processing  : %s@%s status=%s"
              % (run.get("processor"), run.get("processor_version"), run.get("status")))
    segments = body.get("segments") or []
    print("segments    :", len(segments))
    for segment in segments:
        provenance = segment.get("provenance") or {}
        print("  position=%s type=%-6s source_type=%-8s processor=%s@%s spatial=%s"
              % (segment.get("position"), segment["type"], provenance.get("source_type"),
                 provenance.get("processor"), provenance.get("processor_version"),
                 segment.get("spatial")))
        print("    text    :", json.dumps(segment.get("text"), ensure_ascii=False))
    metadata = body.get("metadata") or {}
    print("metadata.image   :", json.dumps(metadata.get("image"), ensure_ascii=False))
    if "image_ocr" in metadata:
        print("metadata.image_ocr:",
              json.dumps(metadata["image_ocr"], ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("metadata.image_ocr: КЛЮЧА НЕТ")
    extra = sorted(set(metadata) - {"image", "image_ocr"})
    if extra:
        print("metadata (прочее):", ", ".join(extra))
else:
    raise SystemExit(f"unknown mode: {mode}")
PY
```

> Не пропускайте вывод `show.py` через `head` — обрыв потока даёт
> `BrokenPipeError`, который легко принять за отказ продукта. Смотрите вывод
> целиком.

### `headers.py` — что читает продуктовый разбор заголовков

Размеры контрольных файлов не выписываются из головы: их читает **тот же**
разбор заголовков, которым пользуется процессор.

```bash
cat > "$ACC/bin/headers.py" <<'PY'
"""Показать, что продуктовый разбор заголовка видит в файле.

Usage: headers.py <файл.png|файл.jpg>...

Никакого декодирования: PNG читается до конца IHDR, JPEG -- до первого
поддержанного SOF, ровно как это делает Phase 4A.
"""

import sys
from pathlib import Path

from core.processing import read_jpeg_header, read_png_header

for name in sys.argv[1:]:
    path = Path(name)
    data = path.read_bytes()
    reader = read_png_header if data[:8] == b"\x89PNG\r\n\x1a\n" else read_jpeg_header
    with path.open("rb") as handle:
        header = reader(handle)
    print("%-22s %9d bytes  format=%-4s %d x %d  (= %d пикселей)"
          % (path.name, len(data), header.encoded_format,
             header.width, header.height, header.width * header.height))
PY
```

### `original.py` — прочитать исходные байты обратно через raw-store

```bash
cat > "$ACC/bin/original.py" <<'PY'
"""Read stored original bytes back through the raw store and compare them.

Usage: original.py <data_dir> <content-или-record.json> <исходный_файл> [--quiet]

Работает через `core.storage.LocalRawObjectStore` -- тот же store, куда пишет
сервер: раскладка на диске не угадывается по имени файла и путь не собирается
руками.

--quiet печатает РОВНО одну строку и больше ничего:

    BYTES IDENTICAL: True          байты совпали
    BYTES IDENTICAL: False         байты различаются
    BYTES IDENTICAL: CHECK_FAILED  сравнение не удалось выполнить

Это режим для изображения владельца: путь, ref, дайджест, длины и текст
исключения там сами по себе являются приватными. Поэтому в --quiet НИ ОДИН
отказ не печатает ни значения, ни сообщения исключения, ни трассировки: любая
ошибка чтения, разбора ссылки или обращения к store становится CHECK_FAILED и
ненулевым кодом выхода. Что именно сломалось, смотрят локально -- тем же
скриптом БЕЗ --quiet. Добавлять в этот режим строки нельзя.
"""

import hashlib
import json
import sys
from pathlib import Path

from core.storage.local import LocalRawObjectStore
from rawref import raw_handle
from unimem_api.wiring import RAW_DIRNAME

quiet = "--quiet" in sys.argv[1:]
data_dir, body_path, expected_path = (Path(p) for p in sys.argv[1:4])


def fail(message: str) -> None:
    """Отказ. В тихом режиме -- одна фиксированная строка без подробностей."""
    if quiet:
        print("BYTES IDENTICAL: CHECK_FAILED")
    else:
        print(message, file=sys.stderr)
    raise SystemExit(2)


def guarded(what: str, action):
    """Выполнить шаг; в тихом режиме подробности отказа наружу не выпускать.

    SystemExit перехватывается намеренно: raw_handle сообщает о рассогласовании
    дайджеста именно так, и его текст содержит дайджест. KeyboardInterrupt и
    прочие BaseException не перехватываются.
    """
    try:
        return action()
    except SystemExit as exit_request:
        fail(f"{what}: {exit_request}")
    except Exception as error:  # noqa: BLE001 - граница приватности, а не логика
        fail(f"{what}: {type(error).__name__}: {error}")


body = guarded("не удалось прочитать сохранённый ответ",
               lambda: json.loads(body_path.read_text(encoding="utf-8")))

originals = [a for a in body.get("assets") or [] if a["role"] == "original"]
if originals:
    if len(originals) != 1:
        fail(f"expected exactly one original asset, found {len(originals)}")
    source, label = originals[0], "content object asset (role=original)"
elif body.get("raw_object"):
    source, label = body["raw_object"], "capture record raw_object"
else:
    fail("neither an original asset nor a raw_object is present")

reference, digest, record_id = guarded("ссылку на оригинал не удалось разобрать",
                                       lambda: raw_handle(source))
store = LocalRawObjectStore(data_dir / RAW_DIRNAME)
stored = guarded("сохранённый оригинал не удалось прочитать",
                 lambda: store.read_bytes(reference))
expected = guarded("исходный файл не удалось прочитать",
                   lambda: expected_path.read_bytes())

if not quiet:
    print("read through   :", label)
    print("exists in store:", store.exists(reference))
    print("record id      :", record_id, "(id записи; НЕ идентичность байтов)")
    print("ref            :", source["ref"])
    print("digest from ref:", digest)
    print("sha256 of file :", hashlib.sha256(expected).hexdigest())
    print("sha256 stored  :", hashlib.sha256(stored).hexdigest())
    print("bytes stored   :", len(stored), "| bytes submitted:", len(expected))
print("BYTES IDENTICAL:", stored == expected)
raise SystemExit(0 if stored == expected else 1)
PY
```

### `same_image_metadata.py` — структурные наблюдения не изменились

IMG-B утверждает, что распознавание **добавляет сегмент и больше ничего**.
Проверяет это не глаз, а сравнение двух сохранённых ответов.

```bash
cat > "$ACC/bin/same_image_metadata.py" <<'PY'
"""Сравнить metadata["image"] двух ContentObject, снятых с одних и тех же байт.

Usage: same_image_metadata.py <content_a.json> <content_b.json>
"""

import json
import sys

paths = sys.argv[1:3]
mappings = []
for path in paths:
    with open(path, encoding="utf-8") as handle:
        mappings.append((json.load(handle).get("metadata") or {}).get("image"))

for path, mapping in zip(paths, mappings):
    print("%-28s %s" % (path.rsplit("/", 1)[-1], json.dumps(mapping, ensure_ascii=False,
                                                            sort_keys=True)))

if any(mapping is None for mapping in mappings):
    print("IMAGE METADATA IDENTICAL: False  (в одном из ответов ключа image нет)")
    raise SystemExit(1)

identical = mappings[0] == mappings[1]
print("IMAGE METADATA IDENTICAL:", identical)
raise SystemExit(0 if identical else 1)
PY
```

### `restart_check.py` — проверить, что перезапуск ничего не изменил

Эта проверка читает снимки **обоих** ответов — и `CaptureRecord`, и
`ContentObject` — вместе с их HTTP-кодами, и только потом сравнивает. Она
существует потому, что сравнения одних тел `ContentObject` недостаточно:
запись могла перейти из `complete` в другое состояние, а содержимое при этом
осталось бы тем же.

Дайджестов она не печатает вовсе — чтобы один и тот же вывод можно было
присылать и для контрольных файлов, и для изображения владельца.

```bash
cat > "$ACC/bin/restart_check.py" <<'PY'
"""Проверить, что перезапуск процесса ничего не изменил.

Usage: restart_check.py <data_dir> <snapshots_dir> <capture_id>:<исходный_файл>...

Читает сохранённые до/после снимки ОБОИХ ответов вместе с их HTTP-кодами и
только потом сравнивает. Отсутствующий снимок -- это BLOCKED, пропавший
capture -- это FAIL. Таблицу результатов владельца этот скрипт не заполняет.

Ни одного дайджеста и ни одного имени файла в выводе: его можно присылать как
есть, в том числе для изображения владельца. Это верно И ПРИ ОТКАЗЕ: любая
ошибка разбора ссылки, обращения к raw-store или чтения исходного файла
превращается в вердикт с фиксированной категорией отказа, а не в трассировку с
путём, ref или дайджестом внутри.
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


class CheckFailed(Exception):
    """Шаг проверки не удалось выполнить. Несёт КАТЕГОРИЮ, а не значение."""


def guarded(category: str, action):
    """Выполнить шаг; наружу выпустить только категорию отказа.

    SystemExit перехватывается намеренно: raw_handle сообщает о рассогласовании
    дайджеста именно так, и его текст содержит дайджест. Ни само значение, ни
    текст исключения дальше не идут -- вердикт несёт только категорию.
    KeyboardInterrupt и прочие BaseException не перехватываются.
    """
    try:
        return action()
    except SystemExit:
        raise CheckFailed(category) from None
    except Exception:  # noqa: BLE001 - граница приватности, а не логика
        raise CheckFailed(category) from None


def read(phase: str, capture_id: str, kind: str) -> tuple[int | None, object]:
    """(HTTP-код, разобранный JSON) одного снимка; (None, None) если его нет."""
    status_path = snapshots / f"{phase}_{capture_id}_{kind}.status"
    body_path = snapshots / f"{phase}_{capture_id}_{kind}.json"
    if not status_path.is_file() or not body_path.is_file():
        return None, None
    try:
        code = int(status_path.read_text(encoding="utf-8").strip())
        return code, json.loads(body_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None


def check(capture_id: str, source_file: Path) -> tuple[str, list[str]]:
    notes: list[str] = []

    before_code, before_record = read("before", capture_id, "record")
    if before_code != 200 or not isinstance(before_record, dict) or "status" not in before_record:
        return "BLOCKED", ["нет годного снимка CaptureRecord до перезапуска"]
    if before_record.get("status") != "complete":
        return "BLOCKED", [
            f"до перезапуска статус был {before_record.get('status')!r}, "
            "а этот прогон предполагает complete -- сценарий выполнялся не так, как описано"
        ]

    after_code, after_record = read("after", capture_id, "record")
    if after_code != 200 or not isinstance(after_record, dict):
        return "FAIL", [f"после перезапуска GET record вернул {after_code} -- capture пропал"]
    if after_record.get("id") != capture_id:
        return "FAIL", [f"после перезапуска id = {after_record.get('id')!r}, ожидался {capture_id!r}"]
    if before_record != after_record:
        differing = sorted(
            k for k in set(before_record) | set(after_record)
            if before_record.get(k) != after_record.get(k)
        )
        return "FAIL", [f"снимок CaptureRecord изменился в полях: {', '.join(differing)}"]
    notes.append("CaptureRecord: complete на месте, снимок не изменился")

    before_content_code, before_content = read("before", capture_id, "content")
    after_content_code, after_content = read("after", capture_id, "content")

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
        return "FAIL", notes + ["content id изменился"]
    if before_content != after_content:
        differing = sorted(
            k for k in set(before_content) | set(after_content)
            if before_content.get(k) != after_content.get(k)
        )
        return "FAIL", notes + [f"снимок ContentObject изменился в полях: {', '.join(differing)}"]
    notes.append("ContentObject: связь с capture верна, снимок не изменился "
                 f"(сегментов: {len(after_content.get('segments') or [])})")

    originals = [a for a in after_content.get("assets") or [] if a["role"] == "original"]
    if len(originals) != 1:
        return "FAIL", notes + [f"ожидался ровно один original asset, найдено {len(originals)}"]

    reference, digest, _ = guarded("ссылку на оригинал не удалось разобрать",
                                   lambda: raw_handle(originals[0]))
    if not guarded("raw-store не отвечает", lambda: store.exists(reference)):
        return "FAIL", notes + ["оригинал отсутствует в raw-store после перезапуска"]
    stored = guarded("сохранённый оригинал не удалось прочитать",
                     lambda: store.read_bytes(reference))
    submitted = guarded("исходный файл не удалось прочитать",
                        lambda: source_file.read_bytes())
    if stored != submitted:
        return "FAIL", notes + ["байты оригинала после перезапуска отличаются от отправленных"]
    if hashlib.sha256(stored).hexdigest() != digest:
        return "FAIL", notes + ["сохранённые байты не соответствуют своему же дайджесту"]
    notes.append("оригинал побайтово тот же")
    return "PASS", notes


worst = "PASS"
for spec in sys.argv[3:]:
    capture_id, source_file = spec.split(":", 1)
    try:
        verdict, notes = check(capture_id, Path(source_file))
    except CheckFailed as unfinished:
        # Проверку выполнить не удалось. Это НЕ вывод о продукте, поэтому
        # BLOCKED, и наружу идёт только категория -- без пути, ref и дайджеста.
        verdict = "BLOCKED"
        notes = [f"проверку целостности выполнить не удалось: {unfinished.args[0]}"]
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

## Шаг 3. Контрольные изображения

Контроли берутся из **уже существующего** построителя фикстур репозитория
(`tests/images.py`). Новый фикстурный фреймворк не вводится, и ничего не
записывается внутрь checkout.

Построитель собран из `struct` и `zlib` — **никакой графической библиотеки**, ни
для генерации, ни для распознавания. Поэтому этот шаг не требует ни `Pillow`, ни
`pypdfium2`, ни набора `[ocr]`.

Генератор **никогда не перезаписывает существующий файл**: дайджест уже
созданного файла мог быть загружен на сервер, и перегенерация сделала бы
сохранённые `file_ref` ссылками на файлы, которых больше нет на диске.

```bash
cat > "$ACC/bin/make_controls.py" <<'PY'
"""Генерирует контрольные изображения приёмки из фикстур самого репозитория.

Usage: make_controls.py <каталог>

Запускать из корня репозитория интерпретатором проекта. Пишет в каталог,
переданный первым аргументом; внутрь checkout ничего не записывается.

НИКОГДА не перезаписывает существующий файл.
"""

import sys
from pathlib import Path

from tests import images

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


# IMG-A1 и IMG-B: настоящий PNG с нарисованным словом. Открывается любым
# просмотрщиком, и именно его читает настоящий Tesseract.
write("text.png", images.render_text_png)

# IMG-A2: JPEG для отдельного разбора кадрового заголовка Phase 4A.
write("structural.jpg", lambda: images.jpeg(width=640, height=360))

# IMG-C: заголовок заявляет 5000 x 5000 = 25 000 000 пикселей, что выше
# штатного предела 20 000 000. Пикселей при этом не существует -- и не должно.
write("oversize-header.png", lambda: images.png(width=5000, height=5000))

print()
print(f"создано: {created}, сохранено без изменений: {kept}")
PY
```

```bash
"$PY" "$ACC/bin/make_controls.py" "$ACC/in"
```

Посмотрите, что о них говорит **продуктовый** разбор заголовков — не `file`, не
просмотрщик и не память:

```bash
"$PY" "$ACC/bin/headers.py" "$ACC/in"/*.png "$ACC/in"/*.jpg
```

| файл | ожидание | размер |
| --- | --- | --- |
| `text.png` | `format=png`, `978 x 232` | 1618 байт |
| `structural.jpg` | `format=jpeg`, `640 x 360` | 54 байта |
| `oversize-header.png` | `format=png`, `5000 x 5000` = **25 000 000** пикселей | 68 байт |

Размеры в байтах детерминированы: построитель собирает их из `struct` и `zlib`,
без кодировщика, без временных меток и без вариантов сжатия. Расхождение — это
находка, а не шум.

### Что из этого настоящая картинка, а что нет

Это важно, и здесь ничего не приукрашивается:

- **`text.png` — настоящее изображение.** Полный PNG в оттенках серого,
  чёрное по белому, со всеми строками пикселей. Его открывает любой
  просмотрщик.
- **`structural.jpg` — НЕ фотография и НЕ декодируемое изображение.** Это
  корректный кадровый заголовок JPEG (`SOI`, `APP0`, `SOF0` с размерами) и
  завершающий хвост — без единого байта энтропийно-кодированных данных.
  Просмотрщик откажется его открыть, и это правильно: Phase 4A читает
  заголовок и останавливается, а IMG-A2 проверяет ровно эту границу.
  Файл назван `structural.jpg`, а не `photo.jpg`, чтобы не подразумевать
  обратного.
- **`oversize-header.png` — тоже НЕ декодируемое изображение.** Корректный
  `IHDR`, заявляющий 5000 × 5000, и токенный хвост `IDAT`/`IEND`. Двадцать пять
  миллионов пикселей в нём никогда не существовали, и в этом весь смысл: предел
  по пикселям проверяется **до** чтения байт для распознавания и **до** запуска
  движка, поэтому контроль стоит 68 байт вместо сотни мегабайт.

### Посмотрите на `text.png` своими глазами

```bash
xdg-open "$ACC/in/text.png"      # либо любой ваш просмотрщик изображений
```

Прочитайте слово на картинке и запомните его. **Это и есть тот факт, который
делает IMG-B осмысленным:** иначе сравнивался бы вывод движка с константой в
скрипте, а не с тем, что действительно нарисовано.

> Если слово на картинке не читается глазом — остановитесь. IMG-B в этом случае
> проверяет не то, что нужно, и его результат ничего не будет значить.

Этот шаг **не** строка таблицы результатов. Это подготовка, без которой IMG-A,
IMG-B и IMG-C выполнять нечем.

### Чего здесь намеренно нет

Контроля «изображение без читаемого текста» здесь нет. Настоящий Tesseract на
изображении без слов уже проверяется автоматически, в задаче CI `Local image
OCR`: движок отрабатывает, возвращает пустой результат, capture остаётся
`COMPLETE` с `segments: []` и `engine_invoked: true`. Дублировать это руками
нечем — результат был бы тем же самым, полученным тем же движком.

Различать `engine_invoked: true` без сегментов и `engine_invoked: false` со
`skipped_reason` вам всё равно придётся: ровно это делает IMG-C, где второй
случай виден целиком.

---

## Шаг 4. Запустить сервер приёмки (обычный режим, без OCR)

**Это первая из трёх жизней сервера.** Ключей распознавания здесь нет —
проверяется то, что пользователь получает сразу после установки.

Во втором терминале:

```bash
source "$ACC/env.sh"; cd "$REPO"
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8792
```

Каталог `$ACC/data` сервер создаст сам. Оставьте терминал открытым — сервер
живёт в нём. Вернитесь в первый терминал и проверьте, что он отвечает:

```bash
curl -sS "$API/health"; echo
```

Ожидается `{"status":"ok"}`. `GET /health` сообщает только о живости процесса и
не проверяет ничего другого.

---

## IMG-A. Приём PNG и JPEG в обычном режиме

**Что проверяется:** то, что получает человек, поставивший UniMem и запустивший
его без единого ключа. Изображение принимается, сохраняется в точности как
прислано, описывается структурно — и **не интерпретируется**.

В строке таблицы две обязательные подпроверки: **IMG-A1** (PNG) и **IMG-A2**
(JPEG). `PASS` ставится, только если прошли обе.

Разбор кадрового заголовка JPEG написан отдельно от разбора `IHDR` у PNG,
поэтому второй формат — не формальность.

### IMG-A1 — PNG

```bash
curl -sS -F "file=@$ACC/in/text.png;type=image/png" "$API/v1/uploads" \
  -o "$ACC/out/a1_upload.json" -w 'upload HTTP %{http_code}\n'
REF_A1=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a1_upload.json")
echo "file_ref: $REF_A1"
```

```bash
"$PY" "$ACC/bin/envelope.py" img_a1_png "$PNG" "$REF_A1" > "$ACC/out/a1_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a1_envelope.json" \
  -o "$ACC/out/a1_capture.json" -w 'capture HTTP %{http_code}\n'
```

```bash
curl -sS "$API/v1/captures/img_a1_png" \
  -o "$ACC/out/a1_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/img_a1_png/content" \
  -o "$ACC/out/a1_content.json" -w 'content HTTP %{http_code}\n'
```

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/a1_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/a1_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/a1_content.json" "$ACC/in/text.png"
```

| поле | ожидание |
| --- | --- |
| upload / capture / record / content | `200` / `201` / `200` / `200` |
| `record.status` | `complete` |
| `record.error` | `None` |
| `record.payload_type` | `image` |
| `content.type` | `image` |
| `assets` | ровно **1**, `role=original`, `mime=image/png` |
| `processing` | `image@0.1`, `status=complete` |
| `segments` | ровно **0** |
| `metadata.image` | `{"encoded_format": "png", "encoded_width": 978, "encoded_height": 232}` |
| `metadata.image_ocr` | **`КЛЮЧА НЕТ`** |
| `BYTES IDENTICAL` | `True` |

### IMG-A2 — JPEG

```bash
curl -sS -F "file=@$ACC/in/structural.jpg;type=image/jpeg" "$API/v1/uploads" \
  -o "$ACC/out/a2_upload.json" -w 'upload HTTP %{http_code}\n'
REF_A2=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a2_upload.json")

"$PY" "$ACC/bin/envelope.py" img_a2_jpeg "$JPEG" "$REF_A2" > "$ACC/out/a2_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a2_envelope.json" \
  -o "$ACC/out/a2_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/img_a2_jpeg" \
  -o "$ACC/out/a2_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/img_a2_jpeg/content" \
  -o "$ACC/out/a2_content.json" -w 'content HTTP %{http_code}\n'
```

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/a2_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/a2_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/a2_content.json" "$ACC/in/structural.jpg"
```

Ожидания те же, с двумя отличиями:

| поле | ожидание |
| --- | --- |
| `assets` | `role=original`, `mime=image/jpeg` |
| `metadata.image` | `{"encoded_format": "jpeg", "encoded_width": 640, "encoded_height": 360}` |

**Провал, если:** любой код ответа отличается от ожидаемого; `status` не
`complete`; `type` не `image`; ассетов не ровно один; сегментов не ноль;
`encoded_format`, `encoded_width` или `encoded_height` не совпадают с тем, что
показал `headers.py` на шаге 3 (в частности, если ширина и высота поменялись
местами); появился ключ `metadata.image_ocr`; процессор не `image@0.1`;
`BYTES IDENTICAL: False`.

**Что прислать:** для каждой из двух подпроверок — четыре HTTP-кода, `status`,
`type`, число ассетов, тройку `encoded_format` / `encoded_width` /
`encoded_height`, число сегментов, имя процессора, строку про
`metadata.image_ocr` и `BYTES IDENTICAL`.

> Ошибочные входы здесь руками не проверяются. Испорченный PNG, обрезанный
> файл, PNG, заявленный как JPEG, отклонённые форматы, ссылка на
> неположенный материал и путь в файловой системе вместо ссылки — всё это
> полностью покрыто автоматическими наборами и от машины к машине не
> меняется.

---

## Шаг 5. Перезапустить сервер с `--image-ocr`

**Это вторая из трёх жизней сервера.** Каталог данных — **тот же**.

В терминале сервера остановите процесс (`Ctrl+C`) и запустите заново:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8792 --image-ocr
```

Набор `[ocr]` для этого **не нужен и не ставится**: изображение уходит движку
как есть, и UniMem ничего не декодирует сам.

Если Tesseract, `eng` или `rus` отсутствуют, сервер **не запустится** и скажет,
чего не хватает. Это правильное поведение, а не дефект: сборка с `--image-ocr`
никогда не поднимается с тихо выключенным распознаванием. В этом случае IMG-B,
IMG-C и IMG-E получают `BLOCKED`, а IMG-D остаётся `NOT_RUN` или `BLOCKED`,
потому что читать после перезапуска будет нечего сверх IMG-A. Поставьте
недостающее сами и начните прогон заново.

```bash
curl -sS "$API/health"; echo
```

Проверьте заодно, что данные шага 4 пережили этот перезапуск и читаются новым
процессом:

```bash
curl -sS -o /dev/null -w 'A1 record %{http_code}\n' "$API/v1/captures/img_a1_png"
curl -sS -o /dev/null -w 'A2 record %{http_code}\n' "$API/v1/captures/img_a2_jpeg"
```

Оба — `200`. Это ещё не IMG-D (тот проверяет и содержимое, и снимки целиком), но
если здесь уже `404`, дальше идти незачем: значит, сервер поднялся над другим
каталогом данных.

---

## IMG-B. Настоящее локальное распознавание, детерминированный текст

**Что проверяется:** на **вашей** машине, **вашим** Tesseract, распознавание
добавляет к изображению ровно один сегмент — и **больше не меняет ничего**.
Оригинал остаётся каноническим, структурные наблюдения остаются прежними.

Берутся **те же байты** `text.png`, что и в IMG-A1, но под **новым** capture id.
Слово на картинке вы уже прочитали глазами на шаге 3.

```bash
curl -sS -F "file=@$ACC/in/text.png;type=image/png" "$API/v1/uploads" \
  -o "$ACC/out/b_upload.json" -w 'upload HTTP %{http_code}\n'
REF_B=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/b_upload.json")
```

`file_ref` будет **тем же самым**, что в IMG-A1: хранилище адресуется
содержимым, одни и те же байты дают одну и ту же ссылку. Это нормально и
ожидаемо — новым здесь является capture id, а не материал.

```bash
"$PY" "$ACC/bin/envelope.py" img_b_ocr_text "$PNG" "$REF_B" > "$ACC/out/b_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/b_envelope.json" \
  -o "$ACC/out/b_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/img_b_ocr_text" \
  -o "$ACC/out/b_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/img_b_ocr_text/content" \
  -o "$ACC/out/b_content.json" -w 'content HTTP %{http_code}\n'
```

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/b_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/b_content.json"
```

```bash
"$PY" "$ACC/bin/same_image_metadata.py" "$ACC/out/a1_content.json" "$ACC/out/b_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/b_content.json" "$ACC/in/text.png"
```

| поле | ожидание |
| --- | --- |
| capture / record / content | `201` / `200` / `200` |
| `record.status` | `complete` |
| `content.type` | `image` |
| `segments` | ровно **1** |
| сегмент: `type` | `ocr` |
| сегмент: `source_type` | `ocr` |
| сегмент: `position` | `0` |
| сегмент: `spatial` | `None` — ни страницы, ни рамки |
| сегмент: `processor` | `image-ocr@0.1` |
| текст сегмента | содержит слово, которое вы прочитали на картинке |
| `assets` | по-прежнему ровно **1**, `role=original` |
| `metadata.image` | **посимвольно то же, что в IMG-A1** |
| `image_ocr.engine_invoked` | `true` |
| `image_ocr.engine` | `tesseract` |
| `image_ocr.engine_version` | версия **вашей** установки |
| `image_ocr.settings.languages` | `eng+rus` |
| `image_ocr.settings.dpi_supplied` | `false` |
| `IMAGE METADATA IDENTICAL` | `True` |
| `BYTES IDENTICAL` | `True` |

Текст сравнивайте **по слову, а не посимвольно**: движок может вернуть его с
иным регистром, пробелами или переводом строки, и это не дефект. Дефект — если
слова нет.

> `dpi_supplied: false` здесь не мелочь. У PDF-пути в аргументах стоит
> `--dpi 300`, потому что растеризация действительно происходила. Отправленное
> изображение здесь никто не растеризовал и его физическую плотность не читал,
> поэтому никакого числа не заявляется.

**Провал, если:** сервер с `--image-ocr` не запустился при установленных
Tesseract, `eng` и `rus`; capture не `201`; `status` не `complete`; сегментов не
ровно один; `type` или `source_type` не `ocr`; `position` не `0`; `spatial` не
пуст; процессор не `image-ocr@0.1`; `engine_invoked` не `true`; `languages` не
`eng+rus`; `dpi_supplied` не `false`; `IMAGE METADATA IDENTICAL: False`;
ассетов стало больше одного; `BYTES IDENTICAL: False`; в тексте нет слова,
которое вы видите на картинке.

**Что прислать:** код capture, `status`, число сегментов, распознанный
синтетический токен (он синтетический — присылать его безопасно), `type` /
`source_type` / `position` / `spatial` сегмента, `engine_invoked`, `engine`,
`engine_version`, `languages`, `dpi_supplied`, строку
`IMAGE METADATA IDENTICAL` и `BYTES IDENTICAL`.

---

## IMG-C. Отказ по ресурсной политике на штатном пределе

**Что проверяется:** изображение, которое эта сборка не берётся интерпретировать,
всё равно **принимается целиком**. Не `422`, не `503`, не `PARTIAL` — а `201`,
`COMPLETE` и записанная причина отказа от обогащения.

Эта строка существует потому, что **настоящий `TesseractImageOcr` на настоящем
сервере при штатном пределе 20 000 000 не отказывал никогда**: в модульных
тестах предел искусственно занижен и движка нет вовсе, а в тестах над HTTP
отказ изображает подменный распознаватель. Здесь всё настоящее.

Сервер — **тот же**, с `--image-ocr`. Файл — `oversize-header.png`, чей
заголовок заявляет 5000 × 5000 = 25 000 000 пикселей.

```bash
curl -sS -F "file=@$ACC/in/oversize-header.png;type=image/png" "$API/v1/uploads" \
  -o "$ACC/out/c_upload.json" -w 'upload HTTP %{http_code}\n'
REF_C=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/c_upload.json")

"$PY" "$ACC/bin/envelope.py" img_c_pixel_skip "$PNG" "$REF_C" > "$ACC/out/c_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/c_envelope.json" \
  -o "$ACC/out/c_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/img_c_pixel_skip" \
  -o "$ACC/out/c_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/img_c_pixel_skip/content" \
  -o "$ACC/out/c_content.json" -w 'content HTTP %{http_code}\n'
```

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/c_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/c_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/c_content.json" "$ACC/in/oversize-header.png"
```

| поле | ожидание |
| --- | --- |
| capture / record / content | `201` / `200` / `200` |
| `record.status` | `complete` |
| `content.type` | `image` |
| `segments` | ровно **0** |
| `metadata.image` | `{"encoded_format": "png", "encoded_width": 5000, "encoded_height": 5000}` |
| `image_ocr.engine_invoked` | `false` |
| `image_ocr.skipped_reason` | `encoded_pixel_limit` |
| `image_ocr.max_encoded_pixels` | `20000000` |
| `image_ocr.engine` | **отсутствует** |
| `image_ocr.engine_version` | **отсутствует** |
| `image_ocr.settings` | **отсутствует** |
| `BYTES IDENTICAL` | `True` |

Полный ключ `image_ocr` должен состоять ровно из трёх полей:

```jsonc
{
  "engine_invoked": false,
  "max_encoded_pixels": 20000000,
  "skipped_reason": "encoded_pixel_limit"
}
```

Ничего не запускалось — значит, фактов о запуске быть не должно.

### Три вещи, которые нельзя путать

Положите рядом вывод IMG-A1, IMG-B и IMG-C: это три **разных** объекта, и
различать их по числу сегментов нельзя.

| | что видно | что это значит |
| --- | --- | --- |
| **IMG-A1** | ключа `image_ocr` нет вовсе | сборка без распознавания; движок не спрашивали |
| **IMG-B** | `engine_invoked: true`, один сегмент | движок отработал и нашёл текст |
| **IMG-C** | `engine_invoked: false` + `skipped_reason` | движок не запускался: предел перейден до него |

Есть и четвёртый случай — движок отработал и не нашёл непробельного текста:
`engine_invoked: true` и `segments: []`. Внешне это совпадает с IMG-C по числу
сегментов и различается **только** значением `engine_invoked`. Этот случай
проверяется автоматически, настоящим движком, в задаче CI `Local image OCR`.

**Провал, если:** capture не `201`; `status` не `complete`; content не `200`;
сегментов не ноль; `engine_invoked` не `false`; `skipped_reason` не
`encoded_pixel_limit`; `max_encoded_pixels` не `20000000`; в `image_ocr`
присутствует `engine`, `engine_version` или `settings`; `BYTES IDENTICAL:
False`. Отдельно провалом является **любой** из ответов `422`, `503` или статус
`partial`: изображение остаётся полноценным независимо от того, может ли эта
сборка позволить себе его интерпретировать.

**Что прислать:** код capture, `status`, число сегментов, ключ `image_ocr`
целиком, явную строку об отсутствии `engine` / `engine_version` / `settings` и
`BYTES IDENTICAL`.

> Предела по байтам (64 МиБ) здесь намеренно нет: семантика у него ровно та же
> — `engine_invoked: false` и `skipped_reason`, — а стоит он файла на 64 мегабайта
> у вас на диске. Предел по пикселям даёт тот же результат за 68 байт и
> срабатывает в адаптере раньше.

---

## IMG-E. Одно настоящее изображение владельца

**Что проверяется:** единственное, что нельзя поручить машине. Синтетические
контроли проверяют продукт — они **не** доказывают, что прошло **ваше**
изображение. Судьёй здесь является человек, который видит картинку.

Сервер — **тот же**, с `--image-ocr`. Этот сценарий идёт раньше IMG-D, потому
что ему нужен именно этот сервер, а перезапуск обязан быть последним.

Возьмите **одно** настоящее изображение PNG или JPEG, на котором есть текст,
который вы сами можете прочитать. Русский текст — **желателен, если он у вас
под рукой**, но **не обязателен**: закрытие фазы не должно зависеть от того,
есть ли у вас образец на конкретном языке.

```bash
MY="/путь/к/вашему/изображению.png"     # ваш путь; никуда не отправляется
MY_MIME="image/png"                      # или image/jpeg

curl -sS -F "file=@$MY;type=$MY_MIME" "$API/v1/uploads" \
  -o "$ACC/out/e_upload.json" -w 'upload HTTP %{http_code}\n'
REF_E=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/e_upload.json")

"$PY" "$ACC/bin/envelope.py" img_e_own_01 "$MY_MIME" "$REF_E" > "$ACC/out/e_envelope.json"
curl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/e_envelope.json" \
  -o "$ACC/out/e_capture.json" -w 'capture HTTP %{http_code}\n'

curl -sS "$API/v1/captures/img_e_own_01" \
  -o "$ACC/out/e_record.json" -w 'record HTTP %{http_code}\n'
curl -sS "$API/v1/captures/img_e_own_01/content" \
  -o "$ACC/out/e_content.json" -w 'content HTTP %{http_code}\n'
```

### Смотрите локально

Эта команда печатает **распознанный текст вашего изображения**. Она для ваших
глаз:

```bash
"$PY" "$ACC/bin/show.py" content "$ACC/out/e_content.json"
```

Откройте рядом само изображение и сравните:

```bash
xdg-open "$MY"
```

На что смотреть:

- совпадает ли распознанное с тем, что вы читаете на картинке;
- сегмент один, `type=ocr`, `source_type=ocr`, `position=0`, `spatial` пуст;
- `engine_invoked: true`;
- оригинал не переписан.

### Условия `PASS` для IMG-E

Суждение человека — **последнее** из условий, а не единственное. IMG-E
существует, чтобы проверить **состоявшееся** распознавание, поэтому `PASS`
требует, чтобы движок действительно отработал и действительно что-то вернул.
`PASS` ставится, только если выполнено **всё**:

| | условие |
| --- | --- |
| 1 | capture — `201` |
| 2 | `record.status` — `complete` |
| 3 | content — `200` |
| 4 | `image_ocr.engine_invoked` — `true` |
| 5 | сегментов — ровно **1** |
| 6 | сегмент: `type` — `ocr` |
| 7 | сегмент: `source_type` — `ocr` |
| 8 | сегмент: `position` — `0` |
| 9 | сегмент: `spatial` — пусто (`None`) |
| 10 | длина текста сегмента — **больше нуля** |
| 11 | `BYTES IDENTICAL` — `True` |
| 12 | вы считаете распознанное приемлемым для этого изображения |

**`FAIL`, если** любое из условий 1–3, 5–11 не выполнено при
`engine_invoked = true` — или если условие 12 не выполнено. В частности: движок
отработал (`engine_invoked: true`), а непробельного `ocr`-сегмента нет, **хотя
текст на изображении вы читаете глазами**, — это `FAIL`, а не «так вышло».

**`BLOCKED`, если `engine_invoked` оказался `false`.** Тогда движок не
запускался — например, ваше изображение крупнее штатного предела в 20 000 000
пикселей и было пропущено ресурсной политикой. Продукт при этом сработал
правильно (`201`, `complete`, `segments: []`, `skipped_reason`), и записывать
это как `FAIL` было бы неправдой. Но и `PASS` тоже: **IMG-E в таком прогоне не
проверил распознавание вообще.**

Что делать при `BLOCKED`: возьмите **другое** настоящее изображение PNG или
JPEG, укладывающееся в отгруженные ресурсные пределы, и выполните **новый
полный прогон приёмки** с шага 0 — новый каталог прогона, новые capture id.
Досдавать одну строку в уже выполненный прогон нельзя: таблица результатов
описывает один прогон целиком.

**Ничего не подкручивайте, чтобы превратить `BLOCKED` или `FAIL` в `PASS`** —
ни пределов, ни политики распознавания.

> **Ничего не подкручивайте, чтобы добиться `PASS`.** Ни PSM, ни DPI, ни выбор
> языков, ни deskew, ни ориентацию, ни повторы. Принимается именно та
> фиксированная политика, которую фаза отгрузила. PSM 3 настроен на страницу
> документа, и README прямо предупреждает: на фотографиях и разрозненном
> тексте в кадре результат может быть хуже, чем дал бы специализированный
> режим. Плохой результат на таком изображении — это честная находка о
> зафиксированной политике, и её следует записать, а не обойти.

### Что именно отправлять по своему изображению

Ваше изображение никуда наружу не уходит: всё выполняется на вашей машине
против локального сервера на `127.0.0.1`, и ни один шаг не обращается к внешнему
сервису. Но **отправка вывода — это отдельный вопрос.**

> `show.py content` печатает распознанный текст целиком. Для вашего изображения
> это его содержимое. Такой вывод пересылать нельзя.

Для отправки есть режимы, которые печатают **закрытый список** полей. Ничего
вырезать вручную не нужно — запрещённого в их выводе нет по построению:

```bash
"$PY" "$ACC/bin/show.py" record-fields   "$ACC/out/e_record.json"
"$PY" "$ACC/bin/show.py" content-fields  "$ACC/out/e_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/e_content.json" "$MY" --quiet
```

Вместе они печатают ровно следующее, и ничего сверх того:

- `capture id` (его задало это руководство) и `status` записи;
- число сегментов;
- по каждому сегменту: `type`, `source_type`, `position`, `spatial` и **длину**
  текста;
- `image_ocr.engine_invoked` — и `skipped_reason`, если он есть;
- одну строку `BYTES IDENTICAL`: `True`, `False` или `CHECK_FAILED`.

> `CHECK_FAILED` означает, что сверку не удалось **выполнить** — например,
> исходный файл больше не по тому пути или ссылку на оригинал не удалось
> разобрать. Подробностей эта строка не содержит намеренно: они приватны.
> Посмотрите их локально тем же скриптом **без** `--quiet` и не присылайте
> этот вывод. `CHECK_FAILED` — это не `PASS`: строка остаётся `BLOCKED`, пока
> сверка не выполнена.

К этому добавьте от себя:

- HTTP-коды четырёх запросов (их печатал `curl -w`);
- одну фразу: **«распознанное совпадает с тем, что я читаю на изображении: да /
  частично / нет»**.

**И больше ничего.** Обычные режимы `record` и `content` показали бы дайджест,
`ref`, mime-тип, `encoded_format`, размеры, `title` и сам распознанный текст —
поэтому для отправки берутся именно `-fields` и `--quiet`, а не эти.

**Не отправляйте:** само изображение, его имя файла, локальный путь, URL,
дайджест или `ref`, `title`, `encoded_format`, размеры изображения, любой вывод
`show.py content` или `show.py record`, выдержку распознанного текста,
EXIF/XMP/ICC, произвольную `metadata` и описание того, что на картинке. В
долговечной записи репозитория это изображение будет названо **только** как
«одно настоящее изображение владельца» — и ничем больше.

Если дефект нельзя показать без содержимого — скажите об этом словами, и
решение о том, что раскрывать, останется за вами.

**Провал, если:** не выполнено любое из условий 1–3 и 5–12 таблицы выше при
`engine_invoked = true` — то есть capture не `201`; `status` не `complete`;
content не `200`; сегментов не ровно один; `type` или `source_type` не `ocr`;
`position` не `0`; `spatial` не пуст; длина текста ноль при видимом глазом
тексте на изображении; `BYTES IDENTICAL: False`; либо вы как человек считаете
распознанное неприемлемым для этого изображения.

**Не провал, а `BLOCKED`:** `engine_invoked = false` — распознавание не
запускалось, и этот прогон IMG-E ничего о нём не сказал. Порядок действий — в
таблице условий выше.

---

## IMG-D. Настоящий перезапуск процесса и обратное чтение

**Что проверяется:** захваченное вчера читается сегодня. Один перезапуск на весь
прогон, в самом конце — не по перезапуску после каждого сценария.

Второй процесс поднимается над **тем же** каталогом данных и **без**
`--image-ocr`. Это делает видимым второй факт: содержимое, созданное сборкой с
распознаванием, читается сборкой **без** него.

> **Точная формулировка того, что этот шаг доказывает.** Он доказывает, что
> содержимое, созданное при включённом распознавании, полностью читается
> процессом, запущенным **без** `--image-ocr`. Он **не** доказывает, что
> Tesseract отсутствует или недоступен: движок, скорее всего, по-прежнему
> установлен на вашей машине. Границу зависимостей — что распознавание
> изображений работает без `pypdfium2` и без `Pillow` — отдельно доказывает
> задача CI `Local image OCR`, которая эти пакеты удаляет и проверяет их
> отсутствие через `importlib`.

### Д1. Снять состояние до перезапуска

Сервер всё ещё работает с `--image-ocr`. Снимите снимки **всех пяти** capture id
этого прогона — и тела ответов, и HTTP-коды:

```bash
for id in img_a1_png img_a2_jpeg img_b_ocr_text img_c_pixel_skip img_e_own_01; do
  for kind in "" "/content"; do
    name=$([ -z "$kind" ] && echo record || echo content)
    code=$(curl -sS -o "$ACC/snap/before_${id}_${name}.json" \
                 -w '%{http_code}' "$API/v1/captures/$id$kind")
    echo "$code" > "$ACC/snap/before_${id}_${name}.status"
    echo "before $id $name $code"
  done
done
```

Все десять строк должны показать `200`. Если IMG-E вы не выполняли, строки
`img_e_own_01` покажут `404` — тогда просто исключите этот id из списка здесь и
в Д3, а IMG-E останется `NOT_RUN` и фаза закрыта быть не может.

> Снимки в `$ACC/snap` — это полные тела ответов, и для `img_e_own_01` они
> содержат распознанный текст вашего изображения. Они остаются у вас локально.
> Наружу из этого шага уходит только вывод `restart_check.py`, который печатает
> вердикты и не печатает ни текста, ни дайджестов, ни имён файлов.

### Д2. Остановить процесс и запустить заново

**Это третья и последняя жизнь сервера.**

В терминале сервера: `Ctrl+C`, дождитесь, что процесс действительно завершился,
и запустите заново **тем же** `--data-dir` и **без** `--image-ocr`:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --port 8792
```

```bash
curl -sS "$API/health"; echo
```

Никаких `--pdf-ocr`, `--image-ocr` и другого каталога данных. Каталог
`$ACC/data` не трогайте, не чистите и не копируйте.

### Д3. Снять состояние после и сравнить

```bash
for id in img_a1_png img_a2_jpeg img_b_ocr_text img_c_pixel_skip img_e_own_01; do
  for kind in "" "/content"; do
    name=$([ -z "$kind" ] && echo record || echo content)
    code=$(curl -sS -o "$ACC/snap/after_${id}_${name}.json" \
                 -w '%{http_code}' "$API/v1/captures/$id$kind")
    echo "$code" > "$ACC/snap/after_${id}_${name}.status"
    echo "after  $id $name $code"
  done
done
```

Если вы делали перерыв, `MY` из IMG-E мог не сохраниться — задайте его снова
тем же путём, иначе последняя строка проверит пустой путь:

```bash
echo "MY=${MY:-<не задан>}"
```

```bash
"$PY" "$ACC/bin/restart_check.py" "$ACC/data" "$ACC/snap" \
  "img_a1_png:$ACC/in/text.png" \
  "img_a2_jpeg:$ACC/in/structural.jpg" \
  "img_b_ocr_text:$ACC/in/text.png" \
  "img_c_pixel_skip:$ACC/in/oversize-header.png" \
  "img_e_own_01:$MY"
```

Убедитесь отдельно, что распознанное действительно читается сборкой без
распознавания:

```bash
"$PY" "$ACC/bin/show.py" content "$ACC/out/b_content.json" > "$ACC/out/b_before.txt"
"$PY" "$ACC/bin/show.py" content "$ACC/snap/after_img_b_ocr_text_content.json" \
  > "$ACC/out/b_after.txt"
diff -u "$ACC/out/b_before.txt" "$ACC/out/b_after.txt" && echo "IMG-B: вывод совпал"
```

| | Проверка |
| --- | --- |
| 1 | до перезапуска все `GET` вернули `200` — иначе `BLOCKED` |
| 2 | после перезапуска все `GET` снова `200`, id те же, статусы снова `complete` |
| 3 | снимки `CaptureRecord` не изменились ни в одном поле |
| 4 | снимки `ContentObject` не изменились ни в одном поле |
| 5 | у IMG-B сегмент `ocr` и его текст на месте — прочитаны процессом **без** `--image-ocr` |
| 6 | у IMG-C ключ `image_ocr` со `skipped_reason` на месте |
| 7 | у IMG-A ключа `image_ocr` по-прежнему нет |
| 8 | оригиналы побайтово те же во всех строках |
| 9 | `ИТОГ ПРОВЕРКИ: PASS` |

**Провал, если:** любой `GET` после перезапуска вернул не `200`; статус
изменился; снимок записи или содержимого изменился в любом поле; сегмент `ocr`
у IMG-B пропал или изменился; `image_ocr` у IMG-C пропал; у IMG-A появился
`image_ocr`; оригинал отличается от отправленного; `ИТОГ ПРОВЕРКИ` не `PASS`.

**Что прислать:** вывод `restart_check.py` целиком (он компактен и безопасен для
строки с изображением владельца: ни дайджестов, ни ссылок, ни имён файлов — в
том числе и когда проверка отказывает), строку `IMG-B: вывод совпал` и одну
строку, подтверждающую, что второй сервер запущен с **тем же** `--data-dir` и
**без** `--image-ocr`.

> Вердикт `BLOCKED` с пометкой «проверку целостности выполнить не удалось»
> означает, что проверка не смогла **состояться** — исходный файл не читается,
> ссылка на оригинал не разбирается, raw-store не отвечает. Это не вывод о
> продукте: IMG-D остаётся `BLOCKED`, пока проверка не выполнена. Категория
> отказа названа без значений намеренно; смотрите подробности локально.

---

## Завершение прогона

Остановите сервер (`Ctrl+C`). Каталог `$ACC` — это ваши доказательства:
оставьте его как есть. Ничего удалять не нужно: ни одна команда выше ничего не
удаляла, и удалять что-либо теперь тоже не требуется.

Обычная установка UniMem всё это время не читалась и не менялась: у неё свой
каталог данных и свой порт.

---

## Результат приёмки владельцем

Заполняется **владельцем** после прогона. Сейчас прогона не было.

- **Дата:** —
- **Базовая версия:** — (точный `git rev-parse HEAD` из шага 0; это слитый
  `main`, содержащий это руководство)
- **Окружение:** — (дистрибутив, Python, версия Tesseract, наличие `eng` и
  `rus`)
- **Итог:** `0 из 5` — прогон не выполнялся.
- **Набор `[ocr]`:** чек-лист не требовал и не устанавливал его.
- **Набор `[ocr]` на машине владельца:** — (наблюдение из шага 0; на статусы
  строк не влияет).

## Таблица результатов

Статусы ставит **только владелец**. Допустимые значения: `NOT_RUN`, `PASS`,
`FAIL`, `BLOCKED`. Новый полный прогон начинает свою таблицу заново с
`NOT_RUN`.

| Сценарий | Статус владельца | Доказательство | Заметки |
| --- | --- | --- | --- |
| IMG-A | NOT_RUN | — | приём PNG и JPEG в обычном режиме |
| IMG-B | NOT_RUN | — | локальное распознавание, детерминированный текст |
| IMG-C | NOT_RUN | — | отказ по пределу пикселей на штатном значении |
| IMG-D | NOT_RUN | — | настоящий перезапуск процесса и обратное чтение |
| IMG-E | NOT_RUN | — | одно настоящее изображение владельца |

Шаг с контрольными изображениями — **подготовка, а не шестая строка**: без него
IMG-A, IMG-B и IMG-C нечем выполнять, но отдельного вердикта у него нет.

> **Macro Phase 4 ОТКРЫТА.** Эта таблица пуста, и ничего в этом файле не
> утверждает обратного.

---

## Что требуется для закрытия Macro Phase 4

Репозиторий сможет правдиво сказать «Macro Phase 4 закрыта» только когда
выполнено **всё** перечисленное:

1. **IMG-A, IMG-B, IMG-C, IMG-D и IMG-E — все пять `PASS`.** Ни одного
   `NOT_RUN`, ни одного `FAIL`, ни одного `BLOCKED`. `BLOCKED` — не разновидность
   прохождения: отсутствие Tesseract, `eng` или `rus` закрытие останавливает.
2. Прогон был **ручным, человеком за клавиатурой на локальной машине** — не CI,
   не облачная сессия, не автоматика, — и записан именно так.
3. Записан **точный SHA слитого `main`**, на котором прогон выполнялся, и этот
   `main` содержит настоящее руководство.
4. **Доказательства CI и доказательства владельца остаются раздельными.** Ничто
   не выдаёт прогон владельца за результат CI и ничто не выдаёт результаты CI за
   ручную проверку.
5. Использовано **одно настоящее изображение владельца**, и в долговечной записи
   оно названо только так: ни имени файла, ни локального пути, ни URL, ни
   дайджеста и `ref`, ни текста и выдержки, ни `title`, ни `encoded_format`, ни
   размеров, ни EXIF/XMP/ICC, ни произвольной `metadata`, ни описания
   содержимого.
6. Записано, что **чек-лист не требовал и не устанавливал набор `[ocr]`**. Это
   утверждение о руководстве, а **не** о машине: требования «`[ocr]` отсутствовал»
   здесь нет. Если набор у владельца был установлен, приёмка остаётся
   действительной — просто она не доказывает границу зависимостей, и эту границу
   доказывает задача CI `Local image OCR`, а не она.
7. **Закрытие не добавляет ни одной возможности.** Ни формата, ни маршрута, ни
   поля контракта, ни enum, ни состояния жизненного цикла, ни зависимости, ни
   задачи CI, ни теста.
8. `SCHEMA_VERSION` остаётся `0.2`, и совместимость не трогается.
9. **Семантика Phase 4A и Phase 4B не пересматривается:** `image@0.1` и
   `image-ocr@0.1`, пределы 20 000 000 пикселей и 64 МиБ, фиксированная политика
   `eng+rus` / OEM 1 / PSM 3 / без `--dpi`, и `503 image_ocr_unavailable`
   остаются ровно такими, какими были отгружены.
10. [ADR-019](ADR/ADR-019-still-image-ingestion.md) и
    [ADR-020](ADR/ADR-020-opt-in-local-image-ocr.md) **не редактируются**: каждый
    описан по своему PR и был точен, когда писался. Принятое решение не
    переписывают из-за того, что сдвинулась контрольная точка.
11. Закрывающее изменение — **только документация**.
12. Проверки качества на закрывающем изменении проходят.

Фазы 0, 1, 2 и 3 закрыты и закрытыми остаются; это закрытие их не касается.

---

## Как это руководство появляется и как закрывается фаза

Phase 4C состоит из двух изменений, и оба — только документация.

1. **Это изменение** добавляет настоящий файл и одну ссылку на него из README.
   Оно **не** меняет статус фазы, **не** трогает `docs/ARCHITECTURE.md`, ADR,
   код, тесты, CI, зависимости, контракты и схему. После него Macro Phase 4
   по-прежнему **ОТКРЫТА**.
2. Владелец берёт слитый `main`, содержащий это руководство, и проходит его
   руками. Тот самый SHA и становится базовой версией приёмки.
3. **Второе изменение** записывает результат владельца в этот файл и, если все
   пять строк `PASS`, закрывает Macro Phase 4 в `docs/ARCHITECTURE.md`.

Никакой Phase 4D нет и не предполагается.

---

## Известные ограничения этого чек-листа

Это список того, чего прогон **не** доказывает, — чтобы его результат не читали
шире, чем он есть:

- Он не проверяет ошибочные входы: испорченные файлы, противоречие между
  заявленным типом и заголовком, отклонённые форматы, неположенные ссылки. Это
  полностью покрыто автоматически.
- Он не проверяет `503 image_ocr_unavailable`. Достичь его без разрушительных
  действий нельзя: при отсутствующем движке сервер с `--image-ocr` просто не
  стартует, а испорченные пиксельные данные один движок отвергает, а другой
  возвращает пустым результатом. Это отображение на границе доставки, и
  автоматические наборы проверяют его именно там — с фиксированным кодом,
  фиксированным текстом, отсутствием утечек, `PROCESSING` у записи и
  отсутствием содержимого.
- Он не проверяет предел по байтам в 64 МиБ отдельно от предела по пикселям.
- Он не проверяет изображение, на котором движок отработал и не нашёл текста:
  это делает настоящий Tesseract в задаче CI `Local image OCR`.
- Он ничего не говорит о форматах, которых Phase 4 не отгружала: GIF, WebP,
  APNG и покадровая интерпретация, EXIF/XMP/ICC, коррекция ориентации, рамки,
  раскладка, оценки уверенности, определение языка, рукописный ввод, подписи и
  зрение, снимки экрана и буфер обмена, эмбеддинги и поиск.
- Прохождение синтетических контролей не означает, что прошло ваше изображение;
  прохождение вашего изображения не означает, что пройдёт следующее.
