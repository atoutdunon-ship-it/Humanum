"""Client Stripe minimal (API REST, httpx) et vérification des webhooks (en-tête Stripe-Signature).

- Paramètres encodés au format Stripe (a[b]=…, a[0][c]=…), clé d'idempotence sur toute création.
- Erreurs : `StripeTransientError` (réseau, 429, 5xx) / `StripeError` (refus définitif, message Stripe conservé).
- Transport injectable : tests et démonstration sans réseau.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Mapping
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

API = "https://api.stripe.com/v1"
TOLERANCE = 300
_transport: httpx.AsyncBaseTransport | None = None


class StripeError(Exception):
    pass


class StripeTransientError(StripeError):
    pass


def set_transport(t: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = t


def configured() -> bool:
    k = get_settings().stripe_secret_key
    return bool(k and k.get_secret_value())


def encode(params: Mapping, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if v is None:
            continue
        if isinstance(v, Mapping):
            out += encode(v, key)
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                out += encode(item, f"{key}[{i}]") if isinstance(item, Mapping) else [(f"{key}[{i}]", _s(item))]
        else:
            out.append((key, _s(v)))
    return out


def _s(v: object) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


async def call(method: str, path: str, params: Mapping | None = None, *, idempotency_key: str | None = None) -> dict:
    s = get_settings()
    if not configured():
        raise StripeError("Stripe n'est pas configuré sur la plateforme")
    headers = {"Authorization": f"Bearer {s.stripe_secret_key.get_secret_value()}", "Stripe-Version": s.stripe_api_version}
    if method == "POST":
        headers["Idempotency-Key"] = idempotency_key or uuid.uuid4().hex
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=_transport) as c:
            if method == "GET":
                r = await c.get(f"{API}{path}", headers=headers, params=encode(params or {}))
            else:
                r = await c.request(method, f"{API}{path}", content=urlencode(encode(params or {})).encode(),
                                    headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError as e:
        raise StripeTransientError(type(e).__name__) from e
    if r.status_code == 429 or r.status_code >= 500:
        raise StripeTransientError(str(r.status_code))
    body = r.json()
    if r.status_code >= 400:
        raise StripeError(body.get("error", {}).get("message") or f"Erreur Stripe {r.status_code}")
    return body


def verify_webhook(payload: bytes, header: str | None, secret: str, now: float | None = None) -> bool:
    """Stripe-Signature : « t=…,v1=… » ; HMAC-SHA256(secret, f"{t}.{corps brut}") en hexadécimal ; 5 minutes de tolérance."""
    if not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts.get("t", ["0"])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + payload, hashlib.sha256).hexdigest()}"
