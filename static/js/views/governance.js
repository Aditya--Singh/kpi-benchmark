// Governance: security controls, tamper-evident audit trail, model and prompt registry, KPI dictionary, users, integration.
import { api, esc, state, can, icon, toast, fmtTime, statusChip, loading } from '../util.js';

const TABS = [['controls', 'Controls'], ['audit', 'Audit trail'], ['models', 'Models & prompts'], ['kpis', 'KPI dictionary'], ['integration', 'Dashboard integration']];

export async function renderGovernance(root) {
  const tabs = can('admin') ? [...TABS, ['users', 'Users']] : TABS;
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Governance</h1>
      <p class="lede">How the platform protects data, keeps a tamper-evident record of every decision, and keeps AI use bounded, explainable and on-premises.</p></header>
    <nav class="subtabs" role="tablist">${tabs.map(([k, t], i) => `<button role="tab" type="button" data-tab="${k}" aria-selected="${i === 0}">${esc(t)}</button>`).join('')}</nav>
    <div id="g-body"></div></div>`;
  const show = async k => {
    root.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    const body = root.querySelector('#g-body');
    body.innerHTML = loading();
    const fn = { controls, audit, models, kpis, integration, users }[k];
    await fn(body);
  };
  root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => show(b.dataset.tab)));
  await show('controls');
}

async function controls(body) {
  const st = state.llm || {};
  const rows = [
    ['Data stays inside the bank', `Language model runs on this server (${esc(st.provider || 'none')}${st.model ? ', ' + esc(st.model) : ''}). Calls to any non-local model address are refused unless an administrator explicitly allows it.`, true],
    ['Public sources only', 'Agents read only the investor-relations pages listed under Sources, honour robots.txt, pause between requests and identify themselves as PNB research.', true],
    ['Maker-checker', 'Analysts prepare, approvers decide. Nobody can approve values or reports they produced (four-eyes rule). Management views show approved values only.', true],
    ['Role-based access', 'Four roles: Administrator, Analyst (maker), Approver (checker), Top Management (viewer). Every API call checks the role.', true],
    ['Tamper-evident audit trail', 'Each audit entry stores the SHA-256 hash of the previous one, so any edit or deletion breaks the chain. Verify it on the Audit trail tab.', true],
    ['Lineage for every number', 'Each value links to the bank document, page, extraction method, confidence, alternatives considered and the approver.', true],
    ['Grounded AI text', 'Report writers receive a numbered fact sheet and must cite it. A guardrail agent removes any sentence whose numbers are not in the facts, and falls back to a template if too much is removed.', true],
    ['Prompt-injection and personal-data screens', 'Chat questions are screened for instruction-override attempts. Personal data patterns (PAN, Aadhaar, phone, e-mail, account numbers) are redacted before any model call and in outputs.', true],
    ['AI transparency', 'Reports state whether a model or a template wrote them, with the model name and prompt version. Chat answers show how they were composed.', true],
    ['Secure sessions', 'Passwords hashed with PBKDF2 (240,000 rounds); five failed sign-ins lock the account for 15 minutes; signed session tokens expire; strict content-security policy; framing allowed only for the configured dashboard host.', true],
    ['Observability', 'Every agent step, log line and model call is stored with timings and streamed live; Prometheus metrics are exposed to localhost for the monitoring stack.', true],
  ];
  body.innerHTML = `<section class="card"><ul class="controls">${rows.map(([t, d, ok]) => `<li>${ok ? `<span class="ok-badge">${icon('check', 14)}</span>` : `<span class="no-badge">${icon('x', 14)}</span>`}<div><b>${t}</b><p>${d}</p></div></li>`).join('')}</ul></section>`;
}

async function audit(body) {
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Audit trail</h2><button class="btn btn-primary btn-sm" id="au-verify" type="button">${icon('shield', 14)} Verify the chain</button></div>
    <div id="au-res"></div><div class="table-wrap" id="au-tbl">${loading()}</div></section>`;
  const draw = async () => {
    const rows = await api('/api/gov/audit?limit=400');
    body.querySelector('#au-tbl').innerHTML = `<table class="tbl compact"><thead><tr><th class="num">#</th><th>Time</th><th>Who</th><th>Action</th><th>On</th><th>Details</th><th>Hash</th><th>Previous</th></tr></thead>
      <tbody>${rows.map(a => `<tr><td class="num">${a.id}</td><td class="small">${esc(fmtTime(a.ts, true))}</td><td>${esc(a.actor)}</td><td><code>${esc(a.action)}</code></td>
      <td class="small">${esc(a.entity || '')} ${esc(a.entity_id || '')}</td><td class="small">${a.details ? `<code>${esc(JSON.stringify(a.details).slice(0, 140))}</code>` : ''}</td>
      <td><code>${esc(a.hash)}</code></td><td><code class="muted">${esc(a.prev)}</code></td></tr>`).join('')}</tbody></table>`;
  };
  body.querySelector('#au-verify').addEventListener('click', async () => {
    const r = await api('/api/gov/audit/verify');
    body.querySelector('#au-res').innerHTML = `<p class="banner ${r.ok ? 'good' : 'bad'}">${icon(r.ok ? 'check' : 'warn', 16)} ${esc(r.message)}</p>`;
    toast(r.ok ? 'Audit chain intact' : 'Audit chain broken', r.ok ? 'good' : 'error');
    draw();
  });
  await draw();
}

async function models(body) {
  const [m, prompts] = await Promise.all([api('/api/gov/models'), api('/api/gov/prompts')]);
  const s = m.status;
  body.innerHTML = `<div class="grid-2">
    <section class="card"><div class="card-head"><h2>Model in use</h2>${statusChip(s.available ? 'ok' : 'awaited')}</div>
      <dl class="dl"><dt>Provider</dt><dd>${esc(s.provider)}</dd><dt>Model</dt><dd>${esc(s.model || '–')}</dd>
      <dt>Status</dt><dd>${esc(s.available ? 'Connected' : (s.reason || 'Not connected'))}</dd>
      ${s.models?.length ? `<dt>Installed on server</dt><dd>${s.models.map(x => `<code>${esc(x)}</code>`).join(' ')}</dd>` : ''}</dl>
      <p class="muted small">Without a model the platform still works end to end: extraction is rule-based, and reports and chat use deterministic templates over the same approved facts.</p></section>
    <section class="card"><div class="card-head"><h2>Model register</h2></div>
      <table class="tbl compact"><thead><tr><th>Model</th><th>Used for</th><th>Hosting</th><th>Risk tier</th><th>Approved by</th></tr></thead>
      <tbody>${m.registry.map(r => `<tr><td><code>${esc(r.name)}</code><br><span class="muted small">${esc(r.provider)}</span></td><td class="small">${esc(r.purpose)}</td><td class="small">${esc(r.hosting)}</td><td>${esc(r.risk_tier)}</td><td>${esc(r.approved_by || '–')}</td></tr>`).join('')}</tbody></table></section>
  </div>
  <section class="card"><div class="card-head"><h2>Prompt register</h2><span class="muted small">The version is a hash of the prompt text, stored with every report and model call</span></div>
    ${prompts.map(p => `<details class="prompt"><summary><b>${esc(p.name)}</b> <code>${esc(p.version)}</code></summary><pre>${esc(p.text)}</pre></details>`).join('')}</section>`;
}

export function plainAlias(rx) {
  return String(rx)
    .replace(/\(\?[:!=][^)]*\)/g, '').replace(/\\s[*+]\??/g, ' ').replace(/\\b/g, '').replace(/[\^$]/g, '')
    .replace(/\\\(/g, '(').replace(/\\\)/g, ')').replace(/\\\./g, '.').replace(/\\%/g, '%').replace(/\\\+/g, '+')
    .replace(/\(([^()|]+)\)\?/g, '[$1]').replace(/\(([^()]+)\)/g, (m, g) => g.includes('|') ? g.split('|').join(' / ') : g)
    .replace(/\[\s*-\s*\]\??/g, '').replace(/\?/g, '').replace(/\s*-\s*/g, '-').replace(/\s+/g, ' ').trim();
}

async function kpis(body) {
  const list = await api('/api/gov/kpis');
  body.innerHTML = `<section class="card"><div class="card-head"><h2>KPI dictionary</h2><span class="muted small">How each bank's wording is mapped to one definition</span></div>
    <div class="table-wrap"><table class="tbl compact"><thead><tr><th>Measure</th><th>Definition and basis</th><th>Better</th><th>Recognised wording</th><th>Never matched when the label says</th><th>Plausible range</th></tr></thead>
    <tbody>${list.filter(k => k.visible).map(k => `<tr><td><b>${esc(k.name)}</b><br><code class="muted">${esc(k.code)}</code></td>
      <td class="small">${esc(k.definition)}<br><span class="muted">${esc(k.basis)}${k.derived ? '. If not published: ' + esc(k.derived.replace(/_/g, ' ').toLowerCase()) : ''}</span></td>
      <td>${k.direction > 0 ? 'Higher' : 'Lower'}</td>
      <td class="small">${k.aliases.map(a => `<span class="alias">${esc(plainAlias(a))}</span>`).join(' ')}</td>
      <td class="small muted">${k.exclude.slice(0, 8).map(a => esc(plainAlias(a))).join(', ')}${k.exclude.length > 8 ? '…' : ''}</td>
      <td class="small num">${k.range[0] ?? ''} to ${k.range[1] ?? ''}${k.unit === '%' ? '%' : ' cr'}</td></tr>`).join('')}</tbody></table></div></section>`;
}

async function integration(body) {
  const origin = location.origin;
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Adding this as a tab in the Top Management Dashboard</h2></div>
    <p>Three ways, all protected by the same roles and audit trail. Step-by-step instructions with code for the dashboard team are in <code>docs/INTEGRATION.md</code>.</p>
    <h3>1. Link the tab to this app</h3>
    <p class="small">The tab opens <code>${esc(origin)}/</code>; executives sign in with a viewer account. Set <code>DASHBOARD_URL</code> in .env to show a link back to the dashboard.</p>
    <h3>2. One-click sign-in (recommended)</h3>
    <p class="small">With <code>HANDOFF_SECRET</code> set, the dashboard's server calls <code>POST /api/auth/handoff</code> and redirects the executive with a single-use ticket valid ${esc(String(120))} seconds. The executive lands on the chosen screen already signed in, as a read-only viewer. ${state.meta.dashboard && state.meta.dashboard.url ? 'Back link: <b>' + esc(state.meta.dashboard.url) + '</b>.' : 'No dashboard link is configured yet.'}</p>
    <h3>3. Embed the screens</h3>
    <p class="small">As option 2 with <code>embed</code> set, shown in an iframe. Set <code>FRAME_ANCESTORS</code> in .env to the dashboard's address so the browser allows framing; an https dashboard needs this app on https too.</p>
    <h3>Data API</h3>
    <p class="small">Every number on screen is available as JSON. Authenticate with <code>POST /api/auth/login</code> and send the token as <code>Authorization: Bearer …</code>. Full interactive documentation: <a href="/docs" target="_blank" rel="noopener">${esc(origin)}/docs</a>.</p>
    <table class="tbl compact"><thead><tr><th>Endpoint</th><th>Returns</th></tr></thead><tbody>
      <tr><td><code>GET /api/bench/scorecard?quarter_key=272</code></td><td>All KPIs for all banks with ranks, peer average and gaps</td></tr>
      <tr><td><code>GET /api/bench/trend?kpi=GNPA&amp;n=20</code></td><td>Up to 20 quarters per bank plus peer average</td></tr>
      <tr><td><code>GET /api/bench/facts</code></td><td>Numbered fact sheet and insights (strengths, concerns, actions)</td></tr>
      <tr><td><code>GET /api/bench/lineage?bank=PNB&amp;kpi=NIM&amp;quarter_key=272</code></td><td>Source document, page, evidence and approvals for one number</td></tr>
      <tr><td><code>GET /api/bench/export.xlsx</code></td><td>Excel workbook of the scorecard with lineage</td></tr>
      <tr><td><code>POST /api/chat</code></td><td>Myra's answer with chart data and sources</td></tr>
      <tr><td><code>GET /healthz</code>, <code>GET /metrics</code></td><td>Health check and Prometheus metrics for monitoring</td></tr>
    </tbody></table></section>`;
}

async function users(body) {
  const list = await api('/api/gov/users');
  body.innerHTML = `<section class="card"><div class="card-head"><h2>Users</h2></div><table class="tbl compact"><thead><tr><th>User ID</th><th>Name</th><th>Role</th><th>Active</th><th>Last sign-in</th></tr></thead>
    <tbody>${list.map(u => `<tr><td><code>${esc(u.username)}</code></td><td>${esc(u.full_name || '')}</td><td>${esc(u.role_label)}</td><td>${u.active ? 'Yes' : 'No'}</td><td class="small">${esc(u.last_login ? fmtTime(u.last_login, true) : 'never')}</td></tr>`).join('')}</tbody></table>
    <p class="muted small">Users are created and reset with the command-line tool on the server (python -m app.cli). Change the default passwords before the demo.</p></section>`;
}
