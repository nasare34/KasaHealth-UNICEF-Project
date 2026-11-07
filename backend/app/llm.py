"""Talks to the AI providers: Grok, Gemini, OpenAI, Qwen and Groq.

They all speak the OpenAI chat format, so one client covers them. I try them
in order. If one fails I move straight on, and if one is slow I start the next
one alongside it and take whichever answers first.

To add another OpenAI-compatible provider, add it in config.all_llm_providers;
it will then show up here and on the admin page."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field

import httpx

from .config import LLMProvider, Settings
from .errors import AppError, UpstreamError
from .http_client import send
from .resilience import Bulkhead, CircuitBreaker, TTLCache

logger = logging.getLogger("kasa.llm")

QUOTA_HINTS = ("credit", "quota", "billing", "spending limit", "insufficient_quota", "exceeded your current")
ORDER_KEY = "llm_settings"
SETTINGS_REFRESH_S = 5.0


@dataclass
class ProviderStats:
    successes: int = 0
    failures: int = 0
    avg_ms: float = 0.0
    last_ok_at: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0
    recent: list = field(default_factory=list)   # last 20 results, True or False

    def ok(self, ms: float) -> None:
        self.successes += 1
        self.avg_ms = ms if not self.avg_ms else self.avg_ms * 0.8 + ms * 0.2
        self.last_ok_at = time.time()
        self.recent = (self.recent + [True])[-20:]

    def fail(self, message: str) -> None:
        self.failures += 1
        self.last_error = message[:300]
        self.last_error_at = time.time()
        self.recent = (self.recent + [False])[-20:]


def classify_failure(exc: UpstreamError) -> tuple[bool, float | None, str]:
    """Works out how bad a failure is: (hard, cooldown_seconds, short_reason)."""
    text = str(exc).lower()
    if any(h in text for h in QUOTA_HINTS) or exc.status == 402:
        return True, 300.0, "out of credits / spending limit"
    if exc.status in (401, 403):
        return True, 120.0, "access denied (key or network)"
    if exc.status == 404:
        return True, 300.0, "model not found"
    if exc.status == 429:
        return False, None, "rate limited by provider"
    if exc.status and exc.status >= 500:
        return False, None, "provider server error"
    if "timeout" in text:
        return False, None, "too slow (timed out)"
    return False, None, "request failed"


class LLMClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, storage=None, reporter=None):
        self.settings = settings
        self.http = http
        self.storage = storage
        self.reporter = reporter          # set by Services afterwards (circular import otherwise)
        self.known = settings.all_llm_providers
        self.breakers = {name: CircuitBreaker(f"llm:{name}", failure_threshold=3, reset_after_s=30)
                         for name in self.known}
        self.stats = {name: ProviderStats() for name in self.known}
        self.bulkhead = Bulkhead("llm", settings.max_concurrent_llm, settings.queue_wait_timeout_s)
        self.cache = TTLCache("llm", settings.cache_max_items, settings.cache_ttl_s)
        self._order: list[str] = list(settings.llm_order)
        self._disabled: set[str] = set()
        self._settings_loaded_at = 0.0
        if not self.providers_sync():
            logger.error("No LLM provider configured — set at least one of XAI_API_KEY, GEMINI_API_KEY, "
                         "OPENAI_API_KEY, QWEN_API_KEY, GROQ_API_KEY.")

    # --- live settings (order / disabled) ---
    async def _refresh_settings(self, force: bool = False) -> None:
        if self.storage is None:
            return
        now = time.monotonic()
        if not force and now - self._settings_loaded_at < SETTINGS_REFRESH_S:
            return
        self._settings_loaded_at = now
        saved = await self.storage.get_meta(ORDER_KEY)
        if isinstance(saved, dict):
            order = [n for n in saved.get("order", []) if n in self.known]
            self._order = order + [n for n in self.settings.llm_order if n not in order]
            self._disabled = {n for n in saved.get("disabled", []) if n in self.known}

    def providers_sync(self) -> list[LLMProvider]:
        order = [n for n in self._order if n in self.known] + [n for n in self.known if n not in self._order]
        return [self.known[n] for n in order if self.known[n].enabled and n not in self._disabled]

    async def providers(self) -> list[LLMProvider]:
        await self._refresh_settings()
        return self.providers_sync()

    async def set_settings(self, order: list[str], disabled: list[str]) -> None:
        clean_order = [n for n in order if n in self.known]
        clean_disabled = [n for n in disabled if n in self.known]
        if self.storage is not None:
            await self.storage.set_meta(ORDER_KEY, {"order": clean_order, "disabled": clean_disabled})
        self._order = clean_order + [n for n in self.settings.llm_order if n not in clean_order]
        self._disabled = set(clean_disabled)
        self._settings_loaded_at = time.monotonic()
        self.cache = TTLCache("llm", self.settings.cache_max_items, self.settings.cache_ttl_s)
        logger.info("LLM settings changed: order=%s disabled=%s", clean_order, clean_disabled)

    def reset_circuits(self) -> None:
        for breaker in self.breakers.values():
            breaker.reset()

    # --- public API ---
    async def complete(self, system_prompt: str, history: list[dict], question: str,
                       max_tokens: int, *, fast: bool = False) -> tuple[str, str]:
        """Returns (answer, provider_name), or raises llm_unavailable if everything fails.
        fast=True is for the voice agent: it gives up on a slow provider sooner."""
        if not history:
            key = hashlib.sha256(f"{system_prompt}|{max_tokens}|{question.strip().lower()}".encode()).hexdigest()
            return await self.cache.get_or_compute(
                key, lambda: self._complete_uncached(system_prompt, history, question, max_tokens, fast))
        return await self._complete_uncached(system_prompt, history, question, max_tokens, fast)

    async def _complete_uncached(self, system_prompt: str, history: list[dict], question: str,
                                 max_tokens: int, fast: bool) -> tuple[str, str]:
        messages = [{"role": "system", "content": system_prompt}, *history,
                    {"role": "user", "content": question}]
        enabled = await self.providers()
        if not enabled:
            raise AppError("llm_unavailable", 503, log_detail="no LLM providers enabled/configured", retry_after=30)
        healthy = [p for p in enabled if self.breakers[p.name].allow()]
        candidates = healthy or enabled          # if everyone is cooling down, try them anyway
        hedge = self.settings.llm_hedge_agent_s if fast else self.settings.llm_hedge_chat_s
        async with self.bulkhead.slot():
            return await self._race(candidates, messages, max_tokens, hedge)

    async def _race(self, providers: list[LLMProvider], messages: list[dict], max_tokens: int,
                    hedge_s: float) -> tuple[str, str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.llm_total_timeout_s
        tasks: dict[asyncio.Task, LLMProvider] = {}
        errors: list[str] = []
        next_idx = 0

        def launch() -> None:
            nonlocal next_idx
            provider = providers[next_idx]
            next_idx += 1
            tasks[asyncio.create_task(self._attempt(provider, messages, max_tokens))] = provider

        launch()
        try:
            while tasks:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    errors.append("overall deadline reached")
                    break
                wait_for = min(hedge_s, remaining) if next_idx < len(providers) else remaining
                done, _ = await asyncio.wait(tasks.keys(), timeout=wait_for,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if next_idx < len(providers):
                        slow = ", ".join(p.name for p in tasks.values())
                        logger.info("LLM hedge: %s slow after %.1fs — also starting %s",
                                    slow, wait_for, providers[next_idx].name)
                        launch()
                        continue
                    errors.append("overall deadline reached")
                    break
                failed = False
                for task in done:
                    provider = tasks.pop(task)
                    try:
                        answer = task.result()
                    except UpstreamError as exc:
                        errors.append(f"{provider.name}: {exc}")
                        failed = True
                        continue
                    return answer, provider.name
                if failed and next_idx < len(providers) and len(tasks) == 0:
                    launch()                      # that one failed, start the next one now
                elif failed and next_idx < len(providers):
                    launch()                      # keep the race going
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        raise AppError("llm_unavailable", 503, log_detail=" | ".join(errors) or "no answer", retry_after=10)

    async def _attempt(self, provider: LLMProvider, messages: list[dict], max_tokens: int) -> str:
        breaker, stats = self.breakers[provider.name], self.stats[provider.name]
        started = time.perf_counter()
        try:
            answer = await self._call(provider, messages, max_tokens)
        except UpstreamError as exc:
            hard, cooldown, reason = classify_failure(exc)
            breaker.record_failure(hard=hard, cooldown_s=cooldown)
            stats.fail(f"{reason}: {str(exc)[:200]}")
            logger.warning("LLM %s failed (%s, status=%s): %s", provider.name, reason, exc.status, str(exc)[:300])
            if hard and self.reporter is not None:
                # e.g. Grok is out of credits. I tell the team now, before everything is down.
                asyncio.create_task(self.reporter.report_error(
                    f"provider_down:{provider.name}", path="llm", request_id="-",
                    detail=f"{provider.label} ({provider.model}) — {reason}\n{str(exc)[:500]}"))
            raise
        except asyncio.CancelledError:
            raise                                  # lost the race, that's not a failure
        ms = (time.perf_counter() - started) * 1000
        breaker.record_success()
        stats.ok(ms)
        logger.info("LLM ok provider=%s model=%s ms=%d", provider.name, provider.model, ms)
        return answer

    def _body(self, provider: LLMProvider, messages: list[dict], max_tokens: int, minimal: bool) -> dict:
        body: dict = {"model": provider.model, "messages": messages}
        if provider.token_param:
            body[provider.token_param] = int(max_tokens * provider.token_multiplier)
        if minimal:
            return body
        if provider.send_temperature:
            body["temperature"] = self.settings.llm_temperature
        body.update(provider.extra_body)
        return body

    async def _call(self, provider: LLMProvider, messages: list[dict], max_tokens: int) -> str:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
        try:
            response = await send(f"llm:{provider.name}", self.http, "POST", url, headers=headers,
                                  json=self._body(provider, messages, max_tokens, minimal=False),
                                  timeout=self.settings.llm_attempt_timeout_s)
        except UpstreamError as exc:
            if exc.status != 400:
                raise
            # Models change what parameters they accept. If a provider rejects one of our
            # optional settings, I try once more with just the basics.
            logger.info("LLM %s rejected parameters (400); retrying with a minimal request", provider.name)
            response = await send(f"llm:{provider.name}", self.http, "POST", url, headers=headers,
                                  json=self._body(provider, messages, max_tokens, minimal=True),
                                  timeout=self.settings.llm_attempt_timeout_s)
        try:
            data = response.json()
            content = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise UpstreamError(f"llm:{provider.name}", f"unexpected response shape: {exc!r}",
                                retryable=True) from exc
        if isinstance(content, list):  # some providers send the content back in parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = content.strip()
        if not content:
            raise UpstreamError(f"llm:{provider.name}", "empty completion", retryable=True)
        return content

    # --- admin / monitoring ---
    async def status(self) -> dict:
        await self._refresh_settings(force=True)
        order = [n for n in self._order if n in self.known] + [n for n in self.known if n not in self._order]
        rows = []
        for position, name in enumerate(order, start=1):
            p, b, st = self.known[name], self.breakers[name], self.stats[name]
            rows.append({
                "name": name, "label": p.label, "model": p.model or "(not set)",
                "position": position, "configured": p.enabled, "enabled": name not in self._disabled,
                "circuit": b.state, "retry_in_s": b.seconds_until_retry,
                "successes": st.successes, "failures": st.failures,
                "avg_ms": int(st.avg_ms), "recent_success_rate": (
                    round(sum(st.recent) / len(st.recent), 2) if st.recent else None),
                "last_error": st.last_error, "last_error_at": st.last_error_at, "last_ok_at": st.last_ok_at,
            })
        return {"providers": rows, "hedge_chat_s": self.settings.llm_hedge_chat_s,
                "hedge_agent_s": self.settings.llm_hedge_agent_s}

    def stats_summary(self) -> dict:
        return {
            "providers": [{"name": p.name, "model": p.model, "circuit": self.breakers[p.name].state}
                          for p in self.providers_sync()],
            "bulkhead": self.bulkhead.stats(),
            "cache": self.cache.stats(),
        }
