/**
 * Turning an outcome into the only UI this connector has: a badge and a title.
 *
 * There is no popup, no notification, and no options page, so these two strings
 * are the entire conversation with the user. They are therefore built from the
 * `outcome` alone — never from an exception, a stack, a response body, or the
 * selected text.
 *
 * That last one matters. The captured material is the user's content: a
 * selection may be a password they highlighted by accident or a paragraph from a
 * private document, and a page snapshot is an entire document including whatever
 * was on screen behind a login. A browser action title is visible to anyone
 * looking at the screen and lands in screenshots. Both are request payload data
 * and nothing else, so neither ever appears here, and neither is logged. Nor do
 * a capture id, a content id, or a URL: they are not wrong to show, they are
 * simply not what a person reading a tooltip needs.
 */

import { OUTCOME } from "./outcomes.js";

/** Sending. */
export const BADGE_BUSY = "...";
/** The server confirmed a complete capture. */
export const BADGE_OK = "OK";
/** Anything else — not saved, or not confirmed. */
export const BADGE_FAIL = "!";

/**
 * What "saving..." says, per capture intent.
 *
 * Two words of honesty: a whole-page capture that reported "saving selection"
 * would be describing something the user did not ask for. The fallback is the
 * selection wording, so a busy report from a caller that names no kind reads
 * exactly as it always has.
 */
const BUSY_TITLES = Object.freeze({
  selection: "UniMem: сохранение выделенного текста…",
  page: "UniMem: сохранение страницы…",
});

/** The one fallback, used for any outcome this map has not been taught. */
const FALLBACK_TITLE = "UniMem: не удалось сохранить";

/** Human wording for a durable lifecycle state a probe found. */
const DURABLE_STATE_TITLES = Object.freeze({
  received: "UniMem: получено, ещё не записано",
  stored: "UniMem: записано, ещё не обработано",
  processing: "UniMem: обработка продолжается",
  failed: "UniMem: сервер не смог обработать материал",
});

/** Human wording for the typed error codes the API documents. */
const SERVER_ERROR_TITLES = Object.freeze({
  unauthorized: "UniMem: токен отклонён. Откройте настройки подключения",
  capture_already_exists: "UniMem: такой захват уже существует",
  content_conflict: "UniMem: результат этого захвата уже существует",
  invalid_capture_state: "UniMem: состояние захвата не допускает обработку",
  not_found: "UniMem: захват не найден",
  unsupported_payload: "UniMem: этот формат пока не поддерживается",
  invalid_capture_envelope: "UniMem: сервер отклонил некорректный запрос",
  invalid_request: "UniMem: сервер отклонил некорректный запрос",
  // Modality-neutral since whole-page capture: an HTML snapshot the server
  // finds no visible text in reaches this code too, and it is not a selection.
  processing_failed: "UniMem: материал не удалось обработать",
  processing_configuration_error: "UniMem: обработчик не настроен на сервере",
  data_integrity_error: "UniMem: сервер не смог прочитать сохранённые данные",
  storage_unavailable: "UniMem: хранилище недоступно",
});

/** What to show the moment the user acts, before anything has happened. */
export function busyFeedback(kind) {
  return { badge: BADGE_BUSY, title: BUSY_TITLES[kind] ?? BUSY_TITLES.selection };
}

/**
 * How a confirmed capture came to be confirmed, as a sentence.
 *
 * All three are the same success — the capture is durable and normalized — and
 * all three get `OK`. They read differently only because the user who just
 * watched the badge sit on `...` is owed a word about why, and "we resent it
 * and the server already had it" is a more reassuring thing to read than
 * silence.
 *
 * `post` is the ordinary case and says nothing extra, because nothing happened
 * worth mentioning.
 */
const CONFIRMATION_TITLES = Object.freeze({
  post: "UniMem: сохранено",
  replay: "UniMem: сохранено (подтверждено повторным запросом)",
  probe: "UniMem: сохранено (подтверждено после сетевой ошибки)",
});

/** What to show once the attempt has resolved, one way or the other. */
export function feedbackFor(result) {
  const outcome = result?.outcome;
  if (outcome === OUTCOME.COMPLETE) {
    return { badge: BADGE_OK, title: successTitle(result) };
  }
  return { badge: BADGE_FAIL, title: failureTitle(result, outcome) };
}

/**
 * Word a success by how it was confirmed, and fall back to plain "saved".
 *
 * A capture this module has no confirmation vocabulary for is still a capture
 * the server called complete, so the fallback says the true thing rather than
 * the detailed one. `probed` is honoured too, so a result shaped by an older
 * path still reads correctly.
 */
function successTitle(result) {
  const known = CONFIRMATION_TITLES[result?.confirmedBy];
  if (known !== undefined) {
    return known;
  }
  return result?.probed === true ? CONFIRMATION_TITLES.probe : CONFIRMATION_TITLES.post;
}

function failureTitle(result, outcome) {
  switch (outcome) {
    case OUTCOME.BLANK_SELECTION:
      return "UniMem: сначала выделите текст";
    case OUTCOME.UNSUPPORTED_PAGE:
      return "UniMem: эту страницу нельзя сохранить";
    case OUTCOME.INJECTION_FAILED:
      return "UniMem: нет доступа к выделению на этой странице";
    case OUTCOME.PAGE_CAPTURE_FAILED:
      // Says only that the page could not be read. Never why, and never with
      // any of the page in it.
      return "UniMem: не удалось прочитать страницу";
    case OUTCOME.UNAVAILABLE:
      return result?.probed
        ? "UniMem: сервер недоступен, результат захвата неизвестен"
        : "UniMem: сервер недоступен";
    case OUTCOME.UNCONFIRMED:
      return "UniMem: сохранение не подтверждено";
    case OUTCOME.DURABLE_STATE:
      return DURABLE_STATE_TITLES[result?.status] ?? FALLBACK_TITLE;
    case OUTCOME.SERVER_ERROR:
      return serverErrorTitle(result);
    case OUTCOME.PROTOCOL_ERROR:
      return "UniMem: неподдерживаемый ответ API";
    case OUTCOME.UNEXPECTED_ERROR:
      // Deliberately says nothing about what threw. Whatever it was, its
      // message is not written for a person reading a toolbar tooltip.
      return FALLBACK_TITLE;
    default:
      return FALLBACK_TITLE;
  }
}

/**
 * A known error code becomes a sentence; anything else becomes the fallback
 * plus its status. The server's own `message` is deliberately not shown: it is
 * written for an operator reading a log, and this is a browser tooltip.
 */
function serverErrorTitle(result) {
  const known = SERVER_ERROR_TITLES[result?.code];
  if (known !== undefined) {
    return known;
  }
  return typeof result?.status === "number"
    ? `${FALLBACK_TITLE} (${result.status})`
    : FALLBACK_TITLE;
}
