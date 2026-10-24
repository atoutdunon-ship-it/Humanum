"""Connexion : un compte peut appartenir à plusieurs organismes ; le jeton est lié à UN organisme.

Parcours : mot de passe → (code TOTP ou code de secours si la double authentification est active)
→ (choix de l'organisme si plusieurs) → jeton d'accès. Chaque étape intermédiaire délivre un jeton
d'étape de quelques minutes, inutilisable sur l'API métier.
"""
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, principal, principal_setup
from app.api.schemas import LoginIn, MembershipOut, MfaCodeIn, MfaDisableIn, MfaLoginIn, SelectOrgIn, TokenOut
from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import (
    MFA_ROLES,
    Role,
    create_access_token,
    create_challenge_token,
    decode_challenge_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.session import set_context, tenant_session
from app.models import Membership, Organization, User
from app.services import mfa as mfa_svc
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["authentification"])
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")
_BAD = HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
_EXPIRED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Étape expirée : reconnectez-vous")


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _mfa_required_for(role: Role) -> bool:
    return get_settings().mfa_enforced and role in MFA_ROLES


async def _memberships(s: AsyncSession, user: User) -> list[MembershipOut]:
    await set_context(s, org_id=None, user_id=user.id)  # la RLS ouvre ses propres adhésions
    rows = (await s.execute(
        select(Membership.organization_id, Organization.name, Membership.role)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user.id, Organization.is_active.is_(True))
    )).all()
    return [MembershipOut(organization_id=o, organization_name=n, role=r) for o, n, r in rows]


async def _issue(s: AsyncSession, user: User, organization_id: uuid.UUID | None, request: Request,
                 mfa_method: str | None = None) -> TokenOut:
    """Toutes les vérifications d'identité sont faites : choix de l'organisme et délivrance du jeton."""
    memberships = await _memberships(s, user)
    if not memberships:
        raise _BAD
    chosen = None
    if organization_id:
        chosen = next((m for m in memberships if m.organization_id == organization_id), None)
        if chosen is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Aucun accès à cet organisme")
    elif len(memberships) == 1:
        chosen = memberships[0]
    if chosen is None:
        return TokenOut(memberships=memberships, select_token=create_challenge_token(subject=user.id, purpose="user:org"))

    setup = _mfa_required_for(chosen.role) and not user.mfa_enabled
    await set_context(s, org_id=chosen.organization_id, user_id=user.id)
    await audit(s, actor_id=user.id, action="auth.login", target_type="user", target_id=user.id, ip=_ip(request),
                **({"mfa": mfa_method} if mfa_method else {}))
    token = create_access_token(user_id=user.id, org_id=chosen.organization_id, role=chosen.role, mfa_setup=setup)
    return TokenOut(access_token=token, organization_id=chosen.organization_id, role=chosen.role,
                    memberships=memberships, mfa_setup_required=setup)


@router.post("/login", response_model=TokenOut, response_model_exclude_none=True)
async def login(body: LoginIn, request: Request) -> TokenOut:
    key = f"user:{body.email.lower()}:{_ip(request) or '?'}"
    await ratelimit.check(key)
    async with tenant_session(org_id=None, user_id=None) as s:
        user = await s.scalar(select(User).where(User.email == body.email.lower()))
        if user is None:
            verify_password(body.password, _DUMMY_HASH)  # temps constant : pas d'énumération des comptes
            await ratelimit.fail(key)
            raise _BAD
        if not user.is_active or not verify_password(body.password, user.password_hash):
            await ratelimit.fail(key)
            raise _BAD
        await ratelimit.reset(key)
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)
        if user.mfa_enabled:  # rien n'est révélé (pas même les organismes) avant le second facteur
            org = str(body.organization_id) if body.organization_id else None
            return TokenOut(mfa_required=True, mfa_token=create_challenge_token(subject=user.id, purpose="user:mfa", org=org))
        return await _issue(s, user, body.organization_id, request)


@router.post("/login/mfa", response_model=TokenOut, response_model_exclude_none=True)
async def login_mfa(body: MfaLoginIn, request: Request) -> TokenOut:
    try:
        claims = decode_challenge_token(body.mfa_token, "user:mfa")
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        raise _EXPIRED from None
    key = f"mfa:user:{user_id}"
    await ratelimit.check(key)
    async with tenant_session(org_id=None, user_id=None) as s:
        user = await s.get(User, user_id, with_for_update=True)  # verrou : un code ne passe qu'une fois
        method = mfa_svc.verify(user, body.code) if user is not None and user.is_active else None
        if method is None:
            await ratelimit.fail(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code invalide")
        await ratelimit.reset(key)
        org = body.organization_id or (uuid.UUID(claims["org"]) if claims.get("org") else None)
        return await _issue(s, user, org, request, mfa_method=method)


@router.post("/select-org", response_model=TokenOut, response_model_exclude_none=True)
async def select_org(body: SelectOrgIn, request: Request) -> TokenOut:
    try:
        user_id = uuid.UUID(decode_challenge_token(body.select_token, "user:org")["sub"])
    except (jwt.PyJWTError, ValueError):
        raise _EXPIRED from None
    async with tenant_session(org_id=None, user_id=None) as s:
        user = await s.get(User, user_id)
        if user is None or not user.is_active:
            raise _EXPIRED
        return await _issue(s, user, body.organization_id, request)


# ---------------------------------------------------------------- double authentification du compte connecté
async def _me(s: AsyncSession, p: Principal, lock: bool = False) -> User:
    user = await s.get(User, p.user_id, with_for_update=lock)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Compte inactif")
    return user


@router.get("/mfa")
async def mfa_status(p: Principal = Depends(principal_setup)) -> dict:
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        return mfa_svc.status_of(await _me(s, p), required=_mfa_required_for(p.role))


@router.post("/mfa/setup")
async def mfa_setup(p: Principal = Depends(principal_setup)) -> dict:
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        user = await _me(s, p)
        return mfa_svc.start_enrollment(user, user.email)


@router.post("/mfa/activate")
async def mfa_activate(body: MfaCodeIn, request: Request, p: Principal = Depends(principal_setup)) -> dict:
    key = f"mfa:user-setup:{p.user_id}"
    await ratelimit.check(key)
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        user = await _me(s, p, lock=True)
        try:
            codes = mfa_svc.activate(user, body.code)
        except HTTPException as e:
            if e.status_code == 422:
                await ratelimit.fail(key)
            raise
        await ratelimit.reset(key)
        await audit(s, actor_id=user.id, action="auth.mfa_enabled", target_type="user", target_id=user.id, ip=_ip(request))
        # Nouveau jeton complet : la restriction d'enrôlement est levée.
        token = create_access_token(user_id=user.id, org_id=p.org_id, role=p.role)
        return {"recovery_codes": codes, "access_token": token, "token_type": "bearer"}


@router.post("/mfa/recovery-codes")
async def mfa_regenerate(body: MfaCodeIn, request: Request, p: Principal = Depends(principal)) -> dict:
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        user = await _me(s, p, lock=True)
        if mfa_svc.verify(user, body.code) != "totp":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code de l'application d'authentification requis")
        codes = mfa_svc.regenerate_codes(user)
        await audit(s, actor_id=user.id, action="auth.mfa_codes_regenerated", target_type="user", target_id=user.id,
                    ip=_ip(request))
        return {"recovery_codes": codes}


@router.post("/mfa/disable", status_code=204)
async def mfa_disable(body: MfaDisableIn, request: Request, p: Principal = Depends(principal)) -> None:
    if _mfa_required_for(p.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La double authentification est obligatoire pour votre rôle")
    key = f"mfa:user:{p.user_id}"
    await ratelimit.check(key)
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        user = await _me(s, p, lock=True)
        if not verify_password(body.password, user.password_hash) or mfa_svc.verify(user, body.code) is None:
            await ratelimit.fail(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe ou code invalide")
        mfa_svc.clear(user)
        await audit(s, actor_id=user.id, action="auth.mfa_disabled", target_type="user", target_id=user.id, ip=_ip(request))
