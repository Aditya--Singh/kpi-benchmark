"""Orchestrator: runs agent pipelines in the background and keeps the agent roster.

Pipelines
  collect : Source Scout -> Document Fetcher -> KPI Extractor -> Knowledge Indexer -> Validation & Reconciliation
            (then STOP: a human checker must approve values - the human-in-the-loop gate)
  report  : Benchmark Analyst -> Insight Writer -> Compliance Guardrail -> Report Publisher
  chat    : Myra (guard -> understand -> tools -> retrieve -> compose -> guardrail)
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from ..audit import audit
from ..catalog import BANKS
from ..config import settings
from ..observability import current_run, log, tracer
from ..periods import Quarter, current_reporting_quarter, from_key, quarter_window
from ..services.retrieval import index_embeddings
from .base import Agent, PipelineContext
from .collection import DocumentFetcherAgent, KpiExtractorAgent, SourceScoutAgent
from .reconcile import ReconcilerAgent
from .reporting import BenchmarkAnalystAgent, GuardrailAgent, InsightWriterAgent, ReportPublisherAgent


class KnowledgeIndexerAgent(Agent):
    name = "indexer"
    title = "Knowledge Indexer"
    goal = "Make every page searchable for Myra: page chunks for BM25, plus embeddings if a local embedding model exists."
    tools = ["BM25 index", "Ollama embeddings (optional)"]
    inputs = ["page text chunks"]
    outputs = ["searchable knowledge base"]

    def execute(self, ctx: PipelineContext):
        n = index_embeddings()
        return {"embeddings_added": n, "lexical_index": "BM25 (built on demand)"}


class MyraCard(Agent):
    name = "myra"
    title = "Myra - Conversational Analyst"
    goal = "Answer top-management questions from approved data and source documents, with citations."
    tools = ["benchmark engine", "hybrid retrieval", "local LLM", "numeric guardrail"]
    inputs = ["question"]
    outputs = ["grounded answer + sources + chart"]


COLLECT = [SourceScoutAgent, DocumentFetcherAgent, KpiExtractorAgent, KnowledgeIndexerAgent, ReconcilerAgent]
REPORT = [BenchmarkAnalystAgent, InsightWriterAgent, GuardrailAgent, ReportPublisherAgent]
ROSTER = COLLECT + REPORT + [MyraCard]


class Orchestrator:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="agents")
        self.collect_lock = threading.Lock()
        self.active: dict[str, dict] = {}

    def roster(self) -> list[dict]:
        return [a.card() for a in ROSTER]

    def _window(self, quarters: int) -> list[Quarter]:
        latest = current_reporting_quarter()
        return quarter_window(latest, max(1, min(28, quarters)))

    def start_collection(self, user: str, banks: list[str] | None, quarters: int, use_llm: bool,
                         offline: bool = False, reextract: bool = False, force_download: bool = False) -> str:
        if self.collect_lock.locked():
            raise RuntimeError("A data collection run is already in progress")
        banks = [b for b in (banks or list(BANKS)) if b in BANKS]
        params = {"banks": banks, "quarters": quarters, "use_llm": use_llm, "offline": offline,
                  "reextract": reextract, "force_download": force_download}
        run_id = tracer.start_run("collect", user, params)
        win = self._window(quarters)
        ctx = PipelineContext(run_id=run_id, user=user, params=params, banks=banks, window=win)
        audit(user, "pipeline.collect.start", "run", run_id, params)
        self.active[run_id] = {"pipeline": "collect", "ctx": ctx}
        self.pool.submit(self._run, ctx, COLLECT, "collect")
        return run_id

    def start_report(self, user: str, rtype: str, quarter_key: int | None, include_staged: bool, use_llm: bool) -> str:
        params = {"rtype": rtype, "quarter_key": quarter_key, "include_staged": include_staged, "use_llm": use_llm}
        run_id = tracer.start_run("report", user, params)
        ctx = PipelineContext(run_id=run_id, user=user, params=params, banks=list(BANKS))
        audit(user, "pipeline.report.start", "run", run_id, params)
        self.active[run_id] = {"pipeline": "report", "ctx": ctx}
        self.pool.submit(self._run, ctx, REPORT, "report")
        return run_id

    def run_sync(self, ctx: PipelineContext, agents, pipeline: str) -> dict:
        return self._run(ctx, agents, pipeline)

    def _run(self, ctx: PipelineContext, agents, pipeline: str) -> dict:
        tok = current_run.set(ctx.run_id)
        lock = self.collect_lock if pipeline == "collect" else None
        if lock:
            lock.acquire()
        try:
            log.info("Pipeline '%s' started for banks %s, window %s..%s", pipeline, ctx.banks,
                     ctx.window[0].label if ctx.window else "-", ctx.window[-1].label if ctx.window else "-")
            for agent_cls in agents:
                agent_cls().run(ctx)
            summary = {k: v for k, v in ctx.stats.items() if k not in ("scorecard", "facts", "draft", "final", "guardrail")}
            if pipeline == "report":
                summary["report_id"] = ctx.stats.get("report_id")
                summary["grounding"] = (ctx.stats.get("guardrail") or {}).get("score")
                summary["mode"] = ctx.stats.get("mode")
            tracer.end_run(ctx.run_id, "succeeded", summary)
            audit(ctx.user, f"pipeline.{pipeline}.done", "run", ctx.run_id, summary)
            log.info("Pipeline '%s' finished: %s", pipeline, summary)
            return summary
        except Exception as exc:
            log.exception("Pipeline '%s' failed: %s", pipeline, exc)
            tracer.end_run(ctx.run_id, "failed", error=str(exc))
            audit(ctx.user, f"pipeline.{pipeline}.failed", "run", ctx.run_id, {"error": str(exc)[:300]})
            return {"error": str(exc)}
        finally:
            if lock:
                lock.release()
            current_run.reset(tok)
            self.active.pop(ctx.run_id, None)


orchestrator = Orchestrator()


def latest_quarter_label() -> str:
    return current_reporting_quarter().label


__all__ = ["orchestrator", "ROSTER", "from_key", "settings"]
