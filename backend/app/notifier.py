"""Emails to the support team.

Two kinds: errors users can't fix themselves, and safety reports. I save
everything to the database first, so nothing is lost if email isn't set up
yet, and I group repeats of the same error so the inbox stays readable.

To reach more people, point ALERT_EMAIL_TO at a group address."""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx

from .config import Settings
from .errors import UpstreamError
from .http_client import send
from .resilience import retry_async
from .storage import Storage, new_id, utcnow

logger = logging.getLogger("kasa.alerts")

# Errors worth emailing about. User mistakes (like a message that's too long) aren't.
REPORTABLE_CODES = {"llm_unavailable", "asr_unavailable", "translate_unavailable", "tts_unavailable",
                    "busy", "internal", "timeout"}


# Copying a Gmail App Password off Google's page often brings invisible
# non-breaking spaces along, and smtplib can only send plain ASCII. This cleans that up.
_ODD_SPACES = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000\ufeff]")


def clean_credential(value: str, *, drop_spaces: bool = False) -> str:
    value = _ODD_SPACES.sub("" if drop_spaces else " ", value or "").strip()
    return value.replace(" ", "") if drop_spaces else value


class Mailer:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self.sent = 0
        self.failed = 0

    @property
    def configured(self) -> bool:
        return self.s.email_configured

    async def send(self, subject: str, body: str, *, reply_to: str | None = None) -> None:
        if not self.configured:
            raise RuntimeError("email not configured (set RESEND_API_KEY or SMTP_HOST)")
        try:
            if self.s.resend_api_key:
                await self._send_resend(subject, body, reply_to)
            else:
                await retry_async(lambda: self._send_smtp(subject, body, reply_to), attempts=3, label="smtp")
            self.sent += 1
            logger.info("Alert email sent: %s", subject)
        except Exception:
            self.failed += 1
            raise

    async def _send_resend(self, subject: str, body: str, reply_to: str | None) -> None:
        payload = {"from": self.s.alert_email_from, "to": [self.s.alert_email_to],
                   "subject": subject, "text": body}
        if reply_to:
            payload["reply_to"] = reply_to

        async def call():
            await send("email", self.http, "POST", self.s.resend_api_url,
                       headers={"Authorization": f"Bearer {self.s.resend_api_key}"}, json=payload, timeout=20)
        await retry_async(call, attempts=3, label="resend")

    async def _send_smtp(self, subject: str, body: str, reply_to: str | None) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.alert_email_from
        msg["To"] = self.s.alert_email_to
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)

        user = clean_credential(self.s.smtp_user, drop_spaces=True)
        # Gmail App Passwords are 16 letters. The spaces Google shows are just for reading.
        password = clean_credential(self.s.smtp_password, drop_spaces="gmail" in self.s.smtp_host.lower())

        def _deliver():
            context = ssl.create_default_context()
            if self.s.smtp_use_ssl:
                server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=20, context=context)
            else:
                server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20)
                server.starttls(context=context)
            try:
                if user:
                    try:
                        server.login(user, password)
                    except UnicodeEncodeError as exc:
                        raise RuntimeError("SMTP_USER or SMTP_PASSWORD contains a character email servers can't "
                                           "accept (often an accent or a symbol pasted from a web page). "
                                           "Retype it by hand in .env and restart.") from exc
                    except smtplib.SMTPAuthenticationError as exc:
                        raise RuntimeError("The email server rejected the login. For Gmail, SMTP_PASSWORD must be "
                                           "a 16-letter App Password (not your normal password) created on the "
                                           "same account as SMTP_USER.") from exc
                server.send_message(msg)
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

        try:
            await asyncio.to_thread(_deliver)
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
            raise UpstreamError("smtp", repr(exc), retryable=True) from exc


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class Reporter:
    def __init__(self, settings: Settings, storage: Storage, mailer: Mailer, jobs, llm_status=None):
        self.s = settings
        self.storage = storage
        self.mailer = mailer
        self.jobs = jobs
        self.llm_status = llm_status     # gives us provider status to put in the email

    async def _record(self, kind: str, payload: dict) -> dict:
        payload = {"id": new_id(), "timestamp": utcnow(), **payload}
        await self.storage.add(kind, payload, sync_status="pending" if self.mailer.configured else "skipped")
        return payload

    async def _queue_email(self, record_id: str, subject: str, body: str, reply_to: str | None = None) -> None:
        await self.storage.update_payload(record_id, {"_email": {"subject": subject, "body": body,
                                                                 "reply_to": reply_to}})
        self._send_later(record_id, subject, body, reply_to)

    async def resend_failed(self) -> int:
        """Re-send alert emails that failed earlier, e.g. after you fix the email settings."""
        if not self.mailer.configured:
            return 0
        count = 0
        for kind in ("safety", "incident"):
            for record in await self.storage.failed(kind, statuses=("failed",)):
                email = record.get("_email") or self._rebuild_safety_email(record)
                if email:
                    self._send_later(record["id"], email["subject"], email["body"], email.get("reply_to"))
                    count += 1
        return count

    def _rebuild_safety_email(self, r: dict) -> dict | None:
        """Older safety records were saved before I started keeping the email text with
        them. A contact request must never get lost, so I rebuild a plain version."""
        if r.get("type") == "contact":
            subject = f"[{self.s.public_app_name}] 🚨 URGENT (re-sent): contact request ({r.get('category') or 'support'})"
            lines = [f"A user asked to be contacted for support. This email failed earlier and is being re-sent.",
                     "", f"Name        : {r.get('name') or '(not given)'}", f"Phone       : {r.get('phone')}",
                     f"Safe to call: {r.get('safe_to_call') or '(not given)'}", f"Best time   : {r.get('best_time') or '(not given)'}",
                     f"Category    : {r.get('category') or '-'}", f"Language    : {r.get('language') or '-'}",
                     f"First saved : {r.get('timestamp')}", "", f"Their message:\n  {r.get('question_original') or '-'}",
                     f"English:\n  {r.get('question_english') or '-'}", f"Note:\n  {r.get('note') or '-'}"]
            return {"subject": subject, "body": "\n".join(lines)}
        if r.get("type") == "flag":
            subject = f"[{self.s.public_app_name}] ⚠️ Safety alert (re-sent): {r.get('category')}"
            body = (f"A message suggested someone may be at risk. This email failed earlier and is being re-sent.\n\n"
                    f"Category : {r.get('category')}\nFirst saved: {r.get('timestamp')}\nLanguage : {r.get('language')}\n"
                    f"Session  : {r.get('session_id') or '-'}\n\nWhat they said:\n  {r.get('question_original')}\n\n"
                    f"English:\n  {r.get('question_english')}\n")
            return {"subject": subject, "body": body}
        return None

    def _send_later(self, record_id: str, subject: str, body: str, reply_to: str | None = None) -> None:
        if not self.mailer.configured:
            logger.warning("ALERT (email not configured) %s — saved as incident %s", subject, record_id)
            return

        async def job():
            try:
                await self.mailer.send(subject, body, reply_to=reply_to)
                await self.storage.mark_sync(record_id, True)
            except Exception as exc:  # a broken email setup shouldn't take a worker down
                logger.error("Could not send alert email '%s': %s", subject, exc)
                await self.storage.mark_sync(record_id, False, str(exc))
        self.jobs.submit(f"email:{record_id}", job)

    # --- error reports ---
    async def report_error(self, code: str, *, path: str, request_id: str, detail: str = "",
                           language: str = "", user_text: str = "") -> None:
        """Safe to call from anywhere; it never raises."""
        try:
            record = await self._record("incident", {
                "code": code, "path": path, "request_id": request_id,
                "detail": detail[:2000], "language": language})
            send_now, repeats = await self.storage.claim_alert(code, self.s.alert_dedupe_minutes * 60)
            if not send_now:
                return
            providers = ""
            if self.llm_status is not None:
                try:
                    status = await self.llm_status()
                    providers = "\n".join(
                        f"  {p['position']}. {p['label']:<22} {p['model']:<32} "
                        f"{'ENABLED ' if p['enabled'] else 'disabled'} "
                        f"{'configured' if p['configured'] else 'NO KEY':<10} circuit={p['circuit']}"
                        + (f"  last error: {p['last_error'][:120]}" if p['last_error'] else "")
                        for p in status["providers"])
                except Exception:
                    providers = "  (unavailable)"
            body = (
                f"{self.s.public_app_name} — a request could not be completed.\n\n"
                f"What happened : {code}\n"
                f"When          : {_stamp()}\n"
                f"Where         : {path}\n"
                f"Reference ID  : {request_id}   (search this in the backend logs)\n"
                f"Language      : {language or '-'}\n"
                f"Similar events since the last email: {repeats}\n\n"
                f"Technical detail:\n{detail[:1500] or '-'}\n\n"
                f"AI providers right now:\n{providers or '  -'}\n\n"
                "What to check first:\n"
                "  • Out of credits / spending limit → top up that provider's billing page.\n"
                "  • Access denied → check the API key, or the provider may block this server's network.\n"
                "  • Switch provider order or disable one: open /admin on the website.\n"
                f"\nThe user saw a friendly message and was invited to try again. "
                f"Further errors of this type are grouped for {self.s.alert_dedupe_minutes} minutes."
            )
            await self._queue_email(record["id"], f"[{self.s.public_app_name}] Error: {code}", body)
        except Exception:
            logger.exception("report_error failed (code=%s)", code)

    # --- safety reports ---
    async def report_safety_flag(self, *, session_id: str, category: str, severity: str, language: str,
                                 question_original: str, question_english: str, request_id: str,
                                 channel: str) -> None:
        """Anonymous early warning when a high-risk message comes in.
        At most one email per conversation every 30 minutes."""
        try:
            record = await self._record("safety", {
                "type": "flag", "session_id": session_id, "category": category, "severity": severity,
                "language": language, "channel": channel, "question_original": question_original[:1000],
                "question_english": question_english[:1000], "request_id": request_id})
            send_now, _ = await self.storage.claim_alert(f"safety-flag:{session_id or record['id']}", 1800)
            if not send_now:
                return
            body = (
                f"⚠️ {self.s.public_app_name} — a user may be at risk.\n\n"
                f"Category  : {category}  (severity: {severity})\n"
                f"When      : {_stamp()}\n"
                f"Channel   : {channel}\n"
                f"Language  : {language}\n"
                f"Session   : {session_id or '-'}\n"
                f"Reference : {request_id}\n\n"
                f"What they said (original):\n  {question_original[:800]}\n\n"
                f"English translation:\n  {question_english[:800]}\n\n"
                "Kasa replied supportively, showed emergency numbers (112, SHEplus) and offered them the "
                "option to share a name and phone number. If they do, you will receive a second email with "
                "their contact details.\n"
            )
            await self._queue_email(record["id"], f"[{self.s.public_app_name}] ⚠️ Safety alert: {category}", body)
        except Exception:
            logger.exception("report_safety_flag failed")

    async def report_safety_contact(self, data: dict, request_id: str) -> str:
        """The user agreed to be contacted. This one always goes out straight away."""
        record = await self._record("safety", {"type": "contact", "request_id": request_id, **data})
        body = (
            f"🚨 {self.s.public_app_name} — a user asked to be contacted for support.\n\n"
            f"Name        : {data.get('name') or '(not given)'}\n"
            f"Phone       : {data.get('phone')}\n"
            f"Best time   : {data.get('best_time') or '(not given)'}\n"
            f"Safe to call: {data.get('safe_to_call') or '(not given)'}\n"
            f"Category    : {data.get('category') or '-'}\n"
            f"Language    : {data.get('language') or '-'}\n"
            f"Session     : {data.get('session_id') or '-'}\n"
            f"When        : {_stamp()}\n"
            f"Reference   : {request_id}\n\n"
            f"Their message:\n  {(data.get('question_original') or '-')[:800]}\n\n"
            f"English:\n  {(data.get('question_english') or '-')[:800]}\n\n"
            f"Optional note from the user:\n  {(data.get('note') or '-')[:800]}\n\n"
            "The user gave consent to be contacted. Please respond promptly and with care. "
            "They were also shown: emergency 112, SHEplus Ghana 055 054 5672 / 0800 00 11 22.\n"
        )
        await self._queue_email(record["id"], f"[{self.s.public_app_name}] 🚨 URGENT: contact request "
                                        f"({data.get('category') or 'support'})", body)
        return record["id"]

    def stats(self) -> dict:
        return {"email_configured": self.mailer.configured, "to": self.s.alert_email_to,
                "sent": self.mailer.sent, "failed": self.mailer.failed}
