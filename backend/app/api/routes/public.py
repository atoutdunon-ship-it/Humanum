"""Lecture publique (sans compte) des textes et des tarifs, et options effectives d'un organisme."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, principal
from app.db.session import tenant_session
from app.models import FeatureFlag, OrgFeature, Plan, PlatformText

router = APIRouter(tags=["public"])


@router.get("/public/texts")
async def texts(locale: str = "fr") -> dict[str, str]:
    async with tenant_session(org_id=None, user_id=None) as s:
        rows = (await s.execute(select(PlatformText.key, PlatformText.value).where(PlatformText.locale == locale))).all()
    return dict(rows)


@router.get("/public/plans")
async def plans() -> list[dict]:
    async with tenant_session(org_id=None, user_id=None) as s:
        rows = (await s.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order))).all()
    return [{"code": p.code, "label": p.label, "monthly_price_cents": p.monthly_price_cents, "currency": p.currency,
             "max_coaches": p.max_coaches, "max_active_coachees": p.max_active_coachees,
             "ai_syntheses_per_month": p.ai_syntheses_per_month, "exports": p.exports, "trial_days": p.trial_days} for p in rows]


@router.get("/features")
async def features(_: Principal = Depends(principal), s: AsyncSession = Depends(db)) -> dict[str, bool]:
    """Options effectives de l'organisme courant (surcharges lues sous RLS)."""
    ov = dict((await s.execute(select(OrgFeature.key, OrgFeature.enabled))).all())
    return {f.key: ov.get(f.key, f.enabled_by_default) for f in (await s.scalars(select(FeatureFlag))).all()}
