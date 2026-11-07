"""Request IDs, the internal-token check, security headers and rate limiting."""
from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings
from .errors import AppError, error_payload

logger = logging.getLogger("kasa.http")

# Always reachable, so Render's health checks work.
PUBLIC_PATHS = {"/health", "/ready"}
# Admin paths use the admin token instead of the internal one, so you can call
# them directly with just X-Admin-Token (from curl, for example).
ADMIN_PATHS = ("/admin/", "/feedback/export", "/feedback/ratings/export", "/survey/export")


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-kasa-client-ip")
        if forwarded:
            return forwarded.strip()[:64]
    return request.client.host if request.client else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming[:64] if incoming.replace("-", "").isalnum() else uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()

        token = self.settings.internal_api_token
        path = request.url.path
        if token and path not in PUBLIC_PATHS and not path.startswith(ADMIN_PATHS):
            supplied = request.headers.get("x-internal-token", "")
            if not hmac.compare_digest(supplied, token):
                logger.warning("Rejected request without valid internal token path=%s", request.url.path)
                return JSONResponse(error_payload("forbidden", request_id), status_code=403,
                                    headers={"X-Request-ID": request_id})

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path not in PUBLIC_PATHS:
            logger.info("%s %s -> %s %.0fms rid=%s", request.method, request.url.path,
                        response.status_code, elapsed_ms, request_id)
        return response


class RateLimiter:
    """Per-IP rate limiter kept in memory. Each worker counts on its own, which is
    good enough to stop abuse. If you ever need exact limits across servers, move this to Redis."""

    def __init__(self):
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def check(self, key: str, bucket: str, limit: int, window_s: float = 60.0) -> int | None:
        """Count a hit. Returns None if it's allowed, or how many seconds to wait."""
        now = time.monotonic()
        self._maybe_sweep(now, window_s)
        hits = self._hits[(bucket, key)]
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(hits[0] + window_s - now) + 1)
        hits.append(now)
        return None

    def _maybe_sweep(self, now: float, window_s: float) -> None:
        # Every couple of minutes I forget idle clients so this doesn't grow forever.
        if now - self._last_sweep < 120:
            return
        self._last_sweep = now
        for k in [k for k, d in self._hits.items() if not d or d[-1] <= now - window_s]:
            self._hits.pop(k, None)


def rate_limit(bucket: str):
    """Use it like Depends(rate_limit("ai")). The limits come from config."""
    async def _dependency(request: Request) -> None:
        services = request.app.state.services
        settings: Settings = services.settings
        limit = {"ai": settings.rate_limit_ai_per_min,
                 "feedback": settings.rate_limit_feedback_per_min}.get(bucket, 60)
        retry_after = services.rate_limiter.check(client_ip(request, settings), bucket, limit)
        if retry_after is not None:
            raise AppError("rate_limited", 429, log_detail=f"bucket={bucket}", retry_after=retry_after)
    return _dependency


def require_admin(request: Request) -> None:
    """Admin routes don't exist at all unless ADMIN_TOKEN is set."""
    settings: Settings = request.app.state.services.settings
    if not settings.admin_token:
        raise AppError("not_found", 404, log_detail="admin disabled (ADMIN_TOKEN unset)")
    supplied = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(supplied, settings.admin_token):
        raise AppError("forbidden", 403, log_detail="bad admin token")
