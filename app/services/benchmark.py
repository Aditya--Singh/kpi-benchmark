"""Benchmark engine: point-in-time scorecard, trends, peer comparison, heatmap, coverage, lineage,
and the numbered FACT SHEET that every generated narrative must be grounded in."""
from __future__ import annotations

import statistics
from collections import defaultdict

from sqlalchemy import select

from ..catalog import BANKS, KPI_BY_CODE, VISIBLE_KPIS
from ..config import settings
from ..db import AuditLog, Candidate, Document, KpiValue, session_scope
from ..periods import Quarter, from_key

HOME = settings.HOME_BANK
PEERS = [b for b in BANKS if b != HOME]

ACTIONS = {
    "CASA_RATIO": "Push low-cost deposit mobilisation: salary & government accounts, transaction banking, CASA-linked digital journeys.",
    "NIM": "Protect margin: tighten deposit pricing, grow higher-yield RAM book, review bulk-deposit dependence.",
    "COST_TO_INCOME": "Improve efficiency: process automation, digital migration of service requests, branch productivity review.",
    "GNPA": "Accelerate resolution in stressed segments (Agri/MSME), strengthen early-warning and SMA-2 monitoring.",
    "NNPA": "Lift recoveries and provisioning on residual NPAs; prioritise high-value legacy accounts.",
    "PCR": "Build provisioning buffers to peer level to protect future earnings from asset-quality shocks.",
    "SLIPPAGE": "Tighten underwriting and collection efficiency in segments driving fresh slippages.",
    "CREDIT_COST": "Contain credit cost through better collections and pre-emptive restructuring discipline.",
    "ROA": "Raise core profitability: fee income, margin management and credit-cost control.",
    "ROE": "Improve return on equity via profitable growth and efficient capital use.",
    "CRAR": "Review capital plan (internal accruals, AT-1/Tier-2) to keep headroom for growth.",
    "CET1": "Strengthen core equity through retained earnings and RWA optimisation.",
    "NET_PROFIT": "Sustain earnings momentum: core income growth with stable credit costs.",
    "OPERATING_PROFIT": "Grow pre-provision profit via NII and fee income while containing opex.",
    "NII": "Grow interest income through balanced loan growth and asset-liability pricing.",
    "GLOBAL_ADVANCES": "Calibrate credit growth to peers while protecting asset quality.",
    "GLOBAL_DEPOSITS": "Deepen deposit franchise, especially granular retail term and CASA.",
    "GLOBAL_BUSINESS": "Expand overall business share through RAM and corporate pipelines.",
    "CD_RATIO": "Balance credit-deposit ratio against liquidity (LCR/NSFR) comfort.",
}


def load_values(include_staged: bool) -> dict[tuple, KpiValue]:
    statuses = ["approved", "overridden"] + (["staged"] if include_staged else [])
    with session_scope() as s:
        rows = s.execute(select(KpiValue).where(KpiValue.status.in_(statuses))).scalars().all()
        for r in rows:
            s.expunge(r)
    return {(r.bank, r.kpi, r.quarter_key): r for r in rows}


def quarters_available(vals: dict) -> list[int]:
    return sorted({qk for (_, _, qk) in vals})


def default_quarter(vals: dict, min_banks: int | None = None) -> int | None:
    """Latest quarter in which ALL banks have reported (a like-for-like peer view); falls back to the
    latest quarter with at least 3 banks, then to the latest quarter with any data."""
    per_q = defaultdict(set)
    for (b, k, qk) in vals:
        if KPI_BY_CODE[k].visible:
            per_q[qk].add(b)
    for need in ([min_banks] if min_banks else [len(BANKS), 3, 1]):
        good = [qk for qk, bs in per_q.items() if len(bs) >= need]
        if good:
            return max(good)
    return None


def quarter_options(vals: dict) -> list[dict]:
    per_q = defaultdict(set)
    for (b, k, qk) in vals:
        if KPI_BY_CODE[k].visible:
            per_q[qk].add(b)
    return [{"key": q, "label": from_key(q).label, "banks": len(per_q[q]), "of": len(BANKS)}
            for q in sorted(per_q, reverse=True)]


def fmt(v: float | None, kpi: str) -> str:
    if v is None:
        return "-"
    k = KPI_BY_CODE[kpi]
    if k.unit == "%":
        return f"{v:.2f}%"
    return f"₹{v:,.0f} cr"


def _growth(vals, bank, kpi, qk, lag) -> float | None:
    cur = vals.get((bank, kpi, qk))
    prev = vals.get((bank, kpi, from_key(qk).shift(-lag).key))
    if not cur or not prev or not prev.value:
        return None
    return (cur.value / prev.value - 1) * 100


def _delta(vals, bank, kpi, qk, lag) -> float | None:
    cur = vals.get((bank, kpi, qk))
    prev = vals.get((bank, kpi, from_key(qk).shift(-lag).key))
    if not cur or not prev:
        return None
    return cur.value - prev.value


def kpi_snapshot(vals: dict, kpi: str, qk: int) -> dict:
    k = KPI_BY_CODE[kpi]
    per_bank = {}
    for b in BANKS:
        r = vals.get((b, kpi, qk))
        if r is None:
            continue
        item = {"bank": b, "value": r.value, "status": r.status, "confidence": r.confidence,
                "flags": r.flags or [], "method": r.method}
        if k.unit == "cr":
            item["yoy_growth"] = _growth(vals, b, kpi, qk, 4)
            item["qoq_growth"] = _growth(vals, b, kpi, qk, 1)
        else:
            d4, d1 = _delta(vals, b, kpi, qk, 4), _delta(vals, b, kpi, qk, 1)
            item["yoy_bps"] = None if d4 is None else d4 * 100
            item["qoq_bps"] = None if d1 is None else d1 * 100
        per_bank[b] = item
    metric = "yoy_growth" if k.unit == "cr" else "value"
    direction = 1 if k.unit == "cr" else k.direction
    rank_basis = "YoY growth" if k.unit == "cr" else "level"
    if k.unit == "cr":
        with_growth = [b for b, it in per_bank.items() if it.get("yoy_growth") is not None]
        if len(with_growth) < min(3, len(per_bank)) or (HOME in per_bank and HOME not in with_growth):
            # not enough year-ago history yet (e.g. first collection) - fall back to ranking by size
            metric, rank_basis = "value", "size (YoY history not yet available)"
    rankable = [(b, it[metric]) for b, it in per_bank.items() if it.get(metric) is not None]
    rankable.sort(key=lambda x: -direction * x[1])
    for i, (b, _) in enumerate(rankable, start=1):
        per_bank[b]["rank"] = i
    peer_vals = [it["value"] for b, it in per_bank.items() if b != HOME]
    peer_metric = [it[metric] for b, it in per_bank.items() if b != HOME and it.get(metric) is not None]
    out = {"kpi": kpi, "name": k.name, "short": k.short, "unit": k.unit, "category": k.category,
           "direction": k.direction, "rank_basis": rank_basis, "rank_metric": metric,
           "banks": per_bank, "n": len(rankable), "quarter": from_key(qk).label, "quarter_key": qk}
    if peer_vals:
        out["peer_avg"] = statistics.mean(peer_vals)
        out["peer_median"] = statistics.median(peer_vals)
    if peer_metric:
        out["peer_avg_metric"] = statistics.mean(peer_metric)
    if rankable:
        out["best_bank"], out["best_metric"] = rankable[0]
    home = per_bank.get(HOME)
    if home:
        out["home"] = home
        if k.unit == "%" and "peer_avg" in out:
            gap = home["value"] - out["peer_avg"]
            out["gap"] = gap
            out["gap_bps"] = gap * 100
            out["favourable"] = gap * k.direction > 0
        elif k.unit == "cr" and metric == "yoy_growth" and home.get("yoy_growth") is not None and "peer_avg_metric" in out:
            gap = home["yoy_growth"] - out["peer_avg_metric"]
            out["gap"] = gap
            out["favourable"] = gap > 0
    return out


def scorecard(include_staged: bool = False, qk: int | None = None) -> dict:
    vals = load_values(include_staged)
    qk = qk or default_quarter(vals)
    if qk is None:
        return {"quarter": None, "kpis": [], "banks_reported": [], "message": "No data yet. Run the data pipeline."}
    snaps = [kpi_snapshot(vals, k.code, qk) for k in VISIBLE_KPIS]
    reported = sorted({b for (b, kk, q) in vals if q == qk and KPI_BY_CODE[kk].visible})
    latest_per_bank = {}
    for b in BANKS:
        qs = [q for (bb, kk, q) in vals if bb == b and KPI_BY_CODE[kk].visible]
        latest_per_bank[b] = from_key(max(qs)).label if qs else None
    provisional = any(it["status"] == "staged" for s in snaps for it in s["banks"].values())
    return {"quarter": from_key(qk).label, "quarter_key": qk, "kpis": snaps, "banks_reported": reported,
            "latest_per_bank": latest_per_bank, "provisional": provisional,
            "quarters": quarter_options(vals)}


def trend(kpi: str, banks: list[str] | None = None, n: int = 20, include_staged: bool = False) -> dict:
    vals = load_values(include_staged)
    qs = quarters_available(vals)
    if not qs:
        return {"kpi": kpi, "quarters": [], "series": []}
    last = max(qs)
    window = [from_key(last).shift(-i) for i in range(n - 1, -1, -1)]
    banks = banks or list(BANKS)
    series = []
    for b in banks:
        pts = []
        for q in window:
            r = vals.get((b, kpi, q.key))
            pts.append(None if r is None else round(r.value, 4))
        series.append({"bank": b, "name": BANKS[b]["short"], "color": BANKS[b]["color"], "values": pts,
                       "statuses": [None if vals.get((b, kpi, q.key)) is None else vals[(b, kpi, q.key)].status
                                    for q in window]})
    peer = []
    for q in window:
        pv = [vals[(b, kpi, q.key)].value for b in PEERS if (b, kpi, q.key) in vals]
        # only plot a peer average when at least 3 of the 4 peers reported, so the line does not jump
        # simply because the set of banks changed
        peer.append(round(statistics.mean(pv), 4) if len(pv) >= min(3, len(PEERS)) else None)
    series.append({"bank": "PEER_AVG", "name": "Peer avg", "color": "#8A8F98", "values": peer, "dashed": True})
    k = KPI_BY_CODE[kpi]
    stats = {}
    for sr in series:
        pts = [(i, v) for i, v in enumerate(sr["values"]) if v is not None]
        if len(pts) >= 2:
            (i0, v0), (i1, v1) = pts[0], pts[-1]
            st = {"first": v0, "last": v1, "first_q": window[i0].label, "last_q": window[i1].label}
            if k.unit == "cr" and v0 > 0 and i1 > i0:
                st["cagr"] = ((v1 / v0) ** (4 / (i1 - i0)) - 1) * 100
            else:
                st["change_bps"] = (v1 - v0) * 100
            stats[sr["bank"]] = st
    return {"kpi": kpi, "name": k.name, "unit": k.unit, "direction": k.direction,
            "quarters": [q.label for q in window], "series": series, "stats": stats}


def heatmap(include_staged: bool = False, qk: int | None = None) -> dict:
    sc = scorecard(include_staged, qk)
    rows = []
    for s in sc.get("kpis", []):
        rows.append({"kpi": s["kpi"], "short": s["short"], "unit": s["unit"], "rank_basis": s["rank_basis"],
                     "cells": {b: {"value": it["value"], "rank": it.get("rank"), "status": it["status"],
                                   "metric": it.get("yoy_growth") if s["unit"] == "cr" else it["value"]}
                               for b, it in s["banks"].items()}, "n": s["n"]})
    return {"quarter": sc.get("quarter"), "rows": rows, "banks": list(BANKS)}


def coverage() -> dict:
    with session_scope() as s:
        rows = s.execute(select(KpiValue.bank, KpiValue.quarter_key, KpiValue.kpi, KpiValue.status)).all()
        docs = s.execute(select(Document.bank, Document.quarter_key, Document.status)).all()
    cell = defaultdict(lambda: {"approved": 0, "staged": 0, "docs": 0, "doc_status": []})
    vis = {k.code for k in VISIBLE_KPIS}
    for b, qk, kpi, st in rows:
        if kpi not in vis or st == "rejected":
            continue
        c = cell[(b, qk)]
        if st in ("approved", "overridden"):
            c["approved"] += 1
        else:
            c["staged"] += 1
    for b, qk, st in docs:
        if qk is None:
            continue
        c = cell[(b, qk)]
        c["docs"] += 1
        c["doc_status"].append(st)
    qks = sorted({qk for (_, qk) in cell})
    return {"banks": list(BANKS), "quarters": [{"key": q, "label": from_key(q).label} for q in qks][-20:],
            "kpi_count": len(vis),
            "cells": {f"{b}|{qk}": v for (b, qk), v in cell.items()}}


def lineage(bank: str, kpi: str, qk: int) -> dict:
    with session_scope() as s:
        v = s.execute(select(KpiValue).where(KpiValue.bank == bank, KpiValue.kpi == kpi,
                                             KpiValue.quarter_key == qk)).scalar_one_or_none()
        cands = s.execute(select(Candidate, Document).join(Document, Candidate.doc_id == Document.id).where(
            Candidate.bank == bank, Candidate.kpi == kpi, Candidate.quarter_key == qk)).all()
        audit = s.execute(select(AuditLog).where(AuditLog.entity == "kpi_value",
                                                 AuditLog.entity_id == f"{bank}|{kpi}|{qk}").order_by(AuditLog.id)).scalars().all()
        doc = s.get(Document, v.doc_id) if v and v.doc_id else None
        return {
            "bank": bank, "kpi": kpi, "kpi_name": KPI_BY_CODE[kpi].name, "quarter": from_key(qk).label,
            "value": None if not v else {
                "value": v.value, "status": v.status, "method": v.method, "confidence": v.confidence,
                "support": v.support, "flags": v.flags, "alternatives": v.alternatives, "evidence": v.evidence,
                "page": v.page_no, "maker": v.maker, "checker": v.checker,
                "decided_at": v.decided_at.isoformat() if v.decided_at else None,
                "override_reason": v.override_reason, "tier": v.tier},
            "document": None if not doc else {"id": doc.id, "title": doc.title, "url": doc.source_url,
                                               "sha256": doc.sha256, "fetched_at": doc.fetched_at.isoformat() if doc.fetched_at else None,
                                               "quarter": from_key(doc.quarter_key).label if doc.quarter_key else None},
            "candidates": [{"value": c.value, "method": c.method, "confidence": c.confidence, "page": c.page_no,
                            "primary": c.is_primary, "evidence": c.evidence, "doc_id": d.id, "doc_title": d.title,
                            "doc_quarter": from_key(d.quarter_key).label if d.quarter_key else None, "url": d.source_url}
                           for c, d in cands],
            "audit": [{"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "details": a.details} for a in audit],
        }


# ----------------------------------------------------------------------------- facts + insights
def _n(v: float, dec: int = 2) -> float:
    return round(v, dec)


def fact_sheet(include_staged: bool = False, qk: int | None = None) -> dict:
    sc = scorecard(include_staged, qk)
    if not sc.get("quarter"):
        return {"quarter": None, "facts": [], "insights": {}, "provisional": False}
    q = sc["quarter"]
    facts: list[dict] = []

    def add(kpi, ftype, text, numbers, banks=None):
        facts.append({"id": f"F{len(facts) + 1}", "kpi": kpi, "type": ftype, "text": text,
                      "numbers": [x for x in numbers if x is not None], "banks": banks or [HOME]})

    strengths, concerns, momentum = [], [], []
    for s in sc["kpis"]:
        k = KPI_BY_CODE[s["kpi"]]
        home = s.get("home")
        if not home:
            continue
        n = s["n"]
        if k.unit == "%":
            v = _n(home["value"])
            txt = f"PNB {k.name} was {v:.2f}% in {q}"
            nums = [v]
            if "peer_avg" in s:
                pa = _n(s["peer_avg"])
                txt += f", against a peer average of {pa:.2f}%"
                nums.append(pa)
            if home.get("rank"):
                txt += f"; PNB ranks {home['rank']} of {n} banks"
                nums += [home["rank"], n]
            if s.get("best_bank") and s["best_bank"] != HOME:
                bb = s["best_bank"]
                bv = _n(s["banks"][bb]["value"])
                txt += f" (best: {BANKS[bb]['short']} at {bv:.2f}%)"
                nums.append(bv)
            add(k.code, "position", txt + ".", nums)
            if home.get("yoy_bps") is not None:
                d = _n(home["yoy_bps"], 0)
                better = d * k.direction > 0
                word = "improved" if better else ("deteriorated" if d != 0 else "was unchanged")
                add(k.code, "movement", f"PNB {k.short} {word} by {abs(d):.0f} bps year-on-year.", [abs(d)])
                peer_moves = [it["yoy_bps"] for b, it in s["banks"].items() if b != HOME and it.get("yoy_bps") is not None]
                if peer_moves and better and d * k.direction > statistics.mean(peer_moves) * k.direction:
                    momentum.append(k.code)
        else:
            v = _n(home["value"], 0)
            txt = f"PNB {k.name} stood at Rs {v:,.0f} crore in {q}"
            nums = [v]
            if home.get("yoy_growth") is not None:
                g = _n(home["yoy_growth"], 1)
                txt += f", up {g:.1f}% year-on-year" if g >= 0 else f", down {abs(g):.1f}% year-on-year"
                nums.append(abs(g))
                if s.get("peer_avg_metric") is not None:
                    pg = _n(s["peer_avg_metric"], 1)
                    txt += f" (peer average growth {pg:.1f}%)"
                    nums.append(abs(pg))
                if home.get("rank") and s.get("rank_metric") == "yoy_growth":
                    txt += f"; growth rank {home['rank']} of {n}"
                    nums += [home["rank"], n]
            if home.get("rank") and s.get("rank_metric") == "value":
                txt += f"; ranks {home['rank']} of {n} by size"
                nums += [home["rank"], n]
            add(k.code, "position", txt + ".", nums)
        if home.get("rank") and n >= 3 and not (k.unit == "cr" and s.get("rank_metric") != "yoy_growth"):
            gap = s.get("gap")
            if k.unit == "%":
                material = gap is not None and abs(gap) >= max(0.05 * abs(s.get("peer_avg") or 0), 0.10)
            else:
                material = gap is not None and abs(gap) >= 1.5
            if home["rank"] == 1 or (s.get("favourable") and material):
                strengths.append(k.code)
            elif home["rank"] == n or (s.get("favourable") is False and material):
                concerns.append(k.code)
    # peer leaders
    for s in sc["kpis"]:
        if s.get("best_bank") and s["best_bank"] != HOME and s["unit"] == "%":
            bb = s["best_bank"]
            add(s["kpi"], "leader", f"Best-in-class {s['short']} in {q}: {BANKS[bb]['name']} at "
                f"{s['banks'][bb]['value']:.2f}%.", [_n(s["banks"][bb]["value"])], [bb])
    actions = [{"kpi": c, "kpi_name": KPI_BY_CODE[c].name, "action": ACTIONS.get(c, "")} for c in concerns]
    return {"quarter": q, "quarter_key": sc["quarter_key"], "facts": facts,
            "insights": {"strengths": strengths, "concerns": concerns, "momentum": momentum, "actions": actions},
            "provisional": sc["provisional"], "banks_reported": sc["banks_reported"],
            "latest_per_bank": sc["latest_per_bank"]}
