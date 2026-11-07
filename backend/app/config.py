"""Every setting lives here. I read them all from the environment once, at startup,
so nothing else in the code calls os.getenv. If you add a setting, put it here too."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# I look for .env in the backend folder first, then in the project root.
_HERE = Path(__file__).resolve().parent.parent
for candidate in (_HERE / ".env", _HERE.parent / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _str(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class LLMProvider:
    """One AI provider. Providers differ in small, annoying ways (what the token limit
    is called, whether they accept a temperature), so I keep those details here."""
    name: str
    label: str
    base_url: str
    api_key: str
    model: str
    token_param: str = "max_tokens"
    send_temperature: bool = True
    token_multiplier: float = 1.0
    extra: tuple = ()          # (key, value) pairs; a tuple because the dataclass is frozen

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    @property
    def extra_body(self) -> dict:
        return dict(self.extra)


@dataclass(frozen=True)
class Settings:
    # --- General ---
    environment: str = field(default_factory=lambda: _str("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))
    data_dir: Path = field(default_factory=lambda: Path(_str("DATA_DIR", str(_HERE / "data"))))
    # Where I look for the old JSON files. They get imported once, on the first start.
    legacy_import_dirs: list[Path] = field(default_factory=lambda: [
        Path(p) for p in _list("LEGACY_IMPORT_DIRS", f"{_str('DATA_DIR', str(_HERE / 'data'))},{_HERE}")])

    # --- LLM providers ---
    # The order I try providers in. Changing it on the admin page overrides this.
    llm_order: list[str] = field(default_factory=lambda: _list(
        "LLM_ORDER", "grok,gemini,openai,qwen,grok-fallback,groq"))
    xai_api_key: str = field(default_factory=lambda: _str("XAI_API_KEY"))
    xai_base_url: str = field(default_factory=lambda: _str("XAI_BASE_URL", "https://api.x.ai/v1"))
    xai_model: str = field(default_factory=lambda: _str("XAI_MODEL", "grok-4.20-0309-non-reasoning"))
    xai_fallback_model: str = field(default_factory=lambda: _str("XAI_FALLBACK_MODEL", "grok-4.3"))
    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY"))
    gemini_base_url: str = field(default_factory=lambda: _str(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"))
    gemini_model: str = field(default_factory=lambda: _str("GEMINI_MODEL", "gemini-3.5-flash"))
    gemini_reasoning_effort: str = field(default_factory=lambda: _str("GEMINI_REASONING_EFFORT", "low"))
    openai_api_key: str = field(default_factory=lambda: _str("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _str("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _str("OPENAI_MODEL", "gpt-5.4-mini"))
    openai_reasoning_effort: str = field(default_factory=lambda: _str("OPENAI_REASONING_EFFORT", "none"))
    qwen_api_key: str = field(default_factory=lambda: _str("QWEN_API_KEY") or _str("DASHSCOPE_API_KEY"))
    qwen_base_url: str = field(default_factory=lambda: _str(
        "QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"))
    qwen_model: str = field(default_factory=lambda: _str("QWEN_MODEL", "qwen-flash"))
    groq_api_key: str = field(default_factory=lambda: _str("GROQ_API_KEY"))
    groq_base_url: str = field(default_factory=lambda: _str("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    groq_model: str = field(default_factory=lambda: _str("GROQ_MODEL", "llama-3.3-70b-versatile"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.3))
    # How long I give a provider before starting the next one alongside it.
    # Lower feels snappier but costs more calls. The voice agent uses the shorter one.
    llm_hedge_chat_s: float = field(default_factory=lambda: _float("LLM_HEDGE_CHAT_SECONDS", 6))
    llm_hedge_agent_s: float = field(default_factory=lambda: _float("LLM_HEDGE_AGENT_SECONDS", 3))
    llm_attempt_timeout_s: float = field(default_factory=lambda: _float("LLM_ATTEMPT_TIMEOUT_SECONDS", 20))
    llm_total_timeout_s: float = field(default_factory=lambda: _float("LLM_TOTAL_TIMEOUT_SECONDS", 30))

    # --- Khaya (GhanaNLP) ---
    khaya_asr_key: str = field(default_factory=lambda: _str("KHAYA_ASR_KEY"))
    khaya_tts_key: str = field(default_factory=lambda: _str("KHAYA_TTS_KEY"))
    khaya_translate_key: str = field(default_factory=lambda: _str("KHAYA_TRANSLATE_KEY"))
    khaya_base_url: str = field(default_factory=lambda: _str("KHAYA_BASE_URL", "https://translation-api.ghananlp.org"))
    default_tts_speaker: str = field(default_factory=lambda: _str("DEFAULT_TTS_SPEAKER", "male_low"))

    # --- Hugging Face ---
    hf_token: str = field(default_factory=lambda: _str("HF_TOKEN"))
    hf_repo: str = field(default_factory=lambda: _str("HF_REPO", "nasare34/kasa-health-feedback"))

    # --- Google Forms ---
    survey_form_url: str = field(default_factory=lambda: _str(
        "SURVEY_FORM_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLSdXvHEUvMM98R_dwNSwXdMxYlEJtmKcYkEimg4oGkZZyGu7wA/formResponse",
    ))
    ratings_form_url: str = field(default_factory=lambda: _str(
        "RATINGS_FORM_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLSfE_u5-jFamI8F7-O-rX4wWZAJj6zDSQPvUiExhCuWwpJLFzg/formResponse",
    ))

    # --- Alerts: errors and safety reports are emailed here ---
    alert_email_to: str = field(default_factory=lambda: _str("ALERT_EMAIL_TO", "akwasi@khaya.ai"))
    alert_email_from: str = field(default_factory=lambda: _str("ALERT_EMAIL_FROM", "Kasa Health <alerts@kasa-health.local>"))
    # Option 1: Resend. Easiest if you can verify a domain; it only needs an API key.
    resend_api_key: str = field(default_factory=lambda: _str("RESEND_API_KEY"))
    resend_api_url: str = field(default_factory=lambda: _str("RESEND_API_URL", "https://api.resend.com/emails"))
    # Option 2: any SMTP server, e.g. Gmail with an App Password.
    smtp_host: str = field(default_factory=lambda: _str("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _str("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _str("SMTP_PASSWORD"))
    smtp_use_ssl: bool = field(default_factory=lambda: _bool("SMTP_USE_SSL", False))
    # So the inbox doesn't fill up, I email each kind of error at most once per
    # this many minutes. Repeats get counted and mentioned in the next email.
    alert_dedupe_minutes: int = field(default_factory=lambda: _int("ALERT_DEDUPE_MINUTES", 15))
    public_app_name: str = field(default_factory=lambda: _str("PUBLIC_APP_NAME", "Kasa Health"))

    # --- Security ---
    # Shared secret between the Flask frontend and this API. If it's set, the API turns
    # away anything that didn't come through the frontend. Use the same value on both.
    internal_api_token: str = field(default_factory=lambda: _str("INTERNAL_API_TOKEN"))
    # Password for the admin page. Leave it empty and the admin routes simply don't exist.
    admin_token: str = field(default_factory=lambda: _str("ADMIN_TOKEN"))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5000"))
    trust_proxy_headers: bool = field(default_factory=lambda: _bool("TRUST_PROXY_HEADERS", True))

    # --- Input limits ---
    max_message_chars: int = field(default_factory=lambda: _int("MAX_MESSAGE_CHARS", 1000))
    max_audio_bytes: int = field(default_factory=lambda: _int("MAX_AUDIO_BYTES", 8 * 1024 * 1024))
    min_audio_bytes: int = field(default_factory=lambda: _int("MIN_AUDIO_BYTES", 1000))
    max_history_turns: int = field(default_factory=lambda: _int("MAX_HISTORY_TURNS", 8))

    # --- Rate limits (requests per minute, per client IP) ---
    rate_limit_ai_per_min: int = field(default_factory=lambda: _int("RATE_LIMIT_AI_PER_MIN", 20))
    rate_limit_feedback_per_min: int = field(default_factory=lambda: _int("RATE_LIMIT_FEEDBACK_PER_MIN", 30))

    # --- Concurrency caps per upstream (protects them and us) ---
    max_concurrent_llm: int = field(default_factory=lambda: _int("MAX_CONCURRENT_LLM", 32))
    max_concurrent_asr: int = field(default_factory=lambda: _int("MAX_CONCURRENT_ASR", 16))
    max_concurrent_tts: int = field(default_factory=lambda: _int("MAX_CONCURRENT_TTS", 16))
    max_concurrent_translate: int = field(default_factory=lambda: _int("MAX_CONCURRENT_TRANSLATE", 32))
    queue_wait_timeout_s: float = field(default_factory=lambda: _float("QUEUE_WAIT_TIMEOUT_SECONDS", 20))

    # --- Caches ---
    cache_ttl_s: int = field(default_factory=lambda: _int("CACHE_TTL_SECONDS", 3600))
    cache_max_items: int = field(default_factory=lambda: _int("CACHE_MAX_ITEMS", 2000))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def all_llm_providers(self) -> dict[str, LLMProvider]:
        """All providers, set up or not, keyed by name."""
        providers = [
            LLMProvider("grok", "Grok (xAI)", self.xai_base_url, self.xai_api_key, self.xai_model),
            LLMProvider("grok-fallback", "Grok fallback (xAI)", self.xai_base_url, self.xai_api_key,
                        self.xai_fallback_model if self.xai_fallback_model != self.xai_model else ""),
            LLMProvider("gemini", "Gemini (Google)", self.gemini_base_url, self.gemini_api_key, self.gemini_model,
                        token_multiplier=4.0,
                        extra=(("reasoning_effort", self.gemini_reasoning_effort),) if self.gemini_reasoning_effort else ()),
            LLMProvider("openai", "OpenAI", self.openai_base_url, self.openai_api_key, self.openai_model,
                        token_param="", send_temperature=False,
                        extra=(("reasoning_effort", self.openai_reasoning_effort),) if self.openai_reasoning_effort else ()),
            LLMProvider("qwen", "Qwen (Alibaba)", self.qwen_base_url, self.qwen_api_key, self.qwen_model,
                        extra=(("enable_thinking", False),)),
            LLMProvider("groq", "Groq", self.groq_base_url, self.groq_api_key, self.groq_model),
        ]
        return {p.name: p for p in providers}

    @property
    def llm_providers(self) -> list[LLMProvider]:
        """The providers that have a key, in the order you chose. Anything you didn't
        list goes at the end."""
        known = self.all_llm_providers
        order = [n for n in self.llm_order if n in known]
        order += [n for n in known if n not in order]
        return [known[n] for n in order if known[n].enabled]

    # --- Alerts (email to the support team) ---
    @property
    def email_configured(self) -> bool:
        return bool(self.alert_email_to and (self.resend_api_key or self.smtp_host))

settings = Settings()
