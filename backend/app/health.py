"""Health checks for everything Kasa depends on.

Nothing here runs on its own. I only call these when someone presses a check
button on the admin dashboard, because every check costs a few API calls.
They go straight to the real APIs (no cache), otherwise a cached translation
could make a broken service look fine."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import struct
import time
import wave

from .errors import UpstreamError
from .http_client import send
from .khaya import LANG_CONFIG

logger = logging.getLogger("kasa.health")

LAST_KEY = "health_last"
HISTORY_KEY = "health_history"
HISTORY_SIZE = 48

# Slower than this and I mark it "slow". Raise these if your network is slow.
SLOW_MS = {"llm": 8000, "translate": 6000, "tts": 10000, "asr": 12000, "hf": 5000}

PROVIDER_CONSOLES = {
    "grok": ("console.x.ai", "XAI_API_KEY", "XAI_MODEL"),
    "grok-fallback": ("console.x.ai", "XAI_API_KEY", "XAI_FALLBACK_MODEL"),
    "gemini": ("aistudio.google.com", "GEMINI_API_KEY", "GEMINI_MODEL"),
    "openai": ("platform.openai.com", "OPENAI_API_KEY", "OPENAI_MODEL"),
    "qwen": ("modelstudio.console.alibabacloud.com", "QWEN_API_KEY", "QWEN_MODEL"),
    "groq": ("console.groq.com", "GROQ_API_KEY", "GROQ_MODEL"),
}
CREDIT_WORDS = ("credit", "quota", "billing", "spending limit", "insufficient_quota", "exceeded your current")


def _result(service: str, name: str, group: str, status: str, *, ms: int | None = None, detail: str = "",
            advice: str = "", http_status: int | None = None) -> dict:
    return {"service": service, "name": name, "group": group, "status": status, "ms": ms,
            "detail": detail[:400], "advice": advice, "http_status": http_status, "checked_at": time.time()}


def _llm_advice(provider: str, exc: UpstreamError) -> tuple[str, str]:
    console, key_var, model_var = PROVIDER_CONSOLES.get(provider, ("the provider's website", "the API key", "the model"))
    text = str(exc).lower()
    if any(w in text for w in CREDIT_WORDS) or exc.status == 402:
        return "Out of credits", (f"Add credits or raise the monthly spending limit at {console} (Billing). "
                                  "Then press “Reset cool-downs” so Kasa uses it again straight away.")
    if exc.status == 401:
        return "API key rejected", (f"The key looks wrong or was revoked. Create a new one at {console}, "
                                    f"put it in {key_var} and restart the backend.")
    if exc.status == 403:
        return "Access denied", (f"Either the key has no permission, or the provider is blocking this server's "
                                 f"network or country. Check the key at {console}; if it works elsewhere, turn this "
                                 "provider off here and rely on the others.")
    if exc.status == 404:
        return "Model not found", (f"The model name has probably changed. Check the current names at {console} "
                                   f"and update {model_var}.")
    if exc.status == 429:
        return "Rate limited", ("Too many requests for your plan right now. It usually clears in a minute; "
                                "if it keeps happening, raise the plan limit or move this provider lower.")
    if exc.status and exc.status >= 500:
        return "Provider outage", "Their servers are having problems. Nothing to fix on our side — the others will cover."
    if "timeout" in text:
        return "Too slow", "It didn't answer in time. Move it lower in the order so faster providers go first."
    return "Not reachable", "Check the server's internet connection and the provider's status page."


def _khaya_advice(service: str, exc: UpstreamError) -> tuple[str, str]:
    key_var = {"translate": "KHAYA_TRANSLATE_KEY", "tts": "KHAYA_TTS_KEY", "asr": "KHAYA_ASR_KEY"}[service]
    if exc.status in (401, 403):
        return "Key rejected", (f"Khaya refused {key_var}. Check that the key is right and the subscription for this "
                                "service is active at translation.ghananlp.org → Profile. Each service has its own key.")
    if exc.status == 429:
        return "Quota reached", "Khaya's rate limit or monthly quota was hit. Wait a little, or ask GhanaNLP to raise the limit."
    if exc.status and exc.status >= 500:
        return "Khaya outage", "Khaya's servers are having trouble. Try again later; if it lasts, contact GhanaNLP support."
    if exc.status and 400 <= exc.status < 500:
        return "Request rejected", f"Khaya said the test request was invalid (HTTP {exc.status}). The API may have changed."
    if "timeout" in str(exc).lower():
        return "Too slow", "Khaya didn't answer in time. Users will see slow replies until it recovers."
    return "Not reachable", "Couldn't connect to Khaya. Check the server's internet connection."


def _test_tone() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 300 * i / 16000)))
                               for i in range(16000)))
    return buf.getvalue()


class HealthChecker:
    def __init__(self, services):
        self.svc = services
        self.s = services.settings

    # --- individual checks ---
    async def _llm(self, provider) -> dict:
        started = time.perf_counter()
        try:
            await self.svc.llm._call(provider, [{"role": "user", "content": "Reply with the single word: ok"}], 10)
            ms = int((time.perf_counter() - started) * 1000)
            status = "slow" if ms > SLOW_MS["llm"] else "ok"
            advice = "Working, but slow. Consider moving it lower in the order." if status == "slow" else ""
            return _result(f"llm:{provider.name}", provider.label, "AI providers", status, ms=ms,
                           detail=provider.model, advice=advice)
        except UpstreamError as exc:
            title, advice = _llm_advice(provider.name, exc)
            return _result(f"llm:{provider.name}", provider.label, "AI providers", "down",
                           ms=int((time.perf_counter() - started) * 1000), detail=f"{title} — {str(exc)[:250]}",
                           advice=advice, http_status=exc.status)

    async def _khaya_call(self, service: str, method_args: dict) -> tuple[object, int]:
        started = time.perf_counter()
        r = await send(service, self.svc.http, **method_args)
        return r, int((time.perf_counter() - started) * 1000)

    async def _translate(self, lang: str) -> dict:
        cfg, name = LANG_CONFIG[lang], f"Translation · {LANG_CONFIG[lang]['name']}"
        try:
            r, ms = await self._khaya_call("translate", {
                "method": "POST", "url": self.svc.khaya.translate_url, "timeout": 20,
                "headers": {"Ocp-Apim-Subscription-Key": self.s.khaya_translate_key, "Content-Type": "application/json"},
                "json": {"in": "How are you?", "lang": cfg["tr_from"]}})
            out = r.text.strip().strip('"')
            if not out:
                return _result(f"translate:{lang}", name, "Khaya", "down", ms=ms, detail="Empty translation",
                               advice="Khaya answered but returned nothing. Try again; if it repeats, contact GhanaNLP.")
            status = "slow" if ms > SLOW_MS["translate"] else "ok"
            return _result(f"translate:{lang}", name, "Khaya", status, ms=ms, detail=f"“How are you?” → {out[:80]}")
        except UpstreamError as exc:
            title, advice = _khaya_advice("translate", exc)
            return _result(f"translate:{lang}", name, "Khaya", "down", detail=f"{title} — {str(exc)[:200]}",
                           advice=advice, http_status=exc.status)

    async def _tts_then_asr(self, lang: str) -> list[dict]:
        """Speak a short phrase with TTS, then feed that same audio to ASR.
        If ASR can hear what TTS said, the whole voice path is working."""
        cfg = LANG_CONFIG[lang]
        phrase = {"tw": "Medaase", "dag": "Naawuni deei", "ee": "Akpe"}.get(lang, "Hello")
        tts_name, asr_name = f"Voice (TTS) · {cfg['name']}", f"Speech recognition · {cfg['name']}"
        audio: bytes | None = None
        try:
            r, ms = await self._khaya_call("tts", {
                "method": "POST", "url": self.svc.khaya.tts_url, "timeout": 30,
                "headers": {"Ocp-Apim-Subscription-Key": self.s.khaya_tts_key, "Content-Type": "application/json"},
                "json": {"text": phrase, "language": cfg["tts"], "speaker": self.svc.khaya.resolve_speaker(None)}})
            ctype = r.headers.get("content-type", "")
            if "audio" in ctype or "octet-stream" in ctype:
                audio = r.content
            else:
                b64 = (r.json() or {}).get("audio") or (r.json() or {}).get("audioContent") or ""
                audio = base64.b64decode(b64) if b64 else None
            if not audio:
                tts = _result(f"tts:{lang}", tts_name, "Khaya", "down", ms=ms, detail="No audio in the response",
                              advice="Khaya answered without audio. Try again; if it repeats, contact GhanaNLP.")
            else:
                status = "slow" if ms > SLOW_MS["tts"] else "ok"
                tts = _result(f"tts:{lang}", tts_name, "Khaya", status, ms=ms,
                              detail=f"“{phrase}” → {len(audio) // 1024} KB of audio")
        except UpstreamError as exc:
            title, advice = _khaya_advice("tts", exc)
            tts = _result(f"tts:{lang}", tts_name, "Khaya", "down", detail=f"{title} — {str(exc)[:200]}",
                          advice=advice, http_status=exc.status)

        # If TTS gave us a WAV I use it. Otherwise a plain tone still proves the key works.
        sample = audio if audio and audio[:4] == b"RIFF" else _test_tone()
        try:
            r, ms = await self._khaya_call("asr", {
                "method": "POST", "url": self.svc.khaya.asr_url, "params": {"language": cfg["asr"]}, "timeout": 40,
                "headers": {"Ocp-Apim-Subscription-Key": self.s.khaya_asr_key, "Content-Type": "audio/wav"},
                "content": sample})
            heard = r.text.strip().strip('"')[:80]
            status = "slow" if ms > SLOW_MS["asr"] else "ok"
            detail = f"Heard: “{heard}”" if heard else "Reachable (the test audio had no words to pick up)"
            asr = _result(f"asr:{lang}", asr_name, "Khaya", status, ms=ms, detail=detail)
        except UpstreamError as exc:
            if exc.status == 400 and sample is not audio:
                asr = _result(f"asr:{lang}", asr_name, "Khaya", "ok", detail="Reachable (rejected the test tone, which is fine)")
            else:
                title, advice = _khaya_advice("asr", exc)
                asr = _result(f"asr:{lang}", asr_name, "Khaya", "down", detail=f"{title} — {str(exc)[:200]}",
                              advice=advice, http_status=exc.status)
        return [tts, asr]

    async def _huggingface(self) -> dict:
        if not self.svc.hf.enabled:
            return _result("hf", "Hugging Face uploads", "Storage & delivery", "off",
                           advice="Set HF_TOKEN (a write token) so voice corrections get uploaded.")
        started = time.perf_counter()
        try:
            r = await send("hf", self.svc.http, "GET", "https://huggingface.co/api/whoami-v2",
                           headers={"Authorization": f"Bearer {self.s.hf_token}"}, timeout=10)
            ms = int((time.perf_counter() - started) * 1000)
            data = r.json()
            role = ((data.get("auth") or {}).get("accessToken") or {}).get("role", "")
            if role and role not in ("write", "fineGrained", "admin"):
                return _result("hf", "Hugging Face uploads", "Storage & delivery", "warning", ms=ms,
                               detail=f"Signed in as {data.get('name')}, but the token is “{role}”",
                               advice="Uploads need a token with write access. Create one at huggingface.co → Settings → Access Tokens.")
            return _result("hf", "Hugging Face uploads", "Storage & delivery", "ok", ms=ms,
                           detail=f"Signed in as {data.get('name')}")
        except UpstreamError as exc:
            advice = ("The token is invalid or expired. Create a new write token at huggingface.co → Settings → "
                      "Access Tokens and update HF_TOKEN." if exc.status in (401, 403)
                      else "Couldn't reach Hugging Face. Corrections are kept on the server and can be re-sent later.")
            return _result("hf", "Hugging Face uploads", "Storage & delivery", "down",
                           detail=str(exc)[:250], advice=advice, http_status=exc.status)

    async def _email(self) -> dict:
        stats = self.svc.reporter.stats()
        if not stats["email_configured"]:
            return _result("email", "Alert emails", "Storage & delivery", "off",
                           detail=f"Would go to {stats['to']}",
                           advice="Set RESEND_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASSWORD (see DEPLOY.md), "
                                  "then use “Send test email”.")
        if stats["failed"] and not stats["sent"]:
            return _result("email", "Alert emails", "Storage & delivery", "down",
                           detail=f"{stats['failed']} send(s) failed, none succeeded",
                           advice="Check the email settings (password / API key / sender address), then send a test email.")
        return _result("email", "Alert emails", "Storage & delivery", "ok",
                       detail=f"Sending to {stats['to']} · sent {stats['sent']}, failed {stats['failed']} (this process)")

    async def _database(self) -> dict:
        started = time.perf_counter()
        ok = await self.svc.storage.ping()
        ms = int((time.perf_counter() - started) * 1000)
        if not ok:
            return _result("db", "Database", "Storage & delivery", "down", ms=ms,
                           advice="The database can't be opened. Check that DATA_DIR exists, is writable and the disk isn't full.")
        return _result("db", "Database", "Storage & delivery", "ok", ms=ms, detail=str(self.svc.storage.db_path))

    async def _jobs(self) -> dict:
        st = self.svc.jobs.stats()
        if st["dropped"] or st["queued"] > 200:
            return _result("jobs", "Background jobs", "Storage & delivery", "warning",
                           detail=f"{st['queued']} waiting, {st['dropped']} deferred",
                           advice="Syncing is falling behind (Google Forms / Hugging Face / emails). Records are safe; "
                                  "use “Retry failed syncs” once things calm down.")
        return _result("jobs", "Background jobs", "Storage & delivery", "ok",
                       detail=f"{st['queued']} waiting · {st['processed']} done · {st['failed']} failed")

    # --- run everything ---
    async def run(self, *, full: bool = False) -> dict:
        langs = list(LANG_CONFIG) if full else ["tw"]
        llm_checks = [self._llm(p) for p in self.svc.llm.known.values() if p.enabled]
        not_set = [_result(f"llm:{p.name}", p.label, "AI providers", "off",
                           advice=f"Add {PROVIDER_CONSOLES[p.name][1]} to use it as a backup.")
                   for p in self.svc.llm.known.values() if not p.enabled and p.name in PROVIDER_CONSOLES
                   and p.name != "grok-fallback"]
        tasks = [*llm_checks, *[self._translate(l) for l in langs], *[self._tts_then_asr(l) for l in langs],
                 self._huggingface(), self._email(), self._database(), self._jobs()]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict] = []
        for item in gathered:
            if isinstance(item, BaseException):
                logger.exception("Health check crashed", exc_info=item)
                results.append(_result("unknown", "Health check", "Other", "down", detail=repr(item)[:200]))
            elif isinstance(item, list):
                results.extend(item)
            else:
                results.append(item)
        results.extend(not_set)

        disabled = getattr(self.svc.llm, "_disabled", set())
        for r in results:
            if r["service"].startswith("llm:") and r["service"][4:] in disabled and r["status"] != "off":
                r["detail"] = f"(switched off in the order) {r['detail']}"

        summary = self._summarise(results)
        report = {"checked_at": time.time(), "full": full, "summary": summary, "results": results}
        await self._save(report)
        return report

    @staticmethod
    def _summarise(results: list[dict]) -> dict:
        llm_up = [r for r in results if r["group"] == "AI providers" and r["status"] in ("ok", "slow")]
        khaya_down = [r for r in results if r["group"] == "Khaya" and r["status"] == "down"]
        down = [r for r in results if r["status"] == "down"]
        warn = [r for r in results if r["status"] in ("slow", "warning")]
        if not llm_up:
            level, headline = "critical", "No AI provider is working — users can't get answers."
        elif any(r["service"].startswith("translate:") for r in khaya_down):
            level, headline = "critical", "Translation is down — answers will come back in English only."
        elif down:
            level, headline = "degraded", f"{len(down)} service(s) need attention. Kasa is still answering."
        elif warn:
            level, headline = "warning", "Everything works, but some services are slow or need a look."
        else:
            level, headline = "ok", "All systems working."
        return {"level": level, "headline": headline, "down": len(down), "warnings": len(warn),
                "llm_working": len(llm_up), "total": len(results)}

    async def _save(self, report: dict) -> None:
        await self.svc.storage.set_meta(LAST_KEY, report)
        history = await self.svc.storage.get_meta(HISTORY_KEY, []) or []
        history.append({"t": report["checked_at"], "level": report["summary"]["level"],
                        "s": {r["service"]: r["status"] for r in report["results"]}})
        await self.svc.storage.set_meta(HISTORY_KEY, history[-HISTORY_SIZE:])

    async def last(self) -> dict | None:
        return await self.svc.storage.get_meta(LAST_KEY)

    async def history(self) -> list:
        return await self.svc.storage.get_meta(HISTORY_KEY, []) or []
