/* ─── KASA HEALTH — app.js v3 ──────────────────────────────────────────────── */
const API_BASE = '/api';

// ── Global state ──────────────────────────────────────────────────────────────
let currentLang    = 'tw';
let currentSpeaker = 'male_low';
let playbackRate   = 1.0;
let isRecording    = false;
let conversationHistory = [];
let agentHistory   = [];
let currentAudio   = null;
let sessionId      = Date.now().toString();

const LANG_CONFIG = {
  tw:  { name: 'Twi' },
  dag: { name: 'Dagbani' },
  ee:  { name: 'Ewe' },
};
const QUICK_PROMPTS = {
  puberty:       "What changes happen to my body during puberty?",
  contraception: "What contraception options are available for young people?",
  sti:           "How can I protect myself from STIs and HIV?",
  pregnancy:     "What are the signs of pregnancy and what should I do?",
  menstruation:  "Is it normal to have irregular periods?",
  consent:       "What is consent and how do I know if I am in a safe relationship?",
};

// ── WAV recorder (shared) ─────────────────────────────────────────────────────
let _audioCtx = null, _processor = null, _stream = null, _pcmChunks = [];
const SAMPLE_RATE = 16000;

async function startWAVRecording() {
  _stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: SAMPLE_RATE, channelCount: 1 } });
  _audioCtx  = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  const src  = _audioCtx.createMediaStreamSource(_stream);
  _processor = _audioCtx.createScriptProcessor(4096, 1, 1);
  _pcmChunks = [];
  _processor.onaudioprocess = (e) => { if (isRecording) _pcmChunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
  src.connect(_processor); _processor.connect(_audioCtx.destination);
  isRecording = true;
}

function stopWAVRecording() {
  isRecording = false;
  if (_processor) { _processor.disconnect(); _processor = null; }
  if (_audioCtx)  { _audioCtx.close();       _audioCtx = null;  }
  if (_stream)    { _stream.getTracks().forEach(t => t.stop()); _stream = null; }
  const total = _pcmChunks.reduce((n,c) => n+c.length, 0);
  if (total < 800) return null;
  return encodeWAV(_pcmChunks, SAMPLE_RATE);
}

function encodeWAV(chunks, sr) {
  const total = chunks.reduce((n,c)=>n+c.length,0);
  const f32   = new Float32Array(total);
  let off = 0; for (const c of chunks) { f32.set(c, off); off += c.length; }
  const i16 = new Int16Array(f32.length);
  for (let i=0;i<f32.length;i++) { const s=Math.max(-1,Math.min(1,f32[i])); i16[i]=s<0?s*0x8000:s*0x7FFF; }
  const ds = i16.length*2, buf = new ArrayBuffer(44+ds), v = new DataView(buf);
  const ws = (o,s) => { for (let i=0;i<s.length;i++) v.setUint8(o+i,s.charCodeAt(i)); };
  ws(0,'RIFF'); v.setUint32(4,36+ds,true); ws(8,'WAVE');
  ws(12,'fmt '); v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
  v.setUint32(24,sr,true); v.setUint32(28,sr*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
  ws(36,'data'); v.setUint32(40,ds,true);
  const pb = new Uint8Array(buf,44);
  for (let i=0;i<i16.length;i++) { pb[i*2]=i16[i]&0xff; pb[i*2+1]=(i16[i]>>8)&0xff; }
  return new Blob([buf], {type:'audio/wav'});
}

// ── Audio playback ─────────────────────────────────────────────────────────────
function playAudio(b64, fmt='wav', onEnd=null) {
  stopAudio();
  const mime = fmt==='mp3'?'audio/mpeg':`audio/${fmt}`;
  const bytes = Uint8Array.from(atob(b64), c=>c.charCodeAt(0));
  const url   = URL.createObjectURL(new Blob([bytes],{type:mime}));
  currentAudio = new Audio(url);
  currentAudio.playbackRate = playbackRate;
  currentAudio.play().catch(console.error);
  currentAudio.addEventListener('ended', ()=>{ URL.revokeObjectURL(url); if(onEnd) onEnd(); });
  return currentAudio;
}
function stopAudio() { if(currentAudio){currentAudio.pause();currentAudio=null;} }

// ── Bar animation ─────────────────────────────────────────────────────────────
function animateBars(vizEl) {
  const bars = vizEl.querySelectorAll('.bar');
  function frame() { if(!isRecording)return; bars.forEach(b=>{b.style.height=(Math.random()*30+6)+'px';}); setTimeout(frame,80); }
  frame();
}
function resetBars(vizEl) { vizEl.querySelectorAll('.bar').forEach(b=>b.style.height='8px'); }

// ── Download helpers ──────────────────────────────────────────────────────────
function downloadText(localText, englishText, lang) {
  const content = `Kasa Health — ASRH Assistant\n${'─'.repeat(40)}\nLanguage: ${LANG_CONFIG[lang]?.name||lang}\nDate: ${new Date().toLocaleString()}\n\n--- Response (${LANG_CONFIG[lang]?.name||lang}) ---\n${localText}\n\n--- English ---\n${englishText||''}\n`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content],{type:'text/plain'}));
  a.download = `kasa-health-${lang}-${Date.now()}.txt`;
  a.click(); URL.revokeObjectURL(a.href);
}
function downloadAudio(b64, fmt, lang) {
  const mime = fmt==='mp3'?'audio/mpeg':`audio/${fmt}`;
  const bytes = Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([bytes],{type:mime}));
  a.download = `kasa-health-${lang}-${Date.now()}.${fmt}`;
  a.click(); URL.revokeObjectURL(a.href);
}

// ── Session/history storage ───────────────────────────────────────────────────
function saveSession() {
  if (!conversationHistory.length) return;
  const sessions = JSON.parse(localStorage.getItem('kasa_sessions')||'[]');
  sessions.unshift({ id:sessionId, timestamp:new Date().toISOString(), language:currentLang,
    preview: conversationHistory[0]?.content?.slice(0,80)||'Conversation', messages:conversationHistory });
  localStorage.setItem('kasa_sessions', JSON.stringify(sessions.slice(0,20)));
}

// ─────────────────────────────────────────────────────────────────────────────
// ── TAB SWITCHING ─────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ── SIDEBAR & GLOBAL CONTROLS ─────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById('menuBtn').addEventListener('click', ()=>document.getElementById('sidebar').classList.toggle('open'));
document.getElementById('sidebarClose').addEventListener('click', ()=>document.getElementById('sidebar').classList.remove('open'));

document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.lang-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active'); currentLang = btn.dataset.lang;
    const name = LANG_CONFIG[currentLang].name;
    document.getElementById('currentLangBadge').textContent = `🇬🇭 ${name}`;
    document.getElementById('hintLang').textContent = name;
    document.getElementById('textHintLang').textContent = name;
    document.getElementById('transLangLabel').textContent = name;
    document.getElementById('transLangBadge').textContent = name;
    if(window.innerWidth<768) document.getElementById('sidebar').classList.remove('open');
  });
});

document.querySelectorAll('.speaker-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    if(btn.disabled || btn.classList.contains('speaker-coming-soon')) return;
    document.querySelectorAll('.speaker-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active'); currentSpeaker = btn.dataset.speaker;
  });
});

const speedSlider = document.getElementById('speedSlider');
const speedLabel  = document.getElementById('speedLabel');
speedSlider.addEventListener('input', ()=>{
  playbackRate = parseFloat(speedSlider.value);
  speedLabel.textContent = `${playbackRate.toFixed(1)}×`;
  if(currentAudio) currentAudio.playbackRate = playbackRate;
});

document.querySelectorAll('.topic-chip').forEach(chip=>{
  chip.addEventListener('click',()=>{ sendTextMessage(QUICK_PROMPTS[chip.dataset.topic]); if(window.innerWidth<768) document.getElementById('sidebar').classList.remove('open'); });
});
document.querySelectorAll('.welcome-card').forEach(card=>{
  card.addEventListener('click',()=>sendTextMessage(card.dataset.prompt));
});
document.getElementById('newChatBtn').addEventListener('click',()=>{
  saveSession(); conversationHistory=[]; sessionId=Date.now().toString();
  document.getElementById('messages').innerHTML='';
  document.getElementById('welcome').style.display='flex';
  if(window.innerWidth<768) document.getElementById('sidebar').classList.remove('open');
});

// ── Chat mode toggle ──────────────────────────────────────────────────────────
document.getElementById('modeVoice').addEventListener('click',()=>{
  document.getElementById('modeVoice').classList.add('active');
  document.getElementById('modeText').classList.remove('active');
  document.getElementById('voicePanel').classList.remove('hidden');
  document.getElementById('textPanel').classList.add('hidden');
});
document.getElementById('modeText').addEventListener('click',()=>{
  document.getElementById('modeText').classList.add('active');
  document.getElementById('modeVoice').classList.remove('active');
  document.getElementById('textPanel').classList.remove('hidden');
  document.getElementById('voicePanel').classList.add('hidden');
  document.getElementById('textInput').focus();
});

const textInput = document.getElementById('textInput');
textInput.addEventListener('input',()=>{ textInput.style.height='auto'; textInput.style.height=Math.min(textInput.scrollHeight,120)+'px'; });
textInput.addEventListener('keydown',(e)=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();const t=textInput.value.trim();if(t)sendTextMessage(t);} });
document.getElementById('sendBtn').addEventListener('click',()=>{ const t=textInput.value.trim();if(t)sendTextMessage(t); });

// ── Survey ────────────────────────────────────────────────────────────────────
document.getElementById('surveyBtn').addEventListener('click',()=>{ const m=document.getElementById('surveyModal'); m.classList.remove('hidden'); setTimeout(()=>m.classList.add('visible'),10); });
document.getElementById('surveyClose').addEventListener('click',closeSurvey);
document.getElementById('surveyModal').addEventListener('click',(e)=>{ if(e.target===document.getElementById('surveyModal')) closeSurvey(); });
function closeSurvey() { const m=document.getElementById('surveyModal'); m.classList.remove('visible'); setTimeout(()=>m.classList.add('hidden'),300); }

// Show/hide tester code field
document.querySelectorAll('input[name="is_tester"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const group = document.getElementById('testerCodeGroup');
    if(group) group.style.display = radio.value === 'Yes' ? 'block' : 'none';
  });
});

document.getElementById('surveyForm').addEventListener('submit',(e)=>{
  e.preventDefault();
  const data = Object.fromEntries(new FormData(document.getElementById('surveyForm')));  // includes name, email, phone, age, gender etc.
  data.timestamp=new Date().toISOString(); data.language=currentLang; data.sessionId=sessionId;
  const surveys=JSON.parse(localStorage.getItem('kasa_surveys')||'[]'); surveys.push(data);
  localStorage.setItem('kasa_surveys',JSON.stringify(surveys));
  fetch(`${API_BASE}/survey`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).catch(()=>{});
  document.getElementById('surveyForm').innerHTML=`<div class="survey-thanks"><div class="thanks-icon">🙏</div><h3>Thank you!</h3><p>Your feedback helps us improve health information for young people in Ghana.</p><button type="button" class="survey-submit-btn" onclick="closeSurvey()">Close</button></div>`;
});

// ── History panel ─────────────────────────────────────────────────────────────
document.getElementById('historyBtn').addEventListener('click',()=>{
  loadHistory();
  const p=document.getElementById('historyPanel'); p.classList.remove('hidden'); setTimeout(()=>p.classList.add('visible'),10);
});
document.getElementById('historyClose').addEventListener('click',()=>{ const p=document.getElementById('historyPanel'); p.classList.remove('visible'); setTimeout(()=>p.classList.add('hidden'),300); });
function loadHistory() {
  const sessions=JSON.parse(localStorage.getItem('kasa_sessions')||'[]');
  const list=document.getElementById('historyList');
  if(!sessions.length){list.innerHTML='<p class="history-empty">No saved conversations yet.</p>';return;}
  list.innerHTML=sessions.map(s=>`<div class="history-item"><div class="history-lang">${LANG_CONFIG[s.language]?.name||s.language}</div><div class="history-preview">${s.preview}…</div><div class="history-time">${new Date(s.timestamp).toLocaleDateString()}</div></div>`).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// ── CHAT TAB ──────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
const micBtn   = document.getElementById('micBtn');
const micLabel = document.getElementById('micLabel');
const voiceViz = document.getElementById('voiceViz');
let chatRecording = false;

micBtn.addEventListener('mousedown', startChatRecord);
micBtn.addEventListener('touchstart',(e)=>{e.preventDefault();startChatRecord();});
micBtn.addEventListener('mouseup', stopChatRecord);
micBtn.addEventListener('touchend',(e)=>{e.preventDefault();stopChatRecord();});
micBtn.addEventListener('mouseleave',()=>{if(chatRecording)stopChatRecord();});

async function startChatRecord() {
  if(chatRecording) return;
  try {
    await startWAVRecording(); chatRecording=true;
    micBtn.classList.add('recording'); micLabel.textContent='Release';
    voiceViz.classList.add('recording'); animateBars(voiceViz);
  } catch(e) { showError('Microphone access denied.'); }
}
function stopChatRecord() {
  if(!chatRecording) return; chatRecording=false;
  micBtn.classList.remove('recording'); micLabel.textContent='Hold to speak';
  voiceViz.classList.remove('recording'); resetBars(voiceViz);
  const blob = stopWAVRecording();
  if(blob) sendVoiceMessage(blob);
}

async function sendVoiceMessage(audioBlob) {
  showWelcome(false); showProcessing('Converting speech to text…'); setStatus('busy');
  const fd = new FormData();
  fd.append('audio', audioBlob, 'recording.wav');
  fd.append('language', currentLang);
  try {
    const asrR = await fetch(`${API_BASE}/asr`,{method:'POST',body:fd});
    const asrD = await asrR.json();
    if(!asrR.ok) throw new Error(asrD.detail||'ASR failed');
    if(!asrD.transcript) throw new Error('Could not understand speech. Please try again.');
    addMessage('user', asrD.transcript, {isVoice:true});

    setProcessingText('Getting your answer…');
    const chatR = await fetch(`${API_BASE}/chat`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:asrD.transcript,language:currentLang,history:conversationHistory.slice(-6)})});
    const chatD = await chatR.json();
    if(!chatR.ok) throw new Error(chatD.detail||'LLM error');
    conversationHistory.push({role:'user',content:chatD.question_translated});
    conversationHistory.push({role:'assistant',content:chatD.answer_english});
    saveSession();

    setProcessingText('Preparing voice response…');
    let audioB64=null,audioFmt=null;
    try {
      const ttsR = await fetch(`${API_BASE}/tts`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:chatD.answer_local,language:currentLang,speaker:currentSpeaker})});
      const ttsD = await ttsR.json();
      if(ttsD.audio_base64){audioB64=ttsD.audio_base64;audioFmt=ttsD.format||'wav';}
    } catch(e){}

    hideProcessing(); setStatus('ok');
    addMessage('bot',chatD.answer_local,{english:chatD.answer_english,audioB64,audioFmt,lang:currentLang,langName:chatD.language_name,questionOriginal:asrD.transcript,questionEnglish:chatD.question_translated});
    if(audioB64) playAudio(audioB64,audioFmt);
  } catch(err){ hideProcessing();setStatus('error');showError(err.message);setTimeout(()=>setStatus('ok'),4000); }
}

async function sendTextMessage(text) {
  showWelcome(false); textInput.value=''; textInput.style.height='auto';
  addMessage('user',text,{}); showTyping(); setStatus('busy');
  try {
    const r = await fetch(`${API_BASE}/chat`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text,language:currentLang,history:conversationHistory.slice(-6)})});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail||'Server error');
    conversationHistory.push({role:'user',content:d.question_translated});
    conversationHistory.push({role:'assistant',content:d.answer_english});
    saveSession(); removeTyping(); setStatus('ok');
    addMessage('bot',d.answer_local,{english:d.answer_english,lang:currentLang,langName:d.language_name,ttsSupported:true,questionOriginal:text,questionEnglish:d.question_translated});
  } catch(err){ removeTyping();setStatus('error');showError(err.message);setTimeout(()=>setStatus('ok'),4000); }
}

function addMessage(role, text, extras={}) {
  const div=document.createElement('div'); div.className=`message ${role}`;
  const avatar=document.createElement('div'); avatar.className='msg-avatar';
  avatar.textContent=role==='user'?'👤':'🏥';
  const content=document.createElement('div'); content.className='msg-content';
  const bubble=document.createElement('div'); bubble.className='msg-bubble'; bubble.textContent=text;
  content.appendChild(bubble);
  if(extras.isVoice){const n=document.createElement('div');n.className='msg-lang-note';n.innerHTML=`🎤 Spoken in <strong>${LANG_CONFIG[currentLang].name}</strong>`;content.appendChild(n);}
  if(role==='bot'&&extras.english&&extras.english!==text){
    const tb=document.createElement('div');tb.className='msg-translation';
    tb.innerHTML=`<div class="msg-translation-label">English</div><div>${extras.english}</div>`;content.appendChild(tb);
  }
  if(role==='bot'){
    // Per-message disclaimer
    const disc=document.createElement('div'); disc.className='msg-disclaimer';
    disc.innerHTML='⚕️ <strong>General information only.</strong> For personal or urgent concerns contact <strong>SHEplus Ghana</strong>: <a href="tel:0550545672" style="color:var(--green-light)">055 054 5672</a> / <a href="tel:0800001122" style="color:var(--green-light)">0800 00 11 22</a>';
    content.appendChild(disc);

    const actions=document.createElement('div'); actions.className='msg-actions';
    // Play / TTS button
    if(extras.audioB64){
      actions.appendChild(makePlayBtn(extras.langName||LANG_CONFIG[currentLang].name, extras.audioB64, extras.audioFmt||'wav', actions, text, extras.lang||currentLang));
      actions.appendChild(makeDlAudioBtn(extras.audioB64, extras.audioFmt||'wav', extras.lang||currentLang));
    } else {
      const tb=document.createElement('button'); tb.className='action-btn play-btn';
      tb.innerHTML=`🔊 Hear in ${extras.langName||LANG_CONFIG[extras.lang||currentLang].name}`;
      tb.addEventListener('click',async()=>{
        tb.disabled=true; tb.textContent='Loading…';
        try {
          const r=await fetch(`${API_BASE}/tts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,language:extras.lang||currentLang,speaker:currentSpeaker})});
          const d=await r.json();
          if(d.audio_base64){
            tb.replaceWith(makePlayBtn(extras.langName,d.audio_base64,d.format||'wav',actions,text,extras.lang||currentLang));
            actions.appendChild(makeDlAudioBtn(d.audio_base64,d.format||'wav',extras.lang||currentLang));
            playAudio(d.audio_base64,d.format||'wav');
          } else tb.textContent='Unavailable';
        } catch{tb.textContent='Unavailable';}
      });
      actions.appendChild(tb);
    }
    // Download text
    const dlT=document.createElement('button'); dlT.className='action-btn dl-btn'; dlT.innerHTML='⬇ Text';
    dlT.addEventListener('click',()=>downloadText(text,extras.english,extras.lang||currentLang));
    actions.appendChild(dlT);

    content.appendChild(actions);
    // Add thumbs using the reusable widget
    content.appendChild(makeThumbs(extras, text, currentLang, 'chat'));
  }
  div.appendChild(avatar); div.appendChild(content); document.getElementById('messages').appendChild(div); scrollToBottom();
}

function makePlayBtn(langName, b64, fmt, actions, text, lang) {
  const btn=document.createElement('button'); btn.className='action-btn play-btn';
  btn.innerHTML=`▶ Play in ${langName}`;
  btn.addEventListener('click',()=>{
    if(btn.classList.contains('playing')){stopAudio();btn.classList.remove('playing');btn.innerHTML=`▶ Play in ${langName}`;}
    else{document.querySelectorAll('.play-btn.playing').forEach(b=>{b.classList.remove('playing');b.innerHTML=b.innerHTML.replace('⏸','▶');});btn.classList.add('playing');btn.innerHTML='⏸ Playing…';
      playAudio(b64,fmt,()=>{btn.classList.remove('playing');btn.innerHTML=`▶ Play in ${langName}`;});}
  }); return btn;
}
function makeDlAudioBtn(b64, fmt, lang) {
  const btn=document.createElement('button'); btn.className='action-btn dl-btn'; btn.innerHTML='⬇ Audio';
  btn.addEventListener('click',()=>downloadAudio(b64,fmt,lang)); return btn;
}

// ── Thumbs feedback functions ─────────────────────────────────────────────────
let _feedbackExtras = null;
let _feedbackLang   = null;
let _feedbackText   = null;
let _fbRecording    = false;
let _fbAudioBlob    = null;

// ── makeThumbs — reusable thumbs up/down widget for any output ─────────────────
// context: 'chat' | 'agent' | 'transcribe'
function makeThumbs(extras, text, lang, context) {
  const wrap = document.createElement('div');
  wrap.className = 'thumbs-wrap';

  const thumbUp = document.createElement('button');
  thumbUp.className = 'thumb-btn thumb-up';
  thumbUp.innerHTML = '👍'; thumbUp.title = 'This was helpful';

  const thumbDown = document.createElement('button');
  thumbDown.className = 'thumb-btn thumb-down';
  thumbDown.innerHTML = '👎'; thumbDown.title = 'This needs improvement';

  thumbUp.addEventListener('click', () => {
    if(thumbUp.classList.contains('rated')) return;
    thumbUp.classList.add('rated','active'); thumbDown.classList.add('rated');
    thumbUp.innerHTML = '👍✓';
    // Track thumbs UP
    submitRating('up', extras, text, lang, context, '');
  });

  thumbDown.addEventListener('click', () => {
    if(thumbDown.classList.contains('rated')) return;
    thumbDown.classList.add('rated','active'); thumbUp.classList.add('rated');
    thumbDown.innerHTML = '👎✓';
    // Open correction modal for thumbs DOWN
    openFeedbackModal(extras, text, lang);
  });

  wrap.appendChild(thumbUp); wrap.appendChild(thumbDown);
  return wrap;
}

// ── submitRating — handles BOTH up and down ratings ────────────────────────────
async function submitRating(rating, extras, text, lang, context, reason) {
  const payload = {
    rating,
    language:          lang,
    context,           // chat | agent | transcribe
    question_original: extras?.questionOriginal || '',
    question_english:  extras?.questionEnglish  || '',
    answer_local:      text || '',
    answer_english:    extras?.english || '',
    reason,
    session_id:        sessionId,
    timestamp:         new Date().toISOString(),
  };
  try {
    await fetch(`${API_BASE}/feedback/rating`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
  } catch(e) { console.error('Rating error:', e); }
}

function openFeedbackModal(extras, text, lang) {
  _feedbackExtras = extras; _feedbackText = text; _feedbackLang = lang;
  _fbAudioBlob = null;
  // Reset modal
  document.getElementById('fbReason').value = '';
  document.getElementById('fbCorrection').value = '';
  document.getElementById('fbRecordLabel').textContent = 'Hold to record correction';
  document.getElementById('fbAudioStatus').textContent = '';
  // Show modal
  const m = document.getElementById('feedbackModal');
  m.classList.remove('hidden'); setTimeout(()=>m.classList.add('visible'),10);
}

function closeFeedbackModal() {
  const m = document.getElementById('feedbackModal');
  m.classList.remove('visible'); setTimeout(()=>m.classList.add('hidden'),300);
}

async function submitThumbsFeedback(rating, extras, text, lang, audioBlob) {
  const fd = new FormData();
  fd.append('rating',            rating);
  fd.append('language',          lang);
  fd.append('question_original', extras?.questionOriginal || '');
  fd.append('question_english',  extras?.questionEnglish  || '');
  fd.append('answer_local',      text || '');
  fd.append('answer_english',    extras?.english || '');
  fd.append('correction_text',   document.getElementById('fbCorrection')?.value || '');
  fd.append('reason',            document.getElementById('fbReason')?.value || '');
  fd.append('session_id',        sessionId);
  if (audioBlob) fd.append('audio', audioBlob, 'correction.wav');
  try {
    await fetch(`${API_BASE}/feedback/thumbs`, { method: 'POST', body: fd });
  } catch(e) { console.error('Feedback error:', e); }
}

// Feedback modal mic
const fbMicBtn = document.getElementById('fbMicBtn');
if(fbMicBtn) {
  fbMicBtn.addEventListener('mousedown', startFbRecord);
  fbMicBtn.addEventListener('touchstart', (e)=>{e.preventDefault();startFbRecord();});
  fbMicBtn.addEventListener('mouseup', stopFbRecord);
  fbMicBtn.addEventListener('touchend', (e)=>{e.preventDefault();stopFbRecord();});
  fbMicBtn.addEventListener('mouseleave', ()=>{ if(_fbRecording) stopFbRecord(); });
}

async function startFbRecord() {
  if(_fbRecording) return;
  try {
    await startWAVRecording(); _fbRecording = true;
    document.getElementById('fbRecordLabel').textContent = 'Release to stop…';
    fbMicBtn.classList.add('recording');
  } catch(e) { alert('Microphone access denied.'); }
}

function stopFbRecord() {
  if(!_fbRecording) return; _fbRecording = false;
  fbMicBtn.classList.remove('recording');
  const blob = stopWAVRecording();
  if(blob) {
    _fbAudioBlob = blob;
    document.getElementById('fbRecordLabel').textContent = '✅ Audio recorded';
    document.getElementById('fbAudioStatus').textContent = 'Audio ready to submit';
  } else {
    document.getElementById('fbRecordLabel').textContent = 'Hold to record correction';
  }
}

document.getElementById('feedbackModal')?.addEventListener('click',(e)=>{ if(e.target===document.getElementById('feedbackModal')) closeFeedbackModal(); });
document.getElementById('fbClose')?.addEventListener('click', closeFeedbackModal);

document.getElementById('fbSubmitBtn')?.addEventListener('click', async ()=>{
  document.getElementById('fbSubmitBtn').textContent = 'Submitting…';
  document.getElementById('fbSubmitBtn').disabled = true;
  const reason = document.getElementById('fbReason')?.value || '';
  // Track thumbs down rating
  await submitRating('down', _feedbackExtras, _feedbackText, _feedbackLang, 'correction', reason);
  await submitThumbsFeedback('down', _feedbackExtras, _feedbackText, _feedbackLang, _fbAudioBlob);
  document.getElementById('feedbackModal').innerHTML = `
    <div class="modal-card" style="text-align:center;padding:40px 24px">
      <div style="font-size:2rem;margin-bottom:12px">🙏</div>
      <h3 style="color:var(--green-light);margin-bottom:8px">Thank you!</h3>
      <p style="color:var(--text-2);font-size:0.87rem;line-height:1.6">Your correction helps us improve Kasa Health for everyone.</p>
      <button onclick="closeFeedbackModal()" class="survey-submit-btn" style="margin-top:20px;width:auto;padding:10px 28px">Close</button>
    </div>`;
  setTimeout(closeFeedbackModal, 3000);
});

function showTyping(){const d=document.createElement('div');d.id='typingIndicator';d.className='message typing-indicator';d.innerHTML=`<div class="msg-avatar">🏥</div><div class="msg-content"><div class="msg-bubble"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;document.getElementById('messages').appendChild(d);scrollToBottom();}
function removeTyping(){document.getElementById('typingIndicator')?.remove();}

// ─────────────────────────────────────────────────────────────────────────────
// ── TRANSCRIBE TAB ─────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
const transMicBtn   = document.getElementById('transMicBtn');
const transMicLabel = document.getElementById('transMicLabel');
const transViz      = document.getElementById('transViz');
let transRecording  = false;
let transHistory    = JSON.parse(localStorage.getItem('kasa_transcriptions')||'[]');

transMicBtn.addEventListener('mousedown', startTransRecord);
transMicBtn.addEventListener('touchstart',(e)=>{e.preventDefault();startTransRecord();});
transMicBtn.addEventListener('mouseup', stopTransRecord);
transMicBtn.addEventListener('touchend',(e)=>{e.preventDefault();stopTransRecord();});
transMicBtn.addEventListener('mouseleave',()=>{if(transRecording)stopTransRecord();});

async function startTransRecord() {
  if(transRecording) return;
  try {
    await startWAVRecording(); transRecording=true;
    transMicBtn.classList.add('recording'); transMicLabel.textContent='Release to transcribe';
    transViz.classList.add('recording'); animateBars(transViz);
  } catch(e){showError('Microphone access denied.');}
}
function stopTransRecord() {
  if(!transRecording) return; transRecording=false;
  transMicBtn.classList.remove('recording'); transMicLabel.textContent='Processing…';
  transViz.classList.remove('recording'); resetBars(transViz);
  const blob = stopWAVRecording();
  if(blob) doTranscribe(blob); else transMicLabel.textContent='Hold to record';
}

async function doTranscribe(audioBlob) {
  const fd = new FormData();
  fd.append('audio', audioBlob, 'recording.wav');
  fd.append('language', currentLang);
  try {
    const r = await fetch(`${API_BASE}/asr`,{method:'POST',body:fd});
    const d = await r.json();
    if(!r.ok||!d.transcript) throw new Error(d.detail||'Could not transcribe');
    showTranscribeResult(d.transcript);
    // Save to history
    transHistory.unshift({text:d.transcript,language:currentLang,timestamp:new Date().toISOString()});
    transHistory=transHistory.slice(0,20);
    localStorage.setItem('kasa_transcriptions',JSON.stringify(transHistory));
    renderTransHistory();
  } catch(e){ showError(e.message); }
  finally { transMicLabel.textContent='Hold to record'; }
}

function showTranscribeResult(text) {
  document.getElementById('transcribeResult').style.display='block';
  document.getElementById('transcriptBox').textContent=text;
  document.getElementById('transLangBadge').textContent=LANG_CONFIG[currentLang].name;
  // Add thumbs below transcript box
  const existing = document.getElementById('transThumbs');
  if(existing) existing.remove();
  const thumbsEl = makeThumbs({
    questionOriginal: 'Speech transcription',
    questionEnglish:  'Speech transcription',
    english:          text,
  }, text, currentLang, 'transcribe');
  thumbsEl.id = 'transThumbs';
  thumbsEl.style.marginTop = '10px';
  document.getElementById('transcribeResult').appendChild(thumbsEl);
}

document.getElementById('transCopyBtn').addEventListener('click',()=>{
  const text = document.getElementById('transcriptBox').textContent;
  navigator.clipboard.writeText(text).then(()=>{ document.getElementById('transCopyBtn').textContent='✅ Copied!'; setTimeout(()=>{document.getElementById('transCopyBtn').textContent='📋 Copy';},2000); });
});
document.getElementById('transDownloadBtn').addEventListener('click',()=>{
  const text = document.getElementById('transcriptBox').textContent;
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([text],{type:'text/plain'}));
  a.download=`transcription-${currentLang}-${Date.now()}.txt`; a.click(); URL.revokeObjectURL(a.href);
});

function renderTransHistory() {
  const list = document.getElementById('transHistoryList');
  if(!transHistory.length){list.innerHTML='<p style="color:var(--text-3);font-size:0.8rem">No transcriptions yet.</p>';return;}
  list.innerHTML=transHistory.map((t,i)=>`
    <div class="trans-item" onclick="document.getElementById('transcriptBox').textContent=this.dataset.text;document.getElementById('transcribeResult').style.display='block';" data-text="${t.text.replace(/"/g,'&quot;')}">
      <span class="trans-lang">${LANG_CONFIG[t.language]?.name||t.language}</span>
      <span class="trans-preview">${t.text.slice(0,60)}${t.text.length>60?'…':''}</span>
      <span class="trans-time">${new Date(t.timestamp).toLocaleTimeString()}</span>
    </div>`).join('');
}
renderTransHistory();

// ─────────────────────────────────────────────────────────────────────────────
// ── VOICE AGENT TAB ────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
const agentMicBtn   = document.getElementById('agentMicBtn');
const agentMicLabel = document.getElementById('agentMicLabel');
const agentViz      = document.getElementById('agentViz');
const agentConv     = document.getElementById('agentConversation');
const agentPill     = document.getElementById('agentStatusPill');
const agentAvatar   = document.getElementById('agentAvatar');
let agentRecording  = false;
let agentSpeaking   = false;

agentMicBtn.addEventListener('mousedown', startAgentRecord);
agentMicBtn.addEventListener('touchstart',(e)=>{e.preventDefault();startAgentRecord();});
agentMicBtn.addEventListener('mouseup', stopAgentRecord);
agentMicBtn.addEventListener('touchend',(e)=>{e.preventDefault();stopAgentRecord();});
agentMicBtn.addEventListener('mouseleave',()=>{if(agentRecording)stopAgentRecord();});

document.getElementById('agentClearBtn').addEventListener('click',()=>{
  agentHistory=[];
  agentConv.innerHTML=`<div class="agent-bubble agent-bubble-system">👋 Hi! I'm Kasa, your health companion. Ask me anything about your health — I'll understand and reply in your language. Press the button below to start talking.</div>`;
  setAgentStatus('Ready','');
});

async function startAgentRecord() {
  if(agentRecording||agentSpeaking) return;
  try {
    await startWAVRecording(); agentRecording=true;
    agentMicBtn.classList.add('recording'); agentMicLabel.textContent='Release to send';
    agentViz.classList.add('recording'); animateBars(agentViz);
    setAgentStatus('Listening…','listening');
  } catch(e){showError('Microphone access denied.');}
}

function stopAgentRecord() {
  if(!agentRecording) return; agentRecording=false;
  agentMicBtn.classList.remove('recording'); agentMicLabel.textContent='Processing…';
  agentViz.classList.remove('recording'); resetBars(agentViz);
  setAgentStatus('Thinking…','thinking');
  const blob = stopWAVRecording();
  if(blob) runAgentTurn(blob); else { agentMicLabel.textContent='Hold to talk'; setAgentStatus('Ready',''); }
}

async function runAgentTurn(audioBlob) {
  const fd = new FormData();
  fd.append('audio', audioBlob, 'recording.wav');
  fd.append('language', currentLang);
  fd.append('speaker', currentSpeaker);
  fd.append('history', JSON.stringify(agentHistory.slice(-8)));

  try {
    const r = await fetch(`${API_BASE}/agent`,{method:'POST',body:fd});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail||'Agent error');

    // Show user bubble
    appendAgentBubble('user', d.transcript, d.language_name||LANG_CONFIG[currentLang].name);
    // Show agent bubble with thumbs
    appendAgentBubble('agent', d.answer_local, 'Kasa', d.answer_english, {
      question: d.transcript, questionEnglish: d.question_english
    });

    // Update history
    agentHistory.push(d.history_entry_user);
    agentHistory.push(d.history_entry_agent);

    // Play audio response
    if(d.audio_base64){
      agentSpeaking=true; setAgentStatus('Speaking…','speaking');
      agentAvatar.classList.add('speaking');
      const audio = playAudio(d.audio_base64, d.audio_format||'wav', ()=>{
        agentSpeaking=false; agentMicLabel.textContent='Hold to talk';
        setAgentStatus('Ready',''); agentAvatar.classList.remove('speaking');
      });
    } else {
      agentMicLabel.textContent='Hold to talk'; setAgentStatus('Ready','');
    }
  } catch(e){
    appendAgentBubble('system','Sorry, something went wrong. Please try again.','');
    agentMicLabel.textContent='Hold to talk'; setAgentStatus('Ready','');
    console.error(e);
  }
}

function appendAgentBubble(role, text, label, englishText='', extras={}) {
  const wrap = document.createElement('div');
  wrap.className = `agent-bubble-wrap agent-bubble-${role}`;

  const labelEl = document.createElement('div');
  if(label) { labelEl.className='agent-bubble-label'; labelEl.textContent=`${role==='user'?'👤':role==='agent'?'🤖':'ℹ️'} ${label}`; wrap.appendChild(labelEl); }

  const bubble = document.createElement('div');
  bubble.className = `agent-bubble agent-bubble-${role}`;
  bubble.textContent = text;
  wrap.appendChild(bubble);

  if(englishText && englishText!==text) {
    const eng = document.createElement('div');
    eng.className = 'agent-bubble-english'; eng.textContent = englishText;
    wrap.appendChild(eng);
  }

  // Per-message disclaimer on agent responses
  if(role === 'agent') {
    const agentDisc = document.createElement('div');
    agentDisc.className = 'msg-disclaimer';
    agentDisc.innerHTML = '⚕️ General info only. Contact <strong>SHEplus Ghana</strong>: <a href="tel:0550545672" style="color:var(--green-light)">055 054 5672</a> / <a href="tel:0800001122" style="color:var(--green-light)">0800 00 11 22</a>';
    wrap.appendChild(agentDisc);
  }

  // Add thumbs on agent responses only
  if(role === 'agent') {
    wrap.appendChild(makeThumbs({
      questionOriginal: extras.question || '',
      questionEnglish:  extras.questionEnglish || '',
      english:          englishText || '',
    }, text, currentLang, 'agent'));
  }

  agentConv.appendChild(wrap);
  agentConv.scrollTop = agentConv.scrollHeight;
}

function escHtml(str) { const d=document.createElement('div');d.textContent=str;return d.innerHTML; }

function setAgentStatus(text, state) {
  agentPill.textContent = text;
  agentPill.className = `agent-status-pill ${state?'agent-status-'+state:''}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// ── UI HELPERS ────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
function showWelcome(show){ document.getElementById('welcome').style.display=show?'flex':'none'; }
function showProcessing(text){ document.getElementById('processingText').textContent=text; document.getElementById('processingOverlay').classList.remove('hidden'); }
function setProcessingText(text){ document.getElementById('processingText').textContent=text; }
function hideProcessing(){ document.getElementById('processingOverlay').classList.add('hidden'); }
function setStatus(state){ const d=document.getElementById('statusDot');d.className='status-dot';if(state==='busy')d.classList.add('busy');if(state==='error')d.classList.add('error'); }
function scrollToBottom(){ requestAnimationFrame(()=>{ const ca=document.getElementById('chatArea');ca.scrollTop=ca.scrollHeight; }); }
function showError(msg){ const d=document.createElement('div');d.className='message';d.innerHTML=`<div class="msg-avatar" style="background:rgba(248,81,73,0.1);border-color:var(--red)">⚠️</div><div class="msg-content"><div class="msg-bubble" style="border-color:rgba(248,81,73,0.3);color:var(--red)">${msg}</div></div>`;document.getElementById('messages').appendChild(d);scrollToBottom(); }

// ── Init ──────────────────────────────────────────────────────────────────────

// ── Disclaimer modal ──────────────────────────────────────────────────────────
const disclaimerModal   = document.getElementById('disclaimerModal');
const disclaimerCheck   = document.getElementById('disclaimerCheck');
const disclaimerAgreeBtn = document.getElementById('disclaimerAgreeBtn');
const checkLabel        = document.getElementById('disclaimerCheckLabel');

// Show disclaimer on every visit (required for safeguarding)
// Only skip if user agreed in this same browser session (not across sessions)
if (!sessionStorage.getItem('kasa_disclaimer_agreed')) {
  disclaimerModal.classList.remove('hidden');
  setTimeout(() => disclaimerModal.classList.add('visible'), 10);
} else {
  disclaimerModal.classList.add('hidden');
}

// Enable agree button only when checkbox is ticked
disclaimerCheck.addEventListener('change', () => {
  if (disclaimerCheck.checked) {
    disclaimerAgreeBtn.disabled = false;
    disclaimerAgreeBtn.style.background    = 'var(--green)';
    disclaimerAgreeBtn.style.borderColor   = 'var(--green)';
    disclaimerAgreeBtn.style.color         = '#fff';
    disclaimerAgreeBtn.style.cursor        = 'pointer';
    checkLabel.style.borderColor           = 'var(--green)';
  } else {
    disclaimerAgreeBtn.disabled = true;
    disclaimerAgreeBtn.style.background    = 'var(--bg-3)';
    disclaimerAgreeBtn.style.borderColor   = 'var(--border)';
    disclaimerAgreeBtn.style.color         = 'var(--text-3)';
    disclaimerAgreeBtn.style.cursor        = 'not-allowed';
    checkLabel.style.borderColor           = 'var(--border)';
  }
});

disclaimerAgreeBtn.addEventListener('click', () => {
  sessionStorage.setItem('kasa_disclaimer_agreed', 'true');
  disclaimerModal.classList.remove('visible');
  disclaimerModal.style.opacity = '0';
  disclaimerModal.style.transition = 'opacity 0.4s';
  setTimeout(() => { disclaimerModal.classList.add('hidden'); disclaimerModal.style.opacity = ''; }, 400);
});

document.getElementById('welcome').style.display='flex';
if(window.innerWidth<768) document.getElementById('sidebar').classList.remove('open');
