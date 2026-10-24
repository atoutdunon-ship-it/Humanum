"""Panneau d'administration : changement de mot de passe imposé, édition des tarifs, options et textes,
séparation stricte entre super-administration et données de coaching."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db.session import platform_session
from tests.conftest import auth, login, make_org


async def test_initial_password_forces_change(client):
    from app.cli import create_superadmin
    await create_superadmin("SECOND", "court-1", reset=False)
    t = (await client.post("/api/v1/admin/auth/login", json={"username": "second", "password": "court-1"})).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert t["must_change_password"] is True
    assert (await client.get("/api/v1/admin/plans", headers=h)).status_code == 403
    r = await client.post("/api/v1/admin/auth/change-password", headers=h,
                          json={"current_password": "court-1", "new_password": "trop-court"})
    assert r.status_code == 422  # politique : 12 caractères, lettres et chiffres
    r = await client.post("/api/v1/admin/auth/change-password", headers=h,
                          json={"current_password": "court-1", "new_password": "Second-admin-2026"})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert (await client.get("/api/v1/admin/plans", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).status_code == 200


async def test_plans_are_editable_and_public(client, admin_token):
    plans = {p["code"]: p for p in (await client.get("/api/v1/admin/plans", headers=admin_token)).json()}
    assert [plans[c]["monthly_price_cents"] for c in ("starter", "pro", "entreprise")] == [5900, 8900, 14900]
    r = await client.patch("/api/v1/admin/plans/pro", headers=admin_token, json={"monthly_price_cents": 9900, "trial_days": 15})
    assert r.status_code == 200 and r.json()["monthly_price_cents"] == 9900
    pub = {p["code"]: p for p in (await client.get("/api/v1/public/plans")).json()}
    assert pub["pro"]["monthly_price_cents"] == 9900 and pub["pro"]["trial_days"] == 15
    await client.patch("/api/v1/admin/plans/pro", headers=admin_token, json={"monthly_price_cents": 8900, "trial_days": 30})
    r = await client.post("/api/v1/admin/plans", headers=admin_token,
                          json={"code": "essai", "label": "Essai", "monthly_price_cents": 0, "is_public": False})
    assert r.status_code == 201
    assert "essai" not in {p["code"] for p in (await client.get("/api/v1/public/plans")).json()}
    assert (await client.delete("/api/v1/admin/plans/essai", headers=admin_token)).status_code == 204


async def test_texts_are_editable_and_served(client, admin_token):
    r = await client.put("/api/v1/admin/texts", headers=admin_token,
                         json={"key": "login.title", "locale": "fr", "value": "Bienvenue sur HUMANUM"})
    assert r.status_code == 200
    assert (await client.get("/api/v1/public/texts?locale=fr")).json()["login.title"] == "Bienvenue sur HUMANUM"


async def test_feature_override_applies_to_org(client, admin_token):
    org = await make_org(client, "epsilon", "admin@epsilon.example.com")
    tok = await login(client, "admin@epsilon.example.com")
    assert (await client.get("/api/v1/features", headers=auth(tok))).json()["funder_portal"] is False
    r = await client.put(f"/api/v1/admin/organizations/{org['id']}/features/funder_portal", headers=admin_token, json={"enabled": True})
    assert r.status_code == 200
    assert (await client.get("/api/v1/features", headers=auth(tok))).json()["funder_portal"] is True
    r = await client.patch(f"/api/v1/admin/organizations/{org['id']}", headers=admin_token, json={"plan": "inexistant"})
    assert r.status_code == 422


async def test_access_management(client, admin_token):
    await make_org(client, "zeta", "admin@zeta.example.com")
    users = (await client.get("/api/v1/admin/users?q=zeta", headers=admin_token)).json()
    uid = users[0]["id"]
    assert (await client.patch(f"/api/v1/admin/users/{uid}", headers=admin_token, json={"is_active": False})).status_code == 200
    r = await client.post("/api/v1/auth/login", json={"email": "admin@zeta.example.com", "password": "Motdepasse-solide-1"})
    assert r.status_code == 401  # compte suspendu
    await client.patch(f"/api/v1/admin/users/{uid}", headers=admin_token, json={"is_active": True})
    assert (await client.post(f"/api/v1/admin/users/{uid}/reset-password", headers=admin_token,
                              json={"new_password": "Reinitialise-2026"})).status_code == 204
    await login(client, "admin@zeta.example.com", password="Reinitialise-2026")


async def test_tokens_do_not_cross(client, admin_token):
    await make_org(client, "eta", "admin@eta.example.com")
    tenant = await login(client, "admin@eta.example.com")
    assert (await client.get("/api/v1/admin/plans", headers=auth(tenant))).status_code == 401
    assert (await client.get("/api/v1/contacts", headers=admin_token)).status_code == 401


async def test_platform_role_cannot_read_coaching_data(admin_token):
    with pytest.raises((DBAPIError, ProgrammingError)) as e:
        async with platform_session() as s:
            await s.execute(text("select * from contacts"))
    assert "permission denied" in str(e.value)


async def test_admin_login_rate_limited_and_audited(client, admin_token):
    for _ in range(5):
        r = await client.post("/api/v1/admin/auth/login", json={"username": "ADMIN", "password": "faux"})
        assert r.status_code == 401
    r = await client.post("/api/v1/admin/auth/login", json={"username": "ADMIN", "password": "faux"})
    assert r.status_code == 429
    actions = [a["action"] for a in (await client.get("/api/v1/admin/audit", headers=admin_token)).json()]
    assert "admin.login_failed" in actions and "plan.update" in actions and "text.upsert" in actions
