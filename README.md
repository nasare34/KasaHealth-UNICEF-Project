# Kasa Health — ASRH Assistant v7
AI-powered Adolescent Sexual & Reproductive Health assistant in Twi, Dagbani, and Ewe.
Built for UNICEF Ghana with Khaya AI + Groq.

## Features
- 💬 **Chat** — Voice or text questions → translated answer + TTS voice response
- 🎤 **Transcribe** — Speech-to-text only, no LLM, editable output
- 🤖 **Voice Agent** — Live conversational mode, 1-2 sentence replies
- 👍👎 **Response Feedback** — Thumbs rating on every answer; thumbs down opens correction form with audio recording uploaded to Hugging Face
- 📝 **Survey** — Full feedback form → Google Sheets via Google Forms
- 📋 **Chat History** — Sessions saved in browser localStorage
- ⬇ **Download** — Every response downloadable as text or audio

## Quick Start
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your 5 keys
python run.py
```
Open: http://localhost:5000
API docs: http://localhost:8000/docs

## Environment Variables
| Variable | Description |
|----------|-------------|
| KHAYA_ASR_KEY | Khaya ASR API key |
| KHAYA_TTS_KEY | Khaya TTS API key |
| KHAYA_TRANSLATE_KEY | Khaya Translation API key |
| GROQ_API_KEY | Groq LLM API key |
| HF_TOKEN | Hugging Face write token (for audio feedback upload) |

## API Endpoints
| Endpoint | Description |
|----------|-------------|
| POST /asr | Speech → text (Khaya ASR v1) |
| POST /chat | Full ASRH chat pipeline |
| POST /tts | Text → speech (Khaya TTS v2) |
| POST /translate | Text translation |
| POST /agent | Live voice agent (short replies) |
| POST /feedback/thumbs | Save thumbs rating + upload audio to HF |
| GET /feedback/export | Download all feedback as JSON |
| POST /survey | Save survey → Google Forms |
| GET /survey/test | Test Google Form submission |
| GET /survey/export | Download all surveys as JSON |

## Hugging Face Dataset
Audio corrections from thumbs-down feedback are uploaded to:
https://huggingface.co/datasets/nasare34/kasa-health-feedback

Each submission creates two files:
- `feedback/TIMESTAMP_ID_LANG.wav` — the audio correction
- `feedback/TIMESTAMP_ID_LANG.json` — metadata (question, system answer, correction text, language)

## Deployment (Render.com)
See DEPLOY.md for full instructions.
Push to GitHub → connect to Render → add 5 env vars → live URL.
