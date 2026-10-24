"""Stripe simulé (API REST v1) au niveau du transport httpx, et fabrique d'événements signés."""
from __future__ import annotations

import json
import secrets
import time
from urllib.parse import parse_qsl

import httpx

from app.integrations import stripe_api

WEBHOOK_SECRET = "whsec_test_" + "x" * 24


def _form(req: httpx.Request) -> dict:
    return dict(parse_qsl(req.content.decode(), keep_blank_values=True)) if req.content else {}


class FakeStripe:
    def __init__(self):
        self.products: dict[str, dict] = {}
        self.prices: dict[str, dict] = {}
        self.customers: dict[str, dict] = {}
        self.sessions: list[dict] = []
        self.subs: dict[str, dict] = {}
        self.idem: dict[str, dict] = {}
        self.tax_rates: dict[str, dict] = {}
        self.coupons: dict[str, dict] = {}
        self.promo_codes: dict[str, dict] = {}
        self.down = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _id(self, p: str) -> str:
        return f"{p}_{secrets.token_hex(6)}"

    def handle(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, json={"error": {"message": "indisponible"}})
        assert req.headers["authorization"].startswith("Bearer sk_test_")
        key = req.headers.get("idempotency-key")
        if key and key in self.idem:
            return httpx.Response(200, json=self.idem[key])
        path, f = req.url.path.removeprefix("/v1"), _form(req)
        out = self._route(req.method, path, f)
        if out is None:
            return httpx.Response(404, json={"error": {"message": f"inconnu {path}"}})
        if "__error__" in out:
            return httpx.Response(400, json={"error": {"message": out["__error__"]}})
        if key:
            self.idem[key] = out
        return httpx.Response(200, json=out)

    def _route(self, m: str, path: str, f: dict) -> dict | None:
        if path == "/products" and m == "POST":
            o = {"id": self._id("prod"), "name": f["name"], "metadata": {"plan_code": f.get("metadata[plan_code]")}}
            self.products[o["id"]] = o
            return o
        if path == "/prices" and m == "POST":
            o = {"id": self._id("price"), "product": f["product"], "unit_amount": int(f["unit_amount"]), "currency": f["currency"],
                 "active": True, "lookup_key": f.get("lookup_key"), "tax_behavior": f.get("tax_behavior"),
                 "metadata": {"plan_code": f.get("metadata[plan_code]")}}
            self.prices[o["id"]] = o
            return o
        if path.startswith("/prices/") and m == "POST":
            p = self.prices[path.rsplit("/", 1)[-1]]
            p["active"] = f.get("active") != "false"
            return p
        if path == "/customers" and m == "POST":
            o = {"id": self._id("cus"), "email": f.get("email"), "name": f.get("name"), "metadata": {"org_id": f.get("metadata[org_id]")}}
            self.customers[o["id"]] = o
            return o
        if path == "/checkout/sessions" and m == "POST":
            o = {"id": self._id("cs_test"), "url": "https://checkout.stripe.com/c/pay/" + secrets.token_hex(8), "params": f}
            self.sessions.append(o)
            return o
        if path == "/billing_portal/sessions" and m == "POST":
            return {"id": self._id("bps"), "url": "https://billing.stripe.com/p/session/" + secrets.token_hex(6)}
        if path == "/tax_rates" and m == "POST":
            o = {"id": self._id("txr"), "percentage": float(f["percentage"]), "inclusive": f["inclusive"] == "true",
                 "jurisdiction": f.get("jurisdiction"), "display_name": f["display_name"]}
            self.tax_rates[o["id"]] = o
            return o
        if path == "/coupons" and m == "POST":
            o = {"id": self._id("co"), "name": f.get("name"), "duration": f["duration"], "percent_off": float(f["percent_off"]) if "percent_off" in f else None,
                 "amount_off": int(f["amount_off"]) if "amount_off" in f else None,
                 "applies_to": [v for k, v in f.items() if k.startswith("applies_to[products]")]}
            self.coupons[o["id"]] = o
            return o
        if path.startswith("/coupons/") and m == "POST":
            c = self.coupons[path.rsplit("/", 1)[-1]]
            c["name"] = f.get("name", c["name"])
            return c
        if path == "/promotion_codes" and m == "POST":
            if any(x["code"] == f["code"] and x["active"] for x in self.promo_codes.values()):
                return {"__error__": "An active promotion code with this code already exists."}  # règle Stripe
            o = {"id": self._id("promo"), "code": f["code"], "coupon": self.coupons[f["coupon"]], "active": f.get("active") != "false",
                 "max_redemptions": int(f["max_redemptions"]) if "max_redemptions" in f else None, "times_redeemed": 0,
                 "first_time": f.get("restrictions[first_time_transaction]") == "true"}
            self.promo_codes[o["id"]] = o
            return o
        if path.startswith("/promotion_codes/"):
            pc = self.promo_codes.get(path.rsplit("/", 1)[-1])
            if pc is not None and m == "POST":
                pc["active"] = f.get("active", "true") != "false"
            return pc
        if path.startswith("/subscriptions/"):
            sub = self.subs.get(path.rsplit("/", 1)[-1])
            if sub is None:
                return None
            if m == "POST":
                if "items[0][price]" in f:
                    sub["items"]["data"][0]["price"] = self.prices[f["items[0][price]"]]
                if "default_tax_rates" in f or "default_tax_rates[0]" in f:
                    sub["default_tax_rates"] = [self.tax_rates[f["default_tax_rates[0]"]]] if f.get("default_tax_rates[0]") else []
                if "discounts[0][promotion_code]" in f:
                    pc = self.promo_codes[f["discounts[0][promotion_code]"]]
                    pc["times_redeemed"] += 1
                    sub["discount"] = {"coupon": pc["coupon"], "promotion_code": pc["id"]}
                sub["metadata"].update({k[9:-1]: v for k, v in f.items() if k.startswith("metadata[")})
            return sub
        return None

    # ---- outils de scénario
    def subscription(self, org_id: str, customer: str, price_id: str, status: str = "active", trial_days: int = 0) -> dict:
        now = int(time.time())
        sid = self._id("sub")
        sub = {"id": sid, "object": "subscription", "customer": customer, "status": status, "cancel_at_period_end": False,
               "current_period_end": now + 30 * 86400, "trial_end": now + trial_days * 86400 if trial_days else None,
               "metadata": {"org_id": org_id, "plan_code": self.prices[price_id]["metadata"]["plan_code"]},
               "items": {"data": [{"id": self._id("si"), "price": self.prices[price_id]}]}}
        self.subs[sid] = sub
        return sub


def event(kind: str, obj: dict, eid: str | None = None) -> tuple[bytes, dict]:
    body = json.dumps({"id": eid or f"evt_{secrets.token_hex(8)}", "object": "event", "type": kind, "data": {"object": obj}}).encode()
    return body, {"stripe-signature": stripe_api.sign(body, WEBHOOK_SECRET), "content-type": "application/json"}
