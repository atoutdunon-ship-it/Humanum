"""Google simulé (OAuth + Calendar v3) au niveau du transport httpx : aucun réseau, comportements contrôlables."""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import jwt


class FakeGoogle:
    def __init__(self, email: str = "coach.agenda@gmail.com"):
        self.email = email
        self.events: dict[str, dict] = {}
        self.busy: list[tuple[datetime, datetime]] = []
        self.revoked = False
        self.down: set[str] = set()   # "events" | "freebusy" | "token" → 503
        self.calls: list[tuple[str, str]] = []
        self.refreshes = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        url, m = str(req.url), req.method
        self.calls.append((m, url.split("?")[0]))
        if "oauth2.googleapis.com/token" in url:
            if "token" in self.down:
                return httpx.Response(503)
            f = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if f["grant_type"] == "authorization_code":
                if f["code"] != "code-ok":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                idt = jwt.encode({"email": self.email, "email_verified": True, "sub": "g-1"}, "cle-de-test-du-faux-google-32-octets!", algorithm="HS256")
                return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600, "refresh_token": "rt-1",
                                                 "scope": "openid email calendar", "id_token": idt})
            if self.revoked:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            self.refreshes += 1
            return httpx.Response(200, json={"access_token": f"at-r{self.refreshes}", "expires_in": 3600})
        if "oauth2.googleapis.com/revoke" in url:
            self.revoked = True
            return httpx.Response(200)
        if req.headers.get("authorization", "").split(" ")[-1].startswith("at-") is False:
            return httpx.Response(401, json={"error": {"message": "unauthenticated"}})
        if url.endswith("/freeBusy"):
            if "freebusy" in self.down:
                return httpx.Response(503)
            cal = json.loads(req.content)["items"][0]["id"]
            busy = [{"start": a.isoformat(), "end": b.isoformat()} for a, b in self.busy]
            return httpx.Response(200, json={"calendars": {cal: {"busy": busy}}})
        if "/events" in url:
            if "events" in self.down:
                return httpx.Response(503)
            path = req.url.path
            if m == "POST":
                ev = json.loads(req.content)
                if ev["id"] in self.events:
                    return httpx.Response(409, json={"error": {"message": "duplicate"}})
                ev["hangoutLink"] = f"https://meet.google.com/{ev['id'][2:5]}-{ev['id'][5:9]}-{ev['id'][9:12]}"
                ev["status"] = "confirmed"
                self.events[ev["id"]] = ev
                return httpx.Response(200, json=ev)
            eid = path.rsplit("/", 1)[-1]
            if eid not in self.events:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            if m == "PATCH":
                self.events[eid].update(json.loads(req.content))
                return httpx.Response(200, json=self.events[eid])
            if m == "DELETE":
                self.events[eid]["status"] = "cancelled"
                return httpx.Response(204)
        return httpx.Response(404)
