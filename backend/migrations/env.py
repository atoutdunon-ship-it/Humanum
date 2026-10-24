"""Alembic asynchrone — migre avec la connexion PROPRIÉTAIRE (HC_DATABASE_OWNER_URL)."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (enregistre les tables)
from app.core.config import get_settings
from app.db.base import Base

target_metadata = Base.metadata


def _run(conn):
    context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    url = context.config.attributes.get("url") or get_settings().database_owner_url
    eng = create_async_engine(url)
    async with eng.connect() as conn:
        await conn.run_sync(_run)
    await eng.dispose()


asyncio.run(main())
