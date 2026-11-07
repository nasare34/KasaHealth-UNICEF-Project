"""Google Forms, Hugging Face, and the background queue that syncs to them.

I save to the database first, answer the user, then sync in the background,
so a slow Google Form never holds anyone up."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .config import Settings
from .errors import UpstreamError
from .http_client import send
from .resilience import retry_async
from .storage import Storage

logger = logging.getLogger("kasa.integrations")
LANG_NAMES = {"tw": "Twi", "dag": "Dagbani", "ee": "Ewe"}

SURVEY_ENTRIES = {
    "name": "entry.613146582", "phone": "entry.1273357101", "email": "entry.1778908494",
    "age": "entry.1268576577", "gender": "entry.1286595230", "location": "entry.1812798526",
    "ease": "entry.569805250", "helpful": "entry.1018906480", "feedback": "entry.1291914333",
    "recommend": "entry.990261598", "language": "entry.1640635370",
    "asr_transcript": "entry.399463896", "text_translation": "entry.572974212",
    "tts_quality": "entry.857550320", "is_tester": "entry.237791033", "tester_code": "entry.285528117",
}
RATINGS_ENTRIES = {
    "session_id": "entry.827562462", "language": "entry.1733085737", "question": "entry.1628278098",
    "answer": "entry.631432683", "rating": "entry.1027195700", "reason": "entry.1402075535",
    "timestamp": "entry.2121134314", "tester_code": "entry.1288031030",
}


# --- Background job queue ---
Job = Callable[[], Awaitable[None]]


class JobQueue:
    """A fixed number of workers pulling from a capped queue. If the queue is full
    I skip the job for now; the record stays "pending" and Retry failed syncs picks it up."""

    def __init__(self, workers: int = 4, max_size: int = 1000):
        self.queue: asyncio.Queue[tuple[str, Job]] = asyncio.Queue(maxsize=max_size)
        self.n_workers = workers
        self.tasks: list[asyncio.Task] = []
        self.processed = 0
        self.failed = 0
        self.dropped = 0

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._worker(i), name=f"job-worker-{i}")
                      for i in range(self.n_workers)]

    def submit(self, label: str, job: Job) -> bool:
        try:
            self.queue.put_nowait((label, job))
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning("Job queue full; deferred %s (will stay pending)", label)
            return False

    async def _worker(self, idx: int) -> None:
        while True:
            label, job = await self.queue.get()
            try:
                await job()
                self.processed += 1
            except Exception:  # one bad job shouldn't take the worker down with it
                self.failed += 1
                logger.exception("Background job %s failed", label)
            finally:
                self.queue.task_done()

    async def stop(self, drain_timeout: float = 10) -> None:
        try:
            await asyncio.wait_for(self.queue.join(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            logger.warning("Shutdown with %d jobs still queued (they remain pending in DB)",
                           self.queue.qsize())
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def stats(self) -> dict:
        return {"queued": self.queue.qsize(), "workers": self.n_workers, "processed": self.processed,
                "failed": self.failed, "dropped": self.dropped}


# --- Google Forms ---
class GoogleForms:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http

    async def _post(self, url: str, data: dict, label: str) -> None:
        async def call():
            await send(f"gform:{label}", self.http, "POST", url, data=data,
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       follow_redirects=True, timeout=15)
        await retry_async(call, attempts=3, label=f"gform:{label}")

    async def submit_survey(self, data: dict) -> None:
        if not self.s.survey_form_url:
            return
        form: dict[str, str] = {}
        for field, entry in SURVEY_ENTRIES.items():
            value = data.get(field)
            if field == "language":
                value = LANG_NAMES.get(value or "", value)
            value = (str(value).strip() if value is not None else "")
            if value:
                form[entry] = value
        # The form requires a tester code, so non-testers get "N/A".
        is_tester = (data.get("is_tester") or "").strip() == "Yes"
        form[SURVEY_ENTRIES["tester_code"]] = (data.get("tester_code") or "N/A") if is_tester else "N/A"
        await self._post(self.s.survey_form_url, form, "survey")

    async def submit_rating(self, data: dict) -> None:
        if not self.s.ratings_form_url:
            return
        question = f"[{data.get('context', '')}] {data.get('question_original') or ''}"
        form = {
            RATINGS_ENTRIES["session_id"]: data.get("session_id") or "",
            RATINGS_ENTRIES["language"]: LANG_NAMES.get(data.get("language", ""), data.get("language", "")),
            RATINGS_ENTRIES["question"]: question[:500],
            RATINGS_ENTRIES["answer"]: (data.get("answer_local") or "")[:500],
            RATINGS_ENTRIES["rating"]: "thumbs_up" if data.get("rating") == "up" else "thumbs_down",
            RATINGS_ENTRIES["reason"]: data.get("reason") or "",
            RATINGS_ENTRIES["timestamp"]: data.get("timestamp") or "",
            RATINGS_ENTRIES["tester_code"]: data.get("tester_code") or "N/A",
        }
        await self._post(self.s.ratings_form_url, form, "rating")


# --- Hugging Face dataset uploads ---
class HuggingFace:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http

    @property
    def enabled(self) -> bool:
        return bool(self.s.hf_token and self.s.hf_repo)

    async def upload(self, path_in_repo: str, content: bytes) -> str:
        """Upload through Hugging Face's preupload + commit API and return the file URL."""
        auth = {"Authorization": f"Bearer {self.s.hf_token}"}
        base = f"https://huggingface.co/api/datasets/{self.s.hf_repo}"

        async def call() -> str:
            pre = await send("hf", self.http, "POST", f"{base}/preupload/main",
                             headers={**auth, "Content-Type": "application/json"},
                             json={"files": [{"path": path_in_repo, "size": len(content),
                                              "sample": base64.b64encode(content[:512]).decode()}]},
                             timeout=30)
            info = (pre.json().get("files") or [{}])[0]
            if info.get("uploadMode") == "lfs":
                upload_url = info.get("uploadUrl")
                if upload_url:
                    await send("hf", self.http, "PUT", upload_url, content=content,
                               headers={"Content-Type": "application/octet-stream"}, timeout=120)
                sha = hashlib.sha256(content).hexdigest()
                op = {"key": path_in_repo, "type": "file",
                      "value": f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha}\nsize {len(content)}\n"}
            else:
                op = {"key": path_in_repo, "type": "file", "encoding": "base64",
                      "value": base64.b64encode(content).decode()}
            await send("hf", self.http, "POST", f"{base}/commit/main",
                       headers={**auth, "Content-Type": "application/json"},
                       json={"summary": f"Add {path_in_repo.rsplit('/', 1)[-1]}", "operations": [op]},
                       timeout=120)
            return f"https://huggingface.co/datasets/{self.s.hf_repo}/resolve/main/{path_in_repo}"

        return await retry_async(call, attempts=3, base_delay=1.0, label="hf")


# --- Sync jobs (run in background workers) ---
def make_rating_job(storage: Storage, forms: GoogleForms, record: dict) -> Job:
    async def job():
        try:
            await forms.submit_rating(record)
            await storage.mark_sync(record["id"], True)
        except UpstreamError as exc:
            await storage.mark_sync(record["id"], False, str(exc))
    return job


def make_survey_job(storage: Storage, forms: GoogleForms, record: dict) -> Job:
    async def job():
        try:
            await forms.submit_survey(record)
            await storage.mark_sync(record["id"], True)
        except UpstreamError as exc:
            await storage.mark_sync(record["id"], False, str(exc))
    return job


def make_feedback_job(storage: Storage, hf: HuggingFace, record: dict, audio_path: Path | None) -> Job:
    """I read the audio from disk (it was saved before the job was queued), so a
    failed upload can be retried later with its audio."""
    async def job():
        if not hf.enabled:
            await storage.mark_sync(record["id"], False, "HF_TOKEN not configured")
            return
        audio = None
        if audio_path and audio_path.exists():
            audio = await asyncio.to_thread(audio_path.read_bytes)
        stamp = record["timestamp"].replace(":", "").replace("-", "")[:15]
        stem = f"feedback/{stamp}_{record['id']}_{record.get('language', 'xx')}"
        updates: dict = {}
        try:
            if audio:
                updates["audio_url"] = await hf.upload(f"{stem}.wav", audio)
            metadata = {k: v for k, v in record.items() if k not in {"audio_url", "metadata_url"}}
            if audio:
                metadata["audio_file"] = f"{stem}.wav"
            updates["metadata_url"] = await hf.upload(
                f"{stem}.json", json.dumps(metadata, indent=2, ensure_ascii=False).encode())
            await storage.update_payload(record["id"], updates)
            await storage.mark_sync(record["id"], True)
        except UpstreamError as exc:
            if updates:
                await storage.update_payload(record["id"], updates)
            await storage.mark_sync(record["id"], False, str(exc))
    return job
