# Deploying Kasa Health v8

## Before anything else: rotate the old keys

The v7 project folder contained a `.env` with live keys (Khaya ×3, Groq, Hugging Face).
If that folder was ever zipped, emailed, uploaded or committed, create new keys and
delete the old ones:

- Khaya: translation.ghananlp.org → Profile
- Hugging Face: huggingface.co → Settings → Access Tokens. Delete the old one; it had write access.
- Groq: console.groq.com → API Keys

## Option A — Render (recommended)

1. Push the project to GitHub. Check that `.env` is **not** committed (`.gitignore` covers it).
2. On Render, click **New → Blueprint** and pick the repo. Render reads `render.yaml` and creates:
   - `kasa-health-backend` (FastAPI, with a 1 GB disk for the database)
   - `kasa-health-frontend` (Flask)
3. Render will ask for the secret values. Fill in:
   `XAI_API_KEY`, `KHAYA_ASR_KEY`, `KHAYA_TTS_KEY`, `KHAYA_TRANSLATE_KEY`, `HF_TOKEN`.
   `GROQ_API_KEY` is optional; leave it blank if you don't want the fallback.
4. `INTERNAL_API_TOKEN` and `ADMIN_TOKEN` are generated automatically, and the frontend
   copies the internal token from the backend. Your admin token is under
   **backend → Environment**.
5. Once both services are live, open
   `https://kasa-health-backend.onrender.com/ready`. It should show `"status": "ready"`.
   Then run the diagnostics to check each key:
   ```bash
   curl -H "X-Admin-Token: <ADMIN_TOKEN>" https://kasa-health-backend.onrender.com/admin/diagnostics
   ```
6. If your frontend URL is different, update `CORS_ORIGINS` on the backend.

**Upgrading an existing v7 Render setup.** Apply the new blueprint, or update each service by hand:
- **Backend:** start command `gunicorn -c gunicorn.conf.py app.main:app`. Add a disk mounted at `/var/data`, set `DATA_DIR=/var/data`, and add `XAI_API_KEY`, `INTERNAL_API_TOKEN` and `ADMIN_TOKEN`.
- **Frontend:** start command `gunicorn -c gunicorn.conf.py app:app`, and the same `INTERNAL_API_TOKEN`.

**Notes:**
- A service with a disk runs as one instance. To scale, raise `WEB_CONCURRENCY` (workers) and the plan size. To scale across several instances, move storage to Postgres and rate limits to Redis.
- Without a disk, the database is wiped on every deploy. Google Forms and Hugging Face still receive everything, but local exports would reset.

## Option B — Docker (any server)

```bash
cp .env.example .env    # fill in keys + INTERNAL_API_TOKEN + ADMIN_TOKEN
docker compose up --build -d
```

The app runs on port 5000. Put HTTPS in front of it (Caddy or nginx), because browsers only allow the
microphone on HTTPS. Set `PROXY_COUNT=1` on the frontend if you do. Data is kept in the
`kasa-data` volume.

## How do I get admin access?

1. Pick a long random token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Locally, put `ADMIN_TOKEN=<token>` in `.env` and restart. On Render, it's generated for you: backend → Environment → `ADMIN_TOKEN`.
3. Open `https://<your-site>/admin`, paste the token and press **Connect**.

To lock someone out, change the token and restart.

## Alert emails (errors + safety reports)

Pick **one** option and set it on the backend. Then press **Send test email** on `/admin`.

**Option A: Gmail (quickest to set up)**
1. Turn on 2-Step Verification on the Google account that will send the alerts.
2. Go to Google Account → Security → **App passwords**, and create one for "Mail".
3. Set:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=youraccount@gmail.com
   SMTP_PASSWORD=<the 16-character app password>
   ALERT_EMAIL_FROM=Kasa Health <youraccount@gmail.com>
   ```

**Option B: Resend (best for production)**
1. Sign up at resend.com and verify a sending domain (for example `khaya.ai`).
2. Create an API key, then set `RESEND_API_KEY=re_...` and `ALERT_EMAIL_FROM=Kasa Health <alerts@khaya.ai>`.

The recipient is `ALERT_EMAIL_TO` (default `akwasi@khaya.ai`). Several people can be reached by pointing this at a group address.

**Responding to safety reports.** A `🚨 URGENT: contact request` email contains a young person's phone number,
and says whether it's safe to call or whether they'd prefer text or WhatsApp only. Please agree **who** on the team
responds, **how fast**, and **how to escalate** (for example to SHEplus, DOVVSU or a health facility), and keep these
emails confidential.

## AI provider keys

| Provider | Where to get a key | Notes |
|---|---|---|
| Grok (xAI) | console.x.ai | Needs prepaid credits (Billing). |
| Gemini | aistudio.google.com → Get API key | Has a free tier. |
| OpenAI | platform.openai.com → API keys | Needs billing. |
| Qwen | Alibaba Cloud Model Studio (international) | Use `QWEN_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1` for a US-region key. |
| Groq | console.groq.com | Some networks are blocked. |

Add as many as you can: each extra key is another backup. After deploying, open `/admin` → **Test all providers**.

## Choosing the Grok model

- `grok-4.20-0309-non-reasoning` (default): fastest replies, best for the voice agent.
- `grok-4.3`: slower but stronger reasoning, and used as the fallback.

Model names change over time. If `/admin/diagnostics` reports an unknown model, check
https://docs.x.ai for current names and update `XAI_MODEL`.

## Sizing guide

| Users talking at the same moment | Suggested setup |
|---|---|
| up to ~50 | Starter plans, defaults |
| ~50–200 | Standard plans, `WEB_CONCURRENCY=4` on both services |
| more | Multiple instances: Postgres + Redis (see notes above) |

The real limits are usually the upstream APIs (Khaya, xAI). Their concurrency caps are
`MAX_CONCURRENT_*` in `.env`.
