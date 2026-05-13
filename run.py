"""
run.py — start both FastAPI backend and Flask frontend in one command.
Usage:  python run.py
"""
import subprocess
import sys
import os
import time

def main():
    env = {**os.environ}

    print("Starting FastAPI backend on http://localhost:8000 ...")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=os.path.join(os.path.dirname(__file__), "backend"),
        env=env,
    )

    time.sleep(2)

    print("Starting Flask frontend on http://localhost:5000 ...")
    frontend = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=os.path.join(os.path.dirname(__file__), "frontend"),
        env=env,
    )

    print("\n✅  Both servers running.")
    print("   Frontend: http://localhost:5000")
    print("   Backend API docs: http://localhost:8000/docs")
    print("   Debug ASR key:   http://localhost:8000/debug/asr-languages")
    print("   Debug TTS key:   http://localhost:8000/debug/tts-speakers")
    print("   Debug Trans key: http://localhost:8000/debug/translate-languages")
    print("\nPress Ctrl+C to stop both.\n")

    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        backend.terminate()
        frontend.terminate()

if __name__ == "__main__":
    main()
