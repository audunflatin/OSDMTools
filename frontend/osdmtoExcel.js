// osdmtoExcel.js – OSDM → Excel. Bruker hjelperne i common.js.

const fileInput = document.getElementById("osdmFile");
const convertBtn = document.getElementById("convertBtn");
const downloadBtn = document.getElementById("downloadBtn");
let currentJobId = null;

function reset() {
  currentJobId = null;
  hideResult();
  downloadBtn.disabled = true;
}

fileInput.addEventListener("change", () => {
  reset();
  const file = fileInput.files[0];
  convertBtn.disabled = !file || !showFileInfo(file);
});

async function convert() {
  reset();
  convertBtn.disabled = true;
  const fd = new FormData();
  fd.append("osdmFile", fileInput.files[0]);

  try {
    showProgress("convertProgress", 0, "uploading");
    const { jobId } = await uploadWithProgress("/frontend/osdm-to-csv", fd, frac =>
      showProgress("convertProgress", Math.round(frac * 50), "uploading")
    );
    while (true) {
      await new Promise(r => setTimeout(r, 500));
      const res = await (await fetch(`/frontend/osdm-to-csv-status/${jobId}`)).json();
      if (res.status === "done") {
        currentJobId = jobId;
        showResult([t("convert_success"), `${t("label_file")}: ${res.filename}`, `${t("label_rows")}: ${res.rows}`].join("\n"), true);
        downloadBtn.disabled = false;
        break;
      }
      if (res.status === "error") throw new Error(translateError(res.error));
      showProgress("convertProgress", 50 + Math.round((res.percent || 0) / 2), "converting");
    }
  } catch (err) {
    showResult(`${t("err_convert_failed")}: ${err.message || t("unknown_error")}`, false);
  } finally {
    hideProgress("convertProgress");
    convertBtn.disabled = !fileInput.files[0];
  }
}

function download() {
  if (currentJobId) window.location.href = `/frontend/osdm-to-csv-download/${currentJobId}`;
}
