"""Wires the clients together and runs the question-to-answer pipeline.

I tried to make sure the user always gets something useful. If translation
fails they still get the English answer, and if the voice fails they still get
the text. Only a full AI outage turns into an error."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .errors import AppError
from .http_client import build_client
from .integrations import GoogleForms, HuggingFace, JobQueue
from .khaya import LANG_CONFIG, KhayaClient
from .llm import LLMClient
from .middleware import RateLimiter
from .notifier import Mailer, Reporter
from .safety import (SafetyCheck, clean_for_speech, crisis_wrap, detect_crisis, enforce_hotline_policy,
                     sanitize_history, strip_urgent_tag)
from .storage import Storage

logger = logging.getLogger("kasa.pipeline")


@dataclass
class Services:
    settings: Settings
    http: httpx.AsyncClient
    llm: LLMClient
    khaya: KhayaClient
    storage: Storage
    forms: GoogleForms
    hf: HuggingFace
    jobs: JobQueue
    mailer: Mailer
    reporter: Reporter
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    health: object = None
    started_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> "Services":
        http = build_client(transport)
        storage = Storage(settings.data_dir)
        jobs = JobQueue(workers=4)
        llm = LLMClient(settings, http, storage)
        mailer = Mailer(settings, http)
        reporter = Reporter(settings, storage, mailer, jobs, llm_status=llm.status)
        llm.reporter = reporter
        return cls(settings=settings, http=http, llm=llm, khaya=KhayaClient(settings, http),
                   storage=storage, forms=GoogleForms(settings, http), hf=HuggingFace(settings, http),
                   jobs=jobs, mailer=mailer, reporter=reporter)

    async def start(self) -> None:
        await self.storage.init(self.settings.legacy_import_dirs)
        self.jobs.start()
        from .health import HealthChecker
        self.health = HealthChecker(self)
        if not self.settings.llm_providers:
            logger.error("STARTUP WARNING: no LLM provider keys set. Add at least one of XAI_API_KEY, "
                         "GEMINI_API_KEY, OPENAI_API_KEY, QWEN_API_KEY to .env")
        if not self.settings.email_configured:
            logger.warning("Alert emails are OFF (set RESEND_API_KEY or SMTP_HOST). Incidents are still "
                           "saved and visible on the admin page.")
        logger.info("Kasa Health API started (env=%s, llm chain=%s)", self.settings.environment,
                    [f"{p.name}:{p.model}" for p in self.settings.llm_providers])

    async def stop(self) -> None:
        await self.jobs.stop()
        await self.http.aclose()


@dataclass
class Answer:
    question_original: str
    question_english: str
    answer_english: str
    answer_local: str
    translation_fallback: bool
    provider: str
    timings_ms: dict
    safety: SafetyCheck


async def answer_question(svc: Services, *, text: str, language: str, history: object,
                          system_prompt: str, max_tokens: int, for_speech: bool) -> Answer:
    if language not in LANG_CONFIG:
        raise AppError("unsupported_language", 400, log_detail=language)
    text = (text or "").strip()
    if not text:
        raise AppError("empty_message", 400)
    if len(text) > svc.settings.max_message_chars:
        raise AppError("message_too_long", 400, log_detail=f"{len(text)} chars")

    cfg = LANG_CONFIG[language]
    timings: dict[str, int] = {}
    clock = time.perf_counter

    t = clock()
    try:
        question_en = await svc.khaya.translate(text, cfg["tr_to"]) or text
    except AppError as exc:
        logger.warning("Question translation failed, using original text: %s", exc.log_detail)
        question_en = text
    timings["translate_in"] = int((clock() - t) * 1000)

    t = clock()
    clean_history = sanitize_history(history, svc.settings.max_history_turns)
    answer_en, provider = await svc.llm.complete(system_prompt, clean_history, question_en, max_tokens,
                                                 fast=for_speech)
    timings["llm"] = int((clock() - t) * 1000)

    # Safety: patterns on what the user said, plus the model's own [URGENT] tag.
    answer_en, model_flag = strip_urgent_tag(answer_en)
    safety = detect_crisis(text, question_en)
    if model_flag and safety.severity != "high":
        safety = SafetyCheck(True, "high", safety.category if safety.flagged else "flagged_by_ai")
    answer_en = enforce_hotline_policy(answer_en)
    if for_speech:
        answer_en = clean_for_speech(answer_en)
    answer_en = crisis_wrap(answer_en, safety, for_speech=for_speech)

    t = clock()
    fallback = False
    try:
        answer_local = await svc.khaya.translate(answer_en, cfg["tr_from"]) or answer_en
    except AppError as exc:
        logger.warning("Answer translation failed, returning English: %s", exc.log_detail)
        answer_local, fallback = answer_en, True
    timings["translate_out"] = int((clock() - t) * 1000)

    return Answer(text, question_en, answer_en, answer_local, fallback, provider, timings, safety)
