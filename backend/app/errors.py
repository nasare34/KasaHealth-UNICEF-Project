"""Error handling.

The rule I follow: users get a kind message and the technical details go to
the logs. If you add a new error code, give it a friendly line in FRIENDLY below
(and in api.js on the frontend)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("kasa.errors")

# What users actually see. The frontend keeps a copy of these too, so it still
# says something kind even if the request never reaches us.
FRIENDLY = {
    "bad_request":       "Hmm, something about that request didn't look quite right. Could you try again?",
    "unsupported_language": "That language isn't available yet. Please pick Twi, Dagbani or Ewe.",
    "message_too_long":  "That's a lot to take in at once! Could you ask a shorter question?",
    "empty_message":     "I'd love to help — please type or say your question first.",
    "audio_too_short":   "I didn't catch that. Please hold the button a little longer and speak clearly.",
    "audio_too_large":   "That recording is a bit too long for me. Please try a shorter question.",
    "audio_invalid":     "I couldn't read that recording. Please try speaking again.",
    "no_speech":         "I couldn't hear any words that time. Please try again a little closer to the mic.",
    "rate_limited":      "You're asking questions faster than I can answer! Please take a short breath and try again in a moment.",
    "busy":              "Lots of people are chatting with Kasa right now. Please try again in a few seconds — thank you for your patience!",
    "llm_unavailable":   "I'm having a little trouble thinking of an answer right now. Please try again shortly. If it's urgent, SHEplus Ghana is there for you: 055 054 5672 / 0800 00 11 22.",
    "asr_unavailable":   "I'm having trouble hearing right now. You can try again, or type your question instead.",
    "tts_unavailable":   "I couldn't prepare the voice for this answer, but you can still read it below.",
    "translate_unavailable": "I'm having trouble translating right now. Please try again in a moment.",
    "timeout":           "That took longer than expected. Please try again — it usually works on the next go.",
    "bad_phone":         "That phone number doesn't look quite right. Please check it and try again.",
    "not_found":         "We couldn't find what you were looking for.",
    "forbidden":         "Sorry, you don't have access to that.",
    "internal":          "Something unexpected happened on our side. Please try again — we're sorry about that.",
}

RETRYABLE = {"rate_limited", "busy", "llm_unavailable", "asr_unavailable",
             "tts_unavailable", "translate_unavailable", "timeout", "internal"}


class AppError(Exception):
    """Something went wrong that we expected might happen, and can explain kindly."""

    def __init__(self, code: str, status: int = 400, *, log_detail: str = "",
                 retry_after: int | None = None):
        self.code = code if code in FRIENDLY else "internal"
        self.status = status
        self.log_detail = log_detail
        self.retry_after = retry_after
        super().__init__(f"{self.code}: {log_detail}")


class UpstreamError(Exception):
    """An upstream API failed. `retryable` means trying again could help;
    `fatal_for_provider` means it won't (bad key, blocked, no credits), so move on."""

    def __init__(self, service: str, message: str, *, status: int | None = None,
                 retryable: bool = False, fatal_for_provider: bool = False):
        self.service = service
        self.status = status
        self.retryable = retryable
        self.fatal_for_provider = fatal_for_provider
        super().__init__(f"[{service}] {message}")


def error_payload(code: str, request_id: str) -> dict:
    message = FRIENDLY.get(code, FRIENDLY["internal"])
    return {
        "ok": False,
        "error": {"code": code, "message": message,
                  "retryable": code in RETRYABLE, "request_id": request_id},
        "detail": message,
    }


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def install_error_handlers(app: FastAPI) -> None:
    async def _report(request: Request, code: str, detail: str) -> None:
        """Emails the team about failures users can't fix themselves. It never raises,
        because a broken email setup shouldn't break the reply as well."""
        try:
            services = getattr(request.app.state, "services", None)
            if services is not None:
                await services.reporter.report_error(code, path=request.url.path, request_id=_rid(request),
                                                     detail=detail)
        except Exception:
            logger.exception("Could not report incident")

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        level = logging.WARNING if exc.status >= 500 else logging.INFO
        logger.log(level, "AppError code=%s status=%s rid=%s detail=%s",
                   exc.code, exc.status, _rid(request), exc.log_detail)
        if exc.status >= 500 and exc.code != "rate_limited":
            await _report(request, exc.code, exc.log_detail)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(error_payload(exc.code, _rid(request)), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # I pick out the common mistakes so users get a specific message, not a generic one.
        code = "bad_request"
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", [])]
            if "message" in loc and err.get("type") == "string_too_long":
                code = "message_too_long"
                break
            if "message" in loc and err.get("type") in {"string_too_short", "missing"}:
                code = "empty_message"
                break
            if "language" in loc:
                code = "unsupported_language"
                break
            if "phone" in loc:
                code = "bad_phone"
                break
        logger.info("Validation error rid=%s errors=%s", _rid(request), exc.errors()[:3])
        return JSONResponse(error_payload(code, _rid(request)), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 403: "forbidden", 401: "forbidden",
                405: "bad_request", 413: "audio_too_large", 429: "rate_limited"}.get(
                    exc.status_code, "internal" if exc.status_code >= 500 else "bad_request")
        return JSONResponse(error_payload(code, _rid(request)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error rid=%s path=%s", _rid(request), request.url.path)
        await _report(request, "internal", f"{type(exc).__name__}: {exc}")
        return JSONResponse(error_payload("internal", _rid(request)), status_code=500)
