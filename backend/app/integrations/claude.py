"""Appel à l'API Messages d'Anthropic (Claude) — httpx, délais bornés, transport injectable pour les tests."""
from __future__ import annotations

import httpx

from app.core.config import get_settings

URL = "https://api.anthropic.com/v1/messages"
_transport: httpx.AsyncBaseTransport | None = None


class ClaudeError(Exception):
    pass


class ClaudeTransientError(ClaudeError):
    pass


def set_transport(t: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = t


def configured() -> bool:
    k = get_settings().anthropic_api_key
    return bool(k and k.get_secret_value())


async def complete(system: str, prompt: str, *, max_tokens: int = 6000) -> dict:
    """→ {text, model, input_tokens, output_tokens}."""
    s = get_settings()
    if not configured():
        raise ClaudeError("clé d'API Anthropic non configurée")
    try:
        async with httpx.AsyncClient(timeout=180.0, transport=_transport) as c:
            r = await c.post(URL, headers={"x-api-key": s.anthropic_api_key.get_secret_value(), "anthropic-version": "2023-06-01",
                                           "content-type": "application/json"},
                             json={"model": s.anthropic_model, "max_tokens": max_tokens, "temperature": 0.2, "system": system,
                                   "messages": [{"role": "user", "content": prompt}]})
    except httpx.HTTPError as e:
        raise ClaudeTransientError(type(e).__name__) from e
    if r.status_code in (429, 529) or r.status_code >= 500:
        raise ClaudeTransientError(str(r.status_code))
    if r.status_code >= 400:
        raise ClaudeError(f"{r.status_code} {r.text[:300]}")
    b = r.json()
    text = "".join(part.get("text", "") for part in b.get("content", []) if part.get("type") == "text")
    if b.get("stop_reason") == "max_tokens":
        text += "\n\n> Synthèse tronquée : longueur maximale atteinte."
    u = b.get("usage", {})
    return {"text": text.strip(), "model": b.get("model", s.anthropic_model),
            "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
