"""HTTP API for the magicpin judge harness. Python standard library only, so it
runs unchanged on any host (Render, Railway, HF Spaces, a VM, ngrok, Docker).

Endpoints: GET /v1/healthz, GET /v1/metadata, POST /v1/context,
           POST /v1/tick, POST /v1/reply, POST /v1/teardown (optional)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import llm
from .composer import compose
from .conversation import respond
from .store import STORE, SCOPES, Conversation
from .text import parse_dt

MAX_BODY = 600 * 1024
MAX_ACTIONS = 20

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _aware(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def metadata() -> dict:
    return {
        "team_name": os.getenv("TEAM_NAME", "Vivek Kumar"),
        "team_members": [m.strip() for m in os.getenv("TEAM_MEMBERS", "Vivek Kumar").split(",") if m.strip()],
        "model": llm.model_label(),
        "approach": ("Grounded composer: fact-extraction layer over the 4 contexts, per-trigger-kind handlers, "
                     "consent + suppression guardrails, rule-based multi-turn state machine "
                     "(auto-reply / intent / hostile / off-topic)"),
        "contact_email": os.getenv("CONTACT_EMAIL", "vivekkumar_23cs466@dtu.ac.in"),
        "version": "1.0.0",
        "submitted_at": os.getenv("SUBMITTED_AT", "2026-09-27T00:00:00Z"),
    }

def healthz() -> dict:
    return {"status": "ok", "uptime_seconds": int(time.time() - STORE.started), "contexts_loaded": STORE.counts()}

def push_context(body: dict) -> tuple[int, dict]:
    scope, cid, version, payload = body.get("scope"), body.get("context_id"), body.get("version"), body.get("payload")
    if scope not in SCOPES:
        return 400, {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {list(SCOPES)}"}
    if not cid or not isinstance(cid, str):
        return 400, {"accepted": False, "reason": "invalid_context_id", "details": "context_id is required"}
    if not isinstance(version, int) or isinstance(version, bool):
        return 400, {"accepted": False, "reason": "invalid_version", "details": "version must be an integer"}
    if not isinstance(payload, dict):
        return 400, {"accepted": False, "reason": "invalid_payload", "details": "payload must be an object"}
    ok, cur = STORE.put_context(scope, cid, version, payload)
    if not ok:
        return 409, {"accepted": False, "reason": "stale_version", "current_version": cur}
    return 200, {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": _now_iso()}

def _category(merchant: dict) -> dict | None:
    cat = STORE.category_for(merchant)
    if cat:
        return cat
    slug = merchant.get("category_slug")
    with STORE.lock:
        for (scope, _cid), item in STORE.contexts.items():
            if scope == "category" and item["payload"].get("slug") == slug:
                return item["payload"]
    return None

def _conv_id(merchant_id: str, trigger: dict) -> str:
    short = "_".join(merchant_id.split("_")[:2]) if merchant_id else "m"
    h = hashlib.sha1(str(trigger.get("suppression_key") or trigger.get("id")).encode()).hexdigest()[:6]
    return f"conv_{short}_{trigger.get('kind', 'msg')}_{h}"

COOLDOWN_MIN = int(os.getenv("MERCHANT_COOLDOWN_MIN", "15"))

def _cooldown_ok(mid: str, trg: dict, now: datetime) -> bool:
    if int(trg.get("urgency", 1) or 1) >= 4:
        return True
    last = _aware(parse_dt(STORE.merchant_last_send.get(mid)))
    return not last or (now - last).total_seconds() >= COOLDOWN_MIN * 60

def tick(body: dict) -> dict:
    now = _aware(parse_dt(body.get("now"))) or datetime.now(timezone.utc)
    ids = []
    for t in body.get("available_triggers") or []:
        if isinstance(t, str) and t not in ids:
            ids.append(t)
    candidates = []
    for tid in ids:
        trg = STORE.get("trigger", tid)
        if not trg:
            continue
        exp = _aware(parse_dt(trg.get("expires_at")))
        if exp and exp < now:
            continue
        skey = trg.get("suppression_key") or tid
        if skey in STORE.sent_suppression:
            continue
        mid = trg.get("merchant_id") or (trg.get("payload") or {}).get("merchant_id")
        merchant = STORE.get("merchant", mid)
        if not merchant or mid in STORE.opted_out_merchants:
            continue
        category = _category(merchant)
        if not category:
            continue
        cid = trg.get("customer_id")
        customer = STORE.get("customer", cid) if cid else None
        if trg.get("scope") == "customer" and not customer:
            continue
        candidates.append((-(int(trg.get("urgency", 1) or 1)), tid, trg, merchant, category, customer))
    candidates.sort(key=lambda c: (c[0], c[1]))
    actions, used_recipients = [], set()
    for _u, tid, trg, merchant, category, customer in candidates:
        if len(actions) >= MAX_ACTIONS:
            break
        mid = merchant.get("merchant_id") or trg.get("merchant_id")
        recipient = ("customer", customer.get("customer_id")) if customer else ("merchant", mid)
        if recipient in used_recipients:
            continue
        if not customer and not _cooldown_ok(mid, trg, now):
            continue
        msg = compose(category, merchant, trg, customer)
        if not msg.get("consent_ok", True) or not msg.get("body"):
            continue
        msg = llm.maybe_polish(msg, category, merchant, trg, customer)
        conv_id = _conv_id(mid, trg)
        if STORE.conv(conv_id):
            continue
        STORE.new_conv(Conversation(conversation_id=conv_id, merchant_id=mid,
                                    customer_id=customer.get("customer_id") if customer else None,
                                    trigger_id=tid, kind=trg.get("kind", ""), send_as=msg["send_as"],
                                    suppression_key=msg["suppression_key"], on_yes=msg.get("on_yes", ""),
                                    bot_bodies=[msg["body"]], turns=[("bot", msg["body"])]))
        STORE.sent_suppression.add(msg["suppression_key"])
        if not customer:
            STORE.merchant_last_send[mid] = now.isoformat()
        used_recipients.add(recipient)
        actions.append({
            "conversation_id": conv_id,
            "merchant_id": mid,
            "customer_id": customer.get("customer_id") if customer else None,
            "send_as": msg["send_as"],
            "trigger_id": tid,
            "template_name": msg["template_name"],
            "template_params": msg["template_params"],
            "body": msg["body"],
            "cta": msg["cta"],
            "suppression_key": msg["suppression_key"],
            "rationale": msg["rationale"],
        })
    return {"actions": actions}

def reply(body: dict) -> tuple[int, dict]:
    conv_id = body.get("conversation_id")
    if not conv_id:
        return 400, {"error": "conversation_id required"}
    out = respond(conv_id, body.get("merchant_id"), body.get("customer_id"),
                  body.get("from_role") or "merchant", str(body.get("message") or ""))
    return 200, out

class Handler(BaseHTTPRequestHandler):
    server_version = "VeraBot/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.getenv("LOG_REQUESTS"):
            super().log_message(fmt, *args)

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
            return obj if isinstance(obj, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/healthz":
            return self._json(200, healthz())
        if path == "/v1/metadata":
            return self._json(200, metadata())
        if path in ("", "/"):
            return self._json(200, {"service": "vera-bot", "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"]})
        return self._json(404, {"error": "not_found"})

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        body = self._body()
        if body is None:
            return self._json(400, {"accepted": False, "reason": "malformed_json", "details": "body must be a JSON object under 600KB"})
        try:
            if path == "/v1/context":
                code, out = push_context(body)
                return self._json(code, out)
            if path == "/v1/tick":
                return self._json(200, tick(body))
            if path == "/v1/reply":
                code, out = reply(body)
                return self._json(code, out)
            if path == "/v1/teardown":
                STORE.teardown()
                return self._json(200, {"ok": True})
        except Exception as exc:
            if path == "/v1/tick":
                return self._json(200, {"actions": [], "error": type(exc).__name__})
            if path == "/v1/reply":
                return self._json(200, {"action": "wait", "wait_seconds": 600,
                                        "rationale": f"internal error ({type(exc).__name__}); backing off"})
            return self._json(500, {"error": type(exc).__name__})
        return self._json(404, {"error": "not_found"})

def serve(host: str = "0.0.0.0", port: int | None = None):
    port = port or int(os.getenv("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"vera-bot listening on http://{host}:{port}  (model: {llm.model_label()})", flush=True)
    httpd.serve_forever()
