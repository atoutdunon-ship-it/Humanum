from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api.routes import (
    admin,
    admin_billing,
    auth,
    billing,
    booking,
    booking_public,
    coachee,
    cohorts,
    contacts,
    fathom,
    members,
    pilotage,
    public,
    syntheses,
)
from app.core import queue, ratelimit
from app.db.session import dispose, tenant_session


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await ratelimit.close()
    await queue.close()
    await dispose()


app = FastAPI(title="HUMANUM CONSULTING API", version="0.1.0", lifespan=lifespan)
for r in (admin.router, public.router, auth.router, members.router, contacts.router, booking.router, booking.oauth,
          booking_public.router, fathom.router, fathom.hooks, cohorts.router, syntheses.router, coachee.staff, coachee.me,
          coachee.public, billing.router, billing.hooks, admin_billing.router,
          pilotage.router, pilotage.admin):
    app.include_router(r, prefix="/api/v1")


@app.get("/health", tags=["supervision"])
async def health() -> dict:
    async with tenant_session(org_id=None, user_id=None) as s:
        await s.execute(text("select 1"))
    return {"status": "ok"}


@app.get("/health/worker", tags=["supervision"])
async def health_worker() -> dict:
    """Sonde de bout en bout : met une tâche en file et attend la réponse du worker (5 s)."""
    job = await queue.enqueue("heartbeat")
    try:
        return {"queue": "ok", "worker": await job.result(timeout=5)}
    except TimeoutError:
        raise HTTPException(503, "Aucun worker n'a traité la tâche de contrôle") from None
