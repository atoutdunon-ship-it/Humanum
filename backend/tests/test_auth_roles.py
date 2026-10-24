import pytest

from tests.conftest import auth, login, make_org


@pytest.fixture(scope="module")
async def ctx(client, admin_token):
    a = await make_org(client, "gamma", "admin@gamma.example.com")
    b = await make_org(client, "delta", "admin@delta.example.com")
    ta = await login(client, "admin@gamma.example.com")
    tb = await login(client, "admin@delta.example.com")
    pwd = "Motdepasse-solide-1"
    for role, email in (("coach", "coach@gamma.example.com"), ("coachee", "coache@gamma.example.com")):
        r = await client.post("/api/v1/members", headers=auth(ta),
                              json={"email": email, "full_name": role, "password": pwd, "role": role})
        assert r.status_code == 201, r.text
    # un même compte membre de deux organismes
    r = await client.post("/api/v1/members", headers=auth(tb),
                          json={"email": "coach@gamma.example.com", "full_name": "coach", "password": pwd, "role": "coordinator"})
    assert r.status_code == 201, r.text
    return {"a": a, "b": b, "ta": ta, "tb": tb}


async def test_bad_credentials(client, ctx):
    for email, pwd in (("admin@gamma.example.com", "mauvais-mot-de-passe"), ("inconnu@gamma.example.com", "Motdepasse-solide-1")):
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
        assert r.status_code == 401 and r.json()["detail"] == "Identifiants invalides"


async def test_multi_org_login_requires_choice(client, ctx):
    t = await login(client, "coach@gamma.example.com")
    assert t.get("access_token") is None and len(t["memberships"]) == 2 and t["select_token"]
    tb = await login(client, "coach@gamma.example.com", org=ctx["b"]["id"])
    assert tb["role"] == "coordinator" and tb["organization_id"] == ctx["b"]["id"]
    # choix de l'organisme sans ressaisir le mot de passe
    r = await client.post("/api/v1/auth/select-org", json={"select_token": t["select_token"], "organization_id": ctx["a"]["id"]})
    assert r.status_code == 200 and r.json()["role"] == "coach"
    # le jeton d'étape n'ouvre ni l'API métier ni un organisme dont on n'est pas membre
    assert (await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {t['select_token']}"})).status_code == 401
    foreign = await make_org(client, "theta", "admin@theta.example.com")
    r = await client.post("/api/v1/auth/select-org", json={"select_token": t["select_token"], "organization_id": foreign["id"]})
    assert r.status_code == 403


async def test_member_list_shows_only_current_org(client, ctx):
    emails = [m["email"] for m in (await client.get("/api/v1/members", headers=auth(ctx["ta"]))).json()]
    assert sorted(emails) == ["admin@gamma.example.com", "coach@gamma.example.com", "coache@gamma.example.com"]


async def test_roles(client, ctx):
    coach = await login(client, "coach@gamma.example.com", org=ctx["a"]["id"])
    coachee = await login(client, "coache@gamma.example.com")
    assert (await client.post("/api/v1/contacts", headers=auth(coach), json={"first_name": "A", "last_name": "B"})).status_code == 201
    assert (await client.get("/api/v1/contacts", headers=auth(coachee))).status_code == 403
    assert (await client.post("/api/v1/members", headers=auth(coach),
                              json={"email": "z@z.example.com", "full_name": "Z Z", "password": "Motdepasse-solide-1",
                                    "role": "org_admin"})).status_code == 403


async def test_token_required(client, ctx):
    assert (await client.get("/api/v1/contacts")).status_code == 401
    assert (await client.get("/api/v1/contacts", headers={"Authorization": "Bearer faux"})).status_code == 401
