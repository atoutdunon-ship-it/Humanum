"""Tableaux de pilotage : organisme (équipe) et plateforme (super-administration)."""
from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformPrincipal, Principal, db, pdb, platform_admin, require
from app.core.security import STAFF_ROLES, Role
from app.models import Organization, Plan
from app.services import pilotage
from app.services.synthesis import quota_left

router = APIRouter(prefix="/pilotage", tags=["pilotage"])
admin = APIRouter(prefix="/admin/pilotage", tags=["administration — pilotage"])
_staff = require(*STAFF_ROLES)
MANAGERS = frozenset({Role.ORG_ADMIN, Role.COORDINATOR})


def _scope(p: Principal, scope: str):
    return None if (p.role in MANAGERS and scope == "all") else p.user_id


@router.get("")
async def board(days: int = Query(default=90, ge=7, le=730), scope: str = Query(default="all", pattern="^(all|mine)$"),
                p: Principal = Depends(_staff), s: AsyncSession = Depends(db)) -> dict:
    out = await pilotage.compute(s, days=days, coach=_scope(p, scope))
    out["quota_left"] = await quota_left(s)
    out["can_see_all"] = p.role in MANAGERS
    return out


@router.get("/export.csv")
async def export(days: int = Query(default=90, ge=7, le=730), scope: str = Query(default="all", pattern="^(all|mine)$"),
                 p: Principal = Depends(_staff), s: AsyncSession = Depends(db)) -> Response:
    d = await pilotage.compute(s, days=days, coach=_scope(p, scope))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # séparateur attendu par Excel en français
    w.writerow(["Indicateur", "Valeur"])
    for k, v in (("Séances réalisées", d["sessions"]["done"]), ("Heures d'accompagnement", d["sessions"]["hours"]),
                 ("Annulations", d["sessions"]["cancelled"]), ("Taux d'annulation (%)", d["sessions"]["cancel_rate"]),
                 ("Réservations en ligne (%)", d["sessions"]["online_share"]), ("Coachés actifs", d["coachees"]["active"]),
                 ("Nouveaux contacts", d["coachees"]["new_contacts"]), ("Séances enregistrées (Fathom)", d["meetings"]["recorded"]),
                 ("Actions réalisées", d["tasks"]["done"])):
        w.writerow([k, "" if v is None else str(v).replace(".", ",")])
    w.writerow([])
    w.writerow(["Semaine du", "Séances réalisées"])
    for x in d["weekly"]:
        w.writerow([x["week"], x["sessions"]])
    if d["by_coach"]:
        w.writerow([])
        w.writerow(["Coach", "Séances", "Heures", "Coachés", "Séances enregistrées", "Synthèses"])
        for c in d["by_coach"]:
            w.writerow([c["coach"], c["sessions"], str(c["hours"]).replace(".", ","), c["coachees"], c["meetings"], c["syntheses"]])
    name = f"pilotage-{datetime.now(UTC):%Y%m%d}-{days}j.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@admin.get("")
async def platform_board(days: int = Query(default=30, ge=7, le=365), _: PlatformPrincipal = Depends(platform_admin),
                         s: AsyncSession = Depends(pdb)) -> dict:
    orgs = (await s.scalars(select(Organization))).all()
    prices = {p.code: p.monthly_price_cents for p in (await s.scalars(select(Plan))).all()}
    paying = [o for o in orgs if o.billing_status in ("active", "past_due")]
    trial = [o for o in orgs if o.billing_status == "trialing"]
    mrr = sum(prices.get(o.plan, 0) for o in paying)
    status_count: dict[str, int] = {}
    for o in orgs:
        status_count[o.billing_status] = status_count.get(o.billing_status, 0) + 1
    usage = (await s.execute(text("select * from hc_platform_usage(:since)"), {"since": datetime.now(UTC) - timedelta(days=days)})).mappings().all()
    names = {o.id: o for o in orgs}
    return {
        "period_days": days, "organizations": len(orgs), "active_organizations": sum(1 for o in orgs if o.is_active),
        "mrr_cents": mrr, "arr_cents": 12 * mrr, "trial_mrr_cents": sum(prices.get(o.plan, 0) for o in trial),
        "paying": len(paying), "trialing": len(trial), "past_due": status_count.get("past_due", 0),
        "canceled": status_count.get("canceled", 0) + status_count.get("unpaid", 0), "by_status": status_count,
        "by_plan": {code: sum(1 for o in paying if o.plan == code) for code in prices},
        "usage": sorted(({"organization": names[u["organization_id"]].name, "plan": names[u["organization_id"]].plan,
                          "billing_status": names[u["organization_id"]].billing_status,
                          **{k: int(u[k]) for k in ("coaches", "coachee_spaces", "contacts", "sessions", "meetings", "syntheses",
                                                     "input_tokens", "output_tokens")}}
                         for u in usage if u["organization_id"] in names), key=lambda x: -x["sessions"]),
    }
