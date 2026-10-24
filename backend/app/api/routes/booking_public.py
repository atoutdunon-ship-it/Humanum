"""Pages publiques de réservation (sans compte) et gestion du rendez-vous par lien personnel.

Résolution de l'organisme par fonction SECURITY DEFINER (slug → identifiant, ou empreinte du lien → identifiant),
puis TOUTES les lectures et écritures se font sous la RLS de cet organisme.
"""
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import AwareDatetime, BaseModel, EmailStr, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.security import STAFF_ROLES
from app.db.session import set_context, tenant_session
from app.models import Appointment, BookingType, Membership, Organization, User
from app.services import agenda, booking, features
from app.services.audit import audit

router = APIRouter(prefix="/public", tags=["réservation publique"])
NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Page de réservation introuvable")
BOOKINGS_PER_IP = 10  # par fenêtre de 15 minutes


class BookIn(BaseModel):
    start: AwareDatetime
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=2000)
    consent: bool = Field(description="Accord pour le traitement des données nécessaires au rendez-vous")


class RescheduleIn(BaseModel):
    start: AwareDatetime


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


async def _type(s: AsyncSession, org_slug: str, type_slug: str) -> tuple[Organization, BookingType, User]:
    org_id = await s.scalar(text("select hc_public_org(:s)"), {"s": org_slug})
    if org_id is None:
        raise NOT_FOUND
    await set_context(s, org_id=org_id, user_id=None)
    await features.require(s, "google_booking")
    bt = await s.scalar(select(BookingType).where(BookingType.slug == type_slug, BookingType.is_active.is_(True)))
    if bt is None:
        raise NOT_FOUND
    coach = await s.get(User, bt.coach_id)
    role = await s.scalar(select(Membership.role).where(Membership.user_id == bt.coach_id))
    acc = await agenda.account_of(s, bt.coach_id)
    if coach is None or not coach.is_active or role not in STAFF_ROLES:
        raise NOT_FOUND
    if acc is None or acc.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "La réservation en ligne est momentanément fermée pour ce coach")
    return await s.get(Organization, org_id), bt, coach


async def _by_token(s: AsyncSession, token: str, lock: bool = False) -> Appointment:
    digest = agenda.hash_token(token)
    org_id = await s.scalar(text("select hc_appointment_org(:h)"), {"h": digest})
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendez-vous introuvable")
    await set_context(s, org_id=org_id, user_id=None)
    appt = await s.scalar(select(Appointment).where(Appointment.manage_token_hash == digest).with_for_update(of=Appointment) if lock
                          else select(Appointment).where(Appointment.manage_token_hash == digest))
    if appt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendez-vous introuvable")
    return appt


def _out(appt: Appointment, coach: User | None, manage: str | None = None) -> dict:
    upcoming = appt.status == "confirmed" and appt.starts_at > datetime.now(UTC)
    return {"id": appt.id, "title": appt.title, "coach_name": coach.full_name if coach else None,
            "attendee_name": appt.attendee_name, "starts_at": appt.starts_at, "ends_at": appt.ends_at,
            "timezone": appt.timezone, "status": appt.status, "meet_url": appt.meet_url,
            "can_cancel": upcoming, "can_reschedule": upcoming and appt.booking_type_id is not None,
            **({"manage_url": manage} if manage else {})}


@router.get("/booking/{org_slug}/{type_slug}")
async def booking_page(org_slug: str, type_slug: str) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        org, bt, coach = await _type(s, org_slug, type_slug)
        sc = await agenda.schedule_of(s, bt.coach_id)
        return {"organization": org.name, "title": bt.title, "description": bt.description,
                "duration_minutes": bt.duration_minutes, "coach_name": coach.full_name, "timezone": sc.timezone,
                "max_days_ahead": sc.max_days_ahead}


@router.get("/booking/{org_slug}/{type_slug}/slots")
async def booking_slots(org_slug: str, type_slug: str, start: date | None = None,
                        days: int = Query(default=7, ge=1, le=14)) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        _, bt, _ = await _type(s, org_slug, type_slug)
        return await booking.slots(s, bt, start or datetime.now(UTC).date(), days)


@router.post("/booking/{org_slug}/{type_slug}", status_code=201)
async def book(org_slug: str, type_slug: str, body: BookIn, request: Request) -> dict:
    if not body.consent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Votre accord est nécessaire pour réserver")
    key = f"book:{request.client.host if request.client else '?'}"
    await ratelimit.check(key, limit=BOOKINGS_PER_IP)
    async with tenant_session(org_id=None, user_id=None) as s:
        org, bt, coach = await _type(s, org_slug, type_slug)
        await booking.ensure_free(s, bt, body.start)
        appt, raw = await booking.create(s, bt, body.start, name=body.name.strip(), email=body.email, phone=body.phone,
                                         message=body.message, source="public")
        await audit(s, actor_id=None, action="appointment.booked", target_type="appointment", target_id=appt.id,
                    ip=request.client.host if request.client else None, source="public")
        org_id, appt_id = org.id, appt.id
    await ratelimit.fail(key)  # compte les réservations par adresse IP (anti-abus)
    meet = await booking.push(org_id, appt_id)
    async with tenant_session(org_id=org_id, user_id=None) as s:
        appt = await s.get(Appointment, appt_id)
        appt.meet_url = appt.meet_url or meet
        return _out(appt, coach, manage=agenda.manage_url(raw))


@router.get("/appointments/{token}")
async def appointment(token: str) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        appt = await _by_token(s, token)
        return _out(appt, await s.get(User, appt.coach_id))


@router.get("/appointments/{token}/slots")
async def appointment_slots(token: str, start: date | None = None, days: int = Query(default=7, ge=1, le=14)) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        appt = await _by_token(s, token)
        bt = await s.get(BookingType, appt.booking_type_id) if appt.booking_type_id else None
        if bt is None or appt.status != "confirmed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce rendez-vous ne peut plus être reporté")
        return await booking.slots(s, bt, start or datetime.now(UTC).date(), days, exclude=appt.id)


@router.post("/appointments/{token}/reschedule")
async def reschedule(token: str, body: RescheduleIn, request: Request) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        appt = await _by_token(s, token, lock=True)
        bt = await s.get(BookingType, appt.booking_type_id) if appt.booking_type_id else None
        if bt is None or appt.status != "confirmed" or appt.starts_at <= datetime.now(UTC):
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce rendez-vous ne peut plus être reporté")
        await booking.ensure_free(s, bt, body.start, exclude=appt.id)
        old = appt.starts_at
        delta = body.start.astimezone(UTC) - appt.starts_at
        try:
            async with s.begin_nested():
                appt.starts_at, appt.ends_at = appt.starts_at + delta, appt.ends_at + delta
                appt.busy_from, appt.busy_to = appt.busy_from + delta, appt.busy_to + delta
                appt.sync_status, appt.sync_version = "pending", appt.sync_version + 1
                await s.flush()
        except IntegrityError:  # contrainte d'exclusion
            raise booking.TAKEN from None
        await audit(s, actor_id=None, action="appointment.rescheduled", target_type="appointment", target_id=appt.id,
                    ip=request.client.host if request.client else None, previous=old.isoformat())
        org_id, appt_id, coach = appt.organization_id, appt.id, await s.get(User, appt.coach_id)
    await booking.push(org_id, appt_id)
    async with tenant_session(org_id=org_id, user_id=None) as s:
        return _out(await s.get(Appointment, appt_id), coach)


@router.post("/appointments/{token}/cancel")
async def cancel(token: str, body: CancelIn, request: Request) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        appt = await _by_token(s, token, lock=True)
        if appt.status != "confirmed" or appt.starts_at <= datetime.now(UTC):
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce rendez-vous ne peut plus être annulé")
        appt.status, appt.cancelled_at, appt.cancel_reason = "cancelled", datetime.now(UTC), body.reason
        appt.sync_status, appt.sync_version = "pending", appt.sync_version + 1
        await audit(s, actor_id=None, action="appointment.cancelled", target_type="appointment", target_id=appt.id,
                    ip=request.client.host if request.client else None, by="attendee")
        org_id, appt_id, coach = appt.organization_id, appt.id, await s.get(User, appt.coach_id)
    await booking.push(org_id, appt_id)
    async with tenant_session(org_id=org_id, user_id=None) as s:
        return _out(await s.get(Appointment, appt_id), coach)

