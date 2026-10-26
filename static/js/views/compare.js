// Peer comparison: one KPI ranked across banks, plus the full bank-by-KPI matrix.
import { api, esc, state, fmtVal, fmtPct, bankName, bankColor, prefs, loading, icon, withAuth, statusChip } from '../util.js';
import { rankBars } from '../charts.js';
import { showLineage } from '../lineage.js';

export async function renderCompare(root) {
  let kpi = prefs.get('compare.kpi', 'NIM');
  const visible = state.meta.kpis.filter(k => k.visible);
  if (!state.kpis[kpi]?.visible) kpi = 'NIM';
  const qs = () => {
    const p = new URLSearchParams();
    if (state.quarterKey) p.set('quarter_key', state.quarterKey);
    if (state.provisional) p.set('provisional', 'true');
    return p.toString();
  };
  root.innerHTML = `<div class="page">
    <header class="page-head"><h1>Peer comparison</h1>
      <p class="lede">Pick a measure to see all five banks side by side for the selected quarter, or scan every measure at once in the table below.</p></header>
    <div class="grid-2">
      <section class="card">
        <div class="card-head"><label class="inline">Measure <select id="c-kpi">${state.meta.categories.map(c => `<optgroup label="${esc(c)}">${visible.filter(k => k.category === c).map(k => `<option value="${k.code}" ${k.code === kpi ? 'selected' : ''}>${esc(k.name)}</option>`).join('')}</optgroup>`).join('')}</select></label></div>
        <div class="chartbox" id="c-bars">${loading()}</div>
        <p class="muted small" id="c-note"></p>
      </section>
      <section class="card">
        <div class="card-head"><h2>How the ranking is decided</h2></div>
        <ul class="plain explain">
          <li><b>Ratios</b> (NIM, GNPA, CRAR and others) are ranked on their level, with the KPI's own direction: lower is better for NPAs, cost-to-income, slippage and credit cost.</li>
          <li><b>Amounts</b> (business, deposits, advances, NII, profits) are ranked on year-on-year growth, because size alone always favours the largest bank. Until year-ago figures are collected they are ranked by size and marked so.</li>
          <li><b>Peer average</b> is the simple average of SBI, Bank of Baroda, Canara Bank and Union Bank; PNB is excluded so the gap is a true comparison.</li>
          <li>Different banks name the same measure differently (for example “Net NPA %”, “NNPA ratio”). The extraction agent maps them to one definition using a reviewed synonym dictionary, shown under Governance.</li>
        </ul>
      </section>
    </div>
    <section class="card">
      <div class="card-head"><h2>All measures, <span id="c-q"></span></h2>
        <a class="btn btn-ghost btn-sm" id="c-xlsx" href="#">${icon('download', 14)} Download Excel</a></div>
      <div class="table-wrap" id="c-matrix">${loading()}</div>
      <p class="muted small">Cell shading shows rank: green for first, red for last. Click any value to see where it came from.</p>
    </section>
  </div>`;

  const drawBars = async () => {
    prefs.set('compare.kpi', kpi);
    const k = state.kpis[kpi];
    const s = await api(`/api/bench/compare?kpi=${kpi}&${qs()}`);
    if (!s.quarter) { root.querySelector('#c-bars').innerHTML = '<p class="muted pad">No data yet.</p>'; return; }
    const growth = k.unit === 'cr' && s.rank_metric === 'yoy_growth';
    const order = Object.values(s.banks).sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
    rankBars(root.querySelector('#c-bars'), {
      unit: growth ? '%' : k.unit, title: k.name,
      valueFmt: growth ? (v => fmtPct(v) + ' YoY') : undefined,
      ref: growth ? (s.peer_avg_metric != null ? { value: s.peer_avg_metric, label: 'Peer avg ' + fmtPct(s.peer_avg_metric) } : null)
        : (s.peer_avg != null ? { value: s.peer_avg, label: 'Peer avg ' + fmtVal(s.peer_avg, k.unit) } : null),
      rows: order.map(it => ({ key: it.bank, label: bankName(it.bank), value: growth ? it.yoy_growth : it.value, color: bankColor(it.bank), emph: it.bank === 'PNB', sub: growth ? fmtVal(it.value, 'cr') : null })),
    });
    const missing = state.bankOrder.filter(b => !s.banks[b]);
    root.querySelector('#c-note').textContent = `${s.quarter}, ranked on ${s.rank_basis}. ${k.direction > 0 || k.unit === 'cr' ? 'Higher' : 'Lower'} is better.` +
      (missing.length ? ` Not reported: ${missing.map(b => bankName(b)).join(', ')}.` : '');
  };

  const drawMatrix = async () => {
    const hm = await api('/api/bench/heatmap?' + qs());
    root.querySelector('#c-q').textContent = hm.quarter || '';
    root.querySelector('#c-xlsx').href = withAuth('/api/bench/export.xlsx?' + qs());
    if (!hm.quarter) { root.querySelector('#c-matrix').innerHTML = '<p class="muted pad">No data yet.</p>'; return; }
    root.querySelector('#c-matrix').innerHTML = `<table class="tbl matrix">
      <thead><tr><th>Measure</th>${hm.banks.map(b => `<th class="num ${b === 'PNB' ? 'home' : ''}"><span class="bdot" style="background:${bankColor(b)}"></span>${esc(bankName(b))}</th>`).join('')}<th>Ranked on</th></tr></thead>
      <tbody>${hm.rows.map(r => `<tr><th scope="row">${esc(state.kpis[r.kpi].name)}</th>${hm.banks.map(b => {
        const c = r.cells[b];
        if (!c) return '<td class="num muted">–</td>';
        const cls = c.rank === 1 ? 'r-best' : c.rank === r.n && r.n > 1 ? 'r-worst' : c.rank === 2 ? 'r-good' : c.rank === r.n - 1 && r.n > 3 ? 'r-weak' : '';
        const prov = c.status !== 'approved' && c.status !== 'overridden';
        return `<td class="num ${cls} ${b === 'PNB' ? 'home' : ''}"><button class="cell-btn" type="button" data-lin="${b}|${r.kpi}">${esc(fmtVal(c.value, r.unit))}${prov ? '*' : ''}</button><small>${c.rank ? '#' + c.rank : ''}${r.unit === 'cr' && c.metric != null ? '&nbsp;&nbsp;' + esc(fmtPct(c.metric)) + ' YoY' : ''}</small></td>`;
      }).join('')}<td class="small muted">${esc(r.rank_basis)}</td></tr>`).join('')}</tbody></table>
      ${hm.rows.some(r => Object.values(r.cells).some(c => c.status !== 'approved' && c.status !== 'overridden')) ? '<p class="muted small">* not yet approved</p>' : ''}`;
    root.querySelectorAll('[data-lin]').forEach(btn => btn.addEventListener('click', () => {
      const [b, k] = btn.dataset.lin.split('|');
      const qk = state.quarters.find(q => q.label === hm.quarter)?.key || state.quarterKey;
      showLineage(b, k, qk);
    }));
  };

  root.querySelector('#c-kpi').addEventListener('change', e => { kpi = e.target.value; drawBars(); });
  await Promise.all([drawBars(), drawMatrix()]);
  void statusChip;
}
