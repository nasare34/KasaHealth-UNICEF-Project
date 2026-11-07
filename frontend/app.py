"""Flask frontend. It serves the web app and passes /api/* calls on to the backend."""
from __future__ import annotations

import logging
import os
import uuid

import requests
from flask import Flask, Response, jsonify, render_template, request
from requests.adapters import HTTPAdapter
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kasa.frontend")

# FASTAPI_URL if you set it, otherwise Render's private address (FASTAPI_HOSTPORT).
_hostport = os.getenv("FASTAPI_HOSTPORT", "")
FASTAPI_URL = (os.getenv("FASTAPI_URL") or (f"http://{_hostport}" if _hostport else "http://localhost:8000")).rstrip("/")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
ASSET_VERSION = os.getenv("ASSET_VERSION", "8.0.0")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
# How many proxies sit in front of Flask (1 on Render). I need it to find the real client IP.
PROXY_COUNT = int(os.getenv("PROXY_COUNT", "1"))

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
# Browsers re-check static files on each load (cheap 304s). The JS modules import
# each other without version tags, so long caching could mix old and new code.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
if PROXY_COUNT > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_COUNT, x_proto=PROXY_COUNT, x_host=PROXY_COUNT)

# one pooled session for all threads
backend = requests.Session()
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=int(os.getenv("BACKEND_POOL_SIZE", "64")), max_retries=0)
backend.mount("http://", _adapter)
backend.mount("https://", _adapter)

# route -> (backend path, allowed methods, timeout in seconds). Add new API routes here.
ROUTES = {
    "languages":         ("/languages", {"GET"}, 10),
    "asr":               ("/asr", {"POST"}, 90),
    "chat":              ("/chat", {"POST"}, 75),
    "tts":               ("/tts", {"POST"}, 75),
    "translate":         ("/translate", {"POST"}, 45),
    "full-pipeline":     ("/full-pipeline", {"POST"}, 120),
    "agent":             ("/agent", {"POST"}, 120),
    "survey":            ("/survey", {"POST"}, 20),
    "feedback/rating":   ("/feedback/rating", {"POST"}, 20),
    "feedback/thumbs":   ("/feedback/thumbs", {"POST"}, 30),
    "safety/contact":    ("/safety/contact", {"POST"}, 20),
    # Admin routes. The backend checks X-Admin-Token; the admin page sends it.
    "admin/llm":         ("/admin/llm", {"GET", "POST"}, 20),
    "admin/llm/reset":   ("/admin/llm/reset", {"POST"}, 20),
    "admin/stats":       ("/admin/stats", {"GET"}, 20),
    "admin/diagnostics": ("/admin/diagnostics", {"GET"}, 90),
    "admin/alerts/test": ("/admin/alerts/test", {"POST"}, 40),
    "admin/retry-sync":  ("/admin/retry-sync", {"POST"}, 30),
    "admin/health":      ("/admin/health", {"GET"}, 90),
    "admin/health/run":  ("/admin/health/run", {"POST"}, 120),
    "admin/overview":    ("/admin/overview", {"GET"}, 20),
    "admin/export/rating":   ("/admin/export/rating", {"GET"}, 60),
    "admin/export/feedback": ("/admin/export/feedback", {"GET"}, 60),
    "admin/export/survey":   ("/admin/export/survey", {"GET"}, 60),
    "admin/export/incident": ("/admin/export/incident", {"GET"}, 30),
    "admin/export/safety":   ("/admin/export/safety", {"GET"}, 30),
}

FRIENDLY = {
    "backend_unreachable": "Kasa is waking up or briefly unavailable. Please try again in a few seconds.",
    "timeout": "That took longer than expected. Please try again — it usually works on the next go.",
    "too_large": "That recording is a bit too long for me. Please try a shorter question.",
    "not_found": "We couldn't find what you were looking for.",
    "internal": "Something unexpected happened. Please try again — we're sorry about that.",
}


def friendly(code: str, status: int, request_id: str = ""):
    message = FRIENDLY.get(code, FRIENDLY["internal"])
    return jsonify({"ok": False, "error": {"code": code, "message": message, "retryable": status >= 500,
                                           "request_id": request_id}, "detail": message}), status


@app.after_request
def security_headers(resp: Response):
    resp.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self'; "
        "worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"))
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/api/") or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.route("/admin")
def admin_page():
    """The admin dashboard. Every action on it needs the ADMIN_TOKEN."""
    resp = Response(render_template("admin.html", asset_version=ASSET_VERSION))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/<path:route>", methods=["GET", "POST"])
def api_proxy(route: str):
    request_id = uuid.uuid4().hex[:16]
    target = ROUTES.get(route)
    if not target or request.method not in target[1]:
        return friendly("not_found", 404, request_id)
    path, _, timeout = target
    method = request.method

    headers = {"X-Request-ID": request_id, "X-Kasa-Client-IP": request.remote_addr or "unknown"}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN
    if request.content_type:
        headers["Content-Type"] = request.content_type  # keeps the multipart boundary intact
    if route.startswith("admin/") and request.headers.get("X-Admin-Token"):
        headers["X-Admin-Token"] = request.headers["X-Admin-Token"]

    try:
        upstream = backend.request(
            method, f"{FASTAPI_URL}{path}", headers=headers, params=request.args,
            data=request.get_data() if method == "POST" else None,
            timeout=(5, timeout))
    except requests.Timeout:
        log.warning("Backend timeout route=%s rid=%s", route, request_id)
        return friendly("timeout", 504, request_id)
    except requests.ConnectionError:
        log.warning("Backend unreachable route=%s rid=%s", route, request_id)
        return friendly("backend_unreachable", 503, request_id)
    except requests.RequestException:
        log.exception("Proxy failure route=%s rid=%s", route, request_id)
        return friendly("internal", 502, request_id)

    if "application/json" not in upstream.headers.get("content-type", ""):
        # never show a raw HTML/text error page to users
        log.warning("Non-JSON backend response route=%s status=%s rid=%s", route, upstream.status_code, request_id)
        return friendly("internal", 502 if upstream.status_code < 500 else upstream.status_code, request_id)

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in {"retry-after", "x-request-id"}}
    return Response(upstream.content, status=upstream.status_code,
                    content_type="application/json", headers=passthrough)


@app.errorhandler(413)
def too_large(_err):
    return friendly("too_large", 413)


@app.errorhandler(404)
def not_found(_err):
    if request.path.startswith("/api/"):
        return friendly("not_found", 404)
    return render_template("index.html", asset_version=ASSET_VERSION), 200


@app.errorhandler(Exception)
def unhandled(err):
    # Flask's own errors (405 etc.) keep their status codes.
    from werkzeug.exceptions import HTTPException
    if isinstance(err, HTTPException) and err.code and err.code < 500:
        if request.path.startswith("/api/"):
            return friendly("not_found" if err.code in (404, 405) else "internal", err.code)
        return err
    log.exception("Unhandled frontend error: %s", err)
    if request.path.startswith("/api/"):
        return friendly("internal", 500)
    return Response("<h2>Kasa will be right back.</h2><p>Please refresh the page in a moment.</p>",
                    status=500, content_type="text/html")


if __name__ == "__main__":
    if not INTERNAL_API_TOKEN:
        log.info("INTERNAL_API_TOKEN not set — fine for local development.")
    app.run(host="0.0.0.0", port=int(os.getenv("FLASK_PORT", "5000")), debug=False, threaded=True)
