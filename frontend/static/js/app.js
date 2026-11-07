// Kasa Health main script. Each module does one job; this file wires them together.
import { api, ApiError, friendlyMessage, isAbort } from './api.js';
import { initAgent, stopAgent } from './agent.js';
import { initFeedback, makeThumbs } from './feedback.js';
import { makeCrisisCard } from './safety.js';
import { downloadAudio, player } from './player.js';
import { mic } from './recorder.js';
import { tapRecorder } from './taprecord.js';
import { LANGS, state, store } from './state.js';
import { $, closeModal, disclaimerNode, downloadText, el,
         openModal, setStatus, toast, toastError } from './ui.js';

const QUICK_PROMPTS = {
  puberty:       'What changes happen to my body during puberty?',
  contraception: 'What contraception options are available for young people?',
  sti:           'How can I protect myself from STIs and HIV?',
  pregnancy:     'What are the signs of pregnancy and what should I do?',
  menstruation:  'Is it normal to have irregular periods?',
  consent:       'What is consent and how do I know if I am in a safe relationship?',
};
const SESSIONS_KEY = 'kasa_sessions';
const TRANSCRIPTS_KEY = 'kasa_transcriptions';
const isMobile = () => window.innerWidth < 900;

// --- Layout: tabs, sidebar, settings ---
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const leavingAgent = $('tab-agent').classList.contains('active') && btn.dataset.tab !== 'agent';
      if (leavingAgent) stopAgent();
      recorders.forEach((r) => r.cancel());   // never keep recording in a tab you've left
      player.stop();
      document.querySelectorAll('.tab-btn').forEach((b) => { b.classList.remove('active'); b.setAttribute('aria-selected', 'false'); });
      document.querySelectorAll('.tab-content').forEach((c) => c.classList.remove('active'));
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      $(`tab-${btn.dataset.tab}`).classList.add('active');
    });
  });
}

/* --- Sidebar: drawer on phones/tablets, collapsible panel on desktop --- */
function setDrawer(open) {
  $('sidebar').classList.toggle('open', open);
  $('sidebarBackdrop').classList.toggle('show', open);
  $('menuBtn').setAttribute('aria-expanded', String(open));
}

function toggleSidebar() {
  if (isMobile()) { setDrawer(!$('sidebar').classList.contains('open')); return; }
  const collapsed = !document.documentElement.classList.contains('sidebar-collapsed');
  document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
  $('menuBtn').setAttribute('aria-expanded', String(!collapsed));
  store.set('kasa_sidebar_collapsed', collapsed);
}

function closeSidebarOnMobile() { if (isMobile()) setDrawer(false); }

/* --- Theme: light (green) / dark --- */
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  $('themeToggle').textContent = theme === 'light' ? '🌙' : '☀️';
  $('themeToggle').title = theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
  $('themeColorMeta')?.setAttribute('content', theme === 'light' ? '#f4faf5' : '#0d1117');
}

function initTheme() {
  applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
  $('themeToggle').addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    applyTheme(next);
    store.set('kasa_theme', next);
  });
}

function applyLanguageLabels() {
  const name = state.langName;
  $('currentLangBadge').textContent = `🇬🇭 ${name}`;
  ['hintLang', 'textHintLang', 'transLangLabel', 'transLangBadge'].forEach((id) => { $(id).textContent = name; });
}

function initSettings() {
  $('menuBtn').addEventListener('click', toggleSidebar);
  $('sidebarClose').addEventListener('click', () => setDrawer(false));
  $('sidebarBackdrop').addEventListener('click', () => setDrawer(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('sidebar').classList.contains('open')) setDrawer(false); });
  window.addEventListener('resize', () => { if (!isMobile()) setDrawer(false); });
  $('menuBtn').setAttribute('aria-expanded', String(isMobile() ? false : !document.documentElement.classList.contains('sidebar-collapsed')));

  document.querySelectorAll('.lang-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.lang-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.lang = btn.dataset.lang;
      store.set('kasa_lang', state.lang);
      applyLanguageLabels();
      closeSidebarOnMobile();
    });
  });

  const savedLang = store.get('kasa_lang', 'tw');
  if (LANGS[savedLang]) {
    state.lang = savedLang;
    document.querySelectorAll('.lang-btn').forEach((b) => b.classList.toggle('active', b.dataset.lang === savedLang));
  }
  applyLanguageLabels();


  // Start from whichever voice button is marked active in the HTML.
  const activeSpeaker = document.querySelector('.speaker-btn.active:not([disabled])');
  if (activeSpeaker) state.speaker = activeSpeaker.dataset.speaker;
  document.querySelectorAll('.speaker-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      document.querySelectorAll('.speaker-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.speaker = btn.dataset.speaker;
    });
  });

  const slider = $('speedSlider');
  slider.addEventListener('input', () => {
    state.playbackRate = parseFloat(slider.value) || 1;
    $('speedLabel').textContent = `${state.playbackRate.toFixed(1)}×`;
    player.setRate(state.playbackRate);
  });

  document.querySelectorAll('.topic-chip').forEach((chip) => chip.addEventListener('click', () => {
    selectTab('chat');
    sendTextMessage(QUICK_PROMPTS[chip.dataset.topic]);
    closeSidebarOnMobile();
  }));
  document.querySelectorAll('.welcome-card').forEach((card) => card.addEventListener('click', () => sendTextMessage(card.dataset.prompt)));

  $('newChatBtn').addEventListener('click', () => { newConversation(); closeSidebarOnMobile(); });
  $('topbarSurveyBtn').addEventListener('click', () => $('surveyBtn').click());
  $('topbarHistoryBtn').addEventListener('click', () => $('historyBtn').click());
}

function selectTab(name) { document.querySelector(`.tab-btn[data-tab="${name}"]`)?.click(); }

// --- Chat ---
const recorders = [];   // tap-to-record controls (chat + transcribe)

const chat = {
  history: [],      // English turns sent to the model
  display: [],      // what the user saw, for saved sessions
  busy: false,
  ctrl: null,
};

function showWelcome(show) { $('welcome').style.display = show ? 'flex' : 'none'; }

function scrollToBottom() {
  requestAnimationFrame(() => { const area = $('chatArea'); area.scrollTop = area.scrollHeight; });
}

function newConversation() {
  saveSession();
  chat.ctrl?.abort();
  chat.ctrl = null;
  chat.busy = false;
  chat.history = [];
  chat.display = [];
  state.newSession();
  player.stop();
  $('messages').replaceChildren();
  showWelcome(true);
  setStatus('ok');
}

function showTyping(stage) {
  let node = $('typingIndicator');
  if (!node) {
    node = el('div', { id: 'typingIndicator', class: 'message bot typing-indicator', 'aria-live': 'polite' },
      el('div', { class: 'msg-avatar' }, '🏥'),
      el('div', { class: 'msg-content' },
        el('div', { class: 'msg-bubble' },
          el('div', { class: 'typing-dot' }), el('div', { class: 'typing-dot' }), el('div', { class: 'typing-dot' })),
        el('div', { class: 'typing-stage', id: 'typingStage' })));
    $('messages').appendChild(node);
  }
  $('typingStage').textContent = stage || '';
  scrollToBottom();
}

function removeTyping() { $('typingIndicator')?.remove(); }

function addUserMessage(text, { isVoice = false } = {}) {
  $('messages').appendChild(el('div', { class: 'message user' },
    el('div', { class: 'msg-avatar' }, '👤'),
    el('div', { class: 'msg-content' },
      el('div', { class: 'msg-bubble' }, text),
      isVoice ? el('div', { class: 'msg-lang-note' }, '🎤 Spoken in ', el('strong', {}, state.langName)) : null)));
  scrollToBottom();
}

// When something goes wrong I show it as a gentle Kasa message, with a retry button if it helps.
function addKindError(err, onRetry) {
  const message = err instanceof ApiError ? err.message : friendlyMessage('internal');
  const content = el('div', { class: 'msg-content' }, el('div', { class: 'msg-bubble kind-error' }, message));
  if (onRetry) {
    const retry = el('button', { type: 'button', class: 'action-btn retry-btn' }, '↻ Try again');
    retry.addEventListener('click', () => { node.remove(); onRetry(); }, { once: true });
    content.appendChild(el('div', { class: 'msg-actions' }, retry));
  }
  const node = el('div', { class: 'message bot' }, el('div', { class: 'msg-avatar' }, '💚'), content);
  $('messages').appendChild(node);
  scrollToBottom();
}

function makePlayButton(label, segments) {
  const btn = el('button', { type: 'button', class: 'action-btn play-btn' }, `▶ Play in ${label}`);
  btn.addEventListener('click', async () => {
    if (btn.classList.contains('playing')) { player.stop(); return; }
    document.querySelectorAll('.play-btn.playing').forEach((b) => b.dispatchEvent(new Event('kasa-reset')));
    btn.classList.add('playing');
    btn.textContent = '⏸ Playing…';
    try { await player.play(segments); }
    catch (err) { toastError(err); }
    finally { btn.dispatchEvent(new Event('kasa-reset')); }
  });
  btn.addEventListener('kasa-reset', () => { btn.classList.remove('playing'); btn.textContent = `▶ Play in ${label}`; });
  return btn;
}

function addBotMessage(text, extras = {}) {
  const lang = extras.lang || state.lang;
  const langName = LANGS[lang]?.name || lang;
  const actions = el('div', { class: 'msg-actions' });

  const addAudioButtons = (b64, fmt) => {
    const segment = { audio_base64: b64, format: fmt };
    actions.prepend(makePlayButton(langName, [segment]),
      el('button', { type: 'button', class: 'action-btn dl-btn',
                     onclick: () => downloadAudio(b64, fmt, `kasa-health-${lang}-${Date.now()}`) }, '⬇ Audio'));
  };

  if (extras.audioB64) {
    addAudioButtons(extras.audioB64, extras.audioFmt || 'wav');
  } else if (extras.ttsSupported !== false) {
    const hear = el('button', { type: 'button', class: 'action-btn play-btn' }, `🔊 Hear in ${langName}`);
    hear.addEventListener('click', async () => {
      hear.disabled = true;
      hear.textContent = 'Preparing voice…';
      try {
        const d = await api('/tts', { json: { text, language: lang, speaker: state.speaker }, timeoutMs: 60000 });
        hear.remove();
        addAudioButtons(d.audio_base64, d.format || 'wav');
        player.play([{ audio_base64: d.audio_base64, format: d.format }]).catch(toastError);
      } catch (err) {
        if (isAbort(err)) return;
        hear.disabled = false;
        hear.textContent = '🔊 Try the voice again';
        toast(friendlyMessage('tts_unavailable'), { tone: 'warn' });
      }
    });
    actions.appendChild(hear);
  }
  actions.appendChild(el('button', { type: 'button', class: 'action-btn dl-btn', onclick: () => {
    const body = `Kasa Health — ASRH Assistant\n${'─'.repeat(40)}\nLanguage: ${langName}\nDate: ${new Date().toLocaleString()}\n\n` +
      `--- Response (${langName}) ---\n${text}\n\n--- English ---\n${extras.english || ''}\n`;
    downloadText(body, `kasa-health-${lang}-${Date.now()}.txt`);
  } }, '⬇ Text'));

  $('messages').appendChild(el('div', { class: 'message bot' },
    el('div', { class: 'msg-avatar' }, '🏥'),
    el('div', { class: 'msg-content' },
      el('div', { class: 'msg-bubble' }, text),
      extras.english && extras.english !== text
        ? el('div', { class: 'msg-translation' }, el('div', { class: 'msg-translation-label' }, 'English'), el('div', {}, extras.english))
        : null,
      extras.notice ? el('div', { class: 'soft-notice' }, `💬 ${extras.notice}`) : null,
      disclaimerNode(false),
      actions,
      extras.safety?.flagged && !extras.readOnly
        ? makeCrisisCard(extras.safety, { questionOriginal: extras.questionOriginal, questionEnglish: extras.questionEnglish })
        : null,
      extras.readOnly ? null : makeThumbs(extras, text, lang, 'chat'))));
  scrollToBottom();
}

function beginRequest() {
  if (chat.busy) {
    toast('Kasa is still answering your last question — one moment! 💚', { timeout: 3000 });
    return null;
  }
  chat.busy = true;
  chat.ctrl = new AbortController();
  setStatus('busy');
  showWelcome(false);
  return chat.ctrl;
}

function endRequest(ok) {
  chat.busy = false;
  chat.ctrl = null;
  removeTyping();
  setStatus(ok ? 'ok' : 'error');
  if (!ok) setTimeout(() => setStatus('ok'), 5000);
}

function recordTurn(userText, d) {
  chat.history.push({ role: 'user', content: d.question_translated }, { role: 'assistant', content: d.answer_english });
  chat.history = chat.history.slice(-16);
  chat.display.push({ role: 'user', text: userText }, { role: 'bot', text: d.answer_local, english: d.answer_english, lang: d.language });
  saveSession();
}

async function askKasa(message, ctrl) {
  return api('/chat', { json: { message, language: state.lang, history: chat.history.slice(-12), session_id: state.sessionId },
                        timeoutMs: 70000, retries: 1, signal: ctrl.signal });
}

async function sendTextMessage(text) {
  text = (text || '').trim();
  if (!text) return;
  const ctrl = beginRequest();
  if (!ctrl) return;
  const input = $('textInput');
  input.value = '';
  input.style.height = 'auto';
  addUserMessage(text);
  showTyping('Finding an answer…');
  try {
    const d = await askKasa(text, ctrl);
    endRequest(true);
    recordTurn(text, d);
    addBotMessage(d.answer_local, { english: d.answer_english, lang: d.language, notice: d.notice, safety: d.safety,
      ttsSupported: d.tts_supported, questionOriginal: text, questionEnglish: d.question_translated });
  } catch (err) {
    if (isAbort(err)) return;
    endRequest(false);
    addKindError(err, () => { chat.busy = false; retryText(text); });
  }
}

function retryText(text) {
  // The question bubble is already shown; ask again without adding it twice.
  const ctrl = beginRequest();
  if (!ctrl) return;
  showTyping('Finding an answer…');
  askKasa(text, ctrl).then((d) => {
    endRequest(true);
    recordTurn(text, d);
    addBotMessage(d.answer_local, { english: d.answer_english, lang: d.language, notice: d.notice, safety: d.safety,
      ttsSupported: d.tts_supported, questionOriginal: text, questionEnglish: d.question_translated });
  }).catch((err) => {
    if (isAbort(err)) return;
    endRequest(false);
    addKindError(err, () => retryText(text));
  });
}

async function sendVoiceMessage(blob, knownTranscript = null) {
  const ctrl = beginRequest();
  if (!ctrl) return;
  let transcript = knownTranscript;
  try {
    if (!transcript) {
      showTyping('Listening to your voice…');
      const form = new FormData();
      form.append('audio', blob, 'recording.wav');
      form.append('language', state.lang);
      const asr = await api('/asr', { form, timeoutMs: 60000, retries: 1, signal: ctrl.signal });
      transcript = asr.transcript;
      addUserMessage(transcript, { isVoice: true });
    }
    showTyping('Finding an answer…');
    const d = await askKasa(transcript, ctrl);
    recordTurn(transcript, d);

    let audioB64 = null, audioFmt = null;
    if (d.tts_supported) {
      showTyping('Preparing the voice…');
      try {
        const t = await api('/tts', { json: { text: d.answer_local, language: state.lang, speaker: state.speaker },
                                      timeoutMs: 60000, retries: 1, signal: ctrl.signal });
        audioB64 = t.audio_base64; audioFmt = t.format || 'wav';
      } catch (err) {
        if (isAbort(err)) throw err;
        // Voice is a bonus: the written answer still appears.
      }
    }
    endRequest(true);
    addBotMessage(d.answer_local, { english: d.answer_english, lang: d.language, audioB64, audioFmt, safety: d.safety,
      notice: d.notice || (d.tts_supported && !audioB64 ? friendlyMessage('tts_unavailable') : null),
      ttsSupported: d.tts_supported, questionOriginal: transcript, questionEnglish: d.question_translated });
    if (audioB64) player.play([{ audio_base64: audioB64, format: audioFmt }]).catch(toastError);
  } catch (err) {
    if (isAbort(err)) return;
    endRequest(false);
    const recoverable = !(err instanceof ApiError && ['no_speech', 'audio_invalid', 'audio_too_short', 'too_large', 'audio_too_large'].includes(err.code));
    addKindError(err, recoverable ? () => sendVoiceMessage(blob, transcript) : null);
  }
}

function initChat() {
  // Text is the default. If someone picks Voice, we remember it for next time.
  const setChatMode = (mode, { focus = false } = {}) => {
    const text = mode !== 'voice';
    $('modeText').classList.toggle('active', text);
    $('modeVoice').classList.toggle('active', !text);
    $('textPanel').classList.toggle('hidden', !text);
    $('voicePanel').classList.toggle('hidden', text);
    if (text && focus) $('textInput').focus();
  };
  $('modeText').addEventListener('click', () => { recorders[0]?.cancel(); setChatMode('text', { focus: true }); store.set('kasa_chat_mode', 'text'); });
  $('modeVoice').addEventListener('click', () => { setChatMode('voice'); store.set('kasa_chat_mode', 'voice'); });
  setChatMode(store.get('kasa_chat_mode', 'text'));

  const input = $('textInput');
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendTextMessage(input.value); } });
  $('sendBtn').addEventListener('click', () => sendTextMessage(input.value));

  // Tap to start, tap again to send (like ChatGPT). Cancel throws the recording away.
  recorders.push(tapRecorder({
    owner: 'chat', button: 'micBtn', label: 'micLabel', viz: 'voiceViz',
    controls: 'recControls', timer: 'recTimer', cancel: 'recCancel',
    idleText: 'Tap to speak', recordingText: 'Tap to send', maxSeconds: 60,
    canStart: () => {
      if (!chat.busy) return true;
      toast('Kasa is still answering your last question — one moment! 💚', { timeout: 3000 });
      return false;
    },
    onStart: () => player.stop(),
    onBlob: (blob) => sendVoiceMessage(blob),
    onMicError: (err) => { if (['mic_denied', 'mic_insecure', 'mic_missing'].includes(err.code)) $('modeText').click(); },
  }));
}

/* --- Saved sessions (browser only) --- */
function saveSession() {
  if (!chat.display.length) return;
  const sessions = store.get(SESSIONS_KEY, []).filter((s) => s && s.id !== state.sessionId);
  sessions.unshift({
    id: state.sessionId, timestamp: new Date().toISOString(), language: state.lang,
    preview: (chat.display.find((m) => m.role === 'user')?.text || 'Conversation').slice(0, 80),
    display: chat.display.slice(-40), history: chat.history.slice(-16),
  });
  store.set(SESSIONS_KEY, sessions.slice(0, 20));
}

function restoreSession(session) {
  newConversation();
  state.sessionId = session.id;
  chat.history = Array.isArray(session.history) ? session.history : [];
  chat.display = Array.isArray(session.display) ? session.display : [];
  showWelcome(chat.display.length === 0);
  for (const m of chat.display) {
    if (m.role === 'user') addUserMessage(m.text);
    else addBotMessage(m.text, { english: m.english, lang: m.lang, readOnly: true });
  }
  closeModal('historyPanel');
  selectTab('chat');
}

function initHistory() {
  $('historyBtn').addEventListener('click', () => {
    const list = $('historyList');
    const sessions = store.get(SESSIONS_KEY, []).filter((s) => s && typeof s === 'object');
    if (!sessions.length) {
      list.replaceChildren(el('p', { class: 'history-empty' }, 'No saved conversations yet.'));
    } else {
      list.replaceChildren(...sessions.map((s) => {
        const item = el('button', { type: 'button', class: 'history-item', title: 'Open this conversation' },
          el('div', { class: 'history-lang' }, LANGS[s.language]?.name || String(s.language || '')),
          el('div', { class: 'history-preview' }, `${String(s.preview || '')}…`),
          el('div', { class: 'history-time' }, new Date(s.timestamp).toLocaleDateString()));
        if (Array.isArray(s.display)) item.addEventListener('click', () => restoreSession(s));
        return item;
      }));
    }
    openModal('historyPanel');
  });
  $('historyClose').addEventListener('click', () => closeModal('historyPanel'));
  $('historyPanel').addEventListener('click', (e) => { if (e.target === $('historyPanel')) closeModal('historyPanel'); });
}

// --- Transcribe ---
let transcripts = store.get(TRANSCRIPTS_KEY, []);

function showTranscript(text) {
  $('transcribeResult').style.display = 'block';
  $('transcriptBox').textContent = text;
  $('transLangBadge').textContent = state.langName;
  $('transThumbs')?.remove();
  const thumbs = makeThumbs({ questionOriginal: 'Speech transcription', questionEnglish: 'Speech transcription', english: text },
    text, state.lang, 'transcribe');
  thumbs.id = 'transThumbs';
  $('transcribeResult').appendChild(thumbs);
}

function renderTranscriptHistory() {
  const list = $('transHistoryList');
  if (!transcripts.length) {
    list.replaceChildren(el('p', { class: 'history-empty' }, 'No transcriptions yet.'));
    return;
  }
  list.replaceChildren(...transcripts.map((t) => {
    const text = String(t.text || '');
    return el('button', { type: 'button', class: 'trans-item', onclick: () => showTranscript(text) },
      el('span', { class: 'trans-lang' }, LANGS[t.language]?.name || String(t.language || '')),
      el('span', { class: 'trans-preview' }, text.length > 60 ? `${text.slice(0, 60)}…` : text),
      el('span', { class: 'trans-time' }, new Date(t.timestamp).toLocaleTimeString()));
  }));
}

function initTranscribe() {
  let busy = false;
  const transcribe = async (blob) => {
    busy = true;
    $('transMicLabel').textContent = 'Transcribing…';
    $('transMicBtn').disabled = true;
    const form = new FormData();
    form.append('audio', blob, 'recording.wav');
    form.append('language', state.lang);
    try {
      const d = await api('/asr', { form, timeoutMs: 60000, retries: 1 });
      showTranscript(d.transcript);
      transcripts.unshift({ text: d.transcript, language: state.lang, timestamp: new Date().toISOString() });
      transcripts = transcripts.slice(0, 20);
      store.set(TRANSCRIPTS_KEY, transcripts);
      renderTranscriptHistory();
    } catch (err) {
      if (!isAbort(err)) toastError(err);
    } finally {
      busy = false;
      $('transMicBtn').disabled = false;
      $('transMicLabel').textContent = 'Tap to record';
    }
  };

  recorders.push(tapRecorder({
    owner: 'transcribe', button: 'transMicBtn', label: 'transMicLabel', viz: 'transViz',
    controls: 'transRecControls', timer: 'transRecTimer', cancel: 'transRecCancel',
    idleText: 'Tap to record', recordingText: 'Tap to stop', maxSeconds: 120,
    canStart: () => {
      if (!busy) return true;
      toast('Still working on your last recording — one moment!', { timeout: 2500 });
      return false;
    },
    onStart: () => player.stop(),
    onBlob: transcribe,
  }));

  $('transCopyBtn').addEventListener('click', async () => {
    const copyBtn = $('transCopyBtn');
    try {
      await navigator.clipboard.writeText($('transcriptBox').textContent);
      copyBtn.textContent = '✅ Copied!';
    } catch {
      copyBtn.textContent = 'Select & copy manually';
    }
    setTimeout(() => { copyBtn.textContent = '📋 Copy'; }, 2000);
  });
  $('transDownloadBtn').addEventListener('click', () =>
    downloadText($('transcriptBox').textContent, `transcription-${state.lang}-${Date.now()}.txt`));
  renderTranscriptHistory();
}

// --- Disclaimer, connectivity, startup ---
function initDisclaimer() {
  const modal = $('disclaimerModal'), check = $('disclaimerCheck'), agree = $('disclaimerAgreeBtn');
  const scroller = $('disclaimerScroll'), hint = $('disclaimerScrollHint');
  let agreed = false;
  try { agreed = sessionStorage.getItem('kasa_disclaimer_agreed') === 'true'; } catch { /* private mode */ }
  if (!agreed) openModal('disclaimerModal'); else modal.classList.add('hidden');

  // The tick-box only unlocks once they've scrolled through everything.
  let readAll = false;
  const unlock = () => {
    if (readAll) return;
    readAll = true;
    check.disabled = false;
    $('disclaimerCheckLabel').classList.remove('disabled');
    hint.textContent = '✓ Thank you for reading. Please tick the box to continue.';
    hint.classList.add('done');
  };
  const checkScrolled = () => {
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 12) unlock();
  };
  scroller.addEventListener('scroll', checkScrolled, { passive: true });
  // On big screens the text may fit without scrolling, so that counts as read.
  const checkFits = () => { if (modal.classList.contains('visible') || !modal.classList.contains('hidden')) checkScrolled(); };
  setTimeout(checkFits, 400);
  window.addEventListener('resize', checkFits);

  check.addEventListener('change', () => {
    agree.disabled = !check.checked;
    agree.classList.toggle('enabled', check.checked);
    $('disclaimerCheckLabel').classList.toggle('checked', check.checked);
  });
  agree.addEventListener('click', () => {
    try { sessionStorage.setItem('kasa_disclaimer_agreed', 'true'); } catch { /* ignore */ }
    closeModal('disclaimerModal');
  });
}

let wasOffline = false;
async function pingBackend() {
  try {
    await api('/languages', { timeoutMs: 6000, retries: 0 });
    setStatus('ok');
    if (wasOffline) { toast("You're back online. 💚", { timeout: 2500 }); wasOffline = false; }
  } catch {
    setStatus('error');
  }
}

function initConnectivity() {
  window.addEventListener('offline', () => {
    wasOffline = true;
    setStatus('error');
    toast(friendlyMessage('offline'), { tone: 'warn', timeout: 6000 });
  });
  window.addEventListener('online', pingBackend);
  setTimeout(pingBackend, 1500);
  setInterval(pingBackend, 120000);
}

// Last safety net: unexpected errors go to the console, never onto the screen.
window.addEventListener('unhandledrejection', (e) => {
  if (isAbort(e.reason)) { e.preventDefault(); return; }
  console.error('[Kasa] Unhandled:', e.reason);
  e.preventDefault();
});
window.addEventListener('error', (e) => console.error('[Kasa] Error:', e.error || e.message));
window.addEventListener('beforeunload', saveSession);

function init() {
  initTheme();
  initTabs();
  initSettings();
  initChat();
  initHistory();
  initTranscribe();
  initAgent();
  initFeedback();
  initDisclaimer();
  initConnectivity();
  showWelcome(true);
  closeSidebarOnMobile();
  if (!window.isSecureContext) toast(friendlyMessage('mic_insecure'), { tone: 'warn', timeout: 8000 });
}

init();
