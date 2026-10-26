// Reports: generate grounded briefs (agents: Analyst -> Writer -> Guardrail -> Publisher), review, approve, download.
import { api, esc, state, can, icon, md, toast, live, fmtTime, statusChip, withAuth, loading, emptyState, prettyOut } from '../util.js';

const DESCR = {
  exec_summary: 'One page for top management: where PNB leads, where it lags and what to watch. Word and PowerPoint.',
  quarterly_brief: 'Area-by-area brief covering business, profitability, asset quality and capital. Word and Excel.',
  speech: 'Talking points for an address or a review meeting, with numbers phrased for speaking. Word.',
  insights: 'Strengths, concerns and suggested actions, each tied to a specific figure. Word.',
  board_pack: 'Deck with the scorecard, trend charts and insights, plus the data workbook with lineage. Word, PowerPoint and Excel.',
};
const STEPS = [['analyst', 'Benchmark Analyst', 'Computes ranks, gaps and trends; writes a numbered fact sheet'],
  ['writer', 'Insight Writer', 'Drafts the narrative citing fact numbers only'],
  ['guardrail', 'Compliance Guardrail', 'Checks every number against the fact sheet, removes anything unsupported, redacts personal data'],
  ['publisher', 'Report Publisher', 'Builds the Word, PowerPoint and Excel files']];

export async function renderReports(root) {
  const canGen = can('report');
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Reports</h1>
      <p class="lede">Briefs, speeches and board packs written from the approved figures. A guardrail agent checks that every number in the text exists in the data before the report is saved; an approver signs it off.</p></header>
    <div class="grid-rep">
      ${canGen ? `<section class="card" id="r-gen">
        <div class="card-head"><h2>Create a report</h2></div>
        <form id="r-form" class="form">
          <fieldset class="rtypes"><legend class="sr-only">Report type</legend>
            ${Object.entries(state.meta.report_types).map(([k, t], i) => `<label class="rtype"><input type="radio" name="rtype" value="${k}" ${i === 0 ? 'checked' : ''}><span><b>${esc(t.replace(' - Peer Benchmarking', ''))}</b><small>${esc(DESCR[k] || '')}</small></span></label>`).join('')}
          </fieldset>
          <label class="check"><input type="checkbox" name="llm" ${state.llm?.available ? 'checked' : 'disabled'}> Write the narrative with the local language model
            <small>${state.llm?.available ? esc(state.llm.model) + ' on this server' : 'Not connected: a fixed template is used instead (same facts, plainer wording)'}</small></label>
          <p class="muted small">Quarter: <b>${esc(state.quarters.find(q => q.key === state.quarterKey)?.label || 'latest')}</b> (change it at the top of the page).${state.provisional ? ' <b>Includes unapproved values</b>, so this report cannot be signed off.' : ''}</p>
          <button class="btn btn-primary" type="submit">${icon('play', 14)} Generate report</button>
        </form>
        <ol class="steps" id="r-steps" hidden>${STEPS.map(([a, t, d]) => `<li data-agent="${a}"><span class="st-dot"></span><div><b>${esc(t)}</b><small>${esc(d)}</small><span class="st-out"></span></div></li>`).join('')}</ol>
      </section>` : ''}
      <section class="card ${canGen ? '' : 'span-2'}">
        <div class="card-head"><h2>Saved reports</h2><button class="icon-btn" id="r-refresh" aria-label="Refresh">${icon('refresh', 16)}</button></div>
        <div id="r-list">${loading()}</div>
      </section>
    </div>
    <section class="card" id="r-view" hidden></section>
  </div>`;

  let watching = null;
  const off = live.on(ev => {
    if (!watching || ev.run_id !== watching) return;
    const steps = root.querySelector('#r-steps');
    if (!steps) return;
    if (ev.type === 'span') {
      const li = steps.querySelector(`[data-agent="${ev.agent}"]`);
      if (!li) return;
      if (ev.event === 'start' && !li.classList.contains('done')) li.className = 'running';
      if (ev.event === 'end' && ev.step.endsWith(': start')) {
        li.className = ev.status === 'ok' ? 'done' : 'failed';
        li.querySelector('.st-out').textContent = prettyOut(ev.output || '').slice(0, 200);
      }
    }
    if (ev.type === 'run' && ev.event === 'end') {
      watching = null;
      root.querySelector('#r-form button[type=submit]').disabled = false;
      loadList().then(() => { if (ev.summary?.report_id) openReport(ev.summary.report_id); });
      if (ev.status !== 'succeeded') toast('Report generation failed. See Agents & logs for the error.', 'error');
    }
  });

  root.querySelector('#r-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const r = await api('/api/reports/generate', { json: { rtype: f.get('rtype'), quarter_key: state.quarterKey, provisional: state.provisional, use_llm: !!f.get('llm') } });
      watching = r.run_id;
      const steps = root.querySelector('#r-steps');
      steps.hidden = false;
      steps.querySelectorAll('li').forEach(li => { li.className = ''; li.querySelector('.st-out').textContent = ''; });
    } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
  });
  root.querySelector('#r-refresh').addEventListener('click', loadList);

  async function loadList() {
    const reps = await api('/api/reports');
    const box = root.querySelector('#r-list');
    if (!reps.length) { box.innerHTML = emptyState('No reports yet', canGen ? 'Choose a report type and generate the first one.' : 'Analysts and approvers can generate reports.'); return; }
    box.innerHTML = `<table class="tbl click">
      <thead><tr><th>Report</th><th>Quarter</th><th>Written by</th><th class="num">Numbers verified</th><th>Status</th><th>Created</th></tr></thead>
      <tbody>${reps.map(r => `<tr data-id="${r.id}" tabindex="0"><td><b>${esc(r.title.replace(/ - Q\d FY\d+$/, ''))}</b>${r.provisional ? ' <span class="chip chip-warn">provisional</span>' : ''}</td><td>${esc(r.quarter)}</td>
        <td>${r.mode === 'llm' ? esc(r.model || 'LLM') : 'Template'}</td><td class="num">${r.grounding != null ? Math.round(r.grounding * 100) + '%' : '–'}</td>
        <td>${statusChip(r.status)}${r.approved_by ? ' <span class="muted small">by ' + esc(r.approved_by) + '</span>' : ''}</td><td class="small">${esc(fmtTime(r.created_at, true))}<br><span class="muted">${esc(r.created_by)}</span></td></tr>`).join('')}</tbody></table>`;
    box.querySelectorAll('tr[data-id]').forEach(tr => {
      tr.addEventListener('click', () => openReport(+tr.dataset.id));
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') openReport(+tr.dataset.id); });
    });
  }

  async function openReport(id) {
    const v = root.querySelector('#r-view');
    v.hidden = false;
    v.innerHTML = loading('Opening report…');
    const r = await api('/api/reports/' + id);
    const facts = Array.isArray(r.facts) ? r.facts : (r.facts?.facts || []);
    const fmap = Object.fromEntries(facts.map(f => [f.id, f]));
    const g = r.guardrail || {};
    const mine = r.created_by === state.user.username;
    const canApprove = can('approve') && r.status === 'draft' && !r.provisional && !mine;
    v.innerHTML = `<div class="card-head"><h2>${esc(r.title)}</h2>
        <div class="row gap">${Object.keys(r.files || {}).map(fmt => `<a class="btn btn-ghost btn-sm" href="${esc(withAuth(`/api/reports/${r.id}/download/${fmt}`))}">${icon('download', 14)} ${esc(fmt.toUpperCase())}</a>`).join('')}
        ${canApprove ? `<button class="btn btn-primary btn-sm" id="r-approve" type="button">${icon('check', 14)} Approve for circulation</button>` : ''}</div></div>
      <div class="rep-meta">
        <div><span class="muted">Status</span><span>${statusChip(r.status)}${r.approved_by ? ` by <b>${esc(r.approved_by)}</b>` : ''}</span></div>
        <div><span class="muted">Written by</span><span>${r.mode === 'llm' ? esc(r.model) : 'Deterministic template'}, prompt <code>${esc(r.prompt_version || '')}</code></span></div>
        <div><span class="muted">Numbers checked</span>${g.grounded ?? '–'} of ${g.numbers ?? '–'} found in the fact sheet (${r.grounding != null ? Math.round(r.grounding * 100) + '%' : '–'})</div>
        <div><span class="muted">Removed by guardrail</span>${g.removed_sentences || 0} sentence(s)${(g.pii_redacted || []).length ? `, ${g.pii_redacted.length} personal-data item(s) redacted` : ''}</div>
      </div>
      ${r.status === 'draft' && mine && can('approve') ? '<p class="banner info">Four-eyes rule: another approver must sign off a report you generated.</p>' : ''}
      ${r.provisional ? '<p class="banner warn">Built on values that were not yet approved. Regenerate after approval to circulate.</p>' : ''}
      <div class="rep-grid">
        <article class="prose">${md(r.body_md, { facts: fmap, shiftHeadings: 1 })}</article>
        <aside class="factlist"><h3>Fact sheet used</h3><p class="muted small">The writer may only use these numbered facts. Hover a citation in the text to see its fact.</p>
          <ol>${facts.map(f => `<li id="fact-${esc(f.id)}"><b>${esc(f.id)}</b> ${esc(f.text)}</li>`).join('')}</ol></aside>
      </div>`;
    v.querySelector('#r-approve')?.addEventListener('click', async () => {
      try { await api(`/api/reports/${r.id}/approve`, { method: 'POST' }); toast('Report approved', 'good'); loadList(); openReport(r.id); }
      catch (err) { toast(err.message, 'error'); }
    });
    v.querySelectorAll('.cite').forEach(c => c.addEventListener('click', () => {
      const li = v.querySelector('#fact-' + c.dataset.fact);
      if (li) { li.scrollIntoView({ block: 'center', behavior: 'smooth' }); li.classList.add('flash'); setTimeout(() => li.classList.remove('flash'), 1400); }
    }));
    v.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  await loadList();
  return () => off();
}
