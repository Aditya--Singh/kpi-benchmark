// Data & approvals: run the collection agents, review and approve values (maker-checker), documents, coverage, sources.
import { api, esc, state, can, icon, toast, live, fmtVal, fmtTime, fmtMs, bankName, bankColor, flagChip, statusChip, METHOD_TEXT,
  openDrawer, closeDrawer, withAuth, loading, emptyState, prefs } from '../util.js';
import { showLineage } from '../lineage.js';
import { prettyOut } from '../util.js';

const TABS = [['run', 'Collect data'], ['review', 'Review & approve'], ['docs', 'Documents'], ['coverage', 'Coverage'], ['sources', 'Sources & upload']];
const COLLECT = [['scout', 'Source Scout', 'Finds quarterly presentation PDFs on each bank\'s investor page'],
  ['fetcher', 'Document Fetcher', 'Downloads each PDF once, checks it and fingerprints it'],
  ['extractor', 'KPI Extractor', 'Reads tables, charts and sentences; maps each bank\'s wording to one KPI'],
  ['indexer', 'Knowledge Indexer', 'Indexes page text so Myra can quote management commentary'],
  ['reconciler', 'Validation & Reconciliation', 'Cross-checks readings, picks the best-supported value and raises flags']];

export async function renderData(root, ctx) {
  let tab = (location.hash.split('?')[1] || '').match(/tab=(\w+)/)?.[1] || prefs.get('data.tab', can('approve') && !can('run') ? 'review' : 'run');
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Data & approvals</h1>
      <p class="lede">Agents collect and read the banks' published presentations; nothing reaches management until a human approver accepts it. Analysts run collection, approvers check the values.</p></header>
    <nav class="subtabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" type="button" data-tab="${k}" aria-selected="${k === tab}">${esc(t)}</button>`).join('')}</nav>
    <div id="d-body"></div></div>`;
  let off = null;
  const show = async k => {
    tab = k; prefs.set('data.tab', k);
    root.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    if (off) { off(); off = null; }
    const body = root.querySelector('#d-body');
    body.innerHTML = loading();
    if (k === 'run') off = await runTab(body, ctx);
    if (k === 'review') await reviewTab(body, ctx);
    if (k === 'docs') await docsTab(body);
    if (k === 'coverage') await coverageTab(body);
    if (k === 'sources') await sourcesTab(body);
  };
  root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => show(b.dataset.tab)));
  await show(tab);
  return () => off && off();
}

// --------------------------------------------------------------------------- run
async function runTab(body, ctx) {
  const st = await api('/api/pipeline/status');
  const canRun = can('run');
  body.innerHTML = `<div class="grid-run">
    <section class="card">
      <div class="card-head"><h2>Start a collection run</h2></div>
      ${canRun ? `<form class="form" id="run-form">
        <fieldset><legend>Banks</legend><div class="checks">${state.bankOrder.map(b => `<label class="check-chip"><input type="checkbox" name="bank" value="${b}" checked><span class="bdot" style="background:${bankColor(b)}"></span>${esc(bankName(b))}</label>`).join('')}</div></fieldset>
        <fieldset><legend>History</legend><div class="seg" role="radiogroup">${[[4, '1 year'], [8, '2 years'], [12, '3 years'], [20, '5 years']].map(([n, l]) => `<label><input type="radio" name="quarters" value="${n}" ${n === 20 ? 'checked' : ''}><span>${l}</span></label>`).join('')}</div></fieldset>
        <fieldset><legend>Where to read from</legend>
          <label class="radio"><input type="radio" name="mode" value="online" checked> The banks' investor-relations websites <small>Respects robots.txt, waits between requests, identifies itself as PNB research</small></label>
          <label class="radio"><input type="radio" name="mode" value="offline"> Only PDFs already placed in the inbox <small>Use when the bank network blocks a site; drop files under data/inbox/&lt;BANK&gt;/ or upload them in Sources</small></label>
        </fieldset>
        <fieldset><legend>Options</legend>
          <label class="check"><input type="checkbox" name="use_llm" ${state.llm?.available ? '' : 'disabled'}> Let the local language model read KPIs the rules missed <small>${state.llm?.available ? 'Slow: one model call per missing KPI per document (a 5-year run can take hours). Each reading is flagged for one-by-one review. Best used for a single bank or quarter.' : 'No model connected'}</small></label>
          <label class="check"><input type="checkbox" name="reextract"> Re-read documents already processed</label>
          <label class="check"><input type="checkbox" name="force_download"> Download again even if the file is cached</label>
        </fieldset>
        <button class="btn btn-primary" type="submit" ${st.busy ? 'disabled' : ''}>${icon('play', 14)} ${st.busy ? 'A run is in progress' : 'Start collection'}</button>
      </form>` : '<p class="muted">Only analysts and administrators can start a run. You can follow runs live under Agents & logs.</p>'}
    </section>
    <section class="card">
      <div class="card-head"><h2>Live progress</h2><a class="btn btn-ghost btn-sm" href="#/agents">Open full observability</a></div>
      <ol class="steps" id="run-steps">${COLLECT.map(([a, t, d]) => `<li data-agent="${a}"><span class="st-dot"></span><div><b>${esc(t)}</b><small>${esc(d)}</small><span class="st-out"></span></div></li>`).join('')}
        <li data-agent="human"><span class="st-dot human"></span><div><b>Human approval</b><small>An approver reviews flagged values and accepts them; only approved values feed dashboards, reports and Myra</small><span class="st-out"></span></div></li></ol>
      <div class="console console-sm" id="run-log" aria-live="polite"></div>
    </section>
  </div>
  <section class="card"><div class="card-head"><h2>Recent runs</h2></div><div id="run-hist"></div></section>`;

  const hist = body.querySelector('#run-hist');
  const drawHist = runs => {
    hist.innerHTML = runs.length ? `<table class="tbl"><thead><tr><th>Started</th><th>Pipeline</th><th>By</th><th>Status</th><th class="num">Took</th><th>Result</th></tr></thead>
      <tbody>${runs.map(r => `<tr><td class="small">${esc(fmtTime(r.started_at, true))}</td><td>${esc(r.pipeline === 'collect' ? 'Data collection' : 'Report')}</td><td>${esc(r.triggered_by || '')}</td>
        <td>${statusChip(r.status)}</td><td class="num">${esc(fmtMs(r.duration_ms))}</td><td class="small">${esc(summ(r))}</td></tr>`).join('')}</tbody></table>` : '<p class="muted pad">No runs yet.</p>';
  };
  drawHist(st.runs);

  let watching = st.active?.[0] || null;
  const steps = body.querySelector('#run-steps'), logBox = body.querySelector('#run-log');
  const off = live.on(ev => {
    if (ev.type === 'run' && ev.event === 'start' && ev.pipeline === 'collect') {
      watching = ev.run_id;
      steps.querySelectorAll('li').forEach(li => { li.className = ''; li.querySelector('.st-out').textContent = ''; });
      logBox.innerHTML = '';
    }
    if (!watching || ev.run_id !== watching) return;
    if (ev.type === 'span') {
      const li = steps.querySelector(`[data-agent="${ev.agent}"]`);
      if (li) {
        if (ev.event === 'start' && !li.classList.contains('done')) { li.className = 'running'; li.querySelector('.st-out').textContent = ev.step; }
        if (ev.event === 'end' && ev.step.endsWith(': start')) { li.className = ev.status === 'ok' ? 'done' : 'failed'; li.querySelector('.st-out').textContent = prettyOut(ev.output || ev.error || '').slice(0, 220); }
      }
    }
    if (ev.type === 'log') {
      const line = document.createElement('div');
      line.className = 'ln lv-' + (ev.level || 'INFO').toLowerCase();
      line.innerHTML = `<span class="ts">${esc(fmtTime(ev.ts))}</span><span class="ag">${esc(ev.agent || '')}</span>${esc(ev.message)}`;
      logBox.appendChild(line);
      while (logBox.children.length > 80) logBox.firstChild.remove();
      logBox.scrollTop = logBox.scrollHeight;
    }
    if (ev.type === 'run' && ev.event === 'end') {
      const h = steps.querySelector('[data-agent="human"]');
      h.className = 'waiting';
      h.querySelector('.st-out').textContent = ev.status === 'succeeded' ? 'Values are staged. Open “Review & approve”.' : 'Run did not finish; see the log.';
      api('/api/pipeline/status').then(s => drawHist(s.runs));
      const btn = body.querySelector('#run-form button[type=submit]');
      if (btn) { btn.disabled = false; btn.innerHTML = icon('play', 14) + ' Start collection'; }
      watching = null;
    }
  });

  body.querySelector('#run-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const banks = f.getAll('bank');
    if (!banks.length) { toast('Choose at least one bank', 'warn'); return; }
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true; btn.textContent = 'Starting…';
    try {
      const r = await api('/api/pipeline/run', { json: { banks, quarters: +f.get('quarters'), offline: f.get('mode') === 'offline', use_llm: !!f.get('use_llm'), reextract: !!f.get('reextract'), force_download: !!f.get('force_download') } });
      watching = r.run_id;
      btn.textContent = 'Running…';
      toast('Collection started. Watch the agents work on the right.', 'info');
    } catch (err) { toast(err.message, 'error'); btn.disabled = false; btn.innerHTML = icon('play', 14) + ' Start collection'; }
  });
  void ctx;
  return off;
}

function summ(r) {
  const s = r.summary || {};
  if (r.error) return r.error.slice(0, 120);
  if (r.pipeline === 'collect') return `${s.documents_new ?? 0} new documents, ${s.candidates ?? 0} readings, ${s.values_staged ?? 0} values staged${s.download_failed ? `, ${s.download_failed} downloads failed` : ''}`;
  if (r.pipeline === 'report') return `report #${s.report_id ?? '–'}, ${s.mode || ''}, ${s.grounding != null ? Math.round(s.grounding * 100) + '% numbers verified' : ''}`;
  return '';
}

// --------------------------------------------------------------------------- review (maker-checker)
async function reviewTab(body) {
  const f = { view: prefs.get('rev.view', 'needs'), bank: '', qk: '' };
  const canApprove = can('approve');
  body.innerHTML = `
    <section class="card" id="rv-findings"></section>
    <section class="card">
      <div class="card-head"><h2>Values</h2><div class="counts" id="rv-counts"></div></div>
      <div class="filters">
        <div class="seg" role="radiogroup" aria-label="Show">${[['needs', 'Needs review'], ['clean', 'Clean'], ['staged', 'All waiting'], ['approved', 'Approved'], ['rejected', 'Rejected']].map(([k, t]) => `<button type="button" role="radio" data-view="${k}" aria-checked="${f.view === k}">${t}</button>`).join('')}</div>
        <label>Bank<select id="rv-bank"><option value="">All</option>${state.bankOrder.map(b => `<option value="${b}">${esc(bankName(b))}</option>`).join('')}</select></label>
        <label>Quarter<select id="rv-q"><option value="">All</option>${state.quarters.map(q => `<option value="${q.key}">${esc(q.label)}</option>`).join('')}</select></label>
        ${canApprove ? `<span class="spacer"></span><button class="btn btn-ghost btn-sm" id="rv-sel" type="button" disabled>${icon('check', 14)} Approve selected</button>
          <button class="btn btn-primary btn-sm" id="rv-clean" type="button">${icon('check', 14)} Approve all clean values</button>
          <button class="btn btn-ghost btn-sm" id="rv-add" type="button">${icon('edit', 14)} Enter a missing value</button>` : ''}
      </div>
      ${canApprove ? '' : '<p class="muted small">You can see the queue; approving needs the Approver role. Values you prepared cannot be approved by you (four-eyes rule).</p>'}
      <div class="table-wrap" id="rv-table">${loading()}</div>
    </section>`;

  const SEV = { error: 0, warn: 1, info: 2 };
  const findings = (await api('/api/review/findings')).sort((a, b) => (SEV[a.severity] ?? 3) - (SEV[b.severity] ?? 3));
  const fbox = body.querySelector('#rv-findings');
  const warn = findings.filter(x => x.severity !== 'info');
  fbox.innerHTML = `<div class="card-head"><h2>What the validation agent found</h2><span class="muted small">${findings.length} open finding(s)</span></div>` +
    (findings.length ? `<ul class="findings">${findings.slice(0, 40).map(x => `<li class="sev-${esc(x.severity)}">${icon(x.severity === 'info' ? 'info' : 'warn', 15)}<span><b>${esc(x.rule.replace(/_/g, ' '))}</b> ${esc(x.message)}</span>
      ${x.bank && x.kpi && x.quarter_key ? `<button class="link-btn" type="button" data-lin="${esc(x.bank)}|${esc(x.kpi)}|${x.quarter_key}">evidence</button>` : ''}</li>`).join('')}</ul>${findings.length > 40 ? `<p class="muted small">Showing 40 of ${findings.length}.</p>` : ''}` : '<p class="muted pad">No open findings.</p>');
  void warn;

  const load = async () => {
    const p = new URLSearchParams({ limit: '1500' });
    if (f.view === 'needs') { p.set('status', 'staged'); p.set('flag', 'any'); }
    else if (f.view === 'clean') { p.set('status', 'staged'); p.set('flag', 'clean'); }
    else p.set('status', f.view === 'staged' ? 'staged' : f.view);
    if (f.bank) p.set('bank', f.bank);
    if (f.qk) p.set('quarter_key', f.qk);
    const r = await api('/api/review/values?' + p);
    body.querySelector('#rv-counts').innerHTML = Object.entries(r.counts).map(([k, n]) => `<span>${statusChip(k)} ${n}</span>`).join(' ');
    const items = r.items.filter(i => i.visible || f.view !== 'approved');
    const tb = body.querySelector('#rv-table');
    if (!items.length) {
      tb.innerHTML = emptyState(f.view === 'needs' ? 'Nothing needs review' : 'No values here', f.view === 'needs' ? 'Every waiting value passed the automatic checks. Approve the clean values to publish them.' : 'Try another filter.');
      return;
    }
    tb.innerHTML = `<table class="tbl review">
      <thead><tr>${canApprove ? '<th><input type="checkbox" id="rv-all" aria-label="Select all"></th>' : ''}<th>Bank</th><th>Measure</th><th>Quarter</th><th class="num">Value</th><th>Read from</th><th class="num">Confidence</th><th>Checks</th><th>Decision</th><th></th></tr></thead>
      <tbody>${items.map(i => `<tr data-id="${i.id}">
        ${canApprove ? `<td>${i.status === 'staged' ? `<input type="checkbox" class="rv-c" value="${i.id}" aria-label="Select">` : ''}</td>` : ''}
        <td><span class="bdot" style="background:${bankColor(i.bank)}"></span>${esc(bankName(i.bank))}</td>
        <td>${esc(i.kpi_name)}${i.visible ? '' : ' <span class="muted small">(helper)</span>'}</td><td class="nowrap">${esc(i.quarter)}</td>
        <td class="num"><b>${esc(fmtVal(i.value, i.unit, { compact: false }))}</b>${(i.alternatives || []).length ? `<small class="alt">also seen: ${i.alternatives.slice(0, 2).map(a => esc(fmtVal(a.value, i.unit, { compact: false }))).join(', ')}</small>` : ''}</td>
        <td class="small">${esc(METHOD_TEXT[i.method] || i.method)}${i.page ? ', p.' + esc(i.page) : ''}<br><span class="muted">${esc(shortDoc(i.doc_title))}</span></td>
        <td class="num"><span class="conf"><span style="width:${Math.round(i.confidence * 100)}%" class="${i.confidence >= 0.8 ? 'hi' : i.confidence >= 0.65 ? 'mid' : 'lo'}"></span></span>${Math.round(i.confidence * 100)}%</td>
        <td>${(i.flags || []).map(flagChip).join(' ') || '<span class="muted small">none</span>'}</td>
        <td>${statusChip(i.status)}${i.checker ? `<br><span class="muted small">${esc(i.checker)}</span>` : ''}</td>
        <td class="row-actions"><button class="link-btn" type="button" data-ev="${i.id}">evidence</button>
          ${canApprove && i.status === 'staged' ? `<button class="mini good" type="button" data-ap="${i.id}" title="Approve">${icon('check', 14)}</button><button class="mini bad" type="button" data-rj="${i.id}" title="Reject">${icon('x', 14)}</button>` : ''}
          ${canApprove ? `<button class="mini" type="button" data-ov="${i.id}" title="Correct the value">${icon('edit', 14)}</button>` : ''}</td></tr>`).join('')}</tbody></table>
      ${r.total > items.length ? `<p class="muted small">Showing ${items.length} of ${r.total}.</p>` : ''}`;
    const byId = Object.fromEntries(items.map(i => [i.id, i]));
    tb.querySelectorAll('[data-ev]').forEach(b => b.addEventListener('click', () => { const i = byId[b.dataset.ev]; showLineage(i.bank, i.kpi, i.quarter_key); }));
    tb.querySelectorAll('[data-ap]').forEach(b => b.addEventListener('click', () => approve([+b.dataset.ap])));
    tb.querySelectorAll('[data-rj]').forEach(b => b.addEventListener('click', () => rejectDlg(byId[b.dataset.rj])));
    tb.querySelectorAll('[data-ov]').forEach(b => b.addEventListener('click', () => overrideDlg(byId[b.dataset.ov])));
    const selBtn = body.querySelector('#rv-sel');
    const upd = () => { if (selBtn) { const n = tb.querySelectorAll('.rv-c:checked').length; selBtn.disabled = !n; selBtn.innerHTML = icon('check', 14) + (n ? ` Approve ${n} selected` : ' Approve selected'); } };
    tb.querySelector('#rv-all')?.addEventListener('change', e => { tb.querySelectorAll('.rv-c').forEach(c => { c.checked = e.target.checked; }); upd(); });
    tb.querySelectorAll('.rv-c').forEach(c => c.addEventListener('change', upd));
  };

  const approve = async ids => {
    try {
      const r = await api('/api/review/approve', { json: { ids } });
      toast(`${r.approved} value(s) approved${r.skipped_own_run ? `, ${r.skipped_own_run} skipped (you prepared them)` : ''}`, r.approved ? 'good' : 'warn');
      load();
    } catch (e) { toast(e.message, 'error'); }
  };
  const rejectDlg = i => {
    const b = openDrawer(`Reject ${bankName(i.bank)} ${i.kpi_name}, ${i.quarter}`, `<form class="form"><p>Value <b>${esc(fmtVal(i.value, i.unit, { compact: false }))}</b> will not be used anywhere.</p>
      <label>Reason (recorded in the audit trail)<textarea name="reason" required minlength="3" rows="3"></textarea></label><button class="btn btn-danger" type="submit">Reject value</button></form>`);
    b.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault();
      try { await api('/api/review/reject', { json: { ids: [i.id], reason: new FormData(e.target).get('reason') } }); toast('Value rejected', 'good'); closeDrawer(); load(); }
      catch (err) { toast(err.message, 'error'); }
    });
  };
  const overrideDlg = i => {
    const b = openDrawer(`Correct ${bankName(i.bank)} ${i.kpi_name}, ${i.quarter}`, `<form class="form">
      <p>Current value <b>${esc(fmtVal(i.value, i.unit, { compact: false }))}</b> read from ${esc(METHOD_TEXT[i.method] || i.method)}${i.page ? ', page ' + esc(i.page) : ''}.</p>
      <label>Correct value (${i.unit === '%' ? 'percent' : '₹ crore'})<input name="value" type="number" step="any" required value="${esc(i.value)}"></label>
      <label>Reason and source (recorded in the audit trail)<textarea name="reason" required minlength="5" rows="3" placeholder="e.g. Annualised slippage ratio per page 14 of the Q2 FY27 presentation"></textarea></label>
      <button class="btn btn-primary" type="submit">Save corrected value</button></form>`);
    b.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try { await api('/api/review/override', { json: { id: i.id, value: +fd.get('value'), reason: fd.get('reason') } }); toast('Corrected value saved and approved', 'good'); closeDrawer(); load(); }
      catch (err) { toast(err.message, 'error'); }
    });
  };

  body.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => {
    f.view = b.dataset.view; prefs.set('rev.view', f.view);
    body.querySelectorAll('[data-view]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    load();
  }));
  body.querySelector('#rv-bank').addEventListener('change', e => { f.bank = e.target.value; load(); });
  body.querySelector('#rv-q').addEventListener('change', e => { f.qk = e.target.value; load(); });
  body.querySelector('#rv-clean')?.addEventListener('click', async () => {
    try {
      const r = await api('/api/review/approve', { json: { all_clean: true, bank: f.bank || null, quarter_key: f.qk ? +f.qk : null } });
      toast(`${r.approved} clean value(s) approved${r.skipped_own_run ? `; ${r.skipped_own_run} skipped because you prepared them` : ''}`, 'good');
      load();
    } catch (e) { toast(e.message, 'error'); }
  });
  body.querySelector('#rv-add')?.addEventListener('click', () => {
    const [cq, cfy] = (state.meta.current_quarter.match(/Q(\d) FY(\d+)/) || []).slice(1).map(Number);
    const qs = [];
    let fy = cfy, q = cq;
    for (let i = 0; i < 20 && fy; i++) { qs.push({ key: fy * 10 + q, label: `Q${q} FY${fy}` }); q -= 1; if (q === 0) { q = 4; fy -= 1; } }
    const b = openDrawer('Enter a missing value', `<form class="form">
      <p class="muted">Use this when a value is in the bank's document but the agents could not read it (for example an image-only page). It is saved as a manual value with your name and reason in the audit trail.</p>
      <label>Bank<select name="bank">${state.bankOrder.map(x => `<option value="${x}">${esc(bankName(x, true))}</option>`).join('')}</select></label>
      <label>Measure<select name="kpi">${state.meta.kpis.filter(k => k.visible).map(k => `<option value="${k.code}">${esc(k.name)} (${k.unit === '%' ? '%' : '₹ crore'})</option>`).join('')}</select></label>
      <label>Quarter<select name="qk">${qs.map(x => `<option value="${x.key}">${esc(x.label)}</option>`).join('')}</select></label>
      <label>Value<input name="value" type="number" step="any" required></label>
      <label>Source and reason<textarea name="reason" required minlength="5" rows="3" placeholder="e.g. Q2 FY27 investor presentation, page 9 (image slide)"></textarea></label>
      <button class="btn btn-primary" type="submit">Save value</button></form>`);
    b.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        await api('/api/review/override', { json: { bank: fd.get('bank'), kpi: fd.get('kpi'), quarter_key: +fd.get('qk'), value: +fd.get('value'), reason: fd.get('reason') } });
        toast('Value saved', 'good'); closeDrawer(); load();
      } catch (err) { toast(err.message, 'error'); }
    });
  });
  body.querySelector('#rv-sel')?.addEventListener('click', () => approve([...body.querySelectorAll('.rv-c:checked')].map(c => +c.value)));
  fbox.querySelectorAll('[data-lin]').forEach(b => b.addEventListener('click', () => { const [bk, k, q] = b.dataset.lin.split('|'); showLineage(bk, k, +q); }));
  await load();
}

function shortDoc(t) { t = String(t || ''); const m = t.match(/ - (.*)$/); t = m ? m[1] : t; return t.length > 38 ? t.slice(0, 37) + '…' : t; }

// --------------------------------------------------------------------------- documents
async function docsTab(body) {
  const docs = await api('/api/pipeline/documents');
  if (!docs.length) { body.innerHTML = emptyState('No documents yet', 'Run a collection to discover and download the banks\' presentations.'); return; }
  body.innerHTML = `<section class="card"><div class="card-head"><h2>${docs.length} documents</h2></div><div class="table-wrap"><table class="tbl">
    <thead><tr><th>Bank</th><th>Quarter</th><th>Document</th><th>Status</th><th class="num">Pages</th><th class="num">KPIs found</th><th class="num">Readings</th><th>Fingerprint</th><th></th></tr></thead>
    <tbody>${docs.map(d => `<tr><td><span class="bdot" style="background:${bankColor(d.bank)}"></span>${esc(bankName(d.bank))}</td><td>${esc(d.quarter || '–')}</td>
      <td class="small">${esc(shortDoc(d.title))}${d.url && /^https?:/.test(d.url) ? `<br><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer" class="muted">${esc(new URL(d.url).hostname)}</a>` : `<br><span class="muted">${esc(d.url || '')}</span>`}</td>
      <td>${statusChip(d.status)}${d.error ? `<br><span class="error small">${esc(d.error.slice(0, 90))}</span>` : ''}${d.image_only_pages ? `<br><span class="chip chip-warn" title="These pages are images; the text layer is empty and values on them need manual entry or OCR">${d.image_only_pages} image-only page(s)</span>` : ''}</td>
      <td class="num">${d.pages ?? '–'}</td><td class="num">${d.kpis_found != null ? d.kpis_found + ' / 19' : '–'}</td><td class="num">${d.candidates ?? '–'}</td>
      <td><code>${esc((d.sha256 || '').slice(0, 10))}</code></td>
      <td>${d.status !== 'discovered' && d.status !== 'failed_download' ? `<a class="link-btn" href="${esc(withAuth(`/api/pipeline/documents/${d.id}/file`))}" target="_blank" rel="noopener">open PDF</a>` : ''}</td></tr>`).join('')}</tbody></table></div></section>`;
}

// --------------------------------------------------------------------------- coverage
async function coverageTab(body) {
  const c = await api('/api/bench/coverage');
  if (!c.quarters.length) { body.innerHTML = emptyState('Nothing collected yet', 'Coverage shows, for each bank and quarter, how many of the 19 measures are approved.'); return; }
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Approved measures per bank and quarter</h2><span class="muted small">out of ${c.kpi_count}</span></div>
    <div class="table-wrap"><table class="tbl coverage"><thead><tr><th>Bank</th>${c.quarters.map(q => `<th class="num">${esc(q.label)}</th>`).join('')}</tr></thead>
    <tbody>${c.banks.map(b => `<tr><th scope="row"><span class="bdot" style="background:${bankColor(b)}"></span>${esc(bankName(b))}</th>${c.quarters.map(q => {
      const cell = c.cells[`${b}|${q.key}`];
      if (!cell) return '<td class="num cv-0">–</td>';
      const share = cell.approved / c.kpi_count;
      const lvl = share >= 0.9 ? 4 : share >= 0.6 ? 3 : share >= 0.3 ? 2 : share > 0 ? 1 : 0;
      return `<td class="num cv-${lvl}" data-tip="${esc(`<div class='tt-h'>${esc(bankName(b))} ${esc(q.label)}</div>${cell.approved} approved, ${cell.staged} waiting<br>${cell.docs} document(s) for this quarter`)}">${cell.approved}${cell.staged ? `<small>+${cell.staged}</small>` : ''}${cell.docs ? '<i class="docmark" aria-label="has its own document"></i>' : ''}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>
    <p class="muted small">Older quarters are filled from the history charts and comparison columns inside newer presentations, so one deck often covers four or five quarters. A dot marks quarters whose own presentation was read. “+n” are values waiting for approval.</p></section>`;
}

// --------------------------------------------------------------------------- sources + upload
async function sourcesTab(body) {
  const src = state.meta.sources;
  body.innerHTML = `<div class="grid-2">
    <section class="card"><div class="card-head"><h2>Where the agents look</h2></div>
      <p class="muted small">Configured in config/sources.yaml. Only these public investor-relations pages are read.</p>
      <ul class="sources">${Object.entries(src).map(([b, s]) => `<li><span class="bdot" style="background:${bankColor(b)}"></span><div><b>${esc(s.name)}</b> <span class="muted small">${esc(String(s.doc_type).replace('_', ' '))}</span>
        ${s.pages.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>`).join('')}</div></li>`).join('')}</ul></section>
    <section class="card"><div class="card-head"><h2>Add a presentation by hand</h2></div>
      ${can('run') ? `<form class="form" id="up-form">
        <p class="muted small">If a website is unreachable from the bank network, download the PDF elsewhere and add it here. It goes through exactly the same agents and approval.</p>
        <label>Bank<select name="bank">${state.bankOrder.map(b => `<option value="${b}">${esc(bankName(b, true))}</option>`).join('')}</select></label>
        <label>PDF file<input type="file" name="file" accept="application/pdf,.pdf" required></label>
        <p class="muted small">Tip: keep the quarter in the file name, for example PNB_Q2FY27_Analyst_Presentation.pdf.</p>
        <button class="btn btn-primary" type="submit">${icon('upload', 14)} Upload to inbox</button>
      </form>` : '<p class="muted">Analysts can upload presentations.</p>'}
    </section></div>`;
  body.querySelector('#up-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api('/api/pipeline/upload', { body: fd, method: 'POST' });
      toast(`Saved to ${r.path}. Start a collection run in “Use only PDFs already placed in the inbox” mode to process it.`, 'good', 8000);
      e.target.reset();
    } catch (err) { toast(err.message, 'error'); }
  });
}
