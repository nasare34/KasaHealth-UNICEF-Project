"""Speech recognition, translation, voice, chat and the voice agent."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from ..errors import AppError
from ..khaya import ALLOWED_PAIRS, LANG_CONFIG
from ..middleware import rate_limit
from ..safety import AGENT_PROMPT, ASRH_PROMPT, DISCLAIMER, validate_wav, wav_is_silent
from ..schemas import ChatRequest, SafetyContact, TranslateRequest, TTSRequest
from ..services import Services, answer_question

logger = logging.getLogger("kasa.routes.ai")
router = APIRouter()

TRANSLATION_FALLBACK_NOTICE = ("I couldn't translate this answer just now, so here it is in English. "
                               "Please try again in a moment for your language.")
TTS_NOTICE = "I couldn't prepare the voice this time, but you can read my answer."
NO_SPEECH_EN = "Sorry, I didn't catch that. Please try speaking again."


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_language(language: str) -> dict:
    if language not in LANG_CONFIG:
        raise AppError("unsupported_language", 400, log_detail=language)
    return LANG_CONFIG[language]


async def _read_audio(services: Services, audio: UploadFile) -> bytes:
    limit = services.settings.max_audio_bytes
    data = await audio.read(limit + 1)  # never read more than the limit into memory
    validate_wav(data, min_bytes=services.settings.min_audio_bytes, max_bytes=limit)
    return data


def _flag_if_needed(request: Request, services: Services, ans, *, language: str, session_id: str,
                    channel: str) -> None:
    """Sends the anonymous early-warning email for high-risk messages. It runs in
    the background so the reply isn't delayed."""
    if not (ans.safety.flagged and ans.safety.severity == "high"):
        return
    logger.warning("Safety flag category=%s channel=%s rid=%s", ans.safety.category, channel,
                   getattr(request.state, "request_id", "-"))
    asyncio.create_task(services.reporter.report_safety_flag(
        session_id=session_id, category=ans.safety.category, severity=ans.safety.severity,
        language=language, question_original=ans.question_original, question_english=ans.question_english,
        request_id=getattr(request.state, "request_id", "-"), channel=channel))


def _parse_history(raw: str) -> list:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


async def _transcribe(services: Services, data: bytes, language: str) -> str:
    if wav_is_silent(data):  # silent recording? no point paying for ASR
        return ""
    return await services.khaya.transcribe(data, language)


# --- ASR ---
@router.post("/asr", dependencies=[Depends(rate_limit("ai"))])
async def transcribe_audio(request: Request, audio: UploadFile = File(...), language: str = Form(...)):
    services = svc(request)
    cfg = _check_language(language)
    data = await _read_audio(services, audio)
    transcript = await _transcribe(services, data, language)
    if not transcript:
        raise AppError("no_speech", 422)
    return {"ok": True, "transcript": transcript, "language": language, "language_name": cfg["name"]}


# --- Translate ---
@router.post("/translate", dependencies=[Depends(rate_limit("ai"))])
async def translate_text(request: Request, req: TranslateRequest):
    if req.lang not in ALLOWED_PAIRS:
        raise AppError("unsupported_language", 400, log_detail=req.lang)
    translated = await svc(request).khaya.translate(req.text, req.lang)
    return {"ok": True, "translated": translated, "lang": req.lang}


# --- TTS ---
@router.post("/tts", dependencies=[Depends(rate_limit("ai"))])
async def text_to_speech(request: Request, req: TTSRequest):
    audio_b64, fmt = await svc(request).khaya.synthesize(req.text, req.language, req.speaker)
    return {"ok": True, "audio_base64": audio_b64, "format": fmt, "language": req.language,
            "tts_supported": True}


# --- Chat ---
@router.post("/chat", dependencies=[Depends(rate_limit("ai"))])
async def chat(request: Request, req: ChatRequest):
    services = svc(request)
    ans = await answer_question(services, text=req.message, language=req.language, history=req.history,
                                system_prompt=ASRH_PROMPT, max_tokens=400, for_speech=False)
    _flag_if_needed(request, services, ans, language=req.language, session_id=req.session_id or "", channel="chat")
    return {
        "safety": ans.safety.to_dict(),
        "ok": True,
        "question_original": ans.question_original,
        "question_translated": ans.question_english,
        "answer_english": ans.answer_english,
        "answer_local": ans.answer_local,
        "language": req.language,
        "language_name": LANG_CONFIG[req.language]["name"],
        "translation_fallback": ans.translation_fallback,
        "notice": TRANSLATION_FALLBACK_NOTICE if ans.translation_fallback else None,
        "tts_supported": not ans.translation_fallback,
        "disclaimer": DISCLAIMER,
        "timings_ms": ans.timings_ms,
    }


# --- Full pipeline (voice in → voice out) ---
@router.post("/full-pipeline", dependencies=[Depends(rate_limit("ai"))])
async def full_pipeline(request: Request, audio: UploadFile = File(...), language: str = Form(...),
                        speaker: str = Form(default=""), history: str = Form(default="[]"),
                        session_id: str = Form(default="", max_length=64)):
    services = svc(request)
    cfg = _check_language(language)
    data = await _read_audio(services, audio)
    transcript = await _transcribe(services, data, language)
    if not transcript:
        raise AppError("no_speech", 422)
    ans = await answer_question(services, text=transcript, language=language, history=_parse_history(history),
                                system_prompt=ASRH_PROMPT, max_tokens=400, for_speech=False)
    _flag_if_needed(request, services, ans, language=language, session_id=session_id, channel="voice-chat")
    audio_b64, fmt, notice = None, None, None
    if not ans.translation_fallback:
        try:
            audio_b64, fmt = await services.khaya.synthesize(ans.answer_local, language, speaker)
        except AppError as exc:
            logger.warning("full-pipeline TTS failed: %s", exc.log_detail)
            notice = TTS_NOTICE
    return {
        "ok": True, "safety": ans.safety.to_dict(), "transcript": transcript, "question_original": transcript,
        "question_translated": ans.question_english, "answer_english": ans.answer_english,
        "answer_local": ans.answer_local, "language": language, "language_name": cfg["name"],
        "audio_base64": audio_b64, "audio_format": fmt, "tts_supported": audio_b64 is not None,
        "translation_fallback": ans.translation_fallback,
        "notice": TRANSLATION_FALLBACK_NOTICE if ans.translation_fallback else notice,
        "disclaimer": DISCLAIMER,
    }


# --- Voice agent ---
async def _no_speech_prompt(services: Services, language: str, speaker: str) -> dict:
    """A spoken "I didn't catch that" in the user's language. It gets cached, so it
    costs one translation and one TTS per language for the life of the server."""
    text_local, audio = NO_SPEECH_EN, None
    try:
        text_local = await services.khaya.translate(NO_SPEECH_EN, LANG_CONFIG[language]["tr_from"])
        audio_b64, fmt = await services.khaya.synthesize(text_local, language, speaker)
        audio = {"text": text_local, "audio_base64": audio_b64, "format": fmt}
    except AppError as exc:
        logger.info("No-speech prompt degraded to text: %s", exc.log_detail)
    return {"message_english": NO_SPEECH_EN, "message_local": text_local,
            "audio_segments": [audio] if audio else []}


@router.post("/agent", dependencies=[Depends(rate_limit("ai"))])
async def voice_agent(request: Request, audio: UploadFile = File(...), language: str = Form(...),
                      speaker: str = Form(default=""), history: str = Form(default="[]"),
                      session_id: str = Form(default="", max_length=64)):
    services = svc(request)
    cfg = _check_language(language)
    started = time.perf_counter()

    data = await _read_audio(services, audio)
    t = time.perf_counter()
    transcript = await _transcribe(services, data, language)
    asr_ms = int((time.perf_counter() - t) * 1000)

    if not transcript:
        # Not an error, this happens in normal conversation. Reply kindly and carry on.
        return {"ok": True, "status": "no_speech", "language": language, "language_name": cfg["name"],
                **await _no_speech_prompt(services, language, speaker)}

    ans = await answer_question(services, text=transcript, language=language, history=_parse_history(history),
                                system_prompt=AGENT_PROMPT, max_tokens=150, for_speech=True)
    _flag_if_needed(request, services, ans, language=language, session_id=session_id, channel="voice-agent")

    segments, notice = [], None
    t = time.perf_counter()
    if ans.translation_fallback:
        notice = TRANSLATION_FALLBACK_NOTICE  # Khaya can't voice English, so no TTS when the translation failed
    else:
        try:
            segments = await services.khaya.synthesize_segments(ans.answer_local, language, speaker)
        except AppError as exc:
            logger.warning("Agent TTS failed: %s", exc.log_detail)
            notice = TTS_NOTICE
    tts_ms = int((time.perf_counter() - t) * 1000)

    timings = {"asr": asr_ms, **ans.timings_ms, "tts": tts_ms,
               "total": int((time.perf_counter() - started) * 1000)}
    logger.info("Agent turn lang=%s provider=%s timings=%s", language, ans.provider, timings)

    first = segments[0] if segments else None
    return {
        "ok": True, "status": "ok", "safety": ans.safety.to_dict(),
        "transcript": transcript,
        "question_english": ans.question_english,
        "answer_english": ans.answer_english,
        "answer_local": ans.answer_local,
        "language": language, "language_name": cfg["name"],
        "audio_segments": segments,
        # older frontends expect a single clip here
        "audio_base64": first["audio_base64"] if first and len(segments) == 1 else None,
        "audio_format": first["format"] if first else None,
        "translation_fallback": ans.translation_fallback,
        "notice": notice,
        "history_entry_user": {"role": "user", "content": ans.question_english},
        "history_entry_agent": {"role": "assistant", "content": ans.answer_english},
        "timings_ms": timings,
    }


# --- Safety: user-consented contact request ---
_PHONE_OK = re.compile(r"^\+?[\d\s()-]{7,20}$")


@router.post("/safety/contact", dependencies=[Depends(rate_limit("feedback"))])
async def safety_contact(request: Request, data: SafetyContact):
    if not data.consent:
        raise AppError("bad_request", 400, log_detail="no consent")
    if not _PHONE_OK.match(data.phone) or len(re.sub(r"\D", "", data.phone)) < 9:
        raise AppError("bad_phone", 400, log_detail="invalid phone")
    services = svc(request)
    record_id = await services.reporter.report_safety_contact(
        data.model_dump(), getattr(request.state, "request_id", "-"))
    logger.warning("Safety contact request saved id=%s category=%s", record_id, data.category)
    return {"ok": True, "id": record_id,
            "message": "Thank you for trusting us. Someone from the support team will reach out as soon as "
                       "possible. If you are in danger right now, please call 112."}
