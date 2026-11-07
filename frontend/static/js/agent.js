// Live voice agent.
// Two modes: hold-to-talk, or hands-free where it listens for when you start and
// stop talking. Each turn gets a number so a late reply can't overwrite a newer one.
import { api, ApiError, friendlyMessage, isAbort } from './api.js';
import { makeThumbs } from './feedback.js';
import { makeCrisisCard } from './safety.js';
import { player } from './player.js';
import { mic } from './recorder.js';
import { state, store } from './state.js';
import { $, animateFromLevel, disclaimerNode, el, holdToTalk, resetBars, setBars } from './ui.js';

const OWNER = 'agent';
const S = { IDLE: 'idle', LISTENING: 'listening', RECORDING: 'recording', THINKING: 'thinking', SPEAKING: 'speaking' };

// Voice detection settings. One "chunk" is about 40 ms. If it cuts people off
// too early, raise endSilenceMs; if background noise sets it off, raise noiseMultiplier.
const VAD = {
  calibrationChunks: 8,        // ~350 ms to learn how noisy the room is
  minThreshold: 0.012,         // never trigger below this level
  noiseMultiplier: 3.0,        // speech must be this many times louder than background
  startChunks: 3,              // ~130 ms of sound before we consider it speech
  endSilenceMs: 1100,          // this much quiet and I treat your turn as done
  minSpeechMs: 350,            // shorter blips are treated as noise
  maxUtteranceS: 25,           // hard limit per turn
  idleTimeoutMs: 45000,        // pause hands-free after this long without speech
};
const MAX_HISTORY_MESSAGES = 16;
const MAX_CONSECUTIVE_ERRORS = 3;
const WELCOME = "👋 Hi! I'm Kasa, your health companion. Ask me anything about your health — I'll understand and reply in your language.";

const ui = {};
const agent = {
  status: S.IDLE,
  mode: store.get('kasa_agent_mode', 'ptt'),
  session: false,
  turnId: 0,
  inflight: null,
  history: [],
  errors: 0,
  stopViz: null,
  unsubscribeVad: null,
  wakeLock: null,
  thinkingTimer: null,
  thinkingStarted: 0,
  hint: '',
  vad: null,
  pauseAfterReply: false,
};

/* --- Rendering --- */
function setStatus(status, hint) {
  agent.status = status;
  if (hint !== undefined) agent.hint = hint;
  render();
}

function render() {
  const { status, mode, session } = agent;
  const hf = mode === 'handsfree';

  const pill = {
    [S.IDLE]: ['Ready', ''],
    [S.LISTENING]: ['Listening…', 'listening'],
    [S.RECORDING]: ['Hearing you…', 'listening'],
    [S.THINKING]: [thinkingText(), 'thinking'],
    [S.SPEAKING]: ['Speaking…', 'speaking'],
  }[status];
  ui.pill.textContent = pill[0];
  ui.pill.className = `agent-status-pill${pill[1] ? ' agent-status-' + pill[1] : ''}`;

  const label = hf ? {
    [S.IDLE]: 'Tap to start talking',
    [S.LISTENING]: 'Listening — tap to stop',
    [S.RECORDING]: 'Tap to send now',
    [S.THINKING]: 'Thinking — tap to cancel',
    [S.SPEAKING]: 'Tap to interrupt',
  } : {
    [S.IDLE]: 'Hold to talk',
    [S.LISTENING]: 'Hold to talk',
    [S.RECORDING]: 'Release to send',
    [S.THINKING]: 'Hold to ask again',
    [S.SPEAKING]: 'Hold to interrupt',
  };
  ui.micLabel.textContent = label[status];
  ui.micBtn.classList.toggle('recording', status === S.RECORDING);
  ui.micBtn.classList.toggle('listening', status === S.LISTENING);
  ui.micBtn.classList.toggle('session', hf && session);
  ui.micBtn.setAttribute('aria-pressed', String(hf && session));
  ui.avatar.classList.toggle('speaking', status === S.SPEAKING);
  ui.avatar.classList.toggle('thinking', status === S.THINKING);
  ui.avatar.classList.toggle('listening', status === S.LISTENING || status === S.RECORDING);

  ui.modePtt.classList.toggle('active', !hf);
  ui.modeHands.classList.toggle('active', hf);
  ui.hint.textContent = agent.hint || defaultHint();
  ui.hint.title = ui.hint.textContent;

  // The bars follow the real mic level while listening, and just sway while speaking.
  agent.stopViz?.();
  agent.stopViz = null;
  if (status === S.LISTENING || status === S.RECORDING) {
    agent.stopViz = animateFromLevel(ui.viz, () => mic.readLevel() * (status === S.LISTENING ? 0.6 : 1));
  } else if (status === S.SPEAKING) {
    let on = true;
    const tick = () => { if (!on) return; setBars(ui.viz, 0.25 + Math.random() * 0.3); setTimeout(tick, 110); };
    tick();
    agent.stopViz = () => { on = false; resetBars(ui.viz); };
  } else {
    resetBars(ui.viz);
  }
}

function defaultHint() {
  const small = window.innerWidth <= 600;   // phones get a shorter version that fits the bar
  if (agent.mode === 'handsfree') {
    return small ? 'Just talk — I reply when you pause.'
                 : 'Hands-free: just talk naturally — Kasa replies when you pause. Press Space to tap.';
  }
  return small ? 'Hold the button while you speak.'
               : 'Hold the button (or the Space bar) while you speak, then let go.';
}

function thinkingText() {
  if (!agent.thinkingStarted) return 'Thinking…';
  const s = Math.floor((Date.now() - agent.thinkingStarted) / 1000);
  if (s >= 20) return 'Still working…';
  return s >= 2 ? `Thinking… ${s}s` : 'Thinking…';
}

/* --- Conversation bubbles --- */
function scrollConv() {
  requestAnimationFrame(() => { ui.conv.scrollTop = ui.conv.scrollHeight; });
}

function addSystemBubble(text) {
  ui.conv.appendChild(el('div', { class: 'agent-bubble-wrap agent-bubble-system' },
    el('div', { class: 'agent-bubble agent-bubble-system' }, text)));
  scrollConv();
}

function addUserBubble(text) {
  ui.conv.appendChild(el('div', { class: 'agent-bubble-wrap agent-bubble-user' },
    el('div', { class: 'agent-bubble-label' }, `👤 ${state.langName}`),
    el('div', { class: 'agent-bubble agent-bubble-user' }, text)));
  scrollConv();
}

function addAgentBubble(d) {
  const lang = d.language || state.lang;
  const actions = el('div', { class: 'msg-actions' });
  let replayBtn = null;
  if (d.audio_segments?.length) {
    replayBtn = el('button', { type: 'button', class: 'action-btn play-btn' }, '🔁 Play again');
    replayBtn.addEventListener('click', () => {
      interrupt();
      speak(d.audio_segments, { resumeAfter: false });
    });
    actions.appendChild(replayBtn);
  }
  const wrap = el('div', { class: 'agent-bubble-wrap agent-bubble-agent' },
    el('div', { class: 'agent-bubble-label' }, '🤖 Kasa'),
    el('div', { class: 'agent-bubble agent-bubble-agent' }, d.answer_local),
    d.answer_english && d.answer_english !== d.answer_local
      ? el('div', { class: 'agent-bubble-english' }, d.answer_english) : null,
    d.notice ? el('div', { class: 'soft-notice' }, `💬 ${d.notice}`) : null,
    disclaimerNode(true),
    d.safety?.flagged
      ? makeCrisisCard(d.safety, { questionOriginal: d.transcript || '', questionEnglish: d.question_english || '' })
      : null,
    actions,
    makeThumbs({ questionOriginal: d.transcript || '', questionEnglish: d.question_english || '',
                 english: d.answer_english || '' }, d.answer_local, lang, 'agent'));
  ui.conv.appendChild(wrap);
  scrollConv();
  return replayBtn;
}

/* --- Turn handling --- */
function startThinkingTimer() {
  agent.thinkingStarted = Date.now();
  clearInterval(agent.thinkingTimer);
  agent.thinkingTimer = setInterval(() => { if (agent.status === S.THINKING) render(); }, 1000);
}

function stopThinkingTimer() {
  clearInterval(agent.thinkingTimer);
  agent.thinkingTimer = null;
  agent.thinkingStarted = 0;
}

async function sendTurn(blob) {
  const id = ++agent.turnId;
  agent.inflight?.abort();
  const ctrl = new AbortController();
  agent.inflight = ctrl;
  startThinkingTimer();
  setStatus(S.THINKING, '');

  const form = new FormData();
  form.append('audio', blob, 'turn.wav');
  form.append('language', state.lang);
  form.append('speaker', state.speaker);
  form.append('history', JSON.stringify(agent.history.slice(-MAX_HISTORY_MESSAGES)));
  form.append('session_id', state.sessionId);

  let d;
  try {
    d = await api('/agent', { form, timeoutMs: 75000, retries: 1, signal: ctrl.signal });
  } catch (err) {
    if (isAbort(err) || id !== agent.turnId) return;      // cancelled, or replaced by a newer turn
    stopThinkingTimer();
    agent.inflight = null;
    return handleTurnError(err);
  }
  if (id !== agent.turnId) return;                         // a newer turn started meanwhile
  stopThinkingTimer();
  agent.inflight = null;
  agent.errors = 0;

  if (d.status === 'no_speech') {
    if (agent.session) {
      agent.hint = "I didn't catch that — go ahead, I'm listening.";
      return listen();
    }
    addSystemBubble(d.message_local && d.message_local !== d.message_english
      ? `${d.message_local}\n(${d.message_english})` : friendlyMessage('no_speech'));
    if (d.audio_segments?.length) return speak(d.audio_segments, { resumeAfter: false });
    return setStatus(S.IDLE);
  }

  addUserBubble(d.transcript);
  const replayBtn = addAgentBubble(d);
  // After a crisis reply I stop listening, so they can read and use the support options.
  agent.pauseAfterReply = Boolean(d.safety?.flagged && agent.session);
  agent.history.push(d.history_entry_user, d.history_entry_agent);
  agent.history = agent.history.filter(Boolean).slice(-MAX_HISTORY_MESSAGES);

  if (d.audio_segments?.length) return speak(d.audio_segments, { resumeAfter: true, replayBtn });
  afterReply();
}

function handleTurnError(err) {
  agent.errors += 1;
  addSystemBubble(err instanceof ApiError ? err.message : friendlyMessage('internal'));
  if (!agent.session) return setStatus(S.IDLE);
  if (err.code === 'rate_limited' || agent.errors >= MAX_CONSECUTIVE_ERRORS) {
    stopSession("I'm taking a short pause. Tap the mic whenever you're ready to try again.");
    return;
  }
  setStatus(S.THINKING, 'Getting ready to listen again…');
  setTimeout(() => { if (agent.session && agent.status === S.THINKING && !agent.inflight) listen(); }, 1500);
}

async function speak(segments, { resumeAfter, replayBtn } = {}) {
  setStatus(S.SPEAKING, agent.mode === 'handsfree' ? 'Tap or press Space to interrupt.' : '');
  try {
    const finished = await player.play(segments);
    if (!finished) return;                       // interrupted; whoever interrupted has already set the new state
  } catch (err) {
    if (err instanceof ApiError && err.code === 'playback_blocked') {
      // The browser blocked autoplay, so offer a one-tap play instead.
      agent.hint = 'Tap “Play again” to hear the answer.';
      replayBtn?.classList.add('pulse-once');
    }
  }
  if (resumeAfter) afterReply(); else if (agent.session) listen(); else setStatus(S.IDLE);
}

function afterReply() {
  if (agent.pauseAfterReply) {
    agent.pauseAfterReply = false;
    stopSession("I've paused listening so you can read the support options below. Tap the mic whenever you're ready. 💚");
    return;
  }
  if (agent.session) listen();
  else setStatus(S.IDLE, '');
}

// Stops whatever Kasa is doing (talking or thinking) but keeps the session going.
function interrupt() {
  if (agent.status === S.SPEAKING) player.stop();
  if (agent.inflight) { agent.turnId++; agent.inflight.abort(); agent.inflight = null; stopThinkingTimer(); }
}

/* --- Push-to-talk --- */
let pttTimer = null;

async function pttPress() {
  interrupt();
  try {
    setStatus(S.RECORDING, '');
    await mic.start(OWNER);
    if (agent.status !== S.RECORDING) { mic.cancel(OWNER); return; }  // released before the mic was ready
    clearTimeout(pttTimer);
    pttTimer = setTimeout(() => { if (agent.status === S.RECORDING) pttRelease(); }, VAD.maxUtteranceS * 1000);
  } catch (err) {
    setStatus(S.IDLE);
    addSystemBubble(err.message || friendlyMessage('mic_busy'));
  }
}

function pttRelease() {
  clearTimeout(pttTimer);
  if (agent.status !== S.RECORDING) return;
  const blob = mic.stop(OWNER, { minSeconds: 0.4 });
  if (!blob) return setStatus(S.IDLE, 'Hold the button down the whole time you are speaking.');
  sendTurn(blob);
}

/* --- Hands-free session --- */
async function startSession() {
  try {
    await mic.open();
  } catch (err) {
    addSystemBubble(err.message || friendlyMessage('mic_busy'));
    return setStatus(S.IDLE);
  }
  mic.keepOpen = true;
  agent.session = true;
  agent.errors = 0;
  try { agent.wakeLock = await navigator.wakeLock?.request('screen'); } catch { agent.wakeLock = null; }
  listen();
}

function stopSession(message) {
  agent.session = false;
  agent.unsubscribeVad?.();
  agent.unsubscribeVad = null;
  interrupt();
  mic.cancel(OWNER);
  mic.keepOpen = false;
  mic.scheduleRelease(4000);
  agent.wakeLock?.release?.().catch(() => {});
  agent.wakeLock = null;
  if (message) addSystemBubble(message);
  setStatus(S.IDLE, '');
}

function listen() {
  if (!agent.session) return setStatus(S.IDLE);
  mic.cancel(OWNER);
  agent.vad = { chunks: 0, noise: 0, above: 0, threshold: VAD.minThreshold,
                startedAt: Date.now(), speechAt: 0, lastVoiceAt: 0, starting: false };
  if (!agent.unsubscribeVad) agent.unsubscribeVad = mic.onLevel(vadTick);
  setStatus(S.LISTENING);
}

function vadTick(rms) {
  const v = agent.vad;
  if (!agent.session || !v) return;
  const now = Date.now();

  if (agent.status === S.LISTENING) {
    if (v.chunks < VAD.calibrationChunks) {                  // learn the background noise
      v.noise = (v.noise * v.chunks + rms) / (v.chunks + 1);
      v.chunks += 1;
      v.threshold = Math.max(VAD.minThreshold, v.noise * VAD.noiseMultiplier);
      return;
    }
    if (rms > v.threshold) {
      v.above += 1;
    } else {
      v.above = 0;
      v.noise = v.noise * 0.97 + rms * 0.03;                 // adapt slowly to changing rooms
      v.threshold = Math.max(VAD.minThreshold, v.noise * VAD.noiseMultiplier);
    }
    if (v.above >= VAD.startChunks && !v.starting) {
      v.starting = true;
      v.speechAt = v.lastVoiceAt = now;
      mic.start(OWNER, { includePreroll: true })
        .then(() => {
          if (agent.session && agent.status === S.LISTENING) setStatus(S.RECORDING, '');
          else mic.cancel(OWNER);                              // session ended while starting
        })
        .catch(() => listen())
        .finally(() => { if (agent.vad) agent.vad.starting = false; });
      return;
    }
    if (now - v.startedAt > VAD.idleTimeoutMs) {
      stopSession("I'm still here whenever you need me. Tap the mic to continue our chat. 💚");
    }
    return;
  }

  if (agent.status === S.RECORDING) {
    if (rms > v.threshold * 0.8) v.lastVoiceAt = now;
    if (now - v.lastVoiceAt > VAD.endSilenceMs || mic.capturedSeconds() > VAD.maxUtteranceS) endUtterance();
  }
}

function endUtterance() {
  const v = agent.vad;
  const spokeFor = v ? v.lastVoiceAt - v.speechAt : 0;
  if (spokeFor < VAD.minSpeechMs) {                          // a cough, a door, a click
    mic.cancel(OWNER);
    return listen();
  }
  const blob = mic.stop(OWNER, { minSeconds: 0.3 });
  if (!blob) return listen();
  sendTurn(blob);
}

function handsFreeTap() {
  if (!agent.session) return startSession();
  switch (agent.status) {
    case S.LISTENING: return stopSession();
    case S.RECORDING: return endUtterance();
    case S.THINKING: interrupt(); return listen();
    case S.SPEAKING: interrupt(); return listen();
    default: return listen();
  }
}

/* --- Public API --- */
function onPress() {
  if (agent.mode === 'handsfree') handsFreeTap();
  else pttPress();
}

function onRelease() {
  if (agent.mode === 'ptt') pttRelease();
}

function setMode(mode) {
  if (mode === agent.mode) return;
  stopAgent();
  agent.mode = mode;
  store.set('kasa_agent_mode', mode);
  agent.hint = '';
  render();
}

function clearConversation() {
  stopAgent();
  agent.history = [];
  ui.conv.replaceChildren(el('div', { class: 'agent-bubble-wrap agent-bubble-system' },
    el('div', { class: 'agent-bubble agent-bubble-system' }, WELCOME)));
}

// Stops everything. I call this when you leave the tab, clear the chat or switch mode.
export function stopAgent() {
  clearTimeout(pttTimer);
  if (agent.session) stopSession();
  else {
    interrupt();
    mic.cancel(OWNER);
    setStatus(S.IDLE, '');
  }
}

export function initAgent() {
  Object.assign(ui, {
    micBtn: $('agentMicBtn'), micLabel: $('agentMicLabel'), viz: $('agentViz'),
    conv: $('agentConversation'), pill: $('agentStatusPill'), avatar: $('agentAvatar'),
    modePtt: $('agentModePtt'), modeHands: $('agentModeHands'), hint: $('agentHint'),
  });

  holdToTalk(ui.micBtn, onPress, onRelease);
  ui.modePtt.addEventListener('click', () => setMode('ptt'));
  ui.modeHands.addEventListener('click', () => setMode('handsfree'));
  $('agentClearBtn').addEventListener('click', clearConversation);

  // Space bar works as the mic button on this tab (but not while typing).
  const spaceOwnsKey = (e) => {
    if (e.code !== 'Space' || !$('tab-agent').classList.contains('active')) return false;
    const t = e.target;
    if (t === ui.micBtn || t.closest?.('input, textarea, select, [contenteditable="true"], .modal-overlay:not(.hidden)')) return false;
    return true;
  };
  document.addEventListener('keydown', (e) => {
    if (!spaceOwnsKey(e)) {
      if (e.key === 'Escape' && $('tab-agent').classList.contains('active') && agent.status !== S.IDLE) stopAgent();
      return;
    }
    e.preventDefault();
    if (!e.repeat) onPress();
  });
  document.addEventListener('keyup', (e) => { if (spaceOwnsKey(e)) { e.preventDefault(); onRelease(); } });

  // Pause when they leave the page. Saves battery and stops us recording an empty room.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) return;
    if (agent.session) stopSession('Paused while you were away. Tap the mic to continue. 💚');
    else if (agent.status === S.RECORDING) { mic.cancel(OWNER); setStatus(S.IDLE); }
  });

  render();
}
