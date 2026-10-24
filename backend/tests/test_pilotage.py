"""Pilotage : indicateurs exacts sur un jeu de données connu, périmètre coach, export CSV, vue plateforme agrégée."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.session import tenant_session
from tests.conftest import auth, login, make_org

API = "/api/v1"


async def test_indicators_scope_and_export(client, admin_token):
    slug = f"pi-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, slug, f"admin@{slug}.example.com")
    h = auth(await login(client, f"admin@{slug}.example.com"))
    await client.post(f"{API}/members", headers=h, json={"email": f"coach@{slug}.example.com", "full_name": "Coach Deux",
                                                         "password": "Motdepasse-solide-1", "role": "coach"})
    hc = auth(await login(client, f"coach@{slug}.example.com"))
    oid = uuid.UUID(o["id"])
    now = datetime.now(UTC)
    async with tenant_session(org_id=oid, user_id=None) as s:
        role = text("select user_id from memberships where role = :r")
        admin_id = (await s.execute(role, {"r": "org_admin"})).scalar_one()
        coach_id = (await s.execute(role, {"r": "coach"})).scalar_one()
        cid = (await s.execute(text("insert into contacts (id, kind, first_name, last_name, pipeline_stage, owner_id) values "
                                    "(gen_random_uuid(), 'coachee', 'A', 'A', 'en_cours', :o) returning id"), {"o": admin_id})).scalar_one()
        await s.execute(text("insert into contacts (id, kind, first_name, last_name, pipeline_stage, owner_id) values "
                             "(gen_random_uuid(), 'coachee', 'B', 'B', 'prospect', :o)"), {"o": coach_id})
        ins = text("insert into appointments (id, coach_id, contact_id, title, attendee_name, attendee_email, timezone, starts_at, ends_at, "
                   "busy_from, busy_to, manage_token_hash, manage_token_enc, status, source, cancelled_at) values (gen_random_uuid(), :co, :c, 't', 'n', "
                   "'e@x.fr', 'UTC', :a, :b, :a, :b, md5(random()::text), 'x', :st, :src, :ca)")
        for i, (co, st, src) in enumerate(((admin_id, "confirmed", "public"), (admin_id, "confirmed", "staff"),
                                           (coach_id, "confirmed", "public"), (admin_id, "cancelled", "public"))):
            a = now - timedelta(days=3 + i, hours=2)
            await s.execute(ins, {"co": co, "c": cid, "a": a, "b": a + timedelta(minutes=90), "st": st, "src": src,
                                  "ca": now - timedelta(days=1) if st == "cancelled" else None})
        a = now + timedelta(days=5)  # à venir
        await s.execute(ins, {"co": admin_id, "c": cid, "a": a, "b": a + timedelta(hours=1), "st": "confirmed", "src": "public", "ca": None})
        await s.execute(text("insert into tasks (id, contact_id, title, done_at) values (gen_random_uuid(), :c, 'x', now()), "
                             "(gen_random_uuid(), :c, 'y', null)"), {"c": cid})
    d = (await client.get(f"{API}/pilotage?days=30", headers=h)).json()
    assert d["scope"] == "organisme" and d["can_see_all"]
    assert d["sessions"] == {"done": 3, "hours": 4.5, "cancelled": 1, "cancel_rate": 25.0, "online_share": 66.7, "upcoming_28d": 1}
    assert d["coachees"]["active"] == 1 and d["tasks"] == {"created": 2, "done": 1, "open": 1}
    assert {x["stage"]: x["count"] for x in d["pipeline"]["stages"]}["en_cours"] == 1 and d["pipeline"]["conversion_rate"] == 50.0
    assert sum(w["sessions"] for w in d["weekly"]) == 3 and len(d["weekly"]) == 12
    assert {c["coach"]: c["sessions"] for c in d["by_coach"]}["Coach Deux"] == 1
    # un coach ne voit que son activité, même s'il demande tout l'organisme
    mine = (await client.get(f"{API}/pilotage?days=30&scope=all", headers=hc)).json()
    assert mine["scope"] == "coach" and mine["sessions"]["done"] == 1 and mine["by_coach"] == [] and not mine["can_see_all"]
    r = await client.get(f"{API}/pilotage/export.csv?days=30", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    body = r.content.decode("utf-8-sig")
    assert "Séances réalisées;3" in body and "Heures d'accompagnement;4,5" in body and "Coach Deux;1;1,5" in body
    # vue plateforme : comptages seulement
    p = (await client.get(f"{API}/admin/pilotage?days=30", headers=admin_token)).json()
    row = next(u for u in p["usage"] if u["organization"] == o["name"])
    assert row["sessions"] == 3 and row["coaches"] == 2 and "mrr_cents" in p
    assert (await client.get(f"{API}/admin/pilotage", headers=h)).status_code == 401
