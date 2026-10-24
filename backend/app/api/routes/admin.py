"""Panneau d'administration de la plateforme : accès, organismes, tarifs, options, textes, journal.

Toutes les routes passent par le rôle PostgreSQL `humanum_platform`, qui ne détient
AUCUN droit sur les données de coaching : la super-administration ne peut pas lire une séance.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformPrincipal, pdb, platform_admin, platform_admin_any
from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import (
    Role,
    create_challenge_token,
    create_platform_token,
    decode_challenge_token,
    hash_password,
    needs_rehash,
    password_policy_error,
    verify_password,
)
from app.db.session import platform_session
from app.models import (
    FeatureFlag,
    Membership,
    Organization,
    OrgFeature,
    Plan,
    PlatformAdmin,
    PlatformAudit,
    PlatformText,
    User,
)
from app.services import mfa as mfa_svc

router = APIRouter(prefix="/admin", tags=["administration"])
_DUMMY = hash_password("timing-equalizer-admin")


async def _log(s: AsyncSession, p: PlatformPrincipal | None, action: str, target: str | None = None, **details) -> None:
    s.add(PlatformAudit(admin_id=p.admin_id if p else None, action=action, target=target,
                        ip=p.ip if p else None, details=details))


def _policy(pwd: str) -> None:
    if err := password_policy_error(pwd):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)


# ---------------------------------------------------------------- authentification
class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AdminToken(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105
    must_change_password: bool = False
    mfa_required: bool = False        # étape 2 : code TOTP ou code de secours attendu
    mfa_token: str | None = None
    mfa_setup_required: bool = False  # jeton restreint : activer la double authentification


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(max_length=128)


class MfaLogin(BaseModel):
    mfa_token: str = Field(max_length=2048)
    code: str = Field(min_length=6, max_length=16)


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=16)


def _must_setup(a: PlatformAdmin) -> bool:
    return get_settings().mfa_enforced and not a.mfa_enabled


def _session_token(a: PlatformAdmin) -> AdminToken:
    setup = not a.must_change_password and _must_setup(a)
    return AdminToken(access_token=create_platform_token(admin_id=a.id, must_change_password=a.must_change_password,
                                                         mfa_setup=setup),
                      must_change_password=a.must_change_password, mfa_setup_required=setup)


@router.post("/auth/login", response_model=AdminToken, response_model_exclude_none=True)
async def admin_login(body: AdminLogin, request: Request, s: AsyncSession = Depends(pdb)) -> AdminToken:
    ip = request.client.host if request.client else "?"
    key = f"admin:{body.username.lower()}:{ip}"
    await ratelimit.check(key)
    a = await s.scalar(select(PlatformAdmin).where(func.lower(PlatformAdmin.username) == body.username.lower()))
    ok = verify_password(body.password, a.password_hash if a else _DUMMY) and a is not None and a.is_active
    if not ok:
        await ratelimit.fail(key)
        async with platform_session() as s2:  # journalisé hors de la transaction annulée
            await _log(s2, None, "admin.login_failed", body.username[:64], ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    await ratelimit.reset(key)
    if needs_rehash(a.password_hash):
        a.password_hash = hash_password(body.password)
    if a.mfa_enabled:  # le mot de passe seul n'ouvre rien : second facteur exigé
        return AdminToken(mfa_required=True, mfa_token=create_challenge_token(subject=a.id, purpose="admin:mfa"))
    a.last_login_at = datetime.now(UTC)
    await _log(s, PlatformPrincipal(a.id, ip), "admin.login", a.username)
    return _session_token(a)


@router.post("/auth/login/mfa", response_model=AdminToken, response_model_exclude_none=True)
async def admin_login_mfa(body: MfaLogin, request: Request, s: AsyncSession = Depends(pdb)) -> AdminToken:
    ip = request.client.host if request.client else None
    try:
        admin_id = uuid.UUID(decode_challenge_token(body.mfa_token, "admin:mfa")["sub"])
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Étape expirée : reconnectez-vous") from None
    key = f"mfa:admin:{admin_id}"
    await ratelimit.check(key)
    a = await s.get(PlatformAdmin, admin_id, with_for_update=True)  # verrou : un code ne passe qu'une fois
    method = mfa_svc.verify(a, body.code) if a is not None and a.is_active else None
    if method is None:
        await ratelimit.fail(key)
        async with platform_session() as s2:
            await _log(s2, None, "admin.mfa_failed", a.username if a else None, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code invalide")
    await ratelimit.reset(key)
    a.last_login_at = datetime.now(UTC)
    p = PlatformPrincipal(a.id, ip)
    if method == "recovery":
        await _log(s, p, "admin.mfa_recovery_used", a.username, remaining=len(a.mfa_recovery))
    await _log(s, p, "admin.login", a.username, mfa=method)
    return _session_token(a)


@router.post("/auth/change-password", response_model=AdminToken, response_model_exclude_none=True)
async def admin_change_password(body: PasswordChange, p: PlatformPrincipal = Depends(platform_admin_any),
                                s: AsyncSession = Depends(pdb)) -> AdminToken:
    a = await s.get(PlatformAdmin, p.admin_id)
    if a is None or not verify_password(body.current_password, a.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe actuel incorrect")
    _policy(body.new_password)
    if verify_password(body.new_password, a.password_hash):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Le nouveau mot de passe doit être différent")
    a.password_hash, a.must_change_password = hash_password(body.new_password), False
    await _log(s, p, "admin.password_changed", a.username)
    return _session_token(a)


@router.get("/auth/mfa")
async def admin_mfa_status(p: PlatformPrincipal = Depends(platform_admin_any), s: AsyncSession = Depends(pdb)) -> dict:
    a = await s.get(PlatformAdmin, p.admin_id)
    return mfa_svc.status_of(a, required=get_settings().mfa_enforced)


@router.post("/auth/mfa/setup")
async def admin_mfa_setup(p: PlatformPrincipal = Depends(platform_admin_any), s: AsyncSession = Depends(pdb)) -> dict:
    a = await s.get(PlatformAdmin, p.admin_id)
    if a.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Changement du mot de passe initial obligatoire")
    return mfa_svc.start_enrollment(a, f"admin:{a.username}")


@router.post("/auth/mfa/activate")
async def admin_mfa_activate(body: MfaCode, p: PlatformPrincipal = Depends(platform_admin_any),
                             s: AsyncSession = Depends(pdb)) -> dict:
    key = f"mfa:admin-setup:{p.admin_id}"
    await ratelimit.check(key)
    a = await s.get(PlatformAdmin, p.admin_id, with_for_update=True)
    try:
        codes = mfa_svc.activate(a, body.code)
    except HTTPException as e:
        if e.status_code == 422:
            await ratelimit.fail(key)
        raise
    await ratelimit.reset(key)
    await _log(s, p, "admin.mfa_enabled", a.username)
    return {"recovery_codes": codes, **_session_token(a).model_dump(exclude_none=True)}


@router.post("/auth/mfa/recovery-codes")
async def admin_mfa_regenerate(body: MfaCode, p: PlatformPrincipal = Depends(platform_admin),
                               s: AsyncSession = Depends(pdb)) -> dict:
    a = await s.get(PlatformAdmin, p.admin_id, with_for_update=True)
    if mfa_svc.verify(a, body.code) != "totp":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code de l'application d'authentification requis")
    codes = mfa_svc.regenerate_codes(a)
    await _log(s, p, "admin.mfa_codes_regenerated", a.username)
    return {"recovery_codes": codes}


@router.get("/me")
async def admin_me(p: PlatformPrincipal = Depends(platform_admin_any), s: AsyncSession = Depends(pdb)) -> dict:
    a = await s.get(PlatformAdmin, p.admin_id)
    if a is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Compte inconnu")
    return {"id": a.id, "username": a.username, "must_change_password": a.must_change_password,
            "last_login_at": a.last_login_at, "mfa_enabled": a.mfa_enabled}


# ---------------------------------------------------------------- super-administrateurs
class AdminCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,64}$")
    initial_password: str = Field(max_length=128)


@router.get("/admins")
async def list_admins(_: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    rows = (await s.scalars(select(PlatformAdmin).order_by(PlatformAdmin.username))).all()
    return [{"id": a.id, "username": a.username, "is_active": a.is_active, "must_change_password": a.must_change_password,
             "last_login_at": a.last_login_at, "mfa_enabled": a.mfa_enabled} for a in rows]


@router.post("/admins", status_code=201)
async def create_admin(body: AdminCreate, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    _policy(body.initial_password)
    a = PlatformAdmin(username=body.username, password_hash=hash_password(body.initial_password), must_change_password=True)
    s.add(a)
    try:
        await s.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Identifiant déjà utilisé") from None
    await _log(s, p, "admin.create", body.username)
    return {"id": a.id, "username": a.username}


@router.patch("/admins/{admin_id}")
async def set_admin_active(admin_id: uuid.UUID, is_active: bool, p: PlatformPrincipal = Depends(platform_admin),
                           s: AsyncSession = Depends(pdb)) -> dict:
    if admin_id == p.admin_id and not is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Impossible de désactiver son propre compte")
    a = await s.get(PlatformAdmin, admin_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    a.is_active = is_active
    await _log(s, p, "admin.set_active", a.username, is_active=is_active)
    return {"id": a.id, "is_active": a.is_active}


@router.post("/admins/{admin_id}/reset-mfa", status_code=204)
async def reset_admin_mfa(admin_id: uuid.UUID, p: PlatformPrincipal = Depends(platform_admin),
                          s: AsyncSession = Depends(pdb)) -> Response:
    """Téléphone perdu sans code de secours : un AUTRE super-administrateur réinitialise ; réenrôlement imposé."""
    if admin_id == p.admin_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Un autre super-administrateur doit effectuer cette réinitialisation")
    a = await s.get(PlatformAdmin, admin_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    mfa_svc.clear(a)
    await _log(s, p, "admin.mfa_reset", a.username)
    return Response(status_code=204)


# ---------------------------------------------------------------- organismes
class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,80}$")
    plan: str = "starter"
    admin_email: EmailStr
    admin_full_name: str = Field(min_length=2, max_length=200)
    admin_password: str = Field(max_length=128)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    plan: str | None = None
    is_active: bool | None = None
    ai_enabled: bool | None = None


def _org(o: Organization, members: int = 0) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug, "plan": o.plan, "is_active": o.is_active,
            "ai_enabled": o.ai_enabled, "members": members, "created_at": o.created_at, "billing_status": o.billing_status,
            "current_period_end": o.current_period_end}


async def _plan_exists(s: AsyncSession, code: str) -> None:
    if await s.get(Plan, code) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Formule inconnue : {code}")


@router.get("/organizations")
async def list_orgs(q: str | None = None, _: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    cnt = select(Membership.organization_id, func.count().label("n")).group_by(Membership.organization_id).subquery()
    stmt = select(Organization, func.coalesce(cnt.c.n, 0)).outerjoin(cnt, cnt.c.organization_id == Organization.id).order_by(Organization.name)
    if q:
        stmt = stmt.where(or_(Organization.name.ilike(f"%{q}%"), Organization.slug.ilike(f"%{q}%")))
    return [_org(o, n) for o, n in (await s.execute(stmt)).all()]


@router.post("/organizations", status_code=201)
async def create_org(body: OrgCreate, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    await _plan_exists(s, body.plan)
    _policy(body.admin_password)
    user = await s.scalar(select(User).where(User.email == body.admin_email.lower()))
    org = Organization(id=uuid.uuid4(), name=body.name, slug=body.slug, plan=body.plan)
    s.add(org)
    if user is None:
        user = User(email=body.admin_email.lower(), full_name=body.admin_full_name, password_hash=hash_password(body.admin_password))
        s.add(user)
    try:
        async with s.begin_nested():  # point de sauvegarde : un doublon n'annule pas la transaction
            await s.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Identifiant d'organisme déjà utilisé") from None
    s.add(Membership(organization_id=org.id, user_id=user.id, role=Role.ORG_ADMIN))
    await _log(s, p, "organization.create", body.slug, plan=body.plan, admin=user.email)
    await s.flush()
    await s.refresh(org)
    return _org(org, 1)


@router.patch("/organizations/{org_id}")
async def update_org(org_id: uuid.UUID, body: OrgPatch, p: PlatformPrincipal = Depends(platform_admin),
                     s: AsyncSession = Depends(pdb)) -> dict:
    o = await s.get(Organization, org_id)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisme introuvable")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "plan" in changes:
        await _plan_exists(s, changes["plan"])
    for k, v in changes.items():
        setattr(o, k, v)
    await _log(s, p, "organization.update", o.slug, **changes)
    await s.flush()
    await s.refresh(o)
    return _org(o)


# ---------------------------------------------------------------- accès : comptes et adhésions
class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    is_active: bool | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(max_length=128)


class RoleSet(BaseModel):
    role: Role


@router.get("/users")
async def list_users(q: str | None = Query(default=None, max_length=100), limit: int = Query(default=100, le=500),
                     _: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    stmt = select(User).order_by(User.full_name).limit(limit)
    if q:
        stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.full_name.ilike(f"%{q}%")))
    users = (await s.scalars(stmt)).all()
    ms = (await s.execute(select(Membership.user_id, Organization.id, Organization.name, Membership.role)
                          .join(Organization, Organization.id == Membership.organization_id)
                          .where(Membership.user_id.in_([u.id for u in users])))).all()
    by: dict[uuid.UUID, list] = {}
    for uid, oid, oname, role in ms:
        by.setdefault(uid, []).append({"organization_id": oid, "organization_name": oname, "role": role})
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active,
             "mfa_enabled": u.mfa_enabled, "memberships": by.get(u.id, [])} for u in users]


@router.patch("/users/{user_id}")
async def update_user(user_id: uuid.UUID, body: UserPatch, p: PlatformPrincipal = Depends(platform_admin),
                      s: AsyncSession = Depends(pdb)) -> dict:
    u = await s.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(u, k, v)
    await _log(s, p, "user.update", u.email, **changes)
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active}


@router.post("/users/{user_id}/reset-password", status_code=204)
async def reset_user_password(user_id: uuid.UUID, body: PasswordReset, p: PlatformPrincipal = Depends(platform_admin),
                              s: AsyncSession = Depends(pdb)) -> Response:
    _policy(body.new_password)
    u = await s.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    u.password_hash = hash_password(body.new_password)
    await _log(s, p, "user.password_reset", u.email)
    return Response(status_code=204)


@router.post("/users/{user_id}/reset-mfa", status_code=204)
async def reset_user_mfa(user_id: uuid.UUID, p: PlatformPrincipal = Depends(platform_admin),
                         s: AsyncSession = Depends(pdb)) -> Response:
    u = await s.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    mfa_svc.clear(u)
    await _log(s, p, "user.mfa_reset", u.email)
    return Response(status_code=204)


@router.put("/organizations/{org_id}/members/{user_id}")
async def set_member_role(org_id: uuid.UUID, user_id: uuid.UUID, body: RoleSet, p: PlatformPrincipal = Depends(platform_admin),
                          s: AsyncSession = Depends(pdb)) -> dict:
    if await s.get(Organization, org_id) is None or await s.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisme ou compte introuvable")
    m = await s.scalar(select(Membership).where(Membership.organization_id == org_id, Membership.user_id == user_id))
    if m is None:
        s.add(Membership(organization_id=org_id, user_id=user_id, role=body.role))
    else:
        m.role = body.role
    await _log(s, p, "membership.set", f"{org_id}/{user_id}", role=body.role.value)
    return {"organization_id": org_id, "user_id": user_id, "role": body.role}


@router.delete("/organizations/{org_id}/members/{user_id}", status_code=204)
async def remove_member(org_id: uuid.UUID, user_id: uuid.UUID, p: PlatformPrincipal = Depends(platform_admin),
                        s: AsyncSession = Depends(pdb)) -> Response:
    r = await s.execute(delete(Membership).where(Membership.organization_id == org_id, Membership.user_id == user_id))
    if r.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Adhésion introuvable")
    await _log(s, p, "membership.remove", f"{org_id}/{user_id}")
    return Response(status_code=204)


# ---------------------------------------------------------------- tarifs
class PlanIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_-]{2,32}$")
    label: str = Field(min_length=1, max_length=80)
    monthly_price_cents: int = Field(ge=0, le=10_000_000)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    max_coaches: int | None = Field(default=None, ge=1)
    max_active_coachees: int | None = Field(default=None, ge=1)
    ai_syntheses_per_month: int | None = Field(default=None, ge=0)
    exports: list[str] = Field(default_factory=lambda: ["pdf"])
    trial_days: int = Field(default=0, ge=0, le=365)
    is_public: bool = True
    sort_order: int = 0


class PlanPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    monthly_price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_coaches: int | None = Field(default=None, ge=1)
    max_active_coachees: int | None = Field(default=None, ge=1)
    ai_syntheses_per_month: int | None = Field(default=None, ge=0)
    exports: list[str] | None = None
    trial_days: int | None = Field(default=None, ge=0, le=365)
    is_public: bool | None = None
    sort_order: int | None = None


def _plan(x: Plan) -> dict:
    return {**{c: getattr(x, c) for c in PlanIn.model_fields}, "stripe_price_id": x.stripe_price_id,
            "stripe_synced": x.stripe_price_id is not None and x.stripe_price_cents == x.monthly_price_cents}


@router.post("/billing/sync-plans")
async def sync_plans(p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    """Publie les formules dans Stripe (produits et prix) ; un tarif modifié crée un nouveau prix."""
    from app.integrations import stripe_api
    from app.services import billing
    try:
        out = await billing.sync_plans(s)
    except stripe_api.StripeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Stripe : {e}") from None
    await _log(s, p, "billing.plans_synced", None, plans=[o["code"] for o in out])
    return out


@router.get("/plans")
async def list_plans(_: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    return [_plan(x) for x in (await s.scalars(select(Plan).order_by(Plan.sort_order, Plan.code))).all()]


@router.post("/plans", status_code=201)
async def create_plan(body: PlanIn, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    if await s.get(Plan, body.code):
        raise HTTPException(status.HTTP_409_CONFLICT, "Code de formule déjà utilisé")
    x = Plan(**body.model_dump())
    s.add(x)
    await _log(s, p, "plan.create", body.code, **body.model_dump())
    return _plan(x)


@router.patch("/plans/{code}")
async def update_plan(code: str, body: PlanPatch, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    x = await s.get(Plan, code)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formule introuvable")
    changes = body.model_dump(exclude_unset=True)  # null explicite = illimité pour les quotas
    before = {k: getattr(x, k) for k in changes}
    for k, v in changes.items():
        setattr(x, k, v)
    await _log(s, p, "plan.update", code, before=before, after=changes)
    return _plan(x)


@router.delete("/plans/{code}", status_code=204)
async def delete_plan(code: str, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> Response:
    used = await s.scalar(select(func.count()).select_from(Organization).where(Organization.plan == code))
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Formule utilisée par {used} organisme(s)")
    r = await s.execute(delete(Plan).where(Plan.code == code))
    if r.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formule introuvable")
    await _log(s, p, "plan.delete", code)
    return Response(status_code=204)


# ---------------------------------------------------------------- options
class FlagIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    label: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled_by_default: bool = False


class FlagPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    enabled_by_default: bool | None = None


class OverrideSet(BaseModel):
    enabled: bool | None  # None = revenir à la valeur par défaut


def _flag(f: FeatureFlag) -> dict:
    return {"key": f.key, "label": f.label, "description": f.description, "enabled_by_default": f.enabled_by_default}


@router.get("/features")
async def list_features(_: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    return [_flag(f) for f in (await s.scalars(select(FeatureFlag).order_by(FeatureFlag.label))).all()]


@router.post("/features", status_code=201)
async def create_feature(body: FlagIn, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    if await s.get(FeatureFlag, body.key):
        raise HTTPException(status.HTTP_409_CONFLICT, "Clé d'option déjà utilisée")
    f = FeatureFlag(**body.model_dump())
    s.add(f)
    await _log(s, p, "feature.create", body.key)
    return _flag(f)


@router.patch("/features/{key}")
async def update_feature(key: str, body: FlagPatch, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    f = await s.get(FeatureFlag, key)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option introuvable")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(f, k, v)
    await _log(s, p, "feature.update", key, **changes)
    return _flag(f)


@router.delete("/features/{key}", status_code=204)
async def delete_feature(key: str, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> Response:
    r = await s.execute(delete(FeatureFlag).where(FeatureFlag.key == key))
    if r.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option introuvable")
    await _log(s, p, "feature.delete", key)
    return Response(status_code=204)


@router.get("/organizations/{org_id}/features")
async def org_features(org_id: uuid.UUID, _: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    ov = dict((await s.execute(select(OrgFeature.key, OrgFeature.enabled).where(OrgFeature.organization_id == org_id))).all())
    flags = (await s.scalars(select(FeatureFlag).order_by(FeatureFlag.label))).all()
    return [{**_flag(f), "override": ov.get(f.key), "effective": ov.get(f.key, f.enabled_by_default)} for f in flags]


@router.put("/organizations/{org_id}/features/{key}")
async def set_org_feature(org_id: uuid.UUID, key: str, body: OverrideSet, p: PlatformPrincipal = Depends(platform_admin),
                          s: AsyncSession = Depends(pdb)) -> dict:
    if await s.get(Organization, org_id) is None or await s.get(FeatureFlag, key) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisme ou option introuvable")
    ov = await s.get(OrgFeature, (org_id, key))
    if body.enabled is None:
        if ov:
            await s.delete(ov)
    elif ov:
        ov.enabled = body.enabled
    else:
        s.add(OrgFeature(organization_id=org_id, key=key, enabled=body.enabled))
    await _log(s, p, "feature.override", f"{org_id}/{key}", enabled=body.enabled)
    return {"organization_id": org_id, "key": key, "override": body.enabled}


# ---------------------------------------------------------------- textes
class TextIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_.-]{2,120}$")
    locale: str = Field(default="fr", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    value: str = Field(max_length=20_000)
    description: str | None = Field(default=None, max_length=255)


@router.get("/texts")
async def list_texts(locale: str | None = None, _: PlatformPrincipal = Depends(platform_admin),
                     s: AsyncSession = Depends(pdb)) -> list[dict]:
    stmt = select(PlatformText).order_by(PlatformText.key, PlatformText.locale)
    if locale:
        stmt = stmt.where(PlatformText.locale == locale)
    return [{"key": t.key, "locale": t.locale, "value": t.value, "description": t.description, "updated_at": t.updated_at}
            for t in (await s.scalars(stmt)).all()]


@router.put("/texts")
async def upsert_text(body: TextIn, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    t = await s.get(PlatformText, (body.key, body.locale))
    before = t.value if t else None
    if t is None:
        s.add(PlatformText(**body.model_dump()))
    else:
        t.value = body.value
        if body.description is not None:
            t.description = body.description
    await _log(s, p, "text.upsert", f"{body.locale}:{body.key}", before=before, after=body.value)
    return body.model_dump()


@router.delete("/texts/{locale}/{key}", status_code=204)
async def delete_text(locale: str, key: str, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> Response:
    r = await s.execute(delete(PlatformText).where(PlatformText.key == key, PlatformText.locale == locale))
    if r.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Texte introuvable")
    await _log(s, p, "text.delete", f"{locale}:{key}")
    return Response(status_code=204)


# ---------------------------------------------------------------- journal
@router.get("/audit")
async def list_audit(limit: int = Query(default=200, le=1000), _: PlatformPrincipal = Depends(platform_admin),
                     s: AsyncSession = Depends(pdb)) -> list[dict]:
    rows = (await s.execute(select(PlatformAudit, PlatformAdmin.username).outerjoin(PlatformAdmin, PlatformAdmin.id == PlatformAudit.admin_id)
                            .order_by(PlatformAudit.at.desc()).limit(limit))).all()
    return [{"at": a.at, "admin": u, "action": a.action, "target": a.target, "ip": str(a.ip) if a.ip else None,
             "details": a.details} for a, u in rows]
