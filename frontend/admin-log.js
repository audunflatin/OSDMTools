// admin-log.js – Aktivitetslogg
if (!window.IS_ADMIN) { window.location.replace("/"); }

const LOG_PAGE_SIZE = 50;
let logPage  = 1;
let logTotal = 0;

// Alle hendelsestyper backend logger (log_event). Etikett: t("event_<type>").
const EVENT_TYPES = [
  "login_success", "login_failed", "logout", "password_changed", "account_name_updated", "account_deleted",
  "osdm_generated", "price_adjust", "discount_applied", "excel_exported", "osdm_analyzed", "osdm_fixed",
  "editor_load", "editor_passenger", "editor_relation", "contact_sent", "access_request",
  "admin_user_created", "admin_user_deleted", "admin_set_admin", "admin_request_approved", "admin_request_rejected",
];

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function eventLabel(type) {
  return EVENT_TYPES.includes(type) ? t("event_" + type) : type;
}

function renderEventOptions() {
  const sel = document.getElementById("logEventType");
  const current = sel.value;
  sel.innerHTML = `<option value="">${escapeHtml(t("log_all_events"))}</option>` +
    EVENT_TYPES.map(type => `<option value="${type}">${escapeHtml(eventLabel(type))}</option>`).join("");
  sel.value = current;
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const locale = { no: "nb-NO", en: "en-GB", de: "de-DE", sv: "sv-SE", fr: "fr-FR" }[currentLang] || undefined;
  return d.toLocaleString(locale, {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

/** Kort detaljtekst per hendelse. Returnerer ren tekst – escapes ved visning. */
function formatDetail(event_type, detail) {
  if (!detail || Object.keys(detail).length === 0) return "";
  const join = parts => parts.filter(Boolean).join(" · ");
  switch (event_type) {
    case "login_success":
    case "login_failed":
      return detail.ip ? `IP: ${detail.ip}` : "";
    case "osdm_generated":
      return join([
        detail.deliveryId && `Delivery: ${detail.deliveryId}`,
        detail.environment && `${t("log_d_env")}: ${detail.environment}`,
        detail.validFrom && `${detail.validFrom} → ${detail.validTo}`,
        detail.updatedFares != null && `${detail.updatedFares} ${t("log_d_fares")}`,
      ]);
    case "price_adjust":
      return join([
        detail.delivery_id && `Delivery: ${detail.delivery_id}`,
        detail.factor != null && `× ${detail.factor}`,
        detail.fares_updated != null && `${detail.fares_updated} ${t("log_d_fares")}`,
      ]);
    case "discount_applied":
      return join([
        detail.deliveryId && `Delivery: ${detail.deliveryId}`,
        detail.fareName,
        detail.discountPct != null && `${detail.discountPct}%`,
        detail.carrierCodes?.length && `${t("log_d_carrier")}: ${detail.carrierCodes.join(", ")}`,
        detail.fareCount != null && `${detail.fareCount} ${t("log_d_fares")}`,
      ]);
    case "excel_exported":
      return join([detail.filename, detail.rows != null && `${detail.rows} ${t("log_d_rows")}`, detail.error]);
    case "contact_sent":
      return detail.name ? `${t("log_d_from")}: ${detail.name}` : "";
    case "access_request":
      return join([detail.name, detail.email, detail.org]);
    case "admin_user_created":
      return join([detail.email, detail.is_admin && "Admin"]);
    case "admin_user_deleted":
    case "admin_request_approved":
    case "admin_request_rejected":
    case "account_deleted":
      return detail.email || "";
    case "admin_set_admin":
      return detail.email ? `${detail.email} → ${detail.is_admin ? "admin" : t("log_d_not_admin")}` : "";
    case "password_changed":
      return detail.via === "reset_link" ? t("log_d_via_reset") : "";
    case "editor_load":
    case "osdm_fixed":
      return detail.filename || "";
    default:
      return JSON.stringify(detail);
  }
}

async function loadLog() {
  const tbody    = document.getElementById("logTableBody");
  tbody.innerHTML = `<tr><td colspan="5" class="table-message">${t("log_loading")}</td></tr>`;

  const search    = document.getElementById("logSearch").value.trim();
  const dateFrom  = document.getElementById("logDateFrom").value;
  const dateTo    = document.getElementById("logDateTo").value;
  const eventType = document.getElementById("logEventType").value;
  const status    = document.getElementById("logStatus").value;

  const params = new URLSearchParams({ page: logPage, page_size: LOG_PAGE_SIZE });
  if (search)    params.set("search",     search);
  if (dateFrom)  params.set("date_from",  dateFrom);
  if (dateTo)    params.set("date_to",    dateTo);
  if (eventType) params.set("event_type", eventType);
  if (status)    params.set("status",     status);

  const r = await fetch(`/admin/event-log?${params}`);
  if (!r.ok) {
    tbody.innerHTML = `<tr><td colspan="5" class="table-error">${t("log_load_failed")}</td></tr>`;
    return;
  }

  const data = await r.json();
  logTotal = data.total;
  const totalPages = Math.max(1, Math.ceil(logTotal / LOG_PAGE_SIZE));

  if (!data.entries.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="table-message">${t("log_no_hits")}</td></tr>`;
  } else {
    tbody.innerHTML = data.entries.map((e, i) => {
      // All tekst fra loggen escapes: e-post fra mislykket innlogging og detaljer fra
      // tilgangsskjemaet kommer fra uinnloggede brukere.
      const isError = e.status === "error";
      const badge = isError
        ? `<span class="badge-error">${t("log_status_error")}</span>`
        : `<span class="badge-ok">OK</span>`;
      return `<tr${isError ? ' class="row-error"' : ""}>
        <td class="muted nowrap">${formatDate(e.logged_at)}</td>
        <td>${escapeHtml(e.user_email)}</td>
        <td>${escapeHtml(eventLabel(e.event_type))}</td>
        <td class="center">${badge}</td>
        <td class="detail-text">${escapeHtml(formatDetail(e.event_type, e.detail))}</td>
      </tr>`;
    }).join("");
  }

  const paginationEl = document.getElementById("logPagination");
  const pageInfoEl   = document.getElementById("logPageInfo");
  const prevBtn      = document.getElementById("logPrev");
  const nextBtn      = document.getElementById("logNext");
  document.getElementById("logTotal").innerText = t("log_total_events").replace("{n}", logTotal);

  if (totalPages > 1) {
    paginationEl.style.display = "flex";
    pageInfoEl.innerText = `${t("page_label")} ${logPage} ${t("of_label")} ${totalPages}`;
    prevBtn.disabled = logPage <= 1;
    nextBtn.disabled = logPage >= totalPages;
  } else {
    paginationEl.style.display = "none";
  }
}

function onSearch() {
  logPage = 1;
  loadLog();
}

function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(logTotal / LOG_PAGE_SIZE));
  logPage = Math.max(1, Math.min(totalPages, logPage + delta));
  loadLog();
}

function resetFilter() {
  document.getElementById("logSearch").value    = "";
  document.getElementById("logDateFrom").value  = "";
  document.getElementById("logDateTo").value    = "";
  document.getElementById("logEventType").value = "";
  document.getElementById("logStatus").value    = "";
  logPage = 1;
  loadLog();
}

renderEventOptions();
loadLog();

// Oppdater hendelseslisten og tabellen ved språkbytte
const _prevSetLanguage = window.setLanguage;
window.setLanguage = function (lang) {
  _prevSetLanguage(lang);
  renderEventOptions();
  loadLog();
};
