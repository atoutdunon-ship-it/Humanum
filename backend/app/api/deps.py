"""Dépendances FastAPI : identité, organisme courant, contrôle des rôles."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Role, decode_access_token, decode_platform_token
from app.db.session import platform_session, tenant_session

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    ip: str | None


def _tenant(request: Request, cred: HTTPAuthorizationCredentials | None) -> tuple[Principal, dict]:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        c = decode_access_token(cred.credentials)
        return Principal(uuid.UUID(c["sub"]), uuid.UUID(c["org"]), Role(c["role"]),
                         request.client.host if request.client else None), c
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Jeton invalide ou expiré",
                            headers={"WWW-Authenticate": "Bearer"}) from None


MFA_SETUP_REQUIRED = "Activation de la double authentification obligatoire"


async def principal(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    p, c = _tenant(request, cred)
    if c.get("mfa_setup"):  # jeton restreint : seule l'activation de la double authentification est possible
        raise HTTPException(status.HTTP_403_FORBIDDEN, MFA_SETUP_REQUIRED)
    return p


async def principal_setup(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    """Accepte aussi le jeton restreint : routes d'enrôlement de la double authentification uniquement."""
    return _tenant(request, cred)[0]


async def db(p: Principal = Depends(principal)) -> AsyncIterator[AsyncSession]:
    """Session transactionnelle cloisonnée sur l'organisme du jeton (RLS)."""
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        yield s


def require(*roles: Role) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    async def _check(p: Principal = Depends(principal)) -> Principal:
        if p.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Rôle insuffisant")
        return p
    return _check


@dataclass(frozen=True, slots=True)
class PlatformPrincipal:
    admin_id: uuid.UUID
    ip: str | None


async def _platform_claims(request: Request, cred: HTTPAuthorizationCredentials | None) -> tuple[dict, str | None]:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise", headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_platform_token(cred.credentials), (request.client.host if request.client else None)
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Jeton invalide ou expiré", headers={"WWW-Authenticate": "Bearer"}) from None


async def platform_admin_any(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> PlatformPrincipal:
    """Accepté même si le mot de passe doit être changé ou la double authentification activée (routes dédiées)."""
    c, ip = await _platform_claims(request, cred)
    return PlatformPrincipal(uuid.UUID(c["sub"]), ip)


async def platform_admin(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> PlatformPrincipal:
    c, ip = await _platform_claims(request, cred)
    if c.get("pwd_change"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Changement du mot de passe initial obligatoire")
    if c.get("mfa_setup"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, MFA_SETUP_REQUIRED)
    return PlatformPrincipal(uuid.UUID(c["sub"]), ip)


async def pdb() -> AsyncIterator[AsyncSession]:
    async with platform_session() as s:
        yield s
