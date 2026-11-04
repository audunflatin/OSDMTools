"""Innlogging, passord (bytte, glemt, tilbakestill) og Min konto."""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from datetime import datetime, timezone, timedelta
import logging, uuid

from backend.core.settings import APP_URL
from backend.auth_db import SessionLocal, User, LoginLog, PasswordResetToken, EventLog
from backend.auth_utils import verify_password, hash_password, needs_rehash
from backend.email_utils import send_reset_link_email
from backend.core.security import _get_client_ip, _rate_limit_check, _rate_limit_reset, _rate_limit, _forgot_requests, require_login, log_event, _safe_next
from backend.core.pages import serve_page

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    if "user_email" in request.session:
        return RedirectResponse("/", status_code=302)
    html = Path("frontend/login.html").read_text(encoding="utf-8")
    safe = _safe_next(next)
    if safe:
        html = html.replace(
            '<button type="submit"',
            f'<input type="hidden" name="next" value="{safe}"><button type="submit"'
        )
    return HTMLResponse(html)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("")):
    ip = _get_client_ip(request)
    _rate_limit_check(ip)
    email = email.lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user or not user.is_active or not verify_password(password, user.password_hash):
            db.add(LoginLog(email=email, ip_address=ip, success=False))
            db.commit()
            log_event(email, "login_failed", "error", {"ip": ip})
            raise HTTPException(status_code=401, detail="Ugyldig innlogging")
        _rate_limit_reset(ip)
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)  # oppgrader til dagens antall runder
        request.session["user_email"] = user.email
        request.session["is_admin"] = user.is_admin
        db.add(LoginLog(email=user.email, ip_address=ip, success=True))
        log_event(user.email, "login_success", "ok", {"ip": ip})
        if user.must_change_password:
            db.commit()
            return RedirectResponse("/change-password", status_code=302)
        if user.first_login_at is None:
            user.first_login_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return RedirectResponse(_safe_next(next) or "/", status_code=302)

@router.get("/logout")
def logout(request: Request):
    email = request.session.get("user_email")
    request.session.clear()
    log_event(email, "logout")
    return RedirectResponse("/", status_code=302)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    if "user_email" not in request.session:
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(Path("frontend/change_password.html").read_text(encoding="utf-8"))

@router.post("/change-password")
def change_password(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
):
    if "user_email" not in request.session:
        raise HTTPException(status_code=401, detail="Ikke innlogget")
    if password != confirm:
        raise HTTPException(status_code=400, detail="Passordene er ikke like")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Passordet må være minst 8 tegn")
    email = request.session["user_email"]
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Bruker ikke funnet")
        user.password_hash = hash_password(password)
        user.must_change_password = False
        if user.first_login_at is None:
            user.first_login_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    log_event(email, "password_changed")
    return {"ok": True}


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page():
    return HTMLResponse(Path("frontend/forgot_password.html").read_text(encoding="utf-8"))

@router.post("/forgot-password")
def forgot_password(request: Request, email: str = Form(...)):
    _rate_limit(_forgot_requests, _get_client_ip(request), 3600, 5)
    email = email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email, User.is_active == True).first()
        if user:
            token = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            db.add(PasswordResetToken(token=token, email=email, expires_at=expires_at))
            db.commit()
            try:
                send_reset_link_email(email, f"{APP_URL}/reset-password/{token}")
            except Exception as exc:
                logger.error("Kunne ikke sende reset-lenke til %s: %s", email, exc)
    finally:
        db.close()
    return {"ok": True}

@router.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(token: str):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rt = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.expires_at > now,
        ).first()
        if not rt:
            return HTMLResponse("<h2>Lenken er ugyldig eller utløpt.</h2>", status_code=400)
    finally:
        db.close()
    html = Path("frontend/reset_password.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__TOKEN__", token))

@router.post("/reset-password/{token}")
def reset_password(token: str, password: str = Form(...), confirm: str = Form(...)):
    if password != confirm:
        raise HTTPException(status_code=400, detail="Passordene er ikke like")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Passordet må være minst 8 tegn")
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rt = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.expires_at > now,
        ).first()
        if not rt:
            raise HTTPException(status_code=400, detail="Lenken er ugyldig eller utløpt")
        user = db.query(User).filter(User.email == rt.email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Bruker ikke funnet")
        user.password_hash = hash_password(password)
        user.must_change_password = False
        db.delete(rt)
        db.commit()
    finally:
        db.close()
    log_event(rt.email, "password_changed", detail={"via": "reset_link"})
    return {"ok": True}


@router.get("/endre-passord")
def endre_passord_redirect():
    return RedirectResponse("/min-konto", status_code=301)

@router.get("/min-konto", response_class=HTMLResponse)
@router.head("/min-konto")
def min_konto_page(request: Request):
    return serve_page(request, "min-konto.html")


@router.get("/account")
def get_account(request: Request):
    require_login(request)
    user_email = request.session.get("user_email")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            raise HTTPException(status_code=404)
        return {
            "email": user.email,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "is_admin": user.is_admin,
        }

@router.post("/account/name")
def update_account_name(request: Request, first_name: str = Form(...), last_name: str = Form(...)):
    require_login(request)
    user_email = request.session.get("user_email")
    first_name = first_name.strip()
    last_name = last_name.strip()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            raise HTTPException(status_code=404)
        user.first_name = first_name
        user.last_name = last_name
        db.commit()
    log_event(user_email, "account_name_updated")
    return {"ok": True}

@router.delete("/account")
def delete_account(request: Request):
    require_login(request)
    user_email = request.session.get("user_email")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            raise HTTPException(status_code=404)
        if user.is_admin:
            raise HTTPException(status_code=403, detail="Admins kan ikke slette sin egen konto")
        db.query(EventLog).filter(EventLog.user_email == user_email).delete()
        db.query(LoginLog).filter(LoginLog.email == user_email).delete()
        db.query(PasswordResetToken).filter(PasswordResetToken.email == user_email).delete()
        db.delete(user)
        db.commit()
    log_event(None, "account_deleted", detail={"email": user_email})
    request.session.clear()
    return {"ok": True}
