"""Agenda : jetons Google des coachs, occupations (base + Google), synchronisation des rendez-vous vers Google Agenda.

Le rendez-vous est enregistré d'abord en base (source de vérité, anti-chevauchement PostgreSQL), puis poussé vers
Google : création de l'événement avec visioconférence Google Meet, invitation du coaché, rappels (e-mail la veille,
notification une heure avant). En cas d'échec transitoire, le worker Arq rejoue ; un rattrapage périodique couvre
le cas où Redis lui-même était indisponible.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mfa as crypto  # chiffrement Fernet commun (clés HC_MFA_KEYS)
from app.core.config import get_settings
from app.integrations import google
from app.models import Appointment, AvailabilityRule, BookingType, CoachSchedule, GoogleAccount, User
from app.services.slots import Interval, Policy, Rule

log = logging.getLogger("humanum.agenda")


def new_manage_token() -> tuple[str, str, str]:
    """(jeton remis au coaché, empreinte pour la recherche, copie chiffrée pour l'invitation Google)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw), crypto.encrypt(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def manage_url(raw: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/rdv/{raw}"


def event_id(appt_id: uuid.UUID) -> str:
    return "hc" + appt_id.hex  # alphabet base32hex accepté par Google : envoi idempotent


async def schedule_of(s: AsyncSession, coach_id: uuid.UUID) -> CoachSchedule:
    sc = await s.scalar(select(CoachSchedule).where(CoachSchedule.user_id == coach_id))
    return sc or CoachSchedule(user_id=coach_id, timezone="America/Martinique", min_notice_minutes=240,
                               max_days_ahead=120, slot_step_minutes=30)


async def rules_of(s: AsyncSession, coach_id: uuid.UUID) -> list[Rule]:
    rows = (await s.scalars(select(AvailabilityRule).where(AvailabilityRule.user_id == coach_id))).all()
    return [Rule(r.weekday, r.start_minute, r.end_minute) for r in rows]


def policy(sc: CoachSchedule, bt: BookingType, *, staff: bool = False) -> Policy:
    return Policy(timezone=sc.timezone, duration=bt.duration_minutes, buffer_before=bt.buffer_before_minutes,
                  buffer_after=bt.buffer_after_minutes, step=sc.slot_step_minutes,
                  min_notice=0 if staff else sc.min_notice_minutes, max_days_ahead=sc.max_days_ahead)


async def account_of(s: AsyncSession, coach_id: uuid.UUID) -> GoogleAccount | None:
    return await s.scalar(select(GoogleAccount).where(GoogleAccount.user_id == coach_id))


async def access_token(acc: GoogleAccount) -> str:
    if acc.status != "active":
        raise google.GoogleAuthError("agenda Google déconnecté")
    now = datetime.now(UTC)
    if acc.access_token_enc and acc.access_expires_at and acc.access_expires_at > now + timedelta(seconds=90):
        return crypto.decrypt(acc.access_token_enc)
    try:
        r = await google.refresh(crypto.decrypt(acc.refresh_token_enc))
    except google.GoogleAuthError as e:
        acc.status, acc.last_error = "revoked", str(e)[:500]
        raise
    acc.access_token_enc = crypto.encrypt(r["access_token"])
    acc.access_expires_at = now + timedelta(seconds=int(r.get("expires_in", 3600)))
    acc.last_error = None
    return r["access_token"]


async def busy(s: AsyncSession, coach_id: uuid.UUID, start: datetime, end: datetime,
               exclude: uuid.UUID | None = None) -> list[Interval]:
    """Occupations : rendez-vous confirmés en base + agenda Google du coach (freeBusy : aucun contenu lu)."""
    q = select(Appointment.busy_from, Appointment.busy_to).where(
        Appointment.coach_id == coach_id, Appointment.status == "confirmed",
        Appointment.busy_from < end, Appointment.busy_to > start)
    if exclude:
        q = q.where(Appointment.id != exclude)
    out: list[Interval] = [(a, b) for a, b in (await s.execute(q)).all()]
    acc = await account_of(s, coach_id)
    if acc is not None and acc.status == "active":
        out += await google.freebusy(await access_token(acc), acc.calendar_id, start, end)
    return out


def _event(appt: Appointment, coach: User) -> dict:
    raw_token = crypto.decrypt(appt.manage_token_enc)
    tz = appt.timezone
    lines = [f"Rendez-vous réservé via HUMANUM CONSULTING avec {coach.full_name}."]
    if appt.message:
        lines += ["", f"Message : {appt.message}"]
    lines += ["", f"Reporter ou annuler : {manage_url(raw_token)}"]
    return {
        "id": event_id(appt.id),
        "summary": appt.title,
        "description": "\n".join(lines),
        "start": {"dateTime": appt.starts_at.astimezone(ZoneInfo(tz)).isoformat(), "timeZone": tz},
        "end": {"dateTime": appt.ends_at.astimezone(ZoneInfo(tz)).isoformat(), "timeZone": tz},
        "attendees": [{"email": appt.attendee_email, "displayName": appt.attendee_name}],
        "conferenceData": {"createRequest": {"requestId": appt.id.hex,  # constant : un report garde le même lien Meet
                                             "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        "reminders": {"useDefault": False, "overrides": [{"method": "email", "minutes": 1440}, {"method": "popup", "minutes": 60}]},
        "extendedProperties": {"private": {"hc_appointment": str(appt.id), "hc_org": str(appt.organization_id)}},
        "guestsCanModify": False, "guestsCanInviteOthers": False,
    }


async def sync(s: AsyncSession, appt: Appointment) -> None:
    """Pousse l'état du rendez-vous vers Google. Lève GoogleTransientError pour que le worker rejoue."""
    acc = await account_of(s, appt.coach_id)
    if acc is None or not google.configured():
        appt.sync_status, appt.sync_error = "skipped", "agenda Google non connecté"
        return
    try:
        token = await access_token(acc)
        if appt.status == "confirmed":
            coach = await s.get(User, appt.coach_id)
            ev = await google.insert_event(token, acc.calendar_id, _event(appt, coach))
            appt.google_event_id, appt.meet_url = ev.get("id", event_id(appt.id)), google.meet_link(ev) or appt.meet_url
        elif appt.google_event_id:
            await google.cancel_event(token, acc.calendar_id, appt.google_event_id)
        appt.sync_status, appt.sync_error = "synced", None
    except google.GoogleTransientError as e:
        appt.sync_status, appt.sync_error = "error", f"temporaire : {e}"[:500]
        raise
    except google.GoogleError as e:
        appt.sync_status, appt.sync_error = "error", str(e)[:500]
        log.warning("Synchronisation Google refusée pour %s : %s", appt.id, e)
