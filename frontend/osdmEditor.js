/* OSDM Editor – frontend logic */

let _summary = null;

// ---------------------------------------------------------------------------
// File handling
// ---------------------------------------------------------------------------

function onFileChange() {
  const file = document.getElementById("editorFile").files[0];
  document.getElementById("loadMsg").innerHTML = "";
  document.getElementById("loadBtn").disabled = !file || !showFileInfo(file);
}

async function loadFile() {
  const file = document.getElementById("editorFile").files[0];
  if (!file) return;

  const btn = document.getElementById("loadBtn");
  btn.disabled = true;
  document.getElementById("loadMsg").innerHTML = "";
  document.getElementById("editorBody").style.display = "none";

  const fd = new FormData();
  fd.append("osdmFile", file);
  const { ok, data } = await uploadAndWait("/osdm-editor/load", fd, "loadProgress", "processing");
  btn.disabled = false;

  if (!ok) {
    document.getElementById("loadMsg").innerHTML = renderError(data.error);
    return;
  }
  _summary = data;
  renderSummary(_summary);
  document.getElementById("editorBody").style.display = "block";
  showTab("overview");
}

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

function showTab(name) {
  ["overview", "passengers", "relations", "download"].forEach(n => {
    document.getElementById("pane" + cap(n)).classList.toggle("active", n === name);
    document.getElementById("tab" + cap(n)).classList.toggle("active", n === name);
  });
}

function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ---------------------------------------------------------------------------
// Render summary
// ---------------------------------------------------------------------------

function renderSummary(s) {
  renderStats(s);

  // Metadata fields
  document.getElementById("metaDeliveryId").value = s.delivery.deliveryId || "";
  // Pre-fill previousDeliveryId with the loaded file's deliveryId if not already set
  document.getElementById("metaPrevDeliveryId").value =
    s.delivery.previousDeliveryId || s.delivery.deliveryId || "";
  document.getElementById("metaValidFrom").value = (s.validity && s.validity.from_date) ? s.validity.from_date : "";
  document.getElementById("metaValidUntil").value = (s.validity && s.validity.until_date) ? s.validity.until_date : "";
  document.getElementById("metaUsage").value = s.delivery.usage || "PRODUCTION";
  document.getElementById("metaOptional").checked = !!s.delivery.optionalDelivery;

  // Passenger profiles
  renderPassengers(s);

  // Relations table
  renderRcTable(s);
}

function renderStats(s) {
  const chip = (n, key) => `<div class="stat-chip"><strong>${n.toLocaleString()}</strong> ${t(key)}</div>`;
  document.getElementById("overviewStats").innerHTML =
    chip(s.stats.rc_count, "label_fare_count_short") + chip(s.stats.fare_count, "editor_stat_fares") +
    chip(s.stats.price_count, "label_price_count_short") + chip(s.passenger_types.length, "editor_stat_passenger_types");
}

function renderPassengers(s) {
  const el = document.getElementById("pcList");
  if (!s.passenger_types.length) { el.innerHTML = "<p>–</p>"; return; }
  el.innerHTML = s.passenger_types.map(pc => `
    <div class="pc-row" id="pcrow-${pc.id}">
      <div>
        <div class="pc-name">${escapeHtml(pc.name || pc.id)}</div>
        ${pc.is_adult ? `<div class="pc-adult">${t("editor_adult_label")}</div>` : ""}
        ${!pc.is_adult ? `<div style="font-size:0.75rem;color:var(--text-muted);">${t("editor_ratio_label")}</div>` : ""}
      </div>
      <div style="text-align:right;">
        ${pc.is_adult
          ? `<input type="number" value="1.00" disabled style="width:80px; opacity:0.5;">`
          : `<input class="pc-ratio-input" id="ratio-${pc.id}" type="number" min="0" max="2" step="0.01" value="${pc.ratio.toFixed(4)}">`
        }
      </div>
      <div>
        ${pc.is_adult
          ? `<button disabled style="opacity:0.4;" data-i18n="btn_save_ratio">${t("btn_save_ratio")}</button>`
          : `<button onclick="saveRatio('${pc.id}')" data-i18n="btn_save_ratio">${t("btn_save_ratio")}</button>`
        }
      </div>
    </div>
    ${pc.is_adult ? "" : `<div id="ratioMsg-${pc.id}" style="font-size:0.8rem; padding-left:0; margin-bottom:0.25rem;"></div>`}
  `).join("");
}

function renderRcTable(s) {
  const wrap = document.getElementById("rcTableWrap");
  if (!s.rc_list.length) { wrap.innerHTML = ""; return; }
  const truncNote = s.stats.rc_list_truncated
    ? `<p class="truncated-note">${t("editor_truncated").replace("{shown}", s.rc_list.length).replace("{total}", s.stats.rc_count)}</p>` : "";
  wrap.innerHTML = `
    ${truncNote}
    <table class="rc-table">
      <thead><tr>
        <th>${t("editor_col_from_uic")}</th><th>${t("editor_col_from_name")}</th><th>${t("editor_col_to_uic")}</th><th>${t("editor_col_to_name")}</th><th>${t("editor_col_distance")}</th>
      </tr></thead>
      <tbody>${s.rc_list.map(rc => `
        <tr>
          <td>${escapeHtml(rc.from_uic)}</td>
          <td>${escapeHtml(rc.from_name || "–")}</td>
          <td>${escapeHtml(rc.to_uic)}</td>
          <td>${escapeHtml(rc.to_name || "–")}</td>
          <td>${rc.distance} km</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Metadata save
// ---------------------------------------------------------------------------

async function saveMetadata() {
  const body = {
    deliveryId:         document.getElementById("metaDeliveryId").value.trim(),
    previousDeliveryId: document.getElementById("metaPrevDeliveryId").value.trim(),
    valid_from:         document.getElementById("metaValidFrom").value,
    valid_until:        document.getElementById("metaValidUntil").value,
    usage:              document.getElementById("metaUsage").value,
    optionalDelivery:   document.getElementById("metaOptional").checked,
  };
  try {
    const resp = await fetch("/osdm-editor/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(errorDetail(await resp.json().catch(() => ({}))));
    showMsg("metaMsg", t("editor_metadata_saved"), "ok");
    if (_summary) _summary.delivery = { ..._summary.delivery, ...body };
  } catch (e) {
    showMsg("metaMsg", e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Passenger ratio save
// ---------------------------------------------------------------------------

async function saveRatio(pcId) {
  const input = document.getElementById(`ratio-${pcId}`);
  const ratio = parseFloat(input.value);
  if (isNaN(ratio) || ratio < 0 || ratio > 2) {
    showMsg(`ratioMsg-${pcId}`, t("editor_ratio_label") + " (0–2)", "err");
    return;
  }
  try {
    const resp = await fetch(`/osdm-editor/passenger/${encodeURIComponent(pcId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ratio }),
    });
    if (!resp.ok) throw new Error(errorDetail(await resp.json().catch(() => ({}))));
    const data = await resp.json();
    showMsg(`ratioMsg-${pcId}`, `${data.updated_fares} ${t("editor_updated_fares")}`, "ok");
    if (_summary) {
      const pc = _summary.passenger_types.find(p => p.id === pcId);
      if (pc) pc.ratio = ratio;
    }
  } catch (e) {
    showMsg(`ratioMsg-${pcId}`, e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Add relation
// ---------------------------------------------------------------------------

async function addRelation() {
  const fromUic      = document.getElementById("relFromUic").value.trim();
  const toUic        = document.getElementById("relToUic").value.trim();
  const adultPrice   = parseFloat(document.getElementById("relAdultPrice").value);
  const distance     = parseInt(document.getElementById("relDistance").value, 10);

  if (!fromUic || !toUic) { showMsg("relMsg", t("editor_error_uic"), "err"); return; }
  if (isNaN(adultPrice) || adultPrice <= 0) { showMsg("relMsg", t("editor_adult_price") + " > 0", "err"); return; }
  if (isNaN(distance) || distance < 0) { showMsg("relMsg", t("editor_distance") + " >= 0", "err"); return; }

  try {
    const resp = await fetch("/osdm-editor/relation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_uic: fromUic, to_uic: toUic, adult_price_eur: adultPrice, distance }),
    });
    if (!resp.ok) throw new Error(errorDetail(await resp.json().catch(() => ({}))));
    const data = await resp.json();
    showMsg("relMsg", `${data.fares_created} ${t("editor_fares_created")}`, "ok");

    // Refresh summary from server to get updated rc_list
    const sumResp = await fetch("/osdm-editor/summary");
    if (sumResp.ok) {
      _summary = await sumResp.json();
      renderRcTable(_summary);
      renderStats(_summary);
    }

    // Clear inputs
    document.getElementById("relFromUic").value = "";
    document.getElementById("relToUic").value = "";
    document.getElementById("relAdultPrice").value = "";
    document.getElementById("relDistance").value = "";
  } catch (e) {
    showMsg("relMsg", e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function showMsg(id, text, type) {
  const el = document.getElementById(id);
  if (!el) return;
  // Samme meldingsstil som resten av appen
  el.className = type === "ok" ? "check-ok" : "validation-error";
  el.textContent = text;
}
