"""Khaya (GhanaNLP): speech recognition, translation and text-to-speech.

If Khaya ever changes an endpoint, the URLs are built in KhayaClient.__init__."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re

import httpx

from .config import Settings
from .errors import AppError, UpstreamError
from .http_client import send
from .resilience import Bulkhead, CircuitBreaker, TTLCache, retry_async

logger = logging.getLogger("kasa.khaya")

LANG_CONFIG: dict[str, dict[str, str]] = {
    "tw":  {"name": "Twi",     "asr": "tw",  "tr_to": "tw-en",  "tr_from": "en-tw",  "tts": "twi"},
    "dag": {"name": "Dagbani", "asr": "dag", "tr_to": "dag-en", "tr_from": "en-dag", "tts": "dag"},
    "ee":  {"name": "Ewe",     "asr": "ee",  "tr_to": "ee-en",  "tr_from": "en-ee",  "tts": "ewe"},
}
ALLOWED_PAIRS = {c["tr_to"] for c in LANG_CONFIG.values()} | {c["tr_from"] for c in LANG_CONFIG.values()}
ALLOWED_SPEAKERS = {"female", "male_low", "male_high"}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_for_tts(text: str, max_chunks: int = 4, min_chars: int = 40) -> list[str]:
    """Split text into sentence chunks so I can voice them in parallel. Tiny
    sentences get merged so we don't send lots of little requests."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    if not sentences:
        return []
    chunks: list[str] = []
    for sentence in sentences:
        if chunks and (len(chunks[-1]) < min_chars or len(chunks) >= max_chunks):
            chunks[-1] = f"{chunks[-1]} {sentence}"
        else:
            chunks.append(sentence)
    return chunks


class KhayaClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        base = settings.khaya_base_url.rstrip("/")
        self.asr_url = f"{base}/asr/v1/transcribe"
        self.tts_url = f"{base}/tts/v2/synthesize"
        self.translate_url = f"{base}/v2/translate"

        wait = settings.queue_wait_timeout_s
        self.bulkheads = {
            "asr": Bulkhead("asr", settings.max_concurrent_asr, wait),
            "tts": Bulkhead("tts", settings.max_concurrent_tts, wait),
            "translate": Bulkhead("translate", settings.max_concurrent_translate, wait),
        }
        self.breakers = {name: CircuitBreaker(name, failure_threshold=5, reset_after_s=20)
                         for name in self.bulkheads}
        self.translate_cache = TTLCache("translate", settings.cache_max_items, settings.cache_ttl_s)
        self.tts_cache = TTLCache("tts", max(50, settings.cache_max_items // 4), settings.cache_ttl_s)

    # --- shared guard: breaker + bulkhead + retry ---
    async def _guarded(self, service: str, call, *, unavailable_code: str):
        breaker = self.breakers[service]
        if not breaker.allow():
            raise AppError(unavailable_code, 503, log_detail=f"{service} circuit open", retry_after=10)
        async with self.bulkheads[service].slot():
            try:
                result = await retry_async(call, attempts=3, label=service)
            except UpstreamError as exc:
                # A 4xx (other than 408/429) is about this input, not Khaya being down.
                if exc.status and 400 <= exc.status < 500 and not exc.retryable and not exc.fatal_for_provider:
                    raise
                breaker.record_failure(hard=exc.fatal_for_provider)
                raise AppError(unavailable_code, 503, log_detail=str(exc), retry_after=10) from exc
            breaker.record_success()
            return result

    # --- ASR ---
    async def transcribe(self, audio: bytes, language: str) -> str:
        cfg = LANG_CONFIG[language]

        async def call():
            r = await send("asr", self.http, "POST", self.asr_url,
                           params={"language": cfg["asr"]},
                           headers={"Ocp-Apim-Subscription-Key": self.s.khaya_asr_key,
                                    "Content-Type": "audio/wav"},
                           content=audio, timeout=60)
            try:
                data = r.json()
            except ValueError:
                return r.text.strip().strip('"')
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                return (data.get("transcription") or data.get("text") or data.get("transcript") or "").strip()
            return ""

        try:
            return await self._guarded("asr", call, unavailable_code="asr_unavailable")
        except UpstreamError as exc:  # a 4xx here means Khaya couldn't make sense of this audio
            raise AppError("audio_invalid", 400, log_detail=str(exc)) from exc

    # --- Translation ---
    async def translate(self, text: str, lang_pair: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if lang_pair not in ALLOWED_PAIRS:
            raise AppError("unsupported_language", 400, log_detail=f"pair {lang_pair}")

        async def call():
            r = await send("translate", self.http, "POST", self.translate_url,
                           headers={"Ocp-Apim-Subscription-Key": self.s.khaya_translate_key,
                                    "Content-Type": "application/json"},
                           json={"in": text, "lang": lang_pair}, timeout=30)
            try:
                data = r.json()
            except ValueError:
                return r.text.strip() or text
            if isinstance(data, str):
                return data.strip() or text
            if isinstance(data, dict):
                return (data.get("translatedText") or data.get("out") or text).strip()
            return text

        key = (lang_pair, text)
        try:
            return await self.translate_cache.get_or_compute(
                key, lambda: self._guarded("translate", call, unavailable_code="translate_unavailable"))
        except UpstreamError as exc:
            raise AppError("translate_unavailable", 502, log_detail=str(exc)) from exc

    # --- TTS ---
    def resolve_speaker(self, speaker: str | None) -> str:
        if speaker in ALLOWED_SPEAKERS:
            return speaker  # type: ignore[return-value]
        return self.s.default_tts_speaker if self.s.default_tts_speaker in ALLOWED_SPEAKERS else "male_low"

    async def _synthesize_once(self, text: str, language: str, speaker: str) -> tuple[str, str]:
        cfg = LANG_CONFIG[language]

        async def call():
            r = await send("tts", self.http, "POST", self.tts_url,
                           headers={"Ocp-Apim-Subscription-Key": self.s.khaya_tts_key,
                                    "Content-Type": "application/json"},
                           json={"text": text, "language": cfg["tts"], "speaker": speaker}, timeout=60)
            ctype = r.headers.get("content-type", "")
            if "audio" in ctype or "octet-stream" in ctype:
                fmt = "mp3" if "mpeg" in ctype or "mp3" in ctype else "wav"
                return base64.b64encode(r.content).decode(), fmt
            data = r.json()
            audio = data.get("audio") or data.get("audioContent") or ""
            if not audio:
                raise UpstreamError("tts", "response had no audio", retryable=True)
            return audio, data.get("format", "wav")

        return await self._guarded("tts", call, unavailable_code="tts_unavailable")

    async def synthesize(self, text: str, language: str, speaker: str | None = None) -> tuple[str, str]:
        """Returns (base64 audio, format) for the whole text."""
        text = (text or "").strip()
        if not text:
            raise AppError("tts_unavailable", 400, log_detail="empty text")
        voice = self.resolve_speaker(speaker)
        key = hashlib.sha256(f"{language}|{voice}|{text}".encode()).hexdigest()

        async def compute():
            try:
                return await self._synthesize_once(text, language, voice)
            except UpstreamError as exc:
                fallback = self.resolve_speaker(None)
                if voice == fallback:
                    raise AppError("tts_unavailable", 502, log_detail=str(exc)) from exc
                logger.info("TTS speaker %s rejected (%s); falling back to %s", voice, exc.status, fallback)
                try:
                    return await self._synthesize_once(text, language, fallback)
                except UpstreamError as exc2:
                    raise AppError("tts_unavailable", 502, log_detail=str(exc2)) from exc2

        return await self.tts_cache.get_or_compute(key, compute)

    async def synthesize_segments(self, text: str, language: str, speaker: str | None = None) -> list[dict]:
        """Voice the sentence chunks in parallel. If one chunk fails I skip it rather
        than lose the whole reply; this only raises if every chunk fails."""
        chunks = split_for_tts(text) or [text]
        results = await asyncio.gather(*(self.synthesize(c, language, speaker) for c in chunks),
                                       return_exceptions=True)
        segments = []
        for chunk, result in zip(chunks, results):
            if isinstance(result, BaseException):
                logger.warning("TTS segment failed (%d chars): %s", len(chunk), result)
                continue
            audio, fmt = result
            segments.append({"text": chunk, "audio_base64": audio, "format": fmt})
        if not segments:
            raise AppError("tts_unavailable", 503, log_detail="all TTS segments failed")
        return segments

    def stats(self) -> dict:
        return {
            "bulkheads": {k: v.stats() for k, v in self.bulkheads.items()},
            "circuits": {k: v.state for k, v in self.breakers.items()},
            "caches": {"translate": self.translate_cache.stats(), "tts": self.tts_cache.stats()},
        }
