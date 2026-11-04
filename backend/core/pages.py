"""Servering av HTML-sider med innloggingssjekk og injiserte JS-variabler."""
import json
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
    js_vars = {"IS_ADMIN": bool(request.session.get("is_admin")), **(extra or {})}
    script = "".join(f"window.{k} = {json.dumps(v)};" for k, v in js_vars.items())
    html = Path("frontend", filename).read_text(encoding="utf-8")
    return HTMLResponse(html.replace("</head>", f"<script>{script}</script></head>", 1))
