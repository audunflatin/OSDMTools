"""Servering av HTML-sider med innloggingssjekk og injiserte JS-variabler."""
import json
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from backend.core.security import _check_is_admin_from_db


def serve_page(request: Request, filename: str, *, admin: bool = False, extra: dict | None = None) -> Response:
    """Server `frontend/<filename>` for innloggede brukere.

    Uinnlogget → /login?next=<sti>. Med admin=True → ikke-admin sendes til /.
    Setter alltid `window.IS_ADMIN`, pluss eventuelle `extra`-variabler (JSON-kodet).
    """
    if "user_email" not in request.session:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=302)
    if admin and not _check_is_admin_from_db(request):
        return RedirectResponse("/", status_code=302)
    is_admin = bool(request.session.get("is_admin"))
    js_vars = {"IS_ADMIN": is_admin, **(extra or {})}
    script = "".join(f"window.{k} = {json.dumps(v)};" for k, v in js_vars.items())
    html = Path("frontend", filename).read_text(encoding="utf-8")
    html = html.replace("<!-- SIDEBAR -->", render_sidebar(request.url.path, is_admin), 1)
    html = html.replace("<!-- APP_HEADER -->", Path("frontend/partials/header.html").read_text(encoding="utf-8"), 1)
    return HTMLResponse(html.replace("</head>", f"<script>{script}</script></head>", 1))


def render_sidebar(path: str, is_admin: bool) -> str:
    """Felles sidebar med aktiv lenke for `path`; admin-blokken bare for admins."""
    html = Path("frontend/partials/sidebar.html").read_text(encoding="utf-8")
    if not is_admin:
        html = re.sub(r"\s*<!-- ADMIN -->.*?<!-- /ADMIN -->", "", html, flags=re.S)
    # Undermeny-lenke (admin) vinner over hovedlenke med samme href
    for cls in ("sidebar-sublink", "sidebar-link"):
        marked = html.replace(f'href="{path}" class="{cls}"', f'href="{path}" class="{cls} active"', 1)
        if marked != html:
            return marked
    return html
