"""SQLite storage for ratings, feedback, surveys, incidents and safety reports.

WAL mode lets several workers write at once without stepping on each other.
If you outgrow one server, this is the file to swap for Postgres."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("kasa.storage")

KINDS = {"rating", "feedback", "survey", "incident", "safety"}
LEGACY_FILES = {"rating": "ratings.json", "feedback": "feedback.json", "survey": "surveys.json"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | skipped
    sync_error  TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_records_sync ON records(sync_status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS alert_state (
    code       TEXT PRIMARY KEY,
    last_sent  REAL NOT NULL DEFAULT 0,
    suppressed INTEGER NOT NULL DEFAULT 0
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "kasa.db"

    @contextmanager
    def _connect(self):
        """Opens a short-lived connection and always closes it. If the database file
        has gone missing I recreate the tables rather than fail every request."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        existed = self.db_path.exists() and self.db_path.stat().st_size > 0
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA synchronous=NORMAL")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone():
                if existed:
                    logger.warning("Database schema missing at %s — recreating", self.db_path)
                conn.executescript(_SCHEMA)
            yield conn
        finally:
            conn.close()

    # --- lifecycle ---
    def init_sync(self, legacy_dirs: list[Path]) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            done = conn.execute("SELECT value FROM meta WHERE key='legacy_import'").fetchone()
            if done:
                conn.execute("COMMIT")
                return
            imported = 0
            for kind, filename in LEGACY_FILES.items():
                for folder in legacy_dirs:
                    path = Path(folder) / filename
                    if not path.exists():
                        continue
                    try:
                        rows = json.loads(path.read_text(encoding="utf-8"))
                    except (ValueError, OSError) as exc:
                        logger.warning("Skipping unreadable legacy file %s: %s", path, exc)
                        continue
                    for row in rows if isinstance(rows, list) else []:
                        if not isinstance(row, dict):
                            continue
                        conn.execute(
                            "INSERT OR IGNORE INTO records(id, kind, created_at, payload, sync_status) "
                            "VALUES (?,?,?,?, 'skipped')",
                            (str(row.get("id") or new_id()), kind,
                             str(row.get("timestamp") or utcnow()), json.dumps(row, ensure_ascii=False)))
                        imported += 1
                    break  # first folder that has the file wins
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_import', ?)", (utcnow(),))
            conn.execute("COMMIT")
            if imported:
                logger.info("Imported %d legacy records from JSON files", imported)

    async def init(self, legacy_dirs: list[Path]) -> None:
        await asyncio.to_thread(self.init_sync, legacy_dirs)

    # --- writes ---
    async def add(self, kind: str, payload: dict, *, sync_status: str = "pending") -> str:
        assert kind in KINDS, kind
        record_id = str(payload.get("id") or new_id())
        payload = {**payload, "id": record_id}

        def _write():
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO records(id, kind, created_at, payload, sync_status) VALUES (?,?,?,?,?)",
                    (record_id, kind, payload.get("timestamp") or utcnow(),
                     json.dumps(payload, ensure_ascii=False), sync_status))
        await asyncio.to_thread(_write)
        return record_id

    async def update_payload(self, record_id: str, updates: dict) -> None:
        def _write():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")  # lock it so two updates can't interleave
                try:
                    row = conn.execute("SELECT payload FROM records WHERE id=?", (record_id,)).fetchone()
                    if row:
                        payload = {**json.loads(row[0]), **updates}
                        conn.execute("UPDATE records SET payload=? WHERE id=?",
                                     (json.dumps(payload, ensure_ascii=False), record_id))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        await asyncio.to_thread(_write)

    async def mark_sync(self, record_id: str, ok: bool, error: str = "") -> None:
        def _write():
            with self._connect() as conn:
                conn.execute(
                    "UPDATE records SET sync_status=?, sync_error=?, attempts=attempts+1 WHERE id=?",
                    ("done" if ok else "failed", error[:500] or None, record_id))
        await asyncio.to_thread(_write)

    # --- reads ---
    async def export(self, kind: str) -> list[dict]:
        def _read():
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT payload, sync_status FROM records WHERE kind=? ORDER BY created_at", (kind,)
                ).fetchall()
            out = []
            for payload, status in rows:
                item = json.loads(payload)
                item["_sync_status"] = status
                out.append(item)
            return out
        return await asyncio.to_thread(_read)

    async def failed(self, kind: str, limit: int = 100,
                     statuses: tuple[str, ...] = ("failed", "pending")) -> list[dict]:
        marks = ",".join("?" * len(statuses))

        def _read():
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT payload FROM records WHERE kind=? AND sync_status IN ({marks}) "
                    "ORDER BY created_at LIMIT ?", (kind, *statuses, limit)).fetchall()
            return [json.loads(r[0]) for r in rows]
        return await asyncio.to_thread(_read)

    async def counts(self) -> dict:
        def _read():
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT kind, sync_status, COUNT(*) FROM records GROUP BY kind, sync_status").fetchall()
            summary: dict = {}
            for kind, status, n in rows:
                summary.setdefault(kind, {})[status] = n
            return summary
        return await asyncio.to_thread(_read)

    # --- shared settings (visible to every worker process) ---
    async def get_meta(self, key: str, default=None):
        def _read():
            with self._connect() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        try:
            return await asyncio.to_thread(_read)
        except (sqlite3.Error, ValueError):
            return default

    async def set_meta(self, key: str, value) -> None:
        def _write():
            with self._connect() as conn:
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                             (key, json.dumps(value)))
        await asyncio.to_thread(_write)

    async def claim_alert(self, code: str, window_s: float) -> tuple[bool, int]:
        """Works across workers. Returns (send_now, repeats_since_last_email).
        If we already emailed about this recently, I just count it and mention
        the count in the next email."""
        import time as _time

        def _claim():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    now = _time.time()
                    row = conn.execute("SELECT last_sent, suppressed FROM alert_state WHERE code=?",
                                       (code,)).fetchone()
                    if row and now - row[0] < window_s:
                        conn.execute("UPDATE alert_state SET suppressed=suppressed+1 WHERE code=?", (code,))
                        conn.execute("COMMIT")
                        return False, row[1] + 1
                    suppressed = row[1] if row else 0
                    conn.execute("INSERT OR REPLACE INTO alert_state(code, last_sent, suppressed) VALUES (?,?,0)",
                                 (code, now))
                    conn.execute("COMMIT")
                    return True, suppressed
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        return await asyncio.to_thread(_claim)

    async def overview(self) -> dict:
        """Numbers for the admin dashboard. All one connection, so it's cheap."""
        import time as _time
        day_ago = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(_time.time() - 86400))
        week_ago = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(_time.time() - 7 * 86400))

        def _read():
            with self._connect() as conn:
                q = conn.execute
                totals = dict(q("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
                ratings = q("""SELECT json_extract(payload,'$.language'), json_extract(payload,'$.rating'), COUNT(*)
                               FROM records WHERE kind='rating' GROUP BY 1, 2""").fetchall()
                incidents = q("""SELECT json_extract(payload,'$.code'), COUNT(*) FROM records
                                 WHERE kind='incident' AND created_at >= ? GROUP BY 1 ORDER BY 2 DESC""",
                              (day_ago,)).fetchall()
                safety = q("""SELECT json_extract(payload,'$.type'), COUNT(*) FROM records
                              WHERE kind='safety' AND created_at >= ? GROUP BY 1""", (week_ago,)).fetchall()
                sync = q("""SELECT kind, sync_status, COUNT(*) FROM records
                            WHERE kind IN ('rating','feedback','survey') GROUP BY 1, 2""").fetchall()
                today = dict(q("""SELECT kind, COUNT(*) FROM records WHERE created_at >= ? GROUP BY kind""",
                               (day_ago,)).fetchall())
            by_lang: dict = {}
            for lang, rating, n in ratings:
                row = by_lang.setdefault(lang or "?", {"up": 0, "down": 0})
                row["up" if rating == "up" else "down"] += n
            sync_summary: dict = {}
            for kind, status, n in sync:
                sync_summary.setdefault(kind, {})[status] = n
            return {"totals": totals, "last_24h": today, "ratings_by_language": by_lang,
                    "incidents_24h": [{"code": c, "count": n} for c, n in incidents],
                    "safety_7d": dict(safety), "sync": sync_summary}
        return await asyncio.to_thread(_read)

    async def ping(self) -> bool:
        def _check():
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        try:
            return await asyncio.to_thread(_check)
        except sqlite3.Error:
            return False
