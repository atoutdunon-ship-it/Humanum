"""Ingestion des réunions Fathom : enregistrement idempotent (recording_id), rattachement au coaché et au rendez-vous."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mfa as crypto
from app.integrations import fathom
from app.models import Appointment, Contact, FathomAccount, Meeting, User

HISTORY_DAYS = 90
MATCH_WINDOW = timedelta(minutes=45)


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


async def upsert(s: AsyncSession, acc: FathomAccount, m: dict) -> Meeting:
    rid = int(m["recording_id"])
    mt = await s.scalar(select(Meeting).where(Meeting.recording_id == rid)) or Meeting(recording_id=rid, coach_id=acc.user_id)
    start = _dt(m.get("recording_start_time")) or _dt(m.get("scheduled_start_time"))
    mt.title = (m.get("meeting_title") or m.get("title") or "Réunion")[:300]
    mt.url, mt.share_url, mt.language = m.get("url"), m.get("share_url"), m.get("transcript_language")
    mt.started_at, mt.ended_at = start, _dt(m.get("recording_end_time")) or _dt(m.get("scheduled_end_time"))
    mt.invitees = [{"name": i.get("name"), "email": (i.get("email") or "").lower() or None, "is_external": bool(i.get("is_external"))}
                   for i in m.get("calendar_invitees") or []]
    mt.summary_md = (m.get("default_summary") or {}).get("markdown_formatted") or mt.summary_md
    if m.get("action_items") is not None:
        mt.action_items = [{"description": a.get("description"), "completed": bool(a.get("completed")),
                            "assignee": (a.get("assignee") or {}).get("name")} for a in m["action_items"]]
    if m.get("transcript") is not None:
        mt.transcript = [{"speaker": (t.get("speaker") or {}).get("display_name"), "text": t.get("text"), "t": t.get("timestamp")}
                         for t in m["transcript"]]
    if mt.contact_id is None or mt.appointment_id is None:
        await _match(s, acc, mt)
    s.add(mt)
    await s.flush()
    return mt


async def _match(s: AsyncSession, acc: FathomAccount, mt: Meeting) -> None:
    coach = await s.get(User, acc.user_id)
    own = {acc.fathom_email.lower(), (coach.email if coach else "").lower()}
    emails = [i["email"] for i in mt.invitees if i.get("email") and i["email"] not in own]
    if not emails:
        return
    if mt.appointment_id is None and mt.started_at:
        appt = await s.scalar(select(Appointment).where(
            Appointment.coach_id == acc.user_id, Appointment.status == "confirmed",
            func.lower(Appointment.attendee_email).in_(emails),
            Appointment.starts_at.between(mt.started_at - MATCH_WINDOW, mt.started_at + MATCH_WINDOW)))
        if appt is not None:
            mt.appointment_id = appt.id
            mt.contact_id = mt.contact_id or appt.contact_id
    if mt.contact_id is None:
        mt.contact_id = await s.scalar(select(Contact.id).where(func.lower(Contact.email).in_(emails)).limit(1))


async def import_history(s: AsyncSession, acc: FathomAccount) -> int:
    since = acc.last_sync_at - timedelta(hours=6) if acc.last_sync_at else datetime.now(UTC) - timedelta(days=HISTORY_DAYS)
    n = 0
    async for m in fathom.meetings(crypto.decrypt(acc.api_key_enc), acc.fathom_email, since):
        await upsert(s, acc, m)
        n += 1
    acc.last_sync_at, acc.status, acc.last_error = datetime.now(UTC), "active", None
    return n


