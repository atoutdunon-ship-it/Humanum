"""Client Google minimal et explicite : OAuth 2.0 (hors ligne), Calendar v3 (freeBusy, événements avec Google Meet).

Aucune bibliothèque Google : httpx asynchrone, délais bornés, erreurs typées.
- `GoogleAuthError` : autorisation retirée ou invalide (invalid_grant) → le compte passe « revoked ».
- `GoogleTransientError` : 429 / 5xx / réseau → la tâche Arq est rejouée.
- `GoogleApiError` : refus définitif (4xx) → consigné sur le rendez-vous.
Le transport est injectable (`set_transport`) : les tests simulent Google sans réseau.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlencode

import httpx
import jwt

from app.core.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL publique)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = ("openid", "email",
          "https://www.googleapis.com/auth/calendar.events",     # créer / modifier / annuler les rendez-vous
          "https://www.googleapis.com/auth/calendar.freebusy")   # occupation seule, jamais le contenu des événements

_transport: httpx.AsyncBaseTransport | None = None


class GoogleError(Exception):
    pass


class GoogleAuthError(GoogleError):
    pass


class GoogleTransientError(GoogleError):
    pass


class GoogleApiError(GoogleError):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {message}")
        self.status = status


def set_transport(t: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = t


def configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret.get_secret_value())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=get_settings().google_timeout_seconds, transport=_transport)


async def _call(method: str, url: str, *, token: str | None = None, **kw) -> dict:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with _client() as c:
            r = await c.request(method, url, headers=headers, **kw)
    except httpx.HTTPError as e:
        raise GoogleTransientError(type(e).__name__) from e
    if r.status_code in (429,) or r.status_code >= 500:
        raise GoogleTransientError(f"{r.status_code}")
    body = r.json() if r.content and "json" in r.headers.get("content-type", "") else {}
    if r.status_code == 401 or (r.status_code == 400 and body.get("error") in ("invalid_grant", "unauthorized_client")):
        raise GoogleAuthError(body.get("error_description") or body.get("error") or "autorisation invalide")
    if r.status_code >= 400:
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or r.text[:200])
        raise GoogleApiError(r.status_code, str(msg))
    return body


# ---------------------------------------------------------------- OAuth
def authorization_url(state: str, login_hint: str | None = None) -> str:
    s = get_settings()
    q = {"client_id": s.google_client_id, "redirect_uri": s.google_redirect_uri, "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",  # garantit le refresh_token
         "include_granted_scopes": "true", "state": state}
    if login_hint:
        q["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(q)}"


async def exchange_code(code: str) -> dict:
    """→ {access_token, expires_in, refresh_token, scope, email}."""
    s = get_settings()
    body = await _call("POST", TOKEN_URL, data={
        "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret.get_secret_value(),
        "redirect_uri": s.google_redirect_uri, "grant_type": "authorization_code"})
    if not body.get("refresh_token"):
        raise GoogleApiError(400, "Google n'a pas fourni d'accès hors ligne : réessayez la connexion")
    # id_token reçu directement du point de jeton de Google en TLS : signature non revérifiée (OIDC Core §3.1.3.7).
    claims = jwt.decode(body.get("id_token", ""), options={"verify_signature": False}) if body.get("id_token") else {}
    if not claims.get("email") or not claims.get("email_verified", False):
        raise GoogleApiError(400, "Adresse Google non vérifiée")
    body["email"] = claims["email"]
    return body


async def refresh(refresh_token: str) -> dict:
    s = get_settings()
    return await _call("POST", TOKEN_URL, data={
        "refresh_token": refresh_token, "client_id": s.google_client_id,
        "client_secret": s.google_client_secret.get_secret_value(), "grant_type": "refresh_token"})


async def revoke(token: str) -> None:
    try:
        await _call("POST", REVOKE_URL, data={"token": token})
    except GoogleError:
        pass  # déjà révoqué ou expiré : la déconnexion locale suffit


# ---------------------------------------------------------------- Calendar
def _iso(d: datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


async def freebusy(token: str, calendar_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    body = await _call("POST", f"{API}/freeBusy", token=token,
                       json={"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": calendar_id}]})
    cal = body.get("calendars", {}).get(calendar_id, {})
    if cal.get("errors"):
        raise GoogleApiError(400, f"agenda inaccessible : {cal['errors'][0].get('reason')}")
    return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")), datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            for b in cal.get("busy", [])]


def _events(calendar_id: str) -> str:
    return f"{API}/calendars/{quote(calendar_id, safe='')}/events"


async def insert_event(token: str, calendar_id: str, event: dict) -> dict:
    """Identifiant d'événement fixé par nous : un double envoi renvoie 409 au lieu de créer un doublon."""
    try:
        return await _call("POST", _events(calendar_id), token=token, json=event,
                           params={"conferenceDataVersion": 1, "sendUpdates": "all"})
    except GoogleApiError as e:
        if e.status != 409:
            raise
        return await patch_event(token, calendar_id, event["id"], event)


async def patch_event(token: str, calendar_id: str, event_id: str, event: dict) -> dict:
    return await _call("PATCH", f"{_events(calendar_id)}/{quote(event_id, safe='')}", token=token, json=event,
                       params={"conferenceDataVersion": 1, "sendUpdates": "all"})


async def cancel_event(token: str, calendar_id: str, event_id: str) -> None:
    try:
        await _call("DELETE", f"{_events(calendar_id)}/{quote(event_id, safe='')}", token=token,
                    params={"sendUpdates": "all"})
    except GoogleApiError as e:
        if e.status not in (404, 410):  # déjà supprimé côté Google
            raise


def meet_link(event: dict) -> str | None:
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for ep in event.get("conferenceData", {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")
    return None
