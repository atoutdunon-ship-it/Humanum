"""Fathom simulé (API externe v1) et Claude simulé : transports httpx, sans réseau."""
from __future__ import annotations

import base64
import json
import secrets

import httpx

KEY = "fk-cle-valide-du-coach"


def meeting(rid: int, start: str, invitee: str, coach: str = "coach@fathom.example.com") -> dict:
    return {"title": f"Séance {rid}", "meeting_title": f"Coaching — séance {rid}", "recording_id": rid,
            "url": f"https://fathom.video/calls/{rid}", "share_url": f"https://fathom.video/share/{rid}",
            "created_at": start, "scheduled_start_time": start, "scheduled_end_time": start, "recording_start_time": start,
            "recording_end_time": start, "transcript_language": "fr", "calendar_invitees_domains_type": "one_or_more_external",
            "shared_with": "no_teams", "recorded_by": {"name": "Coach", "email": coach, "email_domain": "fathom.example.com", "team": None},
            "calendar_invitees": [{"name": "Coach", "email": coach, "is_external": False},
                                  {"name": "Line MARTIN", "email": invitee, "is_external": True}],
            "default_summary": {"template_name": "Général", "markdown_formatted":
                                f"## Résumé\nLine MARTIN a présenté son projet de location (séance {rid}). Objectif : premier client en décembre."},
            "action_items": [{"description": "Line MARTIN finalise sa grille tarifaire", "completed": rid % 2 == 0,
                              "assignee": {"name": "Line MARTIN", "email": invitee}}],
            "transcript": [{"speaker": {"display_name": "Line MARTIN"}, "text": "Je veux lancer mon activité.", "timestamp": "00:00:05"}]}


class FakeFathom:
    def __init__(self, items: list[dict]):
        self.items = items
        self.hooks: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if req.headers.get("x-api-key") != KEY:
            return httpx.Response(401, json={"error": "unauthorized"})
        path = req.url.path.removeprefix("/external/v1")
        if path == "/meetings":
            if req.url.params.get("created_after", "").startswith("2100"):
                return httpx.Response(200, json={"items": [], "next_cursor": None, "limit": 10})
            page = int(req.url.params.get("cursor") or 0)
            chunk = self.items[page * 2:(page + 1) * 2]
            nxt = str(page + 1) if (page + 1) * 2 < len(self.items) else None
            return httpx.Response(200, json={"items": chunk, "next_cursor": nxt, "limit": 2})
        if path == "/webhooks" and req.method == "POST":
            body = json.loads(req.content)
            hid = f"wh_{secrets.token_hex(4)}"
            hook = {"id": hid, "url": body["destination_url"], "secret": "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode(),
                    "created_at": "2026-10-22T00:00:00Z", **{k: v for k, v in body.items() if k != "destination_url"}}
            self.hooks[hid] = hook
            return httpx.Response(201, json=hook)
        if path.startswith("/webhooks/") and req.method == "DELETE":
            self.hooks.pop(path.rsplit("/", 1)[-1], None)
            return httpx.Response(204)
        return httpx.Response(404)


class FakeClaude:
    def __init__(self):
        self.prompts: list[dict] = []
        self.fail_next = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.prompts.append(body)
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})
        assert req.headers["anthropic-version"] == "2023-06-01" and req.headers["x-api-key"]
        text = "## Situation de départ\n[COACHÉ 1] souhaite lancer son activité (séance du 05/10/2026).\n\n## Progrès constatés\nNon renseigné dans les séances."
        return httpx.Response(200, json={"id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
                                         "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 1234, "output_tokens": 321}})
