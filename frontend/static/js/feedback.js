// Thumbs up/down, the correction form and the survey.
// Ratings that fail to send are kept and tried again later.
import { api, ApiError, isAbort } from './api.js';
import { mic } from './recorder.js';
import { state, store } from './state.js';
import { $, closeModal, el, holdToTalk, openModal, toast, toastError } from './ui.js';

const OUTBOX_KEY = 'kasa_rating_outbox';

/* --- Ratings outbox --- */
async function sendRating(payload) {
  try {
    await api('/feedback/rating', { json: payload, timeoutMs: 15000, retries: 1 });
    return true;
  } catch (err) {
    if (err instanceof ApiError && (err.retryable || ['offline', 'network', 'rate_limited'].includes(err.code))) {
      const box = store.get(OUTBOX_KEY, []);
      box.push(payload);
      store.set(OUTBOX_KEY, box.slice(-50));
    }
    return false;
  }
}

let flushing = false;
export async function flushOutbox() {
  if (flushing || navigator.onLine === false) return;
  const box = store.get(OUTBOX_KEY, []);
  if (!box.length) return;
  flushing = true;
  const remaining = [];
  for (const item of box) {
    try { await api('/feedback/rating', { json: item, timeoutMs: 15000, retries: 0 }); }
    catch { remaining.push(item); }
  }
  store.set(OUTBOX_KEY, remaining);
  flushing = false;
}
window.addEventListener('online', flushOutbox);

export function submitRating(rating, extras, text, lang, context, reason = '', testerCode = '') {
  return sendRating({
    rating, language: lang, context,
    question_original: extras?.questionOriginal || '',
    question_english: extras?.questionEnglish || '',
    answer_local: text || '',
    answer_english: extras?.english || '',
    reason, tester_code: (testerCode || '').toUpperCase().trim(),
    session_id: state.sessionId, timestamp: new Date().toISOString(),
  });
}

/* --- Thumbs widget --- */
export function makeThumbs(extras, text, lang, context) {
  const up = el('button', { class: 'thumb-btn thumb-up', type: 'button', title: 'This was helpful', 'aria-label': 'Helpful' }, '👍');
  const down = el('button', { class: 'thumb-btn thumb-down', type: 'button', title: 'This needs improvement', 'aria-label': 'Needs improvement' }, '👎');
  const codeInput = el('input', { type: 'text', class: 'tester-code-input', placeholder: 'e.g. TEST001', maxlength: '40', 'aria-label': 'Tester code' });
  const codeRow = el('div', { class: 'thumb-code-row hidden' },
    el('span', { class: 'thumb-code-label' }, 'Tester code (optional):'), codeInput,
    el('button', { type: 'button', class: 'thumb-code-submit', onclick: (e) => { e.stopPropagation(); submitUp(codeInput.value); } }, 'Submit ✓'),
    el('button', { type: 'button', class: 'thumb-code-skip', onclick: (e) => { e.stopPropagation(); submitUp(''); } }, 'Skip'));
  const wrap = el('div', { class: 'thumbs-wrap' }, up, down, codeRow);

  let current = null, upSent = false;
  const setVisual = (r) => {
    current = r;
    up.classList.toggle('rated', r === 'up'); up.classList.toggle('active', r === 'up');
    down.classList.toggle('rated', r === 'down'); down.classList.toggle('active', r === 'down');
    up.textContent = r === 'up' ? '👍✓' : '👍';
    down.textContent = r === 'down' ? '👎✓' : '👎';
  };
  const submitUp = async (code) => {
    if (upSent) return;
    upSent = true;
    codeRow.classList.add('hidden');
    await submitRating('up', extras, text, lang, context, '', code);
    toast('Thank you! Your rating helps Kasa improve. 💚', { timeout: 2500 });
  };

  up.addEventListener('click', () => {
    if (current === 'up' && upSent) return;
    upSent = false;
    setVisual('up');
    codeRow.classList.remove('hidden');
    codeInput.value = '';
    setTimeout(() => codeInput.focus(), 50);
  });
  codeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitUp(codeInput.value); } });
  down.addEventListener('click', () => {
    if (current === 'up' && !upSent) { upSent = true; codeRow.classList.add('hidden'); }
    setVisual('down');
    openFeedbackModal(extras, text, lang, context);
  });
  return wrap;
}

/* --- Correction (thumbs-down) modal --- */
const fb = { extras: null, text: '', lang: 'tw', context: 'chat', audio: null };
const FB_OWNER = 'feedback';

function openFeedbackModal(extras, text, lang, context) {
  Object.assign(fb, { extras, text, lang, context, audio: null });
  $('fbReason').value = '';
  $('fbCorrection').value = '';
  $('fbTesterCode').value = '';
  $('fbRecordLabel').textContent = 'Hold to record correction';
  $('fbAudioStatus').textContent = '';
  $('fbThanksOverlay')?.remove();
  const btn = $('fbSubmitBtn');
  btn.disabled = false;
  btn.textContent = 'Submit Correction';
  openModal('feedbackModal');
}

function closeFeedbackModal() {
  mic.cancel(FB_OWNER);
  closeModal('feedbackModal');
}

function initCorrectionModal() {
  const micBtn = $('fbMicBtn');
  holdToTalk(micBtn, async () => {
    try {
      await mic.start(FB_OWNER);
      micBtn.classList.add('recording');
      $('fbRecordLabel').textContent = 'Release to stop…';
    } catch (err) { toastError(err); }
  }, () => {
    micBtn.classList.remove('recording');
    const blob = mic.stop(FB_OWNER);
    if (blob) {
      fb.audio = blob;
      $('fbRecordLabel').textContent = '✅ Audio recorded — hold to re-record';
      $('fbAudioStatus').textContent = 'Audio ready to submit';
    } else {
      $('fbRecordLabel').textContent = 'Hold to record correction';
      $('fbAudioStatus').textContent = 'That was very short — please hold a little longer.';
    }
  });

  $('fbClose').addEventListener('click', closeFeedbackModal);
  $('feedbackModal').addEventListener('click', (e) => { if (e.target === $('feedbackModal')) closeFeedbackModal(); });

  $('fbSubmitBtn').addEventListener('click', async () => {
    const btn = $('fbSubmitBtn');
    if (btn.disabled) return;
    const reason = $('fbReason').value;
    if (!reason) {
      toast('Please choose what was wrong, so we know how to improve.', { tone: 'warn' });
      $('fbReason').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Submitting…';
    const testerCode = ($('fbTesterCode').value || '').toUpperCase().trim();

    const form = new FormData();
    form.append('rating', 'down');
    form.append('language', fb.lang);
    form.append('question_original', fb.extras?.questionOriginal || '');
    form.append('question_english', fb.extras?.questionEnglish || '');
    form.append('answer_local', fb.text || '');
    form.append('answer_english', fb.extras?.english || '');
    form.append('correction_text', $('fbCorrection').value || '');
    form.append('reason', reason);
    form.append('tester_code', testerCode);
    form.append('session_id', state.sessionId);
    if (fb.audio) form.append('audio', fb.audio, 'correction.wav');

    // The rating itself goes through the outbox, so it survives a bad connection.
    submitRating('down', fb.extras, fb.text, fb.lang, fb.context || 'correction', reason, testerCode);
    try {
      await api('/feedback/thumbs', { form, timeoutMs: 30000, retries: 1 });
    } catch (err) {
      if (isAbort(err)) return;
      btn.disabled = false;
      btn.textContent = 'Try again';
      toast("We couldn't send your correction just now. Please try again in a moment — thank you for your patience!", { tone: 'warn', timeout: 7000 });
      return;
    }
    const overlay = el('div', { id: 'fbThanksOverlay', class: 'thanks-overlay' },
      el('div', { class: 'thanks-icon' }, '🙏'),
      el('h3', {}, 'Thank you!'),
      el('p', {}, 'Your correction helps improve Kasa Health for young people in Ghana.'),
      el('button', { type: 'button', class: 'survey-submit-btn thanks-close', onclick: closeFeedbackModal }, 'Close'));
    document.querySelector('#feedbackModal .modal-card').appendChild(overlay);
    setTimeout(closeFeedbackModal, 3000);
  });
}

/* --- Survey --- */
function initSurvey() {
  const form = $('surveyForm');
  const testerGroup = $('testerCodeGroup');
  form.addEventListener('change', (e) => {
    if (e.target.name === 'is_tester') testerGroup.classList.toggle('hidden', e.target.value !== 'Yes');
  });

  $('surveyBtn').addEventListener('click', () => openModal('surveyModal'));
  $('surveyClose').addEventListener('click', () => closeModal('surveyModal'));
  $('surveyModal').addEventListener('click', (e) => { if (e.target === $('surveyModal')) closeModal('surveyModal'); });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitBtn = form.querySelector('.survey-submit-btn');
    if (submitBtn.disabled) return;
    const data = Object.fromEntries(new FormData(form));
    data.timestamp = new Date().toISOString();
    data.language = state.lang;
    data.sessionId = state.sessionId;
    if (data.tester_code) data.tester_code = data.tester_code.toUpperCase().trim();

    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting…';
    try {
      await api('/survey', { json: data, timeoutMs: 20000, retries: 2 });
    } catch (err) {
      if (isAbort(err)) return;
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit Feedback';
      // Keep everything they typed. Nobody should have to fill a form in twice.
      toast("We couldn't send your feedback just now, but your answers are still here. Please tap Submit again in a moment.", { tone: 'warn', timeout: 8000 });
      return;
    }

    const thanks = el('div', { id: 'surveyThankYou', class: 'thanks-overlay' },
      el('div', { class: 'thanks-icon' }, '🙏'),
      el('h3', {}, 'Thank you!'),
      el('p', {}, 'Your feedback helps us improve health information for young people in Ghana.'),
      el('button', { type: 'button', class: 'survey-submit-btn thanks-close', onclick: () => closeModal('surveyModal') }, 'Close'));
    document.querySelector('#surveyModal .modal-card').appendChild(thanks);

    setTimeout(() => {
      form.reset();
      testerGroup.classList.add('hidden');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit Feedback';
      $('surveyThankYou')?.remove();
      closeModal('surveyModal');
    }, 3000);
  });
}

export function initFeedback() {
  initCorrectionModal();
  initSurvey();
  flushOutbox();
  setInterval(flushOutbox, 60000);
}
