"""Promotions administrables et TVA optionnelle.

Promotions : chaque code local est adossé à un coupon Stripe (la remise) et à un code promotionnel Stripe (le code
saisi, ses limites). Stripe rend remise et limites immuables ; l'administrateur peut pourtant tout ajuster : la
plateforme crée alors une nouvelle version (nouveau coupon si la remise change, nouveau code Stripe portant le MÊME
code client), désactive l'ancienne et cumule les utilisations. Les abonnements déjà remisés gardent leur remise.

TVA : désactivée par défaut (tarifs hors TVA). Activée, un taux Stripe de 8,5 % (Martinique) est appliqué aux
nouveaux abonnements, et peut l'être aux abonnements en cours.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import stripe_api as stripe
from app.models import Organization, Plan, PlatformSetting, Promotion

VAT_DEFAULT = {"enabled": False, "rate": 8.5, "label": "TVA", "jurisdiction": "Martinique", "stripe_tax_rate_id": None}


# ---------------------------------------------------------------- TVA
async def vat(s: AsyncSession) -> dict:
    row = await s.get(PlatformSetting, "vat")
    return {**VAT_DEFAULT, **(row.value if row else {})}


async def set_vat(s: AsyncSession, enabled: bool) -> dict:
    row = await s.get(PlatformSetting, "vat") or PlatformSetting(key="vat", value=dict(VAT_DEFAULT))
    v = {**VAT_DEFAULT, **row.value}
    if enabled and not v.get("stripe_tax_rate_id"):
        tr = await stripe.call("POST", "/tax_rates", {
            "display_name": v["label"], "percentage": v["rate"], "inclusive": False, "country": "FR",
            "jurisdiction": v["jurisdiction"], "description": f"TVA {v['rate']} % — {v['jurisdiction']}"},
            idempotency_key=f"tax-rate-{v['rate']}-{v['jurisdiction']}")
        v["stripe_tax_rate_id"] = tr["id"]
    v["enabled"] = enabled
    row.value = v  # nouvel objet : modification JSONB détectée
    s.add(row)
    return v


async def apply_vat_to_subscriptions(s: AsyncSession) -> tuple[int, list[str]]:
    """Aligne les abonnements en cours sur le réglage (ajout ou retrait du taux par défaut).
    Un refus isolé n'interrompt pas les autres : (nombre mis à jour, organismes en échec)."""
    v = await vat(s)
    rates = [v["stripe_tax_rate_id"]] if v["enabled"] and v["stripe_tax_rate_id"] else ""
    n, failed = 0, []
    for org in (await s.scalars(select(Organization).where(Organization.stripe_subscription_id.is_not(None),
                                                           Organization.billing_status.in_(["trialing", "active", "past_due"])))).all():
        try:
            await stripe.call("POST", f"/subscriptions/{org.stripe_subscription_id}", {"default_tax_rates": rates})
            n += 1
        except stripe.StripeTransientError:
            raise
        except stripe.StripeError:
            failed.append(org.slug)
    return n, failed


# ---------------------------------------------------------------- promotions
def describe(p: Promotion) -> str:
    value = f"−{Decimal(p.percent_off).normalize():f} %" if p.percent_off is not None else \
        f"−{p.amount_off_cents / 100:.2f} €".replace(".", ",").replace(",00", "")
    span = {"once": "sur la première facture", "forever": "sans limite de durée"}.get(p.duration) or f"pendant {p.duration_in_months} mois"
    return f"{value} {span}"


def out(p: Promotion) -> dict:
    return {"id": p.id, "code": p.code, "label": p.label, "percent_off": float(p.percent_off) if p.percent_off is not None else None,
            "amount_off_cents": p.amount_off_cents, "currency": p.currency, "duration": p.duration,
            "duration_in_months": p.duration_in_months, "plans": p.plans, "max_redemptions": p.max_redemptions,
            "expires_at": p.expires_at, "first_time_only": p.first_time_only, "is_active": p.is_active,
            "times_redeemed": p.redeemed_before + p.times_redeemed, "revision": p.revision, "description": describe(p), "usable": usable(p) is None,
            "stripe_promotion_code_id": p.stripe_promotion_code_id}


def usable(p: Promotion, plan: str | None = None) -> str | None:
    """Motif de refus, ou None si le code est utilisable."""
    if not p.is_active or not p.stripe_promotion_code_id:
        return "Ce code n'est plus actif"
    if p.expires_at and p.expires_at <= datetime.now(UTC):
        return "Ce code a expiré"
    if p.max_redemptions is not None and p.redeemed_before + p.times_redeemed >= p.max_redemptions:
        return "Ce code a atteint son nombre maximal d'utilisations"
    if plan and p.plans and plan not in p.plans:
        return "Ce code ne s'applique pas à cette formule"
    return None


async def _coupon(s: AsyncSession, p: Promotion) -> str:
    coupon: dict = {"duration": p.duration, "name": p.label[:40], "metadata": {"hc_code": p.code}}
    if p.duration == "repeating":
        coupon["duration_in_months"] = p.duration_in_months
    if p.percent_off is not None:
        coupon["percent_off"] = float(p.percent_off)
    else:
        coupon |= {"amount_off": p.amount_off_cents, "currency": (p.currency or "EUR").lower()}
    if p.plans:
        products = (await s.scalars(select(Plan.stripe_product_id).where(Plan.code.in_(p.plans)))).all()
        if not all(products):
            raise HTTPException(status.HTTP_409_CONFLICT, "Publiez d'abord les formules dans Stripe")
        coupon["applies_to"] = {"products": list(products)}
    rev = "" if p.revision == 1 else f"-v{p.revision}"
    return (await stripe.call("POST", "/coupons", coupon, idempotency_key=f"coupon-{p.code}{rev}"))["id"]


async def _promo_code(p: Promotion) -> str:
    rev = "" if p.revision == 1 else f"-v{p.revision}"
    pc: dict = {"coupon": p.stripe_coupon_id, "code": p.code, "active": p.is_active, "metadata": {"hc_code": p.code, "revision": p.revision}}
    if p.max_redemptions:
        left = p.max_redemptions - p.redeemed_before  # le plafond est global, toutes versions confondues
        if left <= 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce plafond est déjà atteint par les utilisations passées")
        pc["max_redemptions"] = left
    if p.expires_at:
        pc["expires_at"] = int(p.expires_at.timestamp())
    if p.first_time_only:
        pc["restrictions"] = {"first_time_transaction": True}
    return (await stripe.call("POST", "/promotion_codes", pc, idempotency_key=f"promo-{p.code}{rev}"))["id"]


async def create(s: AsyncSession, p: Promotion) -> Promotion:
    p.revision, p.redeemed_before, p.times_redeemed = p.revision or 1, p.redeemed_before or 0, p.times_redeemed or 0
    p.stripe_coupon_id = await _coupon(s, p)
    p.stripe_promotion_code_id = await _promo_code(p)
    s.add(p)
    await s.flush()
    return p


DISCOUNT_FIELDS = ("percent_off", "amount_off_cents", "duration", "duration_in_months", "plans")
LIMIT_FIELDS = ("max_redemptions", "expires_at", "first_time_only")


async def adjust(s: AsyncSession, p: Promotion, changes: dict) -> Promotion:
    """Nouvelle version d'une promotion : même code client, nouvelles conditions, utilisations cumulées."""
    discount = any(k in changes and changes[k] != getattr(p, k) for k in DISCOUNT_FIELDS)
    limits = any(k in changes and changes[k] != getattr(p, k) for k in LIMIT_FIELDS)
    if not (discount or limits):
        return p
    if p.stripe_promotion_code_id:  # l'ancien code Stripe doit être inactif avant d'en publier un homonyme
        try:
            current = await stripe.call("GET", f"/promotion_codes/{p.stripe_promotion_code_id}")
            p.times_redeemed = int(current.get("times_redeemed") or p.times_redeemed)
        except stripe.StripeTransientError:
            raise
        except stripe.StripeError:
            pass
    new_max = changes.get("max_redemptions", p.max_redemptions)
    if new_max is not None and new_max - (p.redeemed_before + p.times_redeemed) <= 0:  # contrôle AVANT tout retrait
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce plafond est déjà atteint par les utilisations passées")
    old = p.stripe_promotion_code_id
    if old:
        await stripe.call("POST", f"/promotion_codes/{old}", {"active": False}, idempotency_key=f"retire-{old}-{p.revision}")
    for k, v in changes.items():
        setattr(p, k, v)
    p.redeemed_before, p.times_redeemed, p.revision = p.redeemed_before + p.times_redeemed, 0, p.revision + 1
    try:
        if discount:
            p.stripe_coupon_id = await _coupon(s, p)
        p.stripe_promotion_code_id = await _promo_code(p)
    except Exception:
        if old and p.is_active:  # compensation : le code en service est remis en ligne, la transaction locale est annulée
            await stripe.call("POST", f"/promotion_codes/{old}", {"active": True})
        raise
    return p


async def update(s: AsyncSession, p: Promotion, label: str | None, is_active: bool | None) -> Promotion:
    if label is not None and label != p.label:
        await stripe.call("POST", f"/coupons/{p.stripe_coupon_id}", {"name": label[:40]})
        p.label = label
    if is_active is not None and is_active != p.is_active:
        await stripe.call("POST", f"/promotion_codes/{p.stripe_promotion_code_id}", {"active": is_active})
        p.is_active = is_active
    return p


async def refresh(s: AsyncSession) -> int:
    rows = (await s.scalars(select(Promotion).where(Promotion.stripe_promotion_code_id.is_not(None)))).all()
    for p in rows:
        pc = await stripe.call("GET", f"/promotion_codes/{p.stripe_promotion_code_id}")
        p.times_redeemed, p.is_active = int(pc.get("times_redeemed") or 0), bool(pc.get("active", p.is_active))
    return len(rows)


async def by_code(s: AsyncSession, code: str) -> Promotion | None:
    return await s.scalar(select(Promotion).where(Promotion.code == code.strip().upper()))


async def apply_to_org(s: AsyncSession, org: Organization, p: Promotion) -> None:
    """Geste commercial : remise posée directement sur l'abonnement en cours d'un organisme."""
    if not org.stripe_subscription_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet organisme n'a pas d'abonnement Stripe")
    if reason := usable(p, org.plan):
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    await stripe.call("POST", f"/subscriptions/{org.stripe_subscription_id}", {"discounts": [{"promotion_code": p.stripe_promotion_code_id}]},
                      idempotency_key=f"discount-{org.id}-{p.id}")
    org.discount_label = f"{p.label} ({describe(p)})"
