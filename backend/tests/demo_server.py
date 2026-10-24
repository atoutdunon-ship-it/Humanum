"""API de démonstration — Google, Fathom et Claude simulés, worker intégré : `python -m tests.demo_server`.

Sert aux recettes visuelles et aux démonstrations, sans aucun compte tiers :
agenda Google et liens Meet fictifs, réunions Fathom d'exemple (clé : voir tests/fake_fathom.py), synthèses factices.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import uvicorn

os.environ.setdefault("HC_GOOGLE_CLIENT_ID", "demo")
os.environ.setdefault("HC_GOOGLE_CLIENT_SECRET", "demo")
os.environ.setdefault("HC_ANTHROPIC_API_KEY", "demo")
os.environ.setdefault("HC_STRIPE_SECRET_KEY", "sk_test_demo")
os.environ.setdefault("HC_STRIPE_WEBHOOK_SECRET", "whsec_test_" + "x" * 24)

from arq.worker import Worker  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

from app.core import queue  # noqa: E402
from app.integrations import claude, fathom, google, stripe_api  # noqa: E402
from app.main import app  # noqa: E402
from app.worker import WorkerSettings  # noqa: E402
from tests.fake_fathom import FakeClaude, FakeFathom, meeting  # noqa: E402
from tests.fake_google import FakeGoogle  # noqa: E402
from tests.fake_stripe import FakeStripe, event  # noqa: E402

google.set_transport(FakeGoogle(email="agenda.demo@gmail.com").transport())
fathom.set_transport(FakeFathom([meeting(9000 + i, f"2026-10-{5 + 3 * i:02d}T14:00:00Z", "line.martin@example.com")
                                 for i in range(4)]).transport())
claude.set_transport(FakeClaude().transport())
STRIPE = FakeStripe()
stripe_api.set_transport(STRIPE.transport())
_inner = app.router.lifespan_context


async def _publish_plans() -> None:
    """Le Stripe simulé repart vide à chaque démarrage : les formules y sont republiées automatiquement."""
    from sqlalchemy import text

    from app.db.session import platform_session
    from app.services import billing
    async with platform_session() as s:
        await s.execute(text("update plans set stripe_product_id = null, stripe_price_id = null, stripe_price_cents = null"))
        await s.execute(text("update platform_settings set value = jsonb_set(value, '{stripe_tax_rate_id}', 'null') where key = 'vat'"))
        await billing.sync_plans(s)


@asynccontextmanager
async def lifespan(a):
    w = Worker(functions=WorkerSettings.functions, redis_pool=await queue.pool(), queue_name=queue.QUEUE,
               handle_signals=False, poll_delay=0.3)
    task = asyncio.create_task(w.async_run())
    await _publish_plans()
    async with _inner(a):
        yield
    task.cancel()

app.router.lifespan_context = lifespan


DEMO = "/api/v1/demo"  # sous /api : relayé par l'interface comme le reste de l'API
_base = os.environ.get("HC_PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/")

# Google : l'écran de consentement est remplacé par un retour immédiat « accepté ».
google.authorization_url = lambda state, login_hint=None: f"{_base}{DEMO}/google/consent?{urlencode({'state': state})}"

# Stripe : la page de paiement et le portail client sont simulés localement.
_route = STRIPE._route


def _route_demo(m, path, f):
    out = _route(m, path, f)
    if path == "/checkout/sessions" and out:
        out["url"] = f"{_base}{DEMO}/stripe/checkout/{out['id']}"
    if path == "/billing_portal/sessions" and out:
        out["url"] = f"{_base}/facturation?paiement=portail"
    return out


STRIPE._route = _route_demo


@app.get(f"{DEMO}/google/consent", include_in_schema=False)
async def demo_google(state: str) -> RedirectResponse:
    return RedirectResponse(f"/api/v1/integrations/google/callback?{urlencode({'state': state, 'code': 'code-ok'})}", status_code=303)


@app.get(f"{DEMO}/stripe/checkout/{{session_id}}", include_in_schema=False)
async def demo_checkout(session_id: str) -> RedirectResponse:
    """Paiement simulé : l'abonnement est créé et notifié par un webhook signé, exactement comme avec Stripe."""
    sess = next(x for x in STRIPE.sessions if x["id"] == session_id)
    f = sess["params"]
    org_id, plan = f["subscription_data[metadata][org_id]"], f["subscription_data[metadata][plan_code]"]
    trial = int(f.get("subscription_data[trial_period_days]", "0") or 0)
    await demo_complete(org_id, plan=plan, trial_days=trial)
    return RedirectResponse("/facturation?paiement=ok", status_code=303)


async def demo_complete(org_id: str, plan: str = "pro", trial_days: int = 30) -> dict:
    """Abonnement créé puis webhook signé envoyé à l'API, comme en production."""
    import httpx
    customer = next(c for c, v in STRIPE.customers.items() if v["metadata"]["org_id"] == org_id)
    price = next(p for p in STRIPE.prices.values() if p["metadata"]["plan_code"] == plan and p["active"])
    sub = STRIPE.subscription(org_id, customer, price["id"], status="trialing" if trial_days else "active", trial_days=trial_days)
    raw, headers = event("customer.subscription.created", sub)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as c:
        r = await c.post("/api/v1/integrations/stripe/webhook", content=raw, headers=headers)
    return {"status": r.status_code, "subscription": sub["id"]}


@app.post(f"{DEMO}/stripe/complete/{{org_id}}", include_in_schema=False)
async def demo_complete_route(org_id: str, plan: str = "pro", trial_days: int = 30) -> dict:
    return await demo_complete(org_id, plan, trial_days)


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
