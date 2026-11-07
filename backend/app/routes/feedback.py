"""Ratings, corrections and the survey. Saved first, synced in the background.
I keep personal details out of the logs on purpose."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from ..errors import AppError
from ..integrations import make_feedback_job, make_rating_job, make_survey_job
from ..khaya import LANG_CONFIG
from ..middleware import rate_limit
from ..safety import validate_wav
from ..schemas import RatingData, SurveyData
from ..services import Services
from ..storage import new_id, utcnow

logger = logging.getLogger("kasa.routes.feedback")
router = APIRouter()


def svc(request: Request) -> Services:
    return request.app.state.services


@router.post("/feedback/rating", dependencies=[Depends(rate_limit("feedback"))])
async def save_rating(request: Request, data: RatingData):
    services = svc(request)
    record = data.model_dump()
    record["id"] = new_id()
    record["timestamp"] = record.get("timestamp") or utcnow()
    record["tester_code"] = (record.get("tester_code") or "").upper().strip()
    await services.storage.add("rating", record)
    services.jobs.submit(f"rating:{record['id']}", make_rating_job(services.storage, services.forms, record))
    logger.info("Rating saved id=%s rating=%s lang=%s context=%s",
                record["id"], data.rating, data.language, data.context)
    return {"ok": True, "status": "saved", "id": record["id"], "rating": data.rating}


@router.post("/feedback/thumbs", dependencies=[Depends(rate_limit("feedback"))])
async def thumbs_feedback(
    request: Request,
    rating: str = Form(..., max_length=10),
    language: str = Form(..., max_length=10),
    question_original: str = Form(default="", max_length=3000),
    question_english: str = Form(default="", max_length=3000),
    answer_local: str = Form(default="", max_length=5000),
    answer_english: str = Form(default="", max_length=5000),
    correction_text: str = Form(default="", max_length=5000),
    reason: str = Form(default="", max_length=500),
    tester_code: str = Form(default="", max_length=40),
    session_id: str = Form(default="", max_length=64),
    audio: Optional[UploadFile] = File(default=None),
):
    services = svc(request)
    if rating not in {"up", "down"}:
        raise AppError("bad_request", 400, log_detail=f"rating={rating!r}")

    record = {
        "id": new_id(), "timestamp": utcnow(), "rating": rating, "language": language,
        "language_name": LANG_CONFIG.get(language, {}).get("name", language),
        "question_original": question_original, "question_english": question_english,
        "answer_local": answer_local, "answer_english": answer_english,
        "correction_text": correction_text, "reason": reason,
        "tester_code": tester_code.upper().strip(), "session_id": session_id,
        "audio_url": "", "metadata_url": "",
    }

    audio_path = None
    if audio is not None and rating == "down":
        data = await audio.read(services.settings.max_audio_bytes + 1)
        try:
            validate_wav(data, min_bytes=500, max_bytes=services.settings.max_audio_bytes)
            audio_dir = services.settings.data_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_path = audio_dir / f"{record['id']}.wav"
            await asyncio.to_thread(audio_path.write_bytes, data)
            record["audio_file"] = audio_path.name
        except AppError as exc:
            # Keep the written feedback even if the recording is no good.
            logger.info("Ignoring invalid correction audio id=%s: %s", record["id"], exc.log_detail)

    await services.storage.add("feedback", record)
    if rating == "down":
        services.jobs.submit(f"feedback:{record['id']}",
                             make_feedback_job(services.storage, services.hf, record, audio_path))
    logger.info("Feedback saved id=%s rating=%s lang=%s has_audio=%s",
                record["id"], rating, language, audio_path is not None)
    return {"ok": True, "status": "saved", "id": record["id"]}


@router.post("/survey", dependencies=[Depends(rate_limit("feedback"))])
async def save_survey(request: Request, data: SurveyData):
    services = svc(request)
    record = data.model_dump()
    record["id"] = new_id()
    record["timestamp"] = record.get("timestamp") or utcnow()
    await services.storage.add("survey", record)
    services.jobs.submit(f"survey:{record['id']}", make_survey_job(services.storage, services.forms, record))
    logger.info("Survey saved id=%s", record["id"])  # no personal details in the logs
    return {"ok": True, "status": "saved", "id": record["id"]}
