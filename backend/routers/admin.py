"""Administrasjon: brukere, tilgangsforespørsler, innloggings- og aktivitetslogg (kun admin)."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime, timedelta
import json, logging

from backend.auth_db import SessionLocal, User, LoginLog, EventLog, AccessRequest
from backend.auth_utils import generate_password, hash_password
from backend.email_utils import send_welcome_email
from backend.core.security import _valid_email, _check_is_admin_from_db, require_admin, log_event
from backend.core.pages import serve_page
from backend.core.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/list-users")
def list_users(request: Request):
    require_admin(request)
    db = SessionLocal()
    try:
        users = db.query(User).all()
        return [
            {
                "email": u.email,
                "first_name": u.first_name or "",
                "last_name": u.last_name or "",
                "is_admin": u.is_admin,
                "is_active": u.is_active,
                "has_logged_in": u.first_login_at is not None,
                "first_login_at": u.first_login_at.isoformat() if u.first_login_at else None,
                "must_change_password": bool(u.must_change_password),
            }
            for u in users
        ]
    finally:
        db.close()

@router.post("/admin/add-user")
def admin_add_user(
    request: Request,
    email: str = Form(...),
    is_admin: str = Form("false"),
    first_name: str = Form(""),
    last_name: str = Form(""),
):
    require_admin(request)
    email = email.strip().lower()
    if not _valid_email(email):
        raise AppError(400, "invalid_email")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            raise AppError(400, "user_exists")
        password = generate_password()
        db.add(User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            is_admin=(is_admin.lower() == "true"),
            is_active=True,
            must_change_password=True,
        ))
        db.commit()
    finally:
        db.close()
    log_event(request.session.get("user_email"), "admin_user_created", detail={
        "email": email, "is_admin": is_admin.lower() == "true",
    })
    try:
        send_welcome_email(email, password)
        return {"ok": True, "email": email, "email_sent": True}
    except Exception as exc:
        logger.error("Kunne ikke sende velkomst-e-post til %s: %s", email, exc)
        return {"ok": True, "email": email, "email_sent": False}

@router.post("/admin/set-admin")
def admin_set_admin(request: Request, email: str = Form(...), is_admin: str = Form(...)):
    require_admin(request)
    email = email.lower()
    if email == request.session.get("user_email"):
        raise AppError(400, "cannot_change_own_admin")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise AppError(404, "user_not_found")
        user.is_admin = (is_admin.lower() == "true")
        db.commit()
        log_event(request.session.get("user_email"), "admin_set_admin", detail={
            "email": email, "is_admin": user.is_admin,
        })
        return {"ok": True, "email": email, "is_admin": user.is_admin}
    finally:
        db.close()

@router.post("/admin/delete-user")
def delete_user(request: Request, email: str = Form(...)):
    require_admin(request)
    email = email.lower()
    if email == request.session.get("user_email"):
        raise AppError(400, "cannot_delete_self")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise AppError(404, "user_not_found")
        db.delete(user)
        db.commit()
        log_event(request.session.get("user_email"), "admin_user_deleted", detail={"email": email})
        return {"ok": True, "deleted": email}
    finally:
        db.close()

@router.get("/admin/pending-requests")
def admin_pending_requests(request: Request):
    require_admin(request)
    with SessionLocal() as db:
        rows = (
            db.query(AccessRequest)
            .filter(AccessRequest.status == "pending")
            .order_by(AccessRequest.requested_at.asc())
            .all()
        )
        return [
            {
                "email":        r.email,
                "name":         r.name,
                "org":          r.org,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            }
            for r in rows
        ]


@router.post("/admin/approve-request")
def admin_approve_request(request: Request, email: str = Form(...)):
    require_admin(request)
    email = email.lower()
    with SessionLocal() as db:
        req = db.query(AccessRequest).filter(AccessRequest.email == email).first()
        if not req or req.status != "pending":
            raise AppError(404, "request_not_found")
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            req.status = "approved"
            db.commit()
            return {"ok": True, "email": email, "already_existed": True}
        password = generate_password()
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_admin=False,
            is_active=True,
            must_change_password=True,
            first_name=req.first_name or "",
            last_name=req.last_name or "",
        )
        db.add(user)
        req.status = "approved"
        db.commit()
    email_sent = True
    try:
        send_welcome_email(email, password)
    except Exception as exc:
        logger.warning("Kunne ikke sende velkomstepost: %s", exc)
        email_sent = False
    log_event(request.session.get("user_email"), "admin_request_approved", detail={"email": email})
    return {"ok": True, "email": email, "email_sent": email_sent}


@router.post("/admin/reject-request")
def admin_reject_request(request: Request, email: str = Form(...)):
    require_admin(request)
    email = email.lower()
    with SessionLocal() as db:
        req = db.query(AccessRequest).filter(AccessRequest.email == email).first()
        if not req:
            raise AppError(404, "request_not_found")
        req.status = "rejected"
        db.commit()
    log_event(request.session.get("user_email"), "admin_request_rejected", detail={"email": email})
    return {"ok": True, "email": email}


@router.get("/admin/login-log")
def admin_login_log(
    request: Request,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 25,
):
    require_admin(request)
    db = SessionLocal()
    try:
        q = db.query(LoginLog)
        if search:
            q = q.filter(LoginLog.email.ilike(f"%{search}%"))
        if date_from:
            q = q.filter(LoginLog.logged_at >= date_from)
        if date_to:
            dt_to = datetime.fromisoformat(date_to) + timedelta(days=1)
            q = q.filter(LoginLog.logged_at < dt_to)
        total = q.count()
        entries = (
            q.order_by(LoginLog.logged_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "entries": [
                {
                    "email": e.email,
                    "logged_at": e.logged_at.isoformat() if e.logged_at else None,
                    "ip_address": e.ip_address or "—",
                    "success": bool(e.success) if e.success is not None else True,
                }
                for e in entries
            ],
        }
    finally:
        db.close()


@router.get("/admin", response_class=HTMLResponse)
def admin_redirect(request: Request):
    if "user_email" not in request.session:
        return RedirectResponse("/login?next=/admin/users", status_code=302)
    if not _check_is_admin_from_db(request):
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/admin/users", status_code=302)

@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request):
    return serve_page(request, "admin.html", admin=True)

@router.get("/admin/log", response_class=HTMLResponse)
def admin_log_page(request: Request):
    return serve_page(request, "admin-log.html", admin=True)

@router.get("/admin/presentation", response_class=HTMLResponse)
def admin_presentation_page(request: Request):
    return serve_page(request, "presentation.html", admin=True)

@router.get("/admin/event-log")
def admin_event_log(
    request: Request,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    event_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
):
    require_admin(request)
    db = SessionLocal()
    try:
        q = db.query(EventLog)
        if search:
            q = q.filter(EventLog.user_email.ilike(f"%{search}%"))
        if date_from:
            q = q.filter(EventLog.logged_at >= date_from)
        if date_to:
            dt_to = datetime.fromisoformat(date_to) + timedelta(days=1)
            q = q.filter(EventLog.logged_at < dt_to)
        if event_type:
            q = q.filter(EventLog.event_type == event_type)
        if status:
            q = q.filter(EventLog.status == status)
        total = q.count()
        entries = (
            q.order_by(EventLog.logged_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "entries": [
                {
                    "id": e.id,
                    "logged_at": e.logged_at.isoformat() if e.logged_at else None,
                    "user_email": e.user_email or "—",
                    "event_type": e.event_type,
                    "status": e.status or "ok",
                    "detail": json.loads(e.detail) if e.detail else {},
                }
                for e in entries
            ],
        }
    finally:
        db.close()
