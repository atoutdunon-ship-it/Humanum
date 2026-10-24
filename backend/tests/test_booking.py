"""Lot 2 — prise de rendez-vous : créneaux, Google Agenda + Meet (simulé), anti-chevauchement, liens de gestion, RLS."""
import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import tenant_session
from app.integrations import google
from app.services.slots import Policy, Rule, free_slots, is_free
from tests.conftest import auth, login, make_org
from tests.fake_google import FakeGoogle

API = "/api/v1"
MTQ = ZoneInfo("America/Martinique")


# ---------------------------------------------------------------- calcul des créneaux (pur)
def _d(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_slots_respect_rules_buffers_notice_and_horizon():
    rules = [Rule(0, 9 * 60, 12 * 60)]  # lundi 9h–12h
    pol = Policy("America/Martinique", duration=60, buffer_after=15, step=30, min_notice=0, max_days_ahead=30)
    monday = date(2026, 11, 2)
    now = _d("2026-11-01T00:00:00+00:00")
    got = free_slots(rules, [], pol, first_day=monday, days=1, now=now)[monday]
    assert [t.astimezone(MTQ).strftime("%H:%M") for t in got] == ["09:00", "09:30", "10:00", "10:30", "11:00"]
    # occupation Google 10h–10h30 : 9h00 (fin 10h15 avec battement) et 9h30, 10h00 tombent
    busy = [(_d("2026-11-02T10:00:00-04:00"), _d("2026-11-02T10:30:00-04:00"))]
    got = free_slots(rules, busy, pol, first_day=monday, days=1, now=now)[monday]
    assert [t.astimezone(MTQ).strftime("%H:%M") for t in got] == ["10:30", "11:00"]
    # délai de prévenance : il est 9h40 locale avec 60 min de prévenance → premier créneau 11h00
    late = Policy("America/Martinique", duration=60, buffer_after=15, step=30, min_notice=60, max_days_ahead=30)
    got = free_slots(rules, [], late, first_day=monday, days=1, now=_d("2026-11-02T09:40:00-04:00"))[monday]
    assert got[0].astimezone(MTQ).strftime("%H:%M") == "11:00"
    # horizon
    assert free_slots(rules, [], pol, first_day=date(2027, 3, 1), days=1, now=now) == {}
    assert is_free(_d("2026-11-02T09:00:00-04:00"), rules, [], pol, now)
    assert not is_free(_d("2026-11-02T09:10:00-04:00"), rules, [], pol, now)  # hors grille


def test_slots_across_daylight_saving_change():
    # Paris, dimanche 25 octobre 2026 : 3h → 2h. Une plage 1h–4h locale dure 4 h réelles.
    pol = Policy("Europe/Paris", duration=60, step=60, min_notice=0, max_days_ahead=30)
    day = date(2026, 10, 25)
    got = free_slots([Rule(6, 60, 240)], [], pol, first_day=day, days=1, now=_d("2026-10-01T00:00:00+00:00"))[day]
    assert [t.strftime("%H:%M") for t in got] == ["23:00", "00:00", "01:00", "02:00"]  # UTC : aucune heure perdue ni doublée


# ---------------------------------------------------------------- parcours complet
@pytest.fixture
async def fake(monkeypatch):
    g = FakeGoogle()
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "client-test")
    monkeypatch.setattr(s, "google_client_secret", type(s.google_client_secret)("secret-test"))
    google.set_transport(g.transport())
    yield g
    google.set_transport(None)
    import os

    from redis.asyncio import Redis
    r = Redis.from_url(os.environ["HC_REDIS_URL"])
    await r.flushdb()  # tâches de synchronisation mises en file par les tests de panne : jamais rejouées ailleurs
    await r.aclose()


@pytest.fixture
async def coach(client, admin_token, fake):
    slug = f"rdv-{uuid.uuid4().hex[:6]}"
    org = await make_org(client, slug, f"coach@{slug}.example.com")
    tok = await login(client, f"coach@{slug}.example.com")
    h = auth(tok)
    r = await client.post(f"{API}/booking/google/connect", headers=h)
    assert r.status_code == 200
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert "https://www.googleapis.com/auth/calendar.freebusy" in q["scope"][0] and q["access_type"] == ["offline"]
    r = await client.get(f"{API}/integrations/google/callback", params={"state": q["state"][0], "code": "code-ok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/agenda?google=connecte")
    st = (await client.get(f"{API}/booking/google", headers=h)).json()
    assert st["connected"] is True and st["email"] == fake.email
    # disponibilités : du lundi au vendredi 8h–12h et 14h–17h, prévenance 0 pour le test
    rules = [{"weekday": d, "start": a, "end": b} for d in range(5) for a, b in (("08:00", "12:00"), ("14:00", "17:00"))]
    r = await client.put(f"{API}/booking/schedule", headers=h, json={"timezone": "America/Martinique", "min_notice_minutes": 0,
                                                                     "max_days_ahead": 30, "slot_step_minutes": 30, "rules": rules})
    assert r.status_code == 200 and len(r.json()["rules"]) == 10
    r = await client.post(f"{API}/booking/types", headers=h, json={"slug": "seance", "title": "Séance individuelle",
                                                                   "duration_minutes": 60, "buffer_after_minutes": 15})
    assert r.status_code == 201 and r.json()["public_url"].endswith(f"/r/{slug}/seance")
    return {"org": org, "slug": slug, "h": h, "type": r.json()}


def _next_weekday() -> date:
    d = datetime.now(MTQ).date() + timedelta(days=1)
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d


async def _slots(client, slug: str, day: date) -> list[str]:
    r = await client.get(f"{API}/public/booking/{slug}/seance/slots", params={"start": day.isoformat(), "days": 1})
    assert r.status_code == 200, r.text
    return r.json()["days"][0]["slots"] if r.json()["days"] else []


def _person(n: str = "Line MARTIN") -> dict:
    return {"name": n, "email": "line.martin@example.com", "phone": "0696 00 00 00", "consent": True}


async def test_public_booking_creates_google_event_with_meet(client, coach, fake):
    slug, day = coach["slug"], _next_weekday()
    page = (await client.get(f"{API}/public/booking/{slug}/seance")).json()
    assert page["title"] == "Séance individuelle" and page["timezone"] == "America/Martinique"
    slots = await _slots(client, slug, day)
    assert len(slots) == 7 + 5  # départs 8h→11h puis 14h→16h, au pas de 30 min
    # occupation personnelle dans Google 9h–10h → 8h, 8h30 (battement de 15 min), 9h, 9h30 retirés ; contenu jamais lu
    nine = datetime.combine(day, datetime.min.time(), MTQ).replace(hour=9)
    fake.busy = [(nine.astimezone(UTC), (nine + timedelta(hours=1)).astimezone(UTC))]
    slots = await _slots(client, slug, day)
    assert len(slots) == 12 - 4

    r = await client.post(f"{API}/public/booking/{slug}/seance", json={**_person(), "start": slots[0]})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["meet_url"].startswith("https://meet.google.com/") and "/rdv/" in out["manage_url"]
    ev = next(iter(fake.events.values()))
    assert ev["attendees"][0]["email"] == "line.martin@example.com"
    assert ev["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert {o["minutes"] for o in ev["reminders"]["overrides"]} == {1440, 60}
    assert out["manage_url"] in ev["description"]
    # même créneau : refusé ; coaché ajouté au CRM
    assert (await client.post(f"{API}/public/booking/{slug}/seance", json={**_person(), "start": slots[0]})).status_code == 409
    contacts = (await client.get(f"{API}/contacts", headers=coach["h"])).json()
    assert [c["email"] for c in contacts] == ["line.martin@example.com"]
    staff = (await client.get(f"{API}/booking/appointments", headers=coach["h"])).json()
    assert len(staff) == 1 and staff[0]["sync_status"] == "synced"


async def test_simultaneous_bookings_one_wins(client, coach, fake):
    slug, day = coach["slug"], _next_weekday()
    start = (await _slots(client, slug, day))[2]
    rs = await asyncio.gather(*[client.post(f"{API}/public/booking/{slug}/seance",
                                            json={**_person(f"Personne {i}"), "email": f"p{i}@example.com", "start": start})
                                for i in range(4)])
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409, 409, 409], codes
    assert len(fake.events) == 1


async def test_manage_link_reschedule_and_cancel(client, coach, fake):
    slug, day = coach["slug"], _next_weekday()
    slots = await _slots(client, slug, day)
    out = (await client.post(f"{API}/public/booking/{slug}/seance", json={**_person(), "start": slots[0]})).json()
    token = out["manage_url"].rsplit("/", 1)[-1]
    meet = out["meet_url"]
    got = (await client.get(f"{API}/public/appointments/{token}")).json()
    assert got["can_reschedule"] and got["coach_name"]
    new_slots = (await client.get(f"{API}/public/appointments/{token}/slots", params={"start": day.isoformat(), "days": 1})).json()
    target = new_slots["days"][0]["slots"][-1]
    r = await client.post(f"{API}/public/appointments/{token}/reschedule", json={"start": target})
    assert r.status_code == 200, r.text
    ev = next(iter(fake.events.values()))
    assert datetime.fromisoformat(ev["start"]["dateTime"]) == datetime.fromisoformat(target)
    assert r.json()["meet_url"] == meet  # même lien Meet après report
    assert slots[0] in await _slots(client, slug, day)  # l'ancien créneau est libéré
    r = await client.post(f"{API}/public/appointments/{token}/cancel", json={"reason": "Empêchement"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert ev["status"] == "cancelled"
    assert (await client.post(f"{API}/public/appointments/{token}/cancel", json={})).status_code == 409
    assert (await client.get(f"{API}/public/appointments/{'x' * 43}")).status_code == 404


async def test_google_outage_is_absorbed_then_caught_up(client, coach, fake):
    slug, day = coach["slug"], _next_weekday()
    start = (await _slots(client, slug, day))[1]
    fake.down = {"events"}
    r = await client.post(f"{API}/public/booking/{slug}/seance", json={**_person(), "start": start})
    assert r.status_code == 201 and r.json()["meet_url"] is None  # réservation garantie malgré Google
    appt_id = r.json()["id"]
    fake.down = set()
    from app.worker import sync_appointment
    assert await sync_appointment({"job_try": 1}, coach["org"]["id"], appt_id) == "synced"
    staff = (await client.get(f"{API}/booking/appointments", headers=coach["h"])).json()
    assert staff[0]["meet_url"].startswith("https://meet.google.com/")
    # agenda injoignable : aucun créneau proposé « à l'aveugle »
    fake.down = {"freebusy"}
    r = await client.get(f"{API}/public/booking/{slug}/seance/slots")
    assert r.status_code == 503


async def test_revoked_google_access_closes_public_booking(client, coach, fake):
    slug = coach["slug"]
    async with tenant_session(org_id=uuid.UUID(coach["org"]["id"]), user_id=None) as s:
        await s.execute(text("update google_accounts set access_expires_at = now() - interval '1 hour'"))
    fake.revoked = True  # le coach a retiré l'accès depuis son compte Google
    r = await client.get(f"{API}/public/booking/{slug}/seance/slots")
    assert r.status_code == 409
    st = (await client.get(f"{API}/booking/google", headers=coach["h"])).json()
    assert st["connected"] is False and st["status"] == "revoked" and "revoked" in st["last_error"]
    assert (await client.get(f"{API}/public/booking/{slug}/seance")).status_code == 409
    # reconnexion
    fake.revoked = False
    q = parse_qs(urlparse((await client.post(f"{API}/booking/google/connect", headers=coach["h"])).json()["url"]).query)
    await client.get(f"{API}/integrations/google/callback", params={"state": q["state"][0], "code": "code-ok"})
    assert (await client.get(f"{API}/public/booking/{slug}/seance")).status_code == 200
    # déconnexion volontaire : révocation chez Google, page publique fermée
    assert (await client.delete(f"{API}/booking/google", headers=coach["h"])).status_code == 204
    assert fake.revoked is True
    assert (await client.get(f"{API}/public/booking/{slug}/seance")).status_code == 409
    # état OAuth falsifié ou expiré : aucun compte rattaché
    r = await client.get(f"{API}/integrations/google/callback", params={"state": "faux", "code": "code-ok"})
    assert r.headers["location"].endswith("google=expire")


async def test_feature_flag_and_roles(client, coach, admin_token, fake):
    slug = coach["slug"]
    r = await client.put(f"{API}/admin/organizations/{coach['org']['id']}/features/google_booking", headers=admin_token,
                         json={"enabled": False})
    assert r.status_code == 200
    assert (await client.get(f"{API}/public/booking/{slug}/seance")).status_code == 404
    assert (await client.get(f"{API}/booking/types", headers=coach["h"])).status_code == 404
    await client.put(f"{API}/admin/organizations/{coach['org']['id']}/features/google_booking", headers=admin_token, json={"enabled": None})
    r = await client.post(f"{API}/members", headers=coach["h"], json={"email": f"coache@{slug}.example.com", "full_name": "Coaché",
                                                                      "password": "Motdepasse-solide-1", "role": "coachee"})
    assert r.status_code == 201
    coachee = auth(await login(client, f"coache@{slug}.example.com"))
    assert (await client.get(f"{API}/booking/appointments", headers=coachee)).status_code == 403


async def test_appointments_are_isolated_between_orgs(client, coach, admin_token, fake):
    slug, day = coach["slug"], _next_weekday()
    start = (await _slots(client, slug, day))[0]
    assert (await client.post(f"{API}/public/booking/{slug}/seance", json={**_person(), "start": start})).status_code == 201
    other = f"autre-{uuid.uuid4().hex[:6]}"
    o = await make_org(client, other, f"coach@{other}.example.com")
    h2 = auth(await login(client, f"coach@{other}.example.com"))
    assert (await client.get(f"{API}/booking/appointments", headers=h2)).json() == []
    async with tenant_session(org_id=uuid.UUID(o["id"]), user_id=None) as s:
        for t in ("appointments", "booking_types", "google_accounts", "availability_rules", "coach_schedules"):
            assert (await s.execute(text(f"select count(*) from {t}"))).scalar() == 0, t  # noqa: S608
    # le type « seance » de l'autre organisme ne fuit pas par un slug identique
    assert (await client.get(f"{API}/public/booking/{other}/seance")).status_code == 404


async def test_staff_booking_and_cancel(client, coach, fake):
    day = _next_weekday()
    start = datetime.combine(day, datetime.min.time(), MTQ).replace(hour=18)  # hors disponibilités : permis au coach
    r = await client.post(f"{API}/booking/appointments", headers=coach["h"], json={
        "booking_type_id": coach["type"]["id"], "start": start.isoformat(), "attendee_name": "Willy JEAN-ALPHONSE",
        "attendee_email": "willy@example.com"})
    assert r.status_code == 201 and r.json()["meet_url"] and r.json()["source"] == "staff"
    again = await client.post(f"{API}/booking/appointments", headers=coach["h"], json={
        "booking_type_id": coach["type"]["id"], "start": (start + timedelta(minutes=30)).isoformat(),
        "attendee_name": "Autre", "attendee_email": "autre@example.com"})
    assert again.status_code == 409
    r = await client.post(f"{API}/booking/appointments/{r.json()['id']}/cancel", headers=coach["h"], json={"reason": "Report"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


async def test_database_forbids_overlap_even_without_api(client, coach, fake):
    """La contrainte d'exclusion arbitre, même pour une écriture SQL qui contournerait l'API."""
    from sqlalchemy.exc import IntegrityError
    org = uuid.UUID(coach["org"]["id"])
    sql = text("insert into appointments (id, coach_id, title, attendee_name, attendee_email, timezone, starts_at, ends_at, "
               "busy_from, busy_to, manage_token_hash, manage_token_enc) values (gen_random_uuid(), :c, 't', 'n', 'e@x.fr', 'UTC', "
               ":a, :b, :a, :b, md5(random()::text), 'x')")
    a = datetime(2030, 1, 7, 13, tzinfo=UTC)
    args = {"c": coach["type"]["coach_id"], "a": a, "b": a + timedelta(hours=1)}
    async with tenant_session(org_id=org, user_id=None) as s:
        await s.execute(sql, args)
    with pytest.raises(IntegrityError, match="ex_appointments_no_overlap"):
        async with tenant_session(org_id=org, user_id=None) as s:
            await s.execute(sql, {**args, "a": a + timedelta(minutes=59), "b": a + timedelta(hours=2)})
    async with tenant_session(org_id=org, user_id=None) as s:  # bord à bord : autorisé
        await s.execute(sql, {**args, "a": a + timedelta(hours=1), "b": a + timedelta(hours=2)})
    with pytest.raises(Exception, match="permission denied"):  # un rendez-vous s'annule, il ne s'efface pas
        async with tenant_session(org_id=org, user_id=None) as s:
            await s.execute(text("delete from appointments"))
