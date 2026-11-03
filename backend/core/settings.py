import os

SESSION_SECRET = os.getenv("SESSION_SECRET", "CHANGE_ME_BEFORE_PROD")

APP_URL = os.getenv("APP_URL", "https://osdmtools.com")
_raw_origins = os.getenv("ALLOWED_ORIGINS", f"{APP_URL},http://localhost:8000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDER_EMAIL   = os.getenv("SENDER_EMAIL", "noreply@osdmtools.com")
APP_URL        = os.getenv("APP_URL", "https://osdmtools.com")  # kept for email links
CONTACT_EMAIL  = os.getenv("CONTACT_EMAIL", "kontakt@osdmtools.com")
