"""OSDM-editor: passasjerratioer, metadata, relasjoner, grensepunkter, tekster og stasjonsnavn."""
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
import logging, time

import backend.osdm_editor as osdm_editor
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import EDIT_STORE
from backend.core.errors import AppError, error_payload

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/osdm-editor/load")
def editor_load(request: Request, osdmFile: UploadFile = File(...)):
    require_login(request)
    user_email = request.session["user_email"]
    content = osdmFile.file.read()
    try:
        entry = osdm_editor.load_osdm(content)
    except Exception as e:
        logger.info("Kunne ikke laste OSDM i editor: %s", e)
        raise AppError(400, "invalid_osdm")
    entry["filename"] = _safe_filename(osdmFile.filename or "osdm.json")
    entry["created_at"] = time.time()
    EDIT_STORE[user_email] = entry
    log_event(user_email, "editor_load", detail={"filename": entry["filename"]})
    return osdm_editor.get_summary(entry, entry["filename"])


@router.get("/osdm-editor/summary")
def editor_summary(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    return osdm_editor.get_summary(entry, entry.get("filename", ""))


@router.post("/osdm-editor/metadata")
async def editor_metadata(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    body = await request.json()
    osdm_editor.update_delivery(entry, body)
    return {"ok": True}


@router.post("/osdm-editor/passenger/{pc_id}")
async def editor_passenger(request: Request, pc_id: str):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    body = await request.json()
    new_ratio = body.get("ratio")
    if new_ratio is None:
        raise AppError(422, "ratio_missing")
    try:
        new_ratio = float(new_ratio)
    except (TypeError, ValueError):
        raise AppError(422, "ratio_not_number")
    result = await run_in_threadpool(osdm_editor.update_passenger_ratio, entry, pc_id, new_ratio)
    if "error" in result:
        raise AppError(400, result["error"])
    log_event(user_email, "editor_passenger", detail=result)
    return result


@router.post("/osdm-editor/relation")
async def editor_relation(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    body = await request.json()
    try:
        from_uic = str(body["from_uic"]).strip()
        to_uic = str(body["to_uic"]).strip()
        adult_price_eur = float(body["adult_price_eur"])
        distance = int(body["distance"])
    except (KeyError, TypeError, ValueError):
        raise AppError(422, "invalid_input")
    if adult_price_eur <= 0 or distance < 0:
        raise AppError(422, "price_distance_invalid")
    result = await run_in_threadpool(osdm_editor.add_relation, entry, from_uic, to_uic, adult_price_eur, distance)
    log_event(user_email, "editor_relation", detail=result)
    return result


def _border_body(body: dict) -> tuple[str, list, dict]:
    sides = body.get("sides")
    names = body.get("names") or {}
    if not isinstance(sides, list) or not isinstance(names, dict):
        raise AppError(422, "invalid_input")
    return str(body.get("code", "")), sides, names


@router.get("/osdm-editor/border-points")
def editor_border_points(request: Request):
    require_login(request)
    entry = EDIT_STORE.get(request.session["user_email"])
    if not entry:
        raise AppError(404, "no_file_loaded")
    points = osdm_editor.list_border_points(entry)
    for bp in points:
        bp["warnings"] = [error_payload(w["code"], **w["params"]) for w in bp["warnings"]]
    return {"border_points": points}


@router.post("/osdm-editor/border-point")
async def editor_add_border_point(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    code, sides, names = _border_body(await request.json())
    try:
        result = osdm_editor.add_border_point(entry, code, sides, names)
    except osdm_editor.BorderPointError as e:
        raise AppError(400, e.code, **e.params)
    log_event(user_email, "editor_border_point", detail=result)
    return result


@router.post("/osdm-editor/border-point/{cp_id}")
async def editor_update_border_point(request: Request, cp_id: str):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    code, sides, names = _border_body(await request.json())
    try:
        result = osdm_editor.update_border_point(entry, cp_id, code, sides, names)
    except osdm_editor.BorderPointError as e:
        raise AppError(400 if e.code != "bp_not_found" else 404, e.code, **e.params)
    log_event(user_email, "editor_border_point", detail=result)
    return result


def _entry(request: Request) -> dict:
    require_login(request)
    entry = EDIT_STORE.get(request.session["user_email"])
    if not entry:
        raise AppError(404, "no_file_loaded")
    return entry


@router.get("/osdm-editor/passengers")
def editor_passengers(request: Request):
    entry = _entry(request)
    text_map = entry.get("text_map", {})
    passengers = osdm_editor.passenger_details(entry)
    for pc in passengers:
        pc["name"] = text_map.get(pc["nameRef"], "") or pc["id"]
        pc["ratio"] = round(entry["ratios"].get(pc["id"], 0.0), 4)
        pc["is_adult"] = pc["id"] == entry["adult_id"]
    fs = entry["data"]["fareDelivery"]["fareStructure"]
    return {
        "passengers": passengers,
        "warnings": [error_payload(w["code"], **w["params"]) for w in osdm_editor.passenger_warnings(entry)],
        "types": osdm_editor.PASSENGER_TYPES,
        "texts": [{"id": t.get("id", ""), "text": text_map.get(t.get("id", ""), "")} for t in fs.get("texts", [])],
    }


@router.post("/osdm-editor/passenger/{pc_id}/details")
async def editor_passenger_details(request: Request, pc_id: str):
    entry = _entry(request)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("also_ids", []), list) \
            or not isinstance(body.get("combinationConstraint", []), list):
        raise AppError(422, "invalid_input")
    try:
        result = osdm_editor.update_passenger(entry, pc_id, body, body.get("also_ids"))
    except osdm_editor.EditorError as e:
        raise AppError(404 if e.code == "pc_not_found" else 400, e.code, **e.params)
    log_event(request.session["user_email"], "editor_passenger_details", detail=result)
    return result


@router.get("/osdm-editor/texts")
def editor_texts(request: Request):
    return {"texts": osdm_editor.list_texts(_entry(request))}


@router.post("/osdm-editor/text/{text_id}")
async def editor_update_text(request: Request, text_id: str):
    entry = _entry(request)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("translations", []), list):
        raise AppError(422, "invalid_input")
    try:
        result = osdm_editor.update_text(entry, text_id, body)
    except osdm_editor.EditorError as e:
        raise AppError(404 if e.code == "text_not_found" else 400, e.code, **e.params)
    log_event(request.session["user_email"], "editor_text", detail=result)
    return result


@router.get("/osdm-editor/stations")
def editor_stations(request: Request, q: str = "", missing: bool = False):
    entry = _entry(request)
    return osdm_editor.list_station_names(entry, q[:100], missing)


@router.post("/osdm-editor/station/{uic}")
async def editor_update_station(request: Request, uic: str):
    entry = _entry(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise AppError(422, "invalid_input")
    try:
        result = osdm_editor.update_station_name(entry, uic, body.get("name", ""), body.get("shortName", ""))
    except osdm_editor.EditorError as e:
        raise AppError(400, e.code, **e.params)
    log_event(request.session["user_email"], "editor_station", detail=result)
    return result


@router.get("/osdm-editor/download")
def editor_download(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = EDIT_STORE.get(user_email)
    if not entry:
        raise AppError(404, "no_file_loaded")
    data = osdm_editor.serialize_osdm(entry)
    filename = entry.get("filename", "edited.json")
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/osdm-editor", response_class=HTMLResponse)
@router.head("/osdm-editor")
def osdm_editor_page(request: Request):
    return serve_page(request, "osdm-editor.html")
