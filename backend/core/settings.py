import os

SESSION_SECRET = os.getenv("SESSION_SECRET", "CHANGE_ME_BEFORE_PROD")
# Secure-flagg på session-cookien. Railway setter RAILWAY_ENVIRONMENT_NAME automatisk;
# lokalt (http://localhost) må flagget være av, ellers sendes cookien ikke.
_ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT"))
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "true" if _ON_RAILWAY else "false").lower() == "true"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(7 * 24 * 3600)))  # 7 dager

APP_URL = os.getenv("APP_URL", "https://osdmtools.com")
_raw_origins = os.getenv("ALLOWED_ORIGINS", f"{APP_URL},http://localhost:8000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDER_EMAIL   = os.getenv("SENDER_EMAIL", "noreply@osdmtools.com")
APP_URL        = os.getenv("APP_URL", "https://osdmtools.com")  # kept for email links
CONTACT_EMAIL  = os.getenv("CONTACT_EMAIL", "kontakt@osdmtools.com")
