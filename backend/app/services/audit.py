import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def audit(session: AsyncSession, *, actor_id: uuid.UUID | None, action: str, target_type: str,
                target_id: object | None = None, ip: str | None = None, **details: object) -> None:
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type,
                         target_id=str(target_id) if target_id else None, ip=ip, details=details))
