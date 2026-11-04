"""OSDMTools – oppsett av FastAPI-appen.

All funksjonalitet ligger i `backend/routers/` (én fil per tjeneste); felles hjelpere
i `backend/core/`. Ny tjeneste: lag en router-fil og legg den til i `include_router`-lista nederst.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import Scope

from backend.auth_db import SessionLocal, User, init_db
from backend.core.settings import SESSION_SECRET, ALLOWED_ORIGINS, SESSION_HTTPS_ONLY, SESSION_MAX_AGE
from backend.routers import (
    admin, auth, editor, excel, fare_discount, fix_osdm, generate, price_adjust, public, station,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if SESSION_SECRET == "CHANGE_ME_BEFORE_PROD":
        logger.warning("⚠️  SESSION_SECRET er ikke satt – sesjoner er ikke sikre!")
    yield


app = FastAPI(title="OSDMTools", lifespan=lifespan)


class _NoHTMLStaticFiles(StaticFiles):
    """Block direct access to .html files — they must go through Python routes."""
    async def get_response(self, path: str, scope: Scope) -> Response:
        if path.endswith(".html"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


app.mount("/static", _NoHTMLStaticFiles(directory="frontend"), name="static")


# Stier som er tilgjengelige mens brukeren må bytte passord.
_PASSWORD_CHANGE_ALLOWED = ("/change-password", "/logout", "/login", "/static/", "/health", "/personvern")


@app.middleware("http")
async def validate_session_user(request: Request, call_next):
    """Sjekk sesjonens bruker mot databasen ved hver forespørsel.

    Cookien alene er ikke nok: en slettet eller deaktivert bruker skal miste
    tilgangen med en gang, og tvunget passordbytte skal ikke kunne hoppes over.
    Registreres før SessionMiddleware, slik at den kjører innenfor den.
    """
    email = request.session.get("user_email") if not request.url.path.startswith("/static/") else None
    if email:
        user = await run_in_threadpool(_load_session_user, email)
        if user is None or not user["is_active"]:
            request.session.clear()
        else:
            request.session["is_admin"] = user["is_admin"]
            if user["must_change_password"] and not request.url.path.startswith(_PASSWORD_CHANGE_ALLOWED):
                if request.method in ("GET", "HEAD"):
                    return RedirectResponse("/change-password", status_code=302)
                return JSONResponse({"detail": "must_change_password"}, status_code=403)
    return await call_next(request)


def _load_session_user(email: str) -> dict | None:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return None
        return {
            "is_active": bool(user.is_active),
            "is_admin": bool(user.is_admin),
            "must_change_password": bool(user.must_change_password),
        }


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="strict",
    https_only=SESSION_HTTPS_ONLY,
    max_age=SESSION_MAX_AGE,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ---------------------------------------------------------------------
# Startup

for module in (public, auth, admin, generate, excel, price_adjust, fare_discount, fix_osdm, editor, station):
    app.include_router(module.router)
