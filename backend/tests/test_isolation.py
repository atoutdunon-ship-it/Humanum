"""Le cloisonnement entre organismes est imposé par PostgreSQL, pas seulement par l'API."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db.session import tenant_session
from tests.conftest import auth, login, make_org


@pytest.fixture(scope="module")
async def orgs(client, admin_token):
    a = await make_org(client, "alpha", "admin@alpha.example.com")
    b = await make_org(client, "beta", "admin@beta.example.com")
    return {"a": a, "b": b, "ta": await login(client, "admin@alpha.example.com"), "tb": await login(client, "admin@beta.example.com")}


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_app_role_cannot_bypass_rls():
    async with tenant_session(org_id=None, user_id=None) as s:
        su, bypass = (await s.execute(text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user"))).one()
    assert (su, bypass) == (False, False)


async def test_contact_invisible_to_other_org(client, orgs):
    r = await client.post("/api/v1/contacts", headers=auth(orgs["ta"]),
                          json={"first_name": "Joanne", "last_name": "TEST", "status": "porteur de projet"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["organization_id"] == orgs["a"]["id"]

    assert [c["id"] for c in (await client.get("/api/v1/contacts", headers=auth(orgs["ta"]))).json()] == [cid]
    assert (await client.get("/api/v1/contacts", headers=auth(orgs["tb"]))).json() == []
    assert (await client.get(f"/api/v1/contacts/{cid}", headers=auth(orgs["tb"]))).status_code == 404
    r = await client.patch(f"/api/v1/contacts/{cid}", headers=auth(orgs["tb"]), json={"first_name": "X", "last_name": "Y"})
    assert r.status_code == 404


async def test_database_refuses_cross_org_write_even_without_api(orgs):
    # Contexte B, tentative d'écrire une ligne étiquetée A directement en SQL
    with pytest.raises((DBAPIError, ProgrammingError)) as e:
        async with tenant_session(org_id=uuid.UUID(orgs["b"]["id"]), user_id=None) as s:
            await s.execute(text("insert into contacts (id, organization_id, kind, first_name, last_name, pipeline_stage) "
                                 "values (gen_random_uuid(), :a, 'coachee', 'X', 'Y', 'prospect')"), {"a": orgs["a"]["id"]})
    assert "row-level security" in str(e.value)


async def test_no_context_sees_nothing(orgs):
    async with tenant_session(org_id=None, user_id=None) as s:
        assert (await s.execute(text("select count(*) from contacts"))).scalar() == 0
        assert (await s.execute(text("select count(*) from organizations"))).scalar() == 0


async def test_audit_log_is_append_only(orgs):
    with pytest.raises((DBAPIError, ProgrammingError)) as e:
        async with tenant_session(org_id=uuid.UUID(orgs["a"]["id"]), user_id=None) as s:
            await s.execute(text("delete from audit_logs"))
    assert "permission denied" in str(e.value)


async def test_slug_conflict(client, orgs, admin_token):
    r = await client.post("/api/v1/admin/organizations", headers=admin_token,
                          json={"name": "Doublon", "slug": "alpha", "admin_email": "x@x.example.com",
                                "admin_full_name": "X X", "admin_password": "Motdepasse-solide-1"})
    assert r.status_code == 409
