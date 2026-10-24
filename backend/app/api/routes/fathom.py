"""Fathom : connexion du compte de chaque coach, réception des webhooks signés, réunions rattachées au CRM."""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.core import mfa as crypto
from app.core import queue
from app.core.config import get_settings
from app.core.security import STAFF_ROLES, Role
from app.db.session import set_context, tenant_session
from app.integrations import fathom
from app.models import Contact, FathomAccount, Meeting, User
from app.services import agenda, features
from app.services.audit import audit
from app.services.fathom_sync import upsert

router = APIRouter(prefix="/fathom", tags=["fathom"])
hooks = APIRouter(prefix="/integrations/fathom", tags=["fathom"])
_staff = require(*STAFF_ROLES)
_MANAGERS = frozenset({Role.ORG_ADMIN, Role.COORDINATOR})


async def _feature(p: Principal = Depends(_staff), s: AsyncSession = Depends(db)) -> Principal:
    await features.require(s, "fathom")
    return p


class ConnectIn(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)
    fathom_email: EmailStr | None = None  # adresse du compte Fathom, si différente de celle de la plateforme


class MeetingPatch(BaseModel):
    contact_id: uuid.UUID | None


async def _enqueue_import(org: uuid.UUID, account: uuid.UUID, delay: int | None = None) -> bool:
    """`delay` : la transaction de la requête est validée avant que le worker ne lise le compte."""
    try:
        await queue.enqueue("import_fathom", str(org), str(account), job_id=f"fathom-import:{account}:{datetime.now(UTC):%Y%m%d%H%M}", defer_seconds=delay)
        return True
    except Exception:  # noqa: BLE001 — Redis indisponible : l'import pourra être relancé depuis l'interface
        return False


@router.get("")
async def status_(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    acc = await s.scalar(select(FathomAccount).where(FathomAccount.user_id == p.user_id))
    n = await s.scalar(select(func.count()).select_from(Meeting).where(Meeting.coach_id == p.user_id))
    return {"connected": acc is not None, "fathom_email": acc.fathom_email if acc else None, "status": acc.status if acc else None,
            "last_error": acc.last_error if acc else None, "last_sync_at": acc.last_sync_at if acc else None, "meetings": n}


@router.post("/connect")
async def connect(body: ConnectIn, p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    try:
        await fathom.check_key(body.api_key)
    except fathom.FathomAuthError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Clé d'API Fathom refusée : vérifiez-la dans Fathom › Réglages › API") from None
    except fathom.FathomError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Fathom ne répond pas : réessayez dans quelques minutes") from None
    old = await s.scalar(select(FathomAccount).where(FathomAccount.user_id == p.user_id))
    if old is not None and old.webhook_id:
        await fathom.delete_webhook(crypto.decrypt(old.api_key_enc), old.webhook_id)
    raw = secrets.token_urlsafe(32)
    dest = f"{get_settings().public_base_url.rstrip('/')}/api/v1/integrations/fathom/webhook/{raw}"
    try:
        hook = await fathom.create_webhook(body.api_key, dest)
    except fathom.FathomError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Création du webhook Fathom impossible : {e}") from None
    user = await s.get(User, p.user_id)
    acc = old or FathomAccount(user_id=p.user_id)
    acc.fathom_email = (body.fathom_email or user.email).lower()
    acc.api_key_enc, acc.webhook_id, acc.webhook_secret_enc = crypto.encrypt(body.api_key), hook["id"], crypto.encrypt(hook["secret"])
    acc.hook_token_hash, acc.status, acc.last_error = agenda.hash_token(raw), "active", None
    s.add(acc)
    await s.flush()
    await audit(s, actor_id=p.user_id, action="fathom.connected", target_type="user", target_id=p.user_id, ip=p.ip)
    return {"connected": True, "import_queued": await _enqueue_import(p.org_id, acc.id, delay=3)}


@router.post("/sync", status_code=202)
async def sync(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    acc = await s.scalar(select(FathomAccount).where(FathomAccount.user_id == p.user_id))
    if acc is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Compte Fathom non connecté")
    if not await _enqueue_import(p.org_id, acc.id):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "File de tâches indisponible : réessayez")
    return {"queued": True}


@router.delete("", status_code=204)
async def disconnect(p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> Response:
    acc = await s.scalar(select(FathomAccount).where(FathomAccount.user_id == p.user_id))
    if acc is not None:
        if acc.webhook_id:
            await fathom.delete_webhook(crypto.decrypt(acc.api_key_enc), acc.webhook_id)
        await s.delete(acc)  # les réunions déjà importées restent au dossier des coachés
        await audit(s, actor_id=p.user_id, action="fathom.disconnected", target_type="user", target_id=p.user_id, ip=p.ip)
    return Response(status_code=204)


def _visible(p: Principal):
    q = select(Meeting)
    return q if p.role in _MANAGERS else q.where(Meeting.coach_id == p.user_id)  # secret de l'accompagnement


def _out(m: Meeting, full: bool = False) -> dict:
    d = {"id": m.id, "recording_id": m.recording_id, "title": m.title, "coach_id": m.coach_id, "contact_id": m.contact_id,
         "appointment_id": m.appointment_id, "started_at": m.started_at, "ended_at": m.ended_at, "url": m.url,
         "share_url": m.share_url, "invitees": m.invitees, "summary_md": m.summary_md, "action_items": m.action_items,
         "shared_with_coachee": m.shared_with_coachee}
    if full:
        d["transcript"] = m.transcript
    return d


@router.get("/meetings")
async def list_meetings(contact_id: uuid.UUID | None = None, unmatched: bool = False, p: Principal = Depends(_feature),
                        s: AsyncSession = Depends(db)) -> list[dict]:
    q = _visible(p).order_by(Meeting.started_at.desc().nulls_last()).limit(200)
    if contact_id:
        q = q.where(Meeting.contact_id == contact_id)
    if unmatched:
        q = q.where(Meeting.contact_id.is_(None))
    return [_out(m) for m in (await s.scalars(q)).all()]


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: uuid.UUID, p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    m = await s.scalar(_visible(p).where(Meeting.id == meeting_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Réunion introuvable")
    await audit(s, actor_id=p.user_id, action="meeting.read", target_type="meeting", target_id=m.id, ip=p.ip)
    return _out(m, full=True)


@router.patch("/meetings/{meeting_id}")
async def link_meeting(meeting_id: uuid.UUID, body: MeetingPatch, p: Principal = Depends(_feature), s: AsyncSession = Depends(db)) -> dict:
    m = await s.scalar(_visible(p).where(Meeting.id == meeting_id))
    if m is None or (body.contact_id and await s.get(Contact, body.contact_id) is None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Réunion ou contact introuvable")
    m.contact_id = body.contact_id
    await audit(s, actor_id=p.user_id, action="meeting.linked", target_type="meeting", target_id=m.id, ip=p.ip,
                contact=str(body.contact_id) if body.contact_id else None)
    return _out(m)


@hooks.post("/webhook/{token}", status_code=204)
async def webhook(token: str, request: Request) -> Response:
    """Réception « nouvelle réunion prête ». Signature vérifiée sur le corps brut, idempotent par recording_id."""
    raw = await request.body()
    async with tenant_session(org_id=None, user_id=None) as s:
        row = (await s.execute(text("select organization_id, account_id from hc_fathom_hook(:h)"),
                               {"h": agenda.hash_token(token)})).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Destination inconnue")
        await set_context(s, org_id=row.organization_id, user_id=None)
        acc = await s.get(FathomAccount, row.account_id)
        if acc is None or not acc.webhook_secret_enc or not fathom.verify_webhook(crypto.decrypt(acc.webhook_secret_enc), request.headers, raw):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature invalide")
        if not await features.enabled(s, "fathom"):
            return Response(status_code=204)  # option coupée : accusé de réception sans conservation
        try:
            payload = json.loads(raw)
            int(payload["recording_id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Contenu inattendu") from None
        m = await upsert(s, acc, payload)
        await audit(s, actor_id=None, action="meeting.received", target_type="meeting", target_id=m.id,
                    recording_id=m.recording_id, webhook_id=request.headers.get("webhook-id"))
    return Response(status_code=204)
