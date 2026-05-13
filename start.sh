#!/bin/bash
# ─── KASA HEALTH — Start Script ────────────────────────────────────────────────
# Starts FastAPI backend + Flask frontend concurrently

set -e

# Load env
if [ -f .env ]; then
  export $(cat .env | grep -v '^#' | xargs)
fi

echo "🌿 Starting Kasa Health ASRH Assistant..."

# Start FastAPI backend in background
echo "▶ Starting FastAPI backend on port 8000..."
cd backend
pip install -r requirements.txt -q
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
FASTAPI_PID=$!
cd ..

# Wait for FastAPI to be ready
sleep 2

# Start Flask frontend
echo "▶ Starting Flask frontend on port 5000..."
cd frontend
pip install -r requirements.txt -q
python app.py &
FLASK_PID=$!
cd ..

echo ""
echo "✅ Kasa Health is running!"
echo "   Frontend: http://localhost:5000"
echo "   API docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

# Trap Ctrl+C and kill both
trap "echo ''; echo 'Stopping...'; kill $FASTAPI_PID $FLASK_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
