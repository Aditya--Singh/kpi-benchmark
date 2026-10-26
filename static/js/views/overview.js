// Overview: where PNB stands against its four PSB peers in one quarter.
import { api, esc, state, fmtVal, fmtBps, fmtPct, bankName, bankColor, icon, can, emptyState, loading, openDrawer, statusChip } from '../util.js';
import { ladderSvg, rankBars, lineChart } from '../charts.js';
import { showLineage } from '../lineage.js';

export async function renderOverview(root, ctx) {
  root.innerHTML = `<div class="page">${loading('Building the scorecard…')}</div>`;
  const qs = new URLSearchParams();
  if (state.quarterKey) qs.set('quarter_key', state.quarterKey);
  if (state.provisional) qs.set('provisional', 'true');
  const [sc, fs] = await Promise.all([api('/api/bench/scorecard?' + qs), api('/api/bench/facts?' + qs)]);
  if (!sc.quarter) {
    root.innerHTML = `<div class="page">${emptyState('No approved figures yet',
      'Once the agents have read the banks\' investor presentations and an approver has checked the values, PNB\'s position against its peers appears here.',
      can('run') ? '<a class="btn btn-primary" href="#/data">Collect data from the bank websites</a>' : '<p class="muted">Ask an analyst to run data collection.</p>')}</div>`;
    return;
  }
  const kpis = sc.kpis;
  const withHome = kpis.filter(k => k.home && k.home.rank);
  const leads = withHome.filter(k => k.home.rank === 1).length;
  const better = kpis.filter(k => k.favourable === true).length;
  const worse = kpis.filter(k => k.favourable === false).length;
  const compared = kpis.filter(k => k.favourable != null).length;
  const factById = Object.fromEntries((fs.facts || []).map(f => [f.id, f]));
  const posFact = {};
  (fs.facts || []).forEach(f => { if (f.type === 'position') posFact[f.kpi] = f; });

  const cats = state.meta.categories;
  const ladderW = Math.min(340, Math.max(220, Math.floor((root.clientWidth || 1100) * 0.26)));

  root.innerHTML = `<div class="page">
    <header class="page-head">
      <h1>Where PNB stands in ${esc(sc.quarter)}</h1>
      <p class="lede">PNB ranks first on <b>${leads}</b> of ${withHome.length} measures, is better than the peer average on <b>${better}</b> of ${compared} and behind it on <b>${worse}</b>.
      Ratios are ranked on their level; deposits, advances and profits on year-on-year growth.</p>
      <div class="bank-status">${bankStatus(sc)}</div>
      ${sc.provisional ? '<p class="banner warn">Includes values that have not been approved yet. Management views show approved values only.</p>' : ''}
    </header>
    <div class="ov-grid">
      <section class="board" aria-label="KPI positions">
        <div class="board-head" aria-hidden="true">
          <span>Measure</span><span class="num">PNB</span><span class="num">Rank</span><span class="num">Against peer average</span>
          <span class="lad-h"><span>worse</span><span>Position among the five banks</span><span>better</span></span>
        </div>
        ${cats.map(c => {
          const rows = kpis.filter(k => k.category === c);
          if (!rows.length) return '';
          return `<div class="board-cat"><h2>${esc(c)}</h2>${rows.map(k => row(k, ladderW)).join('')}</div>`;
        }).join('')}
        <p class="muted small board-note">Dots: ${state.bankOrder.map(b => `<span class="nowrap">${dot(b)}${esc(bankName(b))}</span>`).join(' ')} <span class="nowrap"><span class="tick"></span>peer average</span>. Click a row for the trend and the source.</p>
      </section>
      <aside class="takeaways" aria-label="Takeaways">
        <h2>What stands out</h2>
        ${takeSection('Ahead of peers', fs.insights?.strengths, posFact, 'good')}
        ${takeSection('Improving faster than peers', fs.insights?.momentum, Object.fromEntries((fs.facts || []).filter(f => f.type === 'movement').map(f => [f.kpi, f])), 'good')}
        ${takeSection('Needs attention', fs.insights?.concerns, posFact, 'bad')}
        ${(fs.insights?.actions || []).length ? `<h3>Suggested focus</h3><ul class="actions">${fs.insights.actions.map(a => `<li><b>${esc(a.kpi_name)}</b>: ${esc(a.action)}</li>`).join('')}</ul>` : ''}
        <div class="take-cta">
          <button class="btn btn-ghost btn-sm" type="button" data-ask="How is PNB doing versus peers this quarter?">${icon('send', 14)} Ask Myra for a summary</button>
          ${can('report') ? '<a class="btn btn-ghost btn-sm" href="#/reports">' + icon('reports', 14) + ' Create a brief</a>' : ''}
        </div>
      </aside>
    </div>
  </div>`;

  root.querySelectorAll('.krow').forEach(r => {
    const open = () => kpiDrawer(sc, kpis.find(k => k.kpi === r.dataset.kpi), ctx);
    r.addEventListener('click', e => { if (!e.target.closest('a,button')) open(); });
    r.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
  });
  root.querySelector('[data-ask]')?.addEventListener('click', e => ctx.myra.ask(e.currentTarget.dataset.ask));
  void factById;
}

function dot(b) { return `<span class="bdot" style="background:${bankColor(b)}"></span>`; }

function bankStatus(sc) {
  const sel = sc.quarter_key;
  return state.bankOrder.map(b => {
    const latest = sc.latest_per_bank?.[b];
    const reported = (sc.banks_reported || []).includes(b);
    const q = state.quarters.find(x => x.label === latest);
    const ahead = q && q.key > sel;
    let txt;
    if (!latest) txt = 'no data yet';
    else if (!reported) txt = `latest ${latest}, awaited for ${sc.quarter}`;
    else if (ahead) txt = `has also reported ${latest}`;
    else txt = `reported`;
    return `<span class="bstat ${reported ? '' : 'awaited'}">${dot(b)}<b>${esc(bankName(b))}</b> ${esc(txt)}</span>`;
  }).join('');
}

function row(k, w) {
  const h = k.home || null;
  const unit = k.unit;
  const isAmt = unit === 'cr';
  const growthBasis = isAmt && k.rank_metric === 'yoy_growth';
  const pts = Object.values(k.banks).map(it => ({
    key: it.bank, label: bankName(it.bank, true), color: bankColor(it.bank), emph: it.bank === 'PNB',
    value: growthBasis ? it.yoy_growth : it.value,
    extra: growthBasis ? `${fmtVal(it.value, 'cr')}, rank ${it.rank ?? '–'}` : `rank ${it.rank ?? '–'} of ${k.n}`,
  }));
  const ref = growthBasis ? k.peer_avg_metric : (isAmt ? null : k.peer_avg);
  const lad = ladderSvg(pts, {
    width: w, direction: isAmt ? 1 : k.direction, ref, unit: growthBasis ? '%' : unit,
    fmt: growthBasis ? (v => fmtPct(v) + ' YoY') : undefined,
    aria: `${k.name}: PNB rank ${h?.rank ?? 'not available'} of ${k.n}`,
  });
  let vs = '<span class="muted">–</span>';
  if (h && k.favourable != null) {
    const good = k.favourable;
    const amount = isAmt ? `${fmtPct(k.gap, 1).replace('%', ' pp')}` : fmtBps(k.gap_bps);
    vs = `<span class="delta ${good ? 'good' : 'bad'}">${icon(good ? 'arrowUp' : 'arrowDown', 13)}${esc(amount)}<small>${good ? 'better' : 'worse'}</small></span>`;
  } else if (isAmt && !growthBasis) {
    vs = `<span class="muted small" title="${esc(k.rank_basis)}">ranked by size</span>`;
  }
  const rank = h && h.rank ? `<span class="rank ${h.rank === 1 ? 'first' : h.rank === k.n ? 'last' : ''}">${h.rank}<small> of ${k.n}</small></span>` : '<span class="muted">–</span>';
  const sub = isAmt && h ? (h.yoy_growth != null ? `${fmtPct(h.yoy_growth)} YoY` : (h.qoq_growth != null ? `${fmtPct(h.qoq_growth)} QoQ` : '')) :
    (h && h.yoy_bps != null ? `${fmtBps(h.yoy_bps)} YoY` : '');
  const prov = h && h.status !== 'approved' && h.status !== 'overridden' ? '<span class="prov" title="Not yet approved">provisional</span>' : '';
  return `<div class="krow" tabindex="0" role="button" data-kpi="${esc(k.kpi)}" aria-label="${esc(k.name)} details">
    <span class="kname"><b>${esc(k.name)}</b><small>${esc(state.kpis[k.kpi]?.basis || '')}</small></span>
    <span class="num kval">${h ? esc(fmtVal(h.value, unit)) : '<span class="muted">not reported</span>'}${prov}<small>${esc(sub)}</small></span>
    <span class="num">${rank}</span>
    <span class="num">${vs}</span>
    <span class="lad">${lad}</span>
  </div>`;
}

function takeSection(title, codes, factMap, tone) {
  if (!codes || !codes.length) return '';
  return `<h3 class="take-${tone}">${esc(title)}</h3><ul class="takes">${codes.map(c => {
    const f = factMap[c];
    return `<li>${f ? esc(f.text.replace(/^PNB /, '')) : esc(state.kpis[c]?.name || c)}</li>`;
  }).join('')}</ul>`;
}

async function kpiDrawer(sc, k, ctx) {
  const meta = state.kpis[k.kpi];
  const body = openDrawer(k.name, `
    <p class="muted">${esc(meta.definition)} ${k.direction > 0 ? 'Higher is better.' : 'Lower is better.'} Basis: ${esc(meta.basis)}.</p>
    <h3>${esc(sc.quarter)} ranking</h3><div class="chartbox" id="kd-bars"></div>
    <h3>Last 12 quarters</h3><div class="legend" id="kd-legend"></div><div class="chartbox" id="kd-trend"></div>
    <div class="row gap">
      ${k.home ? `<button class="btn btn-primary btn-sm" type="button" id="kd-trace">${icon('link', 14)} Trace PNB's number to its source</button>` : ''}
      <a class="btn btn-ghost btn-sm" href="#/trends?kpi=${esc(k.kpi)}">Open in Trends</a>
      <button class="btn btn-ghost btn-sm" type="button" id="kd-ask">${icon('send', 14)} Ask Myra</button>
    </div>
    <h3>Values by bank</h3>
    <table class="tbl"><thead><tr><th>Bank</th><th class="num">Value</th><th class="num">Rank</th><th>Status</th><th></th></tr></thead>
    <tbody>${state.bankOrder.map(b => {
      const it = k.banks[b];
      if (!it) return `<tr><td>${dot(b)}${esc(bankName(b))}</td><td colspan="4" class="muted">not reported for ${esc(sc.quarter)}</td></tr>`;
      return `<tr><td>${dot(b)}${esc(bankName(b))}</td><td class="num">${esc(fmtVal(it.value, k.unit, { compact: false }))}</td><td class="num">${it.rank ?? '–'}</td><td>${statusChip(it.status)}</td>
        <td><button class="link-btn" type="button" data-trace="${esc(b)}">source</button></td></tr>`;
    }).join('')}</tbody></table>`, { wide: true });
  const order = Object.values(k.banks).sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  rankBars(body.querySelector('#kd-bars'), {
    unit: k.unit, ref: k.peer_avg != null ? { value: k.peer_avg, label: 'Peer avg ' + fmtVal(k.peer_avg, k.unit) } : null,
    rows: order.map(it => ({ key: it.bank, label: bankName(it.bank), value: it.value, color: bankColor(it.bank), emph: it.bank === 'PNB' })),
  });
  body.querySelector('#kd-trace')?.addEventListener('click', () => showLineage('PNB', k.kpi, sc.quarter_key));
  body.querySelectorAll('[data-trace]').forEach(b => b.addEventListener('click', () => showLineage(b.dataset.trace, k.kpi, sc.quarter_key)));
  body.querySelector('#kd-ask').addEventListener('click', () => ctx.myra.ask(`Compare ${meta.short} across all five banks`));
  const tr = await api(`/api/bench/trend?kpi=${k.kpi}&n=12${state.provisional ? '&provisional=true' : ''}`);
  const series = tr.series.map(s => ({ key: s.bank, name: s.bank === 'PEER_AVG' ? 'Peer average' : bankName(s.bank), color: s.color, values: s.values, statuses: s.statuses, dashed: s.bank === 'PEER_AVG', emph: s.bank === 'PNB' }));
  body.querySelector('#kd-legend').innerHTML = series.map(s => `<span class="lg">${s.dashed ? '<span class="lg-dash"></span>' : `<span class="bdot" style="background:${s.color}"></span>`}${esc(s.name)}</span>`).join('');
  lineChart(body.querySelector('#kd-trend'), { quarters: tr.quarters, series, unit: k.unit, direction: k.unit === 'cr' ? 1 : k.direction, height: 240, title: k.name });
}
