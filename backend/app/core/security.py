"""Hachage Argon2id et jetons d'accès JWT liés à un organisme."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_ph = PasswordHasher()  # paramètres Argon2id par défaut (RFC 9106, profil bas-mémoire)


class Role(StrEnum):
    ORG_ADMIN = "org_admin"
    COACH = "coach"
    COORDINATOR = "coordinator"
    COACHEE = "coachee"
    FUNDER = "funder"


STAFF_ROLES = frozenset({Role.ORG_ADMIN, Role.COACH, Role.COORDINATOR})
MFA_ROLES = STAFF_ROLES  # accès aux données des coachés : double authentification obligatoire


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, role: Role, mfa_setup: bool = False) -> str:
    """`mfa_setup` : jeton restreint à l'activation de la double authentification (rôle qui l'exige)."""
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role.value,
        "mfa_setup": mfa_setup,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    claims = jwt.decode(
        token, s.jwt_secret.get_secret_value(), algorithms=[s.jwt_algorithm],
        options={"require": ["sub", "org", "role", "exp"]},
    )
    if "scope" in claims:  # jeton de plateforme ou d'étape intermédiaire : jamais accepté par l'API métier
        raise jwt.InvalidTokenError("portée invalide")
    return claims


def create_platform_token(*, admin_id: uuid.UUID, must_change_password: bool, mfa_setup: bool = False) -> str:
    """Jeton de super-administration : portée « platform », SANS organisme → refusé par l'API métier."""
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(admin_id), "scope": "platform", "pwd_change": must_change_password, "mfa_setup": mfa_setup,
               "iat": now, "exp": now + timedelta(minutes=s.platform_token_minutes), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_platform_token(token: str) -> dict:
    s = get_settings()
    claims = jwt.decode(token, s.jwt_secret.get_secret_value(), algorithms=[s.jwt_algorithm],
                        options={"require": ["sub", "scope", "exp"]})
    if claims.get("scope") != "platform":
        raise jwt.InvalidTokenError("portée invalide")
    return claims


def create_challenge_token(*, subject: uuid.UUID, purpose: str, minutes: int | None = None, **extra: str | None) -> str:
    """Jeton d'étape (« user:mfa », « user:org », « admin:mfa ») : quelques minutes, inutilisable ailleurs."""
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(subject), "scope": f"challenge:{purpose}", "iat": now, "jti": uuid.uuid4().hex,
               "exp": now + timedelta(minutes=minutes or s.mfa_challenge_minutes), **{k: v for k, v in extra.items() if v}}
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_challenge_token(token: str, purpose: str) -> dict:
    s = get_settings()
    claims = jwt.decode(token, s.jwt_secret.get_secret_value(), algorithms=[s.jwt_algorithm],
                        options={"require": ["sub", "scope", "exp"]})
    if claims.get("scope") != f"challenge:{purpose}":
        raise jwt.InvalidTokenError("portée invalide")
    return claims


def password_policy_error(raw: str) -> str | None:
    """Politique minimale : 12 caractères, au moins une lettre et un chiffre."""
    if len(raw) < 12:
        return "Le mot de passe doit contenir au moins 12 caractères"
    if not any(c.isalpha() for c in raw) or not any(c.isdigit() for c in raw):
        return "Le mot de passe doit contenir des lettres et des chiffres"
    return None
