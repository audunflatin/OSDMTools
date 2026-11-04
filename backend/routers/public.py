"""Offentlige sider og skjemaer: landingsside, personvern, robots/sitemap, kontakt og tilgangsforespørsel."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, Response
from pathlib import Path
from datetime import datetime, timezone
import logging

from backend.auth_db import SessionLocal, User, AccessRequest
from backend.email_utils import send_contact_email, send_access_request_email
from backend.core.security import _get_client_ip, _rate_limit, _access_requests, _contact_sent, _CONTACT_MAX, _valid_email, require_login, log_event

from backend.core.pages import serve_page
from backend.core.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    if "user_email" not in request.session:
        return HTMLResponse(Path("frontend/landing.html").read_text(encoding="utf-8"))
    return serve_page(request, "index.html")


@router.get("/personvern", response_class=HTMLResponse)
def personvern_page():
    return HTMLResponse(Path("frontend/personvern.html").read_text(encoding="utf-8"))

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/robots.txt", response_class=Response)
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /login\n"
        "Allow: /personvern\n"
        "Disallow: /admin/\n"
        "Disallow: /ui/\n"
        "Disallow: /logout\n"
        "Disallow: /endre-passord\n"
        "Disallow: /min-konto\n"
        "Disallow: /account\n"
        "Disallow: /price-adjust\n"
        "Disallow: /osdmtoexcel\n"
        "Disallow: /fare-discount\n"
        "Disallow: /fix-osdm\n"
        "Disallow: /osdm-editor\n"
        "Disallow: /stasjonssok\n"
        "Disallow: /kontakt\n"
        "Disallow: /request-access\n"
        "Disallow: /change-password\n"
        "Disallow: /forgot-password\n"
        "Disallow: /reset-password/\n"
        "\n"
        "Sitemap: https://osdmtools.com/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")

@router.get("/sitemap.xml", response_class=Response)
def sitemap_xml():
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        '    <loc>https://osdmtools.com/</loc>\n'
        '    <changefreq>monthly</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '</urlset>\n'
    )
    return Response(content=content, media_type="application/xml")


@router.get("/kontakt", response_class=HTMLResponse)
@router.head("/kontakt")
def kontakt_page(request: Request):
    user_email = request.session.get("user_email", "")
    user_name = ""
    if user_email:
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == user_email).first()
            if user:
                user_name = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
    return serve_page(request, "contact.html", extra={"USER_EMAIL": user_email, "USER_NAME": user_name})

@router.post("/contact")
def contact(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    message: str = Form(...),
):
    require_login(request)
    user_email = request.session["user_email"]
    name, email, message = name.strip()[:200], email.strip()[:254], message.strip()[:5000]
    if not message:
        raise AppError(400, "message_empty")
    _rate_limit(_contact_sent, user_email, 3600, _CONTACT_MAX)
    try:
        send_contact_email(name, email, message)
        log_event(email, "contact_sent", detail={"name": name})
        return {"ok": True}
    except Exception as exc:
        logger.error("Kunne ikke sende kontakt-e-post: %s", exc)
        log_event(email, "contact_sent", "error", {"name": name})
        return {"ok": False}

@router.post("/request-access")
def request_access(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(...),
    org: str = Form(...),
    website: str = Form(""),
):
    if website:
        return {"ok": True}
    ip = _get_client_ip(request)
    _rate_limit(_access_requests, ip, 86400, 3)
    email_clean      = email.strip().lower()
    if not _valid_email(email_clean):
        raise AppError(400, "invalid_email")
    first_name_clean = first_name.strip()[:100]
    last_name_clean  = last_name.strip()[:100]
    name_clean       = f"{first_name_clean} {last_name_clean}".strip()
    org_clean        = org.strip()[:200]
    with SessionLocal() as db:
        existing_user = db.query(User).filter(User.email == email_clean).first()
        if existing_user:
            return {"ok": True}
        existing_req = db.query(AccessRequest).filter(AccessRequest.email == email_clean).first()
        if existing_req:
            existing_req.name       = name_clean
            existing_req.first_name = first_name_clean
            existing_req.last_name  = last_name_clean
            existing_req.org        = org_clean
            existing_req.status     = "pending"
            existing_req.requested_at = datetime.now(timezone.utc)
        else:
            db.add(AccessRequest(
                email=email_clean, name=name_clean,
                first_name=first_name_clean, last_name=last_name_clean,
                org=org_clean,
            ))
        db.commit()
    with SessionLocal() as db:
        admin_emails = [u.email for u in db.query(User).filter(User.is_admin == True, User.is_active == True).all()]
    try:
        send_access_request_email(name_clean, email_clean, org_clean, recipients=admin_emails or None)
    except Exception as exc:
        logger.warning("Kunne ikke sende tilgangsforespørsel-epost: %s", exc)
    log_event(None, "access_request", detail={"name": name_clean, "email": email_clean, "org": org_clean})
    return {"ok": True}
