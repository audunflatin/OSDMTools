"""OSDM → Excel-eksport."""
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
import io, logging, threading
from datetime import datetime, timezone, timedelta
import ijson
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import backend.osdm_editor as osdm_editor
from backend.rics_codes import RICS_CODES as RICS_CARRIER_NAMES
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import XLSX_JOBS, create_job, owned_job

from backend.core.osdm_bytes import require_fare_delivery, read_delivery
from backend.core.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter()


def osdm_to_xlsx_bytes(file_bytes: bytes, job_id: str = None, jobs: dict = None) -> tuple[bytes, int]:

    def set_progress(percent: int):
        if job_id and jobs and job_id in jobs:
            jobs[job_id]["percent"] = percent

    def _s():
        return io.BytesIO(file_bytes)

    # --- Build lookup maps via ijson (no full parse) ---
    delivery: dict = {}
    for item in ijson.items(_s(), "fareDelivery.delivery"):
        delivery = item
        break
    delivery_id = delivery.get("deliveryId", "")

    text_map: dict[str, str] = {}
    for t in ijson.items(_s(), "fareDelivery.fareStructure.texts.item"):
        text_map[t["id"]] = t.get("textUtf8", t.get("text", ""))

    price_map: dict[str, int] = {}
    for p in ijson.items(_s(), "fareDelivery.fareStructure.prices.item"):
        entry = osdm_editor.eur_entry(p)
        if entry:
            price_map[p["id"]] = round(entry["amount"] / (10 ** entry.get("scale", 2)) * 100)

    station_map: dict[str, str] = {}
    for sn in ijson.items(_s(), "fareDelivery.fareStructure.stationNames.item"):
        code = sn.get("code") or sn.get("uicCode")
        if code:
            station_map[str(code)] = sn.get("nameUtf8", sn.get("name", ""))

    service_class_map: dict[str, str] = {}
    for sc in ijson.items(_s(), "fareDelivery.fareStructure.serviceClassDefinitions.item"):
        service_class_map[sc["id"]] = text_map.get(sc.get("textRef", ""), sc["id"])

    pc_map: dict[str, dict] = {}
    for pc in ijson.items(_s(), "fareDelivery.fareStructure.passengerConstraints.item"):
        pc_map[pc["id"]] = pc

    svc_constraint_map: dict[str, str] = {}
    for sc in ijson.items(_s(), "fareDelivery.fareStructure.serviceConstraints.item"):
        svc_constraint_map[sc["id"]] = text_map.get(sc.get("textRef", ""), sc["id"])

    # Reduction-oppslag: id -> korteste representative kortnavn
    reduction_map: dict[str, str] = {}
    for rc in ijson.items(_s(), "fareDelivery.fareStructure.reductionConstraints.item"):
        cards = rc.get("requiredCards", [])
        if cards:
            name = min(cards, key=lambda c: len(c.get("cardName", "")))
            reduction_map[rc["id"]] = name.get("cardName", rc["id"])

    set_progress(10)

    PASSENGER_TYPE_LABELS = {
        "ADULT": "Adult", "CHILD": "Child", "YOUNG_CHILD": "Young child",
        "SENIOR": "Senior", "DOG": "Dog", "INFANT": "Infant",
    }

    # CP-id -> beste UIC-kode og stasjonsnavn
    cp_to_uic: dict[str, str] = {}
    cp_to_name: dict[str, str] = {}
    for cp in ijson.items(_s(), "fareDelivery.fareStructure.connectionPoints.item"):
        best_code = None
        all_codes = []
        for ss in cp.get("stationSets", []):
            for s in ss:
                if s.get("codeList") == "UIC":
                    code = s["code"]
                    all_codes.append(str(code))
                    if best_code is None or s.get("country") == "NO":
                        best_code = str(code)
        best_name = None
        for code in all_codes:
            name = station_map.get(code)
            if name:
                best_name = name
                break
        cp_to_uic[cp["id"]] = best_code or ""
        cp_to_name[cp["id"]] = best_name or best_code or ""

    rc_map: dict[str, dict] = {}
    for rc in ijson.items(_s(), "fareDelivery.fareStructure.regionalConstraints.item"):
        rc_map[rc["id"]] = rc

    cal: dict = {}
    for item in ijson.items(_s(), "fareDelivery.fareStructure.calendars.item"):
        cal = item
        break

    carriers_set: set[str] = set()
    for cc in ijson.items(_s(), "fareDelivery.fareStructure.carrierConstraints.item"):
        for c in cc.get("includedCarrier", []):
            carriers_set.add(c)

    set_progress(20)

    # Single pass through fares: build seen_categories AND rc_prices simultaneously
    seen_categories: dict = {}
    category_order: list = []
    rc_prices: dict = {}
    fare_count = 0

    for fare in ijson.items(_s(), "fareDelivery.fareStructure.fares.item"):
        fare_count += 1
        nr = fare.get("nameRef", "")
        pc_ref = fare.get("passengerConstraintRef", "")
        sc = fare.get("serviceClassRef", "")
        red = fare.get("reductionConstraintRef", "")
        svc = fare.get("serviceConstraintRef", "")
        key = (nr, pc_ref, sc, red, svc)

        # Category discovery (pass 1 logic)
        if key not in seen_categories:
            parts = [text_map.get(nr, nr)]
            sc_name = service_class_map.get(sc, "")
            if sc_name:
                parts.append(sc_name)
            red_name = reduction_map.get(red, "")
            if red_name:
                parts.append(red_name)
            svc_name = svc_constraint_map.get(svc, "")
            if svc_name:
                parts.append(svc_name)
            seen_categories[key] = " ".join(parts)
            category_order.append(key)

        # Price collection (pass 2 logic)
        rc_ref = fare.get("regionalConstraintRef")
        price_ref = fare.get("priceRef")
        amount = price_map.get(price_ref)
        if amount is not None and rc_ref:
            rc_prices.setdefault(rc_ref, {})[key] = amount

        if fare_count % 100000 == 0:
            set_progress(20 + min(40, int(fare_count / 10000)))

    set_progress(60)

    # Pass 2 (in-memory): legg til passasjertype kun for duplikater
    name_counts: dict = {}
    for name in seen_categories.values():
        name_counts[name] = name_counts.get(name, 0) + 1
    duplicates = {name for name, count in name_counts.items() if count > 1}

    for key in category_order:
        if seen_categories[key] in duplicates:
            nr, pc_ref, sc, red, svc = key
            pc = pc_map.get(pc_ref, {})
            ptype = PASSENGER_TYPE_LABELS.get(pc.get("passengerType", ""), "")
            fare_name = text_map.get(nr, nr)
            sc_name = service_class_map.get(sc, "")
            red_name = reduction_map.get(red, "")
            svc_name = svc_constraint_map.get(svc, "")
            parts = [fare_name]
            if ptype:
                parts.append(ptype)
            if sc_name:
                parts.append(sc_name)
            if red_name:
                parts.append(red_name)
            if svc_name:
                parts.append(svc_name)
            seen_categories[key] = " ".join(parts)

    set_progress(70)

    # Bygg rader: slå sammen priser fra alle RC-er med samme stasjonsparparet
    pair_data: dict = {}  # sortert UIC-par -> {"entry_cp", "exit_cp", "rc", "prices"}

    for rc_id, prices in rc_prices.items():
        rc = rc_map.get(rc_id)
        if not rc:
            continue
        entry_cp = rc["entryConnectionPointId"]
        exit_cp = rc["exitConnectionPointId"]
        entry_uic = cp_to_uic.get(entry_cp, "")
        exit_uic = cp_to_uic.get(exit_cp, "")

        pair = tuple(sorted([entry_uic, exit_uic]))
        if pair not in pair_data:
            pair_data[pair] = {
                "entry_cp": entry_cp,
                "exit_cp": exit_cp,
                "rc": rc,
                "prices": {},
            }
        pair_data[pair]["prices"].update(prices)

    rows = []
    for pdata in pair_data.values():
        entry_cp = pdata["entry_cp"]
        exit_cp = pdata["exit_cp"]
        rc = pdata["rc"]
        prices = pdata["prices"]
        row = {
            "From UIC": cp_to_uic.get(entry_cp, ""),
            "From station": cp_to_name.get(entry_cp, ""),
            "To UIC": cp_to_uic.get(exit_cp, ""),
            "To station": cp_to_name.get(exit_cp, ""),
            "Km": rc.get("distance", ""),
        }
        for key in category_order:
            amount = prices.get(key)
            row[seen_categories[key]] = round(amount / 100, 2) if amount is not None else None
        rows.append(row)

    rows.sort(key=lambda r: r["From station"])

    set_progress(80)

    # Bygg XLSX
    wb = Workbook()
    ws = wb.active
    ws.title = f"Priser {delivery_id}"

    fieldnames = (
        ["From UIC", "From station", "To UIC", "To station", "Km"]
        + [seen_categories[key] for key in category_order]
    )
    num_cols = len(fieldnames)
    last_col = get_column_letter(num_cols)

    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", start_color="0066CC")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    normal_font = Font(name="Arial", size=10)
    alt_fill = PatternFill("solid", start_color="E8F1FB")
    price_col_start = 6

    # --- Metadata-seksjon øverst ---
    utc_offset = cal.get("utcOffset", 0)
    tz = timezone(timedelta(minutes=utc_offset))

    def parse_osdm_date(dt_str):
        if not dt_str:
            return ""
        try:
            dt = datetime.fromisoformat(dt_str.replace("+0000", "+00:00"))
            return dt.astimezone(tz).strftime("%Y-%m-%d")
        except Exception:
            return dt_str[:10]

    valid_from  = parse_osdm_date(cal.get("fromDate", ""))
    valid_until = parse_osdm_date(cal.get("untilDate", ""))
    carriers = sorted(carriers_set)
    carriers_display = ", ".join(
        f"{RICS_CARRIER_NAMES[c]} ({c})" if c in RICS_CARRIER_NAMES else c
        for c in carriers
    )
    usage = delivery.get("usage", "")

    thin = Side(style="thin", color="B0C4DE")
    cell_border  = Border(left=thin, right=thin, top=thin, bottom=thin)
    label_fill   = PatternFill("solid", start_color="D0E4F7")
    value_fill   = PatternFill("solid", start_color="F5F9FF")
    label_font   = Font(name="Arial", size=10, bold=True, color="1A3A5C")
    value_font   = Font(name="Arial", size=10, color="1A1A1A")
    label_align  = Alignment(horizontal="left", vertical="center", indent=1)

    # Rad 1: tittelbar
    ws.merge_cells(f"A1:{last_col}1")
    tc = ws["A1"]
    tc.value = (
        f"OSDM Fare Delivery  —  "
        f"Provider {delivery.get('fareProvider', '')}  /  "
        f"Delivery {delivery.get('deliveryId', '')}"
    )
    tc.font      = Font(name="Arial", size=13, bold=True, color="FFFFFF")
    tc.fill      = PatternFill("solid", start_color="003A7A")
    tc.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Rader 2-6: to label+verdi-par per rad
    info_rows = [
        ("Fare Provider", delivery.get("fareProvider", ""),
         "Valid from",    valid_from),
        ("Delivery ID",   delivery.get("deliveryId", ""),
         "Valid until",   valid_until),
        ("OSDM version",  delivery.get("version", ""),
         "Carriers",      carriers_display),
        ("Usage",         usage,
         "Route pairs",   str(len(pair_data))),
        ("Optional",      "Yes" if delivery.get("optionalDelivery") else "No",
         "Fares",         f"{fare_count:,}"),
    ]

    for r_off, (lbl1, val1, lbl2, val2) in enumerate(info_rows, start=2):
        for col, text, is_label in (
            (1, lbl1, True), (2, val1, False),
            (3, lbl2, True), (4, val2, False),
        ):
            cell = ws.cell(row=r_off, column=col, value=text)
            cell.font      = label_font if is_label else value_font
            cell.fill      = label_fill if is_label else value_fill
            cell.border    = cell_border
            cell.alignment = label_align
        # Fargelegg usage-verdien
        if lbl1 == "Usage":
            uc = ws.cell(row=r_off, column=2)
            if val1 == "PRODUCTION":
                uc.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                uc.fill = PatternFill("solid", start_color="2E7D32")
            elif "TEST" in val1:
                uc.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                uc.fill = PatternFill("solid", start_color="C65000")
        ws.row_dimensions[r_off].height = 18

    # Rad 7: tom separator — pristabell starter på rad 8
    TABLE_HEADER_ROW = 8
    TABLE_DATA_START = 9

    # --- Pristabell-header (rad 8) ---
    for col_idx, col_name in enumerate(fieldnames, start=1):
        cell = ws.cell(row=TABLE_HEADER_ROW, column=col_idx, value=col_name)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = header_align

    # --- Pristabell-data (rad 9+) ---
    for row_idx, row in enumerate(rows, start=TABLE_DATA_START):
        fill = alt_fill if row_idx % 2 == 0 else None
        for col_idx, col_name in enumerate(fieldnames, start=1):
            val = row.get(col_name, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = normal_font
            if fill:
                cell.fill = fill
            if col_idx == 5:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")
            elif col_idx >= price_col_start:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right")

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 22
    ws.column_dimensions["E"].width = 8
    for col_idx in range(price_col_start, num_cols + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 20

    ws.freeze_panes = f"A{TABLE_DATA_START}"
    ws.auto_filter.ref = f"A{TABLE_HEADER_ROW}:{last_col}{TABLE_HEADER_ROW + len(rows)}"
    ws.row_dimensions[TABLE_HEADER_ROW].height = 30

    set_progress(95)
    row_count = len(rows)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read(), row_count


@router.post("/frontend/osdm-to-csv")
def osdm_to_csv(
    request: Request,
    osdmFile: UploadFile = File(...),
):
    require_login(request)

    content = osdmFile.file.read()
    require_fare_delivery(content)
    return {"jobId": start_xlsx_job(content, request.session["user_email"])}


def start_xlsx_job(osdm_bytes: bytes, owner: str) -> str:
    """Start Excel-konvertering i bakgrunnen (XLSX_JOBS). Brukes også av generering."""
    delivery = read_delivery(osdm_bytes)
    env_suffix = "test" if delivery.get("usage") == "TEST_ONLY" else "prod"
    filename = f"{delivery.get('fareProvider', '')}_{delivery.get('deliveryId', 'osdm')}_{env_suffix}.xlsx"
    job_id = create_job(XLSX_JOBS, owner, filename=None, rows=0)

    def run():
        try:
            xlsx_bytes, row_count = osdm_to_xlsx_bytes(osdm_bytes, job_id, XLSX_JOBS)
            XLSX_JOBS[job_id].update({"status": "done", "result": xlsx_bytes, "filename": filename,
                                      "rows": row_count, "percent": 100})
            log_event(owner, "excel_exported", detail={"filename": filename, "rows": row_count})
        except Exception as e:
            logger.exception("Excel-eksport feilet")
            XLSX_JOBS[job_id].update({"status": "error", "error": str(e)})
            log_event(owner, "excel_exported", "error", {"error": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return job_id

@router.get("/frontend/osdm-to-csv-status/{job_id}")
def osdm_to_csv_status(job_id: str, request: Request):
    require_login(request)
    job = owned_job(XLSX_JOBS, job_id, request)
    if not job:
        raise AppError(404, "job_not_found")
    return {
        "status": job["status"],
        "percent": job.get("percent", 0),
        "error": job.get("error"),
        "filename": job.get("filename"),
        "rows": job.get("rows", 0),
    }

@router.get("/frontend/osdm-to-csv-download/{job_id}")
def osdm_to_csv_download(job_id: str, request: Request):
    require_login(request)
    job = owned_job(XLSX_JOBS, job_id, request)
    if not job or job["status"] != "done":
        raise AppError(404, "file_not_ready")
    xlsx_bytes = job["result"]
    filename = _safe_filename(job["filename"] or "osdm.xlsx")  # jobben ryddes etter 2 t, så filen kan lastes ned flere ganger
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/osdmtoexcel", response_class=HTMLResponse)
@router.head("/osdmtoexcel")
def osdmtoexcel_page(request: Request):
    return serve_page(request, "osdmtoexcel.html")
