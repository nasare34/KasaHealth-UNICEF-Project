"""Request shapes. Every text field has a length limit."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["tw", "dag", "ee"]
Speaker = Literal["female", "male_low", "male_high"]


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class ChatRequest(_Base):
    message: str = Field(min_length=1, max_length=2000)
    language: Language
    # Loosely typed on purpose. Bad items get dropped in safety.sanitize_history
    # instead of rejecting the user's whole question.
    history: list[Any] = Field(default_factory=list, max_length=40)
    session_id: Optional[str] = Field(default="", max_length=64)


class TTSRequest(_Base):
    text: str = Field(min_length=1, max_length=3000)
    language: Language
    speaker: Optional[str] = Field(default=None, max_length=20)


class TranslateRequest(_Base):
    text: str = Field(min_length=1, max_length=3000)
    lang: str = Field(min_length=4, max_length=10)


class RatingData(_Base):
    rating: Literal["up", "down"]
    language: str = Field(max_length=10)
    context: str = Field(default="chat", max_length=20)
    question_original: Optional[str] = Field(default="", max_length=3000)
    question_english: Optional[str] = Field(default="", max_length=3000)
    answer_local: Optional[str] = Field(default="", max_length=5000)
    answer_english: Optional[str] = Field(default="", max_length=5000)
    reason: Optional[str] = Field(default="", max_length=500)
    tester_code: Optional[str] = Field(default="", max_length=40)
    session_id: Optional[str] = Field(default="", max_length=64)
    timestamp: Optional[str] = Field(default="", max_length=40)


class SurveyData(_Base):
    name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=200)
    age: Optional[str] = Field(default=None, max_length=5)
    gender: Optional[str] = Field(default=None, max_length=40)
    location: Optional[str] = Field(default=None, max_length=80)
    ease: Optional[str] = Field(default=None, max_length=40)
    helpful: Optional[str] = Field(default=None, max_length=40)
    asr_accuracy: Optional[str] = Field(default=None, max_length=40)
    asr_transcript: Optional[str] = Field(default=None, max_length=40)
    text_translation: Optional[str] = Field(default=None, max_length=40)
    tts_quality: Optional[str] = Field(default=None, max_length=40)
    feedback: Optional[str] = Field(default=None, max_length=3000)
    recommend: Optional[str] = Field(default=None, max_length=20)
    is_tester: Optional[str] = Field(default=None, max_length=5)
    tester_code: Optional[str] = Field(default=None, max_length=40)
    timestamp: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=10)
    sessionId: Optional[str] = Field(default=None, max_length=64)


class SafetyContact(_Base):
    """Someone in difficulty chose to share contact details. Consent is required."""
    consent: bool
    phone: str = Field(min_length=7, max_length=30)
    name: Optional[str] = Field(default="", max_length=120)
    best_time: Optional[str] = Field(default="", max_length=80)
    safe_to_call: Optional[str] = Field(default="", max_length=40)
    note: Optional[str] = Field(default="", max_length=1000)
    category: Optional[str] = Field(default="", max_length=40)
    language: Optional[str] = Field(default="", max_length=10)
    session_id: Optional[str] = Field(default="", max_length=64)
    question_original: Optional[str] = Field(default="", max_length=2000)
    question_english: Optional[str] = Field(default="", max_length=2000)
