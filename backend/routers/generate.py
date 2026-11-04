"""Priser fra avstandsfil: TEN-validering, OSDM-validering, avstandssjekk, valutakurs, generering og nedlasting."""
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
import csv, io, json, logging, math, time
import ijson
import requests

import backend.osdm_editor as osdm_editor
from backend.routers.excel import start_xlsx_job
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.state import TEN_STORE, GENERATION_PROGRESS, OSDM_STORE, INPUT_OSDM_STORE, VALIDATION_JOBS, start_file_job, file_job_progress

from backend.core.osdm_bytes import check_fare_delivery, read_delivery

logger = logging.getLogger(__name__)
router = APIRouter()


def validate_ten_csv(text: str):
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = []

    for i, row in enumerate(reader, start=1):
        if len(row) != 3:
            return {"ok": False, "error": f"Linje {i}: feil kolonneantall (forventet 3, fikk {len(row)})"}

        frm, to, price = [c.strip().replace(" ", "").replace("\xa0", "") for c in row]

        try:
            frm_int = int(frm)
        except ValueError:
            return {"ok": False, "error": f"Linje {i}: ugyldig 'fra'-verdi: '{frm}' er ikke et heltall"}

        try:
            to_int = int(to)
        except ValueError:
            return {"ok": False, "error": f"Linje {i}: ugyldig 'til'-verdi: '{to}' er ikke et heltall"}

        try:
            price_int = int(price)
        except ValueError:
            return {"ok": False, "error": f"Linje {i}: ugyldig pris: '{price}' er ikke et heltall"}

        if frm_int < 0 or to_int < 0:
            return {"ok": False, "error": f"Linje {i}: negative km-verdier er ikke tillatt"}

        if frm_int >= to_int:
            return {"ok": False, "error": f"Linje {i}: 'fra' ({frm_int}) må være mindre enn 'til' ({to_int})"}

        if price_int <= 0:
            return {"ok": False, "error": f"Linje {i}: pris må være større enn 0 (fikk {price_int})"}

        if rows and frm_int != rows[-1][1]:
            return {"ok": False, "error": f"Linje {i}: gap eller overlapp i km-intervall (forrige til={rows[-1][1]}, denne fra={frm_int})"}

        rows.append((frm_int, to_int, price_int))

    if not rows:
        return {"ok": False, "error": "Filen er tom"}

    return {
        "ok": True,
        "rows": len(rows),
        "from_km": rows[0][0],
        "to_km": rows[-1][1],
        "table": rows
    }

@router.post("/ui/validate-ten")
def validate_ten(request: Request, tenFile: UploadFile = File(...)):
    require_login(request)
    user_email = request.session["user_email"]

    content = tenFile.file.read().decode("utf-8-sig")
    result = validate_ten_csv(content)

    if not result["ok"]:
        TEN_STORE.pop(user_email, None)
        return {"ok": False, "error": result["error"]}

    TEN_STORE[user_email] = {"table": result["table"], "created_at": time.time()}
    return {"ok": True}


def _run_osdm_validation(job_id: str, file_bytes: bytes) -> None:
    job = VALIDATION_JOBS[job_id]
    try:
        def _s():
            return io.BytesIO(file_bytes)

        # --- Check basic structure (stop at first key, no large objects) ---
        job["phase"] = "parsing"
        error = check_fare_delivery(file_bytes)
        if error:
            job.update({"status": "error", "error": error})
            return

        job["phase"] = "validating"
        job["percent"] = 28

        # --- Delivery info (small object) ---
        delivery = read_delivery(file_bytes)

        job["percent"] = 32

        # --- Collect constraint IDs (streaming, one pass per array) ---
        price_ids  = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.prices.item") if "id" in item}
        pc_ids     = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.passengerConstraints.item") if "id" in item}
        cc_ids     = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.carrierConstraints.item") if "id" in item}
        bundle_ids = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.fareConstraintBundles.item") if "id" in item}
        text_ids   = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.texts.item") if "id" in item}
        cp_ids     = {item["id"] for item in ijson.items(_s(), "fareDelivery.fareStructure.connectionPoints.item") if "id" in item}

        rc_ids  = set()
        rc_list = []
        for item in ijson.items(_s(), "fareDelivery.fareStructure.regionalConstraints.item"):
            if "id" in item:
                rc_ids.add(item["id"])
                rc_list.append({"id": item["id"],
                                "entryConnectionPointId": item.get("entryConnectionPointId"),
                                "exitConnectionPointId":  item.get("exitConnectionPointId")})

        job["percent"] = 38

        # --- Validate fares (streaming, 1.2M+ fares processed one at a time) ---
        used_price_ids: set[str] = set()
        used_pc_ids:    set[str] = set()
        used_rc_ids:    set[str] = set()
        missing: dict[str, set[str]] = {
            "priceRef": set(), "passengerConstraintRef": set(),
            "regionalConstraintRef": set(), "carrierConstraintRef": set(),
            "bundleRef": set(), "nameRef": set(),
        }
        warnings: list[str] = []

        fare_num = 0
        for fare in ijson.items(_s(), "fareDelivery.fareStructure.fares.item"):
            fare_num += 1
            pr = fare.get("priceRef")
            if pr:
                if pr not in price_ids: missing["priceRef"].add(pr)
                else: used_price_ids.add(pr)
            pc = fare.get("passengerConstraintRef")
            if pc:
                if pc not in pc_ids: missing["passengerConstraintRef"].add(pc)
                else: used_pc_ids.add(pc)
            rc = fare.get("regionalConstraintRef")
            if rc:
                if rc not in rc_ids: missing["regionalConstraintRef"].add(rc)
                else: used_rc_ids.add(rc)
            cc = fare.get("carrierConstraintRef")
            if cc and cc not in cc_ids: missing["carrierConstraintRef"].add(cc)
            br = fare.get("bundleRef")
            if br and br not in bundle_ids: missing["bundleRef"].add(br)
            nr = fare.get("nameRef")
            if nr and nr not in text_ids: missing["nameRef"].add(nr)
            if fare_num % 50000 == 0:
                # Asymptotic progress: 38 → 72 over ~1M fares
                job["percent"] = 38 + int(34 * (1 - math.exp(-fare_num / 500000)))

        job["percent"] = 72

        # --- RC connection point check ---
        rc_bad_cps: list[str] = []
        for rc in rc_list:
            for field in ("entryConnectionPointId", "exitConnectionPointId"):
                cp_ref = rc.get(field)
                if cp_ref and cp_ref not in cp_ids:
                    rc_bad_cps.append(f"{rc['id']}: {field}={cp_ref}")

        job["percent"] = 82
        label_map = {
            "priceRef": "prices", "passengerConstraintRef": "passengerConstraints",
            "regionalConstraintRef": "regionalConstraints", "carrierConstraintRef": "carrierConstraints",
            "bundleRef": "fareConstraintBundles", "nameRef": "texts",
        }
        for field, ids in missing.items():
            if ids:
                examples = ", ".join(sorted(ids)[:5])
                more = f" (+ {len(ids) - 5} til)" if len(ids) > 5 else ""
                warnings.append(f"{len(ids)} fare(r) peker på ukjent {field} i {label_map[field]}: {examples}{more}")
        if rc_bad_cps:
            examples = "; ".join(rc_bad_cps[:3])
            more = f" (+ {len(rc_bad_cps) - 3} til)" if len(rc_bad_cps) > 3 else ""
            warnings.append(f"RC-er med ugyldig CP-referanse: {examples}{more}")

        unused_prices = price_ids - used_price_ids
        unused_pcs    = pc_ids - used_pc_ids
        unused_rcs    = rc_ids - used_rc_ids
        if unused_prices:
            warnings.append(f"{len(unused_prices)} pris(er) er definert men ikke referert av noen fare")
        if unused_pcs:
            warnings.append(f"{len(unused_pcs)} passengerConstraint(s) er definert men ikke referert av noen fare")
        if unused_rcs:
            warnings.append(f"{len(unused_rcs)} regionalConstraint(s) er definert men ikke referert av noen fare")

        job["percent"] = 92

        # --- Station count (small array, load each CP as dict) ---
        station_count = 0
        for cp in ijson.items(_s(), "fareDelivery.fareStructure.connectionPoints.item"):
            for ss in cp.get("stationSets", []):
                for s in ss if isinstance(ss, list) else [ss]:
                    if isinstance(s, dict) and s.get("codeList") == "UIC":
                        station_count += 1

        job.update({
            "status": "done", "percent": 100,
            "result": {
                "ok": True,
                "warnings": warnings,
                "deliveryId": delivery.get("deliveryId", ""),
                "fareProvider": delivery.get("fareProvider", ""),
                "fareCount": len(rc_ids),
                "priceCount": len(price_ids),
                "stationCount": station_count,
            }
        })

    except Exception as e:
        job.update({"status": "error", "error": str(e)})


@router.post("/ui/validate-osdm")
async def validate_osdm(request: Request, osdmFile: UploadFile = File(...)):
    require_login(request)
    file_bytes = await osdmFile.read()
    user_email = request.session.get("user_email", "")
    INPUT_OSDM_STORE[user_email] = {
        "filename": osdmFile.filename or "osdm.json",
        "bytes": file_bytes,
        "created_at": time.time(),
    }
    return {"jobId": start_file_job(VALIDATION_JOBS, user_email, _run_osdm_validation, file_bytes)}


@router.get("/ui/validate-osdm/progress/{job_id}")
def validate_osdm_progress(job_id: str, request: Request):
    require_login(request)
    return file_job_progress(VALIDATION_JOBS, job_id, request)


@router.post("/ui/validate-distances")
def validate_distances(
    request: Request,
    distanceFile: UploadFile = File(...),
    osdmFile: UploadFile = File(...),
):
    require_login(request)

    # Parse TEN-format avstandsfil (fra_km;til_km;pris)
    try:
        csv_text = (distanceFile.file.read()).decode("utf-8-sig")
    except Exception:
        return {"ok": False, "error": "Kunne ikke lese avstandsfilen"}

    ten_result = validate_ten_csv(csv_text)
    if not ten_result["ok"]:
        return {"ok": False, "error": ten_result["error"]}
    ten_table: list[tuple[int, int, int]] = ten_result["table"]  # (fra_km, til_km, pris)

    # Parse OSDM
    try:
        data = json.loads(osdmFile.file.read())
    except Exception:
        return {"ok": False, "error": "OSDM-filen er ikke gyldig JSON"}

    fs = data.get("fareDelivery", {}).get("fareStructure", {})
    if not fs:
        return {"ok": False, "error": "OSDM-filen mangler fareDelivery.fareStructure"}

    # Stasjonsnavn fra stationNames
    station_names: dict[str, str] = {
        sn["code"]: sn.get("nameUtf8") or sn.get("name") or sn["code"]
        for sn in fs.get("stationNames", [])
    }

    # CP-id → beste UIC-kode og stasjonsnavn
    cp_to_uic: dict[str, str] = {}
    cp_to_name: dict[str, str] = {}
    for cp in fs.get("connectionPoints", []):
        uic_codes = [
            s["code"]
            for ss in cp.get("stationSets", [])
            for s in ss
            if s.get("codeList") == "UIC"
        ]
        best = next((c for c in uic_codes if str(c).startswith("76")), uic_codes[0] if uic_codes else None)
        if best:
            cp_to_uic[cp["id"]] = str(best)
            cp_to_name[cp["id"]] = station_names.get(str(best), str(best))

    # Samle unike avstandsverdier fra OSDM (én per RC, ignorer duplikater)
    osdm_distances: dict[int, list[str]] = {}  # km → liste med "Fra → Til"-beskrivelser
    for rc in fs.get("regionalConstraints", []):
        km = rc.get("distance")
        if km is None:
            continue
        entry = cp_to_name.get(rc.get("entryConnectionPointId", ""), rc.get("entryConnectionPointId", ""))
        exit_ = cp_to_name.get(rc.get("exitConnectionPointId", ""), rc.get("exitConnectionPointId", ""))
        osdm_distances.setdefault(km, []).append(f"{entry} → {exit_}")

    def ten_covers(km: int) -> bool:
        return any(frm <= km <= til for frm, til, _ in ten_table)

    warnings: list[str] = []
    uncovered: list[str] = []

    for km, routes in sorted(osdm_distances.items()):
        if not ten_covers(km):
            example = routes[0]
            more = f" (+ {len(routes) - 1} RC til med samme avstand)" if len(routes) > 1 else ""
            uncovered.append(f"{km} km ({example}{more})")

    if uncovered:
        cap = 15
        examples = "; ".join(uncovered[:cap])
        more = f" (+ {len(uncovered) - cap} til)" if len(uncovered) > cap else ""
        warnings.append(
            f"{len(uncovered)} avstandsverdi(er) fra OSDM dekkes ikke av TEN-tabellen: {examples}{more}"
        )

    ten_max = ten_table[-1][1]
    osdm_max = max(osdm_distances.keys()) if osdm_distances else 0
    if osdm_max > ten_max:
        warnings.append(
            f"OSDM har ruter opp til {osdm_max} km, men TEN-tabellen slutter på {ten_max} km"
        )

    return {
        "ok": True,
        "warnings": warnings,
        "tenRows": len(ten_table),
        "tenRangeKm": f"{ten_table[0][0]}–{ten_table[-1][1]}",
        "osdmDistinctDistances": len(osdm_distances),
        "osdmRcCount": sum(len(v) for v in osdm_distances.values()),
        "uncoveredCount": len(uncovered),
    }


@router.get("/ui/exchange-rate")
def exchange_rate(request: Request, from_: str = "EUR", to: str = "NOK"):
    require_login(request)
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": from_.upper(), "to": to.upper()},
            timeout=5,
        )
        resp.raise_for_status()
        payload = resp.json()
        rate = payload["rates"][to.upper()]
        return {"ok": True, "from": from_.upper(), "to": to.upper(), "rate": rate, "date": payload.get("date")}
    except Exception as exc:
        logger.warning("Kunne ikke hente valutakurs %s→%s: %s", from_, to, exc)
        return {"ok": False, "error": "Kunne ikke hente valutakurs fra frankfurter.app"}


def nok_price_from_distance(ten_table: list, km: int):
    for frm, to, price in ten_table:
        if frm <= km <= to:
            return price
    raise ValueError(f"Ingen TEN-pris for {km} km")


@router.post("/ui/generate-osdm")
def generate_osdm(
    request: Request,
    exchangeRate: float = Form(...),
    validFrom: str = Form(...),
    validTo: str = Form(...),
    datasetId: str = Form(...),
    environment: str = Form(...),
    optionalDelivery: str = Form("false"),
    previousDeliveryId: str = Form(""),
):
    require_login(request)

    user_email = request.session.get("user_email", "")

    ten_entry = TEN_STORE.get(user_email)
    if not ten_entry:
        raise HTTPException(status_code=400, detail="TEN-CSV er ikke validert")

    input_entry = INPUT_OSDM_STORE.get(user_email)
    if not input_entry:
        raise HTTPException(status_code=400, detail="OSDM-malfil er ikke lastet opp og validert")

    progress = {"status": "running", "percent": 0, "created_at": time.time()}
    GENERATION_PROGRESS[user_email] = progress
    try:
        return _generate_osdm(
            user_email, ten_entry["table"], input_entry, progress,
            exchangeRate, validFrom, validTo, datasetId, environment, optionalDelivery, previousDeliveryId,
        )
    finally:
        if progress["status"] == "running":
            progress["status"] = "error"


def _generate_osdm(
    user_email: str, ten_table: list, input_entry: dict, progress: dict,
    exchangeRate: float, validFrom: str, validTo: str, datasetId: str,
    environment: str, optionalDelivery: str, previousDeliveryId: str,
):

    store_entry = osdm_editor.load_osdm(input_entry["bytes"])
    data = store_entry["data"]
    fs = data["fareDelivery"]["fareStructure"]

    utc_offset_from = osdm_editor.apply_delivery_fields(
        store_entry, datasetId, previousDeliveryId, optionalDelivery, environment, validFrom, validTo,
    )

    # Compute adult price in EUR for each RC from TEN table + exchange rate
    rcs = fs.get("regionalConstraints", [])
    total = len(rcs) or 1
    rc_adult_prices: dict[str, float] = {}
    skipped_rcs: list[dict] = []  # RC-er som beholder malens priser (mangler avstand / ikke dekket av TEN)
    for idx, rc in enumerate(rcs, start=1):
        km = rc.get("distance")
        try:
            if km is None:
                raise ValueError
            rc_adult_prices[rc["id"]] = nok_price_from_distance(ten_table, km) * exchangeRate
        except ValueError:
            skipped_rcs.append(rc)
        progress["percent"] = int(idx / total * 60)

    # Set all prices using ratios inferred from the uploaded OSDM file
    result = osdm_editor.set_prices_from_adult(store_entry, rc_adult_prices)
    progress["percent"] = 90

    # Build example fares from first few RCs that got prices
    cp_to_uic = store_entry["cp_to_uic"]
    station_name_map = store_entry["station_name_map"]

    def _rc_label(rc: dict) -> str:
        from_uic = cp_to_uic.get(rc.get("entryConnectionPointId", ""), "")
        to_uic = cp_to_uic.get(rc.get("exitConnectionPointId", ""), "")
        from_name = station_name_map.get(from_uic, from_uic or "?")
        to_name = station_name_map.get(to_uic, to_uic or "?")
        return f"{from_name} → {to_name} ({rc.get('distance', '?')} km)"

    skipped_examples = [_rc_label(rc) for rc in skipped_rcs[:5]]
    examples: dict[str, str] = {}
    example_idx = 1
    for rc in rcs:
        if example_idx > 5:
            break
        rc_id = rc["id"]
        if rc_id not in rc_adult_prices:
            continue
        from_uic = cp_to_uic.get(rc.get("entryConnectionPointId", ""), "")
        to_uic = cp_to_uic.get(rc.get("exitConnectionPointId", ""), "")
        from_name = station_name_map.get(from_uic, from_uic or "?")
        to_name = station_name_map.get(to_uic, to_uic or "?")
        adult_eur = osdm_editor.round_up_020(rc_adult_prices[rc_id])
        examples[f"example_{example_idx}"] = (
            f"{from_name} → {to_name}: {adult_eur:.2f} EUR ({rc.get('distance', '?')} km)"
        )
        example_idx += 1

    content, fare_provider = osdm_editor.serialize_with_new_prefix(store_entry, datasetId)

    progress["status"] = "done"
    progress["percent"] = 100

    filename = _safe_filename(f"{fare_provider}_{datasetId}_{environment}.json")
    OSDM_STORE[user_email] = {"filename": filename, "content": content, "created_at": time.time()}

    log_event(user_email, "osdm_generated", detail={
        "deliveryId": datasetId,
        "environment": environment,
        "validFrom": validFrom,
        "validTo": validTo,
        "updatedFares": result["updated_fares"],
        "newPrices": result["new_prices"],
        "rcsSkipped": len(skipped_rcs),
    })

    return {
        "step": "OSDM generation",
        "ok": True,
        "outputFile": filename,
        "summary": {
            "pricesUpdated": result["updated_fares"],
            "exchangeRate": exchangeRate,
            "environment": environment,
            "utcOffset": utc_offset_from,
            "exampleFares": examples,
            "rcsSkipped": len(skipped_rcs),
            "rcsSkippedExamples": skipped_examples,
        },
    }


@router.get("/ui/progress")
def get_progress(request: Request):
    require_login(request)
    p = GENERATION_PROGRESS.get(request.session["user_email"], {"status": "idle", "percent": 0})
    return {"status": p["status"], "percent": p["percent"]}

@router.post("/ui/excel-from-generated")
def excel_from_generated(request: Request):
    require_login(request)
    user_email = request.session.get("user_email", "")
    osdm_entry = OSDM_STORE.get(user_email)
    if not osdm_entry:
        raise HTTPException(status_code=400, detail="Ingen generert OSDM-fil tilgjengelig")

    osdm_bytes = osdm_entry["content"].encode("utf-8") if isinstance(osdm_entry["content"], str) else osdm_entry["content"]

    return {"jobId": start_xlsx_job(osdm_bytes, user_email)}


@router.get("/ui/download-osdm/{filename}")
def download_osdm(filename: str, request: Request):
    require_login(request)
    return stored_file_response(OSDM_STORE.get(request.session["user_email"]), filename)


def stored_file_response(entry: dict | None, filename: str) -> Response:
    """Last ned en lagret OSDM-fil (generert eller regulert) hvis filnavnet stemmer."""
    if not entry or entry["filename"] != filename:
        raise HTTPException(status_code=404, detail="OSDM-fil finnes ikke")
    return Response(
        content=entry["content"],
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"'},
    )
