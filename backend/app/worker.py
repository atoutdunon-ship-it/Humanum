"""Processus de tâches : `arq app.worker.WorkerSettings`.

Tâches du lot 0 :
- `heartbeat` : sonde de bout en bout (API → Redis → worker), utilisée par la supervision ;
- `purge_stale_mfa_enrollments` (toutes les heures) : efface les secrets TOTP d'enrôlements jamais finalisés.

Tâches du lot 2 :
- `sync_appointment` : création / report / annulation de l'événement Google Agenda (avec Google Meet) ;
- `resync_pending` (toutes les 10 minutes) : rattrapage des synchronisations restées en attente.

Fathom et synthèses :
- `import_fathom` : historique des réunions d'un coach ;
- `generate_synthesis` : synthèse de parcours, de groupe ou rapport financeur (Claude).
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import text, update

from app.core.config import get_settings
from app.core.queue import QUEUE
from app.db.session import dispose, platform_session, tenant_session
from app.integrations import claude
from app.integrations.fathom import FathomError, FathomTransientError
from app.integrations.google import GoogleTransientError
from app.models import Appointment, FathomAccount, PlatformAdmin, Synthesis, User
from app.services import agenda, synthesis
from app.services.fathom_sync import import_history
from app.services.mfa import PENDING_TTL

log = logging.getLogger("humanum.worker")


async def heartbeat(ctx: dict) -> dict:
    return {"status": "ok", "at": datetime.now(UTC).isoformat(), "job_try": ctx.get("job_try", 1)}


async def purge_stale_mfa_enrollments(ctx: dict) -> int:
    """Rôle plateforme : droits sur users et platform_admins, aucun sur les données de coaching."""
    limit = datetime.now(UTC) - PENDING_TTL
    purged = 0
    async with platform_session() as s:
        for model in (User, PlatformAdmin):
            r = await s.execute(update(model).where(model.mfa_pending_at < limit)
                                .values(mfa_pending_enc=None, mfa_pending_at=None))
            purged += r.rowcount or 0
    if purged:
        log.info("Enrôlements TOTP expirés purgés : %s", purged)
    return purged


async def sync_appointment(ctx: dict, org_id: str, appointment_id: str) -> str:
    """Pousse un rendez-vous vers Google Agenda ; erreur transitoire → nouvel essai avec attente croissante."""
    try:
        async with tenant_session(org_id=uuid.UUID(org_id), user_id=None) as s:
            appt = await s.get(Appointment, uuid.UUID(appointment_id), with_for_update=True)
            if appt is None:
                return "absent"
            await agenda.sync(s, appt)
            return appt.sync_status
    except GoogleTransientError as e:
        raise Retry(defer=min(30 * ctx.get("job_try", 1) ** 2, 900)) from e


async def resync_pending(ctx: dict) -> int:
    """Rattrapage : rendez-vous restés « en attente » ou « en erreur » (Google ou Redis indisponibles au moment voulu)."""
    async with tenant_session(org_id=None, user_id=None) as s:
        rows = (await s.execute(text("select organization_id, appointment_id from hc_pending_syncs(100)"))).all()
    done = 0
    for org, appt_id in rows:
        try:
            await sync_appointment(ctx, str(org), str(appt_id))
            done += 1
        except Retry:
            continue
    return done


async def import_fathom(ctx: dict, org_id: str, account_id: str) -> int:
    """Historique Fathom d'un coach (90 jours, puis depuis la dernière synchronisation)."""
    async with tenant_session(org_id=uuid.UUID(org_id), user_id=None) as s:
        acc = await s.get(FathomAccount, uuid.UUID(account_id))
        if acc is None:
            return 0
        try:
            return await import_history(s, acc)
        except FathomTransientError as e:
            raise Retry(defer=min(60 * ctx.get("job_try", 1), 900)) from e
        except FathomError as e:
            acc.status, acc.last_error = "error", str(e)[:500]
            return 0


async def generate_synthesis(ctx: dict, org_id: str, synthesis_id: str) -> str:
    org = uuid.UUID(org_id)
    async with tenant_session(org_id=org, user_id=None) as s:
        syn = await s.get(Synthesis, uuid.UUID(synthesis_id))
        if syn is None or syn.status == "done":
            return "absent"
        syn.status = "running"
        try:
            prompt, pseudo, sources, appendix = await synthesis.build(s, syn)
        except Exception as e:  # noqa: BLE001 — sujet supprimé entre-temps, données incohérentes
            syn.status, syn.error = "error", f"préparation impossible : {type(e).__name__}"
            return "error"
    try:
        r = await claude.complete(synthesis.SYSTEM, prompt)
    except claude.ClaudeTransientError as e:
        if ctx.get("job_try", 1) < 5:
            raise Retry(defer=30 * ctx.get("job_try", 1)) from e
        r = None
        err = f"service momentanément indisponible ({e})"
    except claude.ClaudeError as e:
        r, err = None, str(e)[:500]
    async with tenant_session(org_id=org, user_id=None) as s:
        syn = await s.get(Synthesis, uuid.UUID(synthesis_id))
        syn.sources, syn.model = sources, (r or {}).get("model")
        if r is None:
            syn.status, syn.error = "error", err
        else:
            syn.content_md = pseudo.show(r["text"]) + appendix
            syn.status, syn.error = "done", None
            syn.input_tokens, syn.output_tokens = r["input_tokens"], r["output_tokens"]
        return syn.status


async def shutdown(ctx: dict) -> None:
    await dispose()


class WorkerSettings:
    functions = [heartbeat, purge_stale_mfa_enrollments, sync_appointment, resync_pending, import_fathom, generate_synthesis]
    cron_jobs = [cron(purge_stale_mfa_enrollments, minute=7, run_at_startup=True),
                 cron(resync_pending, minute=set(range(0, 60, 10)))]
    queue_name = QUEUE
    on_shutdown = shutdown
    max_jobs = 20
    job_timeout = 300          # une synthèse longue ne bloque pas le worker indéfiniment
    max_tries = 5              # nouvelles tentatives sur erreur transitoire (API tierce, réseau)
    keep_result = 3600
    health_check_interval = 30
    # Lue par la commande `arq` au démarrage : HC_REDIS_URL doit être renseignée pour le worker.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://127.0.0.1:6379")
