// Myra - the benchmarking assistant: avatar + chat panel.
import { api, esc, md, icon, state, can, toast, openDrawer, fmtMs } from './util.js';
import { chatChart } from './charts.js';
import { showLineage, showPage } from './lineage.js';

let uid = 0;
// A modern, stylised portrait: sleek side-swept hair, ivory blazer over a maroon top, fine gold accents.
export function avatarSvg(size = 48, extraClass = '') {
  const p = 'mv' + (++uid);
  return `<svg class="avatar ${extraClass}" width="${size}" height="${size}" viewBox="0 0 120 120" role="img" aria-label="Myra, benchmarking assistant">
  <defs>
    <linearGradient id="${p}bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C4304A"/><stop offset="1" stop-color="#5A0A1B"/></linearGradient>
    <linearGradient id="${p}hair" x1="0" y1="0" x2="0.3" y2="1"><stop offset="0" stop-color="#4A2A20"/><stop offset=".55" stop-color="#2A1712"/><stop offset="1" stop-color="#1A0E0B"/></linearGradient>
    <linearGradient id="${p}shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#8A5A43" stop-opacity="0"/><stop offset=".5" stop-color="#9C6A50" stop-opacity=".75"/><stop offset="1" stop-color="#8A5A43" stop-opacity="0"/></linearGradient>
    <radialGradient id="${p}skin" cx=".42" cy=".38" r=".75"><stop offset="0" stop-color="#EDBE9C"/><stop offset=".7" stop-color="#D9A07C"/><stop offset="1" stop-color="#C58964"/></radialGradient>
    <linearGradient id="${p}blz" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FBF6EE"/><stop offset="1" stop-color="#E9DFD0"/></linearGradient>
    <clipPath id="${p}c"><circle cx="60" cy="60" r="60"/></clipPath>
  </defs>
  <g clip-path="url(#${p}c)">
    <rect width="120" height="120" fill="url(#${p}bg)"/>
    <circle cx="96" cy="18" r="46" fill="#fff" opacity=".07"/>
    <path d="M8 120 Q40 96 120 104 L120 120Z" fill="#000" opacity=".12"/>
    <!-- hair, back layer -->
    <path d="M33 50 C31 27 45 15 62 15 C81 15 93 29 91 52 C90 66 93 80 99 94 C101 104 99 112 96 120 L28 120 C23 110 22 100 25 91 C30 79 34 66 33 50Z" fill="url(#${p}hair)"/>
    <!-- neck -->
    <path d="M52 72 L52 89 C55 93 65 93 68 89 L68 72Z" fill="#BF8360"/>
    <path d="M52 80 C56 84 64 84 68 80 L68 74 L52 74Z" fill="#A86E4E" opacity=".55"/>
    <!-- top + blazer -->
    <path d="M16 120 C18 103 32 93 49 89 L60 99 L71 89 C88 93 102 103 104 120Z" fill="#7C1126"/>
    <path d="M49 89 L60 101 L71 89 C67 91.5 53 91.5 49 89Z" fill="#C88A66"/>
    <path d="M14 120 C16 104 29 94 46 89.5 L51 96 L44 104 L52 110 L56 120Z" fill="url(#${p}blz)"/>
    <path d="M106 120 C104 104 91 94 74 89.5 L69 96 L76 104 L68 110 L64 120Z" fill="url(#${p}blz)"/>
    <path d="M46 89.5 L51 96 L44 104 L52 110" fill="none" stroke="#D6C8B4" stroke-width="1"/>
    <path d="M74 89.5 L69 96 L76 104 L68 110" fill="none" stroke="#D6C8B4" stroke-width="1"/>
    <circle cx="80" cy="106" r="2.2" fill="#F2B600"/>
    <!-- fine gold chain -->
    <path d="M51.5 90.5 Q60 100.5 68.5 90.5" fill="none" stroke="#E8B53A" stroke-width=".9"/>
    <circle cx="60" cy="97.6" r="1.6" fill="#F2C53D"/>
    <!-- ears + hoops -->
    <ellipse cx="41.6" cy="55" rx="3.2" ry="5" fill="#CC9170"/>
    <ellipse cx="78.4" cy="55" rx="3.2" ry="5" fill="#CC9170"/>
    <circle cx="41.4" cy="63" r="3.1" fill="none" stroke="#F2B600" stroke-width="1.3"/>
    <circle cx="78.6" cy="63" r="3.1" fill="none" stroke="#F2B600" stroke-width="1.3"/>
    <!-- face -->
    <path d="M42 50 C42 35 50 28.5 60 28.5 C70 28.5 78 35 78 50 C78 63 71.5 76 60 77.5 C48.5 76 42 63 42 50Z" fill="url(#${p}skin)"/>
    <ellipse cx="49.5" cy="61.5" rx="4" ry="2.2" fill="#E07C7C" opacity=".22"/>
    <ellipse cx="70.5" cy="61.5" rx="4" ry="2.2" fill="#E07C7C" opacity=".22"/>
    <!-- brows: soft, high arch -->
    <path d="M47.4 44.2 Q52 41.2 56.6 43" fill="none" stroke="#4A2C20" stroke-width="1.25" stroke-linecap="round"/>
    <path d="M63.4 43 Q68 41.2 72.6 44.2" fill="none" stroke="#4A2C20" stroke-width="1.25" stroke-linecap="round"/>
    <!-- lids -->
    <path d="M48.4 49 Q52.5 46.2 56.6 48.6" fill="none" stroke="#B57E5E" stroke-width=".6" opacity=".8"/>
    <path d="M63.4 48.6 Q67.5 46.2 71.6 49" fill="none" stroke="#B57E5E" stroke-width=".6" opacity=".8"/>
    <!-- eyes: open and bright -->
    <path d="M48 51.4 Q52.5 47.4 57 51.2 Q52.5 54.6 48 51.4Z" fill="#fff"/>
    <path d="M63 51.2 Q67.5 47.4 72 51.4 Q67.5 54.6 63 51.2Z" fill="#fff"/>
    <circle cx="52.6" cy="51.2" r="2.35" fill="#6B4129"/><circle cx="67.4" cy="51.2" r="2.35" fill="#6B4129"/>
    <circle cx="52.6" cy="51.2" r="1.1" fill="#1E120C"/><circle cx="67.4" cy="51.2" r="1.1" fill="#1E120C"/>
    <circle cx="53.4" cy="50.4" r=".65" fill="#fff"/><circle cx="68.2" cy="50.4" r=".65" fill="#fff"/>
    <path d="M48 51.4 Q52.5 47.4 57 51.2 L58 50.5" fill="none" stroke="#2A1712" stroke-width=".85" stroke-linecap="round"/>
    <path d="M72 51.4 Q67.5 47.4 63 51.2 L62 50.5" fill="none" stroke="#2A1712" stroke-width=".85" stroke-linecap="round"/>
    <!-- nose -->
    <path d="M60.4 55.4 Q59.3 59.4 61 60.6" fill="none" stroke="#A9694A" stroke-width=".9" stroke-linecap="round"/>
    <!-- smile -->
    <path d="M53.6 66.2 Q57 64.4 60 65.4 Q63 64.4 66.4 66.2 Q60 67.8 53.6 66.2Z" fill="#A83A52"/>
    <path d="M53.6 66.2 Q60 72.2 66.4 66.2 Q60 68.1 53.6 66.2Z" fill="#C4506A"/>
    <path d="M53.2 65.6 Q53.4 66.5 54.2 66.8" fill="none" stroke="#A9694A" stroke-width=".6" stroke-linecap="round"/>
    <path d="M66.8 65.6 Q66.6 66.5 65.8 66.8" fill="none" stroke="#A9694A" stroke-width=".6" stroke-linecap="round"/>
    <path d="M57.6 69.2 Q60 70.1 62.4 69.2" fill="none" stroke="#fff" stroke-width=".6" opacity=".45" stroke-linecap="round"/>
    <!-- hair, front: deep side part, sweep across to the right, left side tucked behind the ear -->
    <path d="M50 23.5 C61 21 76.5 24.5 81.4 38 C83.4 44 83 50 81.2 57 C79.6 48.6 76 42.4 70 39.2 C63.6 36.2 56.4 35.8 49.6 37.6 C47.4 34.4 47.6 27.6 50 23.5Z" fill="url(#${p}hair)"/>
    <path d="M50 23.5 C44.2 24.6 40.4 31.4 40.6 41.6 C40.7 46.6 41.2 50 42.2 53.4 C43 46.4 44.8 40.6 48.6 37.2 C49.2 32.4 49.4 27.6 50 23.5Z" fill="url(#${p}hair)"/>
    <path d="M54.5 26.6 C63 24.2 72 25.8 77.4 31.6" fill="none" stroke="url(#${p}shine)" stroke-width="2.2" stroke-linecap="round"/>
    <path d="M80.6 50 C83.6 64 81.6 80 87.6 97 C80.6 92.6 76.8 84 76.2 72 C76 64 78 56 80.6 50Z" fill="url(#${p}hair)"/>
    <path d="M81.2 60 C82.4 72 81.8 82 85 91" fill="none" stroke="#7A5040" stroke-width="1.3" opacity=".55" stroke-linecap="round"/>
    <path d="M36.5 60 C35.6 74 33.6 88 30.5 101" fill="none" stroke="#7A5040" stroke-width="1.5" opacity=".45" stroke-linecap="round"/>
    <path d="M88 58 C89.5 72 92 86 95.5 98" fill="none" stroke="#7A5040" stroke-width="1.3" opacity=".35" stroke-linecap="round"/>
  </g>
  <circle cx="60" cy="60" r="58.6" fill="none" stroke="#F2B600" stroke-width="2.2" opacity=".95"/>
</svg>`;
}

// ----------------------------------------------------------------------------- chat panel
const chat = { sessionId: null, open: false, busy: false, history: [] };

export function mountMyra(root) {
  root.innerHTML = `
    <button class="myra-launch" type="button" aria-expanded="false" aria-controls="myra-panel">
      ${avatarSvg(46)}<span class="myra-launch-txt"><b>Ask Myra</b><small>Benchmarking assistant</small></span>
    </button>
    <section id="myra-panel" class="myra-panel" hidden aria-label="Chat with Myra">
      <header class="myra-head">
        ${avatarSvg(44, 'online')}
        <div class="myra-id"><b>Myra</b><small class="myra-sub">Answers only from approved bank data, with sources</small></div>
        <span class="myra-mode" title="How answers are composed"></span>
        <button class="icon-btn myra-close" type="button" aria-label="Close chat">${icon('minimize')}</button>
      </header>
      <div class="myra-log" role="log" aria-live="polite"></div>
      <div class="myra-suggest"></div>
      <form class="myra-form" autocomplete="off">
        <input class="myra-input" name="q" maxlength="1000" placeholder="Ask about any KPI, bank or quarter" aria-label="Your question">
        <button class="btn btn-primary myra-send" type="submit" aria-label="Send">${icon('send', 16)}</button>
      </form>
    </section>`;
  const launch = root.querySelector('.myra-launch'), panel = root.querySelector('.myra-panel');
  const toggle = open => {
    chat.open = open ?? !chat.open;
    panel.hidden = !chat.open;
    launch.hidden = chat.open;
    launch.setAttribute('aria-expanded', String(chat.open));
    if (chat.open) {
      if (!chat.history.length) greet(root);
      setTimeout(() => root.querySelector('.myra-input').focus(), 30);
    }
  };
  launch.addEventListener('click', () => toggle(true));
  root.querySelector('.myra-close').addEventListener('click', () => toggle(false));
  root.querySelector('.myra-form').addEventListener('submit', e => {
    e.preventDefault();
    const inp = root.querySelector('.myra-input');
    const q = inp.value.trim();
    if (q) { inp.value = ''; ask(root, q); }
  });
  root.addEventListener('click', e => {
    const s = e.target.closest('[data-suggest]');
    if (s) ask(root, s.dataset.suggest);
    const src = e.target.closest('[data-src]');
    if (src) {
      const d = JSON.parse(src.dataset.src);
      if (d.lineage) showLineage(d.bank, d.kpi, d.qk); else if (d.doc_id) showPage(d.doc_id, d.page);
    }
  });
  updateMode(root);
  return { open: () => toggle(true), ask: q => { toggle(true); ask(root, q); } };
}

export function updateMode(root = document.getElementById('myra')) {
  const el = root?.querySelector('.myra-mode');
  if (!el) return;
  const llm = state.llm || {};
  el.textContent = llm.available ? 'Local LLM' : 'Rule mode';
  el.className = 'myra-mode ' + (llm.available ? 'on' : 'off');
  el.title = llm.available ? `Answers composed by ${llm.model} running on this server, then number-checked`
    : 'No language model connected: answers are composed from templates over the same approved data';
}

function greet(root) {
  addMsg(root, 'bot', `Hello, I'm **Myra**. Ask me how PNB compares with SBI, Bank of Baroda, Canara Bank and Union Bank on any KPI. Every number I give comes from an approved value, and I can show you the page it was read from.`);
  suggestions(root, (state.meta && state.meta.suggestions) || []);
}

function suggestions(root, list) {
  root.querySelector('.myra-suggest').innerHTML = list.slice(0, 4).map(s => `<button type="button" class="sugg" data-suggest="${esc(s)}">${esc(s)}</button>`).join('');
}

function addMsg(root, who, text, extra = {}) {
  const log = root.querySelector('.myra-log');
  const div = document.createElement('div');
  div.className = 'msg msg-' + who;
  if (who === 'bot') {
    div.innerHTML = `<div class="msg-body">${md(text)}</div>`;
    if (extra.chart) {
      const c = document.createElement('div');
      c.className = 'msg-chart';
      div.appendChild(c);
      requestAnimationFrame(() => chatChart(c, extra.chart, state.kpis[extra.chart.kpi]?.unit || '%'));
    }
    if (extra.sources && extra.sources.length) {
      const s = document.createElement('div');
      s.className = 'msg-src';
      s.innerHTML = '<span class="muted">Sources</span>' + extra.sources.map(src => {
        const data = src.doc_id ? { doc_id: src.doc_id, page: src.page } : null;
        return `<button type="button" class="src-chip" ${data ? `data-src='${esc(JSON.stringify(data))}'` : ''} title="${esc(src.label || '')}">${icon('doc', 13)} ${esc(shortTitle(src.label))}${src.page ? ', p.' + esc(src.page) : ''}</button>`;
      }).join('');
      div.appendChild(s);
    }
    if (extra.lineage) {
      const b = document.createElement('div');
      b.className = 'msg-src';
      b.innerHTML = `<button type="button" class="src-chip" data-src='${esc(JSON.stringify({ lineage: 1, ...extra.lineage }))}'>${icon('link', 13)} Show full lineage</button>`;
      div.appendChild(b);
    }
    if (extra.meta) {
      const m = document.createElement('div');
      m.className = 'msg-meta';
      m.textContent = extra.meta;
      div.appendChild(m);
    }
  } else {
    div.innerHTML = `<div class="msg-body">${esc(text)}</div>`;
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  chat.history.push({ who, text });
  return div;
}

function shortTitle(t) {
  t = String(t || 'Document');
  const m = t.match(/^(.*?) - (.*)$/);
  const s = m ? m[2] : t;
  return s.length > 34 ? s.slice(0, 33) + '…' : s;
}

async function ask(root, q) {
  if (chat.busy) return;
  chat.busy = true;
  addMsg(root, 'user', q);
  const typing = document.createElement('div');
  typing.className = 'msg msg-bot typing';
  typing.innerHTML = '<div class="msg-body"><span></span><span></span><span></span></div>';
  root.querySelector('.myra-log').appendChild(typing);
  root.querySelector('.myra-log').scrollTop = 1e9;
  try {
    const a = await api('/api/chat', { json: { message: q, session_id: chat.sessionId, provisional: state.provisional } });
    chat.sessionId = a.session_id;
    typing.remove();
    const ent = a.entities || {};
    const lineage = a.intent === 'source' && ent.kpis && ent.kpis[0]
      ? { bank: (ent.banks && ent.banks[0]) || 'PNB', kpi: ent.kpis[0], qk: ent.quarter_key || state.quarterKey } : null;
    const how = a.mode === 'llm' ? 'composed by the local LLM' : a.mode === 'rule' ? 'answered by a safety rule' : 'composed from templates';
    const meta = `Understood as “${a.intent}”, ${how}, ${fmtMs(a.latency_ms)}${a.grounded === false ? ', some numbers could not be verified' : ''}`;
    addMsg(root, 'bot', a.answer, { chart: a.chart, sources: a.sources, meta, lineage: lineage && lineage.qk ? lineage : null });
    if (a.suggestions) suggestions(root, a.suggestions.filter(s => s !== q));
  } catch (e) {
    typing.remove();
    addMsg(root, 'bot', 'I could not answer that just now: ' + e.message);
  } finally {
    chat.busy = false;
  }
}
export { openDrawer, toast, can };
