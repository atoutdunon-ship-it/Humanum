"""Indicateurs de pilotage d'un organisme — calculés en SQL, sous la RLS de l'organisme courant.

Périmètre : l'administrateur et le coordinateur voient tout l'organisme ; un coach voit son activité
(ses rendez-vous, ses séances, ses coachés, ses synthèses).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, distinct, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Contact, Meeting, Membership, Synthesis, Task, User

TZ = "America/Martinique"
STAGES = ("prospect", "devis", "contractualise", "en_cours", "termine")


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


async def compute(s: AsyncSession, *, days: int, coach: uuid.UUID | None) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    ap = [Appointment.coach_id == coach] if coach else []
    mt = [Meeting.coach_id == coach] if coach else []
    ct = [Contact.owner_id == coach] if coach else []
    sy = [Synthesis.created_by == coach] if coach else []

    done = and_(Appointment.status == "confirmed", Appointment.starts_at >= since, Appointment.ends_at <= now, *ap)
    r = (await s.execute(select(
        func.count().filter(done),
        func.coalesce(func.sum(case((done, func.extract("epoch", Appointment.ends_at - Appointment.starts_at)), else_=0)) / 3600, 0),
        func.count().filter(and_(Appointment.status == "cancelled", Appointment.cancelled_at >= since, *ap)),
        func.count().filter(and_(done, Appointment.source == "public")),
        func.count().filter(and_(Appointment.status == "confirmed", Appointment.starts_at > now,
                                 Appointment.starts_at <= now + timedelta(days=28), *ap)),
    ))).one()
    sessions, hours_done, cancelled, online, upcoming = int(r[0]), float(r[1]), int(r[2]), int(r[3]), int(r[4])

    active_ids = union(
        select(Appointment.contact_id).where(done, Appointment.contact_id.is_not(None)),
        select(Meeting.contact_id).where(Meeting.started_at >= since, Meeting.contact_id.is_not(None), *mt)).subquery()
    active = await s.scalar(select(func.count()).select_from(active_ids))

    pipe = dict((await s.execute(select(Contact.pipeline_stage, func.count()).where(Contact.kind == "coachee", *ct)
                                 .group_by(Contact.pipeline_stage))).all())
    new_contacts = await s.scalar(select(func.count()).select_from(Contact).where(Contact.created_at >= since, *ct))
    m = (await s.execute(select(func.count(), func.count(Meeting.contact_id)).where(Meeting.started_at >= since, *mt))).one()
    syn = dict((await s.execute(select(Synthesis.kind, func.count()).where(Synthesis.created_at >= since, Synthesis.status == "done", *sy)
                                .group_by(Synthesis.kind))).all())
    tq = [Task.contact_id.in_(select(Contact.id).where(*ct))] if coach else []
    t = (await s.execute(select(func.count().filter(Task.created_at >= since), func.count().filter(Task.done_at >= since),
                                func.count().filter(Task.done_at.is_(None))).where(*tq))).one()

    week = func.date_trunc("week", func.timezone(TZ, Appointment.starts_at))
    first_week = (now - timedelta(weeks=11)).astimezone(UTC)
    rows = dict((await s.execute(select(week, func.count()).where(
        Appointment.status == "confirmed", Appointment.ends_at <= now, Appointment.starts_at >= first_week - timedelta(days=7), *ap)
        .group_by(week))).all())
    local = datetime.now(ZoneInfo(TZ))
    monday = datetime.combine(local.date() - timedelta(days=local.weekday()), datetime.min.time())  # semaines en heure locale
    series = [{"week": (monday - timedelta(weeks=i)).date().isoformat(), "sessions": int(rows.get(monday - timedelta(weeks=i), 0))}
              for i in range(11, -1, -1)]

    by_coach = []
    if coach is None:
        coaches = (await s.execute(select(User.id, User.full_name).join(Membership, Membership.user_id == User.id)
                                   .where(Membership.role.in_(["coach", "org_admin"])).order_by(User.full_name))).all()
        for uid, name in coaches:
            d = and_(Appointment.status == "confirmed", Appointment.starts_at >= since, Appointment.ends_at <= now, Appointment.coach_id == uid)
            a = (await s.execute(select(func.count(), func.coalesce(func.sum(func.extract("epoch", Appointment.ends_at - Appointment.starts_at)) / 3600, 0),
                                        func.count(distinct(Appointment.contact_id))).where(d))).one()
            nm = await s.scalar(select(func.count()).select_from(Meeting).where(Meeting.coach_id == uid, Meeting.started_at >= since))
            ns = await s.scalar(select(func.count()).select_from(Synthesis).where(Synthesis.created_by == uid, Synthesis.created_at >= since,
                                                                                  Synthesis.status == "done"))
            by_coach.append({"coach": name, "sessions": int(a[0]), "hours": round(float(a[1]), 1), "coachees": int(a[2]),
                             "meetings": int(nm or 0), "syntheses": int(ns or 0)})

    stages = [{"stage": k, "count": int(pipe.get(k, 0))} for k in STAGES]
    signed = sum(x["count"] for x in stages if x["stage"] in ("contractualise", "en_cours", "termine"))
    return {
        "period_days": days, "since": since, "scope": "coach" if coach else "organisme",
        "sessions": {"done": sessions, "hours": round(hours_done, 1), "cancelled": cancelled,
                     "cancel_rate": _pct(cancelled, sessions + cancelled), "online_share": _pct(online, sessions), "upcoming_28d": upcoming},
        "coachees": {"active": int(active or 0), "new_contacts": int(new_contacts or 0)},
        "pipeline": {"stages": stages, "conversion_rate": _pct(signed, sum(x["count"] for x in stages))},
        "meetings": {"recorded": int(m[0]), "matched_rate": _pct(int(m[1]), int(m[0]))},
        "syntheses": {k: int(syn.get(k, 0)) for k in ("parcours", "groupe", "financeur")},
        "tasks": {"created": int(t[0]), "done": int(t[1]), "open": int(t[2])},
        "weekly": series, "by_coach": by_coach,
    }


