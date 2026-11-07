#!/usr/bin/env bash
# Kasa Health — start backend + frontend with the production servers (Gunicorn).
# For development with auto-reload, use:  python run.py
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; source .env; set +a      # handles values with spaces, unlike `export $(cat .env)`
else
  echo "⚠️  No .env found — copy .env.example to .env and add your keys."
fi

pip install -q -r requirements.txt

echo "▶ Backend on :8000"
(cd backend && PORT=8000 gunicorn -c gunicorn.conf.py app.main:app) &
BACKEND_PID=$!
for _ in $(seq 1 40); do curl -fs localhost:8000/health >/dev/null 2>&1 && break; sleep 0.5; done

echo "▶ Frontend on :5000"
(cd frontend && PORT=5000 FASTAPI_URL="${FASTAPI_URL:-http://127.0.0.1:8000}" PROXY_COUNT=0 \
   gunicorn -c gunicorn.conf.py app:app) &
FRONTEND_PID=$!

trap 'echo; echo "Stopping…"; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; wait; exit 0' INT TERM
echo "✅ Kasa Health running at http://localhost:5000  (Ctrl+C to stop)"
wait
