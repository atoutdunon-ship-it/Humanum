"""Double authentification : TOTP (RFC 6238), secrets chiffrés au repos, codes de secours à usage unique.

- Secret TOTP de 160 bits, chiffré par Fernet (AES-128-CBC + HMAC-SHA256) ; rotation des clés via MultiFernet.
- Anti-rejeu : le pas de temps du dernier code accepté est mémorisé ; un code ne sert qu'une fois.
- Tolérance d'horloge : ±1 pas (30 s).
- Codes de secours : 10 codes de 50 bits, stockés sous forme d'empreintes HMAC-SHA256 (clé dérivée), supprimés à l'usage.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from functools import lru_cache

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

STEP = 30
_now = time.time  # point d'injection de l'horloge (tests)
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sans 0/O, 1/I/L : lisible à la main


@lru_cache
def _fernet() -> MultiFernet:
    keys = [k.strip() for k in get_settings().mfa_keys.get_secret_value().split(",") if k.strip()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def _pepper() -> bytes:
    first = get_settings().mfa_keys.get_secret_value().split(",")[0].strip().encode()
    return hashlib.sha256(b"hc-recovery-codes|" + first).digest()


def new_secret() -> str:
    return pyotp.random_base32(32)  # 32 caractères base32 = 160 bits


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:  # clé retirée ou donnée altérée
        raise ValueError("secret TOTP illisible") from e


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=get_settings().mfa_issuer)


def qr_svg(uri: str) -> str:
    """QR code SVG autonome (encre de la charte sur fond blanc), sans dépendance côté navigateur."""
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#101826", light="#ffffff", border=2)


def normalize(code: str) -> str:
    return "".join(c for c in code if c.isalnum()).upper()


def verify_totp(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Renvoie le pas de temps accepté, ou None. Refuse tout pas déjà utilisé (anti-rejeu)."""
    code = normalize(code)
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int((_now() if now is None else now) // STEP)
    ok = None
    for step in (current - 1, current, current + 1):  # parcours complet : temps constant
        if hmac.compare_digest(totp.at(step * STEP), code) and (last_step is None or step > last_step):
            ok = step
    return ok


def new_recovery_codes(n: int = 10) -> tuple[list[str], list[str]]:
    """(codes en clair à montrer UNE fois, empreintes à stocker)."""
    codes = ["".join(secrets.choice(_ALPHABET) for _ in range(10)) for _ in range(n)]
    shown = [f"{c[:5]}-{c[5:]}" for c in codes]
    return shown, [_digest(c) for c in codes]


def _digest(code: str) -> str:
    return hmac.new(_pepper(), normalize(code).encode(), hashlib.sha256).hexdigest()


def consume_recovery_code(code: str, digests: list[str]) -> list[str] | None:
    """Renvoie la liste restante si le code est valide (il en est retiré), sinon None."""
    d = _digest(code)
    match = [x for x in digests if hmac.compare_digest(x, d)]
    if not match:
        return None
    return [x for x in digests if x != match[0]]
