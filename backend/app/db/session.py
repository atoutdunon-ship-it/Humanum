"""Sessions asynchrones et contexte d'organisme.

Chaque requête ouvre UNE transaction et y fixe `app.current_org` / `app.current_user`
via set_config(..., is_local => true) : la valeur meurt avec la transaction, elle ne peut
donc pas fuir vers une autre requête qui réutiliserait la même connexion du pool.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine, _factory
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10)
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def factory() -> async_sessionmaker[AsyncSession]:
    engine()
    if _factory is None:  # pragma: no cover
        raise RuntimeError("moteur de base non initialisé")
    return _factory


async def dispose() -> None:
    global _engine, _factory, _pengine, _pfactory
    for e in (_engine, _pengine):
        if e is not None:
            await e.dispose()
    _engine = _factory = _pengine = _pfactory = None


_pengine: AsyncEngine | None = None
_pfactory: async_sessionmaker[AsyncSession] | None = None


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    """Session de super-administration (rôle humanum_platform : BYPASSRLS, tables de plateforme uniquement)."""
    global _pengine, _pfactory
    if _pengine is None:
        _pengine = create_async_engine(get_settings().database_platform_url, pool_pre_ping=True, pool_size=3)
        _pfactory = async_sessionmaker(_pengine, expire_on_commit=False)
    async with _pfactory() as session, session.begin():  # type: ignore[misc]
        yield session


async def set_context(session: AsyncSession, *, org_id: uuid.UUID | None, user_id: uuid.UUID | None) -> None:
    await session.execute(
        text("select set_config('app.current_org', :o, true), set_config('app.current_user', :u, true)"),
        {"o": str(org_id) if org_id else "", "u": str(user_id) if user_id else ""},
    )


@asynccontextmanager
async def tenant_session(*, org_id: uuid.UUID | None, user_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    async with factory()() as session, session.begin():
        await set_context(session, org_id=org_id, user_id=user_id)
        yield session
