"""Limitation partagée dans Redis (et repli), file de tâches Arq."""
import os

import pytest
from arq.worker import Worker
from redis.asyncio import Redis

from app.core import queue, ratelimit
from app.worker import WorkerSettings


async def test_rate_limit_is_shared_in_redis(client):
    r = Redis.from_url(os.environ["HC_REDIS_URL"])
    for _ in range(2):
        await client.post("/api/v1/auth/login", json={"email": "partage@infra.example.com", "password": "faux"})
    keys = [k.decode() async for k in r.scan_iter("hc:rl:user:partage@infra.example.com:*")]
    assert len(keys) == 1 and await r.zcard(keys[0]) == 2 and 0 < await r.ttl(keys[0]) <= ratelimit.WINDOW
    await r.aclose()


async def test_rate_limit_falls_back_when_redis_is_down(monkeypatch):
    from app.core.config import get_settings
    await ratelimit.close()
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")  # port fermé
    try:
        for _ in range(5):
            await ratelimit.fail("panne:redis")
        with pytest.raises(Exception) as e:
            await ratelimit.check("panne:redis")
        assert e.value.status_code == 429  # la protection reste active
    finally:
        await ratelimit.close()


async def test_arq_round_trip():
    job = await queue.enqueue("heartbeat", job_id="controle-unique")
    assert await queue.enqueue("heartbeat", job_id="controle-unique") is None  # idempotence
    w = Worker(functions=WorkerSettings.functions, redis_pool=await queue.pool(), queue_name=queue.QUEUE,
               burst=True, poll_delay=0.05, handle_signals=False)
    await w.main()
    assert (await job.result(timeout=5))["status"] == "ok"
    await w.close()
    await queue.close()
