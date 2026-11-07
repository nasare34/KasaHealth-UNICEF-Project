"""
run.py — start the backend and frontend together for local development.

    pip install -r requirements.txt
    cp .env.example .env      # then fill in your keys
    python run.py

Open http://localhost:5000
"""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env() -> dict:
    env = dict(os.environ)
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import dotenv_values
            env.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
        except ImportError:
            print("python-dotenv not installed; run: pip install -r requirements.txt")
    else:
        print("⚠️  No .env file found. Copy .env.example to .env and add your keys.")
    return env


def wait_for(url: str, seconds: float = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> int:
    env = load_env()
    procs = []
    try:
        print("▶ Starting FastAPI backend on http://localhost:8000 …")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
            cwd=ROOT / "backend", env=env))
        if not wait_for("http://127.0.0.1:8000/health"):
            print("❌ Backend didn't start — check the messages above.")
            return 1

        print("▶ Starting Flask frontend on http://localhost:5000 …")
        procs.append(subprocess.Popen(
            [sys.executable, "app.py"], cwd=ROOT / "frontend",
            env={**env, "FASTAPI_URL": env.get("FASTAPI_URL") or "http://127.0.0.1:8000", "PROXY_COUNT": "0"}))
        wait_for("http://127.0.0.1:5000/healthz")

        print("\n✅ Kasa Health is running")
        print("   App:        http://localhost:5000")
        print("   API docs:   http://localhost:8000/docs   (development only)")
        print("   Readiness:  http://localhost:8000/ready")
        print("   Press Ctrl+C to stop.\n")
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("A server stopped unexpectedly — shutting down the other one.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping…")
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT if os.name != "nt" else signal.CTRL_BREAK_EVENT)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
