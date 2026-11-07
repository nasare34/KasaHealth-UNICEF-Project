// Kasa Health admin dashboard.
// The token only lives in this tab (sessionStorage) and goes out as X-Admin-Token.
import { el } from './ui.js';

const $ = (id) => document.getElementById(id);
let token = '';
let providers = [];
let timers = [];

const LANG = { tw: 'Twi', dag: 'Dagbani', ee: 'Ewe' };
const STATUS_TEXT = { ok: 'Working', slow: 'Slow', warning: 'Needs a look', down: 'Not working', off: 'Not set up' };
const BANNER_ICON = { ok: '✅', warning: '🟡', degraded: '🟠', critical: '🔴' };

function msg(text, bad = false) {
  $('adminMsg').textContent = text;
  $('adminMsg').style.color = bad ? 'var(--red)' : '';
}

async function call(path, { method = 'GET', json } = {}) {
  const res = await fetch(`/api/admin/${path}`, {
    method,
    headers: { 'X-Admin-Token': token, ...(json ? { 'Content-Type': 'application/json' } : {}) },
    body: json ? JSON.stringify(json) : undefined,
  });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const code = body?.error?.code;
    throw new Error(code === 'forbidden' || code === 'not_found'
      ? 'That token was not accepted (or ADMIN_TOKEN is not set on the server).'
      : (body?.error?.message || `Request failed (${res.status})`));
  }
  return body;
}

const ago = (ts) => {
  if (!ts) return 'never';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
};
const fmtMs = (ms) => (ms == null ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

// --- Health ---
function renderHealth(data) {
  const { last, history = [] } = data;
  if (!last) {
    $('statusBanner').className = 'status-banner none';
    $('statusIcon').textContent = '🩺';
    $('statusHeadline').textContent = 'No health check has been run yet.';
    $('statusSub').textContent = 'Press “Run quick check” to test the APIs. Checks only run when you press a button, so nothing is spent in the background.';
    $('healthGroups').replaceChildren(el('p', { class: 'muted' }, 'Results will appear here after your first check.'));
    return;
  }
  const sum = last.summary;
  const banner = $('statusBanner');
  banner.className = `status-banner ${sum.level}`;
  $('statusIcon').textContent = BANNER_ICON[sum.level] || 'ℹ️';
  $('statusHeadline').textContent = sum.headline;
  $('statusSub').textContent = `${sum.llm_working} AI provider(s) working · ${sum.down} down · ${sum.warnings} warning(s) · `
    + `last checked ${ago(last.checked_at)}${last.full ? ' (full check)' : ''} · `
    + 'checks only run when you press a button';

  const recent = history.slice(-24);
  const groups = {};
  for (const r of last.results) (groups[r.group] ||= []).push(r);
  const order = ['AI providers', 'Khaya', 'Storage & delivery', 'Other'];
  const rank = { down: 0, warning: 1, slow: 2, ok: 3, off: 4 };

  $('healthGroups').replaceChildren(...order.filter((g) => groups[g]).map((g) => {
    const items = groups[g].sort((a, b) => (rank[a.status] - rank[b.status]) || a.name.localeCompare(b.name));
    return el('div', { class: 'health-group' },
      el('h3', {}, g),
      el('div', { class: 'health-grid' }, ...items.map((r) => {
        const strip = el('div', { class: 'hc-strip', title: 'Recent checks' },
          ...recent.map((h) => el('span', { class: h.s?.[r.service] || 'none', title: `${new Date(h.t * 1000).toLocaleString()}: ${STATUS_TEXT[h.s?.[r.service]] || 'not checked'}` })));
        return el('div', { class: `health-card ${r.status}` },
          el('div', { class: 'hc-top' },
            el('span', { class: `dot ${r.status}`, 'aria-hidden': 'true' }),
            el('span', { class: 'hc-name' }, r.name),
            el('span', { class: 'hc-ms' }, `${STATUS_TEXT[r.status] || r.status}${r.ms != null ? ' · ' + fmtMs(r.ms) : ''}`)),
          r.detail ? el('div', { class: 'hc-detail' }, r.detail) : null,
          r.advice ? el('div', { class: 'hc-advice' }, el('strong', {}, 'What to do: '), r.advice) : null,
          recent.length ? strip : null);
      })));
  }));
}

async function loadHealth() {
  renderHealth(await call('health'));
}

async function runCheck(full, button) {
  button.disabled = true;
  const label = button.textContent;
  button.textContent = full ? 'Testing all languages…' : 'Checking…';
  msg(full ? 'Running the full check (about 10–30 seconds)…' : 'Running a quick check…');
  try {
    renderHealth(await call('health/run', { method: 'POST', json: { full } }));
    msg(`Check finished at ${new Date().toLocaleTimeString()}.`);
    await loadProviders();
  } catch (err) {
    msg(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// --- Overview ---
function kpi(label, value, sub = '', alert = false) {
  return el('div', { class: `kpi${alert ? ' alert' : ''}` },
    el('div', { class: 'num' }, String(value)), el('div', { class: 'lbl' }, label), sub ? el('div', { class: 'sub' }, sub) : null);
}

async function loadOverview() {
  const o = await call('overview');
  const t = o.totals || {}, d = o.last_24h || {};
  const langs = o.ratings_by_language || {};
  const up = Object.values(langs).reduce((n, r) => n + r.up, 0);
  const down = Object.values(langs).reduce((n, r) => n + r.down, 0);
  const pending = Object.values(o.sync || {}).reduce((n, s) => n + (s.failed || 0) + (s.pending || 0), 0);
  const incidents = (o.incidents_24h || []).filter((i) => !String(i.code).startsWith('recovered:'));
  const incTotal = incidents.reduce((n, i) => n + i.count, 0);
  const safety = o.safety_7d || {};

  $('uptime').textContent = `Server up ${Math.round(o.uptime_s / 3600)} h`;
  $('kpis').replaceChildren(
    kpi('Ratings', t.rating || 0, `${d.rating || 0} today`),
    kpi('Helpful', up + down ? `${Math.round((up / (up + down)) * 100)}%` : '—', `${up} 👍 · ${down} 👎`),
    kpi('Corrections', t.feedback || 0, `${d.feedback || 0} today`),
    kpi('Surveys', t.survey || 0, `${d.survey || 0} today`),
    kpi('Safety contacts (7 days)', safety.contact || 0, `${safety.flag || 0} flagged messages`, (safety.contact || 0) > 0),
    kpi('Errors (24 h)', incTotal, incTotal ? 'see below' : 'all quiet', incTotal > 0),
    kpi('Waiting to sync', pending, pending ? 'press “Retry failed syncs”' : 'all synced', pending > 0),
  );

  const rows = Object.entries(langs);
  $('langBars').replaceChildren(...(rows.length ? rows.map(([lang, r]) => {
    const total = r.up + r.down;
    const pct = total ? Math.round((r.up / total) * 100) : 0;
    return el('div', { class: 'lang-row' },
      el('span', {}, LANG[lang] || lang),
      el('div', { class: 'lang-bar', title: `${pct}% helpful` }, el('span', { style: `width:${pct}%` })),
      el('span', { class: 'muted' }, `${pct}% of ${total}`));
  }) : [el('p', { class: 'muted' }, 'No ratings yet.')]));

  $('incidentSummary').replaceChildren(...(incidents.length
    ? incidents.slice(0, 8).map((i) => el('div', { class: 'inc-row' }, el('code', {}, i.code), el('strong', {}, `× ${i.count}`)))
    : [el('p', { class: 'muted' }, 'No problems in the last 24 hours. 🎉')]));

  const a = o.alerts || {};
  $('alertInfo').textContent = a.email_configured
    ? `Alerts go to ${a.to}.`
    : `Email isn't set up yet, so alerts are only saved here. Add RESEND_API_KEY or SMTP settings (see DEPLOY.md). Recipient: ${a.to}.`;
}

// --- Providers ---
function providerPill(p) {
  if (!p.configured) return el('span', { class: 'pill off' }, 'No API key');
  if (!p.enabled) return el('span', { class: 'pill off' }, 'Switched off');
  if (p.circuit === 'open') return el('span', { class: 'pill bad' }, `Resting ${p.retry_in_s}s`);
  if (p.circuit === 'half-open') return el('span', { class: 'pill warn' }, 'Retrying');
  if (p.failures && p.recent_success_rate !== null && p.recent_success_rate < 0.8) return el('span', { class: 'pill warn' }, 'Unstable');
  return el('span', { class: 'pill ok' }, p.successes ? 'Working' : 'Ready');
}

function renderProviders() {
  $('llmRows').replaceChildren(...providers.map((p, i) => {
    const toggle = el('input', { type: 'checkbox', 'aria-label': `Use ${p.label}` });
    toggle.checked = p.enabled && p.configured;
    toggle.disabled = !p.configured;
    toggle.addEventListener('change', () => { p.enabled = toggle.checked; });
    const up = el('button', { type: 'button', class: 'move', 'aria-label': 'Move up', disabled: i === 0 }, '▲');
    const down = el('button', { type: 'button', class: 'move', 'aria-label': 'Move down', disabled: i === providers.length - 1 }, '▼');
    up.addEventListener('click', () => { [providers[i - 1], providers[i]] = [providers[i], providers[i - 1]]; renderProviders(); });
    down.addEventListener('click', () => { [providers[i + 1], providers[i]] = [providers[i], providers[i + 1]]; renderProviders(); });
    const rate = p.recent_success_rate === null ? '—' : `${Math.round(p.recent_success_rate * 100)}%`;
    return el('tr', {},
      el('td', {}, String(i + 1)), el('td', {}, el('strong', {}, p.label)), el('td', {}, el('code', {}, p.model)),
      el('td', {}, toggle), el('td', {}, providerPill(p)),
      el('td', {}, `${rate} (${p.successes}✓ ${p.failures}✗)`), el('td', {}, p.avg_ms ? fmtMs(p.avg_ms) : '—'),
      el('td', { class: 'err' }, p.last_error ? `${p.last_error} · ${ago(p.last_error_at)}` : '—'),
      el('td', {}, up, down));
  }));
}

async function loadProviders() {
  const data = await call('llm');
  providers = data.providers;
  $('hedgeChat').textContent = data.hedge_chat_s;
  $('hedgeAgent').textContent = data.hedge_agent_s;
  renderProviders();
}

// --- Incidents & safety ---
async function loadReports() {
  const [inc, saf] = await Promise.all([call('export/incident'), call('export/safety')]);
  const recent = (rows) => (rows || []).slice(-40).reverse();
  $('incidentRows').replaceChildren(...recent(inc.incidents).map((r) => el('tr', {},
    el('td', {}, new Date(r.timestamp).toLocaleString()), el('td', {}, el('code', {}, r.code)),
    el('td', {}, r.path || '—'), el('td', {}, el('code', {}, r.request_id || '—')),
    el('td', { class: 'err' }, (r.detail || '').slice(0, 300)))));
  $('safetyRows').replaceChildren(...recent(saf.safety_reports).map((r) => el('tr', {},
    el('td', {}, new Date(r.timestamp).toLocaleString()),
    el('td', {}, el('span', { class: `pill ${r.type === 'contact' ? 'bad' : 'warn'}` }, r.type === 'contact' ? 'Contact request' : 'Flag')),
    el('td', {}, r.category || '—'),
    el('td', {}, r.type === 'contact' ? `${r.name || '(no name)'} · ${r.phone} · ${r.safe_to_call || ''}` : '—'),
    el('td', { class: 'err' }, (r.question_english || r.question_original || '').slice(0, 300)))));
}

// --- Downloads ---
const DATASETS = [
  ['survey', 'surveys', 'Surveys'], ['rating', 'ratings', 'Ratings'], ['feedback', 'feedback', 'Corrections'],
  ['incident', 'incidents', 'Errors / incidents'], ['safety', 'safety_reports', 'Safety reports'],
];

function toCSV(rows) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const cell = (v) => {
    const s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n');
}

function save(content, name, type) {
  const a = el('a', { href: URL.createObjectURL(new Blob([content], { type })), download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function renderDownloads() {
  $('downloads').replaceChildren(...DATASETS.map(([kind, key, label]) => {
    const get = async (fmt, btn) => {
      btn.disabled = true;
      try {
        const rows = (await call(`export/${kind}`))[key] || [];
        const stamp = new Date().toISOString().slice(0, 10);
        if (fmt === 'csv') save('\ufeff' + toCSV(rows), `kasa_${key}_${stamp}.csv`, 'text/csv;charset=utf-8');
        else save(JSON.stringify(rows, null, 2), `kasa_${key}_${stamp}.json`, 'application/json');
        msg(`Downloaded ${rows.length} ${label.toLowerCase()}.`);
      } catch (err) { msg(err.message, true); } finally { btn.disabled = false; }
    };
    const csv = el('button', { type: 'button' }, '⬇ CSV');
    const json = el('button', { type: 'button' }, '⬇ JSON');
    csv.addEventListener('click', () => get('csv', csv));
    json.addEventListener('click', () => get('json', json));
    return el('div', { class: 'dl' }, el('strong', {}, label), el('div', { class: 'row' }, csv, json));
  }));
}

// --- Wiring ---
async function refreshAll() {
  const results = await Promise.allSettled([loadHealth(), loadOverview(), loadProviders(), loadReports()]);
  const failed = results.find((r) => r.status === 'rejected');
  if (failed) msg(failed.reason.message, true);
  else msg(`Connected · updated ${new Date().toLocaleTimeString()}`);
}

function startAutoRefresh() {
  timers.forEach(clearInterval);
  timers = [];
  if (!$('autoRefresh').checked) return;
  timers.push(setInterval(() => Promise.allSettled([loadHealth(), loadOverview(), loadReports()]), 30000));
  timers.push(setInterval(loadProviders, 15000));
}

async function connect() {
  token = $('adminToken').value.trim();
  if (!token) { msg('Please enter the admin token.', true); return; }
  try {
    await loadProviders();          // cheapest call, so I use it to check the token too
  } catch (err) { msg(err.message, true); return; }
  sessionStorage.setItem('kasa_admin_token', token);
  $('dashboard').hidden = false;
  renderDownloads();
  await refreshAll();
  startAutoRefresh();
}

async function busy(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (err) { msg(err.message, true); } finally { button.disabled = false; }
}

$('adminConnect').addEventListener('click', connect);
$('adminToken').addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });
$('autoRefresh').addEventListener('change', startAutoRefresh);
$('quickCheck').addEventListener('click', (e) => runCheck(false, e.currentTarget));
$('fullCheck').addEventListener('click', (e) => runCheck(true, e.currentTarget));
$('saveOrder').addEventListener('click', (e) => busy(e.currentTarget, async () => {
  const data = await call('llm', { method: 'POST', json: {
    order: providers.map((p) => p.name), disabled: providers.filter((p) => !p.enabled).map((p) => p.name) } });
  providers = data.providers;
  renderProviders();
  msg('Saved. Every server will use the new order within a few seconds.');
}));
$('resetCircuits').addEventListener('click', (e) => busy(e.currentTarget, async () => {
  providers = (await call('llm/reset', { method: 'POST' })).providers;
  renderProviders();
  msg('Done — every provider will be tried again straight away.');
}));
$('testEmail').addEventListener('click', (e) => busy(e.currentTarget, async () => {
  const r = await call('alerts/test', { method: 'POST' });
  msg(`Test email sent to ${r.sent_to}. Check the inbox (and spam folder).`);
}));
$('retrySync').addEventListener('click', (e) => busy(e.currentTarget, async () => {
  const r = await call('retry-sync', { method: 'POST' });
  const n = Object.values(r.queued).reduce((a, b) => a + b, 0);
  msg(n ? `Re-sending ${n} record(s) in the background.` : 'Nothing to re-send — all synced.');
  setTimeout(loadOverview, 3000);
}));

// Same light/dark toggle as the main app.
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  $('themeToggle').textContent = t === 'light' ? '🌙' : '☀️';
}
applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
$('themeToggle').addEventListener('click', () => {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  applyTheme(next);
  try { localStorage.setItem('kasa_theme', JSON.stringify(next)); } catch { /* private mode */ }
});

// Floating up/down buttons. Each one only shows when there's somewhere to go.
function updateScrollButtons() {
  const y = window.scrollY;
  const max = document.documentElement.scrollHeight - window.innerHeight;
  $('toTop').hidden = y < 200;
  $('toBottom').hidden = max - y < 200;
}
$('toTop').addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
$('toBottom').addEventListener('click', () => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' }));
window.addEventListener('scroll', updateScrollButtons, { passive: true });
window.addEventListener('resize', updateScrollButtons);
new ResizeObserver(updateScrollButtons).observe(document.body);   // the page grows as data loads

const saved = sessionStorage.getItem('kasa_admin_token');
if (saved) { $('adminToken').value = saved; connect(); }
