# Kasa Health — ASRH Assistant v8

AI-powered Adolescent Sexual & Reproductive Health assistant in **Twi, Dagbani and Ewe**,
built for UNICEF Ghana with Khaya AI (GhanaNLP). Answers come from **Grok, Gemini, OpenAI or Qwen**
(plus optional Groq), with automatic, fast switching between them.

## Features

- 💬 **Chat**: voice or text questions, answered in the user's language and English, with a spoken reply.
- 🎤 **Transcribe**: speech-to-text only, with an editable result.
- 🤖 **Voice Agent**: a live spoken conversation, in two modes:
  - **Hold to talk**: hold the button (or the Space bar) while speaking.
  - **Hands-free**: tap once and talk naturally. Kasa detects when you stop, replies, and listens again. Tap or press Space to interrupt.
- 👍👎 **Ratings and corrections**: a thumbs-down opens a correction form, and recorded corrections go to Hugging Face.
- 📝 **Survey**: sent to Google Forms.
- 📋 **History**: conversations are saved in the browser and can be reopened.
- 🛟 **Safety support**: messages about suicide, self-harm, abuse, serious injury or a medical emergency get a caring reply, tap-to-call 112 and SHEplus numbers, and an *optional* form to be contacted. The support team is emailed straight away.
- 🌗 **Light (green) and dark mode**, a **collapsible side panel**, and a layout built for phones.
- 🛠 **Admin page** at `/admin`: switch or reorder AI providers live, check their health, and view incidents and safety reports.

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env        # add XAI_API_KEY and the Khaya keys at minimum
python run.py
```

Open http://localhost:5000. Check http://localhost:8000/ready, which should report `"status": "ready"`.

If you have answers in the old `ratings.json`, `feedback.json` and `surveys.json` files, leave them in `backend/`. They are imported into the new database automatically on the first start.

## Architecture

```
Browser ──► Flask frontend (threaded Gunicorn) ──private──► FastAPI backend (async Gunicorn)
            serves the UI, forwards /api/*                   ├─ Grok (xAI)  → Groq fallback (optional)
            security headers, friendly errors                ├─ Khaya ASR / translation / TTS
                                                              ├─ SQLite (ratings, feedback, surveys)
                                                              └─ background jobs → Google Forms, Hugging Face
```

```
backend/app/
  config.py        every setting, read from the environment
  errors.py        friendly error messages + handlers
  resilience.py    concurrency caps, circuit breakers, retries, shared cache
  http_client.py   one pooled HTTP client
  llm.py           Grok with automatic failover
  khaya.py         ASR / translation / TTS (cached, parallel TTS)
  storage.py       SQLite storage, safe for concurrent users
  integrations.py  Google Forms, Hugging Face, background job queue
  safety.py        prompts, input hardening, hotline guard
  middleware.py    request IDs, internal token, rate limiting
  routes/          ai.py, feedback.py, admin.py
frontend/static/js/
  app.js  agent.js  recorder.js  recorder-worklet.js  player.js  api.js  feedback.js  ui.js  state.js
```

## How it stays reliable with many users

There's no login, so the app protects itself:

- **Rate limiting.** Each user (by IP address) gets a fixed number of AI requests per minute. Extra requests get a polite "please wait a moment" message.
- **Concurrency caps.** Each upstream service (LLM, ASR, TTS, translation) has a limit on simultaneous calls. If the queue is too long, users get a friendly "busy, try again in a few seconds" message instead of the server falling over.
- **Shared answers.** When many people ask the same first question at once (for example, a quick-topic button), only one AI call is made and everyone shares the answer. Translations and voice clips are cached too.
- **Failover.** If Grok fails, the app tries the fallback Grok model, then Groq (if configured). A provider that keeps failing is skipped automatically for 30 seconds.
- **Retries.** Temporary failures (timeouts, 429, 5xx) are retried with increasing delays. Permanent ones (bad key, blocked network) are not retried.
- **Graceful degradation.** If translation fails, the user still gets the English answer with a gentle note. If voice synthesis fails, the text answer still shows.
- **Feedback is never lost.** Ratings, corrections and surveys are saved to the database first. Syncing to Google Forms and Hugging Face happens in the background and can be retried. Ratings sent while offline are kept in the browser and resent later.

Tested capacity (2 workers each, on a small machine): 100 simultaneous questions were all answered in about 1.6 s with simulated 0.3 s AI latency. See `e2e/`.

## Errors users see

Users never see technical errors. Every problem becomes a kind message, for example:

> "Lots of people are chatting with Kasa right now. Please try again in a few seconds — thank you for your patience!"

Where it helps, a **Try again** button is shown. Each error carries a reference ID, which you can search for in the backend logs to find the exact technical cause.

## AI providers and fast switching

Add a key for each provider you want (see `.env.example`). Kasa tries them in order:

- **Failover:** if a provider *fails*, the next one starts immediately.
- **Hedging:** if a provider is *slow* (over 6 s in chat, 3 s in the voice agent), the next one starts **in parallel** and the first good answer wins.
- **Cool-downs:** a provider that is out of credits or blocked is skipped for 2–5 minutes, so users don't wait on it. The team is emailed.
- **Live control:** change the order or switch a provider off at **`/admin`**. The change reaches every server within about 5 seconds, with no restart needed.

## Alerts to the support team

Reports go to `ALERT_EMAIL_TO` (default **akwasi@khaya.ai**):

| Email | When |
|---|---|
| `Error: llm_unavailable` (and similar) | A user's request failed after every fallback. Repeats are grouped: one email per error type per 15 min, with a count. |
| `Error: provider_down:<name>` | A provider went down hard (out of credits, bad key, blocked), even if another provider covered for it. |
| `⚠️ Safety alert` | A message suggested someone may be at risk (anonymous; at most one per conversation per 30 min). |
| `🚨 URGENT: contact request` | The user chose to share a phone number (and optionally a name) to be contacted. Sent immediately. |

Every incident and safety report is also saved and shown on `/admin`, even if email isn't configured. Set up email using `DEPLOY.md` → "Alert emails".

## Admin dashboard (`/admin`)

Sign in with your `ADMIN_TOKEN` (see "How do I get admin access" in DEPLOY.md). The dashboard shows:

- **Status banner:** green when everything works, orange when something needs attention, red when users are affected (for example, no AI provider is working).
- **Service health:** a card for each AI provider, Khaya translation, voice (TTS) and speech recognition (ASR), Hugging Face, email, the database and background jobs.
  - Each card shows whether it's working, how fast it is, and **what to do** if it isn't (for example, "Out of credits: add credits at console.x.ai, then press Reset cool-downs").
  - **Quick check** tests everything in Twi. **Full check** tests translation, voice and speech recognition in Twi, Dagbani and Ewe.
  - Speech recognition is tested with the voice Khaya just generated, so a pass means the whole voice path works.
  - A coloured strip on each card shows its last 24 checks.
- **Checks only run when you click.** Nothing tests the APIs in the background, and opening or auto-refreshing the dashboard is free. A quick check costs about 3 small Khaya calls plus one tiny request per AI provider; a full check costs about 9 Khaya calls.
- **Overview:** ratings and the share rated helpful (per language), corrections, surveys, safety contacts, errors in the last 24 hours, and records waiting to sync.
- **AI provider order:** reorder providers or switch them on and off live, and reset cool-downs.
- **Recent errors** and **safety reports**, plus **Send test email** and **Retry failed syncs**.
- **Download data:** surveys, ratings, corrections, incidents and safety reports, as CSV (opens in Excel) or JSON.

Admin routes are switched off completely unless `ADMIN_TOKEN` is set. Every request needs the header `X-Admin-Token: <your token>`.

| Endpoint | Purpose |
|---|---|
| `GET /admin/export/survey` (also `rating`, `feedback`) | Download data as JSON |
| `GET /admin/stats` | Live load, cache hits, circuit states, job queue |
| `GET /admin/diagnostics` | Makes real test calls to Grok, Khaya and Hugging Face. This is the quickest way to check keys. |
| `POST /admin/retry-sync` | Re-send records that failed to reach Google Forms or Hugging Face |
| `GET/POST /admin/llm` | Provider status, or set `{"order": [...], "disabled": [...]}` |
| `GET /admin/health`, `POST /admin/health/run` (`{"full": true}`) | Latest health report and history, or run a check now |
| `GET /admin/overview` | Usage numbers for the dashboard |
| `POST /admin/llm/reset` | Clear cool-downs (for example, right after topping up credits) |
| `POST /admin/alerts/test` | Send a test email |
| `GET /admin/export/incident`, `/admin/export/safety` | Download incidents and safety reports |

The old export URLs (`/survey/export` etc.) still work, but now need the token too.

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://<backend>/admin/diagnostics
```

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q   # 39 tests, fully offline
```

The browser and load tests are in `e2e/README.md`.

## Configuration

All settings are documented in `.env.example`. The important ones are:

| Variable | Notes |
|---|---|
| `XAI_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `QWEN_API_KEY` | At least **one** is required. More keys give more backups. |
| `LLM_ORDER` | Default try order (can be changed live at `/admin`) |
| `RESEND_API_KEY` or `SMTP_*` | Turns on alert emails |
| `XAI_MODEL` | Default `grok-4.20-0309-non-reasoning` (lowest latency) |
| `XAI_FALLBACK_MODEL` | Default `grok-4.3`; leave blank to disable |
| `GROQ_API_KEY` | Optional last-resort fallback |
| `KHAYA_ASR_KEY` / `KHAYA_TTS_KEY` / `KHAYA_TRANSLATE_KEY` | Required |
| `HF_TOKEN` | For uploading correction audio |
| `INTERNAL_API_TOKEN` | Same value on both services. Blocks direct calls to the backend. |
| `ADMIN_TOKEN` | Enables admin routes |
| `RATE_LIMIT_AI_PER_MIN` | Per user, per worker (default 20) |

## Hugging Face dataset

Corrections are uploaded to https://huggingface.co/datasets/nasare34/kasa-health-feedback as
`feedback/TIMESTAMP_ID_LANG.wav` and `.json`. The audio is also kept on the server (`DATA_DIR/audio/`) so failed uploads can be retried.

## Deployment

See [DEPLOY.md](DEPLOY.md). What changed from v7 is in [CHANGES.md](CHANGES.md).
