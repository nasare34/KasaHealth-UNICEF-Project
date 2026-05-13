# Deploying Kasa Health to Render.com

Render is the best option for this app — free tier, supports Python, no credit card needed.

## Steps

1. Push your project to GitHub (create a new repo)

2. Go to https://render.com and sign up (free)

3. Click **New → Blueprint** and connect your GitHub repo

4. Render will read `render.yaml` and create two services automatically:
   - `kasa-health-backend` (FastAPI on port 8000)
   - `kasa-health-frontend` (Flask/Gunicorn)

5. Add your environment variables in Render dashboard for the backend service:
   - `KHAYA_ASR_KEY`
   - `KHAYA_TTS_KEY`
   - `KHAYA_TRANSLATE_KEY`
   - `GROQ_API_KEY`

6. Update `render.yaml` line 25 with your actual backend URL:
   ```
   value: https://kasa-health-backend.onrender.com
   ```
   (Render shows you this URL after first deploy)

7. Your app will be live at: `https://kasa-health-frontend.onrender.com`

## Why not Vercel or Streamlit?
- **Vercel**: Python backend not supported (Node.js only)
- **Streamlit**: Wrong tool — it's for data dashboards, not web apps with custom UI
- **Render**: Perfect fit — supports Python, free tier, easy env vars, auto-deploy on git push

## Survey data
Survey responses are saved to `surveys.json` in the backend.
Download them at: `https://kasa-health-backend.onrender.com/survey/export`
