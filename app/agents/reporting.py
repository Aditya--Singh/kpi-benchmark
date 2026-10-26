"""Reporting agents: Benchmark Analyst -> Insight Writer -> Compliance Guardrail -> Report Publisher."""
from __future__ import annotations

import datetime as dt
import re

from ..catalog import KPI_BY_CODE
from ..db import Report, session_scope
from ..llm import llm, prompt_version
from ..observability import log, tracer
from ..periods import find_periods
from ..security import redact_pii
from ..services import benchmark as bm
from ..services import publish
from .base import Agent, PipelineContext

REPORT_TYPES = {
    "exec_summary": {"title": "Executive Summary - Peer Benchmarking", "words": 260,
                     "spec": "An executive summary for the MD & CEO: 1 opening paragraph on PNB's overall position, "
                             "then 4-6 bullets (strengths, concerns, momentum), then a one-line outlook. "},
    "quarterly_brief": {"title": "Quarterly Performance Brief", "words": 520,
                        "spec": "A quarterly performance brief with sections '## Business', '## Profitability', "
                                "'## Asset Quality', '## Capital', each 2-4 sentences comparing PNB with peers. "},
    "speech": {"title": "Talking Points / Speech Draft", "words": 380,
               "spec": "A speech draft for the MD & CEO addressing senior executives: warm opening, 3 performance "
                       "highlights with numbers, 2 candid areas to improve, a closing call to action. Spoken, formal "
                       "Indian banking English. "},
    "insights": {"title": "Actionable Insights", "words": 450,
                 "spec": "6-8 actionable insights. Each insight: '**Observation**' (with numbers), '**Implication**', "
                         "'**Suggested action**' (for management consideration, not a directive). "},
    "board_pack": {"title": "Peer Benchmarking Board Pack", "words": 300,
                   "spec": "A concise executive summary for a board pack: 1 paragraph + 5 bullets. "},
}

SYSTEM = ("You are a senior strategy analyst in the Data Analytics & Strategic Innovation Division of Punjab National "
          "Bank, writing for top management. Use ONLY the numbered FACTS provided. Every sentence that contains a "
          "number must end with the fact id(s) in square brackets, e.g. [F3] or [F3, F7]. Never compute, round "
          "differently, estimate or invent any number, bank, or event. If a fact is missing, do not mention it. "
          "No investment advice, no forward-looking guarantees. Write in formal Indian banking English, in Markdown.")


class BenchmarkAnalystAgent(Agent):
    name = "analyst"
    title = "Benchmark Analyst"
    goal = "Turn approved KPI values into ranks, gaps, trends and a numbered fact sheet for one quarter."
    tools = ["benchmark engine"]
    inputs = ["kpi_values (approved, optionally provisional)"]
    outputs = ["fact sheet", "scorecard", "insights"]

    def execute(self, ctx: PipelineContext):
        inc = ctx.params.get("include_staged", False)
        qk = ctx.params.get("quarter_key")
        ctx.stats["scorecard"] = bm.scorecard(inc, qk)
        ctx.stats["facts"] = bm.fact_sheet(inc, qk)
        f = ctx.stats["facts"]
        if not f.get("quarter"):
            raise RuntimeError("No benchmark data available yet - run data collection and approve values first.")
        return {"quarter": f["quarter"], "facts": len(f["facts"]), "strengths": f["insights"]["strengths"],
                "concerns": f["insights"]["concerns"], "provisional": f["provisional"]}


def _template(rtype: str, fs: dict) -> str:
    q = fs["quarter"]
    facts = fs["facts"]
    ins = fs["insights"]
    pos = {f["kpi"]: f for f in facts if f["type"] == "position"}
    mov = {f["kpi"]: f for f in facts if f["type"] == "movement"}

    def cite(f):
        return f"{f['text']} [{f['id']}]"

    peers_note = "Peers: SBI, Bank of Baroda, Canara Bank and Union Bank of India."
    if rtype in ("exec_summary", "board_pack"):
        lines = [f"# Executive Summary - {q}", "",
                 f"This summary benchmarks Punjab National Bank against its public-sector peers for {q}. {peers_note}", ""]
        if ins["strengths"]:
            lines.append("**Where PNB leads or is ahead of the peer average**")
            lines += [f"- {cite(pos[k])}" for k in ins["strengths"][:5] if k in pos]
            lines.append("")
        if ins["momentum"]:
            lines.append("**Improving faster than peers**")
            lines += [f"- {cite(mov[k])}" for k in ins["momentum"][:4] if k in mov]
            lines.append("")
        if ins["concerns"]:
            lines.append("**Areas needing attention**")
            lines += [f"- {cite(pos[k])}" for k in ins["concerns"][:5] if k in pos]
            lines.append("")
        lines.append("Outlook: sustain strengths while closing gaps in the areas flagged above through the actions "
                     "listed in the Actionable Insights note.")
        return "\n".join(lines)
    if rtype == "quarterly_brief":
        lines = [f"# Quarterly Performance Brief - {q}", "", peers_note, ""]
        for cat in ["Business", "Profitability", "Asset Quality", "Capital"]:
            lines.append(f"## {cat}")
            for f in facts:
                if f["type"] in ("position", "movement") and KPI_BY_CODE[f["kpi"]].category == cat:
                    lines.append(f"- {cite(f)}")
            lines.append("")
        return "\n".join(lines)
    if rtype == "speech":
        strong = [pos[k] for k in ins["strengths"][:3] if k in pos]
        weak = [pos[k] for k in ins["concerns"][:2] if k in pos]
        lines = [f"# Talking Points - {q}", "",
                 "Colleagues, good morning. Let me share how our bank has performed against our peers this quarter.", ""]
        if strong:
            lines.append("First, what we have done well.")
            lines += [f"- {cite(f)}" for f in strong]
            lines.append("")
        if weak:
            lines.append("Second, where we must do better - and I say this candidly.")
            lines += [f"- {cite(f)}" for f in weak]
            lines.append("")
        lines.append("Our task for the coming quarter is clear: protect what we have built, and close these gaps with "
                     "discipline and speed. I count on each one of you. Thank you.")
        return "\n".join(lines)
    if rtype == "insights":
        lines = [f"# Actionable Insights - {q}", ""]
        n = 1
        for a in ins["actions"]:
            k = a["kpi"]
            if k in pos:
                lines += [f"### {n}. {a['kpi_name']}", f"**Observation:** {cite(pos[k])}",
                          "**Implication:** PNB trails the peer set on this measure, which affects relative "
                          "standing with investors, rating agencies and the regulator.",
                          f"**Suggested action:** {a['action']}", ""]
                n += 1
        for k in ins["strengths"][:3]:
            if k in pos:
                lines += [f"### {n}. Sustain: {KPI_BY_CODE[k].name}", f"**Observation:** {cite(pos[k])}",
                          "**Implication:** A competitive advantage worth protecting and communicating.",
                          "**Suggested action:** Keep the drivers of this performance under quarterly review.", ""]
                n += 1
        if n == 1:
            lines.append("No material gaps against peers were detected for this quarter.")
        return "\n".join(lines)
    return ""


class InsightWriterAgent(Agent):
    name = "writer"
    title = "Insight Writer"
    goal = "Draft the requested narrative strictly from the numbered fact sheet (local LLM, else template)."
    tools = ["local LLM (Ollama/vLLM)", "deterministic templates"]
    inputs = ["fact sheet"]
    outputs = ["draft markdown"]

    def execute(self, ctx: PipelineContext):
        rtype = ctx.params.get("rtype", "exec_summary")
        spec = REPORT_TYPES[rtype]
        fs = ctx.stats["facts"]
        mode = "template"
        text = None
        use_llm = ctx.params.get("use_llm", True) and llm.available
        if use_llm:
            fact_lines = "\n".join(f"{f['id']}: {f['text']}" for f in fs["facts"])
            ins = fs["insights"]
            hints = (f"Strengths: {', '.join(ins['strengths']) or 'none'}. Concerns: {', '.join(ins['concerns']) or 'none'}. "
                     f"Improving faster than peers: {', '.join(ins['momentum']) or 'none'}. Suggested actions available: "
                     + " | ".join(f"{a['kpi_name']}: {a['action']}" for a in ins["actions"]))
            user = (f"Reference quarter: {fs['quarter']}. Peers: SBI, Bank of Baroda, Canara Bank, Union Bank of India.\n"
                    f"Task: {spec['spec']} About {spec['words']} words. Start with a level-1 heading.\n"
                    f"Analyst hints (no numbers): {hints}\n\nFACTS:\n{fact_lines}")
            with tracer.span(self.name, f"LLM draft: {rtype}", input={"facts": len(fs["facts"])}) as sp:
                text = llm.chat(SYSTEM, user, purpose=f"narrative:{rtype}", template=SYSTEM + spec["spec"],
                                temperature=0.2, max_tokens=1400)
                sp.set_output((text or "")[:300])
            if text:
                mode = "llm"
        if not text:
            text = _template(rtype, fs)
        ctx.stats["draft"] = text
        ctx.stats["mode"] = mode
        ctx.stats["prompt_version"] = prompt_version(SYSTEM + spec["spec"]) if mode == "llm" else "template-v1"
        return {"mode": mode, "chars": len(text)}


NUM_RE = re.compile(r"(?<![A-Za-z#\d])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])")
BANNED = re.compile(r"\b(guarantee[sd]?|assured returns?|target price|buy|sell the stock|strong buy|will definitely)\b", re.I)


def grounding_check(text: str, facts: list[dict]) -> dict:
    """Every number in the text must appear in the fact sheet (within rounding)."""
    allowed = [abs(float(x)) for f in facts for x in f["numbers"]]
    body = re.sub(r"\[F\d+(?:,\s*F\d+)*\]", " ", text)
    body = re.sub(r"(?m)^\s*(?:#+\s*)?\d+\.\s", " ", body)
    for s_, e_, _ in sorted(find_periods(body), reverse=True):
        body = body[:s_] + " " + body[e_:]
    body = re.sub(r"\b(?:Q[1-4]|FY\s*'?\d{2,4}|H[12]|20\d\d|19\d\d)\b", " ", body)
    sentences = re.split(r"(?<=[.!?])\s+|\n", body)
    orig_sentences = re.split(r"(?<=[.!?])\s+|\n", text)
    total = grounded = 0
    bad_sentences = []
    for idx, sent in enumerate(sentences):
        for m in NUM_RE.finditer(sent):
            raw = m.group(1)
            v = float(raw.replace(",", ""))
            if v <= 10 and float(v).is_integer():
                continue  # ranks, counts, list numbering
            total += 1
            if any(abs(v - a) <= max(0.011, a * 0.002) for a in allowed):
                grounded += 1
            else:
                bad_sentences.append({"sentence": (orig_sentences[idx] if idx < len(orig_sentences) else sent)[:300],
                                      "number": raw})
    banned = sorted({m.group(0).lower() for m in BANNED.finditer(text)})
    _, pii = redact_pii(text)
    score = 1.0 if total == 0 else grounded / total
    return {"numbers": total, "grounded": grounded, "score": round(score, 3), "ungrounded": bad_sentences[:20],
            "banned_phrases": banned, "pii": pii}


class GuardrailAgent(Agent):
    name = "guardrail"
    title = "Compliance Guardrail"
    goal = ("Block hallucinated numbers, investment-advice language and personal data before anything reaches "
            "top management; record a grounding score for every output.")
    tools = ["numeric grounding checker", "banned-phrase filter", "PII scanner"]
    inputs = ["draft markdown", "fact sheet"]
    outputs = ["approved draft", "guardrail report"]

    def execute(self, ctx: PipelineContext):
        fs = ctx.stats["facts"]
        text = ctx.stats["draft"]
        rep = grounding_check(text, fs["facts"])
        removed = 0
        if rep["ungrounded"] and ctx.stats["mode"] == "llm":
            for bad in rep["ungrounded"]:
                if bad["sentence"] and bad["sentence"] in text:
                    text = text.replace(bad["sentence"], "")
                    removed += 1
            rep2 = grounding_check(text, fs["facts"])
            if rep2["numbers"] < max(3, rep["numbers"] * 0.6):
                log.warning("Guardrail removed too much of the LLM draft; falling back to the deterministic template")
                text = _template(ctx.params.get("rtype", "exec_summary"), fs)
                ctx.stats["mode"] = "template"
                ctx.stats["prompt_version"] = "template-v1"
                rep2 = grounding_check(text, fs["facts"])
            rep2["removed_sentences"] = removed
            rep = rep2
        if rep["banned_phrases"]:
            text = BANNED.sub("[removed]", text)
        text, pii = redact_pii(text)
        rep["pii_redacted"] = pii
        ctx.stats["final"] = text
        ctx.stats["guardrail"] = rep
        return {"grounding_score": rep["score"], "numbers": rep["numbers"], "removed": rep.get("removed_sentences", 0),
                "banned": rep["banned_phrases"], "pii": pii}


class ReportPublisherAgent(Agent):
    name = "publisher"
    title = "Report Publisher"
    goal = "Produce Word, PowerPoint and Excel outputs with source appendix and AI disclosure."
    tools = ["python-docx", "python-pptx (native charts)", "openpyxl", "matplotlib"]
    inputs = ["final text", "scorecard", "fact sheet"]
    outputs = ["files in data/outputs", "report record (draft)"]

    def execute(self, ctx: PipelineContext):
        rtype = ctx.params.get("rtype", "exec_summary")
        spec = REPORT_TYPES[rtype]
        fs, sc = ctx.stats["facts"], ctx.stats["scorecard"]
        text = ctx.stats["final"]
        inc = ctx.params.get("include_staged", False)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{rtype}_{fs['quarter'].replace(' ', '')}_{stamp}"
        d = publish.out_dir()
        files = {}
        g = ctx.stats["guardrail"]
        meta = {"quarter": fs["quarter"], "generated": dt.datetime.now().strftime("%d %b %Y %H:%M"),
                "mode": "local LLM" if ctx.stats["mode"] == "llm" else "deterministic template",
                "grounding": f"{g['grounded']}/{g['numbers']} numbers grounded"}
        with tracer.span(self.name, "Word document") as sp:
            p = publish.build_docx(d / f"{base}.docx", f"{spec['title']} - {fs['quarter']}", text,
                                   sc if rtype in ("quarterly_brief", "board_pack", "exec_summary") else None,
                                   fs["provisional"], meta, include_charts=rtype in ("quarterly_brief", "board_pack"),
                                   include_staged=inc)
            files["docx"] = p.name
            sp.set_output(p.name)
        (d / f"{base}.md").write_text(text, encoding="utf-8")
        files["md"] = f"{base}.md"
        if rtype in ("board_pack", "exec_summary"):
            with tracer.span(self.name, "PowerPoint deck") as sp:
                bullets = [ln[2:] for ln in text.splitlines() if ln.startswith("- ")] or \
                          [f["text"] for f in fs["facts"] if f["type"] == "position"][:6]
                p = publish.build_pptx(d / f"{base}.pptx", "Peer Benchmarking", sc, fs, bullets, fs["provisional"], inc)
                files["pptx"] = p.name
                sp.set_output(p.name)
        if rtype in ("board_pack", "quarterly_brief"):
            with tracer.span(self.name, "Excel workbook") as sp:
                p = publish.build_xlsx(d / f"{base}.xlsx", sc, fs, inc)
                files["xlsx"] = p.name
                sp.set_output(p.name)
        with session_scope() as s:
            r = Report(rtype=rtype, title=f"{spec['title']} - {fs['quarter']}", quarter_key=fs["quarter_key"],
                       body_md=text, facts=fs["facts"], files=files, mode=ctx.stats["mode"],
                       model=llm.model if ctx.stats["mode"] == "llm" else None,
                       prompt_version=ctx.stats["prompt_version"], grounding_score=g["score"], guardrail=g,
                       provisional=fs["provisional"], status="draft", created_by=ctx.user, run_id=ctx.run_id)
            s.add(r)
            s.flush()
            ctx.stats["report_id"] = r.id
        return {"report_id": ctx.stats["report_id"], "files": files}
