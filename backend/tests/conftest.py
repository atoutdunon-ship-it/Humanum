"""Base de test jetable : migrée en propriétaire, interrogée avec le rôle applicatif (RLS active)."""
import os
import subprocess

import pytest

PG = os.environ.get("HC_TEST_PG", "127.0.0.1:5433")
DB = "humanum_test"
os.environ["HC_DATABASE_URL"] = f"postgresql+asyncpg://humanum_app:humanum_app@{PG}/{DB}"
os.environ["HC_DATABASE_OWNER_URL"] = f"postgresql+asyncpg://postgres@{PG}/{DB}"
os.environ["HC_DATABASE_PLATFORM_URL"] = f"postgresql+asyncpg://humanum_platform:humanum_platform@{PG}/{DB}"
os.environ["HC_LOGIN_MAX_ATTEMPTS"] = "5"
os.environ["HC_REDIS_URL"] = os.environ.get("HC_TEST_REDIS", "redis://127.0.0.1:6379/15")
os.environ["HC_MFA_ENFORCED"] = "false"  # activée par la fixture `enforce_mfa` dans les tests dédiés
ADMIN_PWD = "Nouveau-mot-de-passe-2026"

import httpx  # noqa: E402

from app.main import app  # noqa: E402

HOST, PORT = PG.split(":")


def _psql(sql: str, db: str = "postgres") -> None:
    subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", "postgres", "-d", db, "-qc", sql], check=True)


@pytest.fixture(scope="session", autouse=True)
def database():
    _psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    _psql(f"CREATE DATABASE {DB}")
    subprocess.run(["psql", "-h", HOST, "-p", PORT, "-U", "postgres", "-qf", "scripts/bootstrap_db.sql"], check=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=os.environ.copy())
    import redis
    redis.Redis.from_url(os.environ["HC_REDIS_URL"]).flushdb()  # base Redis dédiée aux tests
    yield


@pytest.fixture(scope="session")
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def admin_token(client):
    """Compte ADMIN créé comme en production (mot de passe initial court), puis changement imposé."""
    from app.cli import create_superadmin
    await create_superadmin("ADMIN", "init-test-7", reset=False)
    r = await client.post("/api/v1/admin/auth/login", json={"username": "ADMIN", "password": "init-test-7"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True, r.text
    r = await client.post("/api/v1/admin/auth/change-password", headers={"Authorization": f"Bearer {r.json()['access_token']}"},
                          json={"current_password": "init-test-7", "new_password": ADMIN_PWD})
    assert r.status_code == 200, r.text
    _ADMIN["h"] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _ADMIN["h"]


_ADMIN: dict = {}  # jeton ADMIN de session, délivré avant toute activation de `enforce_mfa`


async def make_org(client, slug: str, email: str, password: str = "Motdepasse-solide-1") -> dict:
    if not _ADMIN:
        r = await client.post("/api/v1/admin/auth/login", json={"username": "ADMIN", "password": ADMIN_PWD})
        _ADMIN["h"] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/api/v1/admin/organizations", headers=_ADMIN["h"],
                          json={"name": f"Organisme {slug}", "slug": slug, "admin_email": email,
                                "admin_full_name": f"Admin {slug}", "admin_password": password})
    assert r.status_code == 201, r.text
    return r.json()


async def login(client, email: str, password: str = "Motdepasse-solide-1", org=None) -> dict:
    body = {"email": email, "password": password}
    if org:
        body["organization_id"] = org
    r = await client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def auth(tok: dict) -> dict:
    return {"Authorization": f"Bearer {tok['access_token']}"}


@pytest.fixture
def enforce_mfa(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "mfa_enforced", True)


@pytest.fixture
def clock(monkeypatch):
    """Horloge TOTP pilotable : `clock.tick()` avance d'un pas de 30 s."""
    import time

    from app.core import mfa

    class Clock:
        t = time.time()

        def now(self) -> float:
            return self.t

        def tick(self, steps: int = 1) -> None:
            self.t += 30 * steps

        def code(self, secret: str) -> str:
            import pyotp
            return pyotp.TOTP(secret).at(self.t)
    c = Clock()
    monkeypatch.setattr(mfa, "_now", c.now)
    return c
