"""Creates speech.wav: a looping fake microphone input (silence → voice → silence)."""
import math, struct, wave
from pathlib import Path
rate = 48000
def seg(sec, amp): return [int(amp * math.sin(2 * math.pi * (220 + 80 * math.sin(i / 900)) * i / rate)) for i in range(int(sec * rate))]
# Keep every silence under ~2.8 s so any 3 s recording catches some "speech",
# but long enough (> 1.1 s) for hands-free mode to notice the pause.
samples = seg(1.2, 0) + seg(1.6, 9000) + seg(1.6, 0)
with wave.open(str(Path(__file__).resolve().parent / "speech.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
print("speech.wav written")
