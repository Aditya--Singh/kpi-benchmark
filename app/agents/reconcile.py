"""Validation & Reconciliation agent (the 'Critic').

For every (bank, KPI, quarter) it gathers all candidate readings from all documents - the quarter's own
deck plus later decks that repeat it as a comparison column - clusters readings that agree, and picks the
value with the strongest independent support. It then derives missing KPIs from their components,
runs consistency checks and raises findings for the human approver.

Approved or manually overridden values are never silently replaced: if new evidence disagrees, the value
goes back to 'staged' with a 'changed_after_approval' flag.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select

from ..catalog import KPI_BY_CODE, KPIS
from ..db import Candidate, Document, Finding, KpiValue, now, session_scope
from ..periods import from_key
from .base import Agent, PipelineContext

PRIMARY_BONUS = 0.15


def _safe_formula(expr: str, env: dict) -> float | None:
    """Evaluate 'A + B' / 'A / B * 100' style catalogue formulas left-to-right without eval()."""
    toks = expr.split()
    try:
        acc = float(env[toks[0]]) if toks[0] in env else float(toks[0])
        for op, tok in zip(toks[1::2], toks[2::2]):
            v = float(env[tok]) if tok in env else float(tok)
            if op == "+":
                acc += v
            elif op == "-":
                acc -= v
            elif op == "*":
                acc *= v
            elif op == "/":
                acc /= v
            else:
                return None
        return acc
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _tol(kpi_code: str, v: float) -> float:
    k = KPI_BY_CODE[kpi_code]
    return 0.012 if k.unit == "%" else max(1.0, abs(v) * 0.003)


def _cluster(cands: list[Candidate]) -> list[list[Candidate]]:
    groups: list[list[Candidate]] = []
    for c in sorted(cands, key=lambda c: c.value):
        if groups and abs(c.value - groups[-1][0].value) <= _tol(c.kpi, c.value):
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def _group_score(g: list[Candidate]) -> float:
    best_per_doc: dict = {}
    for c in g:
        sc = c.confidence + (PRIMARY_BONUS if c.is_primary else 0.0)
        best_per_doc[c.doc_id] = max(best_per_doc.get(c.doc_id, 0), sc)
    methods = {c.method for c in g}
    return sum(best_per_doc.values()) + 0.05 * (len(methods) - 1)


class ReconcilerAgent(Agent):
    name = "reconciler"
    title = "Validation & Reconciliation"
    goal = ("Cross-check every reading across documents, choose the best-supported value, derive missing KPIs, "
            "and flag anything a human must look at before it reaches top management.")
    tools = ["agreement clustering", "derivation formulas", "consistency rules", "jump detector", "basis checker"]
    inputs = ["candidates"]
    outputs = ["kpi_values (staged)", "findings"]

    def execute(self, ctx: PipelineContext):
        banks = ctx.banks
        staged = changed = kept = 0
        with session_scope() as s:
            tiers = {d.id: d.tier for d in s.execute(select(Document)).scalars()}
            rows = s.execute(select(Candidate).where(Candidate.bank.in_(banks))).scalars().all()
            by: dict[tuple, list[Candidate]] = defaultdict(list)
            for c in rows:
                by[(c.bank, c.kpi, c.quarter_key)].append(c)
            existing = {(v.bank, v.kpi, v.quarter_key): v for v in
                        s.execute(select(KpiValue).where(KpiValue.bank.in_(banks))).scalars()}
            for key, cands in by.items():
                bank, kpi, qk = key
                groups = _cluster(cands)
                scored = sorted(((_group_score(g), g) for g in groups), key=lambda x: -x[0])
                win_score, win = scored[0]
                rep = max(win, key=lambda c: (c.is_primary, c.method == "table", c.confidence))
                support = len({c.doc_id for c in win})
                flags: list[str] = []
                alts = []
                for sc, g in scored[1:4]:
                    gv = max(g, key=lambda c: c.confidence)
                    alts.append({"value": gv.value, "score": round(sc, 2), "methods": sorted({c.method for c in g}),
                                 "docs": sorted({c.doc_id for c in g}), "evidence": gv.evidence[:200]})
                    if sc >= 0.5 * win_score:
                        flags.append("conflict")
                    if rep.value and gv.value and KPI_BY_CODE[kpi].unit == "%":
                        ratio = gv.value / rep.value if rep.value else 0
                        if 3.5 <= ratio <= 4.5 or 0.22 <= ratio <= 0.29:
                            flags.append("basis_mismatch")
                top_conf = max(c.confidence for c in win)
                if top_conf < 0.6:
                    flags.append("low_confidence")
                if support == 1 and rep.method in ("chart", "keyvalue", "llm"):
                    flags.append("single_source")
                if rep.method == "llm":
                    flags.append("llm_extracted")
                if rep.note:
                    flags.append("basis_note")
                conf = min(0.99, top_conf + 0.04 * (support - 1) + (0.05 if rep.is_primary else 0))
                flags = sorted(set(flags))
                ev = existing.get(key)
                if ev is None:
                    s.add(KpiValue(bank=bank, kpi=kpi, quarter_key=qk, value=rep.value, status="staged",
                                   method=rep.method, confidence=round(conf, 3), support=support, candidate_id=rep.id,
                                   doc_id=rep.doc_id, page_no=rep.page_no, evidence=rep.evidence,
                                   tier=tiers.get(rep.doc_id, 2), flags=flags, alternatives=alts,
                                   maker=f"pipeline:{ctx.user}", run_id=ctx.run_id))
                    staged += 1
                elif ev.status == "overridden":
                    if abs(ev.value - rep.value) > _tol(kpi, rep.value):
                        ev.alternatives = [{"value": rep.value, "score": round(win_score, 2), "methods": [rep.method],
                                            "docs": [rep.doc_id], "evidence": rep.evidence[:200],
                                            "note": "new extraction differs from manual override"}] + (alts or [])
                    kept += 1
                elif ev.status == "approved" and abs(ev.value - rep.value) <= _tol(kpi, rep.value):
                    ev.support, ev.alternatives = support, alts
                    kept += 1
                else:
                    if ev.status == "approved":
                        flags.append("changed_after_approval")
                        alts.insert(0, {"value": ev.value, "score": None, "methods": ["previously approved"],
                                        "docs": [ev.doc_id], "evidence": ev.evidence[:200] if ev.evidence else ""})
                        ev.status, ev.checker, ev.decided_at = "staged", None, None
                        changed += 1
                    ev.value, ev.method, ev.confidence, ev.support = rep.value, rep.method, round(conf, 3), support
                    ev.candidate_id, ev.doc_id, ev.page_no, ev.evidence = rep.id, rep.doc_id, rep.page_no, rep.evidence
                    ev.flags, ev.alternatives, ev.run_id = sorted(set(flags)), alts, ctx.run_id
                    ev.tier = tiers.get(rep.doc_id, 2)
                    if ev.status != "staged":
                        ev.status = "staged"
                    staged += 1
        derived = self._derive(ctx)
        findings = self._validate(ctx)
        ctx.bump("values_staged", staged)
        return {"values_staged_or_updated": staged, "approved_kept": kept, "reopened_after_change": changed,
                "derived": derived, "findings": findings}

    # ------------------------------------------------------------------ derivations
    def _derive(self, ctx: PipelineContext) -> int:
        n = 0
        with session_scope() as s:
            vals = s.execute(select(KpiValue).where(KpiValue.bank.in_(ctx.banks))).scalars().all()
            idx = {(v.bank, v.kpi, v.quarter_key): v for v in vals if v.status != "rejected"}
            keys = {(v.bank, v.quarter_key) for v in vals}
            for bank, qk in keys:
                for k in KPIS:
                    if not k.derived or (bank, k.code, qk) in idx:
                        continue
                    expr = k.derived
                    comps = [c for c in KPI_BY_CODE if c in expr.split()]
                    if not all((bank, c, qk) in idx for c in comps):
                        continue
                    env = {c: idx[(bank, c, qk)].value for c in comps}
                    val = _safe_formula(expr, env)
                    if val is None:
                        continue
                    if not k.in_range(val):
                        continue
                    conf = min(idx[(bank, c, qk)].confidence for c in comps) * 0.95
                    ev = "derived: " + expr + " = " + " ; ".join(f"{c}={env[c]:,.2f}" for c in comps)
                    s.add(KpiValue(bank=bank, kpi=k.code, quarter_key=qk, value=round(val, 4), status="staged",
                                   method="derived", confidence=round(conf, 3), support=len(comps), evidence=ev,
                                   flags=["derived"], alternatives=[], maker=f"pipeline:{ctx.user}", run_id=ctx.run_id,
                                   tier=max(idx[(bank, c, qk)].tier for c in comps)))
                    idx[(bank, k.code, qk)] = True  # type: ignore[assignment]
                    n += 1
        return n

    # ------------------------------------------------------------------ validation rules
    def _validate(self, ctx: PipelineContext) -> dict:
        counts: dict[str, int] = defaultdict(int)
        with session_scope() as s:
            s.execute(delete(Finding).where(Finding.bank.in_(ctx.banks), Finding.status == "open"))
            vals = s.execute(select(KpiValue).where(KpiValue.bank.in_(ctx.banks),
                                                    KpiValue.status != "rejected")).scalars().all()
            idx = {(v.bank, v.kpi, v.quarter_key): v for v in vals}

            def add(sev, rule, msg, bank=None, kpi=None, qk=None, flag=None):
                s.add(Finding(run_id=ctx.run_id, bank=bank, kpi=kpi, quarter_key=qk, severity=sev, rule=rule,
                              message=msg))
                counts[rule] += 1
                if flag and (bank, kpi, qk) in idx:
                    v = idx[(bank, kpi, qk)]
                    v.flags = sorted(set((v.flags or []) + [flag]))

            for v in vals:
                k = KPI_BY_CODE[v.kpi]
                ql = from_key(v.quarter_key).label
                if not k.in_range(v.value):
                    add("error", "range", f"{v.bank} {k.short} {ql} = {v.value} is outside the plausible range",
                        v.bank, v.kpi, v.quarter_key, "range")
                if "conflict" in (v.flags or []):
                    alt = (v.alternatives or [{}])[0].get("value")
                    add("warn", "conflict", f"{v.bank} {k.short} {ql}: sources disagree ({v.value:g} vs {alt}); "
                        "the best-supported value was chosen - please confirm", v.bank, v.kpi, v.quarter_key)
                if "basis_mismatch" in (v.flags or []):
                    add("warn", "basis", f"{v.bank} {k.short} {ql}: one source is ~4x another - likely quarterly vs "
                        "annualised basis. Confirm the annualised figure for peer comparison.", v.bank, v.kpi, v.quarter_key)
                if "changed_after_approval" in (v.flags or []):
                    add("warn", "changed", f"{v.bank} {k.short} {ql}: a newer document reports a different value than "
                        "the one approved earlier (restated?) - re-approval needed", v.bank, v.kpi, v.quarter_key)
            # consistency
            for (bank, kpi, qk), v in idx.items():
                ql = from_key(qk).label
                if kpi == "NNPA" and (bank, "GNPA", qk) in idx and v.value >= idx[(bank, "GNPA", qk)].value:
                    add("error", "consistency", f"{bank} {ql}: Net NPA ({v.value}) >= Gross NPA "
                        f"({idx[(bank, 'GNPA', qk)].value})", bank, kpi, qk, "inconsistent")
                if kpi == "CET1" and (bank, "CRAR", qk) in idx and v.value >= idx[(bank, "CRAR", qk)].value:
                    add("error", "consistency", f"{bank} {ql}: CET-1 ({v.value}) >= CRAR "
                        f"({idx[(bank, 'CRAR', qk)].value})", bank, kpi, qk, "inconsistent")
                if kpi == "GLOBAL_BUSINESS" and (bank, "GLOBAL_DEPOSITS", qk) in idx and (bank, "GLOBAL_ADVANCES", qk) in idx:
                    tot = idx[(bank, "GLOBAL_DEPOSITS", qk)].value + idx[(bank, "GLOBAL_ADVANCES", qk)].value
                    if tot and abs(v.value - tot) / tot > 0.02:
                        add("warn", "consistency", f"{bank} {ql}: Business {v.value:,.0f} differs from Deposits + "
                            f"Advances {tot:,.0f} by more than 2%", bank, kpi, qk, "inconsistent")
            # jumps (quarter-on-quarter)
            for (bank, kpi, qk), v in idx.items():
                k = KPI_BY_CODE[kpi]
                pq = from_key(qk).shift(-1).key
                prev = idx.get((bank, kpi, pq))
                if not prev or not k.visible or not prev.value:
                    continue
                rel = abs(v.value - prev.value) / abs(prev.value) * 100
                absd = abs(v.value - prev.value)
                if rel > k.jump_pct and (k.unit == "cr" or absd > 0.25):
                    add("warn", "jump", f"{bank} {k.short} moved from {prev.value:,.2f} ({from_key(pq).label}) to "
                        f"{v.value:,.2f} ({from_key(qk).label}) - {rel:.0f}% change. Verify against the source "
                        "(one-offs such as exceptional items are common).", bank, kpi, qk, "jump")
            # coverage
            all_q = sorted({qk for (_, _, qk) in idx})
            latest = max(all_q) if all_q else None
            for bank in ctx.banks:
                bq = [qk for (b, _, qk) in idx if b == bank]
                if not bq:
                    add("warn", "coverage", f"{bank}: no KPI values yet - check the Data Pipeline tab for download "
                        "errors or drop the PDFs into data/inbox/" + bank, bank)
                    continue
                last = max(bq)
                if latest and last < latest:
                    add("info", "awaited", f"{bank}: latest reported quarter is {from_key(last).label}; "
                        f"{from_key(latest).label} results not yet published/collected", bank, None, latest)
                for k in KPIS:
                    if k.visible and (bank, k.code, last) not in idx:
                        add("info", "missing", f"{bank} {k.short} not found for {from_key(last).label} "
                            "(not disclosed, or needs manual entry)", bank, k.code, last)
            for d in s.execute(select(Document).where(Document.bank.in_(ctx.banks))).scalars():
                io = (d.stats or {}).get("image_only_pages") or []
                if io:
                    add("info", "ocr", f"{d.bank} {d.title[:60]}: {len(io)} page(s) are images without text - "
                        "values on them need OCR or manual entry", d.bank, None, d.quarter_key)
                if d.status == "failed":
                    add("warn", "document", f"{d.bank}: {d.title[:70]} failed - {d.error}", d.bank, None, d.quarter_key)
        return dict(counts)
