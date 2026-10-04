# UniMem: первый запуск на Zen/Linux и Obsidian

Один совместимый комплект: `capture-core 0.3.1rc1`, расширение `0.6.1`,
самостоятельный **UniMem Connector 0.3.0**, delivery **v1/v2/v3**.
Точный source SHA — в `COMPATIBILITY.json`, байты — в `SHA256SUMS`.
Veynrel и Companion не нужны. Это подготовка кандидата, не публикация релиза.

**Комплект подготовлен; постоянная установка ожидает подписи.**
Пока владелец не получит Mozilla-signed XPI, постоянная установка и полный
перезапуск расширения — **BLOCKED**. Временная загрузка не закрывает этот gate.
Результаты проверки конкретного комплекта записываются отдельно от его сборки.

## 1. Получить и проверить комплект

Распакуйте комплект в постоянную папку, например `~/Downloads/unimem-rc`.
В терминале в этой папке:

```sh
sha256sum -c SHA256SUMS
cat COMPATIBILITY.json
```

Если собираете сами: нужны Python 3.13+, `uv` и Node 22+/npm. В чистом checkout
нужного source SHA выполните одну команду (выходной каталог должен быть новым):

```sh
python3 scripts/build_candidate.py --output "$HOME/Downloads/unimem-rc"
```

Она собирает **committed HEAD**, используя существующие wheel/browser/Connector
builders. Не смешивайте файлы разных комплектов. Архив исходников расширения
предназначен для проверки Mozilla; устанавливаемый ZIP — `*-dev.zip`.
Сборка скачивает build-зависимости, но не модели. В комплекте нет weights,
node_modules, данных, credentials, тестовых профилей и путей каталога сборки.

## 2. Установить сервис из wheel

Для Manjaro/Linux x86_64 нужен Python 3.13+ с venv/pip. Для OCR нужен системный
Tesseract с `eng` и `rus`; для vision — bubblewrap (`/usr/bin/bwrap`), CPU
AVX2/FMA/F16C. Если этих программ нет, установите их штатным пакетным менеджером
самостоятельно. Команды ниже не обновляют системные пакеты.

```sh
python3 -m venv "$HOME/.local/share/unimem-app/venv"
"$HOME/.local/share/unimem-app/venv/bin/python" -m pip install \
  './capture_core-0.3.1rc1-py3-none-any.whl[youtube,asr,images,video]'
export PATH="$HOME/.local/share/unimem-app/venv/bin:$PATH"
unimem setup
unimem diagnose
```

`export PATH` можно один раз добавить в пользовательский shell profile. Без него
используйте полный путь `~/.local/share/unimem-app/venv/bin/unimem`.
Extra-компоненты optional: для текста/страницы достаточно wheel без `[extras]`;
для YouTube — `youtube`, для PNG/JPEG — `images`, для речи — `asr`, для MP4 —
`video`. PDF OCR и структурный media CLI сохраняются, но не расширяют эту приёмку.

| Назначение | Постоянное место по умолчанию |
|---|---|
| Окружение приложения | `~/.local/share/unimem-app/venv` |
| Настройки launcher | `~/.config/unimem/config.toml` (`XDG_CONFIG_HOME` учитывается) |
| Данные | `~/.local/share/unimem` (`XDG_DATA_HOME` учитывается при первом setup) |
| Подготовленные модели | `~/.local/share/unimem-models/asr-base` и `.../vision` |
| Сборки | отдельная папка `~/Downloads/unimem-rc`; не data directory |

В data directory находятся immutable raw store, `unimem.sqlite3`,
`obsidian-delivery.sqlite3`, `api.token` и process lock. Они не зависят от cwd.
Не удаляйте lock-файлы: блокировка принадлежит работающему процессу/worker.
`setup` сохраняет уже существующие config, token и назначения.

**Существующая установка:** до `setup` задайте в config абсолютный прежний
`data_dir` и, если отличается, `token_file`. Используйте прежние credentials и
Connector data.json. Старый `python -m unimem_api --data-dir ...` с его флагами
продолжает работать; одновременно два сервиса с одним каталогом запускать нельзя.

## 3. Один раз подготовить выбранные модели и настройки

```sh
mkdir -p "$HOME/.local/share/unimem-models"
python -m unimem_asr prepare --model-dir "$HOME/.local/share/unimem-models/asr-base"
python -m unimem_vision --profile "$HOME/.local/share/unimem-models/vision"
```

Эти команды только показывают требования. **Явное разрешение загрузки**:

```sh
python -m unimem_asr prepare --model-dir "$HOME/.local/share/unimem-models/asr-base" --download
python -m unimem_vision --profile "$HOME/.local/share/unimem-models/vision" --download
```

ASR: существующий multilingual faster-whisper **base**, CPU int8, около 148 MB
весов; vision: существующий Qwen3-VL-2B Q4_K_M + projector + pinned CPU runtime,
около 1.57 GB загрузки. Другие модели не нужны. Vision — тяжёлая локальная
обработка, до 3 GiB process-tree RSS; свободное место и память необходимы.
Пропустите подготовку и настройки компонента, который не используете.
Отсутствующие/испорченные веса не скачиваются при запуске или открытии страницы.

Откройте `~/.config/unimem/config.toml`. Для всего поддержанного набора:

```toml
data_dir = "/home/YOUR_USER/.local/share/unimem"
youtube = true
image_ocr = true
video_notes = true
audio_model = "/home/YOUR_USER/.local/share/unimem-models/asr-base"
image_description_profile = "/home/YOUR_USER/.local/share/unimem-models/vision"
```

Замените `YOUR_USER`: TOML не разворачивает `$HOME` и `~`, нужны абсолютные пути.
Отключайте ненужные boolean-параметры, удаляйте строки неиспользуемых моделей.
`image_ocr=true` требует обоих языков Tesseract. Конфигурация с явно запрошенным,
но отсутствующим ASR/OCR не выдаётся за работающую; исправьте подготовку или явно
отключите компонент. Vision сообщает причину неготовности в UI.
`unimem diagnose` не импортирует приложение/модели, не запускает процессы,
не открывает базы на запись и не проверяет inference. Наличие папки не означает
готовность модели; runtime readiness показывает страница подключения.

## 4. Настроить два разных credential

До запуска сервиса, один раз:

```sh
unimem destination
unimem show-browser-token
```

Первая команда создаёт **первое** назначение Obsidian и показывает receiver token
один раз. Скопируйте его непосредственно в настройки Connector; не отправляйте
в чат, не добавляйте в argv, shell history, логи или скриншоты. Повтор команды
сохраняет существующие назначения и показывает только их IDs. Если credential
ранее выдан, используйте сохранённый. Он не восстанавливается из server hash;
потеря receiver identity/credential требует отдельного решения, не сброса журнала.

`show-browser-token` — единственная явная команда просмотра browser secret;
диагностика его не печатает. Browser credential разрешает захват/Send, receiver
credential — только получение и подтверждение для своего назначения.

## 5. Установить отдельный Connector и подключить Zen

1. Закройте Obsidian. Распакуйте `unimem-connector-0.3.0.zip` в
   `<vault>/.obsidian/plugins/`: итоговая папка `unimem-connector` содержит
   `main.js` и `manifest.json`. Для первой проверки используйте **пустой тестовый
   vault**. Разрешите Community plugins и включите **UniMem Connector**.
2. В его настройках укажите `http://127.0.0.1:8765`, **receiver token**, папку
   `Inbox/UniMem`. Отдельно разрешите Markdown, исходные изображения и выбранные
   PNG видеокадры. Сохраните настройки. Обновление не включает разрешения.
3. В терминале запустите `unimem start`. Не закрывайте этот терминал.
4. В Connector выполните проверку подключения и явно включите получение.
   Receiver ID и журнал сохраняются в `.obsidian/plugins/unimem-connector/data.json`.
5. Получите подписанный XPI по `MOZILLA_SIGNING.md` из комплекта
   ([исходная инструкция](docs/MOZILLA_SIGNING.md)). В Zen: `about:addons` →
   шестерёнка → **Install Add-on From File…** → matching signed XPI → Add.
   Не отключайте signature verification.
6. В меню значка UniMem выберите **Открыть UniMem**. Введите **browser token** и
   сохраните настройки. По умолчанию он хранится только до полного закрытия Zen.
   **Запомнить токен на этом устройстве** — отдельный явный opt-in: local storage
   не зашифрован, доступен другим программам вашего OS-пользователя. Секреты не
   используют storage.sync. Не включайте этот режим на чужом устройстве.
7. **Проверить подключение** показывает сервис, принятие credential, готовность
   captions/ASR/PNG/OCR/vision/MP4 и назначения. Она работает без YouTube.
   Выбор destination находится у результата; точную папку контролирует Connector.

До подписи для ограниченного dev-smoke: отдельный Zen-профиль →
`about:debugging#/runtime/this-firefox` → Load Temporary Add-on → `*-dev.zip`.
Это **не постоянная установка**, она исчезает после перезапуска.

## 6. Первая заметка и поддержанные материалы

Самый короткий путь: выберите небольшой PNG/JPEG → **Сохранить изображение** →
дождитесь **Готово** → просмотрите результат → выберите destination →
**Отправить в Obsidian** → **Импортировано**. Проверьте заметку и вложение в vault.

| Вход | Поддержанные границы | Что остаётся в UniMem / попадает в Obsidian |
|---|---|---|
| Выделенный текст, страница | явный клик/меню на текущей вкладке; top-level DOM, без iframe/shadow DOM/архива ресурсов | исходный текст/HTML и canonical object в UniMem; прежний захват без новой history/send-функции |
| YouTube captions | доступные субтитры, язык ru/en по заданному порядку, без перевода и скачивания видео; upstream может отказать | captions/evidence в UniMem; Markdown через v1 |
| Локальное аудио | WAV PCM, MP3, OGG Opus; ≤32 MiB, ≤120 s, одна дорожка, 1–2 канала, 8–48 kHz | оригинал и результат в UniMem; только текст через v1 |
| PNG/JPEG | ≤16 MiB, ≤40 млн пикселей; original-only, OCR или отдельное описание | исходник/evidence в UniMem; Markdown и **неизменённый** оригинал с EXIF через v2 |
| MP4 | ≤32 MiB, ≤120 s; одна progressive H.264 и 0–1 AAC, 1–2 канала/8–48 kHz; каждая сторона ≤1920, ≤2 073 600 пикселей, без rotation/interlace | MP4 остаётся в UniMem; текст и до 3 PNG через v3; без кадров — v1 |

MP4: последовательное bounded decode, ≤3600 кадров/12 000 packets и внутренние
бюджеты; даже короткий нестандартный MP4 может быть отклонён. Кадры — первые
реальные PTS не раньше ¼, ½, ¾ длительности, не «важнейшие события». Они уменьшены
до 1024 по длинной стороне, ≤2 MiB каждый. Описание кадров **default off**;
это тяжёлая обработка выборки, не понимание всего видео, без обещанного ETA.
ASR/vision могут ошибаться; сегменты речи и интерпретация изображения раздельны.
OCR не заменяет семантическое описание. Vision-профиль не обещает точную геометрию.

Upload ≠ accepted ≠ processing ≠ complete ≠ imported. До окончания upload не
закрывайте страницу. После durable accepted сервер работает самостоятельно.
Refresh читает прежний ID; результат не запускает новый acquisition/inference.
Send всегда явный. `pending` не значит импорт. Offline/ошибка модели не значит
успех; сохранённый оригинал остаётся. Для v2/v3 один .md без вложений неполон.
Обычный **Сохранить .md…** для audio/YouTube открывает Save As: отмена не удаляет
результат. Пользовательские правки/перемещения после ACK не исправляются polling.

## 7. Каждый день, остановка, перезапуск

```sh
unimem start                 # передний план; Ctrl+C останавливает
unimem diagnose              # read-only, ничего не скачивает и не обрабатывает
```

Закройте и снова откройте сервис, Obsidian и Zen. Data directory должен остаться
тем же. Connector читает прежние receiver ID/journal, Zen — прежнюю историю.
В session-only режиме повторно введите browser token; opt-in persistent token
должен сохраниться. Откройте старый результат, обновите delivery дважды: нового
acquisition/inference и дублирующей заметки быть не должно. При пропавшем ранее
принятом ID сначала проверьте data directory, не создавайте работу автоматически.
Этот gate расширения проверяется **только с подписанным XPI**.

Необязательный пользовательский systemd unit (сервис уже остановлен в терминале):

```sh
mkdir -p "$HOME/.config/systemd/user"
install -m 644 unimem.service "$HOME/.config/systemd/user/unimem.service"
systemctl --user daemon-reload
systemctl --user start unimem
systemctl --user status unimem
journalctl --user -u unimem -n 30
systemctl --user stop unimem
# Только если ВЫ хотите запуск при входе в сеанс:
systemctl --user enable unimem
# Отменить автозапуск:
systemctl --user disable unimem
```

Unit использует сохранённый config, обычный venv и home, без секретов в argv.
При нестандартном пути установки исправьте ExecStart. Не нужны root, lingering,
системная служба или запуск Obsidian. Этот комплект сам автозапуск не включает.
Порт занят: остановите его владельца, не удаляйте lock и не убивайте чужой процесс.

## 8. Обновление без сброса данных

Остановите API и Obsidian; сделайте согласованную резервную копию **всего** data
directory (включая SQLite sidecars, raw store и credential) и vault с
`.obsidian/plugins/unimem-connector/data.json`, заметками/вложениями. Сохраните
config, модели и браузерный профиль отдельно. Не копируйте receiver data.json в
другой независимо работающий vault: это одна привязанная receiver installation.

Проверьте новый комплект/hashes. Установите wheel в то же окружение (или отдельное
окружение с тем же config/data), замените **только main.js и manifest.json**
Connector. Не удаляйте data.json. В Zen вручную установите matching signed XPI с
тем же add-on ID. Проверьте готовность, старый результат и повторный poll.
Разрешения на Markdown/images/video frames остаются раздельными.

**Downgrade не равен замене бинарника.** Старые Connector, не понимающие v3 journal,
останавливают получение; не редактируйте version вручную. Старый API может не
понимать новые canonical/delivery записи. Откат требует согласованной резервной
копии и явного решения о новых данных после неё. Автоматического updater/rollback
нет. Журналы ограничены 1000 delivery, history — 50 ссылок на вид; без автоочистки.

## Открытые gates и границы первой версии

Подпись владельца и signed Zen restart остаются внешним gate до matching XPI.
Точный текущий статус остальных проверок — в `docs/FIRST_RC_VERIFICATION.md`.
Исторические модельные измерения не являются приёмкой нового комплекта.
Windows/macOS/mobile, полный Chromium/Firefox acceptance, автоматические обновления,
длинное видео и дополнительные источники не входят в обязательный Zen/Linux RC.

Git-порядок: **#38 → #39 → #40 → финальный PR**. Все первые три merged;
финальная ветка начата от `origin/main` `f5da0f7`. Финальный PR остаётся открытым.
При изменении base заново проверить diff и обязательный CI; merge/tag/release
этой процедурой не выполняются.
