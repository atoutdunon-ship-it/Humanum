"""Client Fathom (API externe v1) et vérification des webhooks (norme « Standard Webhooks »).

Clé d'API propre à chaque coach (en-tête X-Api-Key). Webhook « nouvelle réunion prête » par coach, limité à ses
propres enregistrements (`my_recordings`), avec résumé, actions et transcription.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import AsyncIterator, Mapping
from datetime import datetime

import httpx

API = "https://api.fathom.ai/external/v1"
TOLERANCE = 5 * 60  # secondes : au-delà, le message est rejeté (anti-rejeu)
_transport: httpx.AsyncBaseTransport | None = None


class FathomError(Exception):
    pass


class FathomAuthError(FathomError):
    pass


class FathomTransientError(FathomError):
    pass


def set_transport(t: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = t


async def _call(method: str, path: str, api_key: str, **kw) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=_transport) as c:
            r = await c.request(method, f"{API}{path}", headers={"X-Api-Key": api_key}, **kw)
    except httpx.HTTPError as e:
        raise FathomTransientError(type(e).__name__) from e
    if r.status_code == 429 or r.status_code >= 500:
        raise FathomTransientError(str(r.status_code))
    if r.status_code in (401, 403):
        raise FathomAuthError("clé d'API Fathom refusée")
    if r.status_code >= 400:
        raise FathomError(f"{r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


async def check_key(api_key: str) -> None:
    await _call("GET", "/meetings", api_key, params={"created_after": "2100-01-01T00:00:00Z"})


async def create_webhook(api_key: str, destination_url: str) -> dict:
    """→ {id, secret, ...}. Transcription, résumé et actions inclus : matière première des synthèses."""
    return await _call("POST", "/webhooks", api_key, json={
        "destination_url": destination_url, "triggered_for": ["my_recordings"],
        "include_transcript": True, "include_summary": True, "include_action_items": True, "include_crm_matches": False})


async def delete_webhook(api_key: str, webhook_id: str) -> None:
    try:
        await _call("DELETE", f"/webhooks/{webhook_id}", api_key)
    except FathomError:
        pass  # déjà supprimé ou clé révoquée : la déconnexion locale suffit


async def meetings(api_key: str, recorded_by: str, created_after: datetime | None) -> AsyncIterator[dict]:
    """Historique paginé (curseur) des réunions enregistrées par le coach."""
    params: dict = {"recorded_by[]": recorded_by, "include_summary": "true", "include_action_items": "true",
                    "include_transcript": "true"}
    if created_after:
        params["created_after"] = created_after.isoformat().replace("+00:00", "Z")
    cursor = None
    for _ in range(200):  # garde-fou
        page = await _call("GET", "/meetings", api_key, params={**params, **({"cursor": cursor} if cursor else {})})
        for m in page.get("items", []):
            yield m
        cursor = page.get("next_cursor")
        if not cursor:
            return


def verify_webhook(secret: str, headers: Mapping[str, str], body: bytes, now: float | None = None) -> bool:
    """Standard Webhooks : HMAC-SHA256 de « id.horodatage.corps brut », clé = base64(secret sans « whsec_ »)."""
    msg_id, ts, sigs = headers.get("webhook-id"), headers.get("webhook-timestamp"), headers.get("webhook-signature")
    if not (msg_id and ts and sigs):
        return False
    try:
        if abs((now if now is not None else time.time()) - int(ts)) > TOLERANCE:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    ok = False
    for part in sigs.split(" "):
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            ok = True
    return ok


def sign(secret: str, msg_id: str, ts: int, body: bytes) -> str:
    """Signature au format Fathom (tests et outils de démonstration)."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
