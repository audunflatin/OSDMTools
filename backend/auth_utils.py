"""Passordhashing med PBKDF2-SHA256 i passlib-formatet.

Format: `$pbkdf2-sha256$<runder>$<salt>$<hash>`, der salt og hash er base64 med
«.» i stedet for «+» og uten «=» (passlibs «ab64»). Hasher laget av passlib
(brukt fram til november 2026) verifiseres uendret.
"""
import base64
import hashlib
import hmac
import secrets
import string

_PREFIX = "$pbkdf2-sha256$"
ROUNDS = 600_000  # OWASP-anbefaling for PBKDF2-SHA256 (2023+). Eldre hasher har 29 000.

_CHARS = string.ascii_letters + string.digits


def _ab64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode().rstrip("=").replace("+", ".")


def _ab64_decode(text: str) -> bytes:
    text = text.replace(".", "+")
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _parse(hashed: str) -> tuple[int, bytes, bytes] | None:
    if not hashed or not hashed.startswith(_PREFIX):
        return None
    try:
        rounds, salt, checksum = hashed[len(_PREFIX):].split("$")
        return int(rounds), _ab64_decode(salt), _ab64_decode(checksum)
    except (ValueError, TypeError):
        return None


def generate_password() -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(10))


def hash_password(password: str, rounds: int = ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    checksum = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=32)
    return f"{_PREFIX}{rounds}${_ab64_encode(salt)}${_ab64_encode(checksum)}"


def verify_password(password: str, hashed: str) -> bool:
    parsed = _parse(hashed)
    if parsed is None:
        return False
    rounds, salt, checksum = parsed
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=len(checksum))
    return hmac.compare_digest(candidate, checksum)


def needs_rehash(hashed: str) -> bool:
    """True hvis hashen bruker færre runder enn dagens standard (oppgraderes ved innlogging)."""
    parsed = _parse(hashed)
    return parsed is not None and parsed[0] < ROUNDS
