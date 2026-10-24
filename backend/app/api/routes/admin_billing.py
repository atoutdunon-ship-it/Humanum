"""Super-administration : TVA optionnelle et promotions (codes, remises, gestes commerciaux)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformPrincipal, pdb, platform_admin
from app.integrations import stripe_api as stripe
from app.models import Organization, Plan, PlatformAudit, Promotion
from app.services import promotions as promo_svc

router = APIRouter(prefix="/admin", tags=["administration — facturation"])


def _log(s: AsyncSession, p: PlatformPrincipal, action: str, target: str | None = None, **details) -> None:
    s.add(PlatformAudit(admin_id=p.admin_id, action=action, target=target, ip=p.ip, details=details))


def _stripe(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Stripe : {e}")


class VatIn(BaseModel):
    enabled: bool


@router.get("/billing/vat")
async def get_vat(_: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    return await promo_svc.vat(s)


@router.put("/billing/vat")
async def put_vat(body: VatIn, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    try:
        v = await promo_svc.set_vat(s, body.enabled)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "billing.vat", None, enabled=body.enabled, rate=v["rate"])
    return v


@router.post("/billing/vat/apply-existing")
async def vat_existing(p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    try:
        n, failed = await promo_svc.apply_vat_to_subscriptions(s)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "billing.vat_applied", None, subscriptions=n, failed=failed)
    return {"subscriptions": n, "failed": failed}


class PromoIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{3,40}$")
    label: str = Field(min_length=2, max_length=120)
    percent_off: float | None = Field(default=None, gt=0, le=100)
    amount_off_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    duration: Literal["once", "repeating", "forever"] = "once"
    duration_in_months: int | None = Field(default=None, ge=1, le=36)
    plans: list[str] = Field(default_factory=list, max_length=20)
    max_redemptions: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: AwareDatetime | None = None
    first_time_only: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _coherent(self) -> PromoIn:
        if (self.percent_off is None) == (self.amount_off_cents is None):
            raise ValueError("indiquez soit un pourcentage, soit un montant")
        if (self.duration == "repeating") != (self.duration_in_months is not None):
            raise ValueError("durée en mois obligatoire pour une remise sur plusieurs mois, et seulement dans ce cas")
        if self.expires_at and self.expires_at <= datetime.now(UTC):
            raise ValueError("date d'expiration déjà passée")
        return self


class PromoPatch(BaseModel):
    label: str | None = Field(default=None, min_length=2, max_length=120)
    is_active: bool | None = None


class PromoTerms(BaseModel):
    """Ajustement des conditions : remise, durée, formules, plafond, échéance, public visé."""
    percent_off: float | None = Field(default=None, gt=0, le=100)
    amount_off_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    duration: Literal["once", "repeating", "forever"]
    duration_in_months: int | None = Field(default=None, ge=1, le=36)
    plans: list[str] = Field(default_factory=list, max_length=20)
    max_redemptions: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: AwareDatetime | None = None
    first_time_only: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> PromoTerms:
        if (self.percent_off is None) == (self.amount_off_cents is None):
            raise ValueError("indiquez soit un pourcentage, soit un montant")
        if (self.duration == "repeating") != (self.duration_in_months is not None):
            raise ValueError("durée en mois obligatoire pour une remise sur plusieurs mois, et seulement dans ce cas")
        if self.expires_at and self.expires_at <= datetime.now(UTC):
            raise ValueError("date d'expiration déjà passée")
        return self


class DiscountIn(BaseModel):
    promotion_id: uuid.UUID


@router.get("/promotions")
async def list_promotions(_: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    return [promo_svc.out(x) for x in (await s.scalars(select(Promotion).order_by(Promotion.created_at.desc()))).all()]


@router.post("/promotions", status_code=201)
async def create_promotion(body: PromoIn, p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> dict:
    data = body.model_dump()
    data["code"] = data["code"].upper()
    unknown = set(body.plans) - set((await s.scalars(select(Plan.code))).all())
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Formule inconnue : {', '.join(sorted(unknown))}")
    if await promo_svc.by_code(s, data["code"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce code existe déjà")
    try:
        x = await promo_svc.create(s, Promotion(**data))
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce code existe déjà") from None
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "promotion.create", x.code, description=promo_svc.describe(x))
    return promo_svc.out(x)


@router.patch("/promotions/{promo_id}")
async def update_promotion(promo_id: uuid.UUID, body: PromoPatch, p: PlatformPrincipal = Depends(platform_admin),
                           s: AsyncSession = Depends(pdb)) -> dict:
    x = await s.get(Promotion, promo_id)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Promotion introuvable")
    try:
        await promo_svc.update(s, x, body.label, body.is_active)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "promotion.update", x.code, **body.model_dump(exclude_none=True))
    return promo_svc.out(x)


@router.put("/promotions/{promo_id}/terms")
async def adjust_promotion(promo_id: uuid.UUID, body: PromoTerms, p: PlatformPrincipal = Depends(platform_admin),
                           s: AsyncSession = Depends(pdb)) -> dict:
    """Ajuste une promotion publiée : nouvelle version chez Stripe, même code pour les clients."""
    x = await s.get(Promotion, promo_id)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Promotion introuvable")
    unknown = set(body.plans) - set((await s.scalars(select(Plan.code))).all())
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Formule inconnue : {', '.join(sorted(unknown))}")
    before = promo_svc.describe(x)
    changes = body.model_dump()
    if changes["percent_off"] is not None:
        from decimal import Decimal
        changes["percent_off"] = Decimal(str(changes["percent_off"]))
    try:
        await promo_svc.adjust(s, x, changes)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "promotion.adjusted", x.code, before=before, after=promo_svc.describe(x), revision=x.revision)
    return promo_svc.out(x)


@router.post("/promotions/refresh")
async def refresh_promotions(p: PlatformPrincipal = Depends(platform_admin), s: AsyncSession = Depends(pdb)) -> list[dict]:
    try:
        await promo_svc.refresh(s)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    return [promo_svc.out(x) for x in (await s.scalars(select(Promotion).order_by(Promotion.created_at.desc()))).all()]


@router.post("/organizations/{org_id}/discount")
async def discount_org(org_id: uuid.UUID, body: DiscountIn, p: PlatformPrincipal = Depends(platform_admin),
                       s: AsyncSession = Depends(pdb)) -> dict:
    org, x = await s.get(Organization, org_id), await s.get(Promotion, body.promotion_id)
    if org is None or x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisme ou promotion introuvable")
    try:
        await promo_svc.apply_to_org(s, org, x)
    except stripe.StripeError as e:
        raise _stripe(e) from None
    _log(s, p, "promotion.applied", org.slug, code=x.code)
    return {"discount_label": org.discount_label}
