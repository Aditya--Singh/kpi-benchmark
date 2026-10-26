// Hand-drawn SVG charts (no CDN, works on an isolated bank network).
// Specs: 2px lines, >=8px markers with a 2px surface ring, hairline solid grid, values in text ink (never series colour).
import { esc, fmtAxis, fmtVal, tip, hideTip, bankName, state } from './util.js';

const INK = '#1B2130', INK2 = '#465063', MUTED = '#768092', GRID = '#E7EAEF', SURFACE = '#FFFFFF';

function niceTicks(min, max, count = 5) {
  if (min === max) { const d = Math.abs(min) * 0.1 || 1; min -= d; max += d; }
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks };
}

// ----------------------------------------------------------------------------- line chart with crosshair
export function lineChart(el, cfg) {
  const W = Math.max(320, el.clientWidth || 640), H = cfg.height || 300;
  const series = cfg.series.filter(s => s.values.some(v => v != null));
  if (!series.length) { el.innerHTML = '<p class="muted pad">No approved values for this selection yet.</p>'; return; }
  // trim leading quarters that are empty for every series
  let start = 0;
  while (start < cfg.quarters.length - 1 && series.every(s => s.values[start] == null)) start++;
  const quarters = cfg.quarters.slice(start);
  const ss = series.map(s => ({ ...s, values: s.values.slice(start), statuses: (s.statuses || []).slice(start) }));
  const n = quarters.length;
  const m = { t: 14, r: cfg.endLabels === false ? 16 : 84, b: 30, l: 56 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const all = ss.flatMap(s => s.values.filter(v => v != null));
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
  const nt = niceTicks(lo - pad, hi + pad, 5);
  lo = nt.lo; hi = nt.hi;
  const x = i => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = v => m.t + ih - ((v - lo) / (hi - lo)) * ih;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 62))));

  let g = '';
  for (const t of nt.ticks) {
    g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}" stroke="${GRID}" stroke-width="1"/>`;
    g += `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end" class="ax">${esc(fmtAxis(t, cfg.unit))}</text>`;
  }
  quarters.forEach((q, i) => {
    if (i % every === 0 || i === n - 1) {
      if (i !== n - 1 && n - 1 - i < every * 0.6) return;
      g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" class="ax">${esc(q)}</text>`;
    }
  });
  // series: draw emphasised series last so it sits on top
  const order = [...ss].sort((a, b) => (a.emph ? 1 : 0) - (b.emph ? 1 : 0));
  let paths = '', dots = '';
  for (const s of order) {
    let d = '', seg = [];
    const flush = () => {
      if (seg.length === 1) {
        const [i, v] = seg[0];
        dots += `<circle cx="${x(i)}" cy="${y(v)}" r="${s.emph ? 4.5 : 3.5}" fill="${s.color}" stroke="${SURFACE}" stroke-width="2"/>`;
      } else if (seg.length > 1) d += 'M' + seg.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('L');
      seg = [];
    };
    s.values.forEach((v, i) => { if (v == null) flush(); else seg.push([i, v]); });
    flush();
    if (d) paths += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.emph ? 2.5 : 2}" stroke-linejoin="round" stroke-linecap="round" ${s.dashed ? 'stroke-dasharray="5 4"' : ''} opacity="${s.dim ? 0.35 : 1}"/>`;
    // hollow markers for provisional (unapproved) points
    s.values.forEach((v, i) => {
      if (v != null && s.statuses[i] && s.statuses[i] !== 'approved' && s.statuses[i] !== 'overridden') {
        dots += `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${SURFACE}" stroke="${s.color}" stroke-width="2"><title>Provisional (not yet approved)</title></circle>`;
      }
    });
  }
  // end labels: emphasised series + peer average, nudged apart if they collide
  let labels = '';
  if (cfg.endLabels !== false) {
    const ends = [];
    for (const s of ss) {
      if (!(s.emph || s.key === 'PEER_AVG')) continue;
      let li = -1; s.values.forEach((v, i) => { if (v != null) li = i; });
      if (li < 0) continue;
      ends.push({ s, i: li, v: s.values[li], yy: y(s.values[li]) });
    }
    ends.sort((a, b) => a.yy - b.yy);
    for (let k = 1; k < ends.length; k++) if (ends[k].yy - ends[k - 1].yy < 14) ends[k].yy = ends[k - 1].yy + 14;
    for (const e of ends) {
      dots += `<circle cx="${x(e.i)}" cy="${y(e.v)}" r="${e.s.emph ? 5 : 4}" fill="${e.s.color}" stroke="${SURFACE}" stroke-width="2"/>`;
      const lx = x(e.i) + 9;
      if (Math.abs(e.yy - y(e.v)) > 2) labels += `<line x1="${x(e.i) + 5}" y1="${y(e.v)}" x2="${lx - 2}" y2="${e.yy}" stroke="${MUTED}" stroke-width="1"/>`;
      labels += `<text x="${lx}" y="${e.yy + 4}" class="endlbl ${e.s.emph ? 'strong' : ''}">${esc(e.s.key === 'PEER_AVG' ? 'Peers' : e.s.name)} ${esc(fmtAxis(e.v, cfg.unit))}</text>`;
    }
  }
  const aria = `${cfg.title || 'Trend'}: ${ss.map(s => s.name).join(', ')} over ${quarters[0]} to ${quarters[n - 1]}`;
  el.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(aria)}">
    ${g}${paths}${dots}${labels}
    <line class="xhair" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" stroke="${INK2}" stroke-width="1" visibility="hidden"/>
    <g class="xhair-dots"></g>
    <rect class="hit" x="${m.l - 10}" y="${m.t}" width="${iw + 20}" height="${ih}" fill="transparent"/></svg>`;
  const svg = el.querySelector('svg'), hair = svg.querySelector('.xhair'), hd = svg.querySelector('.xhair-dots');
  const hit = svg.querySelector('.hit');
  const show = ev => {
    const r = svg.getBoundingClientRect();
    const px = ev.clientX - r.left;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    hair.setAttribute('x1', x(i)); hair.setAttribute('x2', x(i)); hair.setAttribute('visibility', 'visible');
    const rows = ss.map(s => ({ s, v: s.values[i], st: s.statuses[i] })).filter(r2 => r2.v != null);
    hd.innerHTML = rows.map(r2 => `<circle cx="${x(i)}" cy="${y(r2.v)}" r="5" fill="${r2.s.color}" stroke="${SURFACE}" stroke-width="2"/>`).join('');
    const dir = cfg.direction || 1;
    rows.sort((a, b) => dir * (b.v - a.v));
    tip(`<div class="tt-h">${esc(quarters[i])}</div>` + (rows.length ? rows.map(r2 =>
      `<div class="tt-r"><span class="sw" style="background:${r2.s.color}"></span><span class="tt-n">${esc(r2.s.name)}</span><b>${esc(fmtVal(r2.v, cfg.unit))}</b>${r2.st && r2.st !== 'approved' && r2.st !== 'overridden' ? '<i class="tt-p">provisional</i>' : ''}</div>`).join('')
      : '<div class="muted">No values</div>'), ev.clientX, ev.clientY);
  };
  hit.addEventListener('mousemove', show);
  hit.addEventListener('mouseleave', () => { hair.setAttribute('visibility', 'hidden'); hd.innerHTML = ''; hideTip(); });
}

// ----------------------------------------------------------------------------- ranked horizontal bars
export function rankBars(el, cfg) {
  // cfg: {rows:[{key,label,value,color,emph,sub}], unit, ref:{value,label}, valueFmt}
  const rows = cfg.rows.filter(r => r.value != null);
  if (!rows.length) { el.innerHTML = '<p class="muted pad">No values for this quarter.</p>'; return; }
  const W = Math.max(300, el.clientWidth || 560);
  const rowH = 34, barH = 18, m = { t: cfg.ref ? 22 : 6, r: 96, b: 8, l: 78 };
  const H = m.t + rows.length * rowH + m.b;
  const vals = rows.map(r => r.value).concat(cfg.ref ? [cfg.ref.value] : []);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const iw = W - m.l - m.r;
  const x = v => m.l + ((v - lo) / ((hi - lo) || 1)) * iw;
  const f = cfg.valueFmt || (v => fmtVal(v, cfg.unit));
  let s = `<line x1="${x(0)}" x2="${x(0)}" y1="${m.t - 4}" y2="${H - m.b}" stroke="${GRID}" stroke-width="1"/>`;
  rows.forEach((r, k) => {
    const cy = m.t + k * rowH + rowH / 2;
    const x0 = x(0), x1 = x(r.value);
    const left = Math.min(x0, x1), w = Math.max(2, Math.abs(x1 - x0));
    const rad = Math.min(4, w / 2);
    // rounded data-end only (square at the baseline)
    const pos = r.value >= 0;
    const y0 = cy - barH / 2;
    const d = pos
      ? `M${left},${y0} h${w - rad} a${rad},${rad} 0 0 1 ${rad},${rad} v${barH - 2 * rad} a${rad},${rad} 0 0 1 -${rad},${rad} h-${w - rad} z`
      : `M${left + w},${y0} h-${w - rad} a${rad},${rad} 0 0 0 -${rad},${rad} v${barH - 2 * rad} a${rad},${rad} 0 0 0 ${rad},${rad} h${w - rad} z`;
    s += `<text x="${m.l - 10}" y="${cy + 4}" text-anchor="end" class="barlbl ${r.emph ? 'strong' : ''}">${esc(r.label)}</text>`;
    s += `<path d="${d}" fill="${r.color}" opacity="${r.emph || !cfg.emphOnly ? 1 : 0.55}" data-tip="${esc(`<div class='tt-h'>${esc(r.label)}</div><div>${esc(f(r.value))}${r.sub ? '<br><span class=muted>' + esc(r.sub) + '</span>' : ''}</div>`)}"/>`;
    const tx = pos ? x1 + 8 : x1 - 8;
    s += `<text x="${tx}" y="${cy + 4}" text-anchor="${pos ? 'start' : 'end'}" class="barval ${r.emph ? 'strong' : ''}">${esc(f(r.value))}</text>`;
  });
  if (cfg.ref && cfg.ref.value != null) {
    const rx = x(cfg.ref.value);
    s += `<line x1="${rx}" x2="${rx}" y1="${m.t - 6}" y2="${H - m.b}" stroke="${INK2}" stroke-width="1.5"/>`;
    s += `<text x="${rx}" y="${m.t - 10}" text-anchor="middle" class="reflbl">${esc(cfg.ref.label)}</text>`;
  }
  el.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.title || 'Ranking')}">${s}</svg>`;
}

// ----------------------------------------------------------------------------- position ladder (overview)
// Places each bank on a line where RIGHT is always better, so a GM reads every KPI the same way.
export function ladderSvg(points, opts) {
  // points: [{key, value, color, emph, label}], opts: {direction, ref, width, unit, fmt}
  const W = opts.width || 300, H = 36, padX = 12, cy = 18;
  const pts = points.filter(p => p.value != null);
  if (pts.length < 2) return `<svg width="${W}" height="${H}" class="ladder" aria-hidden="true"><line x1="${padX}" x2="${W - padX}" y1="${cy}" y2="${cy}" stroke="${GRID}" stroke-width="2"/><text x="${W / 2}" y="${cy + 4}" text-anchor="middle" class="ax">not enough banks</text></svg>`;
  const vals = pts.map(p => p.value).concat(opts.ref != null ? [opts.ref] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= 1; hi += 1; }
  const dir = opts.direction || 1;
  const x = v => {
    const t = (v - lo) / (hi - lo);
    return padX + (dir > 0 ? t : 1 - t) * (W - 2 * padX);
  };
  const f = opts.fmt || (v => fmtVal(v, opts.unit));
  let s = `<line x1="${padX}" x2="${W - padX}" y1="${cy}" y2="${cy}" stroke="${GRID}" stroke-width="2" stroke-linecap="round"/>`;
  if (opts.ref != null) {
    const rx = x(opts.ref);
    s += `<line x1="${rx}" x2="${rx}" y1="${cy - 9}" y2="${cy + 9}" stroke="${MUTED}" stroke-width="1.5" data-tip="${esc(`<div class='tt-h'>Peer average</div>${esc(f(opts.ref))}`)}"/>`;
  }
  // de-overlap: alternate vertical offsets for dots closer than 11px
  const placed = pts.map(p => ({ ...p, px: x(p.value), dy: 0 })).sort((a, b) => a.px - b.px);
  for (let i = 1; i < placed.length; i++) {
    if (Math.abs(placed[i].px - placed[i - 1].px) < 11) placed[i].dy = placed[i - 1].dy === 0 ? (i % 2 ? -7 : 7) : 0;
  }
  const order = placed.sort((a, b) => (a.emph ? 1 : 0) - (b.emph ? 1 : 0));
  for (const p of order) {
    const r = p.emph ? 7 : 5;
    const t = `<div class='tt-h'>${esc(p.label)}</div>${esc(f(p.value))}${p.extra ? '<br><span class=muted>' + esc(p.extra) + '</span>' : ''}`;
    if (p.emph) s += `<circle cx="${p.px}" cy="${cy + p.dy}" r="${r + 3.5}" fill="none" stroke="#F2B600" stroke-width="2"/>`;
    s += `<circle cx="${p.px}" cy="${cy + p.dy}" r="${r}" fill="${p.color}" stroke="${SURFACE}" stroke-width="2" data-tip="${esc(t)}"/>`;
  }
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" class="ladder" role="img" aria-label="${esc(opts.aria || '')}">${s}</svg>`;
}

// ----------------------------------------------------------------------------- span timeline (Gantt)
export function gantt(el, spans, agentTitles = {}) {
  if (!spans.length) { el.innerHTML = '<p class="muted pad">No steps recorded for this run.</p>'; return; }
  const t = s => Date.parse(s);
  const t0 = Math.min(...spans.map(s => t(s.started_at)));
  const t1 = Math.max(...spans.map(s => s.ended_at ? t(s.ended_at) : Date.now()));
  const total = Math.max(1, t1 - t0);
  const byId = Object.fromEntries(spans.map(s => [s.id, s]));
  const depth = s => { let d = 0, p = s.parent_id; while (p && byId[p] && d < 4) { d++; p = byId[p].parent_id; } return d; };
  const W = Math.max(520, el.clientWidth || 800), labelW = Math.min(330, W * 0.4), rowH = 24, m = { t: 22, r: 70 };
  const H = m.t + spans.length * rowH + 6;
  const iw = W - labelW - m.r;
  const x = ms => labelW + (ms / total) * iw;
  let s = '';
  for (let k = 0; k <= 4; k++) {
    const gx = labelW + (k / 4) * iw;
    s += `<line x1="${gx}" x2="${gx}" y1="${m.t - 6}" y2="${H}" stroke="${GRID}"/>`;
    s += `<text x="${gx}" y="12" text-anchor="middle" class="ax">${esc(fmtMsShort((k / 4) * total))}</text>`;
  }
  spans.forEach((sp, i) => {
    const d = depth(sp);
    const y0 = m.t + i * rowH;
    const st = t(sp.started_at) - t0;
    const du = sp.duration_ms ?? ((sp.ended_at ? t(sp.ended_at) : Date.now()) - t(sp.started_at));
    const color = sp.status === 'error' ? '#B42318' : d === 0 ? '#A6192E' : '#D98A96';
    const name = (agentTitles[sp.agent] || sp.agent);
    const label = d === 0 ? name : sp.step;
    s += `<text x="${8 + d * 14}" y="${y0 + 16}" class="gl ${d === 0 ? 'strong' : ''}">${esc(trunc(label, Math.floor((labelW - 16 - d * 14) / 6.6)))}</text>`;
    const bx = x(st), bw = Math.max(3, x(st + du) - bx);
    const tipHtml = `<div class='tt-h'>${esc(name)}</div><div>${esc(sp.step)}</div><div class=muted>${esc(fmtMsShort(du))} · ${esc(sp.status)}</div>${sp.output ? '<div class=tt-code>' + esc(trunc(sp.output, 220)) + '</div>' : ''}`;
    s += `<rect x="${bx}" y="${y0 + 5}" width="${bw}" height="14" rx="3" fill="${color}" data-span="${esc(sp.id)}" data-tip="${esc(tipHtml)}" class="gbar"/>`;
    s += `<text x="${bx + bw + 6}" y="${y0 + 16}" class="ax">${esc(fmtMsShort(du))}</text>`;
  });
  el.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Run timeline">${s}</svg>`;
}
function fmtMsShort(ms) { return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + ' s'; }
function trunc(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

// ----------------------------------------------------------------------------- small chart for chat replies
export function chatChart(el, chart, unit) {
  if (!chart) return;
  if (chart.type === 'bar') {
    rankBars(el, {
      unit,
      rows: chart.labels.map((l, i) => {
        const code = Object.keys(state.banks).find(c => (state.banks[c].short === l || c === l)) || l;
        return { key: code, label: l, value: chart.values[i], color: state.banks[code]?.color || '#8A8F98', emph: code === chart.highlight };
      }),
    });
  } else if (chart.type === 'line') {
    lineChart(el, {
      quarters: chart.quarters, unit, height: 200,
      series: chart.series.map(s => ({ key: s.name === 'Peer avg' ? 'PEER_AVG' : s.name, name: s.name, color: s.color, values: s.values, dashed: s.dashed, emph: s.name === 'PNB' })),
    });
  }
}
export { bankName };
