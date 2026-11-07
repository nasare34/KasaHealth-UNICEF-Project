#!/usr/bin/env bash
# Starts mock upstreams + real backend + real frontend (Gunicorn) for e2e tests.
# Usage: ./start_stack.sh      stop with: ./start_stack.sh stop
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"; RUN=/tmp/kasa-e2e; mkdir -p $RUN
for f in $RUN/*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; done; sleep 2
[ "${1:-}" = "stop" ] && { echo stopped; exit 0; }
rm -rf $RUN/data && mkdir -p $RUN/data
cd "$HERE" && setsid sh -c "echo \$\$ > $RUN/mock.pid; exec python3 -m uvicorn mock_upstream:app --port 9100" > $RUN/mock.log 2>&1 < /dev/null &
export XAI_API_KEY=xai-test XAI_BASE_URL=http://127.0.0.1:9100/v1 XAI_FALLBACK_MODEL= GROQ_API_KEY=
export GEMINI_API_KEY=g-test GEMINI_BASE_URL=http://127.0.0.1:9100/gemini/v1
export OPENAI_API_KEY=o-test OPENAI_BASE_URL=http://127.0.0.1:9100/openai/v1
export QWEN_API_KEY=q-test QWEN_BASE_URL=http://127.0.0.1:9100/qwen/v1
export RESEND_API_KEY=re-test RESEND_API_URL=http://127.0.0.1:9100/emails LLM_HEDGE_CHAT_SECONDS=1.5 LLM_HEDGE_AGENT_SECONDS=1
export KHAYA_ASR_KEY=k KHAYA_TTS_KEY=k KHAYA_TRANSLATE_KEY=k KHAYA_BASE_URL=http://127.0.0.1:9100
export SURVEY_FORM_URL=http://127.0.0.1:9100/forms/s RATINGS_FORM_URL=http://127.0.0.1:9100/forms/r HF_TOKEN=
export DATA_DIR=$RUN/data LEGACY_IMPORT_DIRS=$RUN/data ADMIN_TOKEN=admin-e2e INTERNAL_API_TOKEN=internal-e2e
export ENVIRONMENT=production RATE_LIMIT_AI_PER_MIN=${RATE_LIMIT_AI_PER_MIN:-60}
cd "$ROOT/backend" && setsid gunicorn -c gunicorn.conf.py -p $RUN/backend.pid app.main:app > $RUN/backend.log 2>&1 < /dev/null &
cd "$ROOT/frontend" && FASTAPI_URL=http://127.0.0.1:8000 PROXY_COUNT=0 setsid gunicorn -c gunicorn.conf.py -p $RUN/frontend.pid app:app > $RUN/frontend.log 2>&1 < /dev/null &
for i in $(seq 1 40); do
  curl -s -m 1 localhost:8000/health >/dev/null && curl -s -m 1 localhost:5000/healthz >/dev/null && curl -s -m 1 localhost:9100/calls >/dev/null && { echo "all up (logs in $RUN)"; exit 0; }
  sleep 0.5
done
echo "startup timed out — see $RUN/*.log"; exit 1
