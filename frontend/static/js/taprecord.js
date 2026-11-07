// Tap once to start recording, tap again to stop (like ChatGPT).
// Used by Chat voice and Transcribe. The voice agent keeps its own hold-to-talk.
import { mic } from './recorder.js';
import { $, animateFromLevel, toast, toastError } from './ui.js';

export function tapRecorder({
  owner, button, label, viz, controls, timer, cancel,
  idleText, recordingText, maxSeconds = 60, minSeconds = 0.6,
  canStart = () => true, onStart = () => {}, onBlob, onMicError = () => {},
}) {
  const btn = $(button), lbl = $(label), vz = $(viz), ctl = $(controls), tmr = $(timer);
  let recording = false, starting = false, stopViz = null, tick = null, startedAt = 0;

  const show = (on) => {
    recording = on;
    btn.classList.toggle('recording', on);
    btn.parentElement.classList.toggle('is-recording', on);   // lets the layout around the button react
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', on ? recordingText : idleText);
    lbl.textContent = on ? recordingText : idleText;
    vz.classList.toggle('recording', on);
    ctl.classList.toggle('hidden', !on);
    stopViz?.();
    stopViz = on ? animateFromLevel(vz, () => mic.readLevel()) : null;
    clearInterval(tick);
    if (!on) return;
    startedAt = Date.now();
    tmr.textContent = '0:00';
    tick = setInterval(() => {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      tmr.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
      if (s >= maxSeconds) {
        toast(`That's the ${maxSeconds >= 60 ? `${maxSeconds / 60}-minute` : `${maxSeconds}-second`} limit, so I've stopped recording.`, { timeout: 3500 });
        stop(true);
      }
    }, 250);
  };

  const start = async () => {
    if (!canStart()) return;
    onStart();
    starting = true;
    try {
      await mic.start(owner);
    } catch (err) {
      toastError(err);
      onMicError(err);
      return;
    } finally {
      starting = false;
    }
    show(true);
  };

  const stop = (send) => {
    if (!recording) return;
    show(false);
    if (!send) { mic.cancel(owner); return; }
    const blob = mic.stop(owner, { minSeconds });
    if (blob) onBlob(blob);
    else toast('That was very short. Tap the mic, speak, then tap again when you’re done. 🎤', { timeout: 4000 });
  };

  btn.addEventListener('click', () => {
    if (starting) return;           // ignore a second tap while the mic is still opening
    if (recording) stop(true); else start();
  });
  $(cancel).addEventListener('click', () => { stop(false); toast('Recording discarded.', { timeout: 2000 }); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && recording) $(cancel).click(); });

  return {
    get recording() { return recording; },
    cancel: () => stop(false),
    setIdleText: (text) => { idleText = text; if (!recording) lbl.textContent = text; },
  };
}
