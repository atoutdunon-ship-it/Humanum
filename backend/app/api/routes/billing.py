"""Facturation de l'organisme : formule, abonnement Stripe (paiement, portail, changement), factures, webhook."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import STAFF_ROLES, Role
from app.db.session import platform_session
from app.integrations import stripe_api as stripe
from app.models import BillingInvoice, Organization, Plan, User
from app.services import billing, promotions

router = APIRouter(prefix="/billing", tags=["facturation"])
hooks = APIRouter(prefix="/integrations/stripe", tags=["facturation"])
_read = require(*STAFF_ROLES)
_admin = require(Role.ORG_ADMIN)  # seul l'administrateur de l'organisme engage la dépense


class PlanChoice(BaseModel):
    plan: str = Field(pattern=r"^[a-z0-9_-]{2,32}$")
    promo_code: str | None = Field(default=None, max_length=40)


class PromoCheck(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    plan: str = Field(pattern=r"^[a-z0-9_-]{2,32}$")


def _stripe_down(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Stripe ne répond pas : {e}")


@router.get("")
async def overview(p: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> dict:
    org = await s.get(Organization, p.org_id)
    plans = (await s.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order))).all()
    invoices = (await s.scalars(select(BillingInvoice).order_by(BillingInvoice.issued_at.desc().nulls_last()).limit(36))).all()
    v = await promotions.vat(s)
    return {
        "vat": {"enabled": v["enabled"], "rate": v["rate"], "label": v["label"]}, "discount_label": org.discount_label,
        "plan": org.plan, "status": org.billing_status, "trial_end": org.trial_end, "current_period_end": org.current_period_end,
        "cancel_at_period_end": org.cancel_at_period_end, "billing_email": org.billing_email, "trial_used": org.trial_used,
        "has_subscription": bool(org.stripe_subscription_id) and org.billing_status in billing.LIVE,
        "can_manage": p.role == Role.ORG_ADMIN, "stripe_configured": stripe.configured(),
        "usage": await billing.usage(s),
        "plans": [{"code": x.code, "label": x.label, "monthly_price_cents": x.monthly_price_cents, "currency": x.currency,
                   "max_coaches": x.max_coaches, "max_active_coachees": x.max_active_coachees,
                   "ai_syntheses_per_month": x.ai_syntheses_per_month, "trial_days": x.trial_days,
                   "available": bool(x.stripe_price_id)} for x in plans],
        "invoices": [{"number": i.number, "status": i.status, "currency": i.currency, "amount_due": i.amount_due,
                      "amount_paid": i.amount_paid, "issued_at": i.issued_at, "period_start": i.period_start,
                      "period_end": i.period_end, "hosted_invoice_url": i.hosted_invoice_url, "invoice_pdf": i.invoice_pdf}
                     for i in invoices],
    }


@router.post("/checkout")
async def checkout(body: PlanChoice, p: Principal = Depends(_admin)) -> dict:
    async with platform_session() as s:
        org, plan, user = await s.get(Organization, p.org_id), await s.get(Plan, body.plan), await s.get(User, p.user_id)
        if plan is None or not plan.is_public:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Formule inconnue")
        if org.stripe_subscription_id and org.billing_status in billing.LIVE:
            raise HTTPException(status.HTTP_409_CONFLICT, "Un abonnement est déjà en cours : changez de formule")
        promo = None
        if body.promo_code:
            promo = await promotions.by_code(s, body.promo_code)
            reason = "Code inconnu" if promo is None else promotions.usable(promo, plan.code)
            if reason:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, reason)
        try:
            return {"url": await billing.checkout_url(s, org, plan, org.billing_email or user.email, promo)}
        except stripe.StripeTransientError as e:
            raise _stripe_down(e) from None
        except stripe.StripeError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None


@router.post("/promo-check")
async def promo_check(body: PromoCheck, _: Principal = Depends(_admin), s: AsyncSession = Depends(db)) -> dict:
    key = f"promo:{_.org_id}"
    await ratelimit.check(key, limit=20)  # pas d'essai systématique de codes
    p = await promotions.by_code(s, body.code)
    reason = "Code inconnu" if p is None else promotions.usable(p, body.plan)
    if reason:
        await ratelimit.fail(key)
        return {"valid": False, "reason": reason}
    return {"valid": True, "code": p.code, "label": p.label, "description": promotions.describe(p)}


@router.post("/change")
async def change(body: PlanChoice, p: Principal = Depends(_admin), s: AsyncSession = Depends(db)) -> dict:
    target = await s.get(Plan, body.plan)
    if target is None or not target.is_public:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formule inconnue")
    u = await billing.usage(s)  # pas de passage à une formule plus petite que l'usage réel
    if target.max_coaches is not None and u["coaches"] > target.max_coaches:
        raise HTTPException(status.HTTP_409_CONFLICT, f"La formule {target.label} comprend {target.max_coaches} coach(s) : vous en avez {u['coaches']}")
    if target.max_active_coachees is not None and u["active_coachees"] > target.max_active_coachees:
        raise HTTPException(status.HTTP_409_CONFLICT, f"La formule {target.label} comprend {target.max_active_coachees} espaces coachés actifs")
    async with platform_session() as ps:
        org = await ps.get(Organization, p.org_id)
        try:
            await billing.change_plan(ps, org, target)
        except stripe.StripeTransientError as e:
            raise _stripe_down(e) from None
        except stripe.StripeError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None
        return {"plan": org.plan, "status": org.billing_status}


@router.post("/portal")
async def portal(p: Principal = Depends(_admin)) -> dict:
    async with platform_session() as s:
        try:
            return {"url": await billing.portal_url(await s.get(Organization, p.org_id))}
        except stripe.StripeError as e:
            raise _stripe_down(e) from None


@hooks.post("/webhook")
async def webhook(request: Request) -> Response:
    """Point de terminaison déclaré dans Stripe. Corps brut signé ; idempotent par identifiant d'événement."""
    secret = get_settings().stripe_webhook_secret
    raw = await request.body()
    if not secret or not stripe.verify_webhook(raw, request.headers.get("stripe-signature"), secret.get_secret_value()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signature Stripe invalide")

    event = json.loads(raw)
    try:
        async with platform_session() as s:
            await billing.handle_event(s, event)
    except stripe.StripeTransientError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Réessayer plus tard") from None  # Stripe rejoue
    return Response(status_code=200)
