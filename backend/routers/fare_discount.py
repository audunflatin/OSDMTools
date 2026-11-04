"""Fare-discount: legg til rabatterte farer i eksisterende OSDM."""
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import base64, io, json, logging, re, time, uuid
import ijson

import backend.osdm_editor as osdm_editor
from backend.rics_codes import RICS_CODES as RICS_CARRIER_NAMES, RICS_COUNTRIES
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import DISCOUNT_STORE, PARSE_JOBS, start_file_job, file_job_progress
from backend.routers.excel import start_xlsx_job
from backend.routers.generate import stored_file_response
from backend.core.osdm_bytes import _append_to_json_array, check_fare_delivery, require_fare_delivery, read_delivery
from backend.core.errors import AppError, error_payload

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/fare-discount/rics")
def fare_discount_rics(request: Request):
    require_login(request)
    return [
        {"code": code, "name": name, "country": RICS_COUNTRIES.get(code, "")}
        for code, name in sorted(RICS_CARRIER_NAMES.items(), key=lambda x: x[1])
    ]


@router.get("/fare-discount", response_class=HTMLResponse)
@router.head("/fare-discount")
def fare_discount_page(request: Request):
    return serve_page(request, "fare-discount.html")

def _run_fare_discount_parse(job_id: str, file_bytes: bytes) -> None:
    job = PARSE_JOBS[job_id]
    try:
        def _s():
            return io.BytesIO(file_bytes)

        job["phase"] = "parsing"
        code = check_fare_delivery(file_bytes)
        if code:
            job.update({"status": "error", "error": error_payload(code)})
            return

        job["phase"] = "validating"
        job["percent"] = 20

        delivery = read_delivery(file_bytes)

        job["percent"] = 25

        texts_map: dict = {}
        for t in ijson.items(_s(), "fareDelivery.fareStructure.texts.item"):
            texts_map[t["id"]] = t

        job["percent"] = 35

        uic_to_name: dict[str, str] = {}
        for sn in ijson.items(_s(), "fareDelivery.fareStructure.stationNames.item"):
            code = sn.get("code") or sn.get("uicCode")
            name = sn.get("nameUtf8") or sn.get("name") or code
            if code:
                uic_to_name[str(code)] = name

        job["percent"] = 50

        stations: list[dict] = []
        seen: set[str] = set()
        for cp in ijson.items(_s(), "fareDelivery.fareStructure.connectionPoints.item"):
            for station_set in cp.get("stationSets", []):
                for s in station_set:
                    if s.get("codeList") == "UIC":
                        uic = str(s["code"])
                        if uic not in seen:
                            seen.add(uic)
                            stations.append({
                                "cp_id": cp["id"],
                                "uic": uic,
                                "name": uic_to_name.get(uic, uic),
                                "country": s.get("country", ""),
                            })
        stations.sort(key=lambda x: x["name"].lower())

        job["percent"] = 65

        carriers: list[dict] = []
        seen_codes: set[str] = set()
        for cc in ijson.items(_s(), "fareDelivery.fareStructure.carrierConstraints.item"):
            for code in cc.get("includedCarrier", []):
                if code not in seen_codes:
                    seen_codes.add(code)
                    carriers.append({
                        "code": code,
                        "name": RICS_CARRIER_NAMES.get(code, code),
                        "constraint_id": cc["id"],
                    })

        job["percent"] = 75

        seen_refs: dict[str, dict] = {}
        for pc in ijson.items(_s(), "fareDelivery.fareStructure.passengerConstraints.item"):
            ref = pc.get("nameRef", "")
            text_obj = texts_map.get(ref, {})
            name = text_obj.get("textUtf8") or text_obj.get("text") or ref
            if ref not in seen_refs:
                seen_refs[ref] = {"nameRef": ref, "name": name, "ids": []}
            seen_refs[ref]["ids"].append(pc["id"])
        passenger_constraints = list(seen_refs.values())

        job["percent"] = 85

        service_classes = []
        for scd in ijson.items(_s(), "fareDelivery.fareStructure.serviceClassDefinitions.item"):
            text_obj = texts_map.get(scd.get("textRef", ""), {})
            name = text_obj.get("textUtf8") or text_obj.get("text") or scd["id"]
            service_classes.append({"id": scd["id"], "name": name})

        job["percent"] = 95

        job.update({
            "status": "done", "percent": 100,
            "result": {
                "deliveryId": delivery.get("deliveryId", ""),
                "stations": stations,
                "carriers": carriers,
                "passengerConstraints": passenger_constraints,
                "serviceClasses": service_classes,
            }
        })

    except Exception as e:
        job.update({"status": "error", "error": str(e)})


@router.post("/fare-discount/parse")
async def fare_discount_parse(request: Request, osdmFile: UploadFile = File(...)):
    require_login(request)
    file_bytes = await osdmFile.read()
    return {"jobId": start_file_job(PARSE_JOBS, request.session["user_email"], _run_fare_discount_parse, file_bytes)}


@router.get("/fare-discount/parse/progress/{job_id}")
def fare_discount_parse_progress(job_id: str, request: Request):
    require_login(request)
    return file_job_progress(PARSE_JOBS, job_id, request)




def _id_base_from_delivery(delivery: dict, sample_ids: list[str]) -> str:
    """Utled ID-base fra delivery dict og en liste med eksisterende IDer som fallback."""
    provider = delivery.get("fareProvider", "")
    did = delivery.get("deliveryId", "")
    if provider and did:
        return f"{provider}_{did}_"
    for sample in sample_ids:
        if sample and "_" in sample:
            parts = sample.split("_")
            if len(parts) >= 2:
                return "_".join(parts[:2]) + "_"
    return f"disc_{did}_" if did else "disc_"


def _next_id_num(existing_ids: list[str], prefix: str) -> int:
    """Finn neste ledige nummer for IDer med gitt prefix."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    nums = [int(m.group(1)) for id_ in existing_ids if (m := pattern.search(id_))]
    return max(nums) + 1 if nums else 1


def _new_fare_id() -> str:
    """Generer en unik fare-ID i samme format som eksisterende IDer."""
    return "_" + base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode()


@router.post("/fare-discount/apply")
def fare_discount_apply(
    request: Request,
    osdmFile: UploadFile = File(...),
    stationPairsJson: str = Form(...),
    discountName: str = Form(...),
    carrierCodes: list[str] = Form(default=[]),
    discountPct: float = Form(...),
    passengerRefs: list[str] = Form(...),
    serviceClassIds: list[str] = Form(...),
):
    require_login(request)

    try:
        content = osdmFile.file.read()
    except Exception:
        raise AppError(400, "file_unreadable")

    try:
        station_pairs = json.loads(stationPairsJson)
    except Exception:
        raise AppError(400, "invalid_station_pairs")

    require_fare_delivery(content)

    # Load small sections via ijson
    delivery = read_delivery(content)
    delivery_id = delivery.get("deliveryId", "")

    regional_constraints: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.regionalConstraints.item"))
    passenger_constraints: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.passengerConstraints.item"))
    prices_list: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.prices.item"))
    texts_list: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.texts.item"))
    carrier_constraints_list: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.carrierConstraints.item"))

    # Derive id_base from delivery + sample IDs
    sample_ids = (
        [carrier_constraints_list[0]["id"]] if carrier_constraints_list else
        [texts_list[0]["id"]] if texts_list else
        [prices_list[0]["id"]] if prices_list else
        []
    )
    id_base = _id_base_from_delivery(delivery, sample_ids)

    # Finn relevante RC-er
    if not station_pairs:
        matching_rc_ids = {rc["id"] for rc in regional_constraints}
        if not matching_rc_ids:
            raise AppError(400, "no_regional_constraints")
    else:
        matching_rc_ids: set[str] = set()
        for pair in station_pairs:
            pair_cps = {pair["fromCpId"], pair["toCpId"]}
            for rc in regional_constraints:
                if {rc.get("entryConnectionPointId"), rc.get("exitConnectionPointId")} == pair_cps:
                    matching_rc_ids.add(rc["id"])
        if not matching_rc_ids:
            raise AppError(400, "no_rc_for_pairs")

    # nameRef → liste av passengerConstraint-IDer
    nameref_to_pc_ids: dict[str, list[str]] = {}
    for pc in passenger_constraints:
        ref = pc.get("nameRef", "")
        if ref in passengerRefs:
            nameref_to_pc_ids.setdefault(ref, []).append(pc["id"])
    selected_pc_ids = {pid for ids in nameref_to_pc_ids.values() for pid in ids}

    # Prisoppslag: priceId → første price-element
    price_lookup: dict[str, dict] = {
        p["id"]: e for p in prices_list if (e := osdm_editor.eur_entry(p))
    }

    multiplier = 1 - discountPct / 100

    # --- Ny carrierConstraint (kun hvis transportører er valgt) ---
    new_cc_id: str | None = None
    new_carrier_constraint: dict | None = None
    if carrierCodes:
        cc_prefix = f"{id_base}C__"
        existing_cc_ids = [c["id"] for c in carrier_constraints_list]
        new_cc_id = f"{cc_prefix}{_next_id_num(existing_cc_ids, cc_prefix)}"
        new_carrier_constraint = {"id": new_cc_id, "includedCarrier": list(carrierCodes)}

    # --- Ny tekst ---
    text_prefix = f"{id_base}P__"
    existing_text_ids = [t["id"] for t in texts_list]
    new_text_id = f"{text_prefix}{_next_id_num(existing_text_ids, text_prefix)}"
    new_text = {
        "id": new_text_id,
        "textUtf8": discountName,
        "text": discountName,
        "shortTextUtf8": discountName,
        "shortText": discountName,
        "translations": [],
    }

    # --- Nye priser (dedupliser på beløp) ---
    price_prefix = f"{id_base}I__"
    existing_price_ids = [p["id"] for p in prices_list]
    next_price_num = _next_id_num(existing_price_ids, price_prefix)
    new_amount_to_price_id: dict[int, str] = {}
    new_prices: list[dict] = []

    def get_or_create_price_id(orig: dict) -> str:
        nonlocal next_price_num
        scale = orig.get("scale", 2)
        eur = orig["amount"] / (10 ** scale)
        discounted_int = int(round(osdm_editor.round_up_020(eur * multiplier) * (10 ** scale)))
        if discounted_int in new_amount_to_price_id:
            return new_amount_to_price_id[discounted_int]
        new_id = f"{price_prefix}{next_price_num}"
        next_price_num += 1
        new_prices.append({
            "id": new_id,
            "price": [{"currency": orig.get("currency", "EUR"), "amount": discounted_int,
                        "scale": scale, "vatDetails": []}],
        })
        new_amount_to_price_id[discounted_int] = new_id
        return new_id

    # --- Velg grunnlagsfare per (RC, passasjerkategori, klasse) ---
    # Filer som 1076 har flere farer per kombinasjon (vanlig + FIP). Rabatten skal
    # beregnes fra primærfaren, dvs. den med høyest pris — ikke den som står først.
    def _price_value(fare: dict) -> float:
        p = price_lookup.get(fare.get("priceRef", ""))
        return p["amount"] / (10 ** p.get("scale", 2)) if p else -1.0

    base_fares: dict[tuple, dict] = {}
    for fare in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.fares.item"):
        rc_ref = fare.get("regionalConstraintRef")
        pc_ref = fare.get("passengerConstraintRef")
        sc_ref = fare.get("serviceClassRef")
        if rc_ref not in matching_rc_ids or pc_ref not in selected_pc_ids or sc_ref not in serviceClassIds:
            continue
        if fare.get("priceRef") not in price_lookup:
            continue
        key = (rc_ref, pc_ref, sc_ref)
        if key not in base_fares or _price_value(fare) > _price_value(base_fares[key]):
            base_fares[key] = fare

    # --- Nye farer ---
    new_fares: list[dict] = []
    for (rc_ref, pc_ref, sc_ref), fare in base_fares.items():
        new_fare: dict = {
            "id": _new_fare_id(),
            "bundleRef": fare.get("bundleRef", ""),
            "fareType": fare.get("fareType", "ADMISSION"),
            "nameRef": new_text_id,
            "priceRef": get_or_create_price_id(price_lookup[fare["priceRef"]]),
            "regionalConstraintRef": rc_ref,
            "regulatoryConditions": fare.get("regulatoryConditions", ["CIV"]),
            "serviceClassRef": sc_ref,
            "passengerConstraintRef": pc_ref,
        }
        # Uten valgte transportører beholdes originalens begrensning, ellers ville
        # rabatten gjelde for alle transportører.
        carrier_ref = new_cc_id or fare.get("carrierConstraintRef")
        if carrier_ref:
            new_fare["carrierConstraintRef"] = carrier_ref
        # SilverRail/OSDM-tool trenger legacy-feltene for konvertering til 108.1 (se CLAUDE.md).
        for field in ("legacyAccountingIdentifier", "legacyConversion", "individualContracts"):
            if field in fare:
                new_fare[field] = fare[field]
        new_fare["involvedTCOs"] = list(fare.get("involvedTCOs") or carrierCodes)
        new_fares.append(new_fare)

    if not new_fares:
        raise AppError(400, "no_matching_fares")

    # Append new items to arrays using byte-level operations
    result_bytes = content
    result_bytes = _append_to_json_array(result_bytes, "fares", new_fares)
    result_bytes = _append_to_json_array(result_bytes, "prices", new_prices)
    result_bytes = _append_to_json_array(result_bytes, "texts", [new_text])
    if new_carrier_constraint:
        result_bytes = _append_to_json_array(result_bytes, "carrierConstraints", [new_carrier_constraint])

    user_email = request.session["user_email"]
    filename = _safe_filename(f"fareDelivery_{id_base.rstrip('_')}_discount.json")
    DISCOUNT_STORE[user_email] = {"filename": filename, "content": result_bytes, "created_at": time.time()}

    log_event(user_email, "discount_applied", detail={
        "deliveryId": delivery_id,
        "stationPairs": station_pairs,
        "carrierCodes": list(carrierCodes),
        "discountPct": discountPct,
        "fareName": discountName,
        "fareCount": len(new_fares),
        "priceCount": len(new_prices),
    })

    return {
        "ok": True,
        "outputFile": filename,
        "summary": {"fareCount": len(new_fares), "priceCount": len(new_prices),
                    "discountName": discountName, "discountPct": discountPct},
    }


@router.get("/fare-discount/download/{filename}")
def fare_discount_download(filename: str, request: Request):
    require_login(request)
    return stored_file_response(DISCOUNT_STORE.get(request.session["user_email"]), filename)


@router.post("/fare-discount/excel")
def fare_discount_excel(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = DISCOUNT_STORE.get(user_email)
    if not entry:
        raise AppError(400, "no_result")
    return {"jobId": start_xlsx_job(entry["content"], user_email)}
