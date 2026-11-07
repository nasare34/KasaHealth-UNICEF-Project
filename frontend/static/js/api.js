// Every network call goes through api(). It deals with timeouts and retries, and
// turns errors into friendly messages, so raw server text never reaches the screen.
// New error code? Add a friendly line to FRIENDLY below.
export const API_BASE = '/api';

export const FRIENDLY = {
  bad_request:           "Hmm, something about that didn't look quite right. Could you try again?",
  unsupported_language:  "That language isn't available yet. Please pick Twi, Dagbani or Ewe.",
  message_too_long:      "That's a lot to take in at once! Could you ask a shorter question?",
  empty_message:         "I'd love to help — please type or say your question first.",
  audio_too_short:       "I didn't catch that. Please hold the button a little longer and speak clearly.",
  audio_too_large:       "That recording is a bit too long for me. Please try a shorter question.",
  too_large:             "That recording is a bit too long for me. Please try a shorter question.",
  audio_invalid:         "I couldn't read that recording. Please try speaking again.",
  no_speech:             "I couldn't hear any words that time. Please try again a little closer to the mic.",
  rate_limited:          "You're asking questions faster than I can answer! Please take a short breath and try again in a moment.",
  busy:                  "Lots of people are chatting with Kasa right now. Please try again in a few seconds — thank you for your patience!",
  llm_unavailable:       "I'm having a little trouble thinking of an answer right now. Please try again shortly. If it's urgent, SHEplus Ghana is there for you: 055 054 5672 / 0800 00 11 22.",
  asr_unavailable:       "I'm having trouble hearing right now. You can try again, or type your question instead.",
  tts_unavailable:       "I couldn't prepare the voice this time, but you can still read my answer.",
  translate_unavailable: "I'm having trouble translating right now. Please try again in a moment.",
  timeout:               "That took longer than expected. Please try again — it usually works on the next go.",
  backend_unreachable:   "Kasa is waking up or briefly unavailable. Please try again in a few seconds.",
  offline:               "It looks like you're offline. Please check your internet connection and try again.",
  network:               "We couldn't reach Kasa just now. Please check your connection and try again.",
  bad_phone:             "That phone number doesn't look quite right. Please check it and try again.",
  forbidden:             "Sorry, that isn't available.",
  not_found:             "We couldn't find what you were looking for.",
  mic_denied:            "Kasa needs your microphone to listen. Please allow microphone access in your browser settings, then try again. You can also type your question.",
  mic_missing:           "We couldn't find a microphone. Please connect one, or type your question instead.",
  mic_busy:              "Your microphone is being used by another app. Please close it and try again.",
  mic_insecure:          "Voice needs a secure (https) connection. You can still type your question.",
  playback_blocked:      "Tap play to hear the answer.",
  internal:              "Something unexpected happened. Please try again — we're sorry about that.",
};

const RETRYABLE = new Set(['busy', 'timeout', 'backend_unreachable', 'network',
  'llm_unavailable', 'asr_unavailable', 'tts_unavailable', 'translate_unavailable', 'internal']);

export function friendlyMessage(code) {
  return FRIENDLY[code] || FRIENDLY.internal;
}

export class ApiError extends Error {
  constructor(code, { status = 0, requestId = '', retryAfter = 0 } = {}) {
    super(friendlyMessage(code));
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
    this.retryable = RETRYABLE.has(code);
  }
}

export function isAbort(err) {
  return err && (err.name === 'AbortError' || err.code === 'aborted');
}

function codeForStatus(status) {
  if (status === 429) return 'rate_limited';
  if (status === 413) return 'too_large';
  if (status === 503) return 'busy';
  if (status === 504) return 'timeout';
  if (status === 404) return 'not_found';
  if (status === 403) return 'forbidden';
  if (status >= 500) return 'internal';
  return 'bad_request';
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const t = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
});

async function once(path, { method, json, form, timeoutMs, signal }) {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  const onAbort = () => ctrl.abort();
  signal?.addEventListener('abort', onAbort, { once: true });

  const init = { method, signal: ctrl.signal, headers: {} };
  if (json !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
  if (form !== undefined) init.body = form;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (err) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (timedOut) throw new ApiError('timeout');
    throw new ApiError(navigator.onLine === false ? 'offline' : 'network');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }

  let body = null;
  try { body = await res.json(); } catch { body = null; }
  if (res.ok && body && body.ok !== false) return body;

  const code = (body && body.error && body.error.code) || codeForStatus(res.status);
  throw new ApiError(code, {
    status: res.status,
    requestId: body?.error?.request_id || res.headers.get('X-Request-ID') || '',
    retryAfter: parseInt(res.headers.get('Retry-After') || '0', 10) || 0,
  });
}

// Use it like api('/chat', { json: {...} }). Other options: method, form, timeoutMs,
// retries and signal (an AbortController signal, so you can cancel).
export async function api(path, { method, json, form, timeoutMs = 60000, retries = 1, signal } = {}) {
  method = method || (json !== undefined || form !== undefined ? 'POST' : 'GET');
  let attempt = 0;
  for (;;) {
    try {
      return await once(path, { method, json, form, timeoutMs, signal });
    } catch (err) {
      if (isAbort(err) || !(err instanceof ApiError) || !err.retryable || attempt >= retries) {
        if (err instanceof ApiError && err.requestId) console.warn(`[Kasa] ${path} failed: ${err.code} (ref ${err.requestId})`);
        throw err;
      }
      attempt += 1;
      const backoff = err.retryAfter ? Math.min(err.retryAfter * 1000, 8000)
                                     : Math.min(800 * 2 ** (attempt - 1), 5000) * (0.6 + Math.random() * 0.8);
      await sleep(backoff, signal);
    }
  }
}
