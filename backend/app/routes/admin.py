"""Admin routes. They all need the X-Admin-Token header, and they don't exist at
all when ADMIN_TOKEN isn't set."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..errors import AppError, UpstreamError
from ..http_client import send
from ..integrations import make_feedback_job, make_rating_job, make_survey_job
from ..khaya import LANG_CONFIG
from ..middleware import require_admin
from ..services import Services
from ..storage import KINDS

public = APIRouter()
admin = APIRouter(dependencies=[Depends(require_admin)])


def svc(request: Request) -> Services:
    return request.app.state.services


# --- Public ---
@public.get("/health")
async def health():
    """Is the process up? Cheap, so Render can call it as often as it likes."""
    return {"status": "ok", "version": "8.0.0"}


@public.get("/ready")
async def ready(request: Request):
    """Are the config and database usable? This never calls a paid API."""
    services = svc(request)
    checks = {
        "llm_configured": bool(services.settings.llm_providers),
        "khaya_configured": all([services.settings.khaya_asr_key, services.settings.khaya_tts_key,
                                 services.settings.khaya_translate_key]),
        "storage": await services.storage.ping(),
    }
    ok = all(checks.values())
    return JSONResponse({"status": "ready" if ok else "degraded", "checks": checks},
                        status_code=200 if ok else 503)


@public.get("/languages")
async def languages():
    return {"languages": {code: {"name": cfg["name"]} for code, cfg in LANG_CONFIG.items()}}


# --- Admin: exports ---
def _export_name(kind: str) -> str:
    return {"rating": "ratings", "feedback": "feedback", "survey": "surveys",
            "incident": "incidents", "safety": "safety_reports"}[kind]


@admin.get("/admin/export/{kind}")
async def export(kind: str, request: Request):
    if kind not in KINDS:
        raise AppError("not_found", 404, log_detail=kind)
    rows = await svc(request).storage.export(kind)
    return JSONResponse({_export_name(kind): rows, "count": len(rows)},
                        headers={"Content-Disposition": f'attachment; filename="kasa_{_export_name(kind)}.json"'})


# The old URLs still work (with the token now), so existing bookmarks don't break.
@admin.get("/feedback/export")
async def export_feedback_legacy(request: Request):
    return await export("feedback", request)


@admin.get("/feedback/ratings/export")
async def export_ratings_legacy(request: Request):
    return await export("rating", request)


@admin.get("/survey/export")
async def export_surveys_legacy(request: Request):
    return await export("survey", request)


# --- Admin: operations ---
@admin.get("/admin/stats")
async def stats(request: Request):
    services = svc(request)
    return {
        "uptime_s": int(time.time() - services.started_at),
        "records": await services.storage.counts(),
        "llm": services.llm.stats_summary(),
        "alerts": services.reporter.stats(),
        "khaya": services.khaya.stats(),
        "jobs": services.jobs.stats(),
    }


@admin.post("/admin/retry-sync")
async def retry_sync(request: Request):
    """Re-send anything that failed to reach Google Forms, Hugging Face or email."""
    services = svc(request)
    queued = {"emails": await services.reporter.resend_failed()}
    for kind in ("rating", "survey", "feedback"):
        rows = await services.storage.failed(kind)
        for record in rows:
            if kind == "rating":
                job = make_rating_job(services.storage, services.forms, record)
            elif kind == "survey":
                job = make_survey_job(services.storage, services.forms, record)
            else:
                if record.get("rating") != "down":
                    continue
                audio_file = record.get("audio_file")
                path = services.settings.data_dir / "audio" / audio_file if audio_file else None
                job = make_feedback_job(services.storage, services.hf, record, path)
            services.jobs.submit(f"retry:{kind}:{record['id']}", job)
        queued[kind] = len(rows)
    return {"queued": queued}


@admin.get("/admin/diagnostics")
async def diagnostics(request: Request):
    """Makes a tiny real call to every upstream. Admin only, since it costs a little."""
    services = svc(request)
    report: dict = {"llm": {}}

    for provider in [p for p in services.llm.known.values() if p.enabled]:
        started = time.perf_counter()
        try:
            answer = await services.llm._call(
                provider, [{"role": "user", "content": "Reply with the single word: ok"}], 5)
            report["llm"][provider.name] = {"ok": True, "model": provider.model, "reply": answer[:20],
                                            "ms": int((time.perf_counter() - started) * 1000)}
        except UpstreamError as exc:
            report["llm"][provider.name] = {"ok": False, "model": provider.model, "status": exc.status,
                                            "error": str(exc)[:300]}

    try:
        report["translate"] = {"ok": True, "sample": await services.khaya.translate("Hello", "en-tw")}
    except AppError as exc:
        report["translate"] = {"ok": False, "error": exc.log_detail[:300]}

    try:
        _, fmt = await services.khaya.synthesize("Medaase", "tw")
        report["tts"] = {"ok": True, "format": fmt}
    except AppError as exc:
        report["tts"] = {"ok": False, "error": exc.log_detail[:300]}

    if services.hf.enabled:
        try:
            r = await send("hf", services.http, "GET", "https://huggingface.co/api/whoami-v2",
                           headers={"Authorization": f"Bearer {services.settings.hf_token}"}, timeout=10)
            report["huggingface"] = {"ok": True, "user": r.json().get("name")}
        except UpstreamError as exc:
            report["huggingface"] = {"ok": False, "error": str(exc)[:300]}
    else:
        report["huggingface"] = {"ok": False, "error": "HF_TOKEN not set"}
    return report


# --- Admin: health & overview ---
class HealthRunIn(BaseModel):
    full: bool = False


@admin.get("/admin/health")
async def health_status(request: Request):
    """The last saved result and the history. Reading this never calls an API,
    so the dashboard can refresh it as often as it likes."""
    services = svc(request)
    return {"last": await services.health.last(), "history": await services.health.history()}


@admin.post("/admin/health/run")
async def health_run(request: Request, body: HealthRunIn):
    services = svc(request)
    # Stops a double-click running the paid checks twice. Anything under 5 s old gets reused.
    last = await services.health.last()
    if last and time.time() - last["checked_at"] < 5 and (last.get("full") or not body.full):
        report = last
    else:
        report = await services.health.run(full=body.full)
    return {"last": report, "history": await services.health.history()}


@admin.get("/admin/overview")
async def overview(request: Request):
    services = svc(request)
    data = await services.storage.overview()
    data["uptime_s"] = int(time.time() - services.started_at)
    data["jobs"] = services.jobs.stats()
    data["alerts"] = services.reporter.stats()
    return data


# --- Admin: LLM providers (live switching) ---
class LLMSettingsIn(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=20)
    disabled: list[str] = Field(default_factory=list, max_length=20)


@admin.get("/admin/llm")
async def llm_status(request: Request):
    return await svc(request).llm.status()


@admin.post("/admin/llm")
async def llm_update(request: Request, body: LLMSettingsIn):
    """Change the provider order or switch some off. Every worker picks it up within ~5 s."""
    services = svc(request)
    await services.llm.set_settings(body.order, body.disabled)
    return await services.llm.status()


@admin.post("/admin/llm/reset")
async def llm_reset(request: Request):
    """Forget recent failures so every provider gets tried again now,
    e.g. right after you top up credits."""
    services = svc(request)
    services.llm.reset_circuits()
    return await services.llm.status()


@admin.post("/admin/alerts/test")
async def alerts_test(request: Request):
    services = svc(request)
    if not services.mailer.configured:
        raise AppError("bad_request", 400, log_detail="email not configured")
    try:
        await services.mailer.send(f"[{services.settings.public_app_name}] Test alert",
                                   "This is a test. If you can read this, error and safety alerts will reach you.")
    except Exception as exc:
        # Admin only, so showing the real reason here is fine (and saves you digging in logs).
        return JSONResponse({"ok": False, "error": {"code": "email_failed", "message": f"Test email failed: {exc}"}},
                            status_code=502)
    return {"ok": True, "sent_to": services.settings.alert_email_to}
