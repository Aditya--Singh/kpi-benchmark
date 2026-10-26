"""Observability: structured logging, agent tracing (runs + spans), live event bus (SSE) and metrics.

Every agent step is wrapped in `tracer.span(...)`. A span records start/end, duration, a short input
and output summary, errors and metadata. Spans and log lines are (1) stored in SQLite, (2) appended to
data/logs/app.jsonl and (3) pushed to every open browser via Server-Sent Events, which is what makes
the Observability screen 'live'.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from .config import settings
from .db import AgentRun, AgentSpan, AppLog, LlmCall, now, session_scope

current_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_run", default=None)
current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span", default=None)
current_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_agent", default=None)


# ----------------------------------------------------------------------------- event bus (SSE)
class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self.recent: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, qq) for lp, qq in self._subs if qq is not q]

    def publish(self, event: dict) -> None:
        event.setdefault("ts", now().isoformat() + "Z")
        with self._lock:
            self.recent.append(event)
            self.recent = self.recent[-300:]
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, q, event)
            except RuntimeError:
                pass


def _safe_put(q: asyncio.Queue, ev: dict) -> None:
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        pass


bus = EventBus()


# ----------------------------------------------------------------------------- logging
class DbAndBusHandler(logging.Handler):
    """Pushes log lines to the live bus immediately and to SQLite via a background writer thread
    (never inside the caller's transaction - avoids SQLite write-lock contention)."""

    def __init__(self) -> None:
        super().__init__()
        import queue
        self.q: "queue.Queue[dict]" = queue.Queue(maxsize=20000)
        threading.Thread(target=self._writer, name="log-writer", daemon=True).start()

    def _writer(self) -> None:
        import queue
        while True:
            batch = [self.q.get()]
            try:
                while len(batch) < 200:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            for attempt in range(3):
                try:
                    with session_scope() as s:
                        s.add_all([AppLog(**b) for b in batch])
                    break
                except Exception:
                    time.sleep(0.5 * (attempt + 1))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("sqlalchemy", "uvicorn.access", "httpx", "httpcore", "pdfminer", "multipart")):
            return
        try:
            msg = record.getMessage()
            run_id = getattr(record, "run_id", None) or current_run.get()
            agent = getattr(record, "agent", None) or current_agent.get()
            row = {"ts": now(), "level": record.levelname, "logger": record.name[:48], "message": msg[:4000],
                   "run_id": run_id, "agent": agent, "extra": getattr(record, "extra_data", None)}
            try:
                self.q.put_nowait(row)
            except Exception:
                pass
            bus.publish({"type": "log", "level": record.levelname, "logger": record.name, "message": msg[:1000],
                         "run_id": run_id, "agent": agent})
        except Exception:  # logging must never crash the app
            pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": now().isoformat() + "Z", "level": record.levelname, "logger": record.name,
            "msg": record.getMessage(), "run_id": current_run.get(), "agent": current_agent.get(),
        }, default=str)


def setup_logging() -> None:
    settings.ensure_dirs()
    root = logging.getLogger()
    if getattr(root, "_kpibench_configured", False):
        return
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(settings.LOG_DIR / "app.jsonl", maxBytes=20_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(sh)
    root.addHandler(DbAndBusHandler())
    for noisy in ("pdfminer", "httpx", "httpx2", "httpcore", "multipart", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._kpibench_configured = True  # type: ignore[attr-defined]


log = logging.getLogger("kpibench")


# ----------------------------------------------------------------------------- tracing
def _short(x, n: int = 600) -> str | None:
    if x is None:
        return None
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


class SpanHandle:
    def __init__(self, span_id: str):
        self.id = span_id
        self.output = None
        self.meta: dict = {}

    def set_output(self, out) -> None:
        self.output = out

    def add_meta(self, **kw) -> None:
        self.meta.update(kw)


class Tracer:
    def start_run(self, pipeline: str, triggered_by: str | None, params: dict | None = None) -> str:
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        with session_scope() as s:
            s.add(AgentRun(id=run_id, pipeline=pipeline, triggered_by=triggered_by, params=params))
        bus.publish({"type": "run", "event": "start", "run_id": run_id, "pipeline": pipeline, "by": triggered_by})
        return run_id

    def end_run(self, run_id: str, status: str = "succeeded", summary: dict | None = None, error: str | None = None):
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            if r:
                r.status, r.summary, r.error, r.ended_at = status, summary, error, now()
        bus.publish({"type": "run", "event": "end", "run_id": run_id, "status": status, "summary": summary})

    @contextmanager
    def span(self, agent: str, step: str, input=None, run_id: str | None = None):
        run_id = run_id or current_run.get()
        span_id = uuid.uuid4().hex[:16]
        parent = current_span.get()
        t0 = time.perf_counter()
        with session_scope() as s:
            s.add(AgentSpan(id=span_id, run_id=run_id or "adhoc", parent_id=parent, agent=agent, step=step[:200],
                            input_summary=_short(input)))
        bus.publish({"type": "span", "event": "start", "span_id": span_id, "parent_id": parent, "run_id": run_id,
                     "agent": agent, "step": step})
        tok_span = current_span.set(span_id)
        tok_agent = current_agent.set(agent)
        tok_run = current_run.set(run_id)
        handle = SpanHandle(span_id)
        status, err = "ok", None
        try:
            yield handle
        except Exception as exc:
            status = "error"
            err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
            raise
        finally:
            dur = int((time.perf_counter() - t0) * 1000)
            current_span.reset(tok_span)
            current_agent.reset(tok_agent)
            current_run.reset(tok_run)
            with session_scope() as s:
                sp = s.get(AgentSpan, span_id)
                if sp:
                    sp.status, sp.ended_at, sp.duration_ms = status, now(), dur
                    sp.output_summary = _short(handle.output)
                    sp.error = err
                    sp.meta = handle.meta or None
            bus.publish({"type": "span", "event": "end", "span_id": span_id, "run_id": run_id, "agent": agent,
                         "step": step, "status": status, "duration_ms": dur, "output": _short(handle.output, 300),
                         "error": err[:300] if err else None})


tracer = Tracer()


def record_llm_call(**kw) -> None:
    with session_scope() as s:
        s.add(LlmCall(run_id=current_run.get(), span_id=current_span.get(), **kw))
    bus.publish({"type": "llm", "run_id": current_run.get(), "purpose": kw.get("purpose"),
                 "model": kw.get("model"), "latency_ms": kw.get("latency_ms"), "ok": kw.get("ok")})


def run_in_context(run_id: str, fn, *args, **kwargs):
    """Execute fn with current_run set (used by background threads)."""
    tok = current_run.set(run_id)
    try:
        return fn(*args, **kwargs)
    finally:
        current_run.reset(tok)
