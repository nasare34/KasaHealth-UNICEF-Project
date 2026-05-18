"""
Kasa Health ASRH Assistant — FastAPI Backend v7
- Shorter LLM responses (max 120 tokens for chat, 80 for agent)
- Thumbs feedback endpoint → Hugging Face dataset upload
- Survey → Google Form
"""

import os, base64, json as json_lib, logging, uuid
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx, groq as groq_sdk
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kasa")

app = FastAPI(title="Kasa Health", version="7.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ── Keys & URLs ────────────────────────────────────────────────────────────────
KHAYA_ASR_KEY       = os.getenv("KHAYA_ASR_KEY", "")
KHAYA_TTS_KEY       = os.getenv("KHAYA_TTS_KEY", "")
KHAYA_TRANSLATE_KEY = os.getenv("KHAYA_TRANSLATE_KEY", "")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
HF_TOKEN            = os.getenv("HF_TOKEN", "")
HF_REPO             = "nasare34/kasa-health-feedback"

ASR_URL       = "https://translation-api.ghananlp.org/asr/v1/transcribe"
TTS_V2_URL    = "https://translation-api.ghananlp.org/tts/v2/synthesize"
TRANSLATE_URL = "https://translation-api.ghananlp.org/v2/translate"

# ── Language config ────────────────────────────────────────────────────────────
LANG_CONFIG = {
    "tw":  {"name":"Twi",     "asr":"tw",  "tr_to":"tw-en",  "tr_from":"en-tw",  "tts":"twi", "speaker":"female"},
    "dag": {"name":"Dagbani", "asr":"dag", "tr_to":"dag-en", "tr_from":"en-dag", "tts":"dag", "speaker":"female"},
    "ee":  {"name":"Ewe",     "asr":"ee",  "tr_to":"ee-en",  "tr_from":"en-ee",  "tts":"ewe", "speaker":"female"},
}

ASRH_PROMPT = """You are a compassionate, accurate Adolescent Sexual and Reproductive Health (ASRH) assistant for UNICEF Ghana, deployed as part of a testing platform called Kasa Health.

You answer questions from young people (ages 10-24) in Ghana about:
- Puberty and body changes
- Menstruation and menstrual health
- Contraception and family planning
- STIs/HIV prevention
- Pregnancy
- Consent, relationships, and gender-based violence
- Mental and emotional health
- Where to access health services in Ghana

STRICT RULES:
- Use simple, clear, non-judgmental language appropriate for young people in Ghana.
- Be culturally sensitive to Ghanaian norms and values.
- Keep answers 3-5 sentences. Be informative but concise.
- NEVER give personal medical diagnoses or treatment advice.
- ALWAYS remind users this is general information only and they should speak to a health worker for personal concerns.
- For support or urgent health concerns, ALWAYS refer ONLY to these Ghana-specific services:
    * SHEplus Ghana (reproductive health for young people): 055 054 5672 or 0800 00 11 22
    * SHEplus is a platform that engages, educates and informs young people about reproductive health and rights.
- NEVER mention any European, American, or non-Ghanaian hotlines or health services.
- NEVER invent or guess hotline numbers — only use the SHEplus numbers above.
- Answer in clear English only — your answer will be translated to the user's Ghanaian language."""

AGENT_PROMPT = """You are Kasa, a friendly Ghanaian health companion for young people, part of the Kasa Health testing platform by UNICEF Ghana.

This is a live voice conversation. Keep replies SHORT — 2 sentences maximum.
Topics: sexual health, puberty, contraception, STIs, relationships, consent, menstruation, pregnancy.
Be warm, encouraging, non-judgmental.
For support, refer ONLY to SHEplus Ghana: 055 054 5672 or 0800 00 11 22.
NEVER mention European or non-Ghanaian hotlines.
Answer in English only — will be translated and spoken aloud."""

# ── Models ─────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    language: str
    history: Optional[List] = []

class TTSRequest(BaseModel):
    text: str
    language: str
    speaker: Optional[str] = None

class TranslateRequest(BaseModel):
    text: str
    lang: str

class ThumbsFeedback(BaseModel):
    rating: str                          # "up" or "down"
    language: str
    question_original: str
    question_english: str
    answer_local: str
    answer_english: str
    correction_text: Optional[str] = None   # user's suggested correction (text)
    reason: Optional[str] = None            # why thumbs down
    session_id: Optional[str] = None
    timestamp: Optional[str] = None

class SurveyData(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    age: Optional[str] = None
    gender: Optional[str] = None
    location: Optional[str] = None
    ease: Optional[str] = None
    helpful: Optional[str] = None
    asr_accuracy: Optional[str] = None   # kept for backward compat
    asr_transcript: Optional[str] = None  # ASR & Transcript Quality
    text_translation: Optional[str] = None  # Text Translation accuracy
    tts_quality: Optional[str] = None    # TTS voice quality
    feedback: Optional[str] = None
    recommend: Optional[str] = None
    is_tester: Optional[str] = None      # Yes / No
    tester_code: Optional[str] = None    # tester code if applicable
    timestamp: Optional[str] = None
    language: Optional[str] = None
    sessionId: Optional[str] = None

# ── Helpers ────────────────────────────────────────────────────────────────────
async def khaya_translate(text: str, lang_pair: str) -> str:
    headers = {"Ocp-Apim-Subscription-Key": KHAYA_TRANSLATE_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(TRANSLATE_URL, headers=headers, json={"in": text, "lang": lang_pair})
        if r.status_code != 200:
            raise HTTPException(r.status_code, f"Translation error: {r.text}")
        result = r.json()
        if isinstance(result, str): return result.strip() or text
        if isinstance(result, dict):
            return result.get("translatedText") or result.get("out") or text
        return text

async def groq_chat(question_en: str, history: list, system_prompt: str, max_tokens: int = 120) -> str:
    client = groq_sdk.AsyncGroq(api_key=GROQ_API_KEY)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-6:]: messages.append(h)
    messages.append({"role": "user", "content": question_en})
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile", messages=messages,
        max_tokens=max_tokens, temperature=0.3)
    return resp.choices[0].message.content

async def do_tts(text: str, language: str, speaker: str = "female") -> tuple:
    cfg = LANG_CONFIG[language]
    headers = {"Ocp-Apim-Subscription-Key": KHAYA_TTS_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(TTS_V2_URL, headers=headers,
                              json={"text": text, "language": cfg["tts"], "speaker": speaker or cfg["speaker"]})
        if r.status_code != 200:
            raise HTTPException(r.status_code, f"TTS error: {r.text}")
        ct = r.headers.get("content-type", "")
        if "audio" in ct:
            return base64.b64encode(r.content).decode(), "mp3" if "mpeg" in ct else "wav"
        result = r.json()
        return result.get("audio") or result.get("audioContent") or "", result.get("format", "wav")

async def upload_to_huggingface(filename: str, content: bytes, content_type: str) -> str:
    """
    Upload a file to HF dataset repo using preupload + commit API.
    Handles both 'regular' (base64 in commit) and 'lfs' (PUT to uploadUrl then commit) modes.
    The commit payload key is 'summary' not 'commit_message'.
    """
    if not HF_TOKEN:
        logger.warning("HF_TOKEN not set — skipping HF upload")
        return ""

    import base64 as b64_mod
    import hashlib

    auth_headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    base_url     = f"https://huggingface.co/api/datasets/{HF_REPO}"

    try:
        async with httpx.AsyncClient(timeout=120) as client:

            # ── Step 1: Preupload — find out upload mode ──────────────────────
            pre_r = await client.post(
                f"{base_url}/preupload/main",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"files": [{
                    "path":   filename,
                    "size":   len(content),
                    "sample": b64_mod.b64encode(content[:512]).decode(),
                }]}
            )
            logger.info(f"HF preupload: {pre_r.status_code} {pre_r.text[:300]}")
            if pre_r.status_code != 200:
                logger.warning(f"HF preupload failed: {pre_r.status_code} {pre_r.text[:300]}")
                return ""

            file_info   = pre_r.json().get("files", [{}])[0]
            upload_mode = file_info.get("uploadMode", "regular")
            logger.info(f"HF upload mode for {filename}: {upload_mode}")

            # ── Step 2: Build the commit operation based on mode ──────────────
            if upload_mode == "lfs":
                # For LFS: upload the raw bytes to the provided URL first,
                # then commit with a pointer (sha256 + size)
                upload_url = file_info.get("uploadUrl", "")
                if upload_url:
                    lfs_r = await client.put(
                        upload_url, content=content,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    logger.info(f"HF LFS PUT: {lfs_r.status_code} {lfs_r.text[:200]}")

                sha256 = hashlib.sha256(content).hexdigest()
                operation = {
                    "key":  filename,
                    "type": "file",
                    "value": (
                        f"version https://git-lfs.github.com/spec/v1\n"
                        f"oid sha256:{sha256}\n"
                        f"size {len(content)}\n"
                    ),
                    # no encoding field for LFS pointer
                }
            else:
                # Regular: embed file as base64 directly in the commit
                operation = {
                    "key":      filename,
                    "type":     "file",
                    "encoding": "base64",
                    "value":    b64_mod.b64encode(content).decode(),
                }

            # ── Step 3: Commit ────────────────────────────────────────────────
            commit_r = await client.post(
                f"{base_url}/commit/main",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={
                    "summary":    f"Add {filename.split('/')[-1]}",   # ← 'summary' not 'commit_message'
                    "operations": [operation],
                }
            )
            logger.info(f"HF commit: {commit_r.status_code} {commit_r.text[:400]}")

            if commit_r.status_code in (200, 201):
                file_url = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/{filename}"
                logger.info(f"HF upload OK: {file_url}")
                return file_url

            logger.warning(f"HF commit failed: {commit_r.status_code} {commit_r.text[:400]}")
            return ""

    except Exception as e:
        logger.warning(f"HF upload exception: {e}")
        return ""

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "7.0.0", "languages": list(LANG_CONFIG.keys())}

@app.get("/languages")
async def get_languages():
    return {"languages": LANG_CONFIG}

# ── Debug ──────────────────────────────────────────────────────────────────────
@app.get("/debug/asr-languages")
async def debug_asr():
    # ASR v1 has no /languages endpoint — use v3 to list supported languages
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://translation-api.ghananlp.org/asr/v3/languages",
                        headers={"Ocp-Apim-Subscription-Key": KHAYA_ASR_KEY})
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:500]}

@app.get("/debug/tts-speakers")
async def debug_tts():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://translation-api.ghananlp.org/tts/v2/speakers",
                        headers={"Ocp-Apim-Subscription-Key": KHAYA_TTS_KEY})
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}

@app.get("/debug/tts-v3-speakers")
async def debug_tts_v3():
    """Check what TTS v3 offers — speakers, languages, voices"""
    results = {}
    async with httpx.AsyncClient(timeout=15) as c:
        for path in ["speakers", "languages", "voices"]:
            r = await c.get(f"https://translation-api.ghananlp.org/tts/v3/{path}",
                            headers={"Ocp-Apim-Subscription-Key": KHAYA_TTS_KEY})
            results[path] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:300]}
    return results

@app.get("/debug/tts-v3-test")
async def debug_tts_v3_test(text: str = "Medaase", language: str = "twi", speaker: str = "female"):
    """Test TTS v3 with a given text, language and speaker"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://translation-api.ghananlp.org/tts/v3/synthesize",
            headers={"Ocp-Apim-Subscription-Key": KHAYA_TTS_KEY, "Content-Type": "application/json"},
            json={"text": text, "language": language, "speaker": speaker}
        )
    return {
        "status": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "body_snippet": r.text[:300] if r.status_code != 200 else "audio returned",
        "audio_size": len(r.content) if r.status_code == 200 else 0,
    }

@app.get("/debug/translate-languages")
async def debug_translate():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://translation-api.ghananlp.org/v2/languages",
                        headers={"Ocp-Apim-Subscription-Key": KHAYA_TRANSLATE_KEY})
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}

@app.get("/debug/hf-upload-test")
async def debug_hf_upload():
    """
    Test HF upload. Open http://localhost:8000/debug/hf-upload-test
    Then check https://huggingface.co/datasets/nasare34/kasa-health-feedback
    """
    if not HF_TOKEN:
        return {"error": "HF_TOKEN not set in .env — add HF_TOKEN=hf_xxx to your .env file"}
    test_content = json_lib.dumps({
        "test": True,
        "message": "Kasa Health HF upload test",
        "timestamp": datetime.utcnow().isoformat(),
        "repo": HF_REPO,
    }, indent=2).encode()
    file_url = await upload_to_huggingface(
        f"test/upload_test_{datetime.utcnow().strftime(chr(37)+'Y%m%d_%H%M%S')}.json",
        test_content,
        "application/json"
    )
    return {
        "hf_token_set": bool(HF_TOKEN),
        "hf_repo":      HF_REPO,
        "uploaded_url": file_url,
        "success":      bool(file_url),
        "check_at":     f"https://huggingface.co/datasets/{HF_REPO}/tree/main/test",
    }


@app.get("/debug/hf-retry-failed")
async def hf_retry_failed():
    """
    Retry uploading any feedback that failed to upload to HF (audio_url is empty).
    Open http://localhost:8000/debug/hf-retry-failed to re-attempt all failed uploads.
    """
    fb_file = Path("feedback.json")
    if not fb_file.exists():
        return {"message": "No feedback.json found"}
    feedbacks = json_lib.loads(fb_file.read_text())
    retried, succeeded = 0, 0
    for record in feedbacks:
        if record.get("rating") == "down" and not record.get("audio_url"):
            # Re-upload metadata at minimum
            retried += 1
            metadata = {k: record[k] for k in record if k != "audio_url"}
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            uid = record.get("id", str(uuid.uuid4())[:8])
            lang = record.get("language", "unknown")
            meta_filename = f"feedback/{ts}_{uid}_{lang}_retry.json"
            url = await upload_to_huggingface(
                meta_filename,
                json_lib.dumps(metadata, indent=2, ensure_ascii=False).encode(),
                "application/json"
            )
            if url:
                record["metadata_url"] = url
                succeeded += 1
    fb_file.write_text(json_lib.dumps(feedbacks, indent=2, ensure_ascii=False))
    return {"retried": retried, "succeeded": succeeded, "total_feedback": len(feedbacks)}


@app.get("/debug/hf-verbose")
async def debug_hf_verbose():
    """Shows full HF API response for debugging upload issues."""
    if not HF_TOKEN:
        return {"error": "HF_TOKEN not set"}

    import base64 as b64_mod
    test_content = b"Hello from Kasa Health"
    auth_headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    results = {}

    async with httpx.AsyncClient(timeout=30) as client:

        # Test 1: Check token is valid
        me = await client.get("https://huggingface.co/api/whoami", headers=auth_headers)
        results["1_whoami"] = {"status": me.status_code, "body": me.json() if me.status_code==200 else me.text}

        # Test 2: Check repo exists and is accessible
        repo = await client.get(f"https://huggingface.co/api/datasets/{HF_REPO}", headers=auth_headers)
        results["2_repo_info"] = {"status": repo.status_code, "body": repo.text[:300]}

        # Test 3: Try preupload
        pre = await client.post(
            f"https://huggingface.co/api/datasets/{HF_REPO}/preupload/main",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"files": [{"path": "test/debug.txt", "size": len(test_content),
                             "sample": b64_mod.b64encode(test_content).decode()}]}
        )
        results["3_preupload"] = {"status": pre.status_code, "body": pre.text[:500]}

        # Test 4: Try direct commit
        commit = await client.post(
            f"https://huggingface.co/api/datasets/{HF_REPO}/commit/main",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={
                "summary": "debug test",
                "operations": [{
                    "key": "test/debug_verbose.txt",
                    "type": "file",
                    "encoding": "base64",
                    "value": b64_mod.b64encode(test_content).decode(),
                }]
            }
        )
        results["4_commit"] = {"status": commit.status_code, "body": commit.text[:500]}

    return results


@app.get("/debug/translate-quick")
async def debug_translate_quick(text: str = "Hello", lang: str = "en-tw"):
    result = await khaya_translate(text, lang)
    return {"input": text, "lang": lang, "output": result}

# ── ASR ────────────────────────────────────────────────────────────────────────
@app.post("/asr")
async def transcribe_audio(audio: UploadFile = File(...), language: str = Form(...)):
    if language not in LANG_CONFIG:
        raise HTTPException(400, f"Unsupported language: {language}")
    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(400, "Audio too short — please hold and speak clearly.")
    asr_code = LANG_CONFIG[language]["asr"]
    logger.info(f"ASR: lang={asr_code} size={len(audio_bytes)}B")
    headers = {"Ocp-Apim-Subscription-Key": KHAYA_ASR_KEY, "Content-Type": "audio/wav"}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(ASR_URL, params={"language": asr_code}, headers=headers, content=audio_bytes)
        logger.info(f"ASR response: {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            raise HTTPException(r.status_code, f"ASR error: {r.text}")
        result = r.json()
    transcript = result.strip() if isinstance(result, str) else (
        result.get("transcription") or result.get("text") or result.get("transcript") or "")
    return {"transcript": transcript, "language": language, "language_name": LANG_CONFIG[language]["name"]}

# ── TRANSLATE ──────────────────────────────────────────────────────────────────
@app.post("/translate")
async def translate_text(req: TranslateRequest):
    translated = await khaya_translate(req.text, req.lang)
    return {"translated": translated, "lang": req.lang}

# ── TTS ────────────────────────────────────────────────────────────────────────
@app.post("/tts")
async def text_to_speech(req: TTSRequest):
    if req.language not in LANG_CONFIG:
        raise HTTPException(400, f"Unsupported language: {req.language}")
    audio_b64, fmt = await do_tts(req.text, req.language, req.speaker or "female")
    return {"audio_base64": audio_b64, "format": fmt, "language": req.language, "tts_supported": True}

# ── CHAT ───────────────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    if req.language not in LANG_CONFIG:
        raise HTTPException(400, f"Unsupported language: {req.language}")
    cfg = LANG_CONFIG[req.language]
    try:    question_en = await khaya_translate(req.message, cfg["tr_to"])
    except: question_en = req.message
    try:    answer_en = await groq_chat(question_en, req.history, ASRH_PROMPT, max_tokens=400)
    except Exception as e: raise HTTPException(500, f"LLM error: {e}")
    try:    answer_local = await khaya_translate(answer_en, cfg["tr_from"])
    except: answer_local = answer_en
    return {
        "question_original":   req.message,
        "question_translated": question_en,
        "answer_english":      answer_en,
        "answer_local":        answer_local,
        "language":            req.language,
        "language_name":       cfg["name"],
        "tts_supported":       True,
        "disclaimer":          "⚠️ This is general information only. For personal, serious or urgent concerns, please speak to a health worker or contact SHEplus Ghana: 055 054 5672 / 0800 00 11 22",
    }

# ── FULL PIPELINE ──────────────────────────────────────────────────────────────
@app.post("/full-pipeline")
async def full_pipeline(audio: UploadFile = File(...), language: str = Form(...),
                        speaker: str = Form(default="female"), history: str = Form(default="[]")):
    hist = json_lib.loads(history)
    asr  = await transcribe_audio(audio=audio, language=language)
    if not asr["transcript"].strip():
        raise HTTPException(400, "Could not transcribe audio. Please try again.")
    chat_req    = ChatRequest(message=asr["transcript"], language=language, history=hist)
    chat_result = await chat(chat_req)
    audio_b64, fmt = await do_tts(chat_result["answer_local"], language, speaker)
    return {**chat_result, "transcript": asr["transcript"],
            "audio_base64": audio_b64, "audio_format": fmt, "tts_supported": True}

# ── VOICE AGENT ────────────────────────────────────────────────────────────────
@app.post("/agent")
async def voice_agent(audio: UploadFile = File(...), language: str = Form(...),
                      speaker: str = Form(default="female"), history: str = Form(default="[]")):
    hist = json_lib.loads(history)
    asr_result = await transcribe_audio(audio=audio, language=language)
    transcript = asr_result["transcript"]
    if not transcript.strip():
        raise HTTPException(400, "Could not hear you clearly. Please try again.")
    cfg = LANG_CONFIG[language]
    try:    question_en = await khaya_translate(transcript, cfg["tr_to"])
    except: question_en = transcript
    try:    answer_en = await groq_chat(question_en, hist, AGENT_PROMPT, max_tokens=150)
    except Exception as e: raise HTTPException(500, f"Agent error: {e}")
    try:    answer_local = await khaya_translate(answer_en, cfg["tr_from"])
    except: answer_local = answer_en
    audio_b64, fmt = await do_tts(answer_local, language, speaker)
    return {
        "transcript":          transcript,
        "question_english":    question_en,
        "answer_english":      answer_en,
        "answer_local":        answer_local,
        "language":            language,
        "language_name":       cfg["name"],
        "audio_base64":        audio_b64,
        "audio_format":        fmt,
        "history_entry_user":  {"role": "user",      "content": question_en},
        "history_entry_agent": {"role": "assistant", "content": answer_en},
    }

# ── RATINGS — thumbs up AND down (all contexts) ───────────────────────────────
# Google Form for ratings — create a form with these fields and paste entry IDs:
#   Session ID, Language, Context, Question, Answer, Rating, Reason, Timestamp
# Paste your ratings form prefill URL to get the entry IDs
# Ratings Google Form — confirmed entry IDs from prefill URL
RATINGS_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfE_u5-jFamI8F7-O-rX4wWZAJj6zDSQPvUiExhCuWwpJLFzg/formResponse"
RATINGS_ENTRIES = {
    "session_id": "entry.827562462",
    "language":   "entry.1733085737",
    "question":   "entry.1628278098",   # includes context prefix e.g. "[chat] What is puberty?"
    "answer":     "entry.631432683",
    "rating":     "entry.1027195700",
    "reason":     "entry.1402075535",
    "timestamp":  "entry.2121134314",
}

class RatingData(BaseModel):
    rating:            str              # "up" or "down"
    language:          str
    context:           str              # "chat" | "agent" | "transcribe"
    question_original: Optional[str] = ""
    question_english:  Optional[str] = ""
    answer_local:      Optional[str] = ""
    answer_english:    Optional[str] = ""
    reason:            Optional[str] = ""
    session_id:        Optional[str] = ""
    timestamp:         Optional[str] = ""

@app.post("/feedback/rating")
async def save_rating(data: RatingData):
    """
    Save thumbs up/down rating.
    - All ratings saved locally to ratings.json
    - Also posted to Google Form (set RATINGS_FORM_URL in .env)
    """
    if not data.timestamp:
        data.timestamp = datetime.utcnow().isoformat()

    record = data.model_dump()
    record["id"] = str(uuid.uuid4())[:8]

    # Save locally
    ratings_file = Path("ratings.json")
    ratings = []
    if ratings_file.exists():
        try: ratings = json_lib.loads(ratings_file.read_text())
        except: ratings = []
    ratings.append(record)
    ratings_file.write_text(json_lib.dumps(ratings, indent=2, ensure_ascii=False))

    # Post to Google Form if configured
    if RATINGS_FORM_URL:
        # Fold context into question field since form has no context field
        question_with_ctx = f"[{data.context}] {data.question_original or ''}"
        form_data = {
            RATINGS_ENTRIES["session_id"]: data.session_id or "",
            RATINGS_ENTRIES["language"]:   LANG_MAP.get(data.language, data.language),
            RATINGS_ENTRIES["question"]:   question_with_ctx[:500],
            RATINGS_ENTRIES["answer"]:     (data.answer_local or "")[:500],
            RATINGS_ENTRIES["rating"]:     "thumbs_up" if data.rating == "up" else "thumbs_down",
            RATINGS_ENTRIES["reason"]:     data.reason or "",
            RATINGS_ENTRIES["timestamp"]:  data.timestamp,
        }
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.post(
                    RATINGS_FORM_URL, data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                logger.info(f"Ratings form: {r.status_code} rating={data.rating} context={data.context}")
        except Exception as e:
            logger.warning(f"Ratings form failed: {e}")

    logger.info(f"Rating saved: {data.rating} lang={data.language} context={data.context} id={record['id']}")
    return {"status": "saved", "id": record["id"], "rating": data.rating}


@app.get("/feedback/ratings/test")
async def test_ratings_form():
    """Test the ratings Google Form submission. Check your Google Sheet after calling this."""
    test_data = {
        RATINGS_ENTRIES["session_id"]: "test_session_001",
        RATINGS_ENTRIES["language"]:   "Twi",
        RATINGS_ENTRIES["question"]:   "[chat] What is puberty?",
        RATINGS_ENTRIES["answer"]:     "Puberty is when your body changes from a child to an adult.",
        RATINGS_ENTRIES["rating"]:     "thumbs_up",
        RATINGS_ENTRIES["reason"]:     "",
        RATINGS_ENTRIES["timestamp"]:  datetime.utcnow().isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(RATINGS_FORM_URL, data=test_data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
            return {"status": r.status_code, "success": r.status_code in (200,302),
                    "form_url": RATINGS_FORM_URL}
    except Exception as e:
        return {"error": str(e)}


@app.get("/feedback/ratings/export")
async def export_ratings():
    from fastapi.responses import FileResponse
    ratings_file = Path("ratings.json")
    if not ratings_file.exists():
        return {"ratings": []}
    return FileResponse(ratings_file, media_type="application/json", filename="kasa_ratings.json")


# ── THUMBS FEEDBACK + HUGGING FACE UPLOAD ─────────────────────────────────────
@app.post("/feedback/thumbs")
async def thumbs_feedback(
    rating: str = Form(...),
    language: str = Form(...),
    question_original: str = Form(...),
    question_english: str = Form(...),
    answer_local: str = Form(...),
    answer_english: str = Form(...),
    correction_text: str = Form(default=""),
    reason: str = Form(default=""),
    session_id: str = Form(default=""),
    audio: Optional[UploadFile] = File(default=None),
):
    """
    Receives thumbs up/down feedback.
    On thumbs down with correction audio → uploads audio + metadata to Hugging Face.
    All feedback saved locally to feedback.json.
    """
    ts        = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    lang_name = LANG_CONFIG.get(language, {}).get("name", language)

    record = {
        "id":                unique_id,
        "timestamp":         datetime.utcnow().isoformat(),
        "rating":            rating,
        "language":          language,
        "language_name":     lang_name,
        "question_original": question_original,
        "question_english":  question_english,
        "answer_local":      answer_local,
        "answer_english":    answer_english,
        "correction_text":   correction_text,
        "reason":            reason,
        "session_id":        session_id,
        "audio_url":         "",
        "metadata_url":      "",
    }

    # Upload to Hugging Face if thumbs down and audio provided
    if rating == "down" and audio and HF_TOKEN:
        try:
            audio_bytes = await audio.read()
            if len(audio_bytes) > 500:
                # Upload audio file
                audio_filename = f"feedback/{ts}_{unique_id}_{language}.wav"
                audio_url = await upload_to_huggingface(
                    audio_filename, audio_bytes, "audio/wav")
                record["audio_url"] = audio_url

                # Upload metadata JSON alongside the audio
                metadata = {
                    "id":                unique_id,
                    "timestamp":         record["timestamp"],
                    "language":          language,
                    "language_name":     lang_name,
                    "question_original": question_original,
                    "question_english":  question_english,
                    "system_answer_local":   answer_local,
                    "system_answer_english": answer_english,
                    "correction_text":   correction_text,
                    "reason":            reason,
                    "audio_file":        audio_filename,
                    "session_id":        session_id,
                }
                meta_filename = f"feedback/{ts}_{unique_id}_{language}.json"
                meta_url = await upload_to_huggingface(
                    meta_filename,
                    json_lib.dumps(metadata, indent=2, ensure_ascii=False).encode(),
                    "application/json")
                record["metadata_url"] = meta_url

        except Exception as e:
            logger.warning(f"HF upload error: {e}")

    # Save locally as backup
    fb_file = Path("feedback.json")
    feedbacks = []
    if fb_file.exists():
        try: feedbacks = json_lib.loads(fb_file.read_text())
        except: feedbacks = []
    feedbacks.append(record)
    fb_file.write_text(json_lib.dumps(feedbacks, indent=2, ensure_ascii=False))

    logger.info(f"Feedback saved: rating={rating} lang={language} id={unique_id} audio_url={record['audio_url']}")
    return {"status": "saved", "id": unique_id, "audio_url": record["audio_url"]}


@app.get("/feedback/export")
async def export_feedback():
    from fastapi.responses import FileResponse
    fb_file = Path("feedback.json")
    if not fb_file.exists():
        return {"feedback": []}
    return FileResponse(fb_file, media_type="application/json", filename="kasa_feedback.json")

# ── SURVEY ─────────────────────────────────────────────────────────────────────
GOOGLE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdXvHEUvMM98R_dwNSwXdMxYlEJtmKcYkEimg4oGkZZyGu7wA/formResponse"
LANG_MAP = {"tw": "Twi", "dag": "Dagbani", "ee": "Ewe"}

@app.post("/survey")
async def save_survey(data: SurveyData):
    survey_file = Path("surveys.json")
    surveys = []
    if survey_file.exists():
        try: surveys = json_lib.loads(survey_file.read_text())
        except: surveys = []
    surveys.append(data.model_dump())
    survey_file.write_text(json_lib.dumps(surveys, indent=2, ensure_ascii=False))

    form_data = {
        "entry.613146582":  data.name or "",
        "entry.1273357101": data.phone or "",
        "entry.1778908494": data.email or "",
        "entry.1268576577": data.age or "",
        "entry.1286595230": data.gender or "",
        "entry.1812798526": data.location or "",
        "entry.569805250":  data.ease or "",
        "entry.1018906480": data.helpful or "",
        "entry.1291914333": data.feedback or "",
        "entry.990261598":  data.recommend or "",
        "entry.1640635370": LANG_MAP.get(data.language, data.language or ""),
        "entry.399463896":  data.asr_transcript or "",
        "entry.572974212":  data.text_translation or "",
        "entry.857550320":  data.tts_quality or "",
        "entry.237791033":  data.is_tester or "",
        "entry.285528117":  data.tester_code or "",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(GOOGLE_FORM_URL, data=form_data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
            logger.info(f"Google Form: {r.status_code}")
    except Exception as e:
        logger.warning(f"Google Form failed: {e}")

    return {"status": "saved", "total": len(surveys)}

@app.get("/survey/test")
async def test_survey():
    test_data = {
        "entry.613146582": "Test User", "entry.1273357101": "0200000000",
        "entry.1778908494": "test@test.com", "entry.1268576577": "19",
        "entry.1286595230": "Male", "entry.1812798526": "Ashanti Region",
        "entry.569805250": "Very Easy", "entry.1018906480": "Very helpful",
        "entry.333168962": "Excellent", "entry.1291914333": "Test submission",
        "entry.990261598": "Yes", "entry.1640635370": "Twi",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(GOOGLE_FORM_URL, data=test_data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
            return {"status": r.status_code, "success": r.status_code in (200, 302),
                    "final_url": str(r.url), "body_snippet": r.text[:200]}
    except Exception as e:
        return {"error": str(e)}

@app.get("/survey/export")
async def export_surveys():
    from fastapi.responses import FileResponse
    survey_file = Path("surveys.json")
    if not survey_file.exists():
        return {"surveys": []}
    return FileResponse(survey_file, media_type="application/json", filename="kasa_surveys.json")
