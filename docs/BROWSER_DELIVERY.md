# UniMem 0.4.0: Zen/Linux, задания и доставка

Это dev-расширение для защищённого локального API B1. Оно сохраняет выделенный
текст, HTML страницы и субтитры выбранного YouTube-видео. Состоянием операции
владеет сервер. [Локальные изображения и полноценная доставка в Obsidian](IMAGE_DELIVERY.md)
добавлены в 0.4.0; прежние проверки B2/B3 ниже относятся к своему этапу.

## Сборка и временная установка

Из корня репозитория, Node 22+ и Python 3:

```bash
npm ci --prefix clients/browser-extension
npm test --prefix clients/browser-extension
npm run lint --prefix clients/browser-extension
npm run build --prefix clients/browser-extension
npm run check-package --prefix clients/browser-extension
sha256sum clients/browser-extension/dist/unimem-browser-0.4.0-dev.zip
```

Пакет: `clients/browser-extension/dist/unimem-browser-0.4.0-dev.zip`.
Рядом — `.sha256`; распакованный набор — `dist/unpacked/`.
Сборка воспроизводима: повторная сборка тех же исходников даёт тот же SHA256.
`web-ext` — только dev-зависимость; расширение исполняет исходные ES modules.
`web-ext lint` допускает документированное предупреждение о неиспользуемом в
Firefox service_worker и предупреждение metadata для Android 140; Android не
входит в проверяемые платформы. Ошибок lint быть не должно.

1. Создайте отдельный тестовый профиль Zen. Не используйте рабочий профиль для
   smoke. Для установленного Flatpak пример запуска:

   ```bash
   mkdir -p /tmp/unimem-zen-manual/profile
   flatpak run --filesystem=/tmp/unimem-zen-manual \
     --filesystem="$PWD/clients/browser-extension:ro" \
     app.zen_browser.zen --no-remote --profile /tmp/unimem-zen-manual/profile
   ```

2. Откройте `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on**.
   Выберите ZIP или `dist/unpacked/manifest.json`. В Zen может понадобиться
   закрепить UniMem через меню расширений → Pin to Toolbar.
3. Правой кнопкой по значку → **Открыть UniMem**. Эта же страница доступна через
   настройки дополнения. Обычный левый клик сохраняет выделенный текст.
4. Для Firefox процедура та же. Для Chromium 121+:
   `chrome://extensions` → Developer mode → Load unpacked → `dist/unpacked/`.
   Результаты Firefox/Chromium не являются acceptance Zen.

Временное дополнение исчезает при перезапуске браузера. Нельзя использовать это
как доказательство обычной подписанной установки. Не отключайте проверку подписи.

## Подключение

В Python-окружении из корня репозитория:

```bash
python -m pip install '.[youtube]'
python -m unimem_api --data-dir ./data --init-token
python -m unimem_api --data-dir ./data --youtube
# В другом терминале, показывайте токен только себе:
python -m unimem_api --data-dir ./data --show-token
```

На странице UniMem введите токен, задайте языки (например `ru,en`), нажмите
**Сохранить настройки**, затем **Проверить YouTube API**. Адрес всегда
`http://127.0.0.1:8765`. При отказе host permission есть кнопка **Разрешить доступ к
локальному API**. На смену серверного токена ответ 401 просит ввести новый.
Удаление/замена токена явно доступны; старый токен не отображается повторно.
Пустое поле при сохранении меняет только языки; для смены режима хранения введите
токен снова.

По умолчанию секрет живёт в `storage.session` до конца сеанса браузера.
**Запомнить токен на этом устройстве** включает незашифрованное `storage.local`.
Chromium позволяет дополнительно запретить доступ недоверенным extension
контекстам; Firefox не предоставляет идентичную настройку для local storage.
История не содержит секрета; sync, cookies, browser history и content scripts
не используются. Не прикладывайте токен к скриншотам/логам/issue.

Проверка подключения не делает capture: GET /health проверяет доступность;
read-only status probe с точным `operation_not_found` проверяет credential и
YouTube capability. Она не гарантирует, что у выбранного ролика есть субтитры.

## Сохранить и вернуться к результату

1. На странице нужного видео правой кнопкой по значку → **YouTube → Markdown**.
2. Страница UniMem показывает зафиксированный URL, языки и ID операции. При
   отсутствии токена запись остаётся «Не отправлено»; после подключения нажмите
   **Отправить**. Смена активной вкладки не меняет источник уже выбранного задания.
3. Принятая операция выполняется сервером. Закройте видео и интерфейс.
4. Позднее выберите **Открыть UniMem**, затем задание. Статус будет прочитан по
   прежнему ID; есть ручное **Обновить статус**. Фонового polling нет.
5. Для готового задания нажмите **Получить Markdown**, затем **Сохранить .md…**.
   Предпросмотр — текст; выберите путь в штатном диалоге браузера. Импорт в vault
   не выполняется. Ошибка экспорта оставляет готовый capture нетронутым.

Повторный выбор того же URL и тех же языков открывает существующее задание.
**Сохранить заново** после подтверждения создаёт новый ID и повторный захват.
**Повторить доставку** работает только с неподтверждённым приёмом: сначала GET,
затем при типизированном not-found — тот же ID и параметры. Ранее принятое
задание, которое сервер больше не находит, автоматически не пересоздаётся.
Failed/interrupted не запускают acquisition заново. Недоступность сервера не
превращается в серверное failed; время последнего успешного чтения видно отдельно.

Лимит — 50 ссылок. Автоматического удаления нет. При заполнении новые задания
не отправляются до явного удаления локальной ссылки. Удаление не удаляет серверный
capture и не отменяет работу; заранее сохраните ID, если он ещё понадобится.
Markdown/transcript/ContentObject целиком в истории не хранятся.

Text/page используют прежние envelopes и ограниченный replay: 200/201 означают
complete; 202 не считается успехом. При отсутствии токена открываются настройки.
Восстановление их незавершённой отправки после unload в этой версии не обещается.
HTML capture по-прежнему сохраняет точную сериализацию текущего верхнего DOM;
сервер исключает скрипты/стили из canonical text. Iframe/shadow DOM/ресурсы не
архивируются. Доступ к странице даётся только явным жестом `activeTab`.

## Повторяемые проверки

```bash
python -m pip install -e '.[dev]'
UNIMEM_REQUIRE_CONNECTOR_INTEGRATION=1 pytest \
  tests/integration/api/test_browser_youtube.py \
  'tests/integration/api/test_completed_capture_replay.py::TestThroughTheConnectorItself' \
  'tests/integration/api/test_browser_whole_page_capture.py::TestWholePageThroughTheConnector' \
  'tests/integration/api/test_browser_whole_page_capture.py::TestTheWholePageLostResponse' -q
pytest tests/unit/api/test_security.py tests/unit/api/test_delivery.py \
  tests/unit/api/test_worker.py tests/unit/youtube/test_operations.py \
  tests/integration/api/test_youtube_delivery.py -q
```

Новый cross-language тест запускает настоящий защищённый B1 с synthetic caption
provider и реальным TCP на 8765. Настоящие JS-модули проходят settings → auth →
text/page → потеря POST/GET ответов → восстановление → 200 replay/409 → restart →
Markdown. Проверяются POST, acquisition и capture counts. API не подменяется
«всегда успешным» mock. Port занят — ошибка при required-mode, не скрытый skip.

Для ручного synthetic Zen smoke (не live YouTube), на свободном 8765:

```bash
mkdir -p /tmp/unimem-browser-fixture
python -m tests.delivery_process server success /tmp/unimem-browser-fixture 8765
```

Это исключительно тестовый сервер: токен задаётся `tests/api_auth.py`, видео
`https://www.youtube.com/watch?v=abcdefghijk`, языки `ru,en`, provider из
`tests/youtube_fixtures.py`. Не используйте этот fixture в обычной эксплуатации.
Источник видео в браузере и получение caption через fixture — разные вещи.

Проверка background выполняется **при установленном** дополнении: отправьте
задание, закройте исходную/управляющую вкладки и debugger фонового контекста;
дождитесь выгрузки event page (обычно десятки секунд). Через **Открыть UniMem**
проверьте прежний ID, новое время наблюдения и Markdown, без нового POST/capture.
Reload/uninstall дополнения и перезапуск браузера не заменяют эту проверку.

Для двух видео выберите A, переключитесь на B во время отправки, проверьте URL A.
Затем выполните SPA-переход на B и новое действие: источник должен быть B.
Проверьте 401 после смены токена, denied host permission, restricted page,
остановленный сервер, failed/interrupted и ошибку/отмену сохранения .md.

## Evidence этой ветки

Фактические результаты и hash пакета фиксируются в
[BROWSER_VERIFICATION.md](BROWSER_VERIFICATION.md). Synthetic и live evidence
разделены. Исходники Python production в B2/B3 не изменены.

## Подписанная ежедневная установка — отдельный gate

Стабильный Gecko ID и manifest metadata подготовлены. Подписанного XPI нет;
signed install, permissions после обычной установки и browser restart остаются
**NOT RUN**. Временный ZIP не является подписанной дистрибуцией. Отправка в AMO,
signing/publication и release требуют отдельного разрешения и здесь не выполняются.
