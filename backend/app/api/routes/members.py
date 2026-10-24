"""Gestion des membres de l'organisme courant (administrateur d'organisme)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, db, require
from app.api.schemas import MemberCreate, MemberOut
from app.core.security import Role, hash_password
from app.models import Membership, Organization, User
from app.services import billing
from app.services.audit import audit

router = APIRouter(prefix="/members", tags=["membres"])


@router.get("", response_model=list[MemberOut])
async def list_members(_: Principal = Depends(require(Role.ORG_ADMIN, Role.COORDINATOR)),
                       s: AsyncSession = Depends(db)) -> list[MemberOut]:
    rows = (await s.execute(select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                            .order_by(User.full_name))).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=r) for u, r in rows]


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(body: MemberCreate, p: Principal = Depends(require(Role.ORG_ADMIN)),
                     s: AsyncSession = Depends(db)) -> MemberOut:
    if body.role in (Role.COACH, Role.ORG_ADMIN):
        await billing.ensure_capacity(s, await s.get(Organization, p.org_id), "coach")
    user = await s.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        user = User(email=body.email.lower(), full_name=body.full_name, password_hash=hash_password(body.password))
        s.add(user)
        await s.flush()
    elif await s.scalar(select(Membership.id).where(Membership.user_id == user.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Déjà membre de cet organisme")
    s.add(Membership(user_id=user.id, role=body.role))
    await audit(s, actor_id=p.user_id, action="member.add", target_type="user", target_id=user.id,
                ip=p.ip, role=body.role.value)
    return MemberOut(user_id=user.id, email=user.email, full_name=user.full_name, role=body.role)
