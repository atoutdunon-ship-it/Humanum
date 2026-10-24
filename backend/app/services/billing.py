"""Facturation : formules ↔ prix Stripe, abonnement des organismes, factures, traitement idempotent des webhooks.

Stripe fait foi : l'état local (statut, formule, échéance, factures) n'est modifié que par les événements signés
reçus de Stripe, ou par la réponse directe d'un appel à Stripe. Écritures avec le rôle plateforme uniquement.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import Role
from app.integrations import stripe_api as stripe
from app.models import BillingInvoice, Contact, Membership, Organization, Plan, StripeEvent

log = logging.getLogger("humanum.billing")
BLOCKED = frozenset({"canceled", "unpaid", "incomplete_expired"})
LIVE = frozenset({"trialing", "active", "past_due", "incomplete"})


def _ts(v: int | None) -> datetime | None:
    return datetime.fromtimestamp(v, UTC) if v else None


# ---------------------------------------------------------------- formules → produits et prix Stripe
async def sync_plans(s: AsyncSession) -> list[dict]:
    """Crée produit et prix manquants ; un changement de tarif crée un nouveau prix (les prix Stripe sont immuables)
    et archive l'ancien. Les abonnements existants restent sur leur prix jusqu'à leur changement de formule."""
    out = []
    for p in (await s.scalars(select(Plan).order_by(Plan.sort_order))).all():
        if not p.stripe_product_id:
            prod = await stripe.call("POST", "/products", {"name": f"HUMANUM CONSULTING — {p.label}", "metadata": {"plan_code": p.code}},
                                     idempotency_key=f"product-{p.code}")
            p.stripe_product_id = prod["id"]
        if p.stripe_price_id is None or p.stripe_price_cents != p.monthly_price_cents:
            old = p.stripe_price_id
            price = await stripe.call("POST", "/prices", {
                "product": p.stripe_product_id, "currency": p.currency.lower(), "unit_amount": p.monthly_price_cents,
                "recurring": {"interval": "month"}, "tax_behavior": "exclusive",  # tarifs exprimés hors taxes
                "lookup_key": f"hc_{p.code}_{p.monthly_price_cents}", "transfer_lookup_key": True,
                "metadata": {"plan_code": p.code}}, idempotency_key=f"price-{p.code}-{p.monthly_price_cents}")
            p.stripe_price_id, p.stripe_price_cents = price["id"], p.monthly_price_cents
            if old and old != price["id"]:
                await stripe.call("POST", f"/prices/{old}", {"active": False}, idempotency_key=f"archive-{old}")
        out.append({"code": p.code, "stripe_price_id": p.stripe_price_id, "monthly_price_cents": p.monthly_price_cents})
    return out


# ---------------------------------------------------------------- parcours d'abonnement
async def ensure_customer(s: AsyncSession, org: Organization, email: str) -> str:
    if org.stripe_customer_id:
        return org.stripe_customer_id
    c = await stripe.call("POST", "/customers", {"name": org.name, "email": email, "preferred_locales": ["fr"],
                                                 "metadata": {"org_id": str(org.id), "org_slug": org.slug}},
                          idempotency_key=f"customer-{org.id}")
    org.stripe_customer_id, org.billing_email = c["id"], email
    return c["id"]


async def checkout_url(s: AsyncSession, org: Organization, plan: Plan, email: str, promo=None) -> str:
    if not plan.stripe_price_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Formule pas encore publiée dans Stripe : contactez HUMANUM CONSULTING")
    customer = await ensure_customer(s, org, email)
    base = get_settings().public_base_url.rstrip("/")
    sub: dict = {"metadata": {"org_id": str(org.id), "plan_code": plan.code}}
    if plan.trial_days and not org.trial_used:
        sub["trial_period_days"] = plan.trial_days
    params: dict = {"mode": "subscription", "customer": customer, "client_reference_id": str(org.id), "locale": "fr",
                    "line_items": [{"price": plan.stripe_price_id, "quantity": 1}], "subscription_data": sub,
                    "billing_address_collection": "required",
                    "tax_id_collection": {"enabled": True}, "customer_update": {"name": "auto", "address": "auto"},
                    "success_url": f"{base}/facturation?paiement=ok", "cancel_url": f"{base}/facturation?paiement=annule",
                    "metadata": {"org_id": str(org.id), "plan_code": plan.code}}
    if promo is not None:  # code validé par la plateforme (Stripe interdit de cumuler avec la saisie libre)
        params["discounts"] = [{"promotion_code": promo.stripe_promotion_code_id}]
    else:
        params["allow_promotion_codes"] = True
    if get_settings().stripe_automatic_tax:
        params["automatic_tax"] = {"enabled": True}
    else:
        from app.services.promotions import vat
        v = await vat(s)
        if v["enabled"] and v["stripe_tax_rate_id"]:
            sub["default_tax_rates"] = [v["stripe_tax_rate_id"]]  # TVA 8,5 % ajoutée au montant hors taxes
    session = await stripe.call("POST", "/checkout/sessions", params)
    return session["url"]


async def portal_url(org: Organization) -> str:
    if not org.stripe_customer_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun abonnement : choisissez d'abord une formule")
    r = await stripe.call("POST", "/billing_portal/sessions", {"customer": org.stripe_customer_id, "locale": "fr",
                                                               "return_url": f"{get_settings().public_base_url.rstrip('/')}/facturation"})
    return r["url"]


async def change_plan(s: AsyncSession, org: Organization, plan: Plan) -> None:
    if not (org.stripe_subscription_id and org.stripe_subscription_item_id and org.billing_status in LIVE):
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun abonnement en cours à modifier")
    if not plan.stripe_price_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Formule pas encore publiée dans Stripe")
    sub = await stripe.call("POST", f"/subscriptions/{org.stripe_subscription_id}", {
        "items": [{"id": org.stripe_subscription_item_id, "price": plan.stripe_price_id}],
        "proration_behavior": "create_prorations", "metadata": {"org_id": str(org.id), "plan_code": plan.code}},
        idempotency_key=f"change-{org.id}-{plan.code}-{plan.stripe_price_id}")
    await apply_subscription(s, sub)


# ---------------------------------------------------------------- quotas des formules
async def usage(s: AsyncSession) -> dict:
    """Sous la RLS de l'organisme courant."""
    coaches = await s.scalar(select(func.count()).select_from(Membership).where(Membership.role.in_([Role.COACH, Role.ORG_ADMIN])))
    coachees = await s.scalar(select(func.count()).select_from(Contact).where(Contact.user_id.is_not(None)))
    return {"coaches": coaches or 0, "active_coachees": coachees or 0}


async def plan_of(s: AsyncSession, org: Organization) -> Plan | None:
    return await s.get(Plan, org.plan)


async def ensure_capacity(s: AsyncSession, org: Organization, what: str) -> None:
    plan = await plan_of(s, org)
    if plan is None:
        return
    u = await usage(s)
    if what == "coach" and plan.max_coaches is not None and u["coaches"] >= plan.max_coaches:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Votre formule {plan.label} comprend {plan.max_coaches} coach(s) : passez à la formule supérieure")
    if what == "coachee" and plan.max_active_coachees is not None and u["active_coachees"] >= plan.max_active_coachees:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Votre formule {plan.label} comprend {plan.max_active_coachees} espaces coachés actifs : passez à la formule supérieure")


def ensure_active(org: Organization) -> None:
    if get_settings().billing_enforced and org.billing_status in BLOCKED:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Abonnement suspendu : régularisez-le dans « Facturation » pour utiliser cette fonction")


# ---------------------------------------------------------------- webhooks
async def _org_for(s: AsyncSession, obj: dict) -> Organization | None:
    oid = (obj.get("metadata") or {}).get("org_id") or obj.get("client_reference_id")
    if oid:
        try:
            org = await s.get(Organization, uuid.UUID(oid))
            if org:
                return org
        except ValueError:
            pass
    cust = obj.get("customer")
    return await s.scalar(select(Organization).where(Organization.stripe_customer_id == cust)) if cust else None


async def apply_subscription(s: AsyncSession, sub: dict) -> None:
    org = await _org_for(s, sub)
    if org is None:
        log.warning("Abonnement Stripe %s sans organisme connu", sub.get("id"))
        return
    item = (sub.get("items", {}).get("data") or [{}])[0]
    price = item.get("price") or {}
    code = (sub.get("metadata") or {}).get("plan_code") or (price.get("metadata") or {}).get("plan_code")
    if not code and price.get("id"):
        code = await s.scalar(select(Plan.code).where(Plan.stripe_price_id == price["id"]))
    org.stripe_customer_id = org.stripe_customer_id or sub.get("customer")
    org.stripe_subscription_id, org.stripe_subscription_item_id = sub["id"], item.get("id")
    org.billing_status = sub.get("status", org.billing_status)
    org.current_period_end = _ts(sub.get("current_period_end") or item.get("current_period_end"))
    org.trial_end = _ts(sub.get("trial_end"))
    org.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    if org.trial_end:
        org.trial_used = True
    disc = sub.get("discount") or ((sub.get("discounts") or [None])[0] if isinstance((sub.get("discounts") or [None])[0], dict) else None)
    if "discount" in sub or "discounts" in sub:
        org.discount_label = ((disc or {}).get("coupon") or {}).get("name") if disc else None
    if code and await s.get(Plan, code) and org.billing_status in LIVE:
        org.plan = code  # la formule suit l'abonnement payé


async def apply_invoice(s: AsyncSession, inv: dict) -> None:
    org = await _org_for(s, inv)
    if org is None:
        return
    row = await s.scalar(select(BillingInvoice).where(BillingInvoice.stripe_invoice_id == inv["id"]))
    if row is None:
        row = BillingInvoice(organization_id=org.id, stripe_invoice_id=inv["id"])  # rôle plateforme : organisme explicite
        s.add(row)
    line = ((inv.get("lines") or {}).get("data") or [{}])[0].get("period") or {}
    row.number, row.status, row.currency = inv.get("number"), inv.get("status") or "open", (inv.get("currency") or "eur").upper()
    row.amount_due, row.amount_paid = int(inv.get("amount_due") or 0), int(inv.get("amount_paid") or 0)
    row.hosted_invoice_url, row.invoice_pdf = inv.get("hosted_invoice_url"), inv.get("invoice_pdf")
    row.period_start, row.period_end = _ts(line.get("start") or inv.get("period_start")), _ts(line.get("end") or inv.get("period_end"))
    row.issued_at = _ts(inv.get("created"))


async def handle_event(s: AsyncSession, event: dict) -> str:
    try:
        async with s.begin_nested():
            s.add(StripeEvent(id=event["id"], type=event["type"]))
            await s.flush()
    except IntegrityError:
        return "duplicate"
    obj, kind = event["data"]["object"], event["type"]
    if kind == "checkout.session.completed" and obj.get("subscription"):
        sub = obj["subscription"] if isinstance(obj["subscription"], dict) else await stripe.call(
            "GET", f"/subscriptions/{obj['subscription']}")
        sub.setdefault("metadata", {}).setdefault("org_id", (obj.get("metadata") or {}).get("org_id") or obj.get("client_reference_id"))
        await apply_subscription(s, sub)
    elif kind.startswith("customer.subscription."):
        await apply_subscription(s, obj)
    elif kind.startswith("invoice."):
        await apply_invoice(s, obj)
    else:
        return "ignored"
    return "applied"
