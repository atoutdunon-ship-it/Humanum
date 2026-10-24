"""Facturation Stripe : formules publiées, paiement, webhooks signés et idempotents, factures, quotas, suspension."""
import time
import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import platform_session
from app.integrations import stripe_api
from tests.conftest import auth, login, make_org
from tests.fake_stripe import WEBHOOK_SECRET, FakeStripe, event

API = "/api/v1"
HOOK = f"{API}/integrations/stripe/webhook"


@pytest.fixture
async def fs(monkeypatch, client, admin_token):
    f = FakeStripe()
    secret = type(get_settings().jwt_secret)
    monkeypatch.setattr(get_settings(), "stripe_secret_key", secret("sk_test_cle"))
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret(WEBHOOK_SECRET))
    stripe_api.set_transport(f.transport())
    async with platform_session() as s:  # chaque test part d'un compte Stripe vierge
        await s.execute(text("update plans set stripe_product_id = null, stripe_price_id = null, stripe_price_cents = null"))
    r = await client.post(f"{API}/admin/billing/sync-plans", headers=admin_token)
    assert r.status_code == 200, r.text
    yield f
    stripe_api.set_transport(None)


@pytest.fixture
async def org(client, fs):
    slug = f"fa-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, slug, f"admin@{slug}.example.com")
    return {"o": o, "slug": slug, "h": auth(await login(client, f"admin@{slug}.example.com"))}


def test_stripe_signature():
    raw = b'{"id":"evt_1"}'
    h = stripe_api.sign(raw, WEBHOOK_SECRET)
    assert stripe_api.verify_webhook(raw, h, WEBHOOK_SECRET)
    assert not stripe_api.verify_webhook(raw + b" ", h, WEBHOOK_SECRET)
    assert not stripe_api.verify_webhook(raw, h, "whsec_autre")
    assert not stripe_api.verify_webhook(raw, stripe_api.sign(raw, WEBHOOK_SECRET, ts=int(time.time()) - 600), WEBHOOK_SECRET)
    assert stripe_api.encode({"line_items": [{"price": "p", "quantity": 1}], "a": {"b": True}}) == \
        [("line_items[0][price]", "p"), ("line_items[0][quantity]", "1"), ("a[b]", "true")]


async def test_plans_published_and_price_change_creates_new_price(client, admin_token, fs):
    plans = {p["code"]: p for p in (await client.get(f"{API}/admin/plans", headers=admin_token)).json()}
    assert all(plans[c]["stripe_synced"] for c in ("starter", "pro", "entreprise"))
    old = plans["pro"]["stripe_price_id"]
    assert fs.prices[old]["unit_amount"] == 8900 and fs.prices[old]["tax_behavior"] == "exclusive"
    await client.patch(f"{API}/admin/plans/pro", headers=admin_token, json={"monthly_price_cents": 9900})
    assert (await client.get(f"{API}/admin/plans", headers=admin_token)).json()[1]["stripe_synced"] is False
    await client.post(f"{API}/admin/billing/sync-plans", headers=admin_token)
    new = {p["code"]: p for p in (await client.get(f"{API}/admin/plans", headers=admin_token)).json()}["pro"]["stripe_price_id"]
    assert new != old and fs.prices[new]["unit_amount"] == 9900 and fs.prices[old]["active"] is False
    await client.patch(f"{API}/admin/plans/pro", headers=admin_token, json={"monthly_price_cents": 8900})
    await client.post(f"{API}/admin/billing/sync-plans", headers=admin_token)


async def test_checkout_webhooks_invoices_and_plan_change(client, org, fs):
    h, oid = org["h"], org["o"]["id"]
    b = (await client.get(f"{API}/billing", headers=h)).json()
    assert b["status"] == "none" and b["can_manage"] and [p["code"] for p in b["plans"]] == ["starter", "pro", "entreprise"]
    r = await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com/")
    params = fs.sessions[-1]["params"]
    assert params["subscription_data[trial_period_days]"] == "30" and params["subscription_data[metadata][org_id]"] == oid
    assert params["tax_id_collection[enabled]"] == "true" and params["mode"] == "subscription"
    await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "pro"})
    assert len(fs.customers) == 1  # client Stripe créé une seule fois
    customer = next(iter(fs.customers))
    # paiement validé chez Stripe → webhook
    price = next(p for p in fs.prices.values() if p["metadata"]["plan_code"] == "pro" and p["active"])
    sub = fs.subscription(oid, customer, price["id"], status="trialing", trial_days=30)
    raw, hd = event("checkout.session.completed", {"id": "cs_1", "customer": customer, "subscription": sub["id"],
                                                   "client_reference_id": oid, "metadata": {"org_id": oid}}, eid="evt_checkout")
    assert (await client.post(HOOK, content=raw, headers=hd)).status_code == 200
    assert (await client.post(HOOK, content=raw, headers=hd)).status_code == 200  # rejoué par Stripe : sans effet
    b = (await client.get(f"{API}/billing", headers=h)).json()
    assert b["status"] == "trialing" and b["plan"] == "pro" and b["trial_used"] and b["has_subscription"]
    assert (await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "pro"})).status_code == 409
    # facture payée
    inv = {"id": "in_1", "customer": customer, "number": "HC-0001", "status": "paid", "currency": "eur", "amount_due": 8900,
           "amount_paid": 8900, "hosted_invoice_url": "https://invoice.stripe.com/i/1", "invoice_pdf": "https://pay.stripe.com/1.pdf",
           "created": int(time.time()), "lines": {"data": [{"period": {"start": int(time.time()), "end": int(time.time()) + 2592000}}]}}
    raw, hd = event("invoice.paid", inv)
    assert (await client.post(HOOK, content=raw, headers=hd)).status_code == 200
    assert [i["number"] for i in (await client.get(f"{API}/billing", headers=h)).json()["invoices"]] == ["HC-0001"]
    # changement de formule (prorata) puis portail client
    r = await client.post(f"{API}/billing/change", headers=h, json={"plan": "entreprise"})
    assert r.status_code == 200 and r.json()["plan"] == "entreprise"
    assert fs.subs[sub["id"]]["items"]["data"][0]["price"]["metadata"]["plan_code"] == "entreprise"
    assert (await client.post(f"{API}/billing/portal", headers=h)).json()["url"].startswith("https://billing.stripe.com/")
    # signature invalide
    assert (await client.post(HOOK, content=raw, headers={"stripe-signature": "t=1,v1=00"})).status_code == 400


async def test_only_org_admin_pays_and_isolation(client, org, fs):
    r = await client.post(f"{API}/members", headers=org["h"], json={"email": f"coach@{org['slug']}.example.com", "full_name": "Coach",
                                                                    "password": "Motdepasse-solide-1", "role": "coach"})
    assert r.status_code == 201
    hc = auth(await login(client, f"coach@{org['slug']}.example.com"))
    assert (await client.get(f"{API}/billing", headers=hc)).json()["can_manage"] is False
    for path in ("/billing/checkout", "/billing/change"):
        assert (await client.post(f"{API}{path}", headers=hc, json={"plan": "pro"})).status_code == 403
    assert (await client.post(f"{API}/billing/portal", headers=hc)).status_code == 403
    async with platform_session() as s:  # une facture d'un autre organisme n'apparaît pas
        other = (await s.execute(text("select id from organizations where id <> :o limit 1"), {"o": org["o"]["id"]})).scalar_one()
        await s.execute(text("insert into billing_invoices (id, organization_id, stripe_invoice_id, status, currency, amount_due, amount_paid) "
                             "values (gen_random_uuid(), :o, :i, 'paid', 'EUR', 100, 100)"), {"o": other, "i": f"in_{uuid.uuid4().hex[:8]}"})
    assert (await client.get(f"{API}/billing", headers=org["h"])).json()["invoices"] == []


async def test_quotas_and_suspension(client, org, fs):
    h, oid = org["h"], org["o"]["id"]  # formule Starter : 3 coachs (administrateur compris)
    for i in range(2):
        r = await client.post(f"{API}/members", headers=h, json={"email": f"c{i}@{org['slug']}.example.com", "full_name": f"C{i}",
                                                                 "password": "Motdepasse-solide-1", "role": "coach"})
        assert r.status_code == 201
    r = await client.post(f"{API}/members", headers=h, json={"email": f"c9@{org['slug']}.example.com", "full_name": "C9",
                                                             "password": "Motdepasse-solide-1", "role": "coach"})
    assert r.status_code == 409 and "3 coach" in r.json()["detail"]
    # abonnement Entreprise, puis tentative de redescendre sous l'usage réel
    await client.post(f"{API}/billing/checkout", headers=h, json={"plan": "entreprise"})
    customer = next(c for c, v in fs.customers.items() if v["metadata"]["org_id"] == oid)
    price = next(p for p in fs.prices.values() if p["metadata"]["plan_code"] == "entreprise" and p["active"])
    sub = fs.subscription(oid, customer, price["id"])
    raw, hd = event("customer.subscription.created", sub)
    await client.post(HOOK, content=raw, headers=hd)
    await client.post(f"{API}/members", headers=h, json={"email": f"c9@{org['slug']}.example.com", "full_name": "C9",
                                                         "password": "Motdepasse-solide-1", "role": "coach"})
    r = await client.post(f"{API}/billing/change", headers=h, json={"plan": "starter"})
    assert r.status_code == 409 and "vous en avez 4" in r.json()["detail"]
    # résiliation : fonctions coûteuses suspendues, lecture conservée
    raw, hd = event("customer.subscription.deleted", {**sub, "status": "canceled"})
    await client.post(HOOK, content=raw, headers=hd)
    c = (await client.post(f"{API}/contacts", headers=h, json={"first_name": "A", "last_name": "B", "email": "a@b.example.com"})).json()
    assert (await client.post(f"{API}/contacts/{c['id']}/invite", headers=h)).status_code == 402
    monkey = get_settings()
    monkey.anthropic_api_key = type(monkey.jwt_secret)("sk-ant")
    try:
        assert (await client.post(f"{API}/syntheses", headers=h, json={"kind": "parcours", "contact_id": c["id"]})).status_code == 402
    finally:
        monkey.anthropic_api_key = None
    assert (await client.get(f"{API}/billing", headers=h)).json()["status"] == "canceled"
    assert (await client.get(f"{API}/contacts", headers=h)).status_code == 200


async def test_stripe_outage(client, org, fs):
    fs.down = True
    assert (await client.post(f"{API}/billing/checkout", headers=org["h"], json={"plan": "pro"})).status_code == 503
