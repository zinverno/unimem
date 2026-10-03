import { localTransport } from "./transport.js";
import { audioClient, audioJobs, AUDIO_KEY, AUDIO_STATES } from "./audio.js";
import { obsidianClient, deliveryStates } from "./obsidian.js";
import { downloadMarkdown, errorText } from "./presentation.js";

export function mountAudioUi(api, settings) {
  const $ = id => document.getElementById(`audio-${id}`);
  const options = { getToken: () => settings.getToken(),
    hasPermission: () => api.permissions.contains({ origins: ["http://127.0.0.1/*"] }) };
  const http = localTransport(options);
  const client = audioClient(http, localTransport({ ...options, timeoutMs: 120000 }));
  const jobs = audioJobs({ local: api.storage.local, client });
  const delivery = obsidianClient(http);
  let rows = [], selected = "", busy = false, markdown = null, receiptLoaded = false, sent = false;
  const downloads = new Map();
  const current = () => rows.find(j => j.operation_id === selected);
  const errors = {
    asr_disabled: "Распознавание не включено на сервере. Сохранённые результаты доступны без модели.",
    input_size_limit: "Выберите непустой файл не больше 32 МиБ.",
    unsupported_format: "Нужен WAV PCM, MP3 или OGG/Opus: одна аудиодорожка, 1–2 канала, 8–48 кГц.",
    mime_mismatch: "Заявленный тип файла не совпадает с содержимым.",
    duration_limit: "Запись превышает лимит 120 секунд.",
    decode_failed: "Не удалось декодировать аудио. Исходник сохранён в UniMem.",
    model_unavailable: "Локальная модель недоступна. Оператор должен подготовить её отдельно.",
    budget_exceeded: "Бюджет обработки исчерпан; процесс остановлен. Исходник сохранён.",
    output_limit: "Результат превышает бюджет ASR. Частичная расшифровка не опубликована.",
    invalid_result: "Движок вернул недопустимый результат; готовая расшифровка не подтверждена.",
    execution_interrupted: "Обработка прервана; автоматического повторного распознавания не будет.",
    execution_unavailable: "Результат обработки не подтверждён. Проверьте локальный сервер.",
  };
  const explain = error => errors[error?.code] ?? errorText(error);
  function notice(text) { $("notice").textContent = text; }
  function buttons() {
    const j = current();
    $("section").querySelectorAll("button,input,select").forEach(e => { e.disabled = busy; });
    $("recognize").disabled = busy || !$("file").files.length;
    $("refresh").disabled = busy || !j?.file_ref;
    $("retry").disabled = busy || !j?.file_ref || j.accepted;
    $("remove").disabled = busy || !j;
    $("preview").disabled = busy || !j?.observed?.result_available;
    $("download").disabled = busy || markdown === null;
    $("obsidian-send").disabled = busy || !receiptLoaded || sent;
    $("obsidian-refresh").disabled = busy || !j?.observed?.result_available;
  }
  function clear() {
    markdown = null; receiptLoaded = false; sent = false;
    $("markdown").textContent = ""; $("delivery-state").textContent = "";
  }
  function render() {
    const picker = $("jobs"); picker.replaceChildren();
    if (!rows.length) picker.add(new Option("Аудиозаданий пока нет", ""));
    for (const j of rows) picker.add(new Option(`${j.captured_at} · ${j.language} · ${j.observed ? AUDIO_STATES[j.observed.state] : "Приём не подтверждён"}`, j.operation_id));
    picker.value = selected;
    const j = current();
    $("details").textContent = j ? `ID: ${j.operation_id}. ${j.observed ? AUDIO_STATES[j.observed.state] : "Приём не подтверждён"}. ${j.accepted ? "Сервер принял задание; страницу можно закрыть." : j.file_ref ? "Файл загружен. Проверьте статус; повтор приёма сохраняет тот же ID." : "Загрузка не подтверждена. После закрытия страницы выберите файл и запустите новое задание."}` : "";
    $("error").textContent = j?.error ? (j.accepted && j.error.code === "operation_not_found"
      ? "Ранее принятое задание не найдено. Проверьте data directory сервера. Новое задание автоматически не создаётся."
      : `Связь или приём не подтверждены: ${explain(j.error)}`)
      : j?.observed?.error_code ? explain({ code: j.observed.error_code }) : "";
    buttons();
  }
  async function load() {
    rows = await jobs.list();
    if (!rows.some(j => j.operation_id === selected)) { selected = rows[0]?.operation_id ?? ""; clear(); }
    render();
  }
  async function act(work) {
    if (busy) return;
    busy = true; buttons();
    try { await work(); } catch (e) { notice(explain(e)); }
    finally { busy = false; buttons(); }
  }
  async function loadDelivery() {
    receiptLoaded = false; sent = false;
    if (!current()?.observed?.result_available) return;
    const destinations = await delivery.destinations(), picker = $("destination"), previous = picker.value;
    picker.replaceChildren();
    for (const d of destinations) picker.add(new Option(d.display_name, d.destination_id));
    if (destinations.some(d => d.destination_id === previous)) picker.value = previous;
    if (!destinations.length) { $("delivery-state").textContent = "Сначала подключите UniMem Connector к назначению."; return; }
    const result = await delivery.status(current().observed.capture_id, picker.value);
    $("delivery-state").textContent = `${result.suggested_filename} · ${result.delivery ? deliveryStates[result.delivery.state] : "Ещё не отправлено"}`;
    receiptLoaded = true; sent = Boolean(result.delivery);
  }
  $("file").onchange = buttons;
  $("form").onsubmit = event => { event.preventDefault(); act(async () => {
    const file = $("file").files[0], language = $("language").value;
    clear();
    const job = await jobs.start(file, language, phase => notice(phase === "uploading"
      ? "Загружается. Не закрывайте страницу: ещё не переданные байты восстановить нельзя."
      : "Файл загружен. Ожидается подтверждение приёма операции."));
    selected = job.operation_id; $("file").value = "";
    await load(); notice(job.accepted ? "Принято. Сервер продолжит обработку после закрытия страницы." : "Приём не подтверждён. Сохранённую ссылку можно проверить ниже.");
  }); };
  $("jobs").onchange = () => act(async () => {
    selected = $("jobs").value; clear();
    if (current()?.file_ref) await jobs.refresh(selected);
    await load(); await loadDelivery();
  });
  for (const [id, retry] of [["refresh", false], ["retry", true]]) $(id).onclick = () => act(async () => {
    await jobs.refresh(selected, retry); await load(); await loadDelivery();
  });
  $("remove").onclick = () => act(async () => {
    if (!confirm(`Удалить только локальную ссылку ${selected}? Серверные данные сохранятся.`)) return;
    await jobs.remove(selected); clear(); await load();
  });
  $("preview").onclick = () => act(async () => {
    markdown = await client.markdown(current()); $("markdown").textContent = markdown;
    notice("Markdown получен полностью. Автоматическая расшифровка может содержать ошибки.");
    await loadDelivery();
  });
  $("download").onclick = () => act(async () => {
    const result = await downloadMarkdown(api, markdown, selected, URL, "audio");
    downloads.set(result.id, result.release);
    const [item] = await api.downloads.search({ id: result.id });
    if (item) finishDownload({ id: item.id, state: { current: item.state } });
  });
  function finishDownload(delta) {
    if (!downloads.has(delta.id) || !["complete", "interrupted"].includes(delta.state?.current)) return;
    downloads.get(delta.id)(); downloads.delete(delta.id);
    notice(delta.state.current === "complete" ? "Файл сохранён." : explain({ code: "download_failed" }));
  }
  api.downloads.onChanged.addListener(finishDownload);
  $("destination").onchange = () => act(loadDelivery);
  $("obsidian-refresh").onclick = () => act(loadDelivery);
  $("obsidian-send").onclick = () => act(async () => {
    if (!receiptLoaded || sent) return;
    await delivery.send(current().observed.capture_id, $("destination").value); await loadDelivery();
  });
  api.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[AUDIO_KEY] && !busy) act(load);
  });
  act(async () => { await load(); if (current()?.file_ref) await jobs.refresh(selected); await load(); await loadDelivery(); });
}
