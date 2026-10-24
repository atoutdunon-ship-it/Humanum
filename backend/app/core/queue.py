"""File de tâches Arq (Redis) : tout traitement long ou dépendant d'un tiers part en arrière-plan.

Lots suivants : ingestion des réunions Fathom, synthèses Claude, rappels de rendez-vous Google, exports.
Les tâches ne reçoivent que des identifiants : chaque tâche rouvre sa propre session cloisonnée.
"""
from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job

from app.core.config import get_settings

QUEUE = "hc:queue"
_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    url = get_settings().redis_url
    if not url:
        raise RuntimeError("HC_REDIS_URL non renseignée : la file de tâches exige Redis")
    return RedisSettings.from_dsn(url)


async def pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings(), default_queue_name=QUEUE)
    return _pool


async def enqueue(task: str, *args: object, job_id: str | None = None, defer_seconds: int | None = None) -> Job | None:
    """`job_id` rend la mise en file idempotente (un même webhook reçu deux fois ne crée qu'une tâche)."""
    return await (await pool()).enqueue_job(task, *args, _job_id=job_id, _defer_by=defer_seconds, _queue_name=QUEUE)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
