"""Groupes de coachés (promotions, ateliers, dispositifs financés)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.core.security import STAFF_ROLES, Role
from app.models import Cohort, CohortMember, Contact
from app.services.audit import audit

router = APIRouter(prefix="/cohorts", tags=["groupes"])
_read = require(*STAFF_ROLES)
_write = require(Role.ORG_ADMIN, Role.COACH, Role.COORDINATOR)


class CohortIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    funder: str | None = Field(default=None, max_length=200)
    starts_on: AwareDatetime | None = None
    ends_on: AwareDatetime | None = None


class CohortPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    funder: str | None = Field(default=None, max_length=200)
    starts_on: AwareDatetime | None = None
    ends_on: AwareDatetime | None = None


def _out(c: Cohort, n: int | None = None) -> dict:
    return {"id": c.id, "name": c.name, "program": c.program, "funder": c.funder, "starts_on": c.starts_on,
            "ends_on": c.ends_on, **({"members": n} if n is not None else {})}


async def _get(s: AsyncSession, cohort_id: uuid.UUID) -> Cohort:
    c = await s.get(Cohort, cohort_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Groupe introuvable")
    return c


@router.get("")
async def list_cohorts(_: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> list[dict]:
    rows = (await s.execute(select(Cohort, func.count(CohortMember.id)).outerjoin(CohortMember, CohortMember.cohort_id == Cohort.id)
                            .group_by(Cohort.id).order_by(Cohort.created_at.desc()))).all()
    return [_out(c, n) for c, n in rows]


@router.post("", status_code=201)
async def create_cohort(body: CohortIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    c = Cohort(**body.model_dump())
    s.add(c)
    await s.flush()
    await audit(s, actor_id=p.user_id, action="cohort.create", target_type="cohort", target_id=c.id, ip=p.ip)
    return _out(c, 0)


@router.get("/{cohort_id}")
async def get_cohort(cohort_id: uuid.UUID, _: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> dict:
    c = await _get(s, cohort_id)
    members = (await s.scalars(select(Contact).join(CohortMember, CohortMember.contact_id == Contact.id)
                               .where(CohortMember.cohort_id == c.id).order_by(Contact.last_name, Contact.first_name))).all()
    return {**_out(c, len(members)), "contacts": [{"id": m.id, "first_name": m.first_name, "last_name": m.last_name,
                                                   "email": m.email, "status": m.status} for m in members]}


@router.patch("/{cohort_id}")
async def update_cohort(cohort_id: uuid.UUID, body: CohortPatch, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> dict:
    c = await _get(s, cohort_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    await audit(s, actor_id=p.user_id, action="cohort.update", target_type="cohort", target_id=c.id, ip=p.ip)
    return _out(c)


@router.delete("/{cohort_id}", status_code=204)
async def delete_cohort(cohort_id: uuid.UUID, p: Principal = Depends(require(Role.ORG_ADMIN)), s: AsyncSession = Depends(db)) -> Response:
    await s.delete(await _get(s, cohort_id))
    await audit(s, actor_id=p.user_id, action="cohort.delete", target_type="cohort", target_id=cohort_id, ip=p.ip)
    return Response(status_code=204)


@router.put("/{cohort_id}/members/{contact_id}", status_code=204)
async def add_member(cohort_id: uuid.UUID, contact_id: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Response:
    await _get(s, cohort_id)
    if await s.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    try:
        async with s.begin_nested():
            s.add(CohortMember(cohort_id=cohort_id, contact_id=contact_id))
            await s.flush()
    except IntegrityError:
        pass  # déjà membre : opération idempotente
    await audit(s, actor_id=p.user_id, action="cohort.member_added", target_type="cohort", target_id=cohort_id, ip=p.ip,
                contact=str(contact_id))
    return Response(status_code=204)


@router.delete("/{cohort_id}/members/{contact_id}", status_code=204)
async def remove_member(cohort_id: uuid.UUID, contact_id: uuid.UUID, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Response:
    m = await s.scalar(select(CohortMember).where(CohortMember.cohort_id == cohort_id, CohortMember.contact_id == contact_id))
    if m is not None:
        await s.delete(m)
        await audit(s, actor_id=p.user_id, action="cohort.member_removed", target_type="cohort", target_id=cohort_id, ip=p.ip,
                    contact=str(contact_id))
    return Response(status_code=204)


