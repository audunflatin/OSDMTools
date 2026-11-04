"""Tilgangskontroll, rate limiting, logging og små sikkerhetshjelpere."""
from fastapi import Request
from collections import defaultdict
import json, re, time

from backend.auth_db import SessionLocal, User, EventLog
from backend.core.errors import AppError


_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW = 60   # sekunder
_LOGIN_MAX    = 10   # maks forsøk per vindu

def _get_client_ip(request: Request) -> str:
    """Hent reell klient-IP — foretrekker Cloudflare-header."""
    return (
        request.headers.get("CF-Connecting-IP")
        or (request.client.host if request.client else "unknown")
    )

def _rate_limit_check(ip: str):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_MAX:
        raise AppError(429, "login_rate_limited")
    _login_attempts[ip].append(now)

def _rate_limit_reset(ip: str):
    _login_attempts.pop(ip, None)

def _rate_limit(store: dict[str, list[float]], key: str, window: int, max_count: int) -> None:
    """Glidende vindu: maks `max_count` hendelser per `window` sekunder per nøkkel."""
    now = time.time()
    store[key] = [t for t in store[key] if now - t < window]
    if len(store[key]) >= max_count:
        raise AppError(429, "rate_limited")
    store[key].append(now)


_access_requests: dict[str, list[float]] = defaultdict(list)   # 3 per IP per døgn
_forgot_requests: dict[str, list[float]] = defaultdict(list)   # 5 per IP per time
_contact_sent:    dict[str, list[float]] = defaultdict(list)   # 5 per bruker per time
_CONTACT_MAX = 5


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

def _valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(_EMAIL_RE.match(email))


def _safe_filename(name: str) -> str:
    """Fjern tegn som kan ødelegge Content-Disposition-headere."""
    return "".join(c for c in name if c not in '"\\/:*?<>|\r\n').strip() or "osdm.json"


def require_login(request: Request):
    if "user_email" not in request.session:
        raise AppError(401, "not_logged_in")

def _check_is_admin_from_db(request: Request) -> bool:
    """Les is_admin direkte fra DB og oppdater sesjonen. Returnerer True/False."""
    user_email = request.session.get("user_email")
    if not user_email:
        return False
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == user_email).first()
        is_admin = bool(user and user.is_admin)
    request.session["is_admin"] = is_admin
    return is_admin

def require_admin(request: Request):
    require_login(request)
    if not _check_is_admin_from_db(request):
        raise AppError(403, "not_admin")


def log_event(
    user_email: str | None,
    event_type: str,
    status: str = "ok",
    detail: dict | None = None,
):
    try:
        db = SessionLocal()
        try:
            db.add(EventLog(
                user_email=user_email,
                event_type=event_type,
                status=status,
                detail=json.dumps(detail, ensure_ascii=False) if detail else None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # Logging skal aldri bryte en forespørsel


def _safe_next(next_url: str) -> str:
    """Validate next URL to prevent open redirect. Returns safe URL or empty string."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//") and next_url not in ("/login", "/logout"):
        return next_url
    return ""
