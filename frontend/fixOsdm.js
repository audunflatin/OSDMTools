// fixOsdm.js — Rydd opp i OSDM (analyser → se hva som fjernes → last ned). Bruker common.js.

const fixInput = document.getElementById("fixOsdmFile");
const fixBtn = document.getElementById("fixBtn");
const downloadBtn = document.getElementById("downloadBtn");

const STAT_LABELS = () => ({
  removed_bad_rcs:       t("fix_stat_bad_rcs"),
  removed_bad_fares:     t("fix_stat_bad_fares"),
  removed_unused_prices: t("fix_stat_unused_prices"),
  removed_unused_pcs:    t("fix_stat_unused_pcs"),
  removed_unused_rcs:    t("fix_stat_unused_rcs"),
});

fixInput.addEventListener("change", () => {
  hideResult();
  downloadBtn.disabled = true;
  const file = fixInput.files[0];
  fixBtn.disabled = !file || !showFileInfo(file);
});

async function doAnalyze() {
  hideResult();
  downloadBtn.disabled = true;
  fixBtn.disabled = true;

  const fd = new FormData();
  fd.append("osdmFile", fixInput.files[0]);
  const { ok, data } = await uploadAndWait("/ui/fix-osdm", fd, "fixProgress", "processing");
  fixBtn.disabled = false;

  if (!ok) {
    showResult(data.error, false);
    return;
  }
  const stats = data.stats || {};
  const changes = Object.entries(stats).filter(([, n]) => n > 0);
  if (!changes.length) {
    showResult(t("fix_osdm_nothing"), true);
    return;
  }
  const labels = STAT_LABELS();
  showResult([t("fix_preview_heading"), ...changes.map(([key, n]) => `• ${n} ${labels[key] || key}`)].join("\n"), true);
  downloadBtn.disabled = false;
}

function doDownload() {
  window.location.href = "/ui/fix-osdm/download";
}
