"""
Kasa Health — Flask Frontend
Serves HTML/CSS/JS and proxies all API calls to FastAPI backend.
"""
import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder="templates", static_folder="static")
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://localhost:8000")


@app.route("/")
def index():
    return render_template("index.html")


def _proxy_json(path, timeout=60):
    r = requests.post(f"{FASTAPI_URL}{path}", json=request.json,
                      headers={"Content-Type": "application/json"}, timeout=timeout)
    try:    return jsonify(r.json()), r.status_code
    except: return jsonify({"detail": r.text}), r.status_code


def _proxy_audio(path, timeout=120):
    f = request.files["audio"]
    files = {"audio": (f.filename or "recording.wav", f.read(), f.content_type or "audio/wav")}
    data  = {k: request.form[k] for k in request.form}
    r = requests.post(f"{FASTAPI_URL}{path}", files=files, data=data, timeout=timeout)
    try:    return jsonify(r.json()), r.status_code
    except: return jsonify({"detail": r.text}), r.status_code


@app.route("/api/languages")
def languages():
    r = requests.get(f"{FASTAPI_URL}/languages", timeout=10)
    return jsonify(r.json()), r.status_code

@app.route("/api/asr",           methods=["POST"]) 
def asr():           return _proxy_audio("/asr")

@app.route("/api/chat",          methods=["POST"])
def chat():          return _proxy_json("/chat")

@app.route("/api/tts",           methods=["POST"])
def tts():           return _proxy_json("/tts")

@app.route("/api/translate",     methods=["POST"])
def translate():     return _proxy_json("/translate")

@app.route("/api/full-pipeline", methods=["POST"])
def full_pipeline(): return _proxy_audio("/full-pipeline")

@app.route("/api/agent",         methods=["POST"])
def agent():         return _proxy_audio("/agent")

@app.route("/api/survey",        methods=["POST"])
def survey():        return _proxy_json("/survey")

@app.route("/api/feedback/rating", methods=["POST"])
def feedback_rating():
    return _proxy_json("/feedback/rating", timeout=15)


@app.route("/api/feedback/thumbs", methods=["POST"])
def feedback_thumbs():
    # Multipart form with optional audio file
    files = {}
    if 'audio' in request.files:
        f = request.files['audio']
        files['audio'] = (f.filename, f.read(), f.content_type or 'audio/wav')
    data = {k: request.form[k] for k in request.form}
    r = requests.post(f"{FASTAPI_URL}/feedback/thumbs", files=files, data=data, timeout=60)
    try:    return jsonify(r.json()), r.status_code
    except: return jsonify({'status': 'saved'}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("FLASK_PORT", 5000)), debug=False)
