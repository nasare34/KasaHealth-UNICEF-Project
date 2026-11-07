// The microphone, shared by chat, transcribe, feedback and the agent.
// I record at the device's own rate and downsample to 16 kHz myself, because
// Firefox won't open a 16 kHz AudioContext.
import { ApiError } from './api.js';

const TARGET_RATE = 16000;
const IDLE_RELEASE_MS = 20000;
const PREROLL_MS = 350;

function micErrorCode(err) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) return 'mic_insecure';
  switch (err && err.name) {
    case 'NotAllowedError': case 'SecurityError': return 'mic_denied';
    case 'NotFoundError': case 'OverconstrainedError': return 'mic_missing';
    case 'NotReadableError': case 'AbortError': return 'mic_busy';
    default: return 'mic_busy';
  }
}

export function downsample(input, inRate, outRate = TARGET_RATE) {
  if (inRate === outRate) return input;
  const ratio = inRate / outRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  if (ratio > 1) {
    // Averaging each window works as a simple low-pass filter, so it doesn't sound harsh.
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio), end = Math.min(input.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = start; j < end; j++) sum += input[j];
      out[i] = sum / Math.max(1, end - start);
    }
  } else {
    for (let i = 0; i < outLen; i++) {
      const pos = i * ratio, j = Math.floor(pos), frac = pos - j;
      out[i] = input[j] * (1 - frac) + (input[j + 1] ?? input[j]) * frac;
    }
  }
  return out;
}

export function encodeWAV(samples, rate = TARGET_RATE) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

class Microphone {
  constructor() {
    this.ctx = null; this.stream = null; this.source = null; this.node = null; this.analyser = null;
    this.capturing = false; this.chunks = []; this.preroll = []; this.prerollSamples = 0;
    this.level = 0; this.listeners = new Set(); this.releaseTimer = null; this.opening = null;
    this.owner = null;
  }

  get isOpen() { return !!this.stream; }

  // Opens the mic (the browser asks permission the first time). Safe to call again.
  async open() {
    clearTimeout(this.releaseTimer);
    if (this.stream) return;
    if (this.opening) return this.opening;
    this.opening = this._open().finally(() => { this.opening = null; });
    return this.opening;
  }

  async _open() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new ApiError('mic_insecure');
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      throw new ApiError(micErrorCode(err));
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx(); // native rate, I downsample myself
    if (this.ctx.state === 'suspended') await this.ctx.resume().catch(() => {});
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.source.connect(this.analyser);

    const onChunk = (samples, rms) => this._onChunk(samples, rms);
    try {
      await this.ctx.audioWorklet.addModule('/static/js/recorder-worklet.js');
      this.node = new AudioWorkletNode(this.ctx, 'kasa-recorder');
      this.node.port.onmessage = (e) => onChunk(e.data.samples, e.data.rms);
      this.source.connect(this.node);
    } catch {
      // Older browsers don't have AudioWorklet, so fall back to ScriptProcessor.
      this.node = this.ctx.createScriptProcessor(2048, 1, 1);
      this.node.onaudioprocess = (e) => {
        const data = new Float32Array(e.inputBuffer.getChannelData(0));
        let sum = 0; for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
        onChunk(data, Math.sqrt(sum / data.length));
      };
      this.source.connect(this.node);
      this.node.connect(this.ctx.destination);
    }
    // in case they unplug the mic or take away permission halfway
    this.stream.getTracks().forEach((t) => t.addEventListener('ended', () => this.close()));
  }

  _onChunk(samples, rms) {
    this.level = rms;
    if (this.capturing) {
      this.chunks.push(samples);
    } else {
      this.preroll.push(samples);
      this.prerollSamples += samples.length;
      const max = (this.ctx?.sampleRate || 48000) * PREROLL_MS / 1000;
      while (this.prerollSamples - (this.preroll[0]?.length || 0) > max) {
        this.prerollSamples -= this.preroll.shift().length;
      }
    }
    this.listeners.forEach((fn) => { try { fn(rms); } catch { /* a broken listener shouldn't stop the audio */ } });
  }

  // Get told the level of every chunk. Call the function it returns to stop listening.
  onLevel(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  // Current mic level from 0 to 1, for the bars.
  readLevel() {
    if (!this.analyser) return 0;
    const data = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
    return Math.min(1, Math.sqrt(sum / data.length) * 4);
  }

  // Start keeping audio. includePreroll keeps the bit just before, so the first word isn't cut off.
  async start(owner, { includePreroll = false } = {}) {
    await this.open();
    if (this.capturing && this.owner !== owner) throw new ApiError('mic_busy');
    this.owner = owner;
    this.chunks = includePreroll ? [...this.preroll] : [];
    this.capturing = true;
  }

  // Stop and hand back a 16 kHz WAV, or null if it was too short to be useful.
  stop(owner, { minSeconds = 0.3 } = {}) {
    if (!this.capturing || (owner && this.owner !== owner)) return null;
    this.capturing = false;
    this.owner = null;
    const chunks = this.chunks; this.chunks = [];
    this.scheduleRelease();
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const rate = this.ctx?.sampleRate || 48000;
    if (total < rate * minSeconds) return null;
    const merged = new Float32Array(total);
    let off = 0; for (const c of chunks) { merged.set(c, off); off += c.length; }
    return encodeWAV(downsample(merged, rate));
  }

  // Throw away what we've recorded so far.
  cancel(owner) {
    if (owner && this.owner !== owner) return;
    this.capturing = false; this.owner = null; this.chunks = [];
    this.scheduleRelease();
  }

  capturedSeconds() {
    const rate = this.ctx?.sampleRate || 48000;
    return this.chunks.reduce((n, c) => n + c.length, 0) / rate;
  }

  // Let go of the mic after a quiet spell, so the browser's mic light turns off.
  scheduleRelease(ms = IDLE_RELEASE_MS) {
    clearTimeout(this.releaseTimer);
    this.releaseTimer = setTimeout(() => { if (!this.capturing && !this.keepOpen) this.close(); }, ms);
  }

  close() {
    clearTimeout(this.releaseTimer);
    this.capturing = false; this.owner = null; this.chunks = []; this.preroll = []; this.prerollSamples = 0;
    try { this.node?.disconnect(); this.source?.disconnect(); this.analyser?.disconnect(); } catch { /* ignore */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    this.ctx = this.stream = this.source = this.node = this.analyser = null;
    this.level = 0;
  }
}

export const mic = new Microphone();
