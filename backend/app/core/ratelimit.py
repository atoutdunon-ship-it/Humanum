"""Limitation des tentatives (connexion, codes de double authentification) — fenêtre glissante de 15 minutes.

Stockage Redis partagé entre toutes les instances de l'API (ZSET horodaté par clé, expirant seul).
Sans `HC_REDIS_URL`, ou si Redis tombe : repli en mémoire locale, journalisé — la protection reste active
sur chaque instance plutôt que de disparaître.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, status

from app.core.config import get_settings

WINDOW = 15 * 60
_PREFIX = "hc:rl:"
log = logging.getLogger("humanum.ratelimit")
_hits: dict[str, deque[float]] = defaultdict(deque)
_client = None  # redis.asyncio.Redis, créé à la première utilisation


def _redis():
    global _client
    url = get_settings().redis_url
    if not url:
        return None
    if _client is None:
        from redis.asyncio import Redis
        _client = Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _too_many() -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives, réessayez dans 15 minutes",
                         headers={"Retry-After": str(WINDOW)})


def _mem_count(key: str, now: float) -> int:
    q = _hits[key]
    while q and now - q[0] > WINDOW:
        q.popleft()
    return len(q)


async def count(key: str) -> int:
    now = time.time()
    r = _redis()
    if r is not None:
        try:
            async with r.pipeline(transaction=True) as p:
                p.zremrangebyscore(_PREFIX + key, 0, now - WINDOW)
                p.zcard(_PREFIX + key)
                return int((await p.execute())[1])
        except Exception as e:  # noqa: BLE001 — Redis indisponible : repli local, jamais d'ouverture totale
            log.warning("Redis indisponible pour la limitation (%s) : repli en mémoire", type(e).__name__)
    return _mem_count(key, now)


async def check(key: str, limit: int | None = None) -> None:
    if await count(key) >= (limit or get_settings().login_max_attempts):
        raise _too_many()


async def fail(key: str) -> None:
    now = time.time()
    r = _redis()
    if r is not None:
        try:
            async with r.pipeline(transaction=True) as p:
                p.zadd(_PREFIX + key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
                p.expire(_PREFIX + key, WINDOW)
                await p.execute()
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Redis indisponible pour la limitation (%s) : repli en mémoire", type(e).__name__)
    _hits[key].append(now)


async def reset(key: str) -> None:
    _hits.pop(key, None)
    r = _redis()
    if r is not None:
        try:
            await r.delete(_PREFIX + key)
        except Exception as e:  # noqa: BLE001
            log.warning("Redis indisponible pour la limitation (%s)", type(e).__name__)
