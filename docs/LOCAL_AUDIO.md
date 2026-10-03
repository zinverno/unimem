# Локальная расшифровка аудио

Рабочий срез: локальный файл → UniMem → faster-whisper → сохранённый Markdown →
явная отправка существующему UniMem Connector → одна текстовая заметка.
[Измерения и native acceptance](AUDIO_VERIFICATION_2026-10-03.md),
[композиция и ограничения](ADR/ADR-028-local-audio-transcription.md).

## Установка и подготовка модели

Проверено на Linux x86-64, Python 3.13.13. Worker использует Linux process limits
и `/proc`; Windows/macOS ASR не проверены. Core и обычный API не требуют ASR.
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) 1.2.1 — MIT,
поддерживает Python ≥3.9; выбран CPU/int8, без CUDA. Optional extra фиксирует также
CTranslate2 4.8.2 и PyAV 16.1.0. Модель — многоязычная
[Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base),
MIT, revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`.
Единственный профиль: `faster-whisper-base-cpu-int8-v1`.

Из корня checkout, после обычной установки проекта:

```bash
uv pip install --python .venv/bin/python -e '.[asr]'
export ASR_DATA="$HOME/.local/share/unimem-audio"
export ASR_MODEL="$HOME/.cache/unimem/faster-whisper-base"

# Только показывает модель и требования; сеть не используется.
.venv/bin/python -m unimem_asr prepare --model-dir "$ASR_MODEL"
# Отдельное явное разрешение загрузить четыре файла закреплённой модели.
.venv/bin/python -m unimem_asr prepare --model-dir "$ASR_MODEL" --download
```

Файлы модели занимают 147 882 941 байт, плюс небольшой manifest/cache metadata.
Зарезервируйте 1 ГиБ диска для подготовки и место для сохраняемых оригиналов.
Worker ограничен 3 ГиБ адресного пространства и 1,5 ГиБ RSS; это верхние бюджеты,
а не обещание такого расхода. На измеренных образцах RSS составлял 384–451 МиБ.
Подготовка пишет `unimem-model.json` с SHA-256 каждого файла. При запуске ASR
проверяет manifest и локальные файлы; испорченные/отсутствующие веса не скачиваются
повторно. Не коммитьте веса, пользовательские записи или API credentials.

```bash
.venv/bin/python -m unimem_api --data-dir "$ASR_DATA" --init-token
HF_HUB_OFFLINE=1 .venv/bin/python -m unimem_api \
  --data-dir "$ASR_DATA" --audio-model "$ASR_MODEL"
```

Если токен уже создан, пропустите `--init-token`. Чтобы одновременно оставить
YouTube, установите также `.[youtube]` и добавьте прежний `--youtube`.
Открытие расширения, импорт модуля и обычный запуск API модель не загружают.
После подготовки весов распознавание не требует сети и не отправляет аудио
внешнему провайдеру. API доступен только на защищённом loopback.

## Zen и Obsidian

Соберите/установите [dev-расширение](BROWSER_DELIVERY.md), откройте существующую
страницу UniMem через меню расширения. Сохраните API token в общих настройках.
Кнопка проверки YouTube в верхней части страницы проверяет прежнюю YouTube
capability; для audio-only сервера распознавание и статусы доступны независимо.

1. В «Загрузить аудио» выберите файл, язык: русский, английский или авто.
2. Нажмите «Распознать». Во время «Загружается» страницу закрывать нельзя:
   недопереданные байты расширение не хранит. Файлы не попадают в storage.local.
3. После «Принято» страницу можно закрыть. Сервер обрабатывает задание своим
   процессом. «Обновить статус» показывает «Обрабатывается», «Готово» либо
   конкретную ошибку/прерывание. Процентов без измеримого прогресса нет.
4. При возвращении выберите сохранённую операцию и обновите состояние.
   После полного рестарта браузера session-only токен нужно ввести заново.
   При недоступной связи ссылка сохраняется. Для непринятого задания с уже
   загруженным исходником явный повтор использует тот же ID и параметры.
   Принятое, но не найденное задание автоматически не создаётся заново.
5. «Получить Markdown» читает готовый результат. «Сохранить .md» использует
   существующий браузерный Save As. Ошибка экспорта сохраняет серверный результат.
6. Подключите [существующий UniMem Connector](OBSIDIAN_DELIVERY.md) к отдельному
   тестовому vault. Выберите назначение и явно нажмите «Отправить в Obsidian».
   Refresh/poll читает квитанцию; `imported` означает подтверждённый импорт.

Оригинал остаётся в UniMem. Заметка не содержит неработающих ссылок на вложение,
локальных путей, защищённых URL, speaker labels, резюме или перевода.
ASR может ошибаться и галлюцинировать; проверьте текст перед использованием.
«Речь не обнаружена» означает результат VAD/ASR, а не доказательство отсутствия речи.

## Точные входы и бюджеты

| Контейнер | Разрешённый кодек |
|---|---|
| RIFF/WAVE | PCM unsigned 8-bit; signed little-endian 16/24/32-bit |
| MP3 | MPEG layer III (`mp3`/`mp3float` decoder) |
| OGG | Opus |

Нужна ровно одна дорожка, без видео/cover art; 1–2 канала, 8–48 кГц.
Float WAV, big-endian/RF64 WAV, OGG/Vorbis, плейлисты и несколько дорожек
не поддерживаются. Одного расширения filename недостаточно.

MIME: `audio/wav`, `audio/mpeg`, `audio/ogg`; aliases `audio/x-wav`, `audio/wave`,
`audio/mp3`, `application/ogg`, `audio/ogg; codecs=opus`. Регистр и внешние пробелы
нормализуются. Пустой MIME и `application/octet-stream` допускают проверку
сигнатуры. Противоречащий или иной MIME отвергается. Исходные байты не меняются.
Приём проверяет размер/сигнатуру; кодек и повреждения проверяет bounded child.
Декодер получает открытый raw-store stream, принудительный demuxer и запрет
вложенных открытий/протоколов, а не путь/URL из запроса.

| Бюджет | Значение |
|---|---:|
| Исходник | >0, ≤32 МиБ |
| Полностью декодированная запись | ≤120 с |
| Mono PCM после resample 16 кГц | ≤1 920 000 samples |
| Одновременно | 1 ASR process; очередь ≤8 принятых active jobs |
| CPU | 2 inference threads; decoder 1 thread |
| Wall / CPU время процесса | 300 с / 300 с |
| Адресное пространство / monitored RSS | 3 ГиБ / 1,5 ГиБ |
| Результат | ≤4 000 сегментов, ≤2 МиБ UTF-8 текста |
| Сохранённые operation receipts | ≤10 000, без автоматического удаления |

При лимите процесс действительно уничтожается и reap/join завершается.
Kernel alarm ограничивает также осиротевший процесс. Это process containment,
не полноценная sandbox для произвольных уязвимостей медиадекодера.
Исходник и уже подтверждённый канонический результат сохраняются при отказах.
Ошибки различают `decode_failed`, `model_unavailable`, `budget_exceeded`,
`duration_limit`, `invalid_result`, `failed` и недоказанный `interrupted`.
Успешный `no_speech` сохраняет результат без выдуманных слов/таймкодов.

## HTTP загрузка и повторное чтение

API использует прежний бинарный multipart upload; аудио не кодируется в base64.
Пример для operator shell с `curl` и Python. Credentials передаются через
закрытый файл заголовков, а не через URL или аргумент с текстом токена:

```bash
umask 077
.venv/bin/python - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['ASR_DATA'])
(root / 'curl.headers').write_text(
    'Authorization: Bearer ' + (root / 'api.token').read_text().strip() + '\n')
PY
curl --fail --silent --show-error --header @"$ASR_DATA/curl.headers" \
  -F 'file=@/path/to/voice.ogg;type=audio/ogg' \
  http://127.0.0.1:8765/v1/uploads > "$ASR_DATA/upload.json"

.venv/bin/python - <<'PY'
import os, json, uuid
from pathlib import Path
from datetime import datetime, UTC
root = Path(os.environ['ASR_DATA'])
request = dict(operation_id=str(uuid.uuid4()),
    file_ref=json.loads((root / 'upload.json').read_text())['file_ref'],
    declared_mime='audio/ogg', language='ru', captured_at=datetime.now(UTC).isoformat(),
    profile='faster-whisper-base-cpu-int8-v1')
(root / 'audio-request.json').write_text(json.dumps(request))
print(request['operation_id'])
PY
curl --fail --silent --show-error --header @"$ASR_DATA/curl.headers" \
  -H 'Content-Type: application/json' --data-binary @"$ASR_DATA/audio-request.json" \
  http://127.0.0.1:8765/v1/audio/operations
```

Сохраните напечатанный ID как `ASR_OPERATION`. Повторяйте **тот же JSON**, если
ответ приёма потерян: первый приём — 202, равный replay — 200, конфликт — 409.
Новый ID означает новое явно запрошенное задание, даже при тех же исходных байтах.

```bash
curl --fail --silent --show-error --header @"$ASR_DATA/curl.headers" \
  "http://127.0.0.1:8765/v1/audio/operations/$ASR_OPERATION"
# Только после complete:
curl --fail --silent --show-error --header @"$ASR_DATA/curl.headers" \
  "http://127.0.0.1:8765/v1/audio/operations/$ASR_OPERATION/markdown" > transcript.md
# Вне HTTP; CAPTURE_ID берётся из complete operation. Output должен быть новым файлом.
.venv/bin/python -m unimem_asr render --data-dir "$ASR_DATA" \
  --capture-id CAPTURE_ID --output transcript-offline.md
```

Обычный API без `--audio-model` также читает прежние статусы/Markdown и replay;
новые ASR jobs отказывает с `asr_disabled`. Рендер CLI и доставка работают без
модели/декодера. При рестарте недоказанный running становится interrupted;
queued сохраняется до запуска сервера с ASR. Старые complete media captures
не перерабатываются.

Raw store содержит исходные байты по SHA-256; `unimem.sqlite3` — CaptureRecord,
один immutable AUDIO ContentObject с TRANSCRIPT сегментами/provenance/временами
и audio operation. Производный текст связан с исходным asset/capture, а не
замаскирован под самостоятельный оригинальный текстовый файл.
Delivery snapshot хранится прежним Obsidian store: digest, capture+destination
replay, receiver auth, create-only, journal и ACK сохранены. Лимит 1 МиБ не менялся:
большой результат доступен для обычного экспорта, отправка получает
`markdown_too_large`, текст не обрезается.

## Проверка реальной модели

Детерминированные tests/CI используют fake output для контрактов. Отдельный
manual smoke действительно вызывает установленную модель, ограниченный worker,
сохраняет canonical content, рендерит Markdown и записывает измерения:

```bash
.venv/bin/python scripts/audio_real_smoke.py --input /path/to/authorized.wav \
  --model-dir "$ASR_MODEL" --data-dir /tmp/unimem-audio-smoke-NEW --language ru
```

Используйте новый каталог. Python socket connect/DNS/sendto запрещены audit hook;
HF работает в offline mode. Это проверка Python network boundary, а не OS packet
capture. Файлы `smoke.json`, `transcript.md`, raw и SQLite остаются локально.
Для независимого peak RSS каждый образец запускайте отдельным процессом.
Пример измерений и честная оценка ошибок: [отчёт](AUDIO_VERIFICATION_2026-10-03.md).

Последующее [сравнение base и small](asr-quality/RESULTS.md) на фиксированном
небольшом наборе хранит отдельные исходные гипотезы, WER и измерения ресурсов.
Small улучшил общий WER, но добавил смысловую ошибку в денежной команде;
проверка не привела к новому production-профилю. Base остаётся ограниченным
стартовым профилем, прежние результаты не переписаны.
