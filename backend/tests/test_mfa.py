"""Double authentification : enrôlement, connexion en deux étapes, anti-rejeu, codes de secours, réinitialisation."""
import pyotp
import pytest
from sqlalchemy import text

from app.core import mfa
from app.db.session import platform_session
from tests.conftest import auth, login, make_org

API = "/api/v1"


async def _enroll(client, headers: dict, clock, base: str = f"{API}/auth/mfa") -> tuple[str, dict]:
    r = await client.post(f"{base}/setup", headers=headers)
    assert r.status_code == 200, r.text
    setup = r.json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and setup["qr_svg"].startswith("<svg")
    r = await client.post(f"{base}/activate", headers=headers, json={"code": "000000"})
    assert r.status_code == 422
    r = await client.post(f"{base}/activate", headers=headers, json={"code": clock.code(setup["secret"])})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["recovery_codes"]) == 10 and len(set(out["recovery_codes"])) == 10
    return setup["secret"], out


def test_totp_core(clock):
    secret = mfa.new_secret()
    assert len(secret) == 32
    step = mfa.verify_totp(secret, clock.code(secret), None)
    assert step is not None
    assert mfa.verify_totp(secret, clock.code(secret), step) is None  # rejeu refusé
    clock.tick(3)
    assert mfa.verify_totp(secret, pyotp.TOTP(secret).at(clock.t - 90), step) is None  # code trop ancien
    assert mfa.verify_totp(secret, "abcdef", None) is None
    shown, digests = mfa.new_recovery_codes()
    left = mfa.consume_recovery_code(shown[0].lower(), digests)
    assert left is not None and len(left) == 9
    assert mfa.consume_recovery_code(shown[0], left) is None  # usage unique
    assert mfa.decrypt(mfa.encrypt(secret)) == secret


def test_dev_secrets_refused_outside_dev():
    from app.core.config import Settings
    with pytest.raises(ValueError, match="HC_JWT_SECRET"):
        Settings(env="prod")


async def test_staff_must_enroll_then_two_step_login(client, admin_token, enforce_mfa, clock):
    await make_org(client, "mfa-a", "admin@mfa-a.example.com")
    t = await login(client, "admin@mfa-a.example.com")
    assert t["mfa_setup_required"] is True
    restricted = auth(t)
    r = await client.get(f"{API}/contacts", headers=restricted)
    assert r.status_code == 403 and "double authentification" in r.json()["detail"]
    assert (await client.get(f"{API}/auth/mfa", headers=restricted)).json() == \
        {"enabled": False, "enabled_at": None, "required": True, "recovery_codes_remaining": 0}

    secret, out = await _enroll(client, restricted, clock)
    assert (await client.get(f"{API}/contacts", headers=auth(out))).status_code == 200

    # secret chiffré au repos
    async with platform_session() as s:
        enc = (await s.execute(text("select mfa_secret_enc from users where email='admin@mfa-a.example.com'"))).scalar_one()
    assert secret not in enc and mfa.decrypt(enc) == secret

    # connexion : le mot de passe seul ne donne ni jeton ni liste d'organismes
    r = await client.post(f"{API}/auth/login", json={"email": "admin@mfa-a.example.com", "password": "Motdepasse-solide-1"})
    step1 = r.json()
    assert step1["mfa_required"] is True and "access_token" not in step1 and step1["memberships"] == []
    assert (await client.get(f"{API}/contacts", headers={"Authorization": f"Bearer {step1['mfa_token']}"})).status_code == 401
    same = clock.code(secret)  # code déjà consommé par l'activation
    r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": same})
    assert r.status_code == 401
    clock.tick()
    r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": clock.code(secret)})
    assert r.status_code == 200 and r.json()["access_token"] and not r.json().get("mfa_setup_required")
    # un jeton d'accès ne sert pas de jeton d'étape
    r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": r.json()["access_token"], "code": "123456"})
    assert r.status_code == 401

    # code de secours : une seule fois
    rc = out["recovery_codes"][0]
    for expected in (200, 401):
        step1 = (await client.post(f"{API}/auth/login", json={"email": "admin@mfa-a.example.com",
                                                               "password": "Motdepasse-solide-1"})).json()
        r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": rc})
        assert r.status_code == expected
    full = {"Authorization": f"Bearer {(await _second_factor(client, 'admin@mfa-a.example.com', secret, clock))}"}
    assert (await client.get(f"{API}/auth/mfa", headers=full)).json()["recovery_codes_remaining"] == 9

    # désactivation interdite pour un rôle d'encadrement
    r = await client.post(f"{API}/auth/mfa/disable", headers=full, json={"password": "Motdepasse-solide-1", "code": "000000"})
    assert r.status_code == 403
    # régénération des codes : exige un code TOTP
    clock.tick()
    r = await client.post(f"{API}/auth/mfa/recovery-codes", headers=full, json={"code": clock.code(secret)})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10

    # téléphone perdu : réinitialisation par la super-administration, réenrôlement imposé
    uid = (await client.get(f"{API}/admin/users?q=mfa-a", headers=admin_token)).json()[0]["id"]
    assert (await client.post(f"{API}/admin/users/{uid}/reset-mfa", headers=admin_token)).status_code == 204
    assert (await login(client, "admin@mfa-a.example.com"))["mfa_setup_required"] is True
    actions = [a["action"] for a in (await client.get(f"{API}/admin/audit", headers=admin_token)).json()]
    assert "user.mfa_reset" in actions


async def _second_factor(client, email: str, secret: str, clock) -> str:
    clock.tick()
    step1 = (await client.post(f"{API}/auth/login", json={"email": email, "password": "Motdepasse-solide-1"})).json()
    r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": clock.code(secret)})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def test_mfa_code_rate_limited(client, admin_token, enforce_mfa, clock):
    await make_org(client, "mfa-b", "admin@mfa-b.example.com")
    secret, _ = await _enroll(client, auth(await login(client, "admin@mfa-b.example.com")), clock)
    step1 = (await client.post(f"{API}/auth/login", json={"email": "admin@mfa-b.example.com",
                                                           "password": "Motdepasse-solide-1"})).json()
    for _ in range(5):
        r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": "000000"})
        assert r.status_code == 401
    clock.tick()
    r = await client.post(f"{API}/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": clock.code(secret)})
    assert r.status_code == 429  # même un bon code est refusé pendant la fenêtre


async def test_optional_role_can_disable(client, admin_token, clock):
    org = await make_org(client, "mfa-c", "admin@mfa-c.example.com")
    ta = await login(client, "admin@mfa-c.example.com")
    r = await client.post(f"{API}/members", headers=auth(ta), json={"email": "coache@mfa-c.example.com", "full_name": "Coaché",
                                                                    "password": "Motdepasse-solide-1", "role": "coachee"})
    assert r.status_code == 201
    t = await login(client, "coache@mfa-c.example.com")
    assert not t.get("mfa_setup_required")
    secret, out = await _enroll(client, auth(t), clock)
    clock.tick()
    r = await client.post(f"{API}/auth/mfa/disable", headers=auth(out), json={"password": "faux", "code": clock.code(secret)})
    assert r.status_code == 401
    r = await client.post(f"{API}/auth/mfa/disable", headers=auth(out),
                          json={"password": "Motdepasse-solide-1", "code": clock.code(secret)})
    assert r.status_code == 204
    assert (await login(client, "coache@mfa-c.example.com", org=org["id"]))["access_token"]


async def test_platform_admin_mfa(client, admin_token, enforce_mfa, clock):
    r = await client.post(f"{API}/admin/admins", headers=admin_token, json={"username": "SECOURS", "initial_password": "Initial-secours-26"})
    assert r.status_code == 201
    sid = r.json()["id"]
    t = (await client.post(f"{API}/admin/auth/login", json={"username": "SECOURS", "password": "Initial-secours-26"})).json()
    assert t["must_change_password"] is True
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert (await client.post(f"{API}/admin/auth/mfa/setup", headers=h)).status_code == 403  # mot de passe d'abord
    t = (await client.post(f"{API}/admin/auth/change-password", headers=h,
                           json={"current_password": "Initial-secours-26", "new_password": "Secours-personnel-26"})).json()
    assert t["mfa_setup_required"] is True
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert (await client.get(f"{API}/admin/plans", headers=h)).status_code == 403
    secret, out = await _enroll(client, h, clock, base=f"{API}/admin/auth/mfa")
    assert (await client.get(f"{API}/admin/plans", headers={"Authorization": f"Bearer {out['access_token']}"})).status_code == 200

    step1 = (await client.post(f"{API}/admin/auth/login", json={"username": "SECOURS", "password": "Secours-personnel-26"})).json()
    assert step1["mfa_required"] is True and "access_token" not in step1
    clock.tick()
    r = await client.post(f"{API}/admin/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": clock.code(secret)})
    assert r.status_code == 200
    me = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.post(f"{API}/admin/admins/{sid}/reset-mfa", headers=me)).status_code == 422  # pas soi-même
    assert (await client.post(f"{API}/admin/admins/{sid}/reset-mfa", headers=admin_token)).status_code == 204
    admins = {a["username"]: a for a in (await client.get(f"{API}/admin/admins", headers=admin_token)).json()}
    assert admins["SECOURS"]["mfa_enabled"] is False


async def test_stale_enrollment_purged_by_worker(client, admin_token, clock):
    from app.worker import purge_stale_mfa_enrollments
    await make_org(client, "mfa-d", "admin@mfa-d.example.com")
    await client.post(f"{API}/auth/mfa/setup", headers=auth(await login(client, "admin@mfa-d.example.com")))
    async with platform_session() as s:
        await s.execute(text("update users set mfa_pending_at = now() - interval '2 hours' where email='admin@mfa-d.example.com'"))
    assert await purge_stale_mfa_enrollments({}) >= 1
    async with platform_session() as s:
        row = (await s.execute(text("select mfa_pending_enc from users where email='admin@mfa-d.example.com'"))).scalar_one()
    assert row is None
