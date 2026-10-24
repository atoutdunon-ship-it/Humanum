"""Espace coaché.

- Côté équipe : invitation à l'espace, actions de parcours, partage explicite des séances et des synthèses.
- Côté coaché (/me) : uniquement SA fiche — rendez-vous, séances partagées (résumé et actions, jamais la
  transcription), documents partagés, actions à cocher. Le rôle « coachee » n'a accès à aucune autre route métier.
- Invitation : lien à usage unique de 7 jours (empreinte seule en base) ; création du compte ou rattachement
  d'un compte existant (mot de passe exigé).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.api.routes.fathom import _visible
from app.core import mfa as crypto
from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import Role, hash_password, password_policy_error, verify_password
from app.db.session import set_context, tenant_session
from app.models import (
    Appointment,
    BookingType,
    Contact,
    Invitation,
    Meeting,
    Membership,
    Organization,
    Synthesis,
    Task,
    User,
)
from app.services import agenda, billing
from app.services.audit import audit

staff = APIRouter(tags=["espace coaché — équipe"])
me = APIRouter(prefix="/me", tags=["espace coaché"])
public = APIRouter(prefix="/public/invitations", tags=["espace coaché"])
_write = require(Role.ORG_ADMIN, Role.COACH, Role.COORDINATOR)
_coachee = require(Role.COACHEE)
INVITE_DAYS = 7


# ---------------------------------------------------------------- équipe : invitation et accès
async def _contact(s: AsyncSession, cid: uuid.UUID) -> Contact:
    c = await s.get(Contact, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    return c


@staff.get("/contacts/{cid}/space")
async def space_status(cid: uuid.UUID, _: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    c = await _contact(s, cid)
    if c.user_id:
        u = await s.get(User, c.user_id)
        return {"status": "active", "email": u.email if u else None}
    inv = await s.scalar(select(Invitation).where(Invitation.contact_id == cid, Invitation.accepted_at.is_(None),
                                                  Invitation.revoked_at.is_(None), Invitation.expires_at > datetime.now(UTC))
                         .order_by(Invitation.created_at.desc()))
    return {"status": "invited", "email": inv.email, "expires_at": inv.expires_at} if inv else {"status": "none"}


@staff.post("/contacts/{cid}/invite", status_code=201)
async def invite(cid: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    c = await _contact(s, cid)
    if not c.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Renseignez d'abord l'e-mail du coaché")
    if c.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce coaché a déjà accès à son espace")
    org = await s.get(Organization, p.org_id)
    billing.ensure_active(org)
    await billing.ensure_capacity(s, org, "coachee")
    await s.execute(update(Invitation).where(Invitation.contact_id == cid, Invitation.accepted_at.is_(None),
                                             Invitation.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    raw = secrets.token_urlsafe(32)
    inv = Invitation(contact_id=cid, email=c.email.lower(), token_hash=agenda.hash_token(raw), created_by=p.user_id,
                     expires_at=datetime.now(UTC) + timedelta(days=INVITE_DAYS))
    s.add(inv)
    await s.flush()
    await audit(s, actor_id=p.user_id, action="coachee.invited", target_type="contact", target_id=cid, ip=p.ip)
    return {"invite_url": f"{get_settings().public_base_url.rstrip('/')}/invitation/{raw}", "expires_at": inv.expires_at,
            "email": inv.email}


@staff.delete("/contacts/{cid}/space", status_code=204)
async def revoke_space(cid: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Response:
    c = await _contact(s, cid)
    await s.execute(update(Invitation).where(Invitation.contact_id == cid, Invitation.accepted_at.is_(None),
                                             Invitation.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    if c.user_id:
        await s.execute(delete(Membership).where(Membership.user_id == c.user_id, Membership.role == Role.COACHEE))
        c.user_id = None
    await audit(s, actor_id=p.user_id, action="coachee.access_revoked", target_type="contact", target_id=cid, ip=p.ip)
    return Response(status_code=204)


# ---------------------------------------------------------------- équipe : actions de parcours et partage
class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    due_on: AwareDatetime | None = None
    visible_to_coachee: bool = True


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    due_on: AwareDatetime | None = None
    visible_to_coachee: bool | None = None
    done: bool | None = None


class ShareIn(BaseModel):
    shared: bool


def _task(t: Task) -> dict:
    return {"id": t.id, "contact_id": t.contact_id, "title": t.title, "notes": t.notes, "due_on": t.due_on,
            "done": t.done_at is not None, "done_at": t.done_at, "visible_to_coachee": t.visible_to_coachee,
            "source_meeting_id": t.source_meeting_id, "created_at": t.created_at}


@staff.get("/contacts/{cid}/tasks")
async def list_tasks(cid: uuid.UUID, _: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> list[dict]:
    await _contact(s, cid)
    rows = (await s.scalars(select(Task).where(Task.contact_id == cid).order_by(Task.done_at.is_not(None), Task.due_on.nulls_last(),
                                                                              Task.created_at))).all()
    return [_task(t) for t in rows]


@staff.post("/contacts/{cid}/tasks", status_code=201)
async def add_task(cid: uuid.UUID, body: TaskIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    await _contact(s, cid)
    t = Task(contact_id=cid, created_by=p.user_id, **body.model_dump())
    s.add(t)
    await s.flush()
    return _task(t)


@staff.patch("/tasks/{tid}")
async def update_task(tid: uuid.UUID, body: TaskPatch, _: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    t = await s.get(Task, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action introuvable")
    data = body.model_dump(exclude_unset=True)
    if "done" in data:
        t.done_at = datetime.now(UTC) if data.pop("done") else None
    for k, v in data.items():
        setattr(t, k, v)
    return _task(t)


@staff.delete("/tasks/{tid}", status_code=204)
async def delete_task(tid: uuid.UUID, _: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Response:
    t = await s.get(Task, tid)
    if t is not None:
        await s.delete(t)
    return Response(status_code=204)


@staff.post("/fathom/meetings/{mid}/share")
async def share_meeting(mid: uuid.UUID, body: ShareIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    m = await s.scalar(_visible(p).where(Meeting.id == mid))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Réunion introuvable")
    if body.shared and m.contact_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Rattachez d'abord la séance à un coaché")
    m.shared_with_coachee = body.shared
    await audit(s, actor_id=p.user_id, action="meeting.shared" if body.shared else "meeting.unshared", target_type="meeting",
                target_id=m.id, ip=p.ip)
    return {"id": m.id, "shared_with_coachee": m.shared_with_coachee}


@staff.post("/fathom/meetings/{mid}/tasks", status_code=201)
async def tasks_from_meeting(mid: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> list[dict]:
    """Reprend les actions détectées par Fathom en actions de parcours (sans doublon)."""
    m = await s.scalar(_visible(p).where(Meeting.id == mid))
    if m is None or m.contact_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Séance introuvable ou non rattachée à un coaché")
    have = set((await s.scalars(select(Task.title).where(Task.source_meeting_id == m.id))).all())
    out = []
    for a in m.action_items or []:
        title = (a.get("description") or "").strip()[:300]
        if len(title) >= 2 and title not in have:
            t = Task(contact_id=m.contact_id, title=title, source_meeting_id=m.id, created_by=p.user_id,
                     done_at=datetime.now(UTC) if a.get("completed") else None)
            s.add(t)
            out.append(t)
    await s.flush()
    return [_task(t) for t in out]


@staff.post("/syntheses/{sid}/share")
async def share_synthesis(sid: uuid.UUID, body: ShareIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    x = await s.get(Synthesis, sid)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Synthèse introuvable")
    if body.shared and (x.kind != "parcours" or x.status != "done"):
        # Une synthèse de groupe ou un rapport financeur parlent d'autres personnes : jamais partagés à un coaché.
        raise HTTPException(status.HTTP_409_CONFLICT, "Seule une synthèse de parcours terminée peut être partagée au coaché")
    x.shared_with_coachee = body.shared
    await audit(s, actor_id=p.user_id, action="synthesis.shared" if body.shared else "synthesis.unshared",
                target_type="synthesis", target_id=x.id, ip=p.ip)
    return {"id": x.id, "shared_with_coachee": x.shared_with_coachee}


# ---------------------------------------------------------------- invitation (public)
class AcceptIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    full_name: str | None = Field(default=None, min_length=2, max_length=200)


async def _invitation(s: AsyncSession, token: str) -> Invitation:
    digest = agenda.hash_token(token)
    org = await s.scalar(text("select hc_invitation_org(:h)"), {"h": digest})
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation introuvable")
    await set_context(s, org_id=org, user_id=None)
    inv = await s.scalar(select(Invitation).where(Invitation.token_hash == digest).with_for_update())
    if inv.accepted_at or inv.revoked_at or inv.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "Invitation expirée ou déjà utilisée : demandez-en une nouvelle à votre coach")
    return inv


@public.get("/{token}")
async def invitation(token: str) -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        inv = await _invitation(s, token)
        c = await s.get(Contact, inv.contact_id)
        org = await s.get(Organization, inv.organization_id)
        existing = await s.scalar(select(User.id).where(User.email == inv.email)) is not None
        return {"organization": org.name, "first_name": c.first_name, "last_name": c.last_name, "email": inv.email,
                "existing_account": existing, "expires_at": inv.expires_at}


@public.post("/{token}/accept")
async def accept(token: str, body: AcceptIn, request: Request) -> dict:
    key = f"invite:{request.client.host if request.client else '?'}"
    await ratelimit.check(key)
    async with tenant_session(org_id=None, user_id=None) as s:
        inv = await _invitation(s, token)
        c = await s.get(Contact, inv.contact_id)
        user = await s.scalar(select(User).where(User.email == inv.email))
        if user is not None:
            if not user.is_active or not verify_password(body.password, user.password_hash):
                await ratelimit.fail(key)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Compte existant : saisissez son mot de passe actuel")
        else:
            if err := password_policy_error(body.password):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)
            user = User(email=inv.email, full_name=body.full_name or f"{c.first_name} {c.last_name}",
                        password_hash=hash_password(body.password))
            s.add(user)
            await s.flush()
        await set_context(s, org_id=inv.organization_id, user_id=user.id)
        if await s.scalar(select(Membership.id).where(Membership.user_id == user.id)) is None:
            s.add(Membership(user_id=user.id, role=Role.COACHEE))
        c.user_id, inv.accepted_at = user.id, datetime.now(UTC)
        await audit(s, actor_id=user.id, action="coachee.joined", target_type="contact", target_id=c.id,
                    ip=request.client.host if request.client else None)
        return {"email": user.email, "organization_id": inv.organization_id}


# ---------------------------------------------------------------- espace du coaché (/me)
async def _mine(s: AsyncSession, p: Principal) -> Contact:
    c = await s.scalar(select(Contact).where(Contact.user_id == p.user_id))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucune fiche de parcours n'est reliée à votre compte")
    return c


@me.get("")
async def overview(p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> dict:
    c = await _mine(s, p)
    org = await s.get(Organization, p.org_id)
    coach = await s.get(User, c.owner_id) if c.owner_id else None
    links = []
    if c.owner_id:
        for bt in (await s.scalars(select(BookingType).where(BookingType.coach_id == c.owner_id, BookingType.is_active.is_(True)))).all():
            links.append({"title": bt.title, "duration_minutes": bt.duration_minutes,
                          "url": f"{get_settings().public_base_url.rstrip('/')}/r/{org.slug}/{bt.slug}"})
    open_tasks = len((await s.scalars(select(Task.id).where(Task.contact_id == c.id, Task.visible_to_coachee.is_(True),
                                                            Task.done_at.is_(None)))).all())
    return {"first_name": c.first_name, "last_name": c.last_name, "email": c.email, "organization": org.name,
            "coach_name": coach.full_name if coach else None, "booking_links": links, "open_tasks": open_tasks}


def _appts(c: Contact):
    cond = Appointment.contact_id == c.id
    return select(Appointment).where(or_(cond, Appointment.attendee_email == (c.email or "-").lower()))


@me.get("/appointments")
async def my_appointments(p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> list[dict]:
    c = await _mine(s, p)
    now = datetime.now(UTC)
    out = []
    for a in (await s.scalars(_appts(c).order_by(Appointment.starts_at.desc()).limit(100))).all():
        upcoming = a.status == "confirmed" and a.starts_at > now
        out.append({"id": a.id, "title": a.title.split(" — ")[0], "starts_at": a.starts_at, "ends_at": a.ends_at,
                    "timezone": a.timezone, "status": a.status, "upcoming": upcoming,
                    "meet_url": a.meet_url if upcoming else None,
                    "manage_url": agenda.manage_url(crypto.decrypt(a.manage_token_enc)) if upcoming else None})
    return out


@me.get("/sessions")
async def my_sessions(p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> list[dict]:
    c = await _mine(s, p)
    rows = (await s.scalars(select(Meeting).where(Meeting.contact_id == c.id, Meeting.shared_with_coachee.is_(True))
                            .order_by(Meeting.started_at.desc().nulls_last()))).all()
    return [{"id": m.id, "title": m.title, "started_at": m.started_at, "summary_md": m.summary_md,
             "action_items": m.action_items} for m in rows]  # jamais la transcription


@me.get("/documents")
async def my_documents(p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> list[dict]:
    c = await _mine(s, p)
    rows = (await s.scalars(select(Synthesis).where(Synthesis.contact_id == c.id, Synthesis.kind == "parcours",
                                                    Synthesis.status == "done", Synthesis.shared_with_coachee.is_(True))
                            .order_by(Synthesis.created_at.desc()))).all()
    return [{"id": x.id, "title": x.title, "created_at": x.created_at, "content_md": x.content_md} for x in rows]


@me.get("/tasks")
async def my_tasks(p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> list[dict]:
    c = await _mine(s, p)
    rows = (await s.scalars(select(Task).where(Task.contact_id == c.id, Task.visible_to_coachee.is_(True))
                            .order_by(Task.done_at.is_not(None), Task.due_on.nulls_last(), Task.created_at))).all()
    return [{k: v for k, v in _task(t).items() if k not in ("visible_to_coachee", "contact_id")} for t in rows]


class DoneIn(BaseModel):
    done: bool


@me.patch("/tasks/{tid}")
async def tick(tid: uuid.UUID, body: DoneIn, p: Principal = Depends(_coachee), s: AsyncSession = Depends(db)) -> dict:
    c = await _mine(s, p)
    t = await s.scalar(select(Task).where(Task.id == tid, Task.contact_id == c.id, Task.visible_to_coachee.is_(True)))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action introuvable")
    t.done_at = datetime.now(UTC) if body.done else None
    await audit(s, actor_id=p.user_id, action="task.done" if body.done else "task.reopened", target_type="task", target_id=t.id, ip=p.ip)
    return {"id": t.id, "done": body.done}
