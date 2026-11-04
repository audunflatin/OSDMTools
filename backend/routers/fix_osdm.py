"""Rydd opp i OSDM: fjern farer med brutte referanser og ubrukte elementer."""
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
import io, json, logging, time
import ijson

from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import FIX_OSDM_STORE
from backend.core.osdm_bytes import _find_section_span, _apply_byte_replacements, _ijson_default, require_fare_delivery
from backend.core.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ui/fix-osdm")
def fix_osdm(request: Request, osdmFile: UploadFile = File(...)):
    require_login(request)

    try:
        content = osdmFile.file.read()
    except Exception:
        raise AppError(400, "file_unreadable")

    require_fare_delivery(content)

    stats: dict[str, int] = {}

    # Bygg sett med definerte IDer via ijson
    price_ids  = {p["id"] for p in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.prices.item")}
    pc_ids     = {p["id"] for p in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.passengerConstraints.item")}
    cp_ids     = {cp["id"] for cp in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.connectionPoints.item")}
    cc_ids     = {c["id"] for c in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.carrierConstraints.item")}
    bundle_ids = {b["id"] for b in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.fareConstraintBundles.item")}
    text_ids   = {t["id"] for t in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.texts.item")}

    # Load regionalConstraints list (small)
    rc_list_orig: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.regionalConstraints.item"))

    # 1. Fjern RC-er med ugyldig CP-referanse
    rc_list_good = [
        rc for rc in rc_list_orig
        if rc.get("entryConnectionPointId") in cp_ids
        and rc.get("exitConnectionPointId") in cp_ids
    ]
    rc_ids = {r["id"] for r in rc_list_good}
    stats["removed_bad_rcs"] = len(rc_list_orig) - len(rc_list_good)

    # 2. Stream fares: find bad fares AND collect used IDs simultaneously
    fares_count_orig = 0
    bad_fare_ids: set[str] = set()
    used_price_ids: set[str] = set()
    used_pc_ids: set[str] = set()
    used_rc_ids: set[str] = set()

    for fare in ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.fares.item"):
        fares_count_orig += 1
        is_bad = (
            (fare.get("priceRef") and fare["priceRef"] not in price_ids)
            or (fare.get("passengerConstraintRef") and fare["passengerConstraintRef"] not in pc_ids)
            or (fare.get("regionalConstraintRef") and fare["regionalConstraintRef"] not in rc_ids)
            or (fare.get("carrierConstraintRef") and fare["carrierConstraintRef"] not in cc_ids)
            or (fare.get("bundleRef") and fare["bundleRef"] not in bundle_ids)
            or (fare.get("nameRef") and fare["nameRef"] not in text_ids)
        )
        if is_bad:
            bad_fare_ids.add(fare.get("id", ""))
        else:
            if fare.get("priceRef"):
                used_price_ids.add(fare["priceRef"])
            if fare.get("passengerConstraintRef"):
                used_pc_ids.add(fare["passengerConstraintRef"])
            if fare.get("regionalConstraintRef"):
                used_rc_ids.add(fare["regionalConstraintRef"])

    bad_fare_count = len(bad_fare_ids)
    stats["removed_bad_fares"] = bad_fare_count

    # Load small arrays for filtering
    prices_orig: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.prices.item"))
    pc_orig: list[dict] = list(ijson.items(io.BytesIO(content), "fareDelivery.fareStructure.passengerConstraints.item"))

    prices_filtered = [p for p in prices_orig if p["id"] in used_price_ids]
    pc_filtered = [p for p in pc_orig if p["id"] in used_pc_ids]
    rc_filtered = [r for r in rc_list_good if r["id"] in used_rc_ids]

    stats["removed_unused_prices"] = len(prices_orig) - len(prices_filtered)
    stats["removed_unused_pcs"] = len(pc_orig) - len(pc_filtered)
    stats["removed_unused_rcs"] = len(rc_list_good) - len(rc_filtered)

    # Build output bytes
    if bad_fare_count > 0:
        # Rebuild fares section by streaming and filtering
        fares_val_start, fares_val_end = _find_section_span(content, "fares")
        out = bytearray()
        out += content[:fares_val_start]
        out += b"["
        first = True
        for fare in ijson.items(io.BytesIO(content[fares_val_start:fares_val_end]), "item"):
            fare_id = fare.get("id", "")
            if fare_id in bad_fare_ids:
                continue
            if not first:
                out += b","
            out += json.dumps(fare, ensure_ascii=False, default=_ijson_default).encode("utf-8")
            first = False
        out += b"]"
        out += content[fares_val_end:]
        result_bytes = bytes(out)
    else:
        result_bytes = content

    # Apply replacements for the small arrays
    try:
        prices_span = _find_section_span(result_bytes, "prices")
        pc_span     = _find_section_span(result_bytes, "passengerConstraints")
        rc_span     = _find_section_span(result_bytes, "regionalConstraints")
    except KeyError as e:
        raise AppError(500, "internal_error", detail=f"mangler nøkkel {e}")

    result_bytes = _apply_byte_replacements(result_bytes, [
        (prices_span[0], prices_span[1], json.dumps(prices_filtered, ensure_ascii=False, default=_ijson_default).encode("utf-8")),
        (pc_span[0],     pc_span[1],     json.dumps(pc_filtered,     ensure_ascii=False, default=_ijson_default).encode("utf-8")),
        (rc_span[0],     rc_span[1],     json.dumps(rc_filtered,     ensure_ascii=False, default=_ijson_default).encode("utf-8")),
    ])

    orig_name = _safe_filename(osdmFile.filename or "osdm.json")
    base = orig_name.rsplit(".", 1)[0] if "." in orig_name else orig_name
    out_name = f"{base}_fixed.json"

    user_email = request.session.get("user_email")
    FIX_OSDM_STORE[user_email] = {"filename": out_name, "content": result_bytes, "created_at": time.time()}

    log_event(user_email, "osdm_analyzed", detail=stats)

    return JSONResponse({"stats": stats, "filename": out_name})


@router.get("/ui/fix-osdm/download")
async def fix_osdm_download(request: Request):
    user_email = request.session.get("user_email")
    if not user_email:
        raise AppError(401, "not_logged_in")
    entry = FIX_OSDM_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_result")
    log_event(user_email, "osdm_fixed", detail={"filename": entry["filename"]})
    return Response(
        content=entry["content"],
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )


@router.get("/fix-osdm", response_class=HTMLResponse)
@router.head("/fix-osdm")
def fix_osdm_page(request: Request):
    return serve_page(request, "fix-osdm.html")
