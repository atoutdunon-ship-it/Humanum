"""Synthèses en un clic : parcours d'un coaché, groupe, rapport financeur. Génération en arrière-plan (Claude)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.core import queue
from app.core.security import STAFF_ROLES, Role
from app.integrations import claude
from app.models import Cohort, Contact, Organization, Synthesis
from app.services import billing, features
from app.services.audit import audit
from app.services.synthesis import quota_left

router = APIRouter(prefix="/syntheses", tags=["synthèses"])
_read = require(*STAFF_ROLES)
_write = require(Role.ORG_ADMIN, Role.COACH, Role.COORDINATOR)
LABELS = {"parcours": "Synthèse de parcours", "groupe": "Synthèse de groupe", "financeur": "Rapport financeur"}


class SynthesisIn(BaseModel):
    kind: Literal["parcours", "groupe", "financeur"]
    contact_id: uuid.UUID | None = None
    cohort_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _subject(self) -> SynthesisIn:
        if (self.kind == "parcours") != (self.contact_id is not None) or (self.kind != "parcours") != (self.cohort_id is not None):
            raise ValueError("parcours : contact_id ; groupe et financeur : cohort_id")
        return self


def _out(x: Synthesis, full: bool = False) -> dict:
    d = {"id": x.id, "kind": x.kind, "title": x.title, "status": x.status, "contact_id": x.contact_id, "cohort_id": x.cohort_id,
         "created_at": x.created_at, "updated_at": x.updated_at, "error": x.error, "model": x.model,
         "shared_with_coachee": x.shared_with_coachee}
    if full:
        d |= {"content_md": x.content_md, "sources": x.sources, "input_tokens": x.input_tokens, "output_tokens": x.output_tokens}
    return d


@router.post("", status_code=202)
async def create(body: SynthesisIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    await features.require(s, "ai_syntheses")
    org = await s.get(Organization, p.org_id)
    billing.ensure_active(org)
    if not org.ai_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "L'organisme a désactivé l'intelligence artificielle")
    if not claude.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Génération indisponible : clé Anthropic non configurée sur la plateforme")
    left = await quota_left(s)
    if left == 0:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Quota mensuel de synthèses atteint pour votre formule")
    subject = await s.get(Contact, body.contact_id) if body.contact_id else await s.get(Cohort, body.cohort_id)
    if subject is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coaché ou groupe introuvable")
    name = f"{subject.first_name} {subject.last_name}" if isinstance(subject, Contact) else subject.name
    x = Synthesis(kind=body.kind, contact_id=body.contact_id, cohort_id=body.cohort_id, created_by=p.user_id,
                  title=f"{LABELS[body.kind]} — {name} — {datetime.now(UTC):%d/%m/%Y}")
    s.add(x)
    await s.flush()
    await audit(s, actor_id=p.user_id, action="synthesis.requested", target_type="synthesis", target_id=x.id, ip=p.ip, kind=body.kind)
    try:  # différé : la transaction de la requête est validée avant que le worker ne lise la demande
        await queue.enqueue("generate_synthesis", str(p.org_id), str(x.id), job_id=f"synthesis:{x.id}", defer_seconds=1)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "File de tâches indisponible : réessayez dans un instant") from None
    return {**_out(x), "quota_left": None if left is None else left - 1}


@router.get("")
async def list_(contact_id: uuid.UUID | None = None, cohort_id: uuid.UUID | None = None,
                _: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> list[dict]:
    q = select(Synthesis).order_by(Synthesis.created_at.desc()).limit(100)
    if contact_id:
        q = q.where(Synthesis.contact_id == contact_id)
    if cohort_id:
        q = q.where(Synthesis.cohort_id == cohort_id)
    return [_out(x) for x in (await s.scalars(q)).all()]


@router.get("/quota")
async def quota(_: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> dict:
    return {"left": await quota_left(s), "enabled": await features.enabled(s, "ai_syntheses") and claude.configured()}


@router.get("/{synthesis_id}")
async def get(synthesis_id: uuid.UUID, p: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> dict:
    x = await s.get(Synthesis, synthesis_id)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Synthèse introuvable")
    await audit(s, actor_id=p.user_id, action="synthesis.read", target_type="synthesis", target_id=x.id, ip=p.ip)
    return _out(x, full=True)


@router.delete("/{synthesis_id}", status_code=204)
async def delete(synthesis_id: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Response:
    x = await s.get(Synthesis, synthesis_id)
    if x is not None:
        await s.delete(x)
        await audit(s, actor_id=p.user_id, action="synthesis.delete", target_type="synthesis", target_id=synthesis_id, ip=p.ip)
    return Response(status_code=204)
