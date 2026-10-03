import { localTransport, ClientError } from "./transport.js";
import { imageClient, imageJobs, IMAGE_KEY, IMAGE_STATES, previewMime } from "./image.js";
import { obsidianClient, deliveryStates } from "./obsidian.js";
import { errorText } from "./presentation.js";

export function mountImageUi(api, settings) {
  const $ = id => document.getElementById(`image-${id}`);
  const options = { getToken: () => settings.getToken(), hasPermission: () => api.permissions.contains({ origins: ["http://127.0.0.1/*"] }) };
  const http = localTransport(options);
  const client = imageClient(http, localTransport({ ...options, timeoutMs: 120000, maxBytes: 16 * 1024 * 1024 }), localTransport({ ...options, timeoutMs: 30000, maxBytes: 16 * 1024 * 1024 }));
  const jobs = imageJobs({ local: api.storage.local, client }), delivery = obsidianClient(http, "2");
  let rows = [], selected = "", file = null, busy = false, result = null, loaded = false, sent = false, enabled = false, blobUrl = null, descriptionReady = false;
  const current = () => rows.find(j => j.operation_id === selected);
  const labels = { not_requested: "OCR не запрашивался. Смысловой анализ не выполнялся.", text: "OCR выполнен: текст получен.", empty: "OCR выполнен: текст не найден.", skipped: "OCR пропущен по известному ограничению; распознавание не выполнено." };
  const errors = {
    vision_disabled: "Локальное описание не включено. Оператор должен явно подготовить профиль и включить его на сервере.",
    vision_profile_missing: "Локальные файлы модели отсутствуют. Автоматической загрузки нет; original-only и OCR доступны независимо.",
    vision_profile_invalid: "Проверка локальных файлов модели не пройдена. Нужно восстановить закреплённый профиль и перезапустить API.",
    vision_sandbox_unavailable: "Для изоляции локальной модели нужен bubblewrap на сервере.",
    vision_platform_unsupported: "Этот профиль требует Linux x86_64 с AVX2/FMA/F16C.",
    vision_execution_failed: "Локальная модель завершилась ошибкой; описания нет. Оригинальный upload сохранён.",
    vision_input_too_narrow: "После уменьшения вход слишком узкий для этого профиля. Оригинальный upload сохранён.",
    vision_budget_exceeded: "Лимит времени или памяти модели исчерпан. Процесс остановлен, готового описания нет.",
    vision_output_limit: "Ответ достиг лимита. Он не сохранён как полное описание; оригинальный upload сохранён.",
    vision_completion_unverified: "Не удалось подтвердить завершённость ответа. Готового описания нет.",
    vision_invalid_output: "Модель вернула пустой или некорректный ответ. Готового описания нет.",
    vision_refused: "Модель отказалась описывать изображение. Готового описания нет.",
    input_size_limit: "Нужен один непустой PNG/JPEG до 16 МиБ.", pixel_limit: "Изображение превышает лимит 40 миллионов пикселей.",
    unsupported_format: "Поддерживаются только статические PNG и JPEG.", mime_mismatch: "MIME противоречит содержимому файла.",
    invalid_image: "Файл повреждён или не декодируется. Загруженный оригинал остаётся в UniMem.",
    ocr_disabled: "OCR не включён: оператор должен запустить сервер с --image-ocr и локальными rus/eng data.",
    ocr_error: "OCR завершился ошибкой. Это не означает отсутствие текста. Upload сохранён; готового материала нет.",
    image_decoder_unavailable: "Нужен пакет capture-core[images] на сервере. OCR для сохранения оригинала не требуется.",
    budget_exceeded: "Бюджет обработки исчерпан; результат не подтверждён. Upload сохранён.",
    execution_interrupted: "Обработка прервана. Upload сохранён; автоматического повтора нет.",
    receiver_upgrade_required: "Обновите UniMem Connector до 0.2.0, разрешите PNG/JPEG-вложения и включите приём.",
  };
  const explain = e => errors[e?.code] ?? errorText(e);
  const notice = text => { $("notice").textContent = text; };
  function preview(blob) {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = blob ? URL.createObjectURL(blob) : null;
    if (blobUrl) $("preview-image").src = blobUrl; else $("preview-image").removeAttribute("src");
    $("preview-image").hidden = !blobUrl;
  }
  function buttons() {
    $("section").querySelectorAll("button,input,select").forEach(e => { e.disabled = busy; });
    for (const id of ["save", "ocr"]) $(id).disabled = busy || !file;
    $("describe").disabled = busy || !file || !descriptionReady;
    $("refresh").disabled = busy || !current()?.file_ref;
    $("retry").disabled = busy || !current()?.file_ref || current().accepted;
    $("remove").disabled = busy || !current();
    $("result").disabled = busy || !current()?.observed?.result_available;
    $("delivery-refresh").disabled = busy || !result;
    $("send").disabled = busy || !result || !loaded || sent || !enabled;
  }
  function clear() { result = null; loaded = sent = enabled = false; $("markdown").textContent = ""; $("delivery").textContent = ""; preview(null); }
  async function load() {
    rows = await jobs.list();
    if (!rows.some(j => j.operation_id === selected)) { selected = rows[0]?.operation_id ?? ""; clear(); }
    const picker = $("jobs"); picker.replaceChildren();
    if (!rows.length) picker.add(new Option("Заданий пока нет", ""));
    for (const j of rows) picker.add(new Option(`${j.captured_at} · ${j.mode === "ocr" ? "OCR" : j.mode === "describe" ? "Описание моделью" : "Оригинал"} · ${j.observed ? IMAGE_STATES[j.observed.state] : "Приём не подтверждён"}`, j.operation_id));
    picker.value = selected;
    const j = current();
    $("details").textContent = j ? `ID: ${j.operation_id}. ${j.file_ref ? "Upload сохранён." : "Upload не подтверждён."} ${j.observed ? IMAGE_STATES[j.observed.state] : "Приём не подтверждён"}. ${j.accepted ? "Сервер принял задание, страницу можно закрыть." : "Проверьте прежний ID; новый capture автоматически не создаётся."}` : "";
    $("error").textContent = j?.error ? explain(j.error) : j?.observed?.error_code ? explain({ code: j.observed.error_code }) : "";
    buttons();
  }
  async function act(work) {
    if (busy) return;
    busy = true; buttons();
    try { await work(); } catch (e) { notice(explain(e)); }
    finally { busy = false; buttons(); }
  }
  async function choose(files) {
    file = null; clear();
    if (files.length !== 1) throw new ClientError("input_size_limit");
    const chosen = files[0], mime = await previewMime(chosen);
    file = chosen; preview(new Blob([file], { type: mime }));
    notice(`${file.size} байт. Выберите сохранение оригинала, OCR или локальное описание моделью. В vault переносится исходный файл со всей metadata.`);
  }
  $("file").onchange = () => act(() => choose($("file").files));
  $("drop").ondragover = e => { e.preventDefault(); };
  $("drop").ondrop = e => { e.preventDefault(); if (!busy) act(() => choose(e.dataTransfer.files)); };
  for (const [id, mode] of [["save", "original"], ["ocr", "ocr"], ["describe", "describe"]]) $(id).onclick = () => act(async () => {
    const chosen = file; clear();
    $("details").textContent = "Новое задание: upload ещё не подтверждён."; $("error").textContent = "";
    const job = await jobs.start(chosen, mode, phase => notice(phase === "uploading"
      ? "Upload: идёт загрузка. Закрытие страницы может прервать её."
      : "Upload сохранён. Ожидается durable acceptance операции."));
    selected = job.operation_id; file = null; $("file").value = ""; await load();
    notice(job.accepted ? "Операция принята. Сервер продолжает обработку; обновите статус для результата." : "Приём не подтверждён; проверьте прежний ID.");
  });
  async function loadDelivery() {
    loaded = sent = enabled = false;
    if (!result) return;
    const destinations = await delivery.destinations(), picker = $("destination"), previous = picker.value;
    picker.replaceChildren();
    for (const d of destinations) picker.add(new Option(d.display_name, d.destination_id));
    if (destinations.some(d => d.destination_id === previous)) picker.value = previous;
    if (!destinations.length) { $("delivery").textContent = "Создайте назначение и подключите Connector."; return; }
    const value = await delivery.status(current().observed.capture_id, picker.value);
    loaded = true; sent = !!value.delivery; enabled = value.attachments_enabled;
    $("delivery").textContent = `В ${picker.selectedOptions[0].textContent}: одна заметка (${result.markdown_bytes} байт) и одно исходное изображение (${result.attachment.size_bytes} байт). Всего ${result.markdown_bytes + result.attachment.size_bytes} байт. ${value.delivery ? deliveryStates[value.delivery.state] : "Ещё не отправлено"}. ${enabled ? "" : errors.receiver_upgrade_required}`;
  }
  $("jobs").onchange = () => act(async () => { selected = $("jobs").value; clear(); await load(); });
  for (const [id, retry] of [["refresh", false], ["retry", true]]) $(id).onclick = () => act(async () => { await jobs.refresh(selected, retry); await load(); });
  $("remove").onclick = () => act(async () => {
    if (!confirm("Удалить только локальную ссылку? Серверные данные сохранятся.")) return;
    await jobs.remove(selected); clear(); await load();
  });
  $("result").onclick = () => act(async () => {
    result = await client.result(current()); $("markdown").textContent = result.markdown;
    preview(await client.original(current(), result.attachment));
    notice(result.description
      ? "Описание моделью: интерпретация может содержать выдуманные или пропущенные детали. Вход уменьшен без обрезки; мелкие детали могут быть потеряны. Отдельное OCR не выполнялось."
      : `${labels[result.ocr_status]} OCR может ошибаться. Описание изображения не создаётся.`);
    await loadDelivery();
  });
  $("destination").onchange = () => act(loadDelivery);
  $("delivery-refresh").onclick = () => act(loadDelivery);
  $("send").onclick = () => act(async () => {
    if (!loaded || sent || !enabled || !result) return;
    await delivery.send(current().observed.capture_id, $("destination").value); await loadDelivery();
  });
  async function capability() {
    descriptionReady = false;
    try {
      const state = await client.capabilities(); descriptionReady = state.ready;
      $("capability").textContent = state.ready
        ? `Локальный профиль подготовлен: ${state.model}. CPU, 2 threads; до 3 минут. Мелкие детали и сложные связи могут быть потеряны или описаны неверно.`
        : explain({ code: state.code });
    } catch (e) { $("capability").textContent = `Готовность описания не подтверждена: ${explain(e)}`; }
  }
  $("capability-refresh").onclick = () => act(capability);
  api.storage.onChanged.addListener((changes, area) => { if (area === "local" && changes[IMAGE_KEY] && !busy) act(load); });
  window.addEventListener("pagehide", () => preview(null));
  act(async () => { await capability(); await load(); if (current()?.file_ref) { await jobs.refresh(selected); await load(); } });
}
