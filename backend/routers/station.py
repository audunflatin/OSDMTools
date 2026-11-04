"""Stasjonssøk (Wikidata) – kun sideservering; søket skjer i frontend."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import logging


from backend.core.pages import serve_page

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stasjonssok", response_class=HTMLResponse)
@router.head("/stasjonssok")
def stasjonssok_page(request: Request):
    return serve_page(request, "station-lookup.html")
