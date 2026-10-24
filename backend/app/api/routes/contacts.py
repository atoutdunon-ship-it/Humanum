"""CRM — contacts. Aucune clause WHERE organization_id : la RLS de PostgreSQL l'impose."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.api.schemas import ContactIn, ContactOut
from app.core.security import STAFF_ROLES, Role
from app.models import Contact
from app.services.audit import audit

router = APIRouter(prefix="/contacts", tags=["crm"])
_read = require(*STAFF_ROLES)
_write = require(Role.ORG_ADMIN, Role.COACH)


@router.get("", response_model=list[ContactOut])
async def list_contacts(q: str | None = Query(default=None, max_length=100), stage: str | None = None,
                        limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                        _: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> list[Contact]:
    stmt = select(Contact).order_by(Contact.last_name, Contact.first_name).limit(limit).offset(offset)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Contact.last_name.ilike(like), Contact.first_name.ilike(like), Contact.email.ilike(like)))
    if stage:
        stmt = stmt.where(Contact.pipeline_stage == stage)
    return list((await s.scalars(stmt)).all())


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(body: ContactIn, p: Principal = Depends(_write), s: AsyncSession = Depends(db)) -> Contact:
    c = Contact(**body.model_dump(), owner_id=p.user_id)
    s.add(c)
    await s.flush()
    await s.refresh(c)
    await audit(s, actor_id=p.user_id, action="contact.create", target_type="contact", target_id=c.id, ip=p.ip)
    return c


@router.get("/{contact_id}", response_model=ContactOut)
async def get_contact(contact_id: uuid.UUID, p: Principal = Depends(_read), s: AsyncSession = Depends(db)) -> Contact:
    c = await s.get(Contact, contact_id)
    if c is None:  # inexistant OU appartenant à un autre organisme : même réponse
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    await audit(s, actor_id=p.user_id, action="contact.read", target_type="contact", target_id=c.id, ip=p.ip)
    return c


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(contact_id: uuid.UUID, body: ContactIn, p: Principal = Depends(_write),
                         s: AsyncSession = Depends(db)) -> Contact:
    c = await s.get(Contact, contact_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    await s.flush()
    await s.refresh(c)
    await audit(s, actor_id=p.user_id, action="contact.update", target_type="contact", target_id=c.id, ip=p.ip)
    return c
