// common.js – felles hjelpere for sider med OSDM-opplasting, fremdrift og resultat.
// Lastes etter i18n.js og før side-spesifikk JS.

const MAX_UPLOAD_MB = 5000;

// ── Fremdrift ─────────────────────────────────────────────────────────────────
// I HTML: <div id="xProgress" class="upload-progress" style="display:none;"></div>
// Innholdet (stolpe, prosent, steg) fylles inn her, så markupen finnes ett sted.

document.querySelectorAll(".upload-progress").forEach(el => {
  el.innerHTML = `<div class="progress-track"><div class="progress-fill"></div></div>
    <div class="progress-info"><span class="spinner-sm"></span><span class="progress-pct">0%</span><span class="progress-stage"></span></div>`;
});

function showProgress(containerId, pct, stage) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.style.display = "";
  el.querySelector(".progress-fill").style.width = pct + "%";
  el.querySelector(".progress-pct").textContent = pct + "%";
  const stageEl = el.querySelector(".progress-stage");
  if (stageEl) stageEl.textContent = (stage ? t("validate_stage_" + stage) : "") || "";
}

function hideProgress(containerId) {
  const el = document.getElementById(containerId);
  if (el) el.style.display = "none";
}

// ── Opplasting og jobb-polling ────────────────────────────────────────────────

/** Feilmelding hvis filen er for stor, ellers null. */
function fileSizeError(file) {
  if (file.size <= MAX_UPLOAD_MB * 1024 * 1024) return null;
  return t("file_too_large")
    .replace("{size}", (file.size / 1024 / 1024).toFixed(1))
    .replace("{max}", MAX_UPLOAD_MB);
}

function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const startTime = Date.now();
    let hasRealProgress = false;

    const timer = setInterval(() => {
      if (!hasRealProgress) {
        const elapsed = (Date.now() - startTime) / 1000;
        onProgress(0.48 * (1 - Math.exp(-elapsed / 60)));
      }
    }, 500);

    xhr.upload.onprogress = e => {
      if (e.lengthComputable && e.total > 0) {
        hasRealProgress = true;
        onProgress(e.loaded / e.total);
      }
    };
    xhr.onload = () => {
      clearInterval(timer);
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ikke-JSON svar */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(errorDetail(data)));
    };
    xhr.onerror = () => { clearInterval(timer); reject(new Error("Network error")); };
    xhr.open("POST", url);
    xhr.send(formData);
  });
}

/**
 * Last opp skjema og vent på svar fra et synkront endepunkt, med fremdrift:
 * opplasting 0–50 %, deretter «stage» (f.eks. "processing") som kryper mot 95 %.
 * Returnerer {ok, data} – data er JSON-svaret, eller {error} ved feil.
 */
function uploadAndWait(url, formData, progressId, stage = "processing") {
  showProgress(progressId, 0, "uploading");
  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    let pct = 0, waiting = false;
    const timer = setInterval(() => {
      if (waiting) pct = Math.min(95, pct + (95 - pct) * 0.04);
      else pct = Math.min(48, pct + 0.5);  // uten reell opplastingsfremdrift
      showProgress(progressId, Math.round(pct), waiting ? stage : "uploading");
    }, 400);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable && e.total > 0) pct = Math.max(pct, e.loaded / e.total * 50);
    };
    xhr.upload.onload = () => { waiting = true; pct = Math.max(pct, 50); };
    xhr.onload = () => {
      clearInterval(timer);
      hideProgress(progressId);
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ikke-JSON svar */ }
      const ok = xhr.status >= 200 && xhr.status < 300;
      resolve({ ok, data: ok ? data : { error: errorDetail(data) } });
    };
    xhr.onerror = () => {
      clearInterval(timer);
      hideProgress(progressId);
      resolve({ ok: false, data: { error: t("err_network") } });
    };
    xhr.open("POST", url);
    xhr.send(formData);
  });
}

/** Feilmelding fra FastAPI-svar ({detail: "…"} eller valideringsliste). */
function errorDetail(data) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).join(".")} – ${e.msg}`).join("; ");
  return d || (data && data.error) || t("unknown_error");
}

async function pollJob(jobId, progressEndpoint, containerId, base = 0) {
  while (true) {
    await new Promise(r => setTimeout(r, 600));
    try {
      const r = await fetch(`${progressEndpoint}/${jobId}`);
      const data = await r.json();
      if (data.status === "done") return data.result;
      if (data.status === "error") return { ok: false, error: data.error };
      const pct = base + Math.round((data.percent || 0) / 100 * (100 - base));
      showProgress(containerId, pct, data.stage || "validating");
    } catch {
      return { ok: false, error: t("unknown_error") };
    }
  }
}

/**
 * Last opp og valider en OSDM-fil (/ui/validate-osdm) med fremdrift.
 * Returnerer valideringsresultatet ({ok, deliveryId, warnings, …}) eller {ok:false, error}.
 */
async function validateOsdmUpload(file, progressId) {
  const sizeError = fileSizeError(file);
  if (sizeError) return { ok: false, error: sizeError };

  showProgress(progressId, 0, "uploading");
  const fd = new FormData();
  fd.append("osdmFile", file);
  try {
    const { jobId } = await uploadWithProgress("/ui/validate-osdm", fd, frac =>
      showProgress(progressId, Math.round(frac * 50), "uploading")
    );
    return await pollJob(jobId, "/ui/validate-osdm/progress", progressId, 50);
  } catch {
    return { ok: false, error: t("unknown_error") };
  } finally {
    hideProgress(progressId);
  }
}

// ── Meldinger ─────────────────────────────────────────────────────────────────

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Linje med filnavn og størrelse under filvelgeren (#fileInfo). Returnerer false hvis filen er for stor. */
function showFileInfo(file, id = "fileInfo") {
  const el = document.getElementById(id);
  if (!el) return true;
  if (!file) { el.style.display = "none"; return true; }
  const sizeError = fileSizeError(file);
  el.className = sizeError ? "validation-error" : "file-info";
  el.innerText = sizeError || `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  el.style.display = "block";
  return !sizeError;
}

/** Grønn infoboks etter vellykket validering av OSDM-fil. */
function renderOsdmSummary(res, { stations = false } = {}) {
  const parts = [
    `${t("label_delivery_id")}: <b>${escapeHtml(res.deliveryId)}</b>`,
    `${t("label_provider")}: <b>${escapeHtml(res.fareProvider || "—")}</b>`,
    `${t("label_fare_count_short")}: <b>${res.fareCount.toLocaleString()}</b>`,
    `${t("label_price_count_short")}: <b>${res.priceCount.toLocaleString()}</b>`,
  ];
  if (stations) parts.push(`${t("label_station_count")}: <b>${res.stationCount}</b>`);
  return `<div class="info-box"><div>
      <strong>${t("osdm_validated")}</strong>
      <div class="info-meta">${parts.join(" &nbsp;·&nbsp; ")}</div>
    </div></div>`;
}

/** Feilboks (samme stil overalt). */
function renderError(msg) {
  return `<pre class="status-error">${escapeHtml(msg || t("unknown_error"))}</pre>`;
}

/** Valideringsadvarsler, eller grønn hake hvis ingen. */
function renderWarnings(warnings, title, footer) {
  if (!warnings || !warnings.length) return `<div class="check-ok">${t("osdm_no_warnings")}</div>`;
  const items = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("");
  return `<div class="warnings-box">
    <div class="warnings-title">⚠ ${title} (${warnings.length})</div>
    <ul class="warnings-list">${items}</ul>
    <div class="warnings-note">${footer || t("osdm_warnings_note")}</div>
  </div>`;
}

/** Inndatafeil under skjemaet (#validationError). Tom melding skjuler den. */
function setValidationError(msg, id = "validationError") {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerText = msg || "";
  el.style.display = msg ? "block" : "none";
}

/** Resultatboks (#resultBox/#finalStatus): grønn ved suksess, rød ved feil. */
function showResult(text, ok) {
  const box = document.getElementById("resultBox");
  box.className = ok ? "status-ok" : "status-error";
  document.getElementById("finalStatus").innerText = text;
  box.style.display = "flex";
}

function hideResult() {
  document.getElementById("resultBox").style.display = "none";
}

/** Felles oppsummeringslinjer for en ny OSDM-fil (generert eller regulert). */
function deliverySummaryLines(res) {
  const yesNo = v => (v ? t("yes") : t("no_text"));
  return [
    `${t("label_file")}: ${res.outputFile}`,
    `${t("label_env")}: ${res.summary.environment}`,
    `${t("label_optional_del")}: ${yesNo(document.getElementById("optionalDelivery").value === "true")}`,
    `${t("label_valid_period")}: ${document.getElementById("validFrom").value} → ${document.getElementById("validTo").value}`,
    `${t("label_summer_time")}: ${yesNo(res.summary.utcOffset === 120)}`,
  ];
}

// ── Excel-eksport av lagret resultat ──────────────────────────────────────────

/** Start Excel-jobb via `startUrl`, vent til den er ferdig og last ned. */
async function exportExcel(startUrl, btnId = "downloadExcelBtn", spinnerId = "spinnerExcel") {
  const btn = document.getElementById(btnId);
  const spinner = document.getElementById(spinnerId);
  btn.disabled = true;
  spinner.style.display = "block";
  try {
    const res = await (await fetch(startUrl, { method: "POST" })).json();
    if (!res.jobId) throw new Error();
    while (true) {
      await new Promise(r => setTimeout(r, 800));
      const s = await (await fetch(`/frontend/osdm-to-csv-status/${res.jobId}`)).json();
      if (s.status === "done") { window.location.href = `/frontend/osdm-to-csv-download/${res.jobId}`; break; }
      if (s.status === "error") throw new Error(s.error);
    }
  } catch {
    showResult(t("err_excel_failed"), false);
  } finally {
    spinner.style.display = "none";
    btn.disabled = false;
  }
}
