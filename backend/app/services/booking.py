"""Réservation : enregistrement en base (arbitrage PostgreSQL), puis envoi vers Google Agenda (direct, sinon worker)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import queue
from app.core.config import get_settings
from app.db.session import tenant_session
from app.integrations import google
from app.models import Appointment, BookingType, Contact
from app.services import agenda
from app.services.slots import free_slots, is_free

log = logging.getLogger("humanum.booking")
TAKEN = HTTPException(status.HTTP_409_CONFLICT, "Ce créneau vient d'être réservé : choisissez-en un autre")
CLOSED = HTTPException(status.HTTP_409_CONFLICT, "La réservation en ligne est momentanément fermée pour ce coach")
CALENDAR_DOWN = HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                              "L'agenda du coach est momentanément indisponible : réessayez dans quelques minutes")


async def busy_or_503(s: AsyncSession, coach_id: uuid.UUID, start: datetime, end: datetime,
                      exclude: uuid.UUID | None = None) -> list:
    try:
        return await agenda.busy(s, coach_id, start, end, exclude)
    except google.GoogleAuthError as e:
        # Autorisation retirée côté Google : consigné hors de la transaction (annulée par le refus qui suit).
        await mark_revoked(s, coach_id, str(e))
        raise CLOSED from None
    except google.GoogleError:
        # Jamais de créneau proposé « à l'aveugle » : sans l'agenda réel du coach, on ne propose rien.
        raise CALENDAR_DOWN from None


async def mark_revoked(s: AsyncSession, coach_id: uuid.UUID, reason: str) -> None:
    with s.no_autoflush:  # la modification en mémoire ne doit pas verrouiller la ligne dans la transaction annulée
        org = await s.scalar(text("select nullif(current_setting('app.current_org', true), '')::uuid"))
    async with tenant_session(org_id=org, user_id=None) as s2:
        acc = await agenda.account_of(s2, coach_id)
        if acc is not None:
            acc.status, acc.last_error, acc.access_token_enc = "revoked", reason[:500], None


async def slots(s: AsyncSession, bt: BookingType, first_day, days: int, *, exclude: uuid.UUID | None = None,
                staff: bool = False) -> dict:
    sc = await agenda.schedule_of(s, bt.coach_id)
    pol = agenda.policy(sc, bt, staff=staff)
    now = datetime.now(UTC)
    start = datetime.combine(first_day, datetime.min.time(), UTC) - timedelta(days=1)
    end = start + timedelta(days=days + 2)
    b = await busy_or_503(s, bt.coach_id, start, end, exclude)
    found = free_slots(await agenda.rules_of(s, bt.coach_id), b, pol, first_day=first_day, days=days, now=now)
    return {"timezone": sc.timezone, "duration_minutes": bt.duration_minutes,
            "days": [{"date": d.isoformat(), "slots": [t.isoformat() for t in ts]} for d, ts in found.items()]}


async def ensure_free(s: AsyncSession, bt: BookingType, start: datetime, *, exclude: uuid.UUID | None = None) -> None:
    sc = await agenda.schedule_of(s, bt.coach_id)
    b = await busy_or_503(s, bt.coach_id, start - timedelta(days=1), start + timedelta(days=1), exclude)
    if not is_free(start, await agenda.rules_of(s, bt.coach_id), b, agenda.policy(sc, bt), datetime.now(UTC)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce créneau n'est plus disponible : choisissez-en un autre")


async def upsert_contact(s: AsyncSession, name: str, email: str, phone: str | None, owner: uuid.UUID) -> uuid.UUID:
    """Tout coaché qui réserve apparaît dans le CRM (étape « prospect » s'il est nouveau)."""
    c = await s.scalar(select(Contact).where(func.lower(Contact.email) == email.lower()))
    if c is None:
        first, _, last = name.strip().partition(" ")
        c = Contact(kind="coachee", first_name=first or name, last_name=last or "—", email=email.lower(), phone=phone,
                    pipeline_stage="prospect", owner_id=owner, notes="Créé par une réservation en ligne")
        s.add(c)
        await s.flush()
    return c.id


async def create(s: AsyncSession, bt: BookingType, start: datetime, *, name: str, email: str, phone: str | None,
                 message: str | None, source: str, contact_id: uuid.UUID | None = None) -> tuple[Appointment, str]:
    sc = await agenda.schedule_of(s, bt.coach_id)
    start = start.astimezone(UTC)
    end = start + timedelta(minutes=bt.duration_minutes)
    raw, digest, enc = agenda.new_manage_token()
    if contact_id is None:
        contact_id = await upsert_contact(s, name, email, phone, bt.coach_id)
    appt = Appointment(booking_type_id=bt.id, coach_id=bt.coach_id, contact_id=contact_id,
                       title=f"{bt.title} — {name}", attendee_name=name, attendee_email=email.lower(),
                       attendee_phone=phone, message=message, timezone=sc.timezone, starts_at=start, ends_at=end,
                       busy_from=start - timedelta(minutes=bt.buffer_before_minutes),
                       busy_to=end + timedelta(minutes=bt.buffer_after_minutes),
                       source=source, manage_token_hash=digest, manage_token_enc=enc)
    try:
        async with s.begin_nested():
            s.add(appt)
            await s.flush()
    except IntegrityError:  # contrainte d'exclusion : un autre rendez-vous a pris la place entre-temps
        raise TAKEN from None
    return appt, raw


async def push(org_id: uuid.UUID, appt_id: uuid.UUID) -> str | None:
    """Envoi direct vers Google (borné dans le temps), sinon mise en file. Renvoie le lien Meet s'il est déjà connu."""
    try:
        async with asyncio.timeout(get_settings().google_timeout_seconds + 2):
            async with tenant_session(org_id=org_id, user_id=None) as s:
                appt = await s.get(Appointment, appt_id, with_for_update=True)
                await agenda.sync(s, appt)
                return appt.meet_url
    except Exception as e:  # noqa: BLE001 — Google lent ou indisponible : le worker prend le relais
        log.info("Synchronisation différée pour %s (%s)", appt_id, type(e).__name__)
        try:
            await queue.enqueue("sync_appointment", str(org_id), str(appt_id), job_id=f"sync:{appt_id}:{datetime.now(UTC):%Y%m%d%H%M}")
        except Exception:  # noqa: BLE001 — Redis absent : le rattrapage périodique s'en chargera
            log.warning("File indisponible : rattrapage périodique pour %s", appt_id)
        return None
