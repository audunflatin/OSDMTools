"""OSDM-editor: passasjerratioer, metadata og nye relasjoner."""
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
import logging, time

import backend.osdm_editor as osdm_editor
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import EDIT_STORE
from backend.core.errors import AppError

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
