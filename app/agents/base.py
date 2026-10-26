"""Agent base class and the shared pipeline context.

An *agent* here is a focused, single-responsibility worker with a name, a goal, declared inputs and
outputs, and tools it may use. The Orchestrator runs agents in sequence, passing a shared
PipelineContext (a typed blackboard). Every agent step is traced, so the Observability screen shows
which agent is working, on what, for how long, and what it produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..observability import log, tracer
from ..periods import Quarter


@dataclass
class PipelineContext:
    run_id: str
    user: str
    params: dict
    banks: list[str]
    window: list[Quarter] = field(default_factory=list)
    doc_ids: list[int] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    cancelled: bool = False

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


class Agent:
    name = "agent"
    title = "Agent"
    goal = ""
    tools: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []

    def run(self, ctx: PipelineContext) -> None:
        with tracer.span(self.name, f"{self.title}: start", input={"banks": ctx.banks, "params": ctx.params}) as sp:
            log.info("%s started", self.title)
            result = self.execute(ctx)
            sp.set_output(result)
            log.info("%s finished: %s", self.title, result)

    def execute(self, ctx: PipelineContext):  # pragma: no cover - overridden
        raise NotImplementedError

    @classmethod
    def card(cls) -> dict:
        return {"name": cls.name, "title": cls.title, "goal": cls.goal, "tools": cls.tools,
                "inputs": cls.inputs, "outputs": cls.outputs}
