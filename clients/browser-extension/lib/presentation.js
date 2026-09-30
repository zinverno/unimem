export const STATES = { queued: "В очереди", running: "Выполняется", complete: "Готово — результат доступен", failed: "Ошибка обработки на сервере", interrupted: "Выполнение прервано" };
export function errorText(error) {
  const code = error?.code;
  const known = {
    missing_token: "Токен не задан. Сохраните его в настройках.", unauthorized: "Токен отклонён (401). После смены серверного токена введите новый и сохраните настройки.",
    unavailable: "Сервер недоступен. Последнее известное состояние сохранено.", timeout: "Время ожидания истекло. Состояние сервера не изменено.",
    invalid_response: "Сервер вернул неподдерживаемый или повреждённый ответ.", invalid_token: "Ожидается токен B1: 43 символа (буквы, цифры, _ и -).",
    invalid_languages: "Введите от 1 до 10 кодов языков через запятую, например ru,en.",
    invalid_url: "Выберите страницу конкретного видео YouTube.", history_full: "Лимит 50 ссылок достигнут. Явно удалите ненужную локальную ссылку; новое задание не отправлено.",
    history_corrupt: "Локальная история повреждена. Новые запросы остановлены; сохранённые данные не удалены.",
    storage_or_internal_error: "Не удалось прочитать или сохранить данные расширения. Откройте задания снова и проверьте прежний ID.",
    operation_not_found: "Операция не найдена. Неподтверждённую доставку можно повторить явно с прежним ID.",
    operation_conflict: "Конфликт ID и параметров (409). Автоматической новой попытки не будет.",
    markdown_unavailable: "Markdown временно недоступен. Готовый capture сохранён; получение результата можно повторить.",
    markdown_too_large: "Markdown превышает лимит доставки 1 МиБ. Материал не обрезан; готовый capture сохранён.",
    destination_not_found: "Назначение не найдено. Обновите список и проверьте выбранный сервер.",
    delivery_history_full: "История доставок заполнена. Новая доставка не принята; прежние подтверждения сохранены.",
    delivery_storage_unavailable: "Хранилище доставок недоступно. Capture сохранён; проверьте состояние перед повторной отправкой.",
    response_too_large: "Ответ превышает лимит 8 МиБ. Результат не обрезан и не сохранён частично.",
    download_failed: "Файл не сохранён: ошибка или отмена загрузки. Операция сохранена, экспорт можно повторить.",
    permission_denied: "Нет разрешения на локальный API. Нажмите «Разрешить доступ к локальному API».",
  };
  if (known[code]) return known[code];
  if (error?.status === 401) return known.unauthorized;
  if (error?.status === 403) return "Доступ запрещён (403). Проверьте разрешения расширения и локального сервера.";
  if (error?.status === 429) return "Сервер ограничил запросы (429). Подождите и обновите статус вручную.";
  if (error?.status === 507) return "История сервера заполнена (507). Новая операция не принята.";
  return "Запрос не выполнен. Проверьте подключение и повторите действие вручную.";
}
export function jobSummary(job) {
  return job.observed ? STATES[job.observed.state] : job.delivery === "not_sent" ? "Не отправлено" : "Приём не подтверждён";
}
export async function downloadMarkdown(api, markdown, operationId, urls = URL) {
  const filename = `youtube-${operationId.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 128)}.md`;
  const url = urls.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  try {
    const id = await api.downloads.download({ url, filename, saveAs: true, conflictAction: "uniquify" });
    return { id, release: () => urls.revokeObjectURL(url) };
  } catch { urls.revokeObjectURL(url); throw new Error("download_failed"); }
}
