import { localTransport, ClientError } from "./transport.js";
import { videoClient, videoJobs, VIDEO_KEY, VIDEO_STATES, VIDEO_STAGES } from "./video.js";
import { obsidianClient, deliveryStates } from "./obsidian.js";
import { errorText } from "./presentation.js";

export function mountVideoUi(api, settings) {
  const $ = id => document.getElementById(`video-${id}`);
  const transport = { getToken: () => settings.getToken(), hasPermission: () => api.permissions.contains({ origins: ["http://127.0.0.1/*"] }) };
  const http = localTransport(transport), client = videoClient(http,
    localTransport({ ...transport, timeoutMs: 120000 }), localTransport({ ...transport, maxBytes: 2 * 1024 * 1024, timeoutMs: 30000 }));
  const jobs = videoJobs({ local: api.storage.local, client });
  let rows = [], selected = "", file = null, busy = false, capability = null, result = null, loaded = false, sent = false, enabled = false;
  let urls = [];
  const current = () => rows.find(j => j.operation_id === selected);
  const delivery = () => obsidianClient(http, result?.delivery_version ?? "1");
  const notice = text => { $("notice").textContent = text; };
  const errors = {
    video_disabled: "Видео не включено на сервере: нужен --video-notes и подготовленные зависимости.",
    asr_disabled: "ASR не готов. Подготовьте существующую ASR-модель или явно снимите «Распознать речь».",
    vision_disabled: "Vision-профиль не готов. Подготовьте его или явно снимите «Описать кадры моделью».",
    profile_unavailable: "Выбранный профиль недоступен. Режим и модель автоматически не заменяются.",
    input_size_limit: "Нужен один непустой локальный MP4 до 32 МиБ.",
    unsupported_mime: "MIME не поддержан. Этот сценарий принимает MP4, не WebM/MOV/плейлист.",
    unsupported_container: "Содержимое не соответствует поддержанному MP4.",
    unsupported_codec: "Нужна одна H.264-дорожка и не более одной AAC-дорожки, 1–2 канала, 8–48 кГц; квадратные пиксели.",
    unsupported_structure: "Структура дорожек не поддержана. Субтитры, дополнительные дорожки и изменение формата исключены.",
    unsupported_orientation: "В этом срезе не поддержаны поворот, чересстрочность или изменение размеров внутри ролика.",
    unsupported_timing: "Временная шкала непригодна или содержит неподдержанные разрывы. Точные метки не выдумываются.",
    inconsistent_timing: "Заявленная и декодированная временные шкалы расходятся.",
    invalid_video: "MP4 повреждён или не декодируется. Оригинальная загрузка сохранена.",
    duration_limit: "Превышен предел 120 секунд или допустимая шкала дорожек.",
    pixel_limit: "Максимум 1920 по каждой стороне и 1920×1080 пикселей в кадре.",
    decode_budget_exceeded: "Исчерпан бюджет извлечения. Готового результата нет; оригинал сохранён.",
    budget_exceeded: "Исчерпан бюджет обработки. Тяжёлые процессы остановлены; оригинал сохранён.",
    asr_execution_failed: "ASR завершился ошибкой. Это не результат «речь не обнаружена».",
    asr_budget_exceeded: "ASR остановлен по лимиту времени; готовой расшифровки нет.",
    execution_interrupted: "Обработка прервана. Автоматического повторного inference нет; оригинал сохранён.",
    receiver_upgrade_required: "Нужен Connector с v3 и отдельным разрешением на выбранные PNG-кадры видео.",
  };
  const explain = e => errors[e?.code] ?? (e?.code?.startsWith("vision_") ? `Описание кадра не завершено (${e.code}). Готовый материал не создан; оригинал сохранён.` : errorText(e));
  function clear() {
    for (const url of urls) URL.revokeObjectURL(url);
    urls = []; result = null; loaded = sent = enabled = false;
    $("preview").replaceChildren(); $("markdown").textContent = ""; $("delivery").textContent = "";
  }
  function buttons() {
    $("section").querySelectorAll("button,input,select").forEach(e => { e.disabled = busy; });
    $("describe").disabled = busy || (!$("describe").checked && (!$("frames").checked || !capability?.vision));
    $("language").disabled = busy || !$("speech").checked;
    $("start").disabled = busy || !file || !capability?.ready ||
      ($("speech").checked && !capability.asr) || ($("describe").checked && (!$("frames").checked || !capability.vision));
    $("refresh").disabled = busy || !current()?.file_ref;
    $("retry").disabled = busy || !current()?.file_ref || current().accepted;
    $("result").disabled = busy || !current()?.observed?.result_available;
    $("remove").disabled = busy || !current();
    $("delivery-refresh").disabled = busy || !result;
    $("send").disabled = busy || !result || !loaded || sent || !enabled;
  }
  async function act(work) {
    if (busy) return;
    busy = true; buttons();
    try { await work(); } catch (e) { notice(explain(e)); }
    finally { busy = false; buttons(); }
  }
  async function load() {
    rows = await jobs.list();
    if (!rows.some(j => j.operation_id === selected)) { selected = rows[0]?.operation_id ?? ""; clear(); }
    $("jobs").replaceChildren();
    if (!rows.length) $("jobs").add(new Option("Заданий пока нет", ""));
    for (const j of rows) $("jobs").add(new Option(`${j.captured_at} · ${j.observed ? VIDEO_STATES[j.observed.state] : "Приём не подтверждён"}`, j.operation_id));
    $("jobs").value = selected;
    const j = current(), state = j?.observed;
    $("details").textContent = j ? `ID: ${j.operation_id}. ${j.file_ref ? "Загрузка сохранена." : "Загрузка не подтверждена."} ${state ? VIDEO_STATES[state.state] : "Приём не подтверждён"}. ${state?.state === "running" ? VIDEO_STAGES[state.stage] + "." : ""} ${j.accepted ? "Сервер принял задание; можно закрыть страницу и вернуться к этому ID." : "Проверьте прежний ID перед повторной отправкой."}` : "";
    $("error").textContent = j?.error ? explain(j.error) : state?.error_code ? explain({ code: state.error_code }) : "";
  }
  async function capabilities() {
    capability = await client.capabilities();
    $("capability").textContent = `Видео: ${capability.ready ? "готово" : "не включено"}. ASR: ${capability.asr ? "готов" : "не готов"}. Описание кадров: ${capability.vision ? "готово" : "не готово"}. Выбранные режимы сохраняются без автоматической замены.`;
  }
  async function loadDelivery() {
    loaded = sent = enabled = false;
    if (!result) return;
    const ds = await delivery().destinations(), picker = $("destination"), previous = picker.value;
    picker.replaceChildren();
    for (const d of ds) picker.add(new Option(d.display_name, d.destination_id));
    if (ds.some(d => d.destination_id === previous)) picker.value = previous;
    if (!ds.length) { $("delivery").textContent = "Создайте назначение и подключите отдельный UniMem Connector."; return; }
    const value = await delivery().status(current().observed.capture_id, picker.value);
    loaded = true; sent = !!value.delivery; enabled = result.delivery_version === "1" || value.attachments_enabled;
    const size = result.attachments.reduce((n, a) => n + a.size_bytes, result.markdown_bytes);
    $("delivery").textContent = `Одна заметка и ${result.attachments.length} PNG; ${size} байт. MP4 остаётся в UniMem. ${value.delivery ? deliveryStates[value.delivery.state] : "Ещё не отправлено"}. ${enabled ? "" : errors.receiver_upgrade_required}`;
  }
  function element(tag, text, parent = $("preview")) { const node = document.createElement(tag); node.textContent = text; parent.append(node); return node; }
  async function preview() {
    clear(); result = await client.result(current());
    const c = result.content, facts = c.metadata.video_notes;
    const speech = { not_requested: "ASR не запускался: не запрошен.", no_audio_track: "Нет аудиодорожки; ASR не запускался.", no_speech: "ASR выполнен: речь не обнаружена механизмом.", transcribed: "Автоматическая расшифровка; возможны ошибки." };
    element("h3", "Расшифровка"); element("p", speech[facts.speech_outcome]);
    for (const s of c.segments.filter(s => s.type === "transcript")) element("p", `[${s.temporal.start.toFixed(3)} – ${s.temporal.end.toFixed(3)} с] ${s.text}`);
    for (const a of result.attachments) {
      const facts = c.assets.find(asset => asset.id === a.asset_id).metadata;
      const figure = element("figure", "");
      element("figcaption", `Кадр ${facts.presentation_seconds.toFixed(3)} с; запрошено ${facts.requested_seconds.toFixed(3)} с`, figure);
      const url = URL.createObjectURL(await client.frame(current(), a)); urls.push(url);
      const img = document.createElement("img"); img.src = url; img.alt = `Выбранный кадр в ${facts.presentation_seconds} с`; figure.append(img);
      const text = c.segments.find(s => s.type === "visual" && s.provenance.asset_id === a.asset_id);
      element("p", text ? `Описание этого кадра моделью: ${text.text}` : "Описание моделью не запрашивалось.", figure);
    }
    element("p", "Показаны отдельные статичные кадры, а не все события ролика. Описания могут выдумывать или пропускать детали; они не устанавливают движение, скрытые действия и причины. ASR и vision не исправляют друг друга.");
    $("markdown").textContent = result.markdown; await loadDelivery();
  }
  $("file").onchange = () => act(async () => {
    file = null; clear();
    const files = $("file").files;
    if (files.length !== 1 || !files[0].size || files[0].size > 32 * 1024 * 1024) throw new ClientError("input_size_limit");
    file = files[0]; notice(`${file.size} байт. Формат и временную шкалу проверит сервер. До подтверждённой загрузки страницу нужно держать открытой.`);
  });
  for (const id of ["speech", "frames", "describe"]) $(id).onchange = buttons;
  $("capability-refresh").onclick = () => act(capabilities);
  $("start").onclick = () => act(async () => {
    const chosen = file, selectedModes = { speech: $("speech").checked, frames: $("frames").checked, describe: $("describe").checked, language: $("language").value };
    await capabilities();
    if (!capability.ready) throw new ClientError("video_disabled");
    if (selectedModes.speech && !capability.asr) throw new ClientError("asr_disabled");
    if (selectedModes.describe && !capability.vision) throw new ClientError("vision_disabled");
    clear();
    const j = await jobs.start(chosen, { ...selectedModes, decoder: capability.decoder, sampling: capability.sampling,
      asr_profile: selectedModes.speech ? capability.asr_profile : null, vision_profile: selectedModes.describe ? capability.vision_profile : null },
    stage => notice(stage === "uploading" ? "Загрузка видео: не закрывайте страницу." : "Загрузка сохранена; ожидается подтверждение приёма задания."));
    selected = j.operation_id; file = null; $("file").value = ""; await load();
    notice(j.accepted ? "Задание принято. Сервер продолжит обработку; страницу можно закрыть." : "Приём не подтверждён. Проверьте прежний ID.");
  });
  $("jobs").onchange = () => act(async () => { selected = $("jobs").value; clear(); await load(); });
  for (const [id, retry] of [["refresh", false], ["retry", true]]) $(id).onclick = () => act(async () => { await jobs.refresh(selected, retry); await load(); });
  $("result").onclick = () => act(preview);
  $("remove").onclick = () => act(async () => { if (confirm("Удалить только локальную ссылку? Серверные данные сохранятся.")) { await jobs.remove(selected); clear(); await load(); } });
  $("destination").onchange = () => act(loadDelivery);
  $("delivery-refresh").onclick = () => act(loadDelivery);
  $("send").onclick = () => act(async () => { if (loaded && !sent && enabled) { await delivery().send(current().observed.capture_id, $("destination").value); await loadDelivery(); } });
  api.storage.onChanged.addListener((changes, area) => { if (area === "local" && changes[VIDEO_KEY] && !busy) act(load); });
  const poll = setInterval(() => { if (!busy && current()?.accepted && ["queued", "running"].includes(current()?.observed?.state)) act(async () => { await jobs.refresh(selected); await load(); }); }, 2000);
  window.addEventListener("pagehide", () => { clearInterval(poll); clear(); });
  act(async () => { await load(); try { await capabilities(); } finally { if (current()?.file_ref) { await jobs.refresh(selected); await load(); } } });
}
