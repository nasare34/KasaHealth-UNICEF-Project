// Shared app state, plus a localStorage wrapper that never throws.
export const LANGS = {
  tw:  { name: 'Twi' },
  dag: { name: 'Dagbani' },
  ee:  { name: 'Ewe' },
};

function newSessionId() {
  return (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`).slice(0, 36);
}

export const state = {
  lang: 'tw',
  speaker: 'male_low',
  playbackRate: 1.0,
  sessionId: newSessionId(),
  newSession() { this.sessionId = newSessionId(); },
  get langName() { return LANGS[this.lang]?.name || this.lang; },
};

// localStorage can be full, switched off (private mode) or corrupted. None of that
// should break the app, so I wrap it and quietly fall back.
export const store = {
  get(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch { return false; }
  },
};
