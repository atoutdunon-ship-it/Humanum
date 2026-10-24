"""TVA optionnelle (8,5 %) et promotions administrables."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import platform_session
from app.integrations import stripe_api
from tests.conftest import auth, login, make_org
from tests.fake_stripe import WEBHOOK_SECRET, FakeStripe, event

API = "/api/v1"


@pytest.fixture
async def fs(monkeypatch, client, admin_token):
    f = FakeStripe()
    secret = type(get_settings().jwt_secret)
    monkeypatch.setattr(get_settings(), "stripe_secret_key", secret("sk_test_cle"))
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret(WEBHOOK_SECRET))
    stripe_api.set_transport(f.transport())
    async with platform_session() as s:
        await s.execute(text("update plans set stripe_product_id = null, stripe_price_id = null, stripe_price_cents = null"))
        await s.execute(text("""update platform_settings set value = '{"enabled": false, "rate": 8.5, "label": "TVA",
                                "jurisdiction": "Martinique", "stripe_tax_rate_id": null}' where key = 'vat'"""))
    assert (await client.post(f"{API}/admin/billing/sync-plans", headers=admin_token)).status_code == 200
    yield f
    stripe_api.set_transport(None)


async def _org(client):
    slug = f"pr-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, slug, f"admin@{slug}.example.com")
    return o, auth(await login(client, f"admin@{slug}.example.com"))


async def test_vat_is_optional_and_applied(client, admin_token, fs):
    o, h = await _org(client)
    assert (await client.get(f"{API}/billing", headers=h)).json()["vat"] == {"enabled": False, "rate": 8.5, "label": "TVA"}
    await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "starter"})
    assert not any(k.startswith("subscription_data[default_tax_rates]") for k in fs.sessions[-1]["params"])  # hors TVA
    r = await client.put(f"{API}/admin/billing/vat", headers=admin_token, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["stripe_tax_rate_id"]
    rate = fs.tax_rates[r.json()["stripe_tax_rate_id"]]
    assert rate["percentage"] == 8.5 and rate["inclusive"] is False and rate["jurisdiction"] == "Martinique"
    await client.put(f"{API}/admin/billing/vat", headers=admin_token, json={"enabled": False})
    await client.put(f"{API}/admin/billing/vat", headers=admin_token, json={"enabled": True})
    assert len(fs.tax_rates) == 1  # le taux Stripe est réutilisé
    await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "starter"})
    assert fs.sessions[-1]["params"]["subscription_data[default_tax_rates][0]"] == rate["id"]
    # abonnement en cours : alignement à la demande
    customer = next(c for c, v in fs.customers.items() if v["metadata"]["org_id"] == o["id"])
    price = next(p for p in fs.prices.values() if p["metadata"]["plan_code"] == "starter")
    sub = fs.subscription(o["id"], customer, price["id"])
    raw, hd = event("customer.subscription.created", sub)
    await client.post(f"{API}/integrations/stripe/webhook", content=raw, headers=hd)
    assert (await client.post(f"{API}/admin/billing/vat/apply-existing", headers=admin_token)).json()["subscriptions"] >= 1
    assert fs.subs[sub["id"]]["default_tax_rates"][0]["percentage"] == 8.5
    assert (await client.get(f"{API}/billing", headers=h)).json()["vat"]["enabled"] is True
    await client.put(f"{API}/admin/billing/vat", headers=admin_token, json={"enabled": False})


async def test_promotions_lifecycle(client, admin_token, fs):
    base = {"label": "Lancement Martinique", "duration": "repeating", "duration_in_months": 3, "percent_off": 20, "plans": ["pro"]}
    bad = await client.post(f"{API}/admin/promotions", headers=admin_token, json={**base, "code": "MIX", "amount_off_cents": 1000})
    assert bad.status_code == 422  # pourcentage OU montant
    code = f"LANCEMENT{uuid.uuid4().hex[:4].upper()}"
    r = await client.post(f"{API}/admin/promotions", headers=admin_token, json={**base, "code": code.lower(), "max_redemptions": 2,
                                                                              "first_time_only": True})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["code"] == code and p["description"] == "−20 % pendant 3 mois" and p["usable"]
    pc = fs.promo_codes[p["stripe_promotion_code_id"]]
    assert pc["code"] == code and pc["max_redemptions"] == 2 and pc["first_time"] and pc["coupon"]["applies_to"]
    assert (await client.post(f"{API}/admin/promotions", headers=admin_token, json={**base, "code": code})).status_code == 409
    o, h = await _org(client)
    # contrôle par l'organisme, puis paiement avec le code
    assert (await client.post(f"{API}/billing/promo-check", headers=h, json={"code": code, "plan": "starter"})).json() == \
        {"valid": False, "reason": "Ce code ne s'applique pas à cette formule"}
    ok = (await client.post(f"{API}/billing/promo-check", headers=h, json={"code": code.lower(), "plan": "pro"})).json()
    assert ok["valid"] and ok["description"] == "−20 % pendant 3 mois"
    assert (await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "pro", "promo_code": code})).status_code == 200
    params = fs.sessions[-1]["params"]
    assert params["discounts[0][promotion_code]"] == p["stripe_promotion_code_id"] and "allow_promotion_codes" not in params
    assert (await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "pro", "promo_code": "INCONNU"})).status_code == 422
    # libellé modifiable, désactivation répercutée chez Stripe
    r = await client.patch(f"{API}/admin/promotions/{p['id']}", headers=admin_token, json={"label": "Offre de lancement", "is_active": False})
    assert r.json()["label"] == "Offre de lancement" and fs.promo_codes[p["stripe_promotion_code_id"]]["active"] is False
    assert (await client.post(f"{API}/billing/promo-check", headers=h, json={"code": code, "plan": "pro"})).json()["reason"] == "Ce code n'est plus actif"
    await client.patch(f"{API}/admin/promotions/{p['id']}", headers=admin_token, json={"is_active": True})
    # geste commercial sur un abonnement existant + relecture des utilisations
    customer = next(c for c, v in fs.customers.items() if v["metadata"]["org_id"] == o["id"])
    price = next(x for x in fs.prices.values() if x["metadata"]["plan_code"] == "pro")
    sub = fs.subscription(o["id"], customer, price["id"])
    raw, hd = event("customer.subscription.created", sub)
    await client.post(f"{API}/integrations/stripe/webhook", content=raw, headers=hd)
    r = await client.post(f"{API}/admin/organizations/{o['id']}/discount", headers=admin_token, json={"promotion_id": p["id"]})
    assert r.status_code == 200 and "Offre de lancement" in r.json()["discount_label"]
    assert (await client.get(f"{API}/billing", headers=h)).json()["discount_label"].startswith("Offre de lancement")
    rows = (await client.post(f"{API}/admin/promotions/refresh", headers=admin_token)).json()
    assert next(x for x in rows if x["id"] == p["id"])["times_redeemed"] == 1
    # montant fixe, une fois, avec expiration
    r = await client.post(f"{API}/admin/promotions", headers=admin_token, json={
        "code": f"MOINS30{uuid.uuid4().hex[:3].upper()}", "label": "Remise 30 €", "amount_off_cents": 3000, "duration": "once",
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    assert r.status_code == 201 and r.json()["description"] == "−30 € sur la première facture"
    # seule la super-administration gère les promotions
    assert (await client.get(f"{API}/admin/promotions", headers=h)).status_code == 401


async def test_promotion_terms_are_adjustable_under_the_same_code(client, admin_token, fs):
    code = f"RENTREE{uuid.uuid4().hex[:4].upper()}"
    p = (await client.post(f"{API}/admin/promotions", headers=admin_token, json={
        "code": code, "label": "Rentrée", "percent_off": 20, "duration": "repeating", "duration_in_months": 3,
        "plans": ["pro"], "max_redemptions": 2})).json()
    first_pc, first_coupon = p["stripe_promotion_code_id"], fs.promo_codes[p["stripe_promotion_code_id"]]["coupon"]["id"]
    fs.promo_codes[first_pc]["times_redeemed"] = 1  # une souscription a déjà utilisé le code
    # remise portée à 30 % sur 6 mois, toutes formules
    r = await client.put(f"{API}/admin/promotions/{p['id']}/terms", headers=admin_token, json={
        "percent_off": 30, "duration": "repeating", "duration_in_months": 6, "plans": [], "max_redemptions": 2})
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["code"] == code and q["description"] == "−30 % pendant 6 mois" and q["revision"] == 2 and q["times_redeemed"] == 1
    new_pc = fs.promo_codes[q["stripe_promotion_code_id"]]
    assert fs.promo_codes[first_pc]["active"] is False and new_pc["active"] and new_pc["code"] == code
    assert new_pc["coupon"]["id"] != first_coupon and new_pc["coupon"]["percent_off"] == 30.0
    assert new_pc["max_redemptions"] == 1  # plafond global : 2 au total, 1 déjà consommée
    _, h = await _org(client)
    ok = (await client.post(f"{API}/billing/promo-check", headers=h, json={"code": code, "plan": "starter"})).json()
    assert ok["valid"] and ok["description"] == "−30 % pendant 6 mois"  # désormais valable pour toutes les formules
    # limites seules : même coupon, nouveau code Stripe
    r = await client.put(f"{API}/admin/promotions/{p['id']}/terms", headers=admin_token, json={
        "percent_off": 30, "duration": "repeating", "duration_in_months": 6, "plans": [], "max_redemptions": 10})
    assert fs.promo_codes[r.json()["stripe_promotion_code_id"]]["coupon"]["id"] == new_pc["coupon"]["id"]
    assert fs.promo_codes[r.json()["stripe_promotion_code_id"]]["max_redemptions"] == 9 and r.json()["revision"] == 3
    # plafond inférieur aux utilisations passées : refusé
    r = await client.put(f"{API}/admin/promotions/{p['id']}/terms", headers=admin_token, json={
        "percent_off": 30, "duration": "repeating", "duration_in_months": 6, "plans": [], "max_redemptions": 1})
    assert r.status_code == 409
    current = (await client.get(f"{API}/admin/promotions", headers=admin_token)).json()
    live = next(x for x in current if x["id"] == p["id"])["stripe_promotion_code_id"]
    assert fs.promo_codes[live]["active"] is True  # le code en service n'a pas été retiré
    # incohérence : pourcentage ET montant
    r = await client.put(f"{API}/admin/promotions/{p['id']}/terms", headers=admin_token, json={
        "percent_off": 10, "amount_off_cents": 500, "duration": "once"})
    assert r.status_code == 422
    actions = [x["action"] for x in (await client.get(f"{API}/admin/audit", headers=admin_token)).json()]
    assert "promotion.adjusted" in actions
