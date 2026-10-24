"""Fathom (connexion, import, webhooks signés) et synthèses en un clic (Claude simulé, pseudonymisation, quotas)."""
import json
import os
import time
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import tenant_session
from app.integrations import claude, fathom
from tests.conftest import auth, login, make_org
from tests.fake_fathom import KEY, FakeClaude, FakeFathom, meeting

API = "/api/v1"
INVITEE = "line.martin@example.com"


@pytest.fixture
async def env(client, admin_token, monkeypatch):
    items = [meeting(1000 + i, f"2026-10-0{i + 1}T14:00:00Z", INVITEE) for i in range(3)]
    fa, cl = FakeFathom(items), FakeClaude()
    fathom.set_transport(fa.transport())
    claude.set_transport(cl.transport())
    monkeypatch.setattr(get_settings(), "anthropic_api_key", type(get_settings().jwt_secret)("sk-ant-test"))
    slug = f"fa-{uuid.uuid4().hex[:6]}"
    org = await make_org(client, slug, f"coach@{slug}.example.com")
    h = auth(await login(client, f"coach@{slug}.example.com"))
    r = await client.post(f"{API}/contacts", headers=h, json={"first_name": "Line", "last_name": "MARTIN", "email": INVITEE,
                                                              "status": "porteur de projet"})
    assert r.status_code == 201
    yield {"fa": fa, "cl": cl, "org": org, "slug": slug, "h": h, "contact": r.json()}
    fathom.set_transport(None)
    claude.set_transport(None)
    rd = Redis.from_url(os.environ["HC_REDIS_URL"])
    await rd.flushdb()
    await rd.aclose()


async def _connect(client, env) -> str:
    r = await client.post(f"{API}/fathom/connect", headers=env["h"], json={"api_key": "mauvaise-cle-123"})
    assert r.status_code == 422
    r = await client.post(f"{API}/fathom/connect", headers=env["h"], json={"api_key": KEY, "fathom_email": "coach@fathom.example.com"})
    assert r.status_code == 200, r.text
    hook = next(iter(env["fa"].hooks.values()))
    assert hook["triggered_for"] == ["my_recordings"] and hook["include_transcript"] and hook["include_summary"]
    return hook["url"].rsplit("/", 1)[-1], hook["secret"]


def _signed(secret: str, body: dict, ts: int | None = None, msg: str = "msg_1") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = ts or int(time.time())
    return raw, {"webhook-id": msg, "webhook-timestamp": str(ts), "webhook-signature": fathom.sign(secret, msg, ts, raw),
                 "content-type": "application/json"}


def test_standard_webhooks_signature():
    import base64
    secret = "whsec_" + base64.b64encode(b"cle-secrete-de-test-24oct").decode()
    raw, h = _signed(secret, {"a": 1})
    assert fathom.verify_webhook(secret, h, raw)
    assert not fathom.verify_webhook(secret, h, raw + b" ")                                       # corps altéré
    assert not fathom.verify_webhook(secret, {**h, "webhook-id": "autre"}, raw)                    # identifiant altéré
    assert not fathom.verify_webhook(secret, h, raw, now=time.time() + 600)                         # trop ancien
    assert fathom.verify_webhook(secret, {**h, "webhook-signature": "v1,AAAA " + h["webhook-signature"]}, raw)  # rotation de clés


async def test_connect_import_and_match(client, env):
    await _connect(client, env)
    from app.worker import import_fathom
    async with tenant_session(org_id=uuid.UUID(env["org"]["id"]), user_id=None) as s:
        acc_id = (await s.execute(text("select id from fathom_accounts"))).scalar_one()
    assert await import_fathom({"job_try": 1}, env["org"]["id"], str(acc_id)) == 3  # 2 pages suivies
    ms = (await client.get(f"{API}/fathom/meetings", params={"contact_id": env["contact"]["id"]}, headers=env["h"])).json()
    assert len(ms) == 3 and all(m["contact_id"] == env["contact"]["id"] for m in ms) and "transcript" not in ms[0]
    full = (await client.get(f"{API}/fathom/meetings/{ms[0]['id']}", headers=env["h"])).json()
    assert full["transcript"][0]["text"] == "Je veux lancer mon activité."
    st = (await client.get(f"{API}/fathom", headers=env["h"])).json()
    assert st["connected"] and st["meetings"] == 3 and st["last_sync_at"]
    # import relancé : aucun doublon
    assert await import_fathom({"job_try": 1}, env["org"]["id"], str(acc_id)) >= 0
    assert len((await client.get(f"{API}/fathom/meetings", headers=env["h"])).json()) == 3


async def test_signed_webhook_is_idempotent_and_isolated(client, env, admin_token):
    token, secret = await _connect(client, env)
    url = f"{API}/integrations/fathom/webhook/{token}"
    payload = meeting(7777, "2026-10-20T15:00:00Z", INVITEE)
    raw, h = _signed(secret, payload)
    assert (await client.post(url, content=raw, headers=h)).status_code == 204
    assert (await client.post(url, content=raw, headers={**h, "webhook-id": "msg_2",
                                                          "webhook-signature": fathom.sign(secret, "msg_2", int(h["webhook-timestamp"]), raw)})).status_code == 204
    ms = (await client.get(f"{API}/fathom/meetings", headers=env["h"])).json()
    assert [m["recording_id"] for m in ms] == [7777] and ms[0]["contact_id"] == env["contact"]["id"]
    bad = {**h, "webhook-signature": "v1," + "A" * 44}
    assert (await client.post(url, content=raw, headers=bad)).status_code == 401
    old_raw, old_h = _signed(secret, payload, ts=int(time.time()) - 3600)
    assert (await client.post(url, content=old_raw, headers=old_h)).status_code == 401
    assert (await client.post(f"{API}/integrations/fathom/webhook/{'x' * 43}", content=raw, headers=h)).status_code == 404
    # un autre organisme ne voit rien
    other = f"fb-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, other, f"coach@{other}.example.com")
    h2 = auth(await login(client, f"coach@{other}.example.com"))
    assert (await client.get(f"{API}/fathom/meetings", headers=h2)).json() == []
    async with tenant_session(org_id=uuid.UUID(o["id"]), user_id=None) as s:
        for t in ("meetings", "fathom_accounts", "cohorts", "cohort_members", "syntheses"):
            assert (await s.execute(text(f"select count(*) from {t}"))).scalar() == 0, t  # noqa: S608
    # déconnexion : webhook supprimé chez Fathom, réunions conservées
    assert (await client.delete(f"{API}/fathom", headers=env["h"])).status_code == 204
    assert env["fa"].hooks == {}
    assert len((await client.get(f"{API}/fathom/meetings", headers=env["h"])).json()) == 1
    assert (await client.post(url, content=raw, headers=h)).status_code == 404


async def test_coach_sees_only_own_meetings(client, env):
    token, secret = await _connect(client, env)
    raw, h = _signed(secret, meeting(5555, "2026-10-21T15:00:00Z", INVITEE))
    await client.post(f"{API}/integrations/fathom/webhook/{token}", content=raw, headers=h)
    r = await client.post(f"{API}/members", headers=env["h"], json={"email": f"coach2@{env['slug']}.example.com", "full_name": "Coach 2",
                                                                    "password": "Motdepasse-solide-1", "role": "coach"})
    assert r.status_code == 201
    h2 = auth(await login(client, f"coach2@{env['slug']}.example.com"))
    assert (await client.get(f"{API}/fathom/meetings", headers=h2)).json() == []  # secret de l'accompagnement


async def _generate(client, env, body: dict) -> dict:
    r = await client.post(f"{API}/syntheses", headers=env["h"], json=body)
    assert r.status_code == 202, r.text
    from app.worker import generate_synthesis
    assert await generate_synthesis({"job_try": 1}, env["org"]["id"], r.json()["id"]) == "done"
    return (await client.get(f"{API}/syntheses/{r.json()['id']}", headers=env["h"])).json()


async def test_parcours_synthesis_is_pseudonymized(client, env):
    token, secret = await _connect(client, env)
    for i in (1, 2):
        raw, h = _signed(secret, meeting(8000 + i, f"2026-10-0{i}T14:00:00Z", INVITEE), msg=f"m{i}")
        await client.post(f"{API}/integrations/fathom/webhook/{token}", content=raw, headers=h)
    x = await _generate(client, env, {"kind": "parcours", "contact_id": env["contact"]["id"]})
    sent = env["cl"].prompts[-1]
    prompt = sent["messages"][0]["content"]
    assert "Line" not in prompt and "MARTIN" not in prompt and INVITEE not in prompt  # jamais de nom réel envoyé
    assert "[COACHÉ 1]" in prompt and "Séance du 01/10/2026" in prompt and "N'invente aucun fait" in sent["system"]
    assert x["status"] == "done" and "Line MARTIN souhaite lancer" in x["content_md"]   # noms restaurés
    assert x["input_tokens"] == 1234 and len(x["sources"]["meetings"]) == 2
    assert x["title"].startswith("Synthèse de parcours — Line MARTIN")


async def test_funder_report_has_computed_indicators_and_quota(client, env, admin_token):
    r = await client.post(f"{API}/cohorts", headers=env["h"], json={"name": "SOLO BOOST — Groupe 2", "program": "SOLO BOOST",
                                                                    "funder": "CTM / FSE+"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert (await client.put(f"{API}/cohorts/{cid}/members/{env['contact']['id']}", headers=env["h"])).status_code == 204
    assert (await client.put(f"{API}/cohorts/{cid}/members/{env['contact']['id']}", headers=env["h"])).status_code == 204  # idempotent
    assert (await client.get(f"{API}/cohorts/{cid}", headers=env["h"])).json()["members"] == 1
    x = await _generate(client, env, {"kind": "financeur", "cohort_id": cid})
    prompt = env["cl"].prompts[-1]["messages"][0]["content"]
    assert "Indicateurs calculés : participants 1" in prompt and "MARTIN" not in prompt
    assert "| Line MARTIN | 0 | 0 | 0.0 |" in x["content_md"]  # annexe calculée par la plateforme, nominative
    # quota : formule à 1 synthèse par mois → la demande suivante est refusée
    code = f"ia{uuid.uuid4().hex[:6]}"
    assert (await client.post(f"{API}/admin/plans", headers=admin_token, json={"code": code, "label": "Essai IA", "monthly_price_cents": 0,
                                                                               "ai_syntheses_per_month": 1, "is_public": False})).status_code == 201
    assert (await client.patch(f"{API}/admin/organizations/{env['org']['id']}", headers=admin_token, json={"plan": code})).status_code == 200
    r = await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "groupe", "cohort_id": cid})
    assert r.status_code == 429
    # IA désactivée par l'organisme, puis option coupée par la plateforme
    await client.patch(f"{API}/admin/organizations/{env['org']['id']}", headers=admin_token, json={"plan": "pro", "ai_enabled": False})
    assert (await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "groupe", "cohort_id": cid})).status_code == 403
    await client.put(f"{API}/admin/organizations/{env['org']['id']}/features/ai_syntheses", headers=admin_token, json={"enabled": False})
    assert (await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "groupe", "cohort_id": cid})).status_code == 404


async def test_claude_overload_is_retried_then_reported(client, env):
    from arq import Retry

    from app.worker import generate_synthesis
    r = await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "parcours", "contact_id": env["contact"]["id"]})
    env["cl"].fail_next = 10
    with pytest.raises(Retry):
        await generate_synthesis({"job_try": 1}, env["org"]["id"], r.json()["id"])
    assert await generate_synthesis({"job_try": 5}, env["org"]["id"], r.json()["id"]) == "error"
    x = (await client.get(f"{API}/syntheses/{r.json()['id']}", headers=env["h"])).json()
    assert x["status"] == "error" and "indisponible" in x["error"]


async def test_synthesis_requires_platform_key(client, env, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    r = await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "parcours", "contact_id": env["contact"]["id"]})
    assert r.status_code == 503
    assert (await client.post(f"{API}/syntheses", headers=env["h"], json={"kind": "parcours"})).status_code == 422
