import json
import time

from main import C, S, V, T, ctx, tick, name, cat, msg


def _json_response(start_response, status_code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    reasons = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        409: "Conflict",
    }
    start_response(
        f"{status_code} {reasons.get(status_code, 'OK')}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        return json.loads(raw or b"{}")
    except Exception:
        return None


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"

    if method == "GET":
        if path == "/v1/healthz":
            return _json_response(
                start_response,
                200,
                {
                    "status": "ok",
                    "uptime_seconds": int(time.time() - T),
                    "contexts_loaded": {
                        s: sum(1 for x in C if x[0] == s)
                        for s in ("category", "merchant", "customer", "trigger")
                    },
                },
            )

        if path == "/v1/metadata":
            return _json_response(
                start_response,
                200,
                {
                    "team_name": "Vivek Kumar",
                    "team_members": ["Vivek Kumar"],
                    "model": "deterministic rule-based composer",
                    "version": "1.0.0",
                },
            )

        return _json_response(
            start_response,
            200,
            {
                "service": "vera-bot",
                "endpoints": [
                    "/v1/healthz",
                    "/v1/metadata",
                    "/v1/context",
                    "/v1/tick",
                    "/v1/reply",
                ],
            },
        )

    if method == "POST":
        data = _read_json(environ)
        if data is None:
            return _json_response(start_response, 400, {"error": "malformed_json"})

        if path == "/v1/context":
            status, payload = ctx(data)
            return _json_response(start_response, status, payload)

        if path == "/v1/tick":
            return _json_response(start_response, 200, tick(data))

        if path == "/v1/reply":
            conversation_id = data.get("conversation_id")
            v = V.get(conversation_id)
            if not v:
                return _json_response(
                    start_response,
                    404,
                    {"action": "wait", "rationale": "conversation not found"},
                )

            message = str(data.get("message", "")).lower().strip()

            if message in ("stop", "unsubscribe", "remove me"):
                return _json_response(
                    start_response,
                    200,
                    {"action": "end", "rationale": "opt-out honored"},
                )

            if message in ("yes", "y", "confirm", "1"):
                return _json_response(
                    start_response,
                    200,
                    {
                        "action": "send",
                        "message": "Done — I'll take care of that next.",
                        "rationale": "acceptance routed to the prepared action",
                    },
                )

            if message in ("no", "n", "later", "not now"):
                return _json_response(
                    start_response,
                    200,
                    {
                        "action": "wait",
                        "wait_seconds": 86400,
                        "rationale": "decline/later honored",
                    },
                )

            return _json_response(
                start_response,
                200,
                {
                    "action": "send",
                    "message": "Got it. Tell me what you'd like to change.",
                    "rationale": "clarification response",
                },
            )

        if path == "/v1/teardown":
            C.clear()
            S.clear()
            V.clear()
            return _json_response(start_response, 200, {"ok": True})

        return _json_response(start_response, 404, {"error": "not_found"})

    return _json_response(
        start_response,
        404,
        {"error": "not_found"},
    )
