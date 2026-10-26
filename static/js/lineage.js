// "Where did this number come from?" - lineage drawer and source-page viewer, used everywhere.
import { api, esc, openDrawer, fmtVal, fmtTime, flagChip, statusChip, METHOD_TEXT, bankName, state, loading, icon, withAuth } from './util.js';

export async function showLineage(bank, kpi, qk) {
  const k = state.kpis[kpi] || { name: kpi, unit: '%' };
  const body = openDrawer(`${bankName(bank)} ${k.name}`, loading('Tracing the number back to its source…'), { wide: true });
  try {
    const L = await api(`/api/bench/lineage?bank=${encodeURIComponent(bank)}&kpi=${encodeURIComponent(kpi)}&quarter_key=${qk}`);
    const v = L.value;
    if (!v) { body.innerHTML = `<p class="muted">No value is stored for ${esc(bankName(bank))} ${esc(k.name)} in ${esc(L.quarter)}.</p>`; return; }
    const d = L.document || {};
    const isWeb = d.url && /^https?:/.test(d.url);
    body.innerHTML = `
      <div class="lin-hero">
        <div><div class="lin-val">${esc(fmtVal(v.value, k.unit, { compact: false }))}</div>
          <div class="muted">${esc(L.quarter)}, ${esc(k.basis || '')}</div></div>
        <div class="lin-status">${statusChip(v.status)} ${(v.flags || []).map(flagChip).join(' ')}</div>
      </div>
      <ol class="lin-steps">
        <li><b>Published by the bank</b><span>${esc(d.title || 'Unknown document')}</span>
          ${d.url ? `<span class="muted small">${isWeb ? `<a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.url)}</a>` : esc(d.url)}</span>` : ''}</li>
        <li><b>Downloaded and fingerprinted</b><span>${d.fetched_at ? esc(fmtTime(d.fetched_at, true)) : '–'}, SHA-256 <code>${esc((d.sha256 || '').slice(0, 16))}…</code></span>
          ${d.id ? `<span><a href="${esc(withAuth(`/api/pipeline/documents/${d.id}/file`))}" target="_blank" rel="noopener">${icon('doc', 14)} Open stored PDF</a></span>` : ''}</li>
        <li><b>Read from page ${esc(v.page ?? '–')}</b><span>${esc(METHOD_TEXT[v.method] || v.method)}, confidence ${(v.confidence * 100).toFixed(0)}%, ${esc(v.support)} supporting document(s)</span>
          <code class="evidence">${esc(v.evidence || '')}</code>
          ${d.id && v.page ? `<button type="button" class="btn btn-ghost btn-sm" data-page='${esc(JSON.stringify({ doc: d.id, page: v.page, value: v.value }))}'>${icon('doc', 14)} Show page text</button>` : ''}</li>
        <li><b>Checked by the validation agent</b><span>${(v.flags || []).length ? (v.flags || []).map(flagChip).join(' ') : 'No issues raised'}</span></li>
        <li><b>${v.status === 'approved' || v.status === 'overridden' ? 'Approved by a human checker' : 'Waiting for a human checker'}</b>
          <span>${v.maker ? 'Prepared by ' + esc(v.maker.replace('pipeline:', 'pipeline run by ')) : ''}${v.checker ? ', decided by <b>' + esc(v.checker) + '</b>' : ''}${v.decided_at ? ' at ' + esc(fmtTime(v.decided_at, true)) : ''}</span>
          ${v.override_reason ? `<span class="muted">Reason: ${esc(v.override_reason)}</span>` : ''}</li>
      </ol>
      <h3>Every reading the agents found</h3>
      <table class="tbl">
        <thead><tr><th>Value</th><th>Method</th><th>Confidence</th><th>Page</th><th>Document</th><th>Evidence</th></tr></thead>
        <tbody>${(L.candidates || []).map(c => `<tr class="${c.value === v.value ? 'hl' : ''}">
          <td class="num">${esc(fmtVal(c.value, k.unit, { compact: false }))}</td><td>${esc(METHOD_TEXT[c.method] || c.method)}</td>
          <td class="num">${(c.confidence * 100).toFixed(0)}%</td><td class="num">${esc(c.page ?? '–')}</td>
          <td class="small">${esc(c.doc_quarter || '')} deck${c.primary ? '' : ', history column'}</td><td><code class="evidence sm">${esc(c.evidence || '')}</code></td></tr>`).join('') || '<tr><td colspan="6" class="muted">Entered manually</td></tr>'}</tbody>
      </table>
      ${(v.alternatives || []).length ? `<h3>Alternatives the reconciler set aside</h3><ul class="plain">${v.alternatives.map(a => `<li>${esc(fmtVal(a.value, k.unit, { compact: false }))} <span class="muted">${esc((a.methods || []).join(', '))} ${esc(a.note || '')}</span></li>`).join('')}</ul>` : ''}
      <h3>Audit trail</h3>
      <ul class="plain">${(L.audit || []).map(a => `<li><span class="muted">${esc(fmtTime(a.ts, true))}</span> <b>${esc(a.actor)}</b> ${esc(a.action)} ${a.details ? `<code>${esc(JSON.stringify(a.details))}</code>` : ''}</li>`).join('') || '<li class="muted">No decisions yet</li>'}</ul>`;
    body.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => {
      const p = JSON.parse(b.dataset.page); showPage(p.doc, p.page, p.value);
    }));
  } catch (e) {
    body.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}

export async function showPage(docId, page, highlightValue) {
  const body = openDrawer(`Source page ${page}`, loading('Loading page text…'), { wide: true });
  try {
    const p = await api(`/api/pipeline/documents/${docId}/page/${page}`);
    let html = esc(p.text || '');
    if (highlightValue != null) {
      const variants = new Set([String(highlightValue), Number(highlightValue).toFixed(2), Number(highlightValue).toLocaleString('en-IN', { maximumFractionDigits: 2 })]);
      for (const vv of variants) {
        if (!vv || vv === 'NaN') continue;
        const re = new RegExp('(^|[^\\d.,])(' + vv.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')(?![\\d])', 'g');
        html = html.replace(re, '$1<mark>$2</mark>');
      }
    }
    body.innerHTML = `<p class="muted">Text layer exactly as the extractor read it from the PDF (page ${esc(page)}). Highlighted: the value used.</p>
      <pre class="pagetext">${html}</pre>
      <p><a class="btn btn-ghost btn-sm" href="${esc(withAuth(`/api/pipeline/documents/${docId}/file`))}#page=${esc(page)}" target="_blank" rel="noopener">${icon('doc', 14)} Open the PDF at this page</a></p>`;
  } catch (e) {
    body.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}
