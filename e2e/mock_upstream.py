"""Impersonates xAI, Gemini, OpenAI, Qwen, Khaya, Google Forms and an email API for e2e tests.

Remote control:  POST /control {"grok": {"status": 403, "delay": 0, "message": "..."}, "tts": {"status": 401}}
Sent emails:     GET  /emails
"""
import asyncio, io, math, struct, wave
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()
CALLS = {"llm": 0, "asr": 0, "tts": 0, "translate": 0, "forms": 0}
BEHAVIOUR: dict = {}
EMAILS: list = []

def tone(seconds=0.4, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000*math.sin(2*math.pi*440*i/rate))) for i in range(int(seconds*rate))))
    return buf.getvalue()
TONE = tone()

async def llm(provider: str, req: Request):
    CALLS["llm"] += 1
    CALLS[provider] = CALLS.get(provider, 0) + 1
    body = await req.json()
    q = body["messages"][-1]["content"]
    cfg = BEHAVIOUR.get(provider, {})
    await asyncio.sleep(cfg.get("delay", 0.3))
    if cfg.get("status", 200) != 200:
        return JSONResponse({"error": {"message": cfg.get("message", "error")}}, status_code=cfg["status"])
    if "FAILALL" in q:
        return JSONResponse({"error": {"message": "Access denied. Please check your network settings."}}, status_code=403)
    if "XSSTEST" in q:
        return {"choices": [{"message": {"content": '<img src=x onerror="window.__xss=1">Hello <b>bold</b>.'}}]}
    if "kill myself" in q.lower():
        return {"choices": [{"message": {"content": "[URGENT] I am so sorry you are feeling this way. You matter."}}]}
    return {"choices": [{"message": {"content": f"Puberty is a normal part of growing up ({provider}). Talk to a health worker. Call 1-800-273-8255 anytime."}}]}

@app.post("/v1/chat/completions")
async def grok(req: Request):
    return await llm("grok", req)

@app.post("/{provider}/v1/chat/completions")
async def other(provider: str, req: Request):
    return await llm(provider, req)

@app.post("/control")
async def control(req: Request):
    BEHAVIOUR.clear(); BEHAVIOUR.update(await req.json()); return {"ok": True}

@app.post("/emails")
async def emails(req: Request):
    EMAILS.append(await req.json()); return {"id": f"e{len(EMAILS)}"}

@app.get("/emails")
async def list_emails():
    return EMAILS

@app.post("/v2/translate")
async def translate(req: Request):
    CALLS["translate"] += 1
    if BEHAVIOUR.get("translate", {}).get("status"):
        return JSONResponse({"message": "Access denied due to invalid subscription key."}, status_code=BEHAVIOUR["translate"]["status"])
    b = await req.json()
    return JSONResponse(f"[{b['lang']}] {b['in']}")

@app.post("/asr/v1/transcribe")
async def asr(req: Request):
    CALLS["asr"] += 1
    if BEHAVIOUR.get("asr", {}).get("status"):
        return JSONResponse({"message": "Access denied due to invalid subscription key."}, status_code=BEHAVIOUR["asr"]["status"])
    await req.body()
    await asyncio.sleep(0.2)
    return JSONResponse("Me pɛ sɛ mehu nkokonsuo ho nsɛm")

@app.post("/tts/v2/synthesize")
async def tts(req: Request):
    CALLS["tts"] += 1
    if BEHAVIOUR.get("tts", {}).get("status"):
        return JSONResponse({"message": "Access denied due to invalid subscription key."}, status_code=BEHAVIOUR["tts"]["status"])
    await asyncio.sleep(0.2)
    return Response(TONE, media_type="audio/wav")

@app.post("/forms/{rest:path}")
async def forms(rest: str):
    CALLS["forms"] += 1
    return Response("ok")

@app.get("/calls")
async def calls():
    return CALLS
