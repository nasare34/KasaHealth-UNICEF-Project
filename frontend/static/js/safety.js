// Support card for messages that look urgent. The emergency numbers always show;
// sharing a name and phone number is optional and needs the consent box ticked.
import { api, isAbort } from './api.js';
import { state } from './state.js';
import { el, toast } from './ui.js';

const shownForSession = new Set();

export function makeCrisisCard(safety, context = {}) {
  const high = safety.severity === 'high';
  const calls = el('div', { class: 'crisis-calls' },
    ...(safety.emergency || []).map((e, i) => el('a', {
      class: `crisis-call${i === 0 ? ' primary' : ''}`, href: `tel:${e.number}`,
      'aria-label': `Call ${e.label}: ${e.display || e.number}`,
    }, i === 0 ? '📞 ' : '☎️ ', `${e.label}: ${e.display || e.number}`)));

  const card = el('div', { class: 'crisis-card', role: 'region', 'aria-label': 'Support options' },
    el('h4', {}, high ? '💚 You are not alone — help is available' : '💚 If this is urgent, help is available'),
    el('p', {}, high
      ? 'If you are in danger or thinking of hurting yourself, please call 112 now, or talk to SHEplus Ghana. You deserve support.'
      : 'If you or someone else is hurt or in danger, please call 112. SHEplus Ghana can also help.'),
    calls);

  // I only ask once per conversation, so nobody who said no gets asked again.
  const key = state.sessionId;
  if (!safety.ask_contact || shownForSession.has(key)) return card;
  shownForSession.add(key);

  const phone = el('input', { type: 'tel', inputmode: 'tel', autocomplete: 'tel', required: true,
    placeholder: 'e.g. 024 123 4567', maxlength: '20' });
  const name = el('input', { type: 'text', autocomplete: 'given-name', placeholder: 'Optional', maxlength: '120' });
  const bestTime = el('select', {},
    el('option', { value: '' }, 'Any time'), el('option', { value: 'Morning' }, 'Morning'),
    el('option', { value: 'Afternoon' }, 'Afternoon'), el('option', { value: 'Evening' }, 'Evening'));
  const safeToCall = el('select', {},
    el('option', { value: '' }, 'Choose…'), el('option', { value: 'Yes, safe to call' }, 'Yes, it is safe to call'),
    el('option', { value: 'Text/WhatsApp only' }, 'Text / WhatsApp only'),
    el('option', { value: 'Not safe — be careful' }, 'Not always safe — please be careful'));
  const note = el('textarea', { rows: '2', maxlength: '1000', placeholder: 'Anything you want them to know (optional)' });
  const consent = el('input', { type: 'checkbox' });
  const submit = el('button', { type: 'submit', class: 'crisis-submit' }, 'Ask someone to contact me');
  const skip = el('button', { type: 'button', class: 'crisis-skip' }, 'No thanks');

  const form = el('form', { class: 'crisis-form', novalidate: true },
    el('p', {}, 'Would you like a trained person from the support team to contact you? This is completely your choice.'),
    el('label', {}, 'Phone number *', phone),
    el('label', {}, 'Your name (optional)', name),
    el('label', {}, 'Is it safe for us to call this number?', safeToCall),
    el('label', {}, 'Best time to reach you', bestTime),
    el('label', {}, 'Message (optional)', note),
    el('label', { class: 'consent' }, consent,
      el('span', {}, 'I agree to share these details with the Kasa Health support team so they can contact me.')),
    el('div', { class: 'crisis-actions' }, submit, skip));
  card.appendChild(form);

  skip.addEventListener('click', () => {
    form.replaceWith(el('p', { class: 'crisis-done' },
      "That's okay. The numbers above are always here if you need them. 💚"));
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submit.disabled) return;
    const digits = phone.value.replace(/\D/g, '');
    if (digits.length < 9) { toast('Please enter a phone number we can reach you on.', { tone: 'warn' }); phone.focus(); return; }
    if (!consent.checked) { toast('Please tick the box to agree to share your details.', { tone: 'warn' }); return; }
    submit.disabled = true;
    submit.textContent = 'Sending…';
    try {
      const res = await api('/safety/contact', {
        json: {
          consent: true, phone: phone.value.trim(), name: name.value.trim(), best_time: bestTime.value,
          safe_to_call: safeToCall.value, note: note.value.trim(), category: safety.category || '',
          language: state.lang, session_id: state.sessionId,
          question_original: context.questionOriginal || '', question_english: context.questionEnglish || '',
        },
        timeoutMs: 20000, retries: 2,
      });
      form.replaceWith(el('p', { class: 'crisis-done' }, `✅ ${res.message}`));
    } catch (err) {
      if (isAbort(err)) return;
      submit.disabled = false;
      submit.textContent = 'Try again';
      toast(err.code === 'bad_phone' ? err.message
        : "We couldn't send that just now. Please try again — and if you are in danger, call 112.", { tone: 'warn', timeout: 9000 });
    }
  });
  return card;
}
