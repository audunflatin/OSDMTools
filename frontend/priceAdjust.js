// priceAdjust.js – Prisregulering: skaler OSDM-priser med fast prosent.
// Bruker hjelperne i common.js, slik at flyt og meldinger er like som «Priser fra avstandsfil».

const osdmFileInput = document.getElementById("osdmFile");
const adjustPctInput = document.getElementById("adjustPct");
const adjustBtn = document.getElementById("adjustBtn");
let lastAdjustedFile = null;

// ── OSDM-validering ───────────────────────────────────────────────────────────

async function validateOsdmFile() {
  const summaryEl = document.getElementById("osdmSummary");
  const warningsEl = document.getElementById("osdmWarnings");
  summaryEl.innerHTML = "";
  warningsEl.innerHTML = "";
  resetResult();
  document.getElementById("deliveryFields").style.display = "none";

  const file = osdmFileInput.files[0];
  if (!file) return;

  const res = await validateOsdmUpload(file, "osdmProgress");
  if (!res || !res.ok) {
    summaryEl.innerHTML = renderError(res && res.error);
    return;
  }

  summaryEl.innerHTML = renderOsdmSummary(res);
  warningsEl.innerHTML = renderWarnings(res.warnings, t("osdm_warnings_title"));
  if (res.deliveryId) document.getElementById("previousDeliveryId").value = res.deliveryId;
  document.getElementById("deliveryFields").style.display = "";
}

// ── Faktor-visning ────────────────────────────────────────────────────────────

adjustPctInput.addEventListener("input", () => {
  const pct = parseFloat(adjustPctInput.value);
  const factorDisplay = document.getElementById("factorDisplay");
  if (!isNaN(pct) && pct !== 0) {
    document.getElementById("factorVal").textContent = (1 + pct / 100).toFixed(4).replace(/\.?0+$/, "");
    factorDisplay.style.display = "";
  } else {
    factorDisplay.style.display = "none";
  }
  setValidationError("");
});

["deliveryId", "validFrom", "validTo"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => setValidationError(""));
});

// ── Juster priser ─────────────────────────────────────────────────────────────

function validateAdjustInputs() {
  const pct = parseFloat(adjustPctInput.value);
  const validFrom = document.getElementById("validFrom").value;
  const validTo = document.getElementById("validTo").value;
  let msg = "";
  if (!osdmFileInput.files[0]) msg = t("adjust_error_no_file");
  else if (isNaN(pct)) msg = t("adjust_error_no_pct");
  else if (pct === 0) msg = t("adjust_error_pct_zero");
  else if (!document.getElementById("deliveryId").value.trim()) msg = t("err_delivery_id");
  else if (!validFrom || !validTo) msg = t("err_valid_dates");
  else if (validFrom > validTo) msg = t("err_date_order");
  setValidationError(msg);
  return !msg;
}

function resetResult() {
  hideResult();
  lastAdjustedFile = null;
  document.getElementById("downloadBtn").disabled = true;
  document.getElementById("downloadExcelBtn").disabled = true;
}

adjustBtn.addEventListener("click", async () => {
  resetResult();
  if (!validateAdjustInputs()) return;

  adjustBtn.disabled = true;

  const fd = new FormData();
  fd.append("osdm_file", osdmFileInput.files[0]);
  fd.append("pct", adjustPctInput.value);
  fd.append("delivery_id", document.getElementById("deliveryId").value.trim());
  fd.append("previous_delivery_id", document.getElementById("previousDeliveryId").value.trim());
  fd.append("environment", document.getElementById("environment").value);
  fd.append("optional_delivery", document.getElementById("optionalDelivery").value);
  fd.append("valid_from", document.getElementById("validFrom").value);
  fd.append("valid_to", document.getElementById("validTo").value);

  const { ok, data: res } = await uploadAndWait("/price-adjust", fd, "adjustProgress", "processing");
  adjustBtn.disabled = false;
  if (!ok) {
    showResult(res.error || t("adjust_error_failed"), false);
    return;
  }
  lastAdjustedFile = res.outputFile;
  const factor = res.summary.factor.toFixed(4).replace(/\.?0+$/, "");
  showResult([
    t("adjust_success"),
    ...deliverySummaryLines(res),
    `${t("label_factor")}: ${factor}`,
    ...priceChangeLines(res.summary, res.summary.faresUpdated),
  ].join("\n"), true);
  document.getElementById("downloadBtn").disabled = false;
  document.getElementById("downloadExcelBtn").disabled = false;
});

function downloadAdjusted() {
  if (lastAdjustedFile) window.location.href = `/price-adjust/download/${encodeURIComponent(lastAdjustedFile)}`;
}

function downloadAdjustedExcel() {
  exportExcel("/price-adjust/excel");
}
