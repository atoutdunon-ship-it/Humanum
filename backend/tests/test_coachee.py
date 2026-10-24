"""Espace coaché : invitation, cloisonnement strict entre coachés, partage explicite, actions de parcours."""
import uuid

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from tests.conftest import auth, login, make_org

API = "/api/v1"
PWD = "Mon-espace-2026"


@pytest.fixture
async def org(client, admin_token):
    slug = f"ec-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, slug, f"coach@{slug}.example.com")
    h = auth(await login(client, f"coach@{slug}.example.com"))
    contacts = []
    for fn in ("Line", "Willy"):
        r = await client.post(f"{API}/contacts", headers=h, json={"first_name": fn, "last_name": "TEST",
                                                                  "email": f"{fn.lower()}@{slug}.example.com"})
        contacts.append(r.json())
    return {"o": o, "slug": slug, "h": h, "a": contacts[0], "b": contacts[1]}


async def _join(client, h, contact) -> dict:
    r = await client.post(f"{API}/contacts/{contact['id']}/invite", headers=h)
    assert r.status_code == 201, r.text
    token = r.json()["invite_url"].rsplit("/", 1)[-1]
    info = (await client.get(f"{API}/public/invitations/{token}")).json()
    assert info["email"] == contact["email"] and info["existing_account"] is False
    assert (await client.post(f"{API}/public/invitations/{token}/accept", json={"password": "court"})).status_code == 422
    assert (await client.post(f"{API}/public/invitations/{token}/accept", json={"password": PWD})).status_code == 200
    assert (await client.post(f"{API}/public/invitations/{token}/accept", json={"password": PWD})).status_code == 410  # usage unique
    t = await login(client, contact["email"], password=PWD)
    assert t["role"] == "coachee" and not t.get("mfa_setup_required")  # double authentification facultative
    return auth(t)


async def test_invitation_and_strict_scope(client, org):
    ha = await _join(client, org["h"], org["a"])
    assert (await client.get(f"{API}/contacts/{org['a']['id']}/space", headers=org["h"])).json()["status"] == "active"
    me = (await client.get(f"{API}/me", headers=ha)).json()
    assert me["first_name"] == "Line" and me["coach_name"]
    for path in ("/contacts", f"/contacts/{org['a']['id']}", "/fathom/meetings", "/booking/appointments", "/syntheses",
                 "/cohorts", "/members", f"/contacts/{org['a']['id']}/tasks"):
        assert (await client.get(f"{API}{path}", headers=ha)).status_code == 403, path  # aucune route de l'équipe
    assert (await client.get(f"{API}/me", headers=org["h"])).status_code == 403          # et réciproquement


async def test_tasks_visibility_and_isolation_between_coachees(client, org):
    ha, hb = await _join(client, org["h"], org["a"]), await _join(client, org["h"], org["b"])
    r = await client.post(f"{API}/contacts/{org['a']['id']}/tasks", headers=org["h"], json={"title": "Finaliser la grille tarifaire"})
    visible = r.json()["id"]
    await client.post(f"{API}/contacts/{org['a']['id']}/tasks", headers=org["h"], json={"title": "Note interne du coach",
                                                                                     "visible_to_coachee": False})
    mine = (await client.get(f"{API}/me/tasks", headers=ha)).json()
    assert [t["title"] for t in mine] == ["Finaliser la grille tarifaire"]
    assert (await client.patch(f"{API}/me/tasks/{visible}", headers=hb, json={"done": True})).status_code == 404  # pas la sienne
    assert (await client.get(f"{API}/me/tasks", headers=hb)).json() == []
    assert (await client.patch(f"{API}/me/tasks/{visible}", headers=ha, json={"done": True})).status_code == 200
    staff = (await client.get(f"{API}/contacts/{org['a']['id']}/tasks", headers=org["h"])).json()
    assert next(t for t in staff if t["id"] == visible)["done"] is True


async def test_sharing_is_explicit_and_never_includes_transcript(client, org):
    ha = await _join(client, org["h"], org["a"])
    oid, cid = uuid.UUID(org["o"]["id"]), org["a"]["id"]
    async with tenant_session(org_id=oid, user_id=None) as s:
        coach = (await s.execute(text("select owner_id from contacts where id = :c"), {"c": cid})).scalar_one()
        mid = (await s.execute(text(
            "insert into meetings (id, recording_id, coach_id, contact_id, title, started_at, summary_md, action_items, transcript) "
            "values (gen_random_uuid(), 42, :co, :c, 'Séance 1', now(), '## Résumé\nPremier client visé', "
            "'[{\"description\": \"Appeler la CCI\", \"completed\": false}]', '[{\"speaker\": \"Line\", \"text\": \"confidentiel\"}]') "
            "returning id"), {"co": coach, "c": cid})).scalar_one()
        sid = (await s.execute(text("insert into syntheses (id, kind, contact_id, title, status, content_md) values "
                                    "(gen_random_uuid(), 'parcours', :c, 'Synthèse de parcours — Line TEST', 'done', '## Progrès') returning id"),
                               {"c": cid})).scalar_one()
        co = (await s.execute(text("insert into cohorts (id, name) values (gen_random_uuid(), 'G') returning id"))).scalar_one()
        gid = (await s.execute(text("insert into syntheses (id, kind, cohort_id, title, status) values "
                                    "(gen_random_uuid(), 'groupe', :g, 'Groupe', 'done') returning id"), {"g": co})).scalar_one()
    assert (await client.get(f"{API}/me/sessions", headers=ha)).json() == []
    assert (await client.get(f"{API}/me/documents", headers=ha)).json() == []
    assert (await client.post(f"{API}/fathom/meetings/{mid}/share", headers=org["h"], json={"shared": True})).status_code == 200
    assert (await client.post(f"{API}/syntheses/{sid}/share", headers=org["h"], json={"shared": True})).status_code == 200
    assert (await client.post(f"{API}/syntheses/{gid}/share", headers=org["h"], json={"shared": True})).status_code == 409
    sessions = (await client.get(f"{API}/me/sessions", headers=ha)).json()
    assert len(sessions) == 1 and "transcript" not in sessions[0] and "confidentiel" not in str(sessions)
    assert [d["title"] for d in (await client.get(f"{API}/me/documents", headers=ha)).json()] == ["Synthèse de parcours — Line TEST"]
    created = (await client.post(f"{API}/fathom/meetings/{mid}/tasks", headers=org["h"])).json()
    assert [t["title"] for t in created] == ["Appeler la CCI"]
    assert (await client.post(f"{API}/fathom/meetings/{mid}/tasks", headers=org["h"])).json() == []  # sans doublon


async def test_revocation_existing_account_and_expiry(client, org):
    await _join(client, org["h"], org["a"])
    assert (await client.delete(f"{API}/contacts/{org['a']['id']}/space", headers=org["h"])).status_code == 204
    r = await client.post(f"{API}/auth/login", json={"email": org["a"]["email"], "password": PWD})
    assert r.status_code == 401  # plus aucun accès à cet organisme
    # nouvelle invitation pour un compte déjà existant : son mot de passe actuel est exigé
    token = (await client.post(f"{API}/contacts/{org['a']['id']}/invite", headers=org["h"])).json()["invite_url"].rsplit("/", 1)[-1]
    assert (await client.get(f"{API}/public/invitations/{token}")).json()["existing_account"] is True
    assert (await client.post(f"{API}/public/invitations/{token}/accept", json={"password": "Autre-mot-2026"})).status_code == 401
    assert (await client.post(f"{API}/public/invitations/{token}/accept", json={"password": PWD})).status_code == 200
    # invitation expirée
    tok_b = (await client.post(f"{API}/contacts/{org['b']['id']}/invite", headers=org["h"])).json()["invite_url"].rsplit("/", 1)[-1]
    async with tenant_session(org_id=uuid.UUID(org["o"]["id"]), user_id=None) as s:
        await s.execute(text("update invitations set expires_at = now() - interval '1 minute' where accepted_at is null"))
    assert (await client.get(f"{API}/public/invitations/{tok_b}")).status_code == 410
    assert (await client.get(f"{API}/public/invitations/{'z' * 43}")).status_code == 404
