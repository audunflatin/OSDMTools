"""Prisregulering: skaler alle priser med fast prosent."""
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
import logging

import backend.osdm_editor as osdm_editor
from backend.core.security import _safe_filename, require_login, log_event
from backend.core.pages import serve_page

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
        raise HTTPException(status_code=400, detail="Ugyldig prosent")

    try:
        content = osdm_file.file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Filen kunne ikke leses")

    # Samme prislogikk som generering: ny voksenpris per (RC, carrier, klasse),
    # øvrige kategorier fra eksakte ratioer, FIP o.l. proporsjonalt.
    try:
        store_entry = osdm_editor.load_osdm(content)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Filen er ikke en gyldig OSDM fareDelivery")
    if not store_entry["data"]["fareDelivery"]["fareStructure"].get("prices"):
        raise HTTPException(status_code=400, detail="Mangler felt i OSDM-strukturen: 'prices'")

    result = osdm_editor.scale_prices(store_entry, factor)
    osdm_editor.apply_delivery_fields(
        store_entry, delivery_id, previous_delivery_id, optional_delivery, environment, valid_from, valid_to,
    )
    result_text, fare_provider = osdm_editor.serialize_with_new_prefix(store_entry, delivery_id)

    env_suffix = "test" if environment == "test" else "prod"
    filename = _safe_filename(f"{fare_provider}_{delivery_id}_{env_suffix}.json")

    log_event(
        request.session.get("user_email"), "price_adjust",
        detail={"factor": factor, "filename": osdm_file.filename, "fares_updated": result["updated_fares"],
                "delivery_id": delivery_id},
    )

    return Response(
        content=result_text.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
