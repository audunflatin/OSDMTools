"""Prisregulering: skaler alle priser med fast prosent."""
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import logging
import time

import backend.osdm_editor as osdm_editor
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page
from backend.core.state import ADJUST_STORE
from backend.routers.excel import start_xlsx_job
from backend.routers.generate import stored_file_response
from backend.core.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/price-adjust", response_class=HTMLResponse)
@router.head("/price-adjust")
def price_adjust_page(request: Request):
    return serve_page(request, "price-adjust.html")


# Tunge endepunkter er bevisst `def`, ikke `async def`: FastAPI kjører `def` i en
# trådpool, mens `async def` kjører på hendelsesløkken og ville fryst hele serveren
# for alle brukere mens filen behandles.
@router.post("/price-adjust")
def price_adjust(
    request: Request,
    osdm_file: UploadFile = File(...),
    pct: float = Form(...),
    delivery_id: str = Form(...),
    previous_delivery_id: str = Form(""),
    environment: str = Form("prod"),
    optional_delivery: str = Form("false"),
    valid_from: str = Form(...),
    valid_to: str = Form(...),
):
    require_login(request)
    factor = 1 + pct / 100
    if factor <= 0:
        raise AppError(400, "invalid_percent")

    try:
        content = osdm_file.file.read()
    except Exception:
        raise AppError(400, "file_unreadable")

    # Samme prislogikk som generering: ny voksenpris per (RC, carrier, klasse),
    # øvrige kategorier fra eksakte ratioer, FIP o.l. proporsjonalt.
    try:
        store_entry = osdm_editor.load_osdm(content)
    except (ValueError, KeyError, TypeError):
        raise AppError(400, "invalid_osdm")
    if not store_entry["data"]["fareDelivery"]["fareStructure"].get("prices"):
        raise AppError(400, "missing_prices")

    before = osdm_editor.adult_prices_by_rc(store_entry)
    result = osdm_editor.scale_prices(store_entry, factor)
    changes = osdm_editor.price_change_summary(store_entry, before)
    utc_offset = osdm_editor.apply_delivery_fields(
        store_entry, delivery_id, previous_delivery_id, optional_delivery, environment, valid_from, valid_to,
    )
    result_text, fare_provider = osdm_editor.serialize_with_new_prefix(store_entry, delivery_id)

    env_suffix = "test" if environment == "test" else "prod"
    filename = _safe_filename(f"{fare_provider}_{delivery_id}_{env_suffix}.json")
    user_email = request.session["user_email"]
    ADJUST_STORE[user_email] = {"filename": filename, "content": result_text, "created_at": time.time()}

    log_event(
        user_email, "price_adjust",
        detail={"factor": factor, "filename": osdm_file.filename, "fares_updated": result["updated_fares"],
                "delivery_id": delivery_id},
    )

    # Samme svarformat som generering, så frontend kan vise resultatet likt
    return {
        "ok": True,
        "outputFile": filename,
        "summary": {
            "environment": environment,
            "utcOffset": utc_offset,
            "factor": factor,
            "faresUpdated": result["updated_fares"],
            "rcsTotal": changes["rcsTotal"],
            "rcsChanged": changes["rcsChanged"],
            "priceExamples": changes["examples"],
        },
    }


@router.get("/price-adjust/download/{filename}")
def price_adjust_download(filename: str, request: Request):
    require_login(request)
    return stored_file_response(ADJUST_STORE.get(request.session["user_email"]), filename)


@router.post("/price-adjust/excel")
def price_adjust_excel(request: Request):
    require_login(request)
    user_email = request.session["user_email"]
    entry = ADJUST_STORE.get(user_email)
    if not entry:
        raise AppError(400, "no_result")
    return {"jobId": start_xlsx_job(entry["content"].encode("utf-8"), user_email)}
