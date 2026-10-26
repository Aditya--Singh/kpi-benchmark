// Trends: up to 5 years (20 quarters) of any KPI for PNB and peers.
import { api, esc, state, fmtVal, fmtBps, fmtPct, bankName, prefs, loading, debounce } from '../util.js';
import { lineChart } from '../charts.js';
import { showLineage } from '../lineage.js';

export async function renderTrends(root) {
  const hq = new URLSearchParams(location.hash.split('?')[1] || '');
  const sel = {
    kpi: hq.get('kpi') || prefs.get('trend.kpi', 'GNPA'),
    n: prefs.get('trend.n', 20),
    banks: new Set(prefs.get('trend.banks', state.bankOrder)),
    peer: prefs.get('trend.peer', true),
  };
  if (!state.kpis[sel.kpi]?.visible) sel.kpi = 'GNPA';
  const visible = state.meta.kpis.filter(k => k.visible);
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Trends</h1>
      <p class="lede">Quarter-by-quarter history for up to five years, read from each bank's own presentations. Hollow points are not yet approved.</p></header>
    <div class="filters" role="group" aria-label="Trend filters">
      <label>Measure<select id="t-kpi">${state.meta.categories.map(c => `<optgroup label="${esc(c)}">${visible.filter(k => k.category === c).map(k => `<option value="${k.code}" ${k.code === sel.kpi ? 'selected' : ''}>${esc(k.name)}</option>`).join('')}</optgroup>`).join('')}</select></label>
      <div class="seg" role="radiogroup" aria-label="Period">${[[8, '2 years'], [12, '3 years'], [20, '5 years']].map(([n, l]) => `<button type="button" role="radio" aria-checked="${sel.n === n}" data-n="${n}">${l}</button>`).join('')}</div>
      <div class="toggles" aria-label="Banks">${state.bankOrder.map(b => `<button type="button" class="tgl" aria-pressed="${sel.banks.has(b)}" data-bank="${b}"><span class="bdot" style="background:${state.banks[b].color}"></span>${esc(bankName(b))}</button>`).join('')}
        <button type="button" class="tgl" aria-pressed="${sel.peer}" data-peer="1"><span class="lg-dash"></span>Peer average</button></div>
    </div>
    <section class="card"><div class="card-head"><h2 id="t-title"></h2><span class="muted small" id="t-basis"></span></div>
      <div class="chartbox" id="t-chart">${loading()}</div></section>
    <section class="card"><div class="card-head"><h2>Change over the period</h2></div><div id="t-stats"></div></section>
  </div>`;

  const draw = async () => {
    prefs.set('trend.kpi', sel.kpi); prefs.set('trend.n', sel.n); prefs.set('trend.banks', [...sel.banks]); prefs.set('trend.peer', sel.peer);
    const k = state.kpis[sel.kpi];
    root.querySelector('#t-title').textContent = k.name;
    root.querySelector('#t-basis').textContent = `${k.basis}. ${k.direction > 0 ? 'Higher' : 'Lower'} is better.`;
    const tr = await api(`/api/bench/trend?kpi=${sel.kpi}&n=${sel.n}${state.provisional ? '&provisional=true' : ''}`);
    const series = tr.series
      .filter(s => s.bank === 'PEER_AVG' ? sel.peer : sel.banks.has(s.bank))
      .map(s => ({ key: s.bank, name: s.bank === 'PEER_AVG' ? 'Peer average' : bankName(s.bank), color: s.color, values: s.values, statuses: s.statuses, dashed: s.bank === 'PEER_AVG', emph: s.bank === 'PNB' }));
    lineChart(root.querySelector('#t-chart'), { quarters: tr.quarters, series, unit: k.unit, direction: k.unit === 'cr' ? 1 : k.direction, height: 340, title: k.name });
    const rows = Object.entries(tr.stats || {}).filter(([b]) => b === 'PEER_AVG' ? sel.peer : sel.banks.has(b));
    root.querySelector('#t-stats').innerHTML = rows.length ? `<table class="tbl">
      <thead><tr><th>Bank</th><th>From</th><th class="num">Value</th><th>To</th><th class="num">Value</th><th class="num">${k.unit === 'cr' ? 'Annual growth (CAGR)' : 'Change'}</th><th class="num">Quarters with data</th><th></th></tr></thead>
      <tbody>${rows.map(([b, st]) => {
        const s = tr.series.find(x => x.bank === b);
        const pts = s ? s.values.filter(v => v != null).length : 0;
        const ch = st.cagr != null ? fmtPct(st.cagr) : fmtBps(st.change_bps);
        const good = st.cagr != null ? st.cagr > 0 : (st.change_bps || 0) * k.direction > 0;
        const qk = state.quarters.find(q => q.label === st.last_q)?.key;
        return `<tr><td>${b === 'PEER_AVG' ? '<span class="lg-dash"></span>' : `<span class="bdot" style="background:${state.banks[b]?.color}"></span>`}${esc(b === 'PEER_AVG' ? 'Peer average' : bankName(b))}</td>
          <td>${esc(st.first_q)}</td><td class="num">${esc(fmtVal(st.first, k.unit))}</td><td>${esc(st.last_q)}</td><td class="num">${esc(fmtVal(st.last, k.unit))}</td>
          <td class="num"><span class="delta ${good ? 'good' : 'bad'}">${esc(ch)}</span></td><td class="num">${pts}</td>
          <td>${b !== 'PEER_AVG' && qk ? `<button class="link-btn" type="button" data-lin="${esc(b)}|${qk}">source</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table>` : '<p class="muted pad">Select at least one bank with two or more quarters of approved data.</p>';
    root.querySelectorAll('[data-lin]').forEach(btn => btn.addEventListener('click', () => {
      const [b, qk] = btn.dataset.lin.split('|'); showLineage(b, sel.kpi, +qk);
    }));
  };

  root.querySelector('#t-kpi').addEventListener('change', e => { sel.kpi = e.target.value; draw(); });
  root.querySelectorAll('[data-n]').forEach(b => b.addEventListener('click', () => {
    sel.n = +b.dataset.n;
    root.querySelectorAll('[data-n]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    draw();
  }));
  root.querySelectorAll('[data-bank]').forEach(b => b.addEventListener('click', () => {
    const code = b.dataset.bank;
    sel.banks.has(code) ? sel.banks.delete(code) : sel.banks.add(code);
    b.setAttribute('aria-pressed', String(sel.banks.has(code)));
    draw();
  }));
  root.querySelector('[data-peer]').addEventListener('click', e => {
    sel.peer = !sel.peer; e.currentTarget.setAttribute('aria-pressed', String(sel.peer)); draw();
  });
  const onResize = debounce(() => draw(), 250);
  window.addEventListener('resize', onResize);
  await draw();
  return () => window.removeEventListener('resize', onResize);
}
