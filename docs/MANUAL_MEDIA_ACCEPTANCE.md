# Ручная приёмка медиа (Macro Phase 5A)

> **Обновление B1:** HTTP теперь требует явный bearer token. Команды ниже
> используют `ucurl` и приватный `$ACC/api.token`; настройки добавлены в `env.sh`.
> Это обновление подключения, а не повторное открытие прежней ручной приёмки.
> Исторические результаты остаются прежними. Лимиты: JSON 8 MiB,
> весь multipart 512 MiB; [контракт защиты и восстановления](LOCAL_DELIVERY.md).


Этот документ — исполняемый чек-лист для владельца. Он проверяет **то, что уже
построено**: приём аудио и видео без интерпретации, с описанием структуры
контейнера локальным `ffprobe` (Phase 5A, срезы 1–3).

Он ничего не добавляет к продукту. Ни один шаг ниже не требует нового формата,
нового маршрута, нового поля контракта и новой настройки: используются только
`POST /v1/uploads`, `POST /v1/captures`, `GET /v1/captures/{id}`,
`GET /v1/captures/{id}/content` и `GET /health`, а из ключей командной строки —
только `--data-dir`, `--port` и `--media`.

Поведение продукта описано в [README](../README.md) (разделы *Quick start* и
*Audio and video: opt-in local structural probing*) и в
[ADR-021](ADR/ADR-021-original-first-time-based-media-ingestion.md),
[ADR-022](ADR/ADR-022-engine-independent-media-processing.md),
[ADR-023](ADR/ADR-023-local-ffprobe-media-capability.md). Здесь оно не
пересказывается — здесь его проверяют.

> **Сценарии называются MED-A … MED-F.** Это не `IMG-A … IMG-E` из
> [ручной приёмки изображений](MANUAL_IMAGE_ACCEPTANCE.md), не `DOC-A … DOC-G`
> из [ручной приёмки документов](MANUAL_DOCUMENT_ACCEPTANCE.md) и не браузерные
> A–G из README. Результаты приёмки Macro Phase 2, 3 и 4 сюда не переносятся, а
> результаты этого прогона не переносятся туда.

> **Macro Phase 5A ОТКРЫТА.** Приёмка владельца по этому чек-листу **ещё не
> выполнялась**: [таблица результатов](#таблица-результатов) целиком в
> `NOT_RUN`, а [история прогонов](#история-прогонов-владельца) пуста. Никакая
> строка здесь не заполнена заранее и ни один результат CI не выдан за результат
> владельца.

## Что здесь проверяется, а что уже проверено автоматически

Автоматические наборы покрывают код, и покрывают его широко. Задача CI
`Local media probing` поднимает настоящий FFmpeg и проверяет **настоящим**
`ffprobe`:

- все поддержанные семейства — WAV, MP3, OGG, MP4, WebM — включая видео со
  звуком и видео с субтитровой дорожкой;
- настоящий HTTP к настоящему процессу сервера, до долговечного `COMPLETE`;
- псевдонимы контейнеров (ISO base media — это `mov`, `mp4`, `m4a`… сразу);
- длительности, кодеки, частоты дискретизации, число каналов, размеры кадра и
  рациональный кадровый темп;
- классификацию отказов: расхождение заявления и наблюдения — `422` и
  долговечный `FAILED`; отсутствие доверенного результата — фиксированный `503`
  и **не**терминальное состояние записи;
- поведение стартового предусловия: и версия движка, и поддержка входного
  протокола `fd`;
- подачу байтов через `fd:` и однопротокольный whitelist;
- перезапуск процесса и обратное чтение — в автоматическом виде;
- старт обычного сервера с намеренно опустошённым `PATH`.

Пакет в этой задаче ставится **без дополнительных наборов**, чем и доказывается,
что Python-зависимости у медиа нет. Отсутствие инверсии зависимостей — `core` не
импортирует `unimem_media`, не знает его имени и не может быть заставлен его
загрузить — проверяют обычные наборы в задаче `Quality gates`, на машине без
FFmpeg вовсе.

**Всё это здесь не переписывается от руки.** Этот чек-лист — не ручной повтор
автоматического набора.

Эта приёмка проверяет другое — то, до чего автоматика не дотягивается:

- документированные команды оператора работают **на вашей машине**, а не внутри
  `pytest`;
- **разделение возможностей развёртывания** видно оператору: обычная установка
  без `--media` остаётся ровно такой, какой была, и медиа не принимает;
- разбор контейнера делает **ваш** FFmpeg, его сборки и его версии;
- на **настоящем** файле владельца структурное описание оценивает **человек**,
  потому что другого судьи для этого нет;
- сохранённое переживает **настоящий перезапуск процесса** над тем же каталогом
  данных и читается сборкой **без** capability.

## Как этим пользоваться

- Идите по шагам сверху вниз. Каждый шаг — отдельная короткая команда, а не один
  скрипт, который делает всё сразу: если что-то пойдёт не так, должно быть видно
  **где**.
- Каждый сценарий заканчивается разделом **«Провал, если»** и
  **«Что прислать»**. Присылайте именно это, а не весь вывод сервера. Логи
  сервера целиком не нужны и не запрашиваются.
- Статусы в [таблице результатов](#таблица-результатов) ставит **только
  владелец**. Допустимые значения: `NOT_RUN`, `PASS`, `FAIL`, `BLOCKED`.
- Ни один шаг здесь не удаляет и не перезаписывает ваши данные. Если шаг
  предлагает что-то, чего вы не понимаете, остановитесь и спросите, а не
  выполняйте.
- **Оболочка.** Команды рассчитаны на вставку в обычный интерактивный терминал —
  `bash` или `zsh` (например, `zsh` по умолчанию на macOS и многих Linux). Они
  не полагаются на неявное разбиение переменной на слова, которое есть в `bash`
  и которого нет в `zsh`: список id задаётся массивом и перебирается через
  `"${IDS[@]}"`. Пометка `bash` у блоков кода — только подсветка синтаксиса.
  **В `zsh` один раз в каждом терминале** выполните
  `setopt interactivecomments`: команды здесь несут комментарии `# …`, а без
  этой опции интерактивный `zsh` считает `#` командой — и, например, строка
  `MY_AUDIO="…"   # или ваш путь` молча оставляет переменную пустой. В `bash`
  ничего делать не нужно.
- Ничего не подкручивайте, чтобы добиться прохождения. Phase 5A читает структуру
  контейнера и больше ничего: ни расшифровки, ни тегов, ни обложек, ни кадров.
  **Именно это и принимается.**

### Порядок выполнения: три жизни сервера над одним каталогом данных

```
① python -m unimem_api --data-dir "$ACC/data" --token-file "$ACC/api.token" --port 8793            → MED-A
② остановить; тот же --data-dir, плюс --media                        → MED-B, MED-C, MED-D, MED-F
③ остановить; тот же --data-dir, СНОВА без --media                   → MED-E
```

Перезапуск между ② и ③ — это и есть MED-E, поэтому **в разделах ниже MED-F идёт
раньше MED-E**: MED-F нужен сервер с `--media`, а последний перезапуск обязан
быть последним. В таблице результатов строки стоят в алфавитном порядке.

Внутри одного прогона **все** запуски сервера используют один и тот же
`--data-dir "$ACC/data"`. Иначе проверяется не долговечность, а пустая база.

---

## Шаг 0. Проверить checkout, коммит и окружение

Ничего не переключайте и ничего не выбрасывайте. Это только осмотр.

**Приёмка проводится на слитом `main`, в котором уже есть этот файл.** Не на
ветке, не на локальном черновике. Сначала убедитесь, что это так:

```bash
cd /путь/к/вашему/checkout/unimem     # ваш путь, здесь он не предполагается
git fetch origin main
git rev-parse --abbrev-ref HEAD       # на какой вы ветке
git rev-parse HEAD                    # ТОЧНЫЙ коммит -- ЗАПИШИТЕ ЕГО
git log --oneline -1
ls -l docs/MANUAL_MEDIA_ACCEPTANCE.md # этот файл должен существовать в checkout
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
Macro Phase 5A закрыта быть не может, пока прогон не выполнен на чистом
checkout.

### Какой именно коммит

**Записанный `git rev-parse HEAD` — это базовая версия вашей приёмки.** Её
придётся указать при отправке результата: без неё непонятно, что именно было
проверено.

Этот чек-лист написан против слитого `main`
`74033ed06289898e7c324d316f85300e63974f01` — коммита слияния PR #28, которым
закрылся срез 5A-3. Это **не** ожидаемый SHA вашего прогона: закрывающая
приёмка выполняется на том слитом `main`, в котором **уже есть этот файл**, а он
приходит позже. Порядок такой:

1. изменение, добавляющее это руководство, сливается в `main`;
2. координатор приёмки сообщает **точный SHA коммита слияния** — это и есть
   ожидаемый SHA закрывающей приёмки;
3. вы сверяете свой `git rev-parse HEAD` с ним **до** начала MED-A.

Продолжайте к шагу 1 только если выполнено **всё**:

- `git status --porcelain` пуст;
- `docs/MANUAL_MEDIA_ACCEPTANCE.md` существует в этом checkout;
- checkout — это тот самый слитый `main`, на котором задумана приёмка, а не
  ветка разработки и не локальный черновик;
- `git rev-parse HEAD` записан дословно;
- записанный `HEAD` **совпадает** с ожидаемым SHA приёмки, который сообщил
  координатор.

Если `HEAD` не совпадает с ожидаемым SHA — не начинайте MED-A: подтяните нужный
`main` обычным образом и повторите этот шаг. Приёмка на другом коммите проверяет
другой продукт.

### Окружение

Посмотрите, какая это система, и запишите ответ — он идёт в результат:

```bash
cat /etc/os-release                   # или: sw_vers   на macOS
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

### Системный движок: `ffprobe`

`--media` требует **системный FFmpeg, дающий `ffprobe`**, и больше ничего.
Сервер с этим ключом не стартует, если движка нет или если эта сборка не умеет
открывать входной протокол `fd`: он говорит, чего не хватает, и никогда не
поднимается «тихо без медиа».

Проверку выполняет **продуктовая поверхность предусловий** — та же, которой
пользуется запуск сервера, — а не самодельный набор shell-команд. Скрипт для неё
заводится на [шаге 2](#prereqpy--что-видит-продуктовая-проверка-предусловий),
там же и запускается. Отдельно полезно записать сырой ответ движка:

```bash
ffprobe -version | head -1            # если ffprobe нет -- команда не найдена, это тоже ответ
```

**Этот чек-лист не устанавливает системные пакеты за вас** и ничего не меняет в
вашей системе и в вашем venv. Если FFmpeg нет, поставьте его сами средствами
своего дистрибутива — README (раздел *Audio and video*) показывает команды для
Debian/Ubuntu, macOS и Fedora — и начните прогон заново.

> Если `ffprobe` недоступен и ставить FFmpeg вы не хотите:
>
> - **MED-A** выполним сам по себе — ему движок не нужен, и именно это он и
>   доказывает;
> - **MED-B, MED-C, MED-D и MED-F** получают `BLOCKED`;
> - **MED-E** свою задачу выполнить не может и остаётся `NOT_RUN` или
>   `BLOCKED`. Он доказывает, что содержимое, созданное сборкой с capability,
>   читается после перезапуска сборкой без неё, — а такого содержимого без
>   MED-B и MED-C просто не появится;
> - **Macro Phase 5A закрыта быть не может** — закрытие требует `PASS` по всем
>   шести строкам.
>
> `BLOCKED` — это честная запись «проверку выполнить не удалось», а не `FAIL`
> продукта и не разновидность прохождения.

### Набора `[media]` не существует

`src/unimem_media/` — это стандартная библиотека плюс `core`. Python-зависимости
у медиа нет, дополнительного набора нет и ставить нечего: единственное
предусловие — системная программа, которую `pip` установить не может. Проверьте,
что ставить действительно нечего:

```bash
.venv/bin/python - <<'PY'
import importlib.metadata as md
print("объявленные extras:", md.metadata("capture-core").get_all("Provides-Extra"))
PY
```

В списке есть `dev` и `ocr`; `media` в нём нет — и не должно быть. Это
наблюдение об окружении, а не условие какой-либо строки.

> Если `ffmpeg` (кодировщик) у вас установлен вместе с `ffprobe` — это нормально
> и ожидаемо: пакет FFmpeg даёт обе программы. **Продуктовый код не вызывает
> `ffmpeg` никогда.** Здесь он используется ровно в одном месте — на
> [шаге 3](#шаг-3-контрольные-файлы), чтобы собрать контрольное немое видео,
> ровно как это делает набор тестов. Сервер видит только байты.

---

## Шаг 1. Свежий изолированный каталог для этого прогона

Каждый **полный** прогон чек-листа получает свой каталог. Он изолирован от
обычной установки UniMem: её база и raw-store не читаются, не меняются и никогда
не удаляются. Сервер приёмки слушает порт `8793`, чтобы не конфликтовать ни с
обычным UniMem на `8765`, ни с приёмкой документов на `8791`, ни с приёмкой
изображений на `8792`.

Каталог создаётся **без** `-p` на последнем сегменте: `mkdir` на уже
существующем каталоге завершается ошибкой, и именно это — проверка. Всё
остальное выполняется через `&&`, то есть **только** если каталог создан этой
командой:

```bash
RUNS="$HOME/unimem-acceptance"            # можно любое другое место
mkdir -p "$RUNS"
CANDIDATE="$RUNS/media-run-$(date -u +%Y%m%d-%H%M%S)"

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
здесь нет ни в одной команде. Каталог прошлого прогона — чужие доказательства:
он не читается, не чистится и не переиспользуется.

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
export API="http://127.0.0.1:8793"
export WAV="audio/wav"
export MP3="audio/mpeg"
export OGG="audio/ogg"
export MP4="video/mp4"
export WEBM="video/webm"
EOF
cat >> "$ACC/env.sh" <<'AUTH'
export UNIMEM_TOKEN_FILE="$ACC/api.token"
ucurl() {
  printf 'header = "Authorization: Bearer %s"\n' "$(cat "$UNIMEM_TOKEN_FILE")" |
    curl --config - "$@"
}
AUTH
source "$ACC/env.sh"
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
| **Перезапускаете сервер этого прогона** (шаги 5 и MED-E) | тот же `$ACC`, тот же `--data-dir "$ACC/data"` | база и raw-store те же; это и есть проверка долговечности |
| **Начинаете новый полный прогон** | выполняете блок создания каталога заново | метка времени другая, поэтому каталог новый и пустой; прошлый прогон остаётся нетронутым |

Разница между первой и третьей строкой — это ровно то, выполняете вы блок
создания каталога или нет. Он всегда делает **новый** каталог и никогда не
подхватывает существующий.

---

## Шаг 2. Вспомогательные скрипты

Они нужны, чтобы не требовать `jq`, Node или медиаплеер, и чтобы JSON строился
безопасно. Скрипты живут **вне** checkout, в `$ACC/bin`, и ничего в репозитории
не меняют.

Скрипты похожи на те, что использует
[приёмка изображений](MANUAL_IMAGE_ACCEPTANCE.md), но упрощены под медиа и
повторены здесь целиком: это руководство должно быть исполнимо само по себе, без
чтения соседнего.

### `prereq.py` — что видит продуктовая проверка предусловий

Это **не** параллельное shell-определение готовности. Скрипт вызывает ту самую
функцию, которой пользуется запуск сервера с `--media`, поэтому его ответ — это
утверждение о том коде, который будет работать, а не о похожей команде.

```bash
cat > "$ACC/bin/prereq.py" <<'PY'
"""Показать, что продуктовая проверка предусловий видит на этой машине.

Usage: prereq.py

Ничего не устанавливает, ничего не скачивает и ничего не чинит. Вызывает
unimem_media.describe_prerequisites() -- ту же поверхность, которой пользуется
старт сервера с --media: версия движка и поддержка входного протокола.
"""

from unimem_media import FFPROBE_EXECUTABLE, INPUT_PROTOCOL, describe_prerequisites
from unimem_media.errors import MediaPrerequisiteError

print("исполняемый файл  :", FFPROBE_EXECUTABLE)
print("требуемый протокол:", INPUT_PROTOCOL)
try:
    print(describe_prerequisites())
except MediaPrerequisiteError as missing:
    # Отсутствующий движок -- это тоже MediaPrerequisiteError: запуск обёрнут
    # в него вместе с OSError. Строка ниже -- ровно то, на чём отказался бы
    # стартовать сервер с --media.
    print("ПРЕДУСЛОВИЕ НЕ ВЫПОЛНЕНО:", missing)
    print()
    print("Медиа-сценарии этого прогона получают BLOCKED, а не FAIL.")
    raise SystemExit(1)
print()
print("ПРЕДУСЛОВИЕ ВЫПОЛНЕНО: сервер с --media на этой машине стартовать может.")
PY
```

Запустите его прямо сейчас и **запишите вывод**: он идёт в результат приёмки.

```bash
"$PY" "$ACC/bin/prereq.py"
```

Строка `input protocol available: True` — это и есть ответ на вопрос «умеет ли
**эта** сборка FFmpeg открывать `fd`». `False` означает, что сервер с `--media`
откажется стартовать, и это правильное поведение, а не дефект.

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

### `envelope.py` — собрать конверт capture для медиа

```bash
cat > "$ACC/bin/envelope.py" <<'PY'
"""Печатает канонический audio/video CaptureEnvelope в JSON.

Usage: envelope.py <capture_id> <audio|video> <mime_type> <file_ref>

Схема 0.3 -- та, в которой AUDIO стал первоклассной модальностью.

Никакой интерполяции в написанный руками JSON: кавычки и экранирование делает
json.dumps, поэтому никакое значение не может испортить тело запроса.

Заголовок здесь НЕ передаётся намеренно. Phase 5A не читает теги контейнера, и
title обязан остаться пустым -- пустым его и проверяют.
"""

import json
import sys
from datetime import datetime, timezone

capture_id, payload_type, mime_type, file_ref = sys.argv[1:5]
if payload_type not in {"audio", "video"}:
    raise SystemExit(f"payload type must be 'audio' or 'video', not {payload_type!r}")

print(
    json.dumps(
        {
            "schema_version": "0.3",
            "id": capture_id,
            "source": {"type": "upload", "provider": "manual-acceptance"},
            "payload": {
                "type": payload_type,
                "mime_type": mime_type,
                "file_ref": file_ref,
            },
            "context": {"captured_at": datetime.now(timezone.utc).isoformat()},
        },
        ensure_ascii=False,
    )
)
PY
```

### `show.py` — показать только те поля, которые нужно смотреть

Режимы `-fields` — это **закрытый список** полей для отправки по файлу
владельца. Они печатают структурное описание (именно его MED-F и просит
оценить), но не печатают ни `ref`, ни дайджеста, ни `title`, ни declared
mime-типа, ни имени файла, ни пути.

```bash
cat > "$ACC/bin/show.py" <<'PY'
"""Печатает небольшой набор полей, который проверяет шаг приёмки.

Usage: show.py ref            <upload.json>   -- только file_ref, для следующего шага
       show.py record         <record.json>
       show.py record-fields  <record.json>   -- то же, но БЕЗ дайджеста и ref
       show.py content        <content.json>
       show.py content-fields <content.json>  -- БЕЗ ref, дайджеста, title и mime

Читает сохранённое тело HTTP-ответа; сам никаких запросов не делает.

Режимы `-fields` -- это ЕДИНСТВЕННЫЙ вывод, который следует отправлять по
настоящему файлу владельца (MED-F). Они печатают строго закрытый список:

    capture id (его задаёт это руководство), status, payload_type,
    наличие/отсутствие error, тип содержимого, число сегментов, роли ассетов,
    processor@version и его status, пустоту derived, наличие/отсутствие title,
    и структурную metadata: media, audio_streams, video_streams.

Всё остальное отсутствует по построению, а не вычёркивается вручную: ни ref, ни
дайджеста, ни mime-типа, ни значения title, ни asset_id, ни имени файла, ни
пути. Добавлять сюда поля нельзя: это не "сокращённый вывод", а граница
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
    raise SystemExit(0)


def media_metadata(metadata: dict[str, object]) -> None:
    """Структурное описание. Печатается в обоих режимах: оно и есть предмет MED-F."""
    for key in ("media", "audio_streams", "video_streams"):
        if key in metadata:
            print(f"metadata.{key:14s}:",
                  json.dumps(metadata[key], ensure_ascii=False, sort_keys=True))
        else:
            print(f"metadata.{key:14s}: КЛЮЧА НЕТ")
    extra = sorted(set(metadata) - {"media", "audio_streams", "video_streams"})
    print("metadata (прочее)   :", ", ".join(extra) if extra else "НЕТ")


if mode in {"record", "record-fields"}:
    print("capture id  :", body.get("id"))
    print("status      :", body.get("status"))
    print("payload_type:", body.get("payload_type"))
    if safe:
        print("error       :", "ОТСУТСТВУЕТ" if body.get("error") is None else "ЗАДАНА")
    else:
        print("error       :", body.get("error"))
        raw = body.get("raw_object") or {}
        print("raw_object  : sha256=%s ref=%s" % (raw.get("sha256"), raw.get("ref")))
elif mode in {"content", "content-fields"}:
    if not safe:
        print("content id  :", body.get("id"))
        print("title       :", repr(body.get("title")))
        original = body.get("original") or {}
        print("original    : sha256=%s mime=%s asset_id=%s"
              % (original.get("sha256"), original.get("mime_type"), original.get("asset_id")))
    else:
        print("title       :",
              "ОТСУТСТВУЕТ (None)" if body.get("title") is None else "ЗАДАН (не печатается)")
    print("type        :", body.get("type"))
    assets = body.get("assets") or []
    print("assets      :", len(assets))
    for asset in assets:
        if safe:
            print("  role=%s" % asset.get("role"))
        else:
            print("  role=%s ref=%s mime=%s"
                  % (asset["role"], asset["ref"], asset.get("mime_type")))
    for run in body.get("processing") or []:
        print("processing  : %s@%s status=%s"
              % (run.get("processor"), run.get("processor_version"), run.get("status")))
    print("segments    :", len(body.get("segments") or []))
    derived = body.get("derived") or {}
    print("derived     : summary=%s topics=%d entities=%d"
          % ("ОТСУТСТВУЕТ" if derived.get("summary") is None else "ЗАДАН",
             len(derived.get("topics") or []), len(derived.get("entities") or [])))
    media_metadata(body.get("metadata") or {})
else:
    raise SystemExit(f"unknown mode: {mode}")
PY
```

> Не пропускайте вывод `show.py` через `head` — обрыв потока даёт
> `BrokenPipeError`, который легко принять за отказ продукта. Смотрите вывод
> целиком.

### `no_interpretation.py` — ничего из Phase 5B не появилось

Phase 5A читает структуру контейнера и больше ничего. Проверяет это не глаз, а
закрытый список утверждений.

```bash
cat > "$ACC/bin/no_interpretation.py" <<'PY'
"""Проверить, что в содержимом нет ни одной возможности Phase 5B.

Usage: no_interpretation.py <content.json>

Ни одного значения в выводе: только вердикты. Его можно присылать как есть,
в том числе по файлу владельца.
"""

import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    body = json.load(handle)

assets = body.get("assets") or []
roles = [asset.get("role") for asset in assets]
derived = body.get("derived") or {}
metadata = body.get("metadata") or {}

checks = [
    ("сегментов нет (segments == [])", len(body.get("segments") or []) == 0),
    ("ровно один asset", len(assets) == 1),
    ("единственный asset -- ORIGINAL", roles == ["original"]),
    ("нет извлечённой аудиодорожки (role=audio)", "audio" not in roles),
    ("нет ключевого кадра (role=keyframe)", "keyframe" not in roles),
    ("нет миниатюры (role=thumbnail)", "thumbnail" not in roles),
    ("title не взят из тегов (title is None)", body.get("title") is None),
    ("derived.summary пуст", derived.get("summary") is None),
    ("derived.topics пуст", not (derived.get("topics") or [])),
    ("derived.entities пуст", not (derived.get("entities") or [])),
    ("metadata -- только структура контейнера",
     set(metadata) <= {"media", "audio_streams", "video_streams"}),
]

for label, ok in checks:
    print(f"  {'OK  ' if ok else 'FAIL'} {label}")

worst = all(ok for _, ok in checks)
print()
print("ИНТЕРПРЕТАЦИИ НЕТ:", worst)
raise SystemExit(0 if worst else 1)
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

Это режим для файла владельца: путь, ref, дайджест и длины там сами по себе
являются приватными. Поэтому в --quiet НИ ОДИН отказ не печатает ни значения,
ни сообщения исключения, ни трассировки: любая ошибка чтения, разбора ссылки
или обращения к store становится CHECK_FAILED и ненулевым кодом выхода. Что
именно сломалось, смотрят локально -- тем же скриптом БЕЗ --quiet.
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

### `restart_check.py` — проверить, что перезапуск ничего не изменил

Эта проверка читает снимки **обоих** ответов — и `CaptureRecord`, и
`ContentObject` — вместе с их HTTP-кодами, и только потом сравнивает. Она
существует потому, что сравнения одних тел `ContentObject` недостаточно: запись
могла перейти из `complete` в другое состояние, а содержимое при этом осталось
бы тем же.

Дайджестов она не печатает вовсе — чтобы один и тот же вывод можно было
присылать и для контрольных файлов, и для файла владельца.

```bash
cat > "$ACC/bin/restart_check.py" <<'PY'
"""Проверить, что перезапуск процесса ничего не изменил.

Usage: restart_check.py <data_dir> <snapshots_dir> <capture_id>:<исходный_файл>...

Читает сохранённые до/после снимки ОБОИХ ответов вместе с их HTTP-кодами и
только потом сравнивает. Отсутствующий снимок -- это BLOCKED, пропавший
capture -- это FAIL. Таблицу результатов владельца этот скрипт не заполняет.

Ни одного дайджеста и ни одного имени файла в выводе: его можно присылать как
есть, в том числе для файла владельца. Это верно И ПРИ ОТКАЗЕ: любая ошибка
разбора ссылки, обращения к raw-store или чтения исходного файла превращается
в вердикт с фиксированной категорией отказа, а не в трассировку с путём, ref
или дайджестом внутри.
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
    """Выполнить шаг; наружу выпустить только категорию отказа."""
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

    structural = (after_content.get("metadata") or {}).get("media")
    if not isinstance(structural, dict) or "container" not in structural:
        return "FAIL", notes + ["структурная metadata.media после перезапуска не читается"]
    notes.append("metadata.media на месте и прочитана процессом без --media")

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
    print(f"{capture_id:24s} {verdict}")
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

## Шаг 3. Контрольные файлы

Два детерминированных контроля, и оба видно насквозь: ни один не является
бинарной фикстурой из репозитория — в репозитории бинарных медиа-фикстур нет
вовсе, и этот чек-лист их туда не добавляет.

### `control.wav` — собирается стандартной библиотекой, без FFmpeg

Нужен на шаге MED-A, который выполняется **до** и **независимо от** движка:
секунда синусоиды 440 Гц, моно, 44 100 Гц, 16 бит PCM.

```bash
"$PY" - "$ACC/in/control.wav" <<'PY'
import math
import struct
import sys
import wave

RATE, SECONDS, FREQ = 44100, 1.0, 440.0
frames = b"".join(
    struct.pack("<h", int(20000 * math.sin(2 * math.pi * FREQ * n / RATE)))
    for n in range(int(RATE * SECONDS))
)
with wave.open(sys.argv[1], "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(RATE)
    out.writeframes(frames)
print("собран:", sys.argv[1])
PY
ls -l "$ACC/in/control.wav"
```

### `control-silent.mp4` — немое видео, и для него нужен `ffmpeg`

Это **обязательный** контроль сценария MED-C: правило «видео без звука —
валидное медиа» нельзя надёжно установить на произвольном файле владельца,
потому что у произвольного видео звук обычно есть.

Рецепт — тот же, которым пользуется набор тестов (`tests/media_fixtures.py`):
тестовый шаблон 64×48, 25 кадров в секунду, одна секунда, H.264, **без единой
аудиодорожки**.

```bash
ffmpeg -loglevel error -y \
  -f lavfi -i "testsrc=size=64x48:rate=25:duration=1.0" \
  -c:v libx264 -pix_fmt yuv420p \
  "$ACC/in/control-silent.mp4"
ls -l "$ACC/in/control-silent.mp4"
```

Если `ffmpeg` отсутствует, эта команда не выполнится. Тогда MED-C получает
`BLOCKED` (как и остальные медиа-сценарии — движка нет), и фаза закрыта быть не
может. Ставить FFmpeg за вас этот чек-лист не будет.

> **`ffmpeg` здесь — инструмент сборки контроля, и только.** Продуктовый код не
> вызывает его никогда: сервер запускает `ffprobe` и только `ffprobe`. Ровно так
> же устроены и автоматические наборы.

### Что здесь контроль, а что доказательство владельца

| Файл | Откуда | Что им доказывают |
| --- | --- | --- |
| `control.wav` | сгенерирован этим руководством | MED-A; при необходимости — запасной вариант MED-B |
| `control-silent.mp4` | сгенерирован этим руководством | MED-C (правило немого видео), MED-D |
| **файл владельца** | **выбран владельцем, не репозиторием** | MED-F, и вместе с ним MED-B и/или MED-C |

Эти три источника **не смешиваются в доказательствах**. В каждой строке
результата должно быть написано, что именно использовалось; `PASS` по контролю
не засчитывается как `PASS` по файлу владельца, и наоборот.

**Файл владельца в репозиторий не кладётся** — ни в этом прогоне, ни в
закрывающем. Здесь он называется «настоящий медиафайл владельца» либо нейтральной
меткой, которую выберет владелец.

---

## Шаг 4. Запустить сервер приёмки (обычный режим, без `--media`)

**Это первая из трёх жизней сервера.** Ключа `--media` здесь нет — проверяется
то, что пользователь получает сразу после установки.

Во втором терминале:

```bash
source "$ACC/env.sh"; cd "$REPO"
"$PY" -m unimem_api --data-dir "$ACC/data" --token-file "$ACC/api.token" --init-token
"$PY" -m unimem_api --data-dir "$ACC/data" --token-file "$ACC/api.token" --port 8793
```

Каталог `$ACC/data` сервер создаст сам. Оставьте терминал открытым — сервер
живёт в нём. Вернитесь в первый терминал и проверьте, что он отвечает:

```bash
ucurl -sS "$API/health"; echo
```

Ожидается `{"status":"ok"}`. `GET /health` сообщает только о живости процесса и
не проверяет ничего другого.

---

## MED-A. Обычная установка остаётся без медиа

**Что проверяется:** разделение возможностей развёртывания — то, что видит
оператор, который поставил UniMem и запустил его без единого ключа. Это **не**
проверка форматов: ни один байт здесь не разбирается.

Сервер стартовал без `--media`, значит: `unimem_media` не импортируется,
`ffprobe` не запускается и не опрашивается, и наличие или отсутствие FFmpeg на
машине на этот запуск не влияет. Сам факт того, что сервер поднялся и отвечает
`{"status":"ok"}`, — первое доказательство строки.

В строке таблицы три подпроверки: **MED-A1**, **MED-A2** и **MED-A3**. `PASS`
ставится, только если прошли все три.

### MED-A1 — медиабайты стажируются, а AUDIO-capture отклоняется

Стажирование — это не capture: `POST /v1/uploads` не заводит запись, не мнёт
идентификатор и не запускает обработку. Оно обязано работать и в сборке без
медиа.

```bash
ucurl -sS -F "file=@$ACC/in/control.wav;type=audio/wav" "$API/v1/uploads" \
  -o "$ACC/out/a_upload.json" -w 'upload HTTP %{http_code}\n'
REF_A=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a_upload.json")
echo "file_ref: $REF_A"
```

```bash
"$PY" "$ACC/bin/envelope.py" med_a1_audio_refused audio "$WAV" "$REF_A" \
  > "$ACC/out/a1_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a1_envelope.json" \
  -o "$ACC/out/a1_capture.json" -w 'capture HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/a1_capture.json"
```

Записи быть не должно — ни в каком состоянии:

```bash
ucurl -sS "$API/v1/captures/med_a1_audio_refused" \
  -o "$ACC/out/a1_record.json" -w 'record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/a1_record.json"
```

| поле | ожидание |
| --- | --- |
| upload | `200` |
| capture | `422`, `error.code` = `unsupported_payload` |
| текст ошибки | называет **capability развёртывания**, а не ваш файл |
| последующий `GET` записи | `404`, `error.code` = `not_found` |

### MED-A2 — VIDEO-capture отклоняется так же

Те же стажированные байты, но заявленные как видео. Байты здесь ни при чём:
сборка без capability отвечает одинаково на любое заявление.

```bash
"$PY" "$ACC/bin/envelope.py" med_a2_video_refused video "$MP4" "$REF_A" \
  > "$ACC/out/a2_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a2_envelope.json" \
  -o "$ACC/out/a2_capture.json" -w 'capture HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/a2_capture.json"

ucurl -sS -o /dev/null -w 'record HTTP %{http_code}\n' \
  "$API/v1/captures/med_a2_video_refused"
```

Ожидается `422 unsupported_payload` и затем `404`.

### MED-A3 — отказ по capability идёт раньше любой медийной проверки

Это гарантия, а не оптимизация: сборка без capability обязана отвечать одинаково
и на нормально стажированные байты, и на ссылку, за которой ничего нет. Иначе
форма ошибки позволяла бы прощупывать долговечное хранилище.

```bash
UNSTAGED="sha256:$("$PY" -c 'import hashlib; print(hashlib.sha256(b"unimem media acceptance: never staged").hexdigest())')"
echo "никогда не стажированная ссылка: $UNSTAGED"

"$PY" "$ACC/bin/envelope.py" med_a3_unstaged_refused video "$MP4" "$UNSTAGED" \
  > "$ACC/out/a3_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/a3_envelope.json" \
  -o "$ACC/out/a3_capture.json" -w 'capture HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/a3_capture.json"

ucurl -sS -o /dev/null -w 'record HTTP %{http_code}\n' \
  "$API/v1/captures/med_a3_unstaged_refused"
```

Ожидается **тот же** `422 unsupported_payload` — а **не**
`capture_material_unavailable`, — и затем `404`.

**Провал, если:** сервер без `--media` не стартовал или не ответил на `/health`;
`POST /v1/uploads` не вернул `200`; любая из трёх попыток capture вернула не
`422` или код ошибки не `unsupported_payload`; MED-A3 дал
`capture_material_unavailable` вместо `unsupported_payload`; хотя бы один
последующий `GET /v1/captures/{id}` вернул не `404`; текст ошибки называет ваш
`file_ref`, declared MIME-тип, путь, контейнер или что-либо о `ffprobe`.

**Что прислать:** строку `{"status":"ok"}`; `upload HTTP 200`; по каждой из трёх
подпроверок — HTTP-код capture, `error.code`, HTTP-код последующего `GET`
записи; и одну фразу: «сервер был запущен **без** `--media`».

> **Оговорка, которую надо записать честно.** Эта строка доказывает, что сборка
> без `--media` медиа не принимает и что `ffprobe` ей для старта не потребовался.
> Если FFmpeg на вашей машине **установлен**, этот прогон сам по себе не
> доказывает, что сервер поднялся бы и на машине совсем без него. Эту границу
> доказывают автоматические наборы: задача CI `Quality gates` работает на машине
> без FFmpeg вовсе, а в задаче `Local media probing` есть отдельная проверка,
> поднимающая обычный сервер с намеренно опустошённым `PATH`. Запишите в
> результат, был ли `ffprobe` на машине; на статус строки это не влияет.

---

## Шаг 5. Перезапустить сервер с `--media`

**Это вторая из трёх жизней сервера.** Каталог данных — **тот же**.

Сначала убедитесь, что предусловие выполнено, — той же продуктовой проверкой,
которой пользуется старт:

```bash
"$PY" "$ACC/bin/prereq.py"
```

Если она отказала, сервер с `--media` не поднимется. Это правильное поведение,
а не дефект: сборка с `--media` никогда не стартует с тихо выключенным
разбором. В этом случае MED-B, MED-C, MED-D и MED-F получают `BLOCKED`, а MED-E
остаётся `NOT_RUN` или `BLOCKED`. Поставьте FFmpeg сами и начните прогон заново.

В терминале сервера остановите процесс (`Ctrl+C`) и запустите заново:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --token-file "$ACC/api.token" --port 8793 --media
```

```bash
ucurl -sS "$API/health"; echo
```

Никакого набора `[media]` для этого **не нужно и не ставится**: Python-зависимости
у медиа нет.

Проверьте заодно, что отказы шага 4 не оставили после себя записей и что новый
процесс работает над тем же каталогом данных:

```bash
ucurl -sS -o /dev/null -w 'A1 record %{http_code}\n' "$API/v1/captures/med_a1_audio_refused"
ucurl -sS -o /dev/null -w 'A2 record %{http_code}\n' "$API/v1/captures/med_a2_video_refused"
ucurl -sS -o /dev/null -w 'A3 record %{http_code}\n' "$API/v1/captures/med_a3_unstaged_refused"
```

Все три — `404`. Включение capability **не оживляет** отклонённые попытки: их
никогда не было. Стажированные байты при этом на месте — их сейчас и будет
использовать MED-B.

---

## MED-B. Настоящий standalone-звук

**Что проверяется:** аудио — самостоятельная модальность, а не видео без
картинки. Подкаст принимается, сохраняется в точности как прислан, описывается
структурно — и **не интерпретируется**.

### Какой файл брать

Один настоящий файл в одном из поддержанных аудиосемейств: `audio/wav`,
`audio/mpeg` (MP3) или `audio/ogg`.

**Предпочтителен файл владельца, а не сгенерированный контроль.** Если вы берёте
свой файл, эта же строка даёт доказательство для
[MED-F](#med-f-настоящий-медиафайл-владельца) — запишите это явно.

Если подходящего своего файла нет, допустим контрольный `control.wav` из
[шага 3](#шаг-3-контрольные-файлы). Тогда:

- в доказательстве MED-B должно быть **написано, что это контроль**;
- MED-F обязан быть закрыт настоящим файлом владельца в MED-C.

Знать точные ожидаемые значения метаданных произвольного файла от вас **не
требуется**. Проверяется правдоподобие, а не бит-в-бит совпадение с числом,
которого вы заранее не знаете.

```bash
MY_AUDIO="$ACC/in/control.wav"    # или ваш путь; никуда не отправляется
MY_AUDIO_MIME="$WAV"              # $WAV | $MP3 | $OGG
B_ID=med_b_control_audio          # для своего файла возьмите med_b_owner_audio

ucurl -sS -F "file=@$MY_AUDIO;type=$MY_AUDIO_MIME" "$API/v1/uploads" \
  -o "$ACC/out/b_upload.json" -w 'upload HTTP %{http_code}\n'
REF_B=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/b_upload.json")

"$PY" "$ACC/bin/envelope.py" "$B_ID" audio "$MY_AUDIO_MIME" "$REF_B" \
  > "$ACC/out/b_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/b_envelope.json" \
  -o "$ACC/out/b_capture.json" -w 'capture HTTP %{http_code}\n'

ucurl -sS "$API/v1/captures/$B_ID" \
  -o "$ACC/out/b_record.json" -w 'record HTTP %{http_code}\n'
ucurl -sS "$API/v1/captures/$B_ID/content" \
  -o "$ACC/out/b_content.json" -w 'content HTTP %{http_code}\n'
```

```bash
"$PY" "$ACC/bin/show.py" record  "$ACC/out/b_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/b_content.json"
"$PY" "$ACC/bin/no_interpretation.py" "$ACC/out/b_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/b_content.json" "$MY_AUDIO"
```

| поле | ожидание |
| --- | --- |
| upload / capture / record / content | `200` / `201` / `200` / `200` |
| `record.status` | `complete` |
| `record.error` | `None` |
| `record.payload_type` | `audio` |
| `content.type` | `audio` |
| `assets` | ровно **1**, `role=original` |
| `processing` | `audio@0.1`, `status=complete` |
| `segments` | ровно **0** |
| `metadata.media.container` | `wav`, `mp3` или `ogg` — **тот, что соответствует заявленному вами типу** |
| `metadata.media.audio_stream_count` | **≥ 1** |
| `metadata.audio_streams` | непустой; у каждой записи есть `index` |
| `codec` / `sample_rate` / `channels` | правдоподобны для **вашего** файла (см. ниже) |
| `ИНТЕРПРЕТАЦИИ НЕТ` | `True` |
| `BYTES IDENTICAL` | `True` |

Отображение заявленного типа в требуемый контейнер фиксировано и здесь не
угадывается: `audio/wav` → `wav`, `audio/mpeg` → `mp3`, `audio/ogg` → `ogg`,
`video/mp4` → `mp4`, `video/webm` → `webm`.

### Что значит «правдоподобно»

Только это, и ничего сверх:

- `codec` — обычный кодек для этого семейства (например, `pcm_s16le` для WAV,
  `mp3` для MP3, `vorbis` или `opus` для OGG), и вы не видите противоречия;
- `sample_rate` — обычная частота дискретизации (44 100, 48 000, 22 050 и т. п.);
- `channels` — 1 для моно, 2 для стерео, и это не противоречит тому, что вы
  знаете о файле;
- `duration_seconds` — близко к реальной длительности, **если контейнер её
  объявил**. Отсутствие ключа — валидное наблюдение: у живой записи или у
  обрезанного файла длительности в контейнере может не быть, и UniMem пишет
  отсутствие как отсутствие, а не как `0` или `"unknown"`.

Поля, которых контейнер не объявил, **отсутствуют**. Это не дефект.

**Провал, если:** любой код ответа отличается от ожидаемого; `status` не
`complete`; `type` не `audio`; ассетов не ровно один; сегментов не ноль; роль
ассета не `original`; процессор не `audio@0.1`; `container` не соответствует
заявленному типу; `audio_streams` пуст; наблюдённые значения противоречат
файлу, который вы сами слушаете; `ИНТЕРПРЕТАЦИИ НЕТ: False`;
`BYTES IDENTICAL: False`.

**Что прислать:** четыре HTTP-кода; `status`, `payload_type`, `type`; число
ассетов и роль; имя процессора; число сегментов; `metadata.media`,
`metadata.audio_streams`, `metadata.video_streams`; строку
`ИНТЕРПРЕТАЦИИ НЕТ`; строку `BYTES IDENTICAL`; и одну фразу — **контроль это
или файл владельца**.

---

## MED-C. Настоящее видео, включая правило немого видео

**Что проверяется:** видео принимается и описывается структурно, а звук для
видео **не обязателен**. Немое видео — валидное медиа; беззвучное аудио —
нет. Это правило асимметрично намеренно.

В строке две части: **обязательный контроль** и **необязательное видео
владельца**.

### C1 — немое видео (обязательно, детерминированно)

На произвольном файле владельца это правило надёжно не устанавливается: у
обычного видео звук есть. Поэтому здесь берётся контроль из
[шага 3](#шаг-3-контрольные-файлы) — `control-silent.mp4`, собранный без единой
аудиодорожки. **Эта подпроверка обязана пройти.**

```bash
ucurl -sS -F "file=@$ACC/in/control-silent.mp4;type=video/mp4" "$API/v1/uploads" \
  -o "$ACC/out/c1_upload.json" -w 'upload HTTP %{http_code}\n'
REF_C1=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/c1_upload.json")

"$PY" "$ACC/bin/envelope.py" med_c_silent_control video "$MP4" "$REF_C1" \
  > "$ACC/out/c1_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/c1_envelope.json" \
  -o "$ACC/out/c1_capture.json" -w 'capture HTTP %{http_code}\n'

ucurl -sS "$API/v1/captures/med_c_silent_control" \
  -o "$ACC/out/c1_record.json" -w 'record HTTP %{http_code}\n'
ucurl -sS "$API/v1/captures/med_c_silent_control/content" \
  -o "$ACC/out/c1_content.json" -w 'content HTTP %{http_code}\n'

"$PY" "$ACC/bin/show.py" record  "$ACC/out/c1_record.json"
"$PY" "$ACC/bin/show.py" content "$ACC/out/c1_content.json"
"$PY" "$ACC/bin/no_interpretation.py" "$ACC/out/c1_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/c1_content.json" \
  "$ACC/in/control-silent.mp4"
```

| поле | ожидание |
| --- | --- |
| upload / capture / record / content | `200` / `201` / `200` / `200` |
| `record.status` | `complete` — **звука нет, и это не мешает** |
| `content.type` | `video` |
| `processing` | `video@0.1`, `status=complete` |
| `assets` / `segments` | ровно **1** (`role=original`) / ровно **0** |
| `metadata.media.container` | `mp4` |
| `metadata.media.video_stream_count` | `1` |
| `metadata.media.audio_stream_count` | **`0`** |
| `metadata.audio_streams` | **`[]`** — присутствует и пуст, а не отсутствует |
| `metadata.video_streams` | одна запись: `width` 64, `height` 48, `frame_rate` `"25/1"` |
| `ИНТЕРПРЕТАЦИИ НЕТ` / `BYTES IDENTICAL` | `True` / `True` |

Кадровый темп — **точная рациональная дробь**, как её объявил контейнер, а не
десятичное приближение: округление нельзя было бы отменить потом. Для этого
контроля это `"25/1"`; у настоящего видео вы вполне можете увидеть, например,
`"30000/1001"`, и это правильно, а не «29.97 записали криво».

Пустой `audio_streams` присутствует намеренно: «звук искали и его нет» — это
наблюдение, а отсутствие ключа было бы «никто не смотрел».

### C2 — видео владельца (необязательно)

Если у вас есть настоящий `video/mp4` или `video/webm`, прогоните и его — те же
команды с `med_c_owner_video` и своим путём. Тогда MED-C заодно даёт
доказательство для [MED-F](#med-f-настоящий-медиафайл-владельца).

Для видео владельца ожидания те же, кроме одного: **звук опционален**. И
`audio_stream_count: 0`, и `audio_stream_count: 2` одинаково нормальны. Ширина,
высота и кадровый темп должны быть правдоподобны для файла, который вы сами
смотрите.

**Провал, если:** любой код ответа отличается от ожидаемого; немой контроль не
дошёл до `complete`; `type` не `video`; `video_streams` пуст; ассетов не ровно
один; сегментов не ноль; `container` не `mp4`; у контроля `audio_streams` не
`[]` или ключ отсутствует; кадровый темп пришёл десятичным числом вместо
рациональной дроби; `ИНТЕРПРЕТАЦИИ НЕТ: False`; `BYTES IDENTICAL: False`.
Отсутствие звука у видео **провалом не является ни при каких условиях.**

**Что прислать:** по C1 — четыре HTTP-кода, `status`, `type`, число ассетов и
сегментов, `metadata.media`, `metadata.audio_streams`, `metadata.video_streams`,
`ИНТЕРПРЕТАЦИИ НЕТ`, `BYTES IDENTICAL`. По C2, если выполняли, — то же самое
через `-fields`-режимы (см. [MED-F](#что-именно-отправлять-по-своему-файлу)).

---

## MED-D. Заявление и наблюдение расходятся

**Что проверяется:** заявленный тип **маршрутизирует**, наблюдённый контейнер
**проверяет**. Заявление не «исправляется» тихо, и расхождение — это
детерминированный вердикт о присланных байтах, а не «движок недоступен».

Берутся **уже стажированные** байты немого MP4 из MED-C и присылаются под
заведомо неверной, но поддержанной комбинацией: `audio` + `audio/mpeg`. Это
самое безопасное расхождение из возможных — оба типа поддержаны, ничего нового
не вводится, и результат от машины к машине не меняется.

```bash
REF_C1=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/c1_upload.json")

"$PY" "$ACC/bin/envelope.py" med_d_mismatch audio "$MP3" "$REF_C1" \
  > "$ACC/out/d_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/d_envelope.json" \
  -o "$ACC/out/d_capture.json" -w 'capture HTTP %{http_code}\n'
cat "$ACC/out/d_capture.json"; echo
```

```bash
ucurl -sS "$API/v1/captures/med_d_mismatch" \
  -o "$ACC/out/d_record.json" -w 'record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/d_record.json"

ucurl -sS "$API/v1/captures/med_d_mismatch/content" \
  -o "$ACC/out/d_content.json" -w 'content HTTP %{http_code}\n'
cat "$ACC/out/d_content.json"; echo

"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/d_record.json" \
  "$ACC/in/control-silent.mp4"
```

| поле | ожидание |
| --- | --- |
| capture | `422` |
| `error.code` | `processing_failed` |
| `error.message` | ровно `media bytes do not match the declared supported format` |
| `record` | `200`, `status` = **`failed`** (долговечно) |
| `record.payload_type` | `audio` — заявление сохранено как было, а не переписано на `video` |
| `record.error` | тот же фиксированный текст |
| `content` | `404`, `error.code` = `not_found` |
| `BYTES IDENTICAL` | `True` — стажированный оригинал не тронут |

### Чего в ответе быть не должно

Прочитайте оба тела глазами. В них не должно встречаться:

- слова `ffprobe`, номера версии движка, кода возврата, `stderr`;
- любого пути в файловой системе и имени временного файла;
- **наблюдённого псевдонима контейнера** — ни `mp4`, ни `mov`, ни `m4a`, ни
  `isom`;
- вашего `file_ref`, дайджеста или имени файла;
- имени SQLite-файла, таблицы или колонки.

Сообщение фиксированное и одинаковое для любого расхождения — это и есть
ожидаемое поведение.

### Три вещи, которые нельзя путать

| Что случилось | Ответ | Состояние записи |
| --- | --- | --- |
| сборка вообще не принимает медиа | `422 unsupported_payload` | **записи нет** |
| контейнер противоречит заявлению (эта строка) | `422 processing_failed` | долговечный **`failed`** |
| доверенного результата разбора не получено | `503 media_probe_unavailable` | **не**терминальное, содержимого нет |

Третью строку этот чек-лист **не проверяет** и проверить не может без
разрушительных действий: при отсутствующем движке сервер с `--media` просто не
стартует. Это отображение на границе доставки, и автоматические наборы проверяют
его именно там. **Нового контракта ошибок здесь не вводится и не испытывается.**

**Провал, если:** capture вернул не `422`; код ошибки не `processing_failed`;
текст ошибки не фиксированный; запись не `failed` или её нет; `payload_type`
переписан; `GET .../content` вернул не `404`; появился `ContentObject`; в теле
ответа встретилось что-либо из списка выше; стажированный оригинал изменился.

**Что прислать:** HTTP-код capture и тело ответа целиком (оно однострочное);
HTTP-код и `status`, `payload_type`, `error` записи; HTTP-код и тело ответа
`.../content`; строку `BYTES IDENTICAL`; и одну фразу: «в телах ответов нет ни
`ffprobe`, ни путей, ни наблюдённого контейнера».

---

## MED-F. Настоящий медиафайл владельца

**Что проверяется:** единственное, что нельзя поручить машине. Сгенерированные
контроли проверяют продукт — они **не** доказывают, что прошёл **ваш** файл.
Судьёй здесь является человек, который этот файл знает.

Сервер — **тот же**, с `--media`. Этот сценарий идёт раньше MED-E, потому что
ему нужен именно этот сервер, а перезапуск обязан быть последним.

### Что требуется

**Минимум один** настоящий файл, выбранный вами, а не сгенерированный этим
репозиторием, в одном из пяти поддержанных семейств: `audio/wav`, `audio/mpeg`,
`audio/ogg`, `video/mp4`, `video/webm`.

Он может быть **тем же самым**, что вы использовали в MED-B или в MED-C — тогда
отдельного прогона не нужно, достаточно ясно указать, какая строка его
описывает. Требование ровно одно: хотя бы один файл в прогоне выбран владельцем.

### Суждение человека здесь намеренно узкое

Вас просят подтвердить **три** вещи, и только их:

1. **структурное описание правдоподобно для этого файла** — семейство,
   длительность, число дорожек, кодеки, а для видео ещё размер кадра и кадровый
   темп не противоречат тому, что вы о файле знаете;
2. **сохранённый неизменяемый оригинал соответствует присланному файлу** —
   строка `BYTES IDENTICAL: True`;
3. **ничего не выдумано** — в ответе нет ни одного утверждения о файле сверх
   того, что объявил контейнер.

### Чего вас НЕ просят оценивать

Ни одного из этих суждений в Phase 5A не существует, и никакое из них не может
быть причиной `FAIL`:

- точность расшифровки речи — расшифровки **нет**;
- смысл, тему или содержание записи;
- миниатюры, ключевые кадры и обложки — их **нет**;
- теги контейнера: исполнителя, альбом, название — они **не читаются**;
- распознавание сцен, лиц, объектов, языка;
- качество звука или картинки.

Этих возможностей в Phase 5A нет. Их отсутствие — **предмет проверки**
`no_interpretation.py`, а не недостаток.

```bash
MY="/путь/к/вашему/файлу.mp3"     # ваш путь; никуда не отправляется
MY_MIME="$MP3"                     # $WAV | $MP3 | $OGG | $MP4 | $WEBM
MY_TYPE=audio                      # audio для трёх первых, video для двух последних
F_ID=med_b_owner_audio             # или med_c_owner_video -- ту строку он и закрывает
```

Если файл уже отправлен в MED-B или MED-C — **не отправляйте его второй раз**.
Просто укажите, какая строка его описывает, и возьмите её сохранённые ответы:

```bash
F_ID=med_b_owner_audio
cp "$ACC/out/b_record.json"  "$ACC/out/f_record.json"
cp "$ACC/out/b_content.json" "$ACC/out/f_content.json"
```

Иначе отправьте его как отдельную строку:

```bash
ucurl -sS -F "file=@$MY;type=$MY_MIME" "$API/v1/uploads" \
  -o "$ACC/out/f_upload.json" -w 'upload HTTP %{http_code}\n'
REF_F=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/f_upload.json")

"$PY" "$ACC/bin/envelope.py" "$F_ID" "$MY_TYPE" "$MY_MIME" "$REF_F" \
  > "$ACC/out/f_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/f_envelope.json" \
  -o "$ACC/out/f_capture.json" -w 'capture HTTP %{http_code}\n'

ucurl -sS "$API/v1/captures/$F_ID" \
  -o "$ACC/out/f_record.json" -w 'record HTTP %{http_code}\n'
ucurl -sS "$API/v1/captures/$F_ID/content" \
  -o "$ACC/out/f_content.json" -w 'content HTTP %{http_code}\n'
```

### Смотрите локально

```bash
"$PY" "$ACC/bin/show.py" content "$ACC/out/f_content.json"
```

Откройте файл своим обычным плеером и сравните. На что смотреть:

- семейство контейнера — то, что вы заявили;
- длительность — близко к реальной (или отсутствует, если контейнер её не
  объявил);
- число и вид дорожек не противоречат файлу;
- для видео: размер кадра и кадровый темп правдоподобны;
- оригинал не переписан.

### Условия `PASS` для MED-F

`PASS` ставится, только если выполнено **всё**:

| | условие |
| --- | --- |
| 0 | файл выбран **владельцем**, а не сгенерирован этим руководством |
| 1 | семейство — одно из пяти поддержанных |
| 2 | capture — `201` |
| 3 | `record.status` — `complete` |
| 4 | content — `200` |
| 5 | `content.type` — `audio` или `video`, в соответствии с заявленным |
| 6 | ассет ровно один, `role=original` |
| 7 | сегментов — ровно **0** |
| 8 | `metadata.media.container` соответствует заявленному типу |
| 9 | для аудио — хотя бы одна аудиодорожка; для видео — хотя бы одна видеодорожка |
| 10 | `ИНТЕРПРЕТАЦИИ НЕТ` — `True` |
| 11 | `BYTES IDENTICAL` — `True` |
| 12 | вы считаете структурное описание правдоподобным для этого файла |

**`FAIL`, если** не выполнено любое из условий 2–12 при выполненных 0 и 1. В
частности: описание противоречит файлу, который вы сами слушаете или смотрите,
— это `FAIL`, а не «так вышло».

**`BLOCKED`, если** сверку не удалось **выполнить** — например,
`BYTES IDENTICAL: CHECK_FAILED`, — или если своего файла в поддержанном
семействе у вас нет. `BLOCKED` — это не `PASS`: **Macro Phase 5A в таком прогоне
закрыта быть не может.**

**Ничего не подкручивайте, чтобы получить `PASS`.** Ни контейнера, ни
заявленного типа, ни файла. Плохой результат — это честная находка, и её следует
записать, а не обойти.

### Что именно отправлять по своему файлу

Ваш файл никуда наружу не уходит: всё выполняется на вашей машине против
локального сервера на `127.0.0.1`, и ни один шаг не обращается к внешнему
сервису. Но **отправка вывода — это отдельный вопрос.**

> `show.py content` печатает `ref`, дайджест, mime-тип и `title`. Такой вывод по
> своему файлу пересылать не нужно.

Для отправки есть режимы с **закрытым списком** полей:

```bash
"$PY" "$ACC/bin/show.py" record-fields   "$ACC/out/f_record.json"
"$PY" "$ACC/bin/show.py" content-fields  "$ACC/out/f_content.json"
"$PY" "$ACC/bin/no_interpretation.py"    "$ACC/out/f_content.json"
"$PY" "$ACC/bin/original.py" "$ACC/data" "$ACC/out/f_content.json" "$MY" --quiet
```

Вместе они печатают ровно следующее, и ничего сверх того:

- `capture id` (его задало это руководство), `status`, `payload_type` и
  «error отсутствует/задана»;
- наличие или отсутствие `title` — **без значения**;
- тип содержимого, число ассетов и их роли, `processor@version` и его `status`;
- число сегментов и пустоту `derived`;
- структурную `metadata`: `media`, `audio_streams`, `video_streams` — **это и
  есть предмет вашего суждения**;
- одиннадцать строк вердиктов `no_interpretation.py`;
- одну строку `BYTES IDENTICAL`: `True`, `False` или `CHECK_FAILED`.

> `CHECK_FAILED` означает, что сверку не удалось **выполнить** — например,
> исходный файл больше не по тому пути. Подробностей эта строка не содержит
> намеренно: они приватны. Посмотрите их локально тем же скриптом **без**
> `--quiet` и не присылайте этот вывод. `CHECK_FAILED` — это не `PASS`.

К этому добавьте от себя:

- HTTP-коды выполненных запросов (их печатал `curl -w`);
- **имя файла или нейтральную метку владельца** — на ваш выбор, одно из двух;
- **семейство** (`audio/wav`, `audio/mpeg`, `audio/ogg`, `video/mp4`,
  `video/webm`);
- **по желанию** — локальный SHA-256 файла;
- одну фразу: **«структурное описание правдоподобно для этого файла: да /
  частично / нет»**.

**И больше ничего.** В частности, **не отправляйте** сам файл, локальный путь,
URL, `ref`, `title`, произвольные фрагменты `metadata` сверх перечисленного и
описание того, что в записи звучит или показано.

Если дефект нельзя показать без содержимого — скажите об этом словами, и
решение о том, что раскрывать, останется за вами.

**Файл владельца в репозиторий не кладётся.** В долговечной записи он будет
назван так, как вы сами его назовёте: именем файла или нейтральной меткой, и
семейством — и ничем больше.

---

## MED-E. Долговечность и чтение без capability

**Что проверяется:** захваченное вчера читается сегодня — и читается сборкой,
у которой capability **выключена**. Один перезапуск на весь прогон, в самом
конце, а не после каждого сценария.

Это важное архитектурное свойство, видимое оператору: разбор контейнера нужен,
чтобы **создать** содержимое, и не нужен, чтобы его **прочитать**. Структурная
metadata — это долговечная запись, а не живой вызов движка.

> **Точная формулировка того, что этот шаг доказывает.** Он доказывает, что
> содержимое, созданное при включённой capability, полностью читается процессом,
> запущенным **без** `--media`, и что чтение не требует живого `ffprobe`. Он
> **не** доказывает, что `ffprobe` отсутствует или недоступен: движок, скорее
> всего, по-прежнему установлен на вашей машине. Границу «никакой
> Python-зависимости у медиа нет» отдельно доказывает задача CI
> `Local media probing`, которая ставит пакет **без** дополнительных наборов.

### Е1. Снять состояние до перезапуска

Сервер всё ещё работает с `--media`. Перечислите в `IDS` **все** успешные
capture id этого прогона — те, что дошли до `complete`. Уберите из списка те,
которых у вас нет (например, `med_c_owner_video`, если вы его не выполняли).

`IDS` — это **массив**, а не строка через пробел: строку `bash` разбил бы на
слова, а `zsh` — нет, и цикл получил бы один склеенный id. Массив одинаково
перебирается в обеих оболочках. Строки `IDS+=(…)` раскомментируйте только для
тех capture, которые у вас действительно были:

```bash
IDS=(med_b_control_audio med_c_silent_control)
# IDS+=(med_b_owner_audio)       # только если выполняли MED-B со своим файлом
# IDS+=(med_c_owner_video)       # только если выполняли MED-C C2
printf '%s\n' "${IDS[@]}"
```

Проверьте вывод: **каждый id на своей строке**. Если в одной строке оказалось
несколько id через пробел, массив не задан — не идите дальше, задайте `IDS`
заново.

```bash
for id in "${IDS[@]}"; do
  for kind in "" "/content"; do
    name=$([ -z "$kind" ] && echo record || echo content)
    code=$(ucurl -sS -o "$ACC/snap/before_${id}_${name}.json" \
                 -w '%{http_code}' "$API/v1/captures/$id$kind")
    echo "$code" > "$ACC/snap/before_${id}_${name}.status"
    echo "before $id $name $code"
  done
done
```

Все строки должны показать `200`. Если какая-то показала другое, дальше идти
незачем: MED-E останется `BLOCKED`, потому что сравнивать будет нечего.

> Снимки в `$ACC/snap` — это полные тела ответов, и для файла владельца они
> содержат его `ref` и дайджест. Они остаются у вас локально. Наружу из этого
> шага уходит только вывод `restart_check.py`, который печатает вердикты и не
> печатает ни дайджестов, ни ссылок, ни имён файлов.

### Е2. Остановить процесс и запустить заново

**Это третья и последняя жизнь сервера.**

В терминале сервера: `Ctrl+C`, дождитесь, что процесс действительно завершился,
и запустите заново **тем же** `--data-dir` и **без** `--media`:

```bash
"$PY" -m unimem_api --data-dir "$ACC/data" --token-file "$ACC/api.token" --port 8793
```

```bash
ucurl -sS "$API/health"; echo
```

Никаких `--media`, `--pdf-ocr`, `--image-ocr` и другого каталога данных. Каталог
`$ACC/data` не трогайте, не чистите и не копируйте.

### Е3. Снять состояние после и сравнить

`IDS` — тот же массив, что в Е1. Если это новый терминал, задайте его заново
теми же строками и снова проверьте `printf '%s\n' "${IDS[@]}"`.

```bash
for id in "${IDS[@]}"; do
  for kind in "" "/content"; do
    name=$([ -z "$kind" ] && echo record || echo content)
    code=$(ucurl -sS -o "$ACC/snap/after_${id}_${name}.json" \
                 -w '%{http_code}' "$API/v1/captures/$id$kind")
    echo "$code" > "$ACC/snap/after_${id}_${name}.status"
    echo "after  $id $name $code"
  done
done
```

Если вы делали перерыв, пути к исходным файлам могли не сохраниться — задайте их
снова теми же значениями. Список пар ниже должен соответствовать вашему `IDS`:

```bash
"$PY" "$ACC/bin/restart_check.py" "$ACC/data" "$ACC/snap" \
  "med_b_control_audio:$ACC/in/control.wav" \
  "med_c_silent_control:$ACC/in/control-silent.mp4"
# добавьте "med_b_owner_audio:$MY_AUDIO" и/или "med_c_owner_video:<ваш путь>",
# если эти строки у вас были
```

### Е4. Долговечный `failed` и отказ по умолчанию

Упавший capture из MED-D обязан остаться упавшим, а новая медиа-попытка —
отклониться: сборка без `--media` снова ничего не принимает.

```bash
ucurl -sS "$API/v1/captures/med_d_mismatch" \
  -o "$ACC/out/e_d_record.json" -w 'MED-D record HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/e_d_record.json"
ucurl -sS -o /dev/null -w 'MED-D content HTTP %{http_code}\n' \
  "$API/v1/captures/med_d_mismatch/content"
```

```bash
REF_A=$("$PY" "$ACC/bin/show.py" ref "$ACC/out/a_upload.json")
"$PY" "$ACC/bin/envelope.py" med_e_refused_after_restart audio "$WAV" "$REF_A" \
  > "$ACC/out/e_envelope.json"
ucurl -sS -X POST "$API/v1/captures" -H 'content-type: application/json' \
  --data-binary @"$ACC/out/e_envelope.json" \
  -o "$ACC/out/e_capture.json" -w 'capture HTTP %{http_code}\n'
"$PY" "$ACC/bin/show.py" record "$ACC/out/e_capture.json"
ucurl -sS -o /dev/null -w 'record HTTP %{http_code}\n' \
  "$API/v1/captures/med_e_refused_after_restart"
```

| | Проверка |
| --- | --- |
| 1 | до перезапуска все `GET` вернули `200` — иначе `BLOCKED` |
| 2 | после перезапуска все `GET` снова `200`, id те же, статусы снова `complete` |
| 3 | снимки `CaptureRecord` не изменились ни в одном поле |
| 4 | снимки `ContentObject` не изменились ни в одном поле |
| 5 | `metadata.media` на месте и прочитана процессом **без** `--media` |
| 6 | оригиналы побайтово те же во всех строках |
| 7 | `ИТОГ ПРОВЕРКИ: PASS` |
| 8 | MED-D: запись по-прежнему `200` и `failed`, content по-прежнему `404` |
| 9 | новая медиа-попытка: `422 unsupported_payload`, затем `404` |

**Провал, если:** любой `GET` после перезапуска вернул не `200`; статус
изменился; снимок записи или содержимого изменился в любом поле;
`metadata.media` пропала или изменилась; оригинал отличается от отправленного;
`ИТОГ ПРОВЕРКИ` не `PASS`; упавший capture из MED-D ожил или пропал; новая
медиа-попытка была принята.

**Что прислать:** вывод `restart_check.py` целиком (он компактен и безопасен для
строки с файлом владельца: ни дайджестов, ни ссылок, ни имён файлов — в том
числе и когда проверка отказывает); `status` и HTTP-коды по MED-D после
перезапуска; HTTP-код и `error.code` новой медиа-попытки и код последующего
`GET`; и одну строку, подтверждающую, что последний сервер запущен с **тем же**
`--data-dir` и **без** `--media`.

> Вердикт `BLOCKED` с пометкой «проверку целостности выполнить не удалось»
> означает, что проверка не смогла **состояться**. Это не вывод о продукте:
> MED-E остаётся `BLOCKED`, пока проверка не выполнена. Категория отказа названа
> без значений намеренно; смотрите подробности локально.

---

## Завершение прогона

Остановите сервер (`Ctrl+C`). Каталог `$ACC` — это ваши доказательства:
оставьте его как есть. Ничего удалять не нужно: ни одна команда выше ничего не
удаляла, и удалять что-либо теперь тоже не требуется.

Обычная установка UniMem всё это время не читалась и не менялась: у неё свой
каталог данных и свой порт.

---

## Автоматические доказательства — и почему они фазу не закрывают

**Этот раздел — не результат владельца.** Он существует, чтобы автоматическое и
человеческое доказательства не перепутались между собой.

Задача CI `Local media probing` ставит системный FFmpeg, ставит пакет **без
дополнительных наборов** и запускает медийные наборы с запретом на пропуски:
все поддержанные семейства через настоящий `ffprobe`, настоящий HTTP к
настоящему процессу сервера до долговечного `COMPLETE`, псевдонимы контейнеров,
длительности, кодеки, параметры дорожек, обе линии отказов, подачу байтов через
`fd:`, ограничение протоколов, перезапуск и обратное чтение, старт обычного
сервера с намеренно опустошённым `PATH`, и отдельную проверку того, что наборы
действительно выполнялись, а не были пропущены. Задача `Quality gates` при этом
работает на машине **без** FFmpeg и доказывает, что обычная сборка его не
требует.

Это много — и это **не** приёмка владельца:

- CI не запускает документированные команды оператора на **вашей** машине;
- CI не отправляет и не оценивает **ваш** файл;
- CI не выносит человеческого суждения о правдоподобии структурного описания;
- CI по конвенции этого репозитория макро-фазу **не закрывает** —
  [ADR-023](ADR/ADR-023-local-ffprobe-media-capability.md#macro-phase-5a-is-not-closed-here)
  говорит это прямо.

**Ни одна строка [таблицы результатов](#таблица-результатов) не заполняется из
CI.** Зелёный CI не даёт `PASS` ни одному сценарию MED-A…MED-F.

---

## Таблица результатов

Статусы ставит **только владелец**. Допустимые значения: `NOT_RUN`, `PASS`,
`FAIL`, `BLOCKED`. Новый полный прогон начинает свою таблицу заново с `NOT_RUN`.

**Приёмка ещё не выполнялась.** Все строки — `NOT_RUN`, и это правда, а не
заготовка.

| Сценарий | Статус владельца | Доказательство | Заметки |
| --- | --- | --- | --- |
| Предусловие — `prereq.py` | NOT_RUN | — | не строка приёмки: без него медиа-сценарии `BLOCKED` |
| MED-A — обычная установка остаётся без медиа | NOT_RUN | — | разделение возможностей развёртывания |
| MED-B — настоящий standalone-звук | NOT_RUN | — | контроль или файл владельца — указать явно |
| MED-C — видео и правило немого видео | NOT_RUN | — | немой контроль обязателен и обязан пройти |
| MED-D — заявление и наблюдение расходятся | NOT_RUN | — | `422 processing_failed`, долговечный `failed` |
| MED-E — долговечность и чтение без capability | NOT_RUN | — | последний сервер — **без** `--media` |
| MED-F — настоящий медиафайл владельца | NOT_RUN | — | суждение человека; узкое намеренно |

Строка предусловия — **подготовка, а не седьмой сценарий**: без неё MED-B…MED-F
нечем выполнять, но отдельного вердикта о продукте у неё нет.

> **Macro Phase 5A ОТКРЫТА.** Закрыть её может только прогон владельца,
> записанный в этой таблице и в [истории](#история-прогонов-владельца). Сейчас
> такого прогона нет.

---

## История прогонов владельца

Этот раздел — **архив**. Он не переписывается позднейшими уточнениями и сам по
себе закрытия фазы не даёт.

**Прогонов владельца пока не было.** Ни одной даты, ни одной машины, ни одного
окружения и ни одного результата здесь не записано, потому что записывать нечего.

Когда прогон состоится, он добавляется сюда отдельной записью по образцу
[приёмки изображений](MANUAL_IMAGE_ACCEPTANCE.md#история-прогонов-владельца):
дата, базовая версия (точный SHA слитого `main`), характер прогона, окружение,
итог по строкам.

**Неудавшийся или заблокированный прогон сохраняется как есть.** Если первый
прогон даст `FAIL` или `BLOCKED`, он остаётся в этом разделе дословно, а
закрывающий прогон выполняется заново и целиком — новый каталог, новые capture
id, все шесть строк подряд. Ни одна строка прошлого прогона в новую таблицу не
переносится, включая его `PASS`.

---

## Правило закрытия

**Macro Phase 5A закрывается только когда выполнено всё перечисленное:**

1. **Этот чек-лист уже слит в `main`.** Приёмка по неслитому черновику фазу не
   закрывает.
2. **Владелец выполнил свежий полный прогон** против **названного слитого
   `main`**, и точный SHA этого коммита записан.
3. **Checkout был чистым:** `git status --porcelain` пуст.
4. **Все обязательные строки приёмки — `PASS` в одном и том же прогоне.**
   MED-A, MED-B, MED-C, MED-D, MED-E и MED-F. Ни одного `NOT_RUN`, ни одного
   `FAIL`, ни одного `BLOCKED`. `BLOCKED` — **не** разновидность прохождения:
   отсутствие `ffprobe` закрытие останавливает.
5. **Результат записан как человеческое доказательство владельца** — прогон
   руками на локальной машине, не CI, не облачная сессия, не автоматика, — и
   записан именно так.
6. **Использован хотя бы один настоящий файл владельца** (MED-F), и в
   долговечной записи он назван только тем, чем владелец согласился его назвать.
7. **Доказательства CI и доказательства владельца остаются раздельными.** Ничто
   не выдаёт прогон владельца за результат CI и ничто не выдаёт результаты CI за
   ручную проверку.
8. **Закрытие не добавляет ни одной возможности.** Ни формата, ни маршрута, ни
   поля контракта, ни enum, ни состояния жизненного цикла, ни зависимости, ни
   задачи CI, ни теста. `SCHEMA_VERSION` остаётся `0.3`, и совместимость не
   трогается.
9. **Семантика Phase 5A не пересматривается:** `audio@0.1` и `video@0.1`, пять
   поддержанных семейств, фиксированное отображение в контейнеры, `segments = []`,
   один `ORIGINAL`-ассет, `422` на расхождении и `503` на отсутствии доверенного
   результата остаются ровно такими, какими были отгружены.
10. [ADR-021](ADR/ADR-021-original-first-time-based-media-ingestion.md),
    [ADR-022](ADR/ADR-022-engine-independent-media-processing.md) и
    [ADR-023](ADR/ADR-023-local-ffprobe-media-capability.md) **не редактируются**:
    каждый описан по своему PR и был точен, когда писался. Принятое решение не
    переписывают из-за того, что сдвинулась контрольная точка.
11. **Закрывающее изменение — только документация**, и проверки качества на нём
    проходят.

**Чего для закрытия недостаточно:**

- **зелёного CI.** Автоматические наборы макро-фазу не закрывают — это
  конвенция репозитория, и [ADR-023](ADR/ADR-023-local-ffprobe-media-capability.md#macro-phase-5a-is-not-closed-here)
  говорит это прямо;
- **частичного прогона.** Пять `PASS` из шести фазу не закрывают, как не закрыли
  Macro Phase 4 четыре `PASS` из пяти;
- **смешивания прогонов.** Строки, полученные на разных коммитах или в разных
  прогонах, не складываются в одну таблицу;
- **прогона на более раннем `main`**, чем тот, в котором этот файл появился.

Фазы 0, 1, 2, 3 и 4 закрыты и закрытыми остаются; это правило их не касается.

---

## Известные ограничения этого чек-листа

Это список того, чего прогон **не** доказывает, — чтобы его результат не читали
шире, чем он есть:

- Он не перебирает все пять поддержанных семейств вручную: WAV, MP3, OGG, MP4 и
  WebM полностью покрыты задачей CI `Local media probing` на настоящем
  `ffprobe`, и от машины к машине этот перебор не меняется.
- Он не проверяет `503 media_probe_unavailable`. Достичь его без разрушительных
  действий нельзя: при отсутствующем движке сервер с `--media` просто не
  стартует. Это отображение на границе доставки, и автоматические наборы
  проверяют его именно там.
- Он не проверяет подачу байтов через `fd:`, ограничение протоколов, таймаут в
  30 секунд и бюджет вывода в 1 МиБ. Это внутренние границы адаптера, и их
  проверяет CI.
- Он не проверяет ошибочные и повреждённые входы, ссылки на неположенный
  материал и путь в файловой системе вместо ссылки — всё это покрыто
  автоматически.
- Он ничего не говорит о возможностях, которых Phase 5A не отгружала:
  расшифровке речи, интерпретированных сегментах, извлечённой аудиодорожке,
  миниатюрах, ключевых кадрах, тегах контейнера, выборе главной дорожки,
  перекодировании и облачной обработке. Их отсутствие он как раз проверяет.
- Границы `--media` — это **границы, а не песочница**: они не ограничивают
  память и процессорное время дочернего процесса. Этот чек-лист их и не
  испытывает.
- Прохождение контролей не означает, что прошёл ваш файл; прохождение вашего
  файла не означает, что пройдёт следующий.
