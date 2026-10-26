// Shared helpers: API client, formatting, tiny markdown, tooltip, toasts, drawer, live event stream.
export const state = {
  token: null, user: null, meta: null, kpis: {}, banks: {}, bankOrder: [],
  provisional: false, quarterKey: null, quarters: [], embed: false, llm: null,
};

const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* ignore */ } },
};
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem('kpib.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('kpib.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

export function setToken(t) { state.token = t; store.set('kpib_token', t); }
export function loadToken() { state.token = store.get('kpib_token'); return state.token; }

let unauthorizedHandler = () => {};
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

export async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  let body = opts.body;
  if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
  const r = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body, credentials: 'same-origin' });
  if (r.status === 401 && !opts.noAuthRedirect) { unauthorizedHandler(); throw new Error('Session expired - please sign in again'); }
  if (!r.ok) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    const e = new Error(detail || ('HTTP ' + r.status)); e.status = r.status; throw e;
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('application/json') ? r.json() : r.text();
}

export function withAuth(url) {
  // For links that open in a new tab (files) and for the event stream in embed mode.
  if (!state.token) return url;
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(state.token);
}

export function can(perm) { return !!(state.user && state.user.permissions.includes(perm)); }

// ----------------------------------------------------------------------------- DOM
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function on(root, event, selector, fn) {
  root.addEventListener(event, e => {
    const t = e.target.closest(selector);
    if (t && root.contains(t)) fn(e, t);
  });
}
export function debounce(fn, ms = 200) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ----------------------------------------------------------------------------- formatting
const nfIN0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmtCr(v, compact = true) {
  if (v == null || isNaN(v)) return '–';
  if (compact && Math.abs(v) >= 100000) return '₹' + nf2.format(v / 100000) + ' lakh cr';
  return '₹' + nfIN0.format(v) + ' cr';
}
export function fmtVal(v, unit, opts = {}) {
  if (v == null || isNaN(v)) return '–';
  if (unit === '%') return nf2.format(v) + '%';
  return fmtCr(v, opts.compact !== false);
}
export function fmtAxis(v, unit) {
  if (unit === '%') return (Math.abs(v) < 10 ? nf2 : nf1).format(v) + '%';
  if (Math.abs(v) >= 100000) return nf1.format(v / 100000) + 'L';
  if (Math.abs(v) >= 1000) return nf1.format(v / 1000) + 'K';
  return nfIN0.format(v);
}
export function fmtBps(v) {
  if (v == null || isNaN(v)) return '–';
  const r = Math.round(v);
  return (r > 0 ? '+' : r < 0 ? '−' : '±') + Math.abs(r) + ' bps';
}
export function fmtPct(v, digits = 1) {
  if (v == null || isNaN(v)) return '–';
  return (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(digits) + '%';
}
export function fmtMs(ms) {
  if (ms == null) return '–';
  if (ms < 1000) return ms + ' ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + ' s';
  return Math.floor(ms / 60000) + ' min ' + Math.round((ms % 60000) / 1000) + ' s';
}
export function fmtTime(iso, withDate = false) {
  if (!iso) return '–';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  const t = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  return withDate ? d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) + ' ' + t : t;
}
export function bankName(code, long = false) {
  const b = state.banks[code];
  if (!b) return code === 'PEER_AVG' ? 'Peer average' : code;
  return long ? b.name : b.short;
}
export function bankColor(code) { return code === 'PEER_AVG' ? '#8A8F98' : (state.banks[code]?.color || '#888'); }
export function bankDot(code) {
  return `<span class="bdot" style="background:${bankColor(code)}"></span>`;
}

// ----------------------------------------------------------------------------- markdown (small, safe)
export function md(src, opts = {}) {
  const facts = opts.facts || {};
  const inline = s => {
    let t = esc(s);
    t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, '$1<em>$2</em>');
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\[(F\d+(?:,\s*F\d+)*)\]/g, (m, ids) => ids.split(/,\s*/).map(id => {
      const f = facts[id];
      return `<sup class="cite" tabindex="0" data-fact="${esc(id)}" title="${esc(f ? f.text : id)}">${esc(id)}</sup>`;
    }).join(''));
    return t;
  };
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  let html = '', list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(' '))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flushPara(); closeList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); closeList();
      const lvl = Math.min(4, m[1].length + (opts.shiftHeadings || 0));
      html += `<h${lvl}>${inline(m[2])}</h${lvl}>`; continue;
    }
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara(); if (list !== 'ul') { closeList(); html += '<ul>'; list = 'ul'; }
      html += `<li>${inline(m[1])}</li>`; continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara(); if (list !== 'ol') { closeList(); html += '<ol>'; list = 'ol'; }
      html += `<li>${inline(m[1])}</li>`; continue;
    }
    closeList();
    para.push(line.trim());
  }
  flushPara(); closeList();
  return html;
}

// ----------------------------------------------------------------------------- tooltip
let tipEl;
export function tip(html, x, y) {
  tipEl = tipEl || document.getElementById('tooltip');
  if (!html) { tipEl.hidden = true; return; }
  tipEl.innerHTML = html;
  tipEl.hidden = false;
  const r = tipEl.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  tipEl.style.left = Math.max(8, left) + 'px';
  tipEl.style.top = Math.max(8, top) + 'px';
}
export function hideTip() { tip(null); }

// ----------------------------------------------------------------------------- toasts
export function toast(msg, kind = 'info', ms = 4200) {
  const box = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = 'toast toast-' + kind;
  t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
}

// ----------------------------------------------------------------------------- drawer
export function openDrawer(title, html, opts = {}) {
  const d = document.getElementById('drawer');
  d.innerHTML = `<div class="drawer-scrim" data-close></div>
    <aside class="drawer-panel ${opts.wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <header class="drawer-head"><h2>${esc(title)}</h2>
        <button class="icon-btn" data-close aria-label="Close">${icon('close')}</button></header>
      <div class="drawer-body">${html}</div></aside>`;
  d.hidden = false;
  d.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', closeDrawer));
  setTimeout(() => d.querySelector('.drawer-panel')?.focus(), 10);
  return d.querySelector('.drawer-body');
}
export function closeDrawer() { const d = document.getElementById('drawer'); d.hidden = true; d.innerHTML = ''; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

// ----------------------------------------------------------------------------- live event stream (SSE)
const listeners = new Set();
let es = null;
export const live = {
  connected: false,
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  start() {
    if (es) return;
    const url = state.embed && state.token ? withAuth('/api/obs/stream') : '/api/obs/stream';
    es = new EventSource(url);
    es.onopen = () => { live.connected = true; listeners.forEach(fn => fn({ type: 'stream', connected: true })); };
    es.onerror = () => { live.connected = false; listeners.forEach(fn => fn({ type: 'stream', connected: false })); };
    es.onmessage = m => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      listeners.forEach(fn => { try { fn(ev); } catch (e) { console.error(e); } });
    };
  },
  stop() { if (es) { es.close(); es = null; } },
};

// ----------------------------------------------------------------------------- icons (inline, no CDN)
const ICONS = {
  overview: 'M3 12h4v8H3zM10 4h4v16h-4zM17 9h4v11h-4z',
  trends: 'M3 17l5-5 4 4 8-9M15 7h5v5',
  compare: 'M4 6h10M4 12h16M4 18h7',
  reports: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7',
  data: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6',
  agents: 'M5 6a2 2 0 1 0 0 .1M19 6a2 2 0 1 0 0 .1M12 18a2 2 0 1 0 0 .1M7 6h10M6 8l5 8M18 8l-5 8',
  governance: 'M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6zM9 12l2 2 4-4',
  close: 'M6 6l12 12M18 6L6 18',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  play: 'M7 5l12 7-12 7z',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  doc: 'M6 3h9l4 4v14H6zM14 3v5h5',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  send: 'M4 12l16-8-6 16-3-6z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c1-4 4.5-6 8-6s7 2 8 6',
  upload: 'M12 20V9M7 14l5-5 5 5M5 4h14',
  shield: 'M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z',
  pause: 'M8 5v14M16 5v14',
  arrowUp: 'M12 19V5M6 11l6-6 6 6',
  arrowLeft: 'M19 12H5M11 6l-6 6 6 6',
  arrowDown: 'M12 5v14M6 13l6 6 6-6',
  info: 'M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  warn: 'M12 4l9 16H3zM12 10v4M12 17h.01',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  expand: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  minimize: 'M6 12h12',
};
export function icon(name, size = 18) {
  const d = ICONS[name] || ICONS.info;
  return `<svg class="ico" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
}

// status chips -----------------------------------------------------------------
const FLAG_TEXT = {
  conflict: 'Sources disagree', basis_mismatch: 'Basis mismatch', low_confidence: 'Low confidence',
  single_source: 'Single source', llm_extracted: 'Read by LLM', basis_note: 'Basis note', jump: 'Unusual jump',
  inconsistent: 'Inconsistent', range: 'Out of range', changed_after_approval: 'Changed after approval',
  derived: 'Derived', manual_override: 'Manual override',
};
const BLOCKING = new Set(['conflict', 'basis_mismatch', 'low_confidence', 'jump', 'inconsistent', 'range', 'changed_after_approval', 'llm_extracted']);
export function flagChip(f) {
  return `<span class="chip ${BLOCKING.has(f) ? 'chip-warn' : 'chip-quiet'}" title="${esc(f)}">${esc(FLAG_TEXT[f] || f)}</span>`;
}
export function statusChip(s) {
  const map = { approved: 'good', overridden: 'info', staged: 'warn', rejected: 'bad', running: 'info', succeeded: 'good',
    failed: 'bad', error: 'bad', ok: 'good', draft: 'warn', extracted: 'good', downloaded: 'info', discovered: 'quiet',
    failed_download: 'bad', cancelled: 'quiet', awaited: 'quiet' };
  return `<span class="chip chip-${map[s] || 'quiet'}">${esc(s)}</span>`;
}
export const METHOD_TEXT = { table: 'Table', chart: 'Chart labels', keyvalue: 'Key-value tile', sentence: 'Sentence',
  llm: 'Local LLM', derived: 'Derived', manual: 'Manual' };

export function prettyOut(text) {
  // Agent outputs arrive as JSON; show them as plain "key value" phrases.
  let o; try { o = JSON.parse(text); } catch { return String(text || ''); }
  if (o == null || typeof o !== 'object') return String(o);
  const fmt = v => (v && typeof v === 'object') ? (Array.isArray(v) ? v.join(', ') : Object.entries(v).map(([k, x]) => `${k.replace(/_/g, ' ')} ${x}`).join(', ')) : String(v);
  return Object.entries(o).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${fmt(v)}`).join('; ');
}
export function emptyState(title, body, actionHtml = '') {
  return `<div class="empty"><h3>${esc(title)}</h3><p>${body}</p>${actionHtml}</div>`;
}
export function loading(msg = 'Loading…') { return `<div class="loading"><span class="spinner"></span>${esc(msg)}</div>`; }
