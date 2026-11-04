import html
import logging
import requests
from backend.core.settings import RESEND_API_KEY, SENDER_EMAIL, APP_URL, CONTACT_EMAIL

logger = logging.getLogger(__name__)

_STYLE = {
    "body":    "margin:0;padding:0;background:#0d1b2a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;",
    "wrap":    "background:#0d1b2a;padding:40px 20px;",
    "card":    "background:#152535;border-radius:8px;border-top:3px solid #ff5959;overflow:hidden;width:560px;max-width:100%;",
    "header":  "padding:28px 40px 24px;border-bottom:1px solid rgba(255,255,255,0.1);",
    "h1":      "margin:0;font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-0.02em;",
    "body_td": "padding:32px 40px;",
    "p":       "margin:0 0 16px;font-size:15px;line-height:1.6;color:#ffffff;",
    "label":   "font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:rgba(255,255,255,0.5);margin-bottom:4px;",
    "cred":    "background:rgba(255,255,255,0.07);border-radius:4px;border:1px solid rgba(255,255,255,0.12);padding:12px 16px;margin-bottom:16px;",
    "cred_val":"font-size:15px;font-weight:600;color:#ffffff;font-family:monospace;",
    "btn_td":  "padding-top:8px;",
    "btn":     "display:inline-block;background:#ff5959;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:12px 28px;border-radius:4px;",
    "footer":  "padding:20px 40px;border-top:1px solid rgba(255,255,255,0.1);text-align:center;",
}


def _email_html(heading: str, body_rows: str) -> str:
    s = _STYLE
    return f"""<!DOCTYPE html>
<html lang="en" xmlns:v="urn:schemas-microsoft-com:vml">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="format-detection" content="telephone=no,date=no,address=no,email=no,url=no">
  <meta name="color-scheme" content="only light">
  <meta name="supported-color-schemes" content="only light">
  <style>
    :root {{ color-scheme: only light; }}
    body, .email-wrap {{ background-color: #0d1b2a !important; }}
    .email-card {{ background-color: #152535 !important; border-top: 3px solid #ff5959 !important; }}
    h1, p, .cred-val {{ color: #ffffff !important; }}
    .cred-label {{ color: rgba(255,255,255,0.5) !important; }}
    .email-footer span {{ color: rgba(255,255,255,0.3) !important; }}
    @media (prefers-color-scheme: dark) {{
      body, .email-wrap {{ background-color: #0d1b2a !important; }}
      .email-card {{ background-color: #152535 !important; }}
      h1, p, .cred-val {{ color: #ffffff !important; }}
      .cred-label {{ color: rgba(255,255,255,0.5) !important; }}
    }}
  </style>
</head>
<body style="{s['body']}" class="email-wrap">
<table width="100%" cellpadding="0" cellspacing="0" style="{s['wrap']}" class="email-wrap">
  <tr><td align="center">
    <table cellpadding="0" cellspacing="0" style="{s['card']}" class="email-card" data-ogsc="background: #152535;">

      <tr><td style="{s['header']}">
        <h1 style="{s['h1']}">{heading}</h1>
      </td></tr>

      <tr><td style="{s['body_td']}">
        {body_rows}
      </td></tr>

      <tr><td style="{s['footer']}" class="email-footer">
        <span style="color:#ff5959;font-size:20px;line-height:1;">&#9679;</span><span style="color:rgba(255,255,255,0.3);font-size:12px;letter-spacing:0px;">&middot;&middot;</span><span style="color:rgba(255,255,255,0.8);font-size:18px;line-height:1;">&#9675;</span><span style="font-size:22px;font-weight:800;color:#ff5959;padding-left:10px;">OSDM</span><span style="font-size:22px;font-weight:800;color:#ffffff;">Tools</span>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def _esc(value: str) -> str:
    """Escape brukerinput før den settes inn i HTML-e-post."""
    return html.escape(value or "", quote=True)


def _subject_safe(value: str) -> str:
    """Fjern linjeskift fra verdier som havner i emnefeltet."""
    return " ".join((value or "").split())[:100]


def _cred_row(label: str, value: str) -> str:
    s = _STYLE
    return f"""
    <table cellpadding="0" cellspacing="0" width="100%" style="{s['cred']}">
      <tr><td><p style="{s['label']}">{label}</p><p style="{s['cred_val']}"><span style="color:#ffffff;text-decoration:none;">{value}</span></p></td></tr>
    </table>"""


def _send(to: str | list[str], subject: str, html: str) -> None:
    recipients = [to] if isinstance(to, str) else to
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY er ikke satt – e-post ikke sendt til %s", recipients)
        return
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"from": SENDER_EMAIL, "to": recipients, "subject": subject, "html": html},
        timeout=10,
    )
    resp.raise_for_status()


def send_welcome_email(to: str, password: str) -> None:
    s = _STYLE
    email_link = f'<a href="mailto:{_esc(to)}" style="color:#ffffff;text-decoration:none;">{_esc(to)}</a>'
    body = f"""
    <p style="{s['p']}">You have been granted access to OSDMTools.</p>
    {_cred_row("Email", email_link)}
    {_cred_row("Temporary password", password)}
    <p style="{s['p']}">You will be asked to choose a new password on your first login.</p>
    <table cellpadding="0" cellspacing="0"><tr><td style="{s['btn_td']}">
      <a href="{APP_URL}/login" style="{s['btn']}">Log in</a>
    </td></tr></table>"""
    _send(to=to, subject="Welcome to OSDMTools", html=_email_html('Welcome to <span style="color:#ff5959;">OSDM</span>Tools', body))


def send_contact_email(name: str, from_email: str, message: str) -> None:
    s = _STYLE
    body = f"""
    <p style="{s['p']}">New message from the contact form on osdmtools.com.</p>
    {_cred_row("Name", _esc(name))}
    {_cred_row("Email", _esc(from_email))}
    <table cellpadding="0" cellspacing="0" width="100%" style="{s['cred']}">
      <tr><td><p style="{s['label']}">Message</p><p style="font-size:15px;color:#ffffff;white-space:pre-wrap;">{_esc(message)}</p></td></tr>
    </table>"""
    _send(
        to=CONTACT_EMAIL,
        subject=f"OSDMTools - melding fra {_subject_safe(name)}",
        html=_email_html('Ny melding fra <span style="color:#ff5959;">kontaktskjema</span>', body),
    )


def send_reset_link_email(to: str, reset_url: str) -> None:
    s = _STYLE
    body = f"""
    <p style="{s['p']}">We received a request to reset your OSDMTools password.</p>
    <p style="{s['p']}">Click the button below to choose a new password. The link expires in <strong style="color:#ffffff;">1 hour</strong>.</p>
    <table cellpadding="0" cellspacing="0"><tr><td style="{s['btn_td']}">
      <a href="{reset_url}" style="{s['btn']}">Reset password</a>
    </td></tr></table>
    <p style="margin-top:24px;font-size:13px;color:rgba(255,255,255,0.45);">If you did not request this, you can safely ignore this email.</p>"""
    _send(to=to, subject="Reset your OSDMTools password", html=_email_html("Reset password", body))


def send_access_request_email(name: str, from_email: str, org: str, recipients: list[str] | None = None) -> None:
    s = _STYLE
    body = f"""
    <p style="{s['p']}">New access request from the landing page on osdmtools.com.</p>
    {_cred_row("Name", _esc(name))}
    {_cred_row("Email", _esc(from_email))}
    {_cred_row("Railway operator", _esc(org))}
    <table cellpadding="0" cellspacing="0"><tr><td style="{s['btn_td']}">
      <a href="{APP_URL}/admin/users" style="{s['btn']}">Manage users</a>
    </td></tr></table>"""
    _send(
        to=recipients or [CONTACT_EMAIL],
        subject=f"OSDMTools - tilgangsforespørsel fra {_subject_safe(name)}",
        html=_email_html('Tilgangsforespørsel fra <span style="color:#ff5959;">OSDMTools</span>', body),
    )
