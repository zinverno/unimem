import { getExtension } from "./lib/browser.js";
import { settingsStore } from "./lib/settings.js";
import { errorText, jobSummary, downloadMarkdown } from "./lib/presentation.js";
import { deliveryStates } from "./lib/obsidian.js";
const api = getExtension();
const settings = settingsStore(api.storage);
const $ = (id) => document.getElementById(id);
let rows = [], selected = location.hash.slice(1), markdown = null, busy = false;
let deliveryLoaded = false, deliveryExists = false, deliveryRead = 0;
const downloads = new Map();
function notice(text, error = false) { $("notice").textContent = text; $("notice").className = error ? "error" : ""; }
async function message(type, id) {
  const answer = await api.runtime.sendMessage(id === undefined ? { type } : { type, id });
  if (!answer?.ok) throw answer?.error ?? { code: "storage_or_internal_error" };
  return answer.value;
}
async function act(work) {
  if (busy) return;
  busy = true; renderButtons();
  try { await work(); } catch (e) { notice(errorText(e?.code ? e : { code: e?.message }), true); }
  finally { busy = false; renderButtons(); }
}
function renderButtons() {
  document.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  $("jobs").disabled = busy;
  const job = rows.find((j) => j.operation_id === selected);
  $("send").disabled = busy || !job || job.accepted || job.delivery !== "not_sent";
  $("retry").disabled = busy || !job || job.accepted;
  $("preview").disabled = busy || !job?.observed?.result_available;
  $("download").disabled = busy || markdown === null;
  $("destination").disabled = busy;
  $("obsidian-send").disabled = busy || !job?.observed?.result_available || !deliveryLoaded || deliveryExists;
  $("obsidian-refresh").disabled = busy || !job?.observed?.result_available;
}
function render() {
  const picker = $("jobs"); picker.replaceChildren();
  if (!rows.length) picker.add(new Option("Заданий пока нет", ""));
  for (const job of rows) picker.add(new Option(`${jobSummary(job)} · ${job.url} · ${job.created_at}`, job.operation_id));
  const job = rows.find((j) => j.operation_id === selected);
  picker.value = job ? selected : "";
  $("job").hidden = !job;
  if (job) {
    const details = $("details"); details.replaceChildren();
    const fields = { "Отправляемый URL": job.url, "Языки по порядку": job.languages.join(", "), "ID операции": job.operation_id,
      "Подключение": job.connection, "Создано локально": job.created_at, "Приём": job.accepted ? "Подтверждён сервером" : job.delivery === "not_sent" ? "Не отправлено" : "Не подтверждён",
      "Последнее известное состояние": jobSummary(job), "Последняя успешная проверка": job.observed_at ?? "Ещё не было",
      "Capture ID": job.observed?.capture_id ?? "Сервер не выдал", "Content ID": job.observed?.content_id ?? "Сервер не выдал", "Код ошибки обработки": job.observed?.error_code ?? "Нет" };
    for (const [label, value] of Object.entries(fields)) { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = label; dd.textContent = value; details.append(dt, dd); }
    $("job-error").textContent = job.error ? job.accepted && job.error.code === "operation_not_found"
      ? "Ранее принятое задание не найдено. Возможно, сервер запущен с другим data directory. Оно не будет создано заново автоматически."
      : `Текущее состояние не удалось получить: ${errorText(job.error)}` : "";
    $("job-error").className = job.error ? "error" : "";
  }
  renderButtons();
}
async function load() {
  rows = await message("list");
  if (!rows.some((j) => j.operation_id === selected)) {
    selected = rows[0]?.operation_id ?? "";
    clearPreview();
  }
  render();
  await loadDelivery();
}
async function deliveryMessage(type) {
  const answer = await api.runtime.sendMessage({ type, id: selected, destination_id: $("destination").value });
  if (!answer?.ok) throw answer?.error ?? { code: "invalid_response" };
  return answer.value;
}
async function loadDelivery() {
  const read = ++deliveryRead, id = selected;
  deliveryLoaded = false; deliveryExists = false; renderButtons();
  if (!rows.find(j => j.operation_id === id)?.observed?.result_available) {
    $("delivery-state").textContent = "Доступно после завершения обработки."; $("delivery-filename").textContent = ""; return;
  }
  try {
    const destinations = await message("destinations");
    if (read !== deliveryRead || selected !== id) return;
    const picker = $("destination"), previous = picker.value; picker.replaceChildren();
    for (const d of destinations) picker.add(new Option(d.display_name, d.destination_id));
    if (destinations.some(d => d.destination_id === previous)) picker.value = previous;
    if (!destinations.length) { $("delivery-state").textContent = "Сначала создайте destination командой UniMem --create-destination."; return; }
    const result = await deliveryMessage("obsidian-status");
    if (read !== deliveryRead || selected !== id) return;
    $("delivery-filename").textContent = `Имя файла: ${result.suggested_filename}`;
    $("delivery-state").textContent = result.delivery ? deliveryStates[result.delivery.state] : "Ещё не отправлено";
    deliveryExists = Boolean(result.delivery); deliveryLoaded = true;
  } catch (e) {
    if (read === deliveryRead) $("delivery-state").textContent = `Статус не получен: ${errorText(e)}`;
  } finally { if (read === deliveryRead) renderButtons(); }
}
$("destination").onchange = () => act(loadDelivery);
$("obsidian-refresh").onclick = () => act(loadDelivery);
$("obsidian-send").onclick = () => act(async () => {
  if (!deliveryLoaded || deliveryExists) return;
  await deliveryMessage("obsidian-send"); await loadDelivery();
});
async function credential() { const state = await settings.read(); $("languages").value = state.languages.join(","); $("remember").checked = state.remembered; $("credential-state").textContent = state.hasToken ? `Токен задан: ${state.remembered ? "постоянное хранение" : "до завершения сеанса браузера"}.` : "Токен не задан."; }
function clearPreview() { markdown = null; $("markdown").textContent = ""; $("export-state").textContent = ""; }
$("settings-form").addEventListener("submit", (event) => { event.preventDefault(); act(async () => {
  const token = $("token").value; $("token").value = "";
  if (token) await settings.save(token, $("remember").checked, $("languages").value);
  else await settings.saveLanguages($("languages").value);
  await credential(); notice("Настройки сохранены. Проверка подключения выполняется отдельно.");
}); });
$("delete-token").onclick = () => act(async () => { $("token").value = ""; await settings.removeToken(); await credential(); notice("Токен удалён. Локальные ссылки на задания сохранены."); });
$("check").onclick = () => act(async () => { const s = await message("check"); $("connection-state").textContent = `Сервер: ${s.server ? "доступен" : "не подтверждён"}. Токен: ${s.token ? "принят" : "не подтверждён"}. YouTube API: ${s.youtube ? "доступен" : "не подтверждён"}.`; notice(s.error ? errorText(s.error) : "Подключение подтверждено. Доступность субтитров конкретного ролика проверяется при захвате.", Boolean(s.error)); });
$("permission").onclick = () => act(async () => {
  // act invokes work synchronously: request still belongs to this gesture.
  if (!await api.permissions.request({ origins: ["http://127.0.0.1/*"] })) throw { code: "permission_denied" };
  notice("Разрешение на локальный API предоставлено.");
});
$("jobs").onchange = () => { selected = $("jobs").value; location.hash = selected; clearPreview(); render(); act(async () => { if (selected) await message("refresh", selected); await load(); }); };
for (const type of ["refresh", "send", "retry", "again", "remove"]) $(type).onclick = () => act(async () => {
  if (type === "again" && !confirm("Создать новую операцию и повторно получить субтитры этого ролика?")) return;
  if (type === "remove" && !confirm("Удалить только локальную ссылку? Сервер продолжит работу. Для восстановления понадобится ID: " + selected)) return;
  const result = await message(type, selected);
  if (type === "again") { selected = result.operation_id; location.hash = selected; clearPreview(); }
  if (type === "remove") clearPreview();
  await load(); notice("Список заданий обновлён.");
});
$("preview").onclick = () => act(async () => {
  const id = selected;
  clearPreview();
  try {
    const text = await message("markdown", id);
    if (selected !== id) return; // Another UI may have removed this local link.
    markdown = text;
    $("markdown").textContent = markdown;
    $("export-state").textContent = "Markdown получен полностью. Для сохранения файла нажмите «Сохранить .md…».";
  }
  catch (e) { $("export-state").textContent = `Markdown недоступен: ${errorText(e)} Готовая операция сохранена.`; }
});
$("download").onclick = () => act(async () => {
  $("export-state").textContent = "Выберите имя и путь в диалоге сохранения браузера.";
  const result = await downloadMarkdown(api, markdown, selected);
  downloads.set(result.id, result.release);
  // A small Blob can finish before onChanged is delivered to this page.
  const [download] = await api.downloads.search({ id: result.id });
  if (download) finishDownload({ id: result.id, state: { current: download.state } });
});
function finishDownload(delta) {
  if (!downloads.has(delta.id) || !["complete", "interrupted"].includes(delta.state?.current)) return;
  downloads.get(delta.id)(); downloads.delete(delta.id);
  $("export-state").textContent = delta.state.current === "complete" ? "Файл сохранён." : errorText({ code: "download_failed" });
}
api.downloads.onChanged.addListener(finishDownload);
api.storage.onChanged.addListener((changes, area) => { if (area === "local" && changes.youtubeJobs) load().catch(() => notice(errorText({ code: "storage_or_internal_error" }), true)); });
act(async () => {
  const initialError = location.hash.startsWith("#error-") ? location.hash.slice(7) : null;
  await credential(); await load();
  if (selected) { await message("refresh", selected); await load(); }
  if (initialError) notice(errorText({ code: initialError }), true);
});
