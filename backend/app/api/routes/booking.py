"""Espace coach : connexion de l'agenda Google, disponibilités, types de rendez-vous, rendez-vous."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import AwareDatetime, BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.core import mfa as crypto
from app.core.config import get_settings
from app.core.security import STAFF_ROLES, Role, create_challenge_token, decode_challenge_token
from app.db.session import tenant_session
from app.integrations import google
from app.models import Appointment, AvailabilityRule, BookingType, CoachSchedule, GoogleAccount, Organization, User
from app.services import agenda, booking, features
from app.services.audit import audit

router = APIRouter(prefix="/booking", tags=["prise de rendez-vous"])
oauth = APIRouter(prefix="/integrations/google", tags=["prise de rendez-vous"])
_staff = require(*STAFF_ROLES)
_coach = require(Role.ORG_ADMIN, Role.COACH)
_MANAGERS = frozenset({Role.ORG_ADMIN, Role.COORDINATOR})


async def _feature(p: Principal = Depends(_staff), s: AsyncSession = Depends(db)) -> Principal:
    await features.require(s, "google_booking")
    return p


# ---------------------------------------------------------------- agenda Google du coach
@router.get("/google")
async def google_status(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    acc = await agenda.account_of(s, p.user_id)
    return {"configured": google.configured(), "connected": acc is not None and acc.status == "active",
            "email": acc.google_email if acc else None, "status": acc.status if acc else None,
            "last_error": acc.last_error if acc else None}


@router.post("/google/connect")
async def google_connect(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    if not google.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Connexion Google non configurée sur la plateforme")
    state = create_challenge_token(subject=p.user_id, purpose="google:oauth", minutes=15, org=str(p.org_id))
    user = await s.get(User, p.user_id)
    return {"url": google.authorization_url(state, login_hint=user.email if user else None)}


@oauth.get("/callback")
async def google_callback(request: Request, state: str, code: str | None = None, error: str | None = None) -> RedirectResponse:
    """Retour de Google (navigateur du coach). Le `state` signé lie la réponse au compte et à l'organisme d'origine."""
    base = get_settings().public_base_url.rstrip("/")
    try:
        c = decode_challenge_token(state, "google:oauth")
        user_id, org_id = uuid.UUID(c["sub"]), uuid.UUID(c["org"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return RedirectResponse(f"{base}/agenda?google=expire", status_code=303)
    if error or not code:
        return RedirectResponse(f"{base}/agenda?google=refuse", status_code=303)
    try:
        tok = await google.exchange_code(code)
    except google.GoogleError:
        return RedirectResponse(f"{base}/agenda?google=erreur", status_code=303)
    async with tenant_session(org_id=org_id, user_id=user_id) as s:
        acc = await agenda.account_of(s, user_id) or GoogleAccount(user_id=user_id)
        acc.google_email, acc.scopes, acc.status, acc.last_error = tok["email"], tok.get("scope", ""), "active", None
        acc.refresh_token_enc = crypto.encrypt(tok["refresh_token"])
        acc.access_token_enc = crypto.encrypt(tok["access_token"])
        acc.access_expires_at = datetime.now(UTC) + timedelta(seconds=int(tok.get("expires_in", 3600)))
        s.add(acc)
        await audit(s, actor_id=user_id, action="google.connected", target_type="user", target_id=user_id,
                    ip=request.client.host if request.client else None, google_email=tok["email"])
    return RedirectResponse(f"{base}/agenda?google=connecte", status_code=303)


@router.delete("/google", status_code=204)
async def google_disconnect(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> Response:
    acc = await agenda.account_of(s, p.user_id)
    if acc is not None:
        await google.revoke(crypto.decrypt(acc.refresh_token_enc))
        await s.delete(acc)
        await audit(s, actor_id=p.user_id, action="google.disconnected", target_type="user", target_id=p.user_id, ip=p.ip)
    return Response(status_code=204)


# ---------------------------------------------------------------- disponibilités
class RuleIO(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")

    def minutes(self) -> tuple[int, int]:
        f = lambda v: int(v[:2]) * 60 + int(v[3:])  # noqa: E731
        return f(self.start), f(self.end)


class ScheduleIO(BaseModel):
    timezone: str = "America/Martinique"
    min_notice_minutes: int = Field(default=240, ge=0, le=20160)
    max_days_ahead: int = Field(default=120, ge=1, le=365)  # jusqu'à 4 mois à l'avance
    slot_step_minutes: int = Field(default=30)
    rules: list[RuleIO] = Field(default_factory=list, max_length=50)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("fuseau horaire inconnu") from None
        return v

    @field_validator("slot_step_minutes")
    @classmethod
    def _step(cls, v: int) -> int:
        if v not in (5, 10, 15, 20, 30, 45, 60, 90, 120):
            raise ValueError("pas autorisé : 5, 10, 15, 20, 30, 45, 60, 90 ou 120 minutes")
        return v


def _hhmm(m: int) -> str:
    return "24:00" if m == 1440 else f"{m // 60:02d}:{m % 60:02d}"


@router.get("/schedule", response_model=ScheduleIO)
async def get_schedule(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> ScheduleIO:
    sc = await agenda.schedule_of(s, p.user_id)
    rules = (await s.scalars(select(AvailabilityRule).where(AvailabilityRule.user_id == p.user_id)
                             .order_by(AvailabilityRule.weekday, AvailabilityRule.start_minute))).all()
    return ScheduleIO(timezone=sc.timezone, min_notice_minutes=sc.min_notice_minutes, max_days_ahead=sc.max_days_ahead,
                      slot_step_minutes=sc.slot_step_minutes,
                      rules=[RuleIO(weekday=r.weekday, start=_hhmm(r.start_minute), end=_hhmm(r.end_minute)) for r in rules])


@router.put("/schedule", response_model=ScheduleIO)
async def put_schedule(body: ScheduleIO, p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> ScheduleIO:
    spans: dict[int, list[tuple[int, int]]] = {}
    for r in body.rules:
        a, b = r.minutes()
        if a >= b:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Plage invalide : {r.start}–{r.end}")
        spans.setdefault(r.weekday, []).append((a, b))
    for day in spans.values():
        day.sort()
        if any(day[i][1] > day[i + 1][0] for i in range(len(day) - 1)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Deux plages se chevauchent le même jour")
    sc = await s.scalar(select(CoachSchedule).where(CoachSchedule.user_id == p.user_id)) or CoachSchedule(user_id=p.user_id)
    sc.timezone, sc.min_notice_minutes = body.timezone, body.min_notice_minutes
    sc.max_days_ahead, sc.slot_step_minutes = body.max_days_ahead, body.slot_step_minutes
    s.add(sc)
    await s.execute(delete(AvailabilityRule).where(AvailabilityRule.user_id == p.user_id))
    for d, items in spans.items():
        for a, b in items:
            s.add(AvailabilityRule(user_id=p.user_id, weekday=d, start_minute=a, end_minute=b))
    await audit(s, actor_id=p.user_id, action="booking.schedule_updated", target_type="user", target_id=p.user_id, ip=p.ip)
    await s.flush()
    return await get_schedule(p, s)


# ---------------------------------------------------------------- types de rendez-vous
class TypeIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,80}$")
    title: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int = Field(default=60, ge=10, le=480)
    buffer_before_minutes: int = Field(default=0, ge=0, le=120)
    buffer_after_minutes: int = Field(default=15, ge=0, le=120)
    is_active: bool = True


class TypePatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, ge=10, le=480)
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=120)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=120)
    is_active: bool | None = None


async def _org_slug(s: AsyncSession, p: Principal) -> str:
    return (await s.get(Organization, p.org_id)).slug


def _type_out(bt: BookingType, org_slug: str) -> dict:
    return {"id": bt.id, "coach_id": bt.coach_id, "slug": bt.slug, "title": bt.title, "description": bt.description,
            "duration_minutes": bt.duration_minutes, "buffer_before_minutes": bt.buffer_before_minutes,
            "buffer_after_minutes": bt.buffer_after_minutes, "is_active": bt.is_active,
            "public_url": f"{get_settings().public_base_url.rstrip('/')}/r/{org_slug}/{bt.slug}"}


async def _own_type(s: AsyncSession, p: Principal, type_id: uuid.UUID) -> BookingType:
    bt = await s.get(BookingType, type_id)
    if bt is None or (bt.coach_id != p.user_id and p.role != Role.ORG_ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Type de rendez-vous introuvable")
    return bt


@router.get("/types")
async def list_types(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> list[dict]:
    q = select(BookingType).order_by(BookingType.title)
    if p.role not in _MANAGERS:
        q = q.where(BookingType.coach_id == p.user_id)
    slug = await _org_slug(s, p)
    return [_type_out(bt, slug) for bt in (await s.scalars(q)).all()]


@router.post("/types", status_code=201)
async def create_type(body: TypeIn, p: Principal = Depends(_coach), s: AsyncSession = Depends(db)) -> dict:
    await features.require(s, "google_booking")
    bt = BookingType(coach_id=p.user_id, **body.model_dump())
    try:
        async with s.begin_nested():
            s.add(bt)
            await s.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette adresse de réservation est déjà utilisée") from None
    await audit(s, actor_id=p.user_id, action="booking.type_created", target_type="booking_type", target_id=bt.id, ip=p.ip)
    return _type_out(bt, await _org_slug(s, p))


@router.patch("/types/{type_id}")
async def update_type(type_id: uuid.UUID, body: TypePatch, p: Principal = Depends(_coach), s: AsyncSession = Depends(db)) -> dict:
    bt = await _own_type(s, p, type_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "description":
            setattr(bt, k, v)
    await audit(s, actor_id=p.user_id, action="booking.type_updated", target_type="booking_type", target_id=bt.id, ip=p.ip)
    return _type_out(bt, await _org_slug(s, p))


@router.delete("/types/{type_id}", status_code=204)
async def delete_type(type_id: uuid.UUID, p: Principal = Depends(_coach), s: AsyncSession = Depends(db)) -> Response:
    bt = await _own_type(s, p, type_id)
    await s.delete(bt)  # les rendez-vous existants sont conservés (lien SET NULL)
    await audit(s, actor_id=p.user_id, action="booking.type_deleted", target_type="booking_type", target_id=type_id, ip=p.ip)
    return Response(status_code=204)


@router.get("/types/{type_id}/slots")
async def type_slots(type_id: uuid.UUID, start: date | None = None, days: int = Query(default=7, ge=1, le=14),
                     p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    """Vue coach : sans délai de prévenance (rendez-vous pris au téléphone, en séance…)."""
    bt = await _own_type(s, p, type_id) if p.role != Role.COORDINATOR else await s.get(BookingType, type_id)
    if bt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Type de rendez-vous introuvable")
    return await booking.slots(s, bt, start or datetime.now(UTC).date(), days, staff=True)


# ---------------------------------------------------------------- rendez-vous
class StaffBookIn(BaseModel):
    booking_type_id: uuid.UUID
    start: AwareDatetime
    attendee_name: str = Field(min_length=2, max_length=200)
    attendee_email: EmailStr
    attendee_phone: str | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=2000)
    contact_id: uuid.UUID | None = None


class StaffCancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _appt_out(a: Appointment) -> dict:
    return {"id": a.id, "title": a.title, "coach_id": a.coach_id, "contact_id": a.contact_id, "booking_type_id": a.booking_type_id,
            "attendee_name": a.attendee_name, "attendee_email": a.attendee_email, "attendee_phone": a.attendee_phone,
            "message": a.message, "starts_at": a.starts_at, "ends_at": a.ends_at, "timezone": a.timezone,
            "status": a.status, "source": a.source, "meet_url": a.meet_url, "sync_status": a.sync_status,
            "sync_error": a.sync_error, "cancel_reason": a.cancel_reason}


@router.get("/appointments")
async def list_appointments(start: AwareDatetime | None = None, end: AwareDatetime | None = None,
                            scope: str = Query(default="mine", pattern="^(mine|all)$"), include_cancelled: bool = False,
                            p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> list[dict]:
    start = start or datetime.now(UTC) - timedelta(hours=12)
    end = end or start + timedelta(days=60)
    q = select(Appointment).where(Appointment.starts_at < end, Appointment.ends_at > start).order_by(Appointment.starts_at).limit(500)
    if scope == "mine" or p.role not in _MANAGERS:
        q = q.where(Appointment.coach_id == p.user_id)
    if not include_cancelled:
        q = q.where(Appointment.status == "confirmed")
    return [_appt_out(a) for a in (await s.scalars(q)).all()]


@router.post("/appointments", status_code=201)
async def staff_book(body: StaffBookIn, p: Principal = Depends(_coach)) -> dict:
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        await features.require(s, "google_booking")
        bt = await _own_type(s, p, body.booking_type_id)
        a0 = body.start - timedelta(minutes=bt.buffer_before_minutes)
        a1 = body.start + timedelta(minutes=bt.duration_minutes + bt.buffer_after_minutes)
        if any(x < a1 and a0 < y for x, y in await booking.busy_or_503(s, bt.coach_id, a0, a1)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Le coach est déjà occupé sur cet horaire")
        appt, _ = await booking.create(s, bt, body.start, name=body.attendee_name, email=body.attendee_email,
                                       phone=body.attendee_phone, message=body.message, source="staff",
                                       contact_id=body.contact_id)
        await audit(s, actor_id=p.user_id, action="appointment.booked", target_type="appointment", target_id=appt.id,
                    ip=p.ip, source="staff")
        appt_id = appt.id
    await booking.push(p.org_id, appt_id)
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        return _appt_out(await s.get(Appointment, appt_id))


@router.post("/appointments/{appt_id}/cancel")
async def staff_cancel(appt_id: uuid.UUID, body: StaffCancelIn, p: Principal = Depends(_staff)) -> dict:
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        a = await s.get(Appointment, appt_id, with_for_update=True)
        if a is None or (a.coach_id != p.user_id and p.role not in _MANAGERS):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendez-vous introuvable")
        if a.status != "confirmed":
            raise HTTPException(status.HTTP_409_CONFLICT, "Rendez-vous déjà annulé")
        a.status, a.cancelled_at, a.cancel_reason = "cancelled", datetime.now(UTC), body.reason
        a.sync_status, a.sync_version = "pending", a.sync_version + 1
        await audit(s, actor_id=p.user_id, action="appointment.cancelled", target_type="appointment", target_id=a.id,
                    ip=p.ip, by="staff")
    await booking.push(p.org_id, appt_id)
    async with tenant_session(org_id=p.org_id, user_id=p.user_id) as s:
        return _appt_out(await s.get(Appointment, appt_id))
