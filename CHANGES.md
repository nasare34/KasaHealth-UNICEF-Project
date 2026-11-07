# v8.2 — Admin dashboard and health monitoring

- **Health checks for every API:**
  - **Covered:** each AI provider, Khaya translation, voice (TTS) and speech recognition (ASR), Hugging Face, email, the database and the background job queue.
  - **Quick or full:** the quick check uses Twi; the full check covers Twi, Dagbani and Ewe.
  - **ASR is a round trip:** it's tested with the audio TTS just produced, so a pass proves the whole voice path works.
- **Plain-language advice for every failure:** out of credits, rejected key, blocked network, model renamed, rate limited, outage, too slow, or not set up. Each tells you exactly which setting or website to go to.
- **Status banner:** green, yellow, orange or red, with a one-line summary.
- **A history strip** on every service card, covering the last 24 checks.
- **Checks run only when you press a button:** there are no background checks, so they never cost anything on their own. Opening or auto-refreshing the dashboard makes no API calls.
- **Overview:** ratings and helpful % by language, corrections, surveys, safety contacts, errors in the last 24 hours, and records waiting to sync.
- **CSV and JSON downloads** for surveys, ratings, corrections, incidents and safety reports.
- **More dashboard features:** auto-refresh, a light/dark switch, section links, and a phone-friendly layout.
- **Code comments rewritten:** short and plain, with change history moved here instead of sitting in the code.
- **Tests:** 39 backend tests, plus 76 browser checks across three suites.

# v8.1 — What changed from v8

- **More AI providers:** Gemini, OpenAI and Qwen join Grok and Groq. Each provider's quirks are handled for you (for example, OpenAI GPT-5 models only accept their default temperature). If a provider rejects a setting, Kasa retries with a minimal request.
- **Fast, automatic switching:**
  - A failing provider is skipped instantly.
  - A slow provider gets the next one started in parallel ("hedging"), and the first answer wins.
  - Providers that are out of credits or blocked are skipped for 2–5 minutes, instead of being retried on every request as in v8.
- **`/admin` page:** reorder providers, switch them on or off, reset cool-downs, test everything, and view incidents and safety reports. Changes reach all servers within about 5 seconds.
- **Error alerts** are emailed to akwasi@khaya.ai (Resend or SMTP). They are grouped so the inbox isn't flooded, and every incident is saved even when email is off.
- **AI safety:**
  - Crisis detection uses phrase matching plus an `[URGENT]` flag from the AI itself.
  - Flagged replies always include 112 and SHEplus.
  - There is a support card with tap-to-call numbers and an optional, consented form for name and phone.
  - The team receives an immediate email.
  - In hands-free mode, the voice agent pauses after a crisis reply so the person can use the form.
- **"Kasa is an AI and can make mistakes"** now appears under every answer, with SHEplus and 112.
- **Scroll-to-accept agreement:** the tick-box unlocks only after the text has been scrolled to the end. The agreement now also explains the optional crisis contact.
- **Light (green) and dark themes,** remembered per device and following the system setting by default.
- **Collapsible side panel** on desktop, remembered between visits. On phones and tablets it becomes a slide-in drawer with a backdrop.
- **Mobile layout:**
  - Room for the iPhone notch and home bar.
  - Full-height screens that don't jump when the browser bar moves.
  - Scrolling tabs and full-screen dialogs.
  - 16px text fields, so iOS doesn't zoom in.
  - A larger voice-agent button.
- **Faster voice agent:** it moves to a backup provider after 3 s, compared with 6 s for chat.
- **Tests:** 32 backend tests, plus 49 browser checks in two suites (23 + 26), covering desktop and iPhone-sized screens.

# v8 — What changed from v7

## The original error

`LLM error: Error code: 403 - Access denied. Please check your network settings.`
was not a code bug. Groq blocks some server IP ranges at its network edge. v8 uses
**Grok (xAI)** as the main LLM and can fall back to a second Grok model and then Groq.
The user never sees a raw error message any more.

## Security fixes

- **Survey exports were public.** In v7, anyone could download every survey (names, phone numbers, emails of young people) from `/survey/export`. All exports now need `X-Admin-Token`.
- **Debug routes were public.** Anyone could trigger paid API calls through the `/debug/*` routes. They've been replaced by admin-only `/admin/diagnostics`.
- **Code injection in the page (XSS).** AI answers and saved history were inserted as HTML, so an answer containing markup could run code in the user's browser. All text is now inserted safely.
- **Fake system messages.** Conversation history sent from the browser was passed to the AI as-is, so anyone could slip in a fake "system" instruction. It is now cleaned.
- **Personal data in logs.** Survey answers, including personal details, were written to the server logs. Logs now hold no personal data.
- **Survey copies in the browser.** Survey answers were also stored in the browser's localStorage. That has been removed.
- **New protections:**
  - Per-user rate limits.
  - An internal token between the frontend and backend.
  - Security headers, including a strict CSP that blocks inline scripts.
  - Audio is validated, with size limits.
  - Every text field has a length limit.
  - API docs are hidden in production.
  - The app runs as a non-root user in Docker.
- **Invented phone numbers.** If the AI includes any phone number other than SHEplus, it is replaced with SHEplus automatically.

## Scalability and concurrency

- **Only 2 requests at a time.** The frontend ran 2 synchronous workers, so only two requests could be in progress across all users. It now runs threaded workers (64 by default).
- **Lost data.** Ratings, feedback and surveys were stored by rewriting whole JSON files, so simultaneous users could overwrite each other's data. They now go into SQLite in WAL mode, which is safe for concurrent writes. Old JSON files are imported automatically.
- **New connection per call.** A new HTTP connection was opened for every outbound call. All calls now share one connection pool.
- **New protections:** caps on simultaneous calls per upstream service, circuit breakers, retries with increasing delays, and caching with request merging (identical questions asked at once make one AI call).
- **Responses waited on syncing.** Users waited for Google Forms and Hugging Face before getting a reply. Syncing now runs in background workers with status tracking and a retry endpoint.

## Voice agent

- **Hands-free mode.** Kasa detects when you start and stop speaking, and adjusts to background noise automatically.
- **Interrupt any time** by tapping or pressing Space. There are Space and Escape shortcuts.
- **No lost first word.** The start of speech is kept (pre-roll), and the mic stays warm briefly so recordings start instantly.
- **Recording works everywhere.** Audio is recorded on a background audio thread (AudioWorklet) at the device's native rate and converted correctly. v7 forced 16 kHz, which Firefox rejects.
- **No mixed-up answers.** Turns are numbered, so late or cancelled replies are discarded.
- **Faster replies.** Each sentence is synthesised in parallel on the server.
- **Silence is free.** Silent recordings are detected before paying for speech recognition.
- **Kind "didn't catch that".** When Kasa hears nothing useful, it says so in the user's language.
- **Self-recovery.** It recovers from errors on its own, pauses politely after repeated failures, long silence, or when the tab is hidden, and keeps the screen awake during a session.
- **Live display.** The level bars show the real mic level, and the status shows a thinking timer.
- **Mic problems explained.** Permission and device problems get specific, helpful messages.

## Other fixes

- **Wrong voice selected.** The JS defaulted to the "female" voice (marked "coming soon") while "Male Low" was shown as selected.
- **Duplicate history.** Chat history saved a duplicate copy of the session on every message.
- **Correction audio kept.** The correction-recording audio is now kept on the server, so a failed Hugging Face upload can be retried *with* its audio.
- **Wrong Docker settings.** `docker-compose.yml` used wrong variable names (`KHAYA_API_KEY`).
- **`start.sh` broke** on `.env` values containing spaces.
- **Repeated feedback.** Ratings sent while offline are kept and resent.
- **Reopen conversations.** Saved conversations can now be reopened from History.
- **Accessibility.** Keyboard focus outlines, ARIA labels, and support for the reduced-motion setting.

## Compatibility

- `uvicorn main:app` still works (`backend/main.py` is a shim).
- Response fields from v7 are kept (plus new ones such as `notice` and `audio_segments`).
- The Groq SDK dependency was removed; providers are called over their OpenAI-compatible HTTP APIs.
