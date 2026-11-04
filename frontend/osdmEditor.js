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
  ["overview", "passengers", "relations", "border", "texts", "stations", "download"].forEach(n => {
    document.getElementById("pane" + cap(n)).classList.toggle("active", n === name);
    document.getElementById("tab" + cap(n)).classList.toggle("active", n === name);
  });
  if (name === "border") loadBorderPoints();
  if (name === "texts") loadTexts();
  if (name === "stations") loadStations();
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

function renderPassengers() {
  // Detaljene hentes fra eget endepunkt (aldersgrenser, kombinasjoner, kontroll)
  loadPassengers();
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
// Passenger categories: ratio, age limits, weight, combination rules
// ---------------------------------------------------------------------------

let _pcData = null;        // svar fra /osdm-editor/passengers
let _editingPcId = null;

async function loadPassengers() {
  try {
    _pcData = await getJson("/osdm-editor/passengers");
    document.getElementById("pcWarnings").innerHTML =
      renderWarnings(_pcData.warnings, t("editor_pc_warnings_title"), t("editor_pc_warnings_note"));
    renderPcTable();
  } catch (e) {
    document.getElementById("pcTableWrap").innerHTML = renderError(e.message);
  }
}

function pcShortId(id) {
  const m = /[A-Z]__\d+$/.exec(id || "");
  return m ? m[0] : id;
}

function pcAgeText(pc) {
  return `${pc.lowerAgeLimit ?? "–"}–${pc.upperAgeLimit ?? "–"}`;
}

function renderPcTable() {
  const wrap = document.getElementById("pcTableWrap");
  const list = _pcData ? _pcData.passengers : [];
  if (!list.length) { wrap.innerHTML = "<p>–</p>"; return; }
  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th>${t("editor_pc_col_name")}</th><th>${t("editor_pc_col_type")}</th><th>${t("editor_pc_col_age")}</th>
        <th class="right">${t("editor_pc_col_weight")}</th><th class="right">${t("editor_pc_col_fares")}</th>
        <th>${t("editor_pc_col_ratio")}</th><th></th>
      </tr></thead>
      <tbody>${list.map((pc, i) => `
        <tr class="${pc.id === _editingPcId ? "row-selected" : ""}">
          <td>${escapeHtml(pc.name)}<br><span class="subtle mono">${escapeHtml(pcShortId(pc.id))} · ${pc.bundles.map(b => escapeHtml(pcShortId(b))).join(", ")}</span>
            ${pc.is_adult ? `<br><span class="pc-adult">${t("editor_adult_label")}</span>` : ""}</td>
          <td class="mono">${escapeHtml(pc.passengerType)}</td>
          <td class="nowrap">${escapeHtml(pcAgeText(pc))}</td>
          <td class="right">${pc.passengerWeight ?? "–"}</td>
          <td class="right">${pc.fare_count.toLocaleString()}</td>
          <td class="nowrap">${pc.is_adult
            ? `<input class="pc-ratio-input" type="number" value="1.00" disabled>`
            : `<input class="pc-ratio-input" data-ratio-input="${i}" type="number" min="0" max="2" step="0.01" value="${pc.ratio.toFixed(4)}">
               <button data-pc-ratio="${i}">${t("btn_save_ratio")}</button>`}</td>
          <td class="right"><button data-pc-edit="${i}">${t("btn_edit")}</button></td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

async function saveRatio(i) {
  const pc = _pcData.passengers[i];
  const ratio = parseFloat(document.querySelector(`[data-ratio-input="${i}"]`).value);
  if (isNaN(ratio) || ratio < 0 || ratio > 2) {
    showMsg("pcMsg", t("editor_ratio_label") + " (0–2)", "err");
    return;
  }
  try {
    const data = await postJson(`/osdm-editor/passenger/${encodeURIComponent(pc.id)}`, { ratio });
    showMsg("pcMsg", `${pc.name}: ${data.updated_fares} ${t("editor_updated_fares")}`, "ok");
    pc.ratio = ratio;
  } catch (e) {
    showMsg("pcMsg", e.message, "err");
  }
}

function editPassenger(i) {
  const pc = _pcData.passengers[i];
  if (!pc) return;
  _editingPcId = pc.id;
  document.getElementById("pcFormHeading").textContent =
    t("editor_pc_edit_heading").replace("{name}", `${pc.name} (${pcShortId(pc.id)})`);

  const nameSel = document.getElementById("pcNameRef");
  nameSel.innerHTML = _pcData.texts.map(tx =>
    `<option value="${escapeHtml(tx.id)}">${escapeHtml(tx.text)} (${escapeHtml(pcShortId(tx.id))})</option>`).join("");
  nameSel.value = pc.nameRef;

  const types = _pcData.types.includes(pc.passengerType) || !pc.passengerType
    ? _pcData.types : [pc.passengerType, ..._pcData.types];
  const typeSel = document.getElementById("pcType");
  typeSel.innerHTML = types.map(ty => `<option value="${escapeHtml(ty)}">${escapeHtml(ty)}</option>`).join("");
  typeSel.value = pc.passengerType;

  document.getElementById("pcLower").value = pc.lowerAgeLimit ?? 0;
  document.getElementById("pcUpper").value = pc.upperAgeLimit ?? 0;
  document.getElementById("pcTravelAlone").value = pc.ageLimitToTravelAlone ?? 0;
  document.getElementById("pcReservation").value = pc.ageLimitForReservation ?? 0;
  document.getElementById("pcWeight").value = pc.passengerWeight ?? 0;
  document.getElementById("pcAncillary").checked = !!pc.isAncillaryItem;
  document.getElementById("pcBundles").textContent =
    t("editor_pc_bundles").replace("{list}", pc.bundles.map(pcShortId).join(", ") || "–");

  const sib = document.getElementById("pcSiblingsWrap");
  if (pc.siblings.length) {
    sib.style.display = "";
    document.getElementById("pcSiblings").checked = true;
    document.getElementById("pcSiblingsLabel").textContent =
      t("editor_pc_siblings").replace("{list}", pc.siblings.map(pcShortId).join(", "));
  } else {
    sib.style.display = "none";
    document.getElementById("pcSiblings").checked = false;
  }

  document.getElementById("pcCombos").innerHTML = "";
  pc.combinationConstraint.forEach(c => addComboRow(c));
  document.getElementById("pcForm").style.display = "";
  document.getElementById("pcMsg").innerHTML = "";
  renderPcTable();
  document.getElementById("pcFormHeading").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closePassengerForm() {
  _editingPcId = null;
  document.getElementById("pcForm").style.display = "none";
  renderPcTable();
}

function addComboRow(c = {}) {
  const row = document.createElement("div");
  row.className = "translation-row";
  const options = _pcData.passengers.map(p =>
    `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)} (${escapeHtml(pcShortId(p.id))})</option>`).join("");
  row.innerHTML = `
    <select class="cb-ref">${options}</select>
    <input type="number" class="cb-min" min="0" max="999" step="1" placeholder="${escapeHtml(t("editor_pc_comb_min"))}" value="${c.minNumber ?? 0}" />
    <input type="number" class="cb-max" min="0" max="999" step="1" placeholder="${escapeHtml(t("editor_pc_comb_max"))}" value="${c.maxNumber ?? 1}" />
    <button data-remove-row>${t("btn_remove")}</button>`;
  if (c.passengerConstraintRef) row.querySelector(".cb-ref").value = c.passengerConstraintRef;
  document.getElementById("pcCombos").appendChild(row);
}

async function savePassenger() {
  const pc = _pcData.passengers.find(p => p.id === _editingPcId);
  if (!pc) return;
  const num = id => document.getElementById(id).value.trim();
  const body = {
    nameRef: document.getElementById("pcNameRef").value,
    passengerType: document.getElementById("pcType").value,
    lowerAgeLimit: num("pcLower"),
    upperAgeLimit: num("pcUpper"),
    ageLimitToTravelAlone: num("pcTravelAlone"),
    ageLimitForReservation: num("pcReservation"),
    passengerWeight: num("pcWeight"),
    isAncillaryItem: document.getElementById("pcAncillary").checked,
    combinationConstraint: [...document.querySelectorAll("#pcCombos .translation-row")].map(r => ({
      passengerConstraintRef: r.querySelector(".cb-ref").value,
      minNumber: r.querySelector(".cb-min").value.trim(),
      maxNumber: r.querySelector(".cb-max").value.trim(),
    })),
    also_ids: document.getElementById("pcSiblings").checked ? pc.siblings : [],
  };
  try {
    const data = await postJson(`/osdm-editor/passenger/${encodeURIComponent(pc.id)}/details`, body);
    closePassengerForm();
    await loadPassengers();
    showMsg("pcMsg", t("editor_pc_saved").replace("{n}", data.updated.length), "ok");
  } catch (e) {
    showMsg("pcMsg", e.message, "err");
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
// Border points
// ---------------------------------------------------------------------------

let _borderPoints = [];
let _editingBorderId = null;   // null = nytt grensepunkt

async function loadBorderPoints() {
  const wrap = document.getElementById("bpTableWrap");
  try {
    const resp = await fetch("/osdm-editor/border-points");
    if (!resp.ok) throw new Error(errorDetail(await resp.json().catch(() => ({}))));
    _borderPoints = (await resp.json()).border_points;
    renderBorderTable();
  } catch (e) {
    wrap.innerHTML = renderError(e.message);
  }
}

function renderStationList(side) {
  return (side || []).map(st =>
    `<span class="bp-station">${escapeHtml(st.name || "–")} <span class="mono">${escapeHtml(st.uic)} ${escapeHtml(st.country)}</span></span>`
  ).join("");
}

function renderBorderTable() {
  const wrap = document.getElementById("bpTableWrap");
  if (!_borderPoints.length) {
    wrap.innerHTML = `<p class="field-hint">${t("editor_border_none")}</p>`;
    return;
  }
  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th>${t("editor_border_col_code")}</th><th>${t("editor_border_col_side1")}</th>
        <th>${t("editor_border_col_side2")}</th><th class="right">${t("editor_border_col_rcs")}</th><th></th>
      </tr></thead>
      <tbody>${_borderPoints.map((bp, i) => `
        <tr class="${bp.id === _editingBorderId ? "row-selected" : ""}">
          <td class="mono">${escapeHtml(bp.code)}<br><span class="subtle">${escapeHtml(bp.id)}</span></td>
          <td>${renderStationList(bp.sides[0])}</td>
          <td>${bp.sides.length > 1 ? renderStationList(bp.sides[1]) : `<span class="muted">–</span>`}
            ${bp.warnings.length ? `<ul class="bp-warnings">${bp.warnings.map(w => `<li>${escapeHtml(translateError(w))}</li>`).join("")}</ul>` : ""}
          </td>
          <td class="right">${bp.rc_count}</td>
          <td class="right"><button data-bp-index="${i}">${t("btn_edit")}</button></td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

// Én delegert lytter – ingen inline onclick med data fra fila
document.addEventListener("click", e => {
  const btn = e.target.closest("[data-bp-index]");
  if (btn) editBorderPoint(parseInt(btn.dataset.bpIndex, 10));
});

function editBorderPoint(i) {
  const bp = _borderPoints[i];
  if (!bp) return;
  _editingBorderId = bp.id;
  document.getElementById("bpCode").value = bp.code;
  document.getElementById("bpSide1").value = (bp.sides[0] || []).map(s => s.uic).join(", ");
  document.getElementById("bpSide2").value = (bp.sides[1] || []).map(s => s.uic).join(", ");
  document.getElementById("bpFormHeading").textContent = t("editor_border_edit_heading").replace("{code}", bp.code);
  document.getElementById("bpSaveBtn").textContent = t("btn_save_border");
  document.getElementById("bpCancelBtn").style.display = "";
  document.getElementById("bpMsg").innerHTML = "";
  renderBorderNameInputs();
  renderBorderTable();
  document.getElementById("bpFormHeading").scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetBorderForm() {
  _editingBorderId = null;
  ["bpCode", "bpSide1", "bpSide2"].forEach(id => { document.getElementById(id).value = ""; });
  document.getElementById("bpFormHeading").textContent = t("editor_border_add_heading");
  document.getElementById("bpSaveBtn").textContent = t("btn_add_border");
  document.getElementById("bpCancelBtn").style.display = "none";
  renderBorderNameInputs();
  renderBorderTable();
}

function parseUics(id) {
  return document.getElementById(id).value.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean);
}

function knownStationName(uic) {
  for (const bp of _borderPoints)
    for (const side of bp.sides)
      for (const st of side) if (st.uic === uic && st.name) return st.name;
  for (const rc of (_summary ? _summary.rc_list : [])) {
    if (rc.from_uic === uic && rc.from_name !== uic) return rc.from_name;
    if (rc.to_uic === uic && rc.to_name !== uic) return rc.to_name;
  }
  return "";
}

function renderBorderNameInputs() {
  const el = document.getElementById("bpNames");
  // Behold det brukeren allerede har skrevet
  const typed = {};
  el.querySelectorAll("input[data-uic]").forEach(inp => { typed[inp.dataset.uic] = inp.value; });
  const uics = [...parseUics("bpSide1"), ...parseUics("bpSide2")].filter(u => /^\d{7}$/.test(u));
  document.getElementById("bpNamesWrap").style.display = uics.length ? "" : "none";
  el.innerHTML = uics.map(u => `
    <div class="field-group">
      <label class="mono">${escapeHtml(u)}</label>
      <input type="text" data-uic="${escapeHtml(u)}" placeholder="${escapeHtml(t("editor_border_name_ph"))}"
        value="${escapeHtml(u in typed ? typed[u] : knownStationName(u))}" />
    </div>`).join("");
}

async function saveBorderPoint() {
  const names = {};
  document.querySelectorAll("#bpNames input[data-uic]").forEach(inp => {
    const known = knownStationName(inp.dataset.uic);
    const v = inp.value.trim();
    if (v && v !== known) names[inp.dataset.uic] = v;
  });
  const body = {
    code: document.getElementById("bpCode").value.trim(),
    sides: [parseUics("bpSide1"), parseUics("bpSide2")].filter(s => s.length),
    names,
  };
  const url = _editingBorderId
    ? `/osdm-editor/border-point/${encodeURIComponent(_editingBorderId)}`
    : "/osdm-editor/border-point";
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(errorDetail(await resp.json().catch(() => ({}))));
    const data = await resp.json();
    const msg = _editingBorderId ? t("editor_border_saved")
      : t(data.converted ? "editor_border_converted" : "editor_border_added").replace("{id}", data.id);
    resetBorderForm();
    await loadBorderPoints();
    showMsg("bpMsg", msg, "ok");
    // Stasjonsnavn kan ha endret seg – oppdater relasjonstabellen
    const sumResp = await fetch("/osdm-editor/summary");
    if (sumResp.ok) { _summary = await sumResp.json(); renderRcTable(_summary); }
  } catch (e) {
    showMsg("bpMsg", e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Texts
// ---------------------------------------------------------------------------

let _texts = [];
let _editingTextId = null;

async function getJson(url) {
  const resp = await fetch(url);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(errorDetail(data));
  return data;
}

async function postJson(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(errorDetail(data));
  return data;
}

async function refreshSummary() {
  try {
    _summary = await getJson("/osdm-editor/summary");
    loadPassengers();
    renderRcTable(_summary);
  } catch (e) { /* oversikten oppdateres ved neste innlasting */ }
}

async function loadTexts() {
  try {
    _texts = (await getJson("/osdm-editor/texts")).texts;
    renderTextTable();
  } catch (e) {
    document.getElementById("txtTableWrap").innerHTML = renderError(e.message);
  }
}

function renderTextTable() {
  const wrap = document.getElementById("txtTableWrap");
  if (!_texts.length) { wrap.innerHTML = `<p class="field-hint">${t("editor_texts_none")}</p>`; return; }
  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th>${t("editor_texts_col_id")}</th><th>${t("editor_texts_col_text")}</th>
        <th>${t("editor_texts_col_short")}</th><th>${t("editor_texts_col_used")}</th><th></th>
      </tr></thead>
      <tbody>${_texts.map((tx, i) => `
        <tr class="${tx.id === _editingTextId ? "row-selected" : ""}">
          <td class="mono subtle">${escapeHtml(tx.id)}</td>
          <td>${escapeHtml(tx.text)}${tx.translations.length ? `<br><span class="subtle">${tx.translations.map(tr => escapeHtml(tr.language)).join(", ")}</span>` : ""}</td>
          <td>${escapeHtml(tx.short_text)}</td>
          <td>${tx.used_by.length
            ? tx.used_by.map(u => `<span class="used-by">${escapeHtml(u.where)} (${u.count.toLocaleString()})</span>`).join("")
            : `<span class="muted">${t("editor_texts_unused")}</span>`}</td>
          <td class="right"><button data-text-index="${i}">${t("btn_edit")}</button></td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

function editText(i) {
  const tx = _texts[i];
  if (!tx) return;
  _editingTextId = tx.id;
  document.getElementById("txtFormHeading").textContent = t("editor_text_edit_heading").replace("{id}", tx.id);
  document.getElementById("txtText").value = tx.text;
  document.getElementById("txtShort").value = tx.short_text === tx.text ? "" : tx.short_text;
  document.getElementById("txtTextAscii").value = tx.text_ascii;
  document.getElementById("txtShortAscii").value = tx.short_text_ascii;
  document.querySelectorAll("#txtForm .txt-ascii").forEach(el => { el.style.display = tx.legacy_ascii ? "" : "none"; });
  document.getElementById("txtTranslations").innerHTML = "";
  tx.translations.forEach(tr => addTranslationRow(tr));
  document.getElementById("txtForm").style.display = "";
  document.getElementById("txtMsg").innerHTML = "";
  renderTextTable();
  document.getElementById("txtFormHeading").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeTextForm() {
  _editingTextId = null;
  document.getElementById("txtForm").style.display = "none";
  renderTextTable();
}

function addTranslationRow(tr = {}) {
  const row = document.createElement("div");
  row.className = "translation-row";
  row.innerHTML = `
    <input type="text" class="tr-lang" maxlength="2" placeholder="${escapeHtml(t("editor_text_lang"))}" value="${escapeHtml(tr.language || "")}" />
    <input type="text" class="tr-text" placeholder="${escapeHtml(t("editor_text_text"))}" value="${escapeHtml(tr.text || "")}" />
    <input type="text" class="tr-short" placeholder="${escapeHtml(t("editor_texts_col_short"))}" value="${escapeHtml(tr.shortText || "")}" />
    <button data-remove-translation>${t("btn_remove")}</button>`;
  document.getElementById("txtTranslations").appendChild(row);
}

async function saveText() {
  if (!_editingTextId) return;
  const body = {
    text: document.getElementById("txtText").value,
    shortText: document.getElementById("txtShort").value,
    textAscii: document.getElementById("txtTextAscii").value,
    shortTextAscii: document.getElementById("txtShortAscii").value,
    translations: [...document.querySelectorAll("#txtTranslations .translation-row")].map(r => ({
      language: r.querySelector(".tr-lang").value,
      text: r.querySelector(".tr-text").value,
      shortText: r.querySelector(".tr-short").value,
    })),
  };
  try {
    await postJson(`/osdm-editor/text/${encodeURIComponent(_editingTextId)}`, body);
    closeTextForm();
    await loadTexts();
    showMsg("txtMsg", t("editor_text_saved"), "ok");
    refreshSummary();  // passasjerkategorienes navn kommer fra tekstene
  } catch (e) {
    showMsg("txtMsg", e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Station names
// ---------------------------------------------------------------------------

let _stations = [];
let _stationSearchTimer = null;

function onStationSearch() {
  clearTimeout(_stationSearchTimer);
  _stationSearchTimer = setTimeout(loadStations, 250);
}

async function loadStations() {
  const q = document.getElementById("stSearch").value.trim();
  const missing = document.getElementById("stMissingOnly").checked;
  try {
    const data = await getJson(`/osdm-editor/stations?q=${encodeURIComponent(q)}&missing=${missing}`);
    _stations = data.stations;
    document.getElementById("stMissingLabel").textContent = t("editor_stations_missing_only").replace("{n}", data.missing_count);
    document.getElementById("stShortHint").textContent = t("editor_station_short_hint").replace("{n}", data.short_max);
    renderStationTable(data);
  } catch (e) {
    document.getElementById("stTableWrap").innerHTML = renderError(e.message);
  }
}

function renderStationTable(data) {
  const wrap = document.getElementById("stTableWrap");
  if (!_stations.length) { wrap.innerHTML = `<p class="field-hint">${t("editor_stations_none")}</p>`; return; }
  const trunc = data.truncated
    ? `<p class="field-hint">${t("editor_stations_truncated").replace("{shown}", _stations.length).replace("{total}", data.total)}</p>` : "";
  wrap.innerHTML = `${trunc}
    <table class="data-table">
      <thead><tr>
        <th>${t("editor_stations_col_uic")}</th><th>${t("editor_stations_col_name")}</th>
        <th>${t("editor_stations_col_short")}</th><th class="right">${t("editor_stations_col_border")}</th><th></th>
      </tr></thead>
      <tbody>${_stations.map((st, i) => `
        <tr>
          <td class="mono">${escapeHtml(st.uic)}</td>
          <td>${st.in_file ? escapeHtml(st.name) : `<span class="muted">${t("editor_stations_no_name")}</span>`}</td>
          <td>${escapeHtml(st.short_name)}</td>
          <td class="right">${st.border_code !== "0" ? escapeHtml(st.border_code) : ""}</td>
          <td class="right"><button data-station-index="${i}">${t("btn_edit")}</button></td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

function editStation(i) {
  const st = _stations[i];
  if (!st) return;
  document.getElementById("stUic").value = st.uic;
  document.getElementById("stName").value = st.name;
  document.getElementById("stShort").value = st.short_name;
  document.getElementById("stFormHeading").textContent = t("editor_station_edit_heading").replace("{uic}", st.uic);
  document.getElementById("stMsg").innerHTML = "";
  document.getElementById("stName").focus();
}

function resetStationForm() {
  ["stUic", "stName", "stShort"].forEach(id => { document.getElementById(id).value = ""; });
  document.getElementById("stFormHeading").textContent = t("editor_station_add_heading");
}

async function saveStation() {
  const uic = document.getElementById("stUic").value.trim();
  try {
    const data = await postJson(`/osdm-editor/station/${encodeURIComponent(uic)}`, {
      name: document.getElementById("stName").value,
      shortName: document.getElementById("stShort").value,
    });
    resetStationForm();
    await loadStations();
    showMsg("stMsg", t(data.created ? "editor_station_added" : "editor_station_saved"), "ok");
    refreshSummary();  // relasjonstabellen viser stasjonsnavn
  } catch (e) {
    showMsg("stMsg", e.message, "err");
  }
}

// Delegerte klikk for knapper i tabellene (ingen inline onclick med data fra fila)
document.addEventListener("click", e => {
  const txt = e.target.closest("[data-text-index]");
  if (txt) return editText(parseInt(txt.dataset.textIndex, 10));
  const st = e.target.closest("[data-station-index]");
  if (st) return editStation(parseInt(st.dataset.stationIndex, 10));
  const rm = e.target.closest("[data-remove-translation], [data-remove-row]");
  if (rm) return rm.closest(".translation-row").remove();
  const pr = e.target.closest("[data-pc-ratio]");
  if (pr) return saveRatio(parseInt(pr.dataset.pcRatio, 10));
  const pe = e.target.closest("[data-pc-edit]");
  if (pe) return editPassenger(parseInt(pe.dataset.pcEdit, 10));
});

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
