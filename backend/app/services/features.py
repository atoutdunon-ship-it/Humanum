"""Options effectives d'un organisme (valeur par défaut de la plateforme, surchargée par organisme). Lecture sous RLS."""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeatureFlag, OrgFeature


async def enabled(s: AsyncSession, key: str) -> bool:
    override = await s.scalar(select(OrgFeature.enabled).where(OrgFeature.key == key))
    if override is not None:
        return override
    return bool(await s.scalar(select(FeatureFlag.enabled_by_default).where(FeatureFlag.key == key)))


async def require(s: AsyncSession, key: str) -> None:
    if not await enabled(s, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option non activée pour cet organisme")
