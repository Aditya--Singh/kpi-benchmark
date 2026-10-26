// Agents & logs: live agent map, metrics, streaming log console, run timelines and LLM calls.
import { api, esc, state, icon, live, fmtTime, fmtMs, statusChip, openDrawer, loading } from '../util.js';
import { gantt } from '../charts.js';

const LANES = [
  { title: 'Data pipeline', note: 'Runs when an analyst starts a collection', nodes: ['scout', 'fetcher', 'extractor', 'indexer', 'reconciler', 'human'] },
  { title: 'Insight pipeline', note: 'Runs when someone requests a report', nodes: ['analyst', 'writer', 'guardrail', 'publisher'] },
  { title: 'Assistant', note: 'Runs for every question asked in chat', nodes: ['myra'] },
];
const HUMAN = { name: 'human', title: 'Human approver', goal: 'Reviews flagged values, approves, rejects or corrects them. Nothing reaches dashboards, reports or Myra before this.', tools: ['Review queue', 'Lineage viewer'], inputs: ['staged values + findings'], outputs: ['approved values', 'audit entries'] };

export async function renderAgents(root) {
  const roster = Object.fromEntries([...state.meta.roster, HUMAN].map(a => [a.name, a]));
  const titles = Object.fromEntries(Object.values(roster).map(a => [a.name, a.title]));
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Agents & logs</h1>
      <p class="lede">Each box is an agent with one job. When a pipeline runs, its boxes light up step by step, and every step, log line and model call is recorded here and in the database.</p></header>
    <section class="card"><div class="card-head"><h2>Agent map</h2><span class="muted small" id="ag-live">Waiting for activity</span></div>
      <div class="lanes">${LANES.map(l => `<div class="lane"><div class="lane-h"><b>${esc(l.title)}</b><small>${esc(l.note)}</small></div>
        <div class="flow">${l.nodes.map((n, i) => `${i ? '<span class="arrow" aria-hidden="true"></span>' : ''}<button type="button" class="node ${n === 'human' ? 'human' : ''}" data-node="${n}">
          <b>${esc(roster[n]?.title || n)}</b><small class="goal">${esc(roster[n]?.goal || '')}</small><span class="nstat">idle</span></button>`).join('')}
          ${l.nodes[0] === 'myra' ? '<div class="myra-steps"><span>Safety screen</span><span>Understand question</span><span>Query approved data</span><span>Search documents</span><span>Compose answer</span><span>Check numbers</span></div>' : ''}</div></div>`).join('')}</div>
    </section>
    <section class="card"><div class="card-head"><h2>Health</h2><button class="icon-btn" id="m-ref" aria-label="Refresh">${icon('refresh', 16)}</button></div><div id="metrics">${loading()}</div></section>
    <section class="card console-card"><div class="card-head"><h2>Live log</h2>
        <div class="filters tight">
          <label>Level<select id="lg-level"><option value="">All</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label>
          <label>Agent<select id="lg-agent"><option value="">All</option>${Object.values(roster).filter(a => a.name !== 'human').map(a => `<option value="${a.name}">${esc(a.title)}</option>`).join('')}</select></label>
          <label>Search<input id="lg-q" placeholder="text"></label>
          <button class="btn btn-ghost btn-sm" id="lg-pause" type="button" aria-pressed="false">${icon('pause', 14)} Pause</button>
          <button class="btn btn-ghost btn-sm" id="lg-clear" type="button">Clear</button></div></div>
      <div class="console" id="console" aria-live="off"></div>
      <p class="muted small">Also written to data/logs/app.jsonl (rotating, structured JSON) for the bank's log tools.</p></section>
    <div class="grid-runs">
      <section class="card"><div class="card-head"><h2>Runs</h2>
        <select id="rn-pipe" aria-label="Pipeline"><option value="">All</option><option value="collect">Data collection</option><option value="report">Reports</option><option value="chat">Chat</option></select></div>
        <div id="runs">${loading()}</div></section>
      <section class="card"><div id="run-detail"><p class="muted pad">Select a run to see its timeline: which agent did what, for how long, and what it produced.</p></div></section>
    </div>
  </div>`;

  // ---- agent map
  const nodes = Object.fromEntries([...root.querySelectorAll('[data-node]')].map(n => [n.dataset.node, n]));
  const setNode = (agent, cls, text) => {
    const n = nodes[agent]; if (!n) return;
    n.classList.remove('running', 'ok', 'error');
    if (cls) n.classList.add(cls);
    n.querySelector('.nstat').textContent = text;
  };
  root.querySelectorAll('[data-node]').forEach(n => n.addEventListener('click', () => {
    const a = roster[n.dataset.node];
    openDrawer(a.title, `<p>${esc(a.goal)}</p>
      <h3>Tools it uses</h3><ul class="plain">${(a.tools || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      <h3>Reads</h3><ul class="plain">${(a.inputs || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      <h3>Produces</h3><ul class="plain">${(a.outputs || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>`);
  }));
  // last known state of each agent
  try {
    const runs = await api('/api/obs/runs?limit=60');
    for (const pipe of ['collect', 'report', 'chat']) {
      const r = runs.find(x => x.pipeline === pipe);
      if (!r) continue;
      const d = await api('/api/obs/runs/' + r.id);
      for (const sp of d.spans.filter(s => !s.parent_id)) {
        if (sp.status === 'running' && r.status === 'running') setNode(sp.agent, 'running', `working since ${fmtTime(sp.started_at, true)}`);
        else setNode(sp.agent, sp.status === 'ok' ? 'ok' : 'error', `last run ${fmtTime(sp.started_at, true)}, ${fmtMs(sp.duration_ms)}`);
      }
      if (pipe === 'chat') setNode('myra', 'ok', `last question ${fmtTime(r.started_at, true)}, ${fmtMs(r.duration_ms)}`);
    }
    const hs = await api('/api/review/values?status=staged&limit=1');
    setNode('human', (hs.counts.staged || 0) ? 'running' : 'ok', (hs.counts.staged || 0) ? `${hs.counts.staged} value(s) waiting` : 'queue empty');
  } catch { /* ignore */ }

  // ---- metrics
  const drawMetrics = async () => {
    const m = await api('/api/obs/metrics');
    const v = m.values || {}, d = m.documents || {}, f = m.findings || {}, r = m.runs || {};
    const cell = (label, value, note = '') => `<div class="metric"><span class="m-l">${esc(label)}</span><span class="m-v">${value}</span>${note ? `<span class="m-n">${note}</span>` : ''}</div>`;
    root.querySelector('#metrics').innerHTML = `<div class="metrics">
      ${cell('Pipeline runs', esc(Object.values(r).reduce((a, b) => a + b, 0)), `${r.succeeded || 0} succeeded, ${r.failed || 0} failed`)}
      ${cell('Documents', esc(Object.values(d).reduce((a, b) => a + b, 0)), Object.entries(d).map(([k, n]) => `${n} ${esc(k)}`).join(', '))}
      ${cell('KPI values', esc(Object.values(v).reduce((a, b) => a + b, 0)), `${v.approved || 0} approved, ${v.staged || 0} waiting`)}
      ${cell('Open findings', esc(Object.values(f).reduce((a, b) => a + b, 0)), Object.entries(f).map(([k, n]) => `${n} ${esc(k)}`).join(', '))}
      ${cell('LLM calls', esc(m.llm.calls), m.llm.calls ? `p50 ${fmtMs(m.llm.p50_ms)}, p95 ${fmtMs(m.llm.p95_ms)}` : (state.llm?.available ? 'none yet' : 'template mode'))}
      ${cell('Chat questions', esc(m.chat.questions), m.chat.questions ? `average ${fmtMs(m.chat.avg_latency_ms)}` : '')}
      ${cell('Report numbers verified', m.reports_avg_grounding != null ? Math.round(m.reports_avg_grounding * 100) + '%' : '–', 'average across reports')}
      ${cell('Log warnings / errors', `${esc(m.log_warnings)} / ${esc(m.log_errors)}`, `${m.sse_clients} live viewer(s)`)}
    </div>
    <table class="tbl compact"><thead><tr><th>Agent</th><th class="num">Steps recorded</th><th class="num">Average time</th><th class="num">Errors</th></tr></thead>
      <tbody>${m.agents.map(a => `<tr><td>${esc(titles[a.agent] || a.agent)}</td><td class="num">${a.spans}</td><td class="num">${esc(fmtMs(a.avg_ms))}</td><td class="num ${a.errors ? 'error' : ''}">${a.errors}</td></tr>`).join('')}</tbody></table>`;
  };
  root.querySelector('#m-ref').addEventListener('click', drawMetrics);
  drawMetrics().catch(() => {});

  // ---- console
  const con = root.querySelector('#console');
  const flt = { level: '', agent: '', q: '', paused: false };
  const buffer = [];
  const lineHtml = e => `<span class="ts">${esc(fmtTime(e.ts))}</span><span class="lv">${esc((e.level || 'INFO').slice(0, 4))}</span><span class="ag">${esc(titles[e.agent] || e.agent || e.logger || '')}</span><span class="msg">${esc(e.message)}</span>`;
  const passes = e => (!flt.level || e.level === flt.level) && (!flt.agent || e.agent === flt.agent) && (!flt.q || (e.message || '').toLowerCase().includes(flt.q));
  const append = e => {
    if (!passes(e)) return;
    const div = document.createElement('div');
    div.className = 'ln lv-' + (e.level || 'INFO').toLowerCase();
    div.innerHTML = lineHtml(e);
    con.appendChild(div);
    while (con.children.length > 600) con.firstChild.remove();
    if (!flt.paused) con.scrollTop = con.scrollHeight;
  };
  const redraw = () => { con.innerHTML = ''; buffer.forEach(append); };
  try { (await api('/api/obs/logs?limit=250')).forEach(e => buffer.push(e)); redraw(); } catch { /* ignore */ }
  root.querySelector('#lg-level').addEventListener('change', e => { flt.level = e.target.value; redraw(); });
  root.querySelector('#lg-agent').addEventListener('change', e => { flt.agent = e.target.value; redraw(); });
  root.querySelector('#lg-q').addEventListener('input', e => { flt.q = e.target.value.toLowerCase(); redraw(); });
  root.querySelector('#lg-pause').addEventListener('click', e => { flt.paused = !flt.paused; e.currentTarget.setAttribute('aria-pressed', String(flt.paused)); e.currentTarget.innerHTML = icon(flt.paused ? 'play' : 'pause', 14) + (flt.paused ? ' Resume' : ' Pause'); });
  root.querySelector('#lg-clear').addEventListener('click', () => { buffer.length = 0; con.innerHTML = ''; });

  // ---- runs
  let selected = null;
  const drawRuns = async () => {
    const pipe = root.querySelector('#rn-pipe').value;
    const runs = await api('/api/obs/runs?limit=60' + (pipe ? '&pipeline=' + pipe : ''));
    root.querySelector('#runs').innerHTML = runs.length ? `<table class="tbl click compact"><thead><tr><th>When</th><th>Pipeline</th><th>By</th><th>Status</th><th class="num">Took</th></tr></thead>
      <tbody>${runs.map(r => `<tr data-run="${esc(r.id)}" tabindex="0" class="${r.id === selected ? 'sel' : ''}"><td class="small">${esc(fmtTime(r.started_at, true))}</td>
        <td>${esc(r.pipeline === 'collect' ? 'Data collection' : r.pipeline === 'report' ? 'Report: ' + (r.params?.rtype || '') : 'Chat')}${r.pipeline === 'chat' && r.params?.question ? `<br><span class="muted small">${esc(r.params.question.slice(0, 48))}</span>` : ''}</td>
        <td>${esc(r.triggered_by || '')}</td><td>${statusChip(r.status)}</td><td class="num">${esc(fmtMs(r.duration_ms))}</td></tr>`).join('')}</tbody></table>` : '<p class="muted pad">No runs yet.</p>';
    root.querySelectorAll('[data-run]').forEach(tr => {
      tr.addEventListener('click', () => openRun(tr.dataset.run));
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') openRun(tr.dataset.run); });
    });
  };
  const openRun = async id => {
    selected = id;
    root.querySelectorAll('[data-run]').forEach(tr => tr.classList.toggle('sel', tr.dataset.run === id));
    const box = root.querySelector('#run-detail');
    box.innerHTML = loading();
    const d = await api('/api/obs/runs/' + id);
    const r = d.run;
    box.innerHTML = `<div class="card-head"><h2>${esc(r.pipeline === 'collect' ? 'Data collection' : r.pipeline === 'report' ? 'Report generation' : 'Chat answer')}</h2>${statusChip(r.status)}</div>
      <p class="muted small">Run <code>${esc(r.id)}</code>, started ${esc(fmtTime(r.started_at, true))} by ${esc(r.triggered_by || 'system')}, took ${esc(fmtMs(r.duration_ms))}.</p>
      ${r.error ? `<pre class="errbox">${esc(r.error)}</pre>` : ''}
      ${r.summary ? `<p class="small"><b>Result:</b> ${esc(Object.entries(r.summary).map(([k, v]) => `${k.replace(/_/g, ' ')} ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', '))}</p>` : ''}
      <h3>Timeline</h3><div class="chartbox" id="gantt"></div>
      <h3>Steps</h3><div class="spans">${d.spans.map(sp => `<details class="span ${sp.status === 'error' ? 'err' : ''}"><summary><b>${esc(titles[sp.agent] || sp.agent)}</b> ${esc(sp.step)} <span class="muted">${esc(fmtMs(sp.duration_ms))}</span></summary>
        ${sp.input ? `<div class="kv"><span>Input</span><code>${esc(sp.input)}</code></div>` : ''}${sp.output ? `<div class="kv"><span>Output</span><code>${esc(sp.output)}</code></div>` : ''}
        ${sp.meta ? `<div class="kv"><span>Details</span><code>${esc(JSON.stringify(sp.meta))}</code></div>` : ''}${sp.error ? `<pre class="errbox">${esc(sp.error)}</pre>` : ''}</details>`).join('')}</div>
      <h3>Language-model calls</h3>${d.llm_calls.length ? `<table class="tbl compact"><thead><tr><th>Purpose</th><th>Model</th><th class="num">Time</th><th class="num">Tokens in / out</th><th>Prompt version</th><th>OK</th></tr></thead>
        <tbody>${d.llm_calls.map(c => `<tr><td>${esc(c.purpose)}</td><td>${esc(c.model)}</td><td class="num">${esc(fmtMs(c.latency_ms))}</td><td class="num">${c.prompt_tokens ?? '–'} / ${c.completion_tokens ?? '–'}</td><td><code>${esc(c.prompt_version || '')}</code></td><td>${c.ok ? statusChip('ok') : statusChip('error')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted small">None in this run (rules and templates only).</p>'}`;
    gantt(box.querySelector('#gantt'), d.spans, titles);
  };
  root.querySelector('#rn-pipe').addEventListener('change', drawRuns);
  await drawRuns();

  // ---- live updates
  const liveTxt = root.querySelector('#ag-live');
  const refreshSoon = debounceAsync(async () => { await drawRuns(); drawMetrics().catch(() => {}); if (selected) openRun(selected); }, 900);
  const off = live.on(ev => {
    if (ev.type === 'log') { buffer.push(ev); if (buffer.length > 1200) buffer.splice(0, 400); append(ev); }
    if (ev.type === 'span') {
      if (ev.event === 'start') { setNode(ev.agent, 'running', ev.step.replace(/^.*?: /, '')); liveTxt.textContent = `${titles[ev.agent] || ev.agent}: ${ev.step}`; }
      else if (ev.step.endsWith(': start') || ev.agent === 'myra') setNode(ev.agent, ev.status === 'ok' ? 'ok' : 'error', `${ev.status === 'ok' ? 'done' : 'failed'} in ${fmtMs(ev.duration_ms)}`);
      else setNode(ev.agent, 'running', `${ev.step} (${fmtMs(ev.duration_ms)})`);
    }
    if (ev.type === 'run') {
      if (ev.event === 'start' && ev.pipeline === 'collect') setNode('human', '', 'waiting for the run');
      if (ev.event === 'end') {
        liveTxt.textContent = `Last run ${ev.status}`;
        if (ev.summary?.values_staged != null) setNode('human', 'running', `${ev.summary.values_staged} value(s) to review`);
        refreshSoon();
      }
    }
    if (ev.type === 'stream') liveTxt.textContent = ev.connected ? 'Live feed connected' : 'Live feed reconnecting…';
  });
  return off;
}

function debounceAsync(fn, ms) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }
