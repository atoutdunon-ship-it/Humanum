"""Cycle de vie de la double authentification, commun aux comptes des organismes et aux super-administrateurs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, status

from app.core import mfa
from app.models import MfaMixin

PENDING_TTL = timedelta(hours=1)


def status_of(obj: MfaMixin, required: bool) -> dict:
    return {"enabled": obj.mfa_enabled, "enabled_at": obj.mfa_enabled_at, "required": required,
            "recovery_codes_remaining": len(obj.mfa_recovery or [])}


def start_enrollment(obj: MfaMixin, account: str) -> dict:
    if obj.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "La double authentification est déjà active")
    secret = mfa.new_secret()
    obj.mfa_pending_enc, obj.mfa_pending_at = mfa.encrypt(secret), datetime.now(UTC)
    uri = mfa.provisioning_uri(secret, account)
    # Le secret n'est renvoyé qu'ici, pour la saisie manuelle dans l'application d'authentification.
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": mfa.qr_svg(uri)}


def activate(obj: MfaMixin, code: str) -> list[str]:
    if obj.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "La double authentification est déjà active")
    if not obj.mfa_pending_enc or not obj.mfa_pending_at or datetime.now(UTC) - obj.mfa_pending_at > PENDING_TTL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun enrôlement en cours : recommencez l'étape du QR code")
    step = mfa.verify_totp(mfa.decrypt(obj.mfa_pending_enc), code, None)
    if step is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Code incorrect : vérifiez l'heure de votre téléphone")
    shown, digests = mfa.new_recovery_codes()
    obj.mfa_secret_enc, obj.mfa_enabled_at, obj.mfa_last_step = obj.mfa_pending_enc, datetime.now(UTC), step
    obj.mfa_pending_enc = obj.mfa_pending_at = None
    obj.mfa_recovery = digests
    return shown


def verify(obj: MfaMixin, code: str) -> Literal["totp", "recovery"] | None:
    """Second facteur : code TOTP à 6 chiffres, ou code de secours (consommé)."""
    if not obj.mfa_enabled:
        return None
    step = mfa.verify_totp(mfa.decrypt(obj.mfa_secret_enc), code, obj.mfa_last_step)
    if step is not None:
        obj.mfa_last_step = step
        return "totp"
    left = mfa.consume_recovery_code(code, list(obj.mfa_recovery or []))
    if left is not None:
        obj.mfa_recovery = left  # nouvelle liste : SQLAlchemy détecte la modification du JSONB
        return "recovery"
    return None


def regenerate_codes(obj: MfaMixin) -> list[str]:
    shown, digests = mfa.new_recovery_codes()
    obj.mfa_recovery = digests
    return shown


def clear(obj: MfaMixin) -> None:
    obj.mfa_secret_enc = obj.mfa_pending_enc = obj.mfa_pending_at = obj.mfa_enabled_at = obj.mfa_last_step = None
    obj.mfa_recovery = []
