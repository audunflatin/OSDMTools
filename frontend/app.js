let lastGeneratedFile = null;

function show(id){document.getElementById(id).style.display="block";}
function hide(id){document.getElementById(id).style.display="none";}

function validateGenerateInputs() {
  const datasetId = document.getElementById("datasetId").value.trim();
  const exchangeRate = parseFloat(document.getElementById("exchangeRate").value);
  const validFrom = document.getElementById("validFrom").value;
  const validTo = document.getElementById("validTo").value;

  let msg = "";
  if (!datasetId) msg = t("err_delivery_id");
  else if (!exchangeRate || exchangeRate <= 0) msg = t("err_exchange_rate");
  else if (!validFrom || !validTo) msg = t("err_valid_dates");
  else if (validFrom > validTo) msg = t("err_date_order");
  setValidationError(msg);
  return !msg;
}

async function validateTen() {
  const statusEl = document.getElementById("tenStatus");
  const generateBtn = document.getElementById("generateBtn");

  statusEl.className = "";
  statusEl.innerText = "";
  generateBtn.disabled = true;
  hide("stepOsdm");
  hide("generateForm");

  if (!tenFile.files.length) return;

  show("spinnerTen");

  const fd = new FormData();
  fd.append("tenFile", tenFile.files[0]);

  const r = await fetch("/ui/validate-ten", { method: "POST", body: fd });
  const res = await r.json();

  hide("spinnerTen");

  if (res.ok === true) {
    statusEl.innerText = t("ten_validated");
    statusEl.classList.add("status-ok");
    // Reset OSDM step
    document.getElementById("osdmValFile").value = "";
    document.getElementById("osdmSummary").innerHTML = "";
    document.getElementById("osdmWarnings").innerHTML = "";
    document.getElementById("distanceSummary").innerHTML = "";
    show("stepOsdm");
  } else {
    statusEl.innerText = `${t("err_ten_failed")}\n${res.error || t("unknown_error")}`;
    statusEl.classList.add("status-error");
  }
}

async function validateOsdm() {
  const summaryEl   = document.getElementById("osdmSummary");
  const warningsEl  = document.getElementById("osdmWarnings");
  const distEl      = document.getElementById("distanceSummary");
  const generateBtn = document.getElementById("generateBtn");

  summaryEl.innerHTML  = "";
  warningsEl.innerHTML = "";
  distEl.innerHTML     = "";
  generateBtn.disabled = true;
  hide("generateForm");

  const fileInput = document.getElementById("osdmValFile");
  if (!fileInput.files.length) return;

  const res = await validateOsdmUpload(fileInput.files[0], "osdmValProgress");
  if (!res || !res.ok) {
    summaryEl.innerHTML = renderError(res && res.error);
    return;
  }

  summaryEl.innerHTML = renderOsdmSummary(res, { stations: true });
  warningsEl.innerHTML = renderWarnings(res.warnings, t("osdm_warnings_title"));

  // Distance coverage check (run automatically if TEN file is available)
  if (tenFile.files.length) {
    show("spinnerDistances");
    const fd2 = new FormData();
    fd2.append("distanceFile", tenFile.files[0]);
    fd2.append("osdmFile", fileInput.files[0]);

    try {
      const r2  = await fetch("/ui/validate-distances", { method: "POST", body: fd2 });
      const res2 = await r2.json();
      hide("spinnerDistances");

      if (res2.ok) {
        if (res2.warnings && res2.warnings.length > 0) {
          distEl.innerHTML = renderWarnings(res2.warnings, t("label_distance_check"),
            `${t("distance_stats").replace("{covered}", res2.osdmRcCount - res2.uncoveredCount).replace("{total}", res2.osdmRcCount)}`);
        } else {
          distEl.innerHTML = `<div class="check-ok">${t("distance_ok").replace("{rows}", res2.tenRows).replace("{rc}", res2.osdmRcCount)}</div>`;
        }
      } else if (res2.error) {
        distEl.innerHTML = renderError(`${t("label_distance_check")}: ${res2.error}`);
      }
    } catch {
      hide("spinnerDistances");
    }
  }

  // Autofyll previousDeliveryId med deliveryId fra opplastet fil
  const prevEl = document.getElementById("previousDeliveryId");
  if (prevEl && res.deliveryId) prevEl.value = res.deliveryId;

  // Show generate form and auto-fetch exchange rate
  show("generateForm");
  generateBtn.disabled = false;
  fetchExchangeRate();
}

function updateExchangeRateLabel() {
  const sel = document.getElementById("distanceCurrency");
  if (!sel) return;
  const key = "label_exchange_rate_" + sel.value.toLowerCase();
  const lbl = document.getElementById("exchangeRateLabel");
  if (lbl) {
    lbl.setAttribute("data-i18n", key);
    lbl.textContent = t(key);
  }
}

function onCurrencyChange() {
  updateExchangeRateLabel();
  fetchExchangeRate();
}

async function fetchExchangeRate() {
  const rateInput  = document.getElementById("exchangeRate");
  const rateStatus = document.getElementById("rateStatus");
  const currency   = document.getElementById("distanceCurrency")?.value || "EUR";

  if (currency === "EUR") {
    rateInput.value = "1";
    rateStatus.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5ac39a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-1px;margin-right:4px"><path d="M20 6 9 17l-5-5"/></svg>${t("rate_eur_no_conversion")}`;
    return;
  }

  rateStatus.innerHTML = `<span style="opacity:0.5;">${t("rate_fetching")}</span>`;

  try {
    const r   = await fetch(`/ui/exchange-rate?from_=EUR&to=${currency}`);
    const res = await r.json();
    if (res.ok) {
      const currPerEur = res.rate;
      const eurPerCurr = (1 / currPerEur).toFixed(6);
      rateInput.value  = eurPerCurr;
      rateStatus.innerHTML =
        `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5ac39a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-1px;margin-right:4px"><path d="M20 6 9 17l-5-5"/></svg>` +
        `${t("rate_fetched")
          .replace("{rate}", currPerEur.toFixed(4))
          .replace("{currency}", currency)
          .replace("{date}", res.date)}`;
    } else {
      rateStatus.innerHTML = `<span style="opacity:0.45;">${t("rate_fetch_failed")}</span>`;
    }
  } catch {
    rateStatus.innerHTML = `<span style="opacity:0.45;">${t("rate_fetch_failed")}</span>`;
  }
}

async function generateOsdm() {
  hideResult();
  if (!validateGenerateInputs()) return;

  const generateBtn = document.getElementById("generateBtn");
  generateBtn.disabled = true;
  document.getElementById("downloadBtn").disabled = true;
  document.getElementById("downloadExcelBtn").disabled = true;
  show("spinnerGenerate");
  startProgress();

  const fd = new FormData();
  ["exchangeRate", "validFrom", "validTo", "datasetId", "environment", "optionalDelivery", "previousDeliveryId"]
    .forEach(id => fd.append(id, document.getElementById(id).value));

  try {
    const r = await fetch("/ui/generate-osdm", { method: "POST", body: fd });
    const res = await r.json().catch(() => ({}));
    if (!r.ok || !res.ok || !res.outputFile) throw new Error();

    lastGeneratedFile = res.outputFile;
    const lines = [
      t("osdm_generated"),
      ...deliverySummaryLines(res),
      `${t("label_price_count")}: ${res.summary.pricesUpdated}`,
    ];
    // Relasjoner avstandsfilen ikke dekker beholder malens gamle priser
    if (res.summary.rcsSkipped > 0) {
      const more = res.summary.rcsSkipped - res.summary.rcsSkippedExamples.length;
      lines.push("", `⚠ ${t("rcs_skipped_warning").replace("{count}", res.summary.rcsSkipped)}`,
        ...res.summary.rcsSkippedExamples.map(e => `  • ${e}`),
        ...(more > 0 ? [`  • ${t("and_n_more").replace("{n}", more)}`] : []));
    }
    showResult(lines.join("\n"), true);
    document.getElementById("downloadBtn").disabled = false;
    document.getElementById("downloadExcelBtn").disabled = false;
  } catch {
    showResult(t("err_osdm_failed"), false);
  } finally {
    hide("spinnerGenerate");
    generateBtn.disabled = false;
  }
}

function downloadOsdm() {
  if (lastGeneratedFile) window.location.href = `/ui/download-osdm/${encodeURIComponent(lastGeneratedFile)}`;
}

function downloadAsExcel() {
  exportExcel("/ui/excel-from-generated");
}

function startProgress(){
  const bar = progressBar;
  bar.style.display = "block";
  const t_ = setInterval(async () => {
    const p = await (await fetch("/ui/progress")).json();
    bar.value = p.percent;
    if(p.status === "done" || p.status === "error"){ clearInterval(t_); bar.style.display = "none"; }
  }, 500);
}



["datasetId", "exchangeRate", "validFrom", "validTo"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => setValidationError(""));
});

// Oppdater valutakurs-etikett ved språkbytte
const _prevSetLanguage = window.setLanguage;
window.setLanguage = function(lang) {
  _prevSetLanguage(lang);
  updateExchangeRateLabel();
};
