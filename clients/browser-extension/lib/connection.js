import { errorText } from "./presentation.js";
import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { obsidianClient } from "./obsidian.js";
import { videoClient } from "./video.js";
import { safeError } from "./youtube.js";

// Fixed existing GET routes only. Checking never acquires content or loads a model.
export async function checkConnection(http) {
  const result = { server: false, token: false, youtube: false, image: null, video: null, destinations: [], error: null };
  try {
    const health = await http(`${API_ORIGIN}/health`);
    result.server = health.status === 200 && (await jsonBody(health))?.status === "ok";
    if (!result.server) throw new ClientError("invalid_response");
    result.destinations = await obsidianClient(http).destinations();
    result.token = true;
    const response = await http(`${API_ORIGIN}/v1/image/capabilities`);
    if (response.status !== 200) throw await httpError(response);
    const image = await jsonBody(response);
    if (typeof image?.ocr !== "boolean" || typeof image.original !== "boolean" ||
        typeof image.description?.ready !== "boolean" || typeof image.description.code !== "string" ||
        !/^[a-z_]{1,80}$/.test(image.description.code)) throw new ClientError("invalid_response");
    result.image = { original: image.original, ocr: image.ocr,
      description: { ready: image.description.ready, code: image.description.code } };
    result.video = await videoClient(http).capabilities();
    const probe = await http(`${API_ORIGIN}/v1/youtube/operations/probe-${crypto.randomUUID()}`);
    const error = await httpError(probe);
    if (probe.status !== 404) throw error;
    if (error.code === "operation_not_found") result.youtube = true;
    else if (error.code !== "http_404") throw error;
  } catch (e) { result.error = safeError(e); }
  return result;
}

export function connectionLines(s) {
  const ready = v => v ? "готово" : "не включено / не готово";
  return [
    `Сервис: ${s.server ? "доступен" : "не подтверждён"}. Browser credential: ${s.token ? "принят" : "не подтверждён"}.`,
    ...(s.token ? [`Текст и страница: доступны. YouTube captions: ${ready(s.youtube)}.`] : []),
    ...(s.image ? [`PNG/JPEG: ${s.image.original ? "готово" : "нет декодера Pillow; установите extra images"}. OCR: ${ready(s.image.ocr)}.`,
      `Описание изображений (Qwen3-VL): ${s.image.description.ready ? "готово" : errorText(s.image.description)}.`] : []),
    ...(s.video ? [`Аудио (ASR base): ${ready(s.video.asr)}. MP4: ${ready(s.video.ready)}. Описание выбранных кадров: ${ready(s.video.vision)}.`] : []),
    `Назначения Obsidian: ${s.destinations.length ? s.destinations.map(d => d.display_name).join(", ") : "нет подтверждённых; unimem destination создаёт первое"}.`,
    "Выберите назначение у результата. Папку задаёт Connector; импорт начинается после явной отправки.",
    "Требуется UniMem Connector 0.3.0: delivery v1 (текст), v2 (изображение), v3 (PNG видеокадры). Разрешения включаются отдельно в Connector.",
  ];
}
