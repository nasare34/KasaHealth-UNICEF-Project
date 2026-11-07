// Plays answers back. One <audio> element, unlocked on the first tap, so mobile
// browsers let us play later without asking for another tap.
import { ApiError } from './api.js';
import { encodeWAV } from './recorder.js';

function b64ToBlob(b64, fmt) {
  const mime = fmt === 'mp3' ? 'audio/mpeg' : `audio/${fmt || 'wav'}`;
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

class Player {
  constructor() {
    this.el = new Audio();
    this.el.preload = 'auto';
    this.rate = 1.0;
    this.token = 0;          // goes up on every play/stop, so stale callbacks know to stop
    this.url = null;
    this.unlocked = false;
    this.onStateChange = null;
    const unlock = () => this.unlock();
    window.addEventListener('pointerdown', unlock, { once: true, capture: true });
    window.addEventListener('keydown', unlock, { once: true, capture: true });
  }

  get playing() { return !this.el.paused && !this.el.ended; }

  unlock() {
    if (this.unlocked) return;
    this.unlocked = true;
    const silent = URL.createObjectURL(encodeWAV(new Float32Array(160)));
    this.el.src = silent;
    this.el.play().then(() => this.el.pause()).catch(() => {}).finally(() => URL.revokeObjectURL(silent));
  }

  setRate(rate) {
    this.rate = rate;
    this.el.playbackRate = rate;
  }

  // Play segments one after another. Resolves false if stopped part-way;
  // throws 'playback_blocked' if the browser won't let us play yet.
  async play(segments) {
    const list = (Array.isArray(segments) ? segments : [segments]).filter((s) => s && s.audio_base64);
    this.stop();
    const myToken = ++this.token;
    this._emit(true);
    try {
      for (const seg of list) {
        if (myToken !== this.token) return false;
        await this._playOne(seg, myToken);
      }
      return myToken === this.token;
    } finally {
      if (myToken === this.token) this._emit(false);
    }
  }

  _playOne(seg, myToken) {
    return new Promise((resolve, reject) => {
      this._revoke();
      this.url = URL.createObjectURL(b64ToBlob(seg.audio_base64, seg.format));
      this.el.src = this.url;
      this.el.playbackRate = this.rate;
      const done = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); resolve(); };  // a broken clip shouldn't stop the rest
      const cleanup = () => {
        this.el.removeEventListener('ended', done);
        this.el.removeEventListener('error', fail);
        this.el.removeEventListener('kasa-stop', done);
      };
      this.el.addEventListener('ended', done);
      this.el.addEventListener('error', fail);
      this.el.addEventListener('kasa-stop', done);
      this.el.play().catch((err) => {
        cleanup();
        if (myToken !== this.token) return resolve();
        if (err && err.name === 'NotAllowedError') reject(new ApiError('playback_blocked'));
        else resolve();
      });
    });
  }

  stop() {
    this.token++;
    try { this.el.pause(); } catch { /* ignore */ }
    this.el.dispatchEvent(new Event('kasa-stop'));
    this._revoke();
    this._emit(false);
  }

  _revoke() {
    if (this.url) { URL.revokeObjectURL(this.url); this.url = null; }
  }

  _emit(isPlaying) {
    if (this.onStateChange) { try { this.onStateChange(isPlaying); } catch { /* ignore */ } }
  }
}

export const player = new Player();

export function downloadAudio(b64, fmt, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b64ToBlob(b64, fmt));
  a.download = `${name}.${fmt || 'wav'}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
