// App shell: sign-in, navigation, global quarter / provisional controls, live run indicator.
import { state, api, $, $$, esc, icon, toast, setToken, loadToken, onUnauthorized, can, live, tip, hideTip, openDrawer, closeDrawer, prefs } from './util.js';
import { mountMyra, updateMode, avatarSvg } from './myra.js';
import { renderOverview } from './views/overview.js';
import { renderTrends } from './views/trends.js';
import { renderCompare } from './views/compare.js';
import { renderReports } from './views/reports.js';
import { renderData } from './views/data.js';
import { renderAgents } from './views/agents.js';
import { renderGovernance } from './views/governance.js';

const VIEWS = {
  overview: { title: 'Overview', icon: 'overview', render: renderOverview },
  trends: { title: 'Trends', icon: 'trends', render: renderTrends },
  compare: { title: 'Peer comparison', icon: 'compare', render: renderCompare },
  reports: { title: 'Reports', icon: 'reports', render: renderReports },
  data: { title: 'Data & approvals', icon: 'data', render: renderData },
  agents: { title: 'Agents & logs', icon: 'agents', render: renderAgents },
  governance: { title: 'Governance', icon: 'governance', render: renderGovernance },
};

let current = null, cleanup = null, myra = null;
const params = new URLSearchParams(location.search);
state.embed = params.get('embed') === '1';

// --------------------------------------------------------------------------- boot
async function boot() {
  document.body.classList.toggle('embed', state.embed);
  const cleanUrl = () => history.replaceState(null, '', location.pathname + (state.embed ? '?embed=1' : '') + location.hash);
  onUnauthorized(() => { setToken(null); live.stop(); showLogin(); });
  if (params.get('ticket')) {
    // one-click arrival from the Top Management Dashboard: swap the single-use ticket for a session
    const ticket = params.get('ticket');
    cleanUrl();
    try {
      const r = await api('/api/auth/exchange', { json: { ticket }, noAuthRedirect: true });
      setToken(r.token);
      state.user = r.user;
      await start();
      return;
    } catch (e) {
      showLogin(e.message);
      return;
    }
  }
  if (params.get('token')) { setToken(params.get('token')); cleanUrl(); }
  else loadToken();
  try {
    state.user = await api('/api/auth/me', { noAuthRedirect: true });
    await start();
  } catch {
    showLogin();
  }
}

function showLogin(msg) {
  $('#app').hidden = true;
  const box = $('#login');
  box.hidden = false;
  box.innerHTML = `
    <div class="login-brand">
      <div class="brand-mark" aria-hidden="true">PNB</div>
      <h1>Peer Benchmarking</h1>
      <p>Compares Punjab National Bank with SBI, Bank of Baroda, Canara Bank and Union Bank of India, quarter by quarter, using figures read from each bank's own published investor presentations.</p>
      <p class="login-note">Every number on screen traces back to a page in a bank's document and to the person who approved it.</p>
      <div class="login-myra">${avatarSvg(56)}<span>Myra, the benchmarking assistant, will be available once you sign in.</span></div>
    </div>
    <form class="login-form" autocomplete="on">
      <h2>Sign in</h2>
      ${msg ? `<p class="error">${esc(msg)}</p>` : ''}
      <label>User ID<input name="username" autocomplete="username" required autofocus></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <button class="btn btn-primary btn-block" type="submit">Sign in</button>
      <p class="muted small">Roles: analysts collect data, approvers check it, management views it. Access is logged.</p>
    </form>`;
  box.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector('button');
    btn.disabled = true; btn.textContent = 'Signing in…';
    try {
      const r = await api('/api/auth/login', { json: { username: f.get('username'), password: f.get('password') }, noAuthRedirect: true });
      setToken(r.token);
      state.user = r.user;
      await start();
      if (r.default_password) {
        toast('You are using the default password. Please change it from the user menu.', 'warn', 9000);
      }
    } catch (err) {
      showLogin(err.message || 'Sign-in failed');
    }
  });
}

async function start() {
  $('#login').hidden = true;
  $('#app').hidden = false;
  state.meta = await api('/api/meta');
  state.llm = state.meta.llm;
  state.kpis = Object.fromEntries(state.meta.kpis.map(k => [k.code, k]));
  state.banks = Object.fromEntries(state.meta.banks.map(b => [b.code, b]));
  state.bankOrder = state.meta.banks.map(b => b.code);
  state.provisional = can('approve') || can('run') ? prefs.get('provisional', false) : false;
  renderShell();
  myra = mountMyra($('#myra'));
  live.start();
  live.on(onLive);
  await refreshQuarters();
  window.addEventListener('hashchange', route);
  route();
}

// --------------------------------------------------------------------------- shell
function renderShell() {
  const u = state.user;
  $('#topbar').innerHTML = `
    ${state.meta.dashboard && state.meta.dashboard.url ? `<a class="tb-back" href="${esc(state.meta.dashboard.url)}" title="Back to ${esc(state.meta.dashboard.name)}">${icon('arrowLeft', 16)}<span>${esc(state.meta.dashboard.name)}</span></a>` : ''}
    <div class="tb-brand"><span class="brand-mark sm" aria-hidden="true">PNB</span><span class="tb-title">Peer Benchmarking</span></div>
    <div class="tb-controls">
      <label class="tb-q"><span>Quarter</span><select id="qsel" aria-label="Quarter"></select></label>
      ${can('approve') || can('run') ? `<label class="switch" title="Include values that are extracted but not yet approved"><input type="checkbox" id="provsw" ${state.provisional ? 'checked' : ''}><span class="sw-track"></span><span>Include unapproved</span></label>` : ''}
      <button class="run-pill" id="runpill" hidden type="button"></button>
      <span class="llm-pill" id="llmpill"></span>
      <div class="user-menu">
        <button class="user-btn" id="userbtn" type="button" aria-haspopup="true">${icon('user', 16)}<span>${esc(u.full_name || u.username)}</span><small>${esc(u.role_label)}</small></button>
        <div class="menu" id="usermenu" hidden>
          <button type="button" data-act="password">Change password</button>
          <button type="button" data-act="logout">Sign out</button>
        </div>
      </div>
    </div>`;
  $('#rail').innerHTML = Object.entries(VIEWS).map(([k, v]) =>
    `<a href="#/${k}" data-view="${k}" class="rail-item">${icon(v.icon, 18)}<span>${esc(v.title)}</span></a>`).join('') +
    `<div class="rail-foot"><span class="live-dot" id="livedot"></span><span id="livetxt">Connecting live feed…</span></div>`;
  updateLlmPill();
  $('#qsel').addEventListener('change', e => { state.quarterKey = +e.target.value || null; rerender(); });
  $('#provsw')?.addEventListener('change', e => { state.provisional = e.target.checked; prefs.set('provisional', state.provisional); refreshQuarters().then(rerender); });
  $('#userbtn').addEventListener('click', () => { const m = $('#usermenu'); m.hidden = !m.hidden; });
  document.addEventListener('click', e => { if (!e.target.closest('.user-menu')) $('#usermenu') && ($('#usermenu').hidden = true); });
  $('#usermenu').addEventListener('click', async e => {
    const a = e.target.closest('[data-act]')?.dataset.act;
    if (a === 'logout') {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
      setToken(null); live.stop();
      const back = state.meta && state.meta.dashboard && state.meta.dashboard.url;
      if (back) { location.href = back; return; }
      location.hash = ''; location.reload();
    }
    if (a === 'password') passwordDialog();
  });
  $('#runpill').addEventListener('click', () => { location.hash = '#/agents'; });
}

function updateLlmPill() {
  const l = state.llm || {};
  const el = $('#llmpill');
  if (!el) return;
  el.className = 'llm-pill ' + (l.available ? 'on' : 'off');
  el.textContent = l.available ? 'Local LLM ready' : 'Template mode';
  el.title = l.available ? `${l.provider}: ${l.model} (runs on this server, no data leaves it)` : (l.reason || 'No language model connected; templates are used');
  updateMode();
}

export async function refreshQuarters() {
  try {
    const sc = await api('/api/bench/scorecard' + (state.provisional ? '?provisional=true' : ''));
    state.quarters = sc.quarters || [];
    const def = sc.quarter_key;
    if (!state.quarterKey || !state.quarters.some(q => q.key === state.quarterKey)) state.quarterKey = def || null;
    const sel = $('#qsel');
    sel.innerHTML = state.quarters.length ? state.quarters.map(q =>
      `<option value="${q.key}" ${q.key === state.quarterKey ? 'selected' : ''}>${esc(q.label)} (${q.banks} of ${q.of} banks)</option>`).join('')
      : '<option value="">No data yet</option>';
  } catch (e) { console.warn(e); }
}

function route() {
  const name = (location.hash.replace(/^#\/?/, '').split('?')[0]) || params.get('tab') || 'overview';
  const key = VIEWS[name] ? name : 'overview';
  $$('#rail .rail-item').forEach(a => a.classList.toggle('active', a.dataset.view === key));
  current = key;
  rerender();
}

async function rerender() {
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  hideTip(); closeDrawer();
  const v = VIEWS[current];
  const main = $('#view');
  main.innerHTML = '';
  main.focus({ preventScroll: true });
  document.title = `${v.title} | PNB Peer Benchmarking`;
  try {
    cleanup = await v.render(main, { rerender, refreshQuarters, myra });
  } catch (e) {
    console.error(e);
    main.innerHTML = `<div class="page"><p class="error">This page could not load: ${esc(e.message)}</p></div>`;
  }
}

// --------------------------------------------------------------------------- live feed
const activeRuns = new Map();
function onLive(ev) {
  if (ev.type === 'stream') {
    $('#livedot')?.classList.toggle('on', ev.connected);
    const t = $('#livetxt'); if (t) t.textContent = ev.connected ? 'Live feed connected' : 'Live feed reconnecting…';
    return;
  }
  if (ev.type === 'run' && ev.pipeline !== 'chat') {
    if (ev.event === 'start') activeRuns.set(ev.run_id, { pipeline: ev.pipeline, step: 'starting' });
    if (ev.event === 'end') {
      const r = activeRuns.get(ev.run_id);
      activeRuns.delete(ev.run_id);
      if (r) {
        toast(`${r.pipeline === 'collect' ? 'Data collection' : 'Report'} run ${ev.status}`, ev.status === 'succeeded' ? 'good' : 'error');
        if (r.pipeline === 'collect') refreshQuarters();
      }
    }
  }
  if (ev.type === 'span' && ev.event === 'start' && activeRuns.has(ev.run_id)) {
    activeRuns.get(ev.run_id).step = ev.step;
  }
  const pill = $('#runpill');
  if (pill) {
    const r = [...activeRuns.values()][0];
    pill.hidden = !r;
    if (r) pill.innerHTML = `<span class="pulse"></span>${esc(r.pipeline === 'collect' ? 'Collecting' : 'Writing report')}: ${esc(String(r.step).slice(0, 42))}`;
  }
}

// --------------------------------------------------------------------------- tooltips for any [data-tip]
document.addEventListener('mouseover', e => {
  const t = e.target.closest('[data-tip]');
  if (t) tip(t.getAttribute('data-tip'), e.clientX, e.clientY);
});
document.addEventListener('mousemove', e => {
  const t = e.target.closest('[data-tip]');
  if (t) tip(t.getAttribute('data-tip'), e.clientX, e.clientY);
});
document.addEventListener('mouseout', e => { if (e.target.closest('[data-tip]')) hideTip(); });
document.addEventListener('focusin', e => {
  const t = e.target.closest('[data-tip]');
  if (t) { const r = t.getBoundingClientRect(); tip(t.getAttribute('data-tip'), r.right, r.bottom); }
});
document.addEventListener('focusout', e => { if (e.target.closest('[data-tip]')) hideTip(); });

// --------------------------------------------------------------------------- password
function passwordDialog() {
  const body = openDrawer('Change password', `
    <form class="form" id="pwform">
      <label>Current password<input type="password" name="old" required autocomplete="current-password"></label>
      <label>New password<input type="password" name="new" required autocomplete="new-password" minlength="8"></label>
      <p class="muted small">At least 8 characters with an upper-case letter, a lower-case letter, a digit and a symbol.</p>
      <button class="btn btn-primary" type="submit">Change password</button>
    </form>`);
  body.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api('/api/auth/password', { json: { old_password: f.get('old'), new_password: f.get('new') } });
      toast('Password changed', 'good'); closeDrawer();
    } catch (err) { toast(err.message, 'error'); }
  });
}

boot();
