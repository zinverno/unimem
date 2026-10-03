import { API_ORIGIN } from "./api.js";
import { ClientError, jsonBody, httpError } from "./transport.js";
import { validId } from "./youtube.js";
export const validDestination = id => typeof id === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id);
export const deliveryStates = { pending: "Ожидает получателя", claimed: "Получатель обрабатывает", imported: "Импортировано", conflict: "Конфликт — нужна проверка", failed: "Ошибка импорта", ambiguous: "Результат неоднозначен — нужна проверка" };
export function deliveryReceipt(d, captureId, destinationId, version = "1") {
  if (!d || d.protocol_version !== version || !validDestination(d.delivery_id) || d.destination_id !== destinationId ||
      d.source_capture_id !== captureId || !validId(d.source_content_id) || !Object.hasOwn(deliveryStates, d.state) ||
      !/^[0-9a-f]{64}$/.test(d.markdown_sha256) || !/^unimem-[0-9a-f]{64}\.md$/.test(d.suggested_filename) ||
      typeof d.created_at !== "string" || !Number.isFinite(Date.parse(d.created_at))) throw new ClientError("invalid_response");
  if (version === "2" && (!/^[0-9a-f]{64}$/.test(d.package_sha256) || !Array.isArray(d.attachments) || d.attachments.length !== 1)) throw new ClientError("invalid_response");
  return Object.fromEntries(["delivery_id", "destination_id", "source_capture_id", "source_content_id", "state", "suggested_filename", "markdown_sha256", "created_at"].map(k => [k, d[k]]));
}
export function obsidianClient(http, version = "1") {
  async function request(path, body) {
    const r = await http(API_ORIGIN + path, body ? { method: "POST", body: JSON.stringify(body) } : {});
    if (![200, 202].includes(r.status)) throw await httpError(r);
    return jsonBody(r);
  }
  function validate(capture, dest) { if (!validId(capture) || !validDestination(dest)) throw new ClientError("invalid_message"); }
  return {
    async destinations() {
      const rows = await request("/v1/destinations");
      if (!Array.isArray(rows) || rows.some(d => !validDestination(d.destination_id) || typeof d.display_name !== "string" || d.display_name.length > 80)) throw new ClientError("invalid_response");
      return rows.map(d => ({ destination_id: d.destination_id, display_name: d.display_name }));
    },
    async status(capture, dest) {
      validate(capture, dest);
      const result = await request(`/v${version}/destinations/${dest}/captures/${capture}/delivery`);
      if (!result || !/^unimem-[0-9a-f]{64}\.md$/.test(result.suggested_filename)) throw new ClientError("invalid_response");
      return { suggested_filename: result.suggested_filename, ...(version === "2" ? { attachments_enabled: result.attachments_enabled === true } : {}),
        delivery: result.delivery === null ? null : deliveryReceipt(result.delivery, capture, dest, version) };
    },
    async send(capture, dest) {
      validate(capture, dest);
      return deliveryReceipt(await request(`/v${version}/deliveries`, { source_capture_id: capture, destination_id: dest }), capture, dest, version);
    },
  };
}
