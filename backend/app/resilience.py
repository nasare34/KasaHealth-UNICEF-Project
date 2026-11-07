"""Things that keep us standing when an upstream API misbehaves: limits on how
many calls run at once, circuit breakers, retries and a shared cache."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Hashable, TypeVar

from .errors import AppError, UpstreamError

logger = logging.getLogger("kasa.resilience")
T = TypeVar("T")


class Bulkhead:
    def __init__(self, name: str, limit: int, wait_timeout_s: float):
        self.name = name
        self.limit = max(1, limit)
        self.wait_timeout_s = wait_timeout_s
        self._sem = asyncio.Semaphore(self.limit)
        self.in_flight = 0
        self.waiting = 0

    @asynccontextmanager
    async def slot(self):
        self.waiting += 1
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.wait_timeout_s)
        except asyncio.TimeoutError:
            raise AppError("busy", 503, log_detail=f"bulkhead {self.name} saturated", retry_after=5)
        finally:
            self.waiting -= 1
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
            self._sem.release()

    def stats(self) -> dict:
        return {"limit": self.limit, "in_flight": self.in_flight, "waiting": self.waiting}


class CircuitBreaker:
    """Stops calling a service that keeps failing, then tries again after a
    cool-down. A hard failure (bad key, no credits) trips it straight away."""

    def __init__(self, name: str, failure_threshold: int = 5, reset_after_s: float = 30):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self.failures = 0
        self.opened_at: float | None = None
        self.cooldown_s = reset_after_s

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            return "half-open"
        return "open"

    @property
    def seconds_until_retry(self) -> int:
        if self.opened_at is None:
            return 0
        return max(0, int(self.cooldown_s - (time.monotonic() - self.opened_at)))

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        if self.opened_at is not None:
            logger.info("Circuit %s closed again", self.name)
        self.failures = 0
        self.opened_at = None
        self.cooldown_s = self.reset_after_s

    def record_failure(self, *, hard: bool = False, cooldown_s: float | None = None) -> None:
        self.failures += 1
        if hard or self.failures >= self.failure_threshold or self.state == "half-open":
            if self.state != "open":
                logger.warning("Circuit %s OPEN (failures=%s, hard=%s, cooldown=%ss)",
                               self.name, self.failures, hard, cooldown_s or self.reset_after_s)
            self.cooldown_s = cooldown_s or self.reset_after_s
            self.opened_at = time.monotonic()

    def reset(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.cooldown_s = self.reset_after_s


async def retry_async(fn: Callable[[], Awaitable[T]], *, attempts: int = 3,
                      base_delay: float = 0.4, max_delay: float = 4.0,
                      label: str = "call") -> T:
    """Retries UpstreamErrors marked retryable. Anything else goes straight up."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except UpstreamError as exc:
            last = exc
            if not exc.retryable or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random())
            logger.info("Retrying %s in %.2fs (attempt %s/%s): %s", label, delay, attempt, attempts, exc)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


class TTLCache:
    def __init__(self, name: str, max_items: int, ttl_s: float):
        self.name = name
        self.max_items = max(1, max_items)
        self.ttl_s = ttl_s
        self._data: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Future] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl_s, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    async def get_or_compute(self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        cached = self.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        # If the same request is already running, wait for that one instead of starting another.
        pending = self._inflight.get(key)
        if pending is not None:
            self.hits += 1
            return await asyncio.shield(pending)

        self.misses += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            value = await compute()
            self.set(key, value)
            fut.set_result(value)
            return value
        except BaseException as exc:
            fut.set_exception(exc)
            # Mark the error as seen, otherwise asyncio complains when nobody else was waiting.
            fut.exception()
            raise
        finally:
            self._inflight.pop(key, None)

    def stats(self) -> dict:
        return {"size": len(self._data), "hits": self.hits, "misses": self.misses}
