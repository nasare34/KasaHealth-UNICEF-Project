// Small DOM helpers. Text always goes in through textContent, never innerHTML.
import { friendlyMessage } from './api.js';

// Usage: el('div', { class: 'x', onclick: fn }, 'some text', childNode)
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (id) => document.getElementById(id);

/* --- Toasts: gentle, temporary notes --- */
export function toast(message, { tone = 'info', timeout = 5000 } = {}) {
  const host = $('toastHost');
  if (!host) return;
  // Don't stack identical messages.
  for (const t of host.children) if (t.dataset.msg === message) return;
  const icon = { info: '💚', warn: '🌤️', error: '🙏' }[tone] || '💚';
  const node = el('div', { class: `toast toast-${tone}`, role: 'status', dataset: { msg: message } },
    el('span', { class: 'toast-icon', 'aria-hidden': 'true' }, icon),
    el('span', { class: 'toast-text' }, message),
    el('button', { class: 'toast-close', 'aria-label': 'Dismiss', onclick: () => dismiss() }, '✕'));
  host.appendChild(node);
  requestAnimationFrame(() => node.classList.add('show'));
  const dismiss = () => { node.classList.remove('show'); setTimeout(() => node.remove(), 250); };
  if (timeout) setTimeout(dismiss, timeout);
}

export function toastError(err) {
  toast(err?.message || friendlyMessage(err?.code), { tone: 'warn', timeout: 7000 });
}

/* --- SHEplus disclaimer (shared by chat and agent) --- */
export function disclaimerNode(short = false) {
  const link = (num, label) => el('a', { href: `tel:${num}`, class: 'tel-link' }, label);
  return el('div', { class: 'msg-disclaimer' },
    '⚕️ ', el('strong', {}, short ? 'General info only.' : 'General information only.'),
    short ? ' If urgent, contact ' : ' For personal or urgent concerns contact ',
    el('strong', {}, 'SHEplus Ghana'), ': ',
    link('0550545672', '055 054 5672'), ' / ', link('0800001122', '0800 00 11 22'),
    ' · Emergency: ', link('112', '112'),
    el('span', { class: 'ai-note' }, '🤖 Kasa is an AI and can make mistakes. Please check important information with a health worker.'));
}

/* --- Status dot --- */
export function setStatus(mode) {
  const dot = $('statusDot');
  if (!dot) return;
  dot.className = 'status-dot' + (mode === 'busy' ? ' busy' : mode === 'error' ? ' error' : '');
  dot.title = mode === 'error' ? 'Having trouble connecting' : mode === 'busy' ? 'Working…' : 'Online';
}

/* --- Modals --- */
export function openModal(id) {
  const m = $(id);
  m.classList.remove('hidden');
  setTimeout(() => m.classList.add('visible'), 10);
}

export function closeModal(id) {
  const m = $(id);
  m.classList.remove('visible');
  setTimeout(() => m.classList.add('hidden'), 300);
}

/* --- Visualiser bars --- */
export function setBars(vizEl, level) {
  const bars = vizEl.querySelectorAll('.bar');
  const n = bars.length;
  bars.forEach((bar, i) => {
    const shape = 1 - Math.abs(i - (n - 1) / 2) / n;   // taller in the middle
    const jitter = 0.75 + Math.random() * 0.5;
    bar.style.height = `${Math.max(6, Math.min(36, 6 + level * 60 * shape * jitter))}px`;
  });
}

export function resetBars(vizEl) {
  vizEl.querySelectorAll('.bar').forEach((b) => { b.style.height = '8px'; });
}

// Moves the bars with the live mic level. Call the function it returns to stop.
export function animateFromLevel(vizEl, readLevel) {
  let raf = 0, running = true;
  const frame = () => { if (!running) return; setBars(vizEl, readLevel()); raf = requestAnimationFrame(frame); };
  frame();
  return () => { running = false; cancelAnimationFrame(raf); resetBars(vizEl); };
}

export function downloadText(content, filename) {
  const a = el('a', { href: URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' })), download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// Turns a button into hold-to-talk. Works with mouse, touch, and Space/Enter on the keyboard.
export function holdToTalk(button, onStart, onStop) {
  let active = false;
  const start = (e) => { if (e) e.preventDefault(); if (active) return; active = true; onStart(); };
  const stop = (e) => { if (e) e.preventDefault(); if (!active) return; active = false; onStop(); };
  button.addEventListener('pointerdown', (e) => { if (e.button === 0 || e.pointerType !== 'mouse') { button.setPointerCapture?.(e.pointerId); start(e); } });
  button.addEventListener('pointerup', stop);
  button.addEventListener('pointercancel', stop);
  button.addEventListener('lostpointercapture', () => stop());
  button.addEventListener('contextmenu', (e) => e.preventDefault());
  button.addEventListener('keydown', (e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) start(e); });
  button.addEventListener('keyup', (e) => { if (e.key === ' ' || e.key === 'Enter') stop(e); });
  return { get active() { return active; }, release: () => stop() };
}
