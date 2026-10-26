"""Application entry point: python -m app.main  (or: uvicorn app.main:app)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api.routes import health_router, router
from .audit import audit
from .config import settings
from .db import ModelRegistry, init_db, session_scope
from .llm import llm
from .observability import log, setup_logging
from .security import security_headers, seed_users


def bootstrap() -> None:
    settings.ensure_dirs()
    init_db()
    setup_logging()
    created = seed_users()
    if created:
        log.warning("Created default users %s with the default password from .env - change them after first login",
                    created)
        audit("system", "users.seeded", "user", None, {"users": created})
    with session_scope() as s:
        if not s.execute(select(ModelRegistry)).scalars().first():
            s.add_all([
                ModelRegistry(name=settings.OLLAMA_MODEL, provider="ollama", hosting="on-premises (GB10, no egress)",
                              purpose="Narratives, chat answers, extraction fallback (grounded)", risk_tier="Medium",
                              approved_by="pending"),
                ModelRegistry(name=settings.EMBED_MODEL, provider="ollama", hosting="on-premises (GB10, no egress)",
                              purpose="Semantic search embeddings for Myra", risk_tier="Low", approved_by="pending"),
                ModelRegistry(name="rule-based extractor v1", provider="in-house", hosting="application",
                              purpose="Primary KPI extraction (tables, charts, text)", risk_tier="Low",
                              approved_by="pending"),
            ])
    # runs left 'running' by a previous stop of the server are marked interrupted
    from .db import AgentRun, AgentSpan, now
    with session_scope() as s:
        stale = s.execute(select(AgentRun).where(AgentRun.status == "running")).scalars().all()
        for r in stale:
            r.status, r.ended_at, r.error = "interrupted", now(), "Server stopped while this run was in progress"
        for sp in s.execute(select(AgentSpan).where(AgentSpan.status == "running")).scalars():
            sp.status, sp.ended_at = "interrupted", now()
    if stale:
        log.warning("Marked %d unfinished run(s) from a previous server session as interrupted", len(stale))
    st = llm.status(force=True)
    log.info("LLM status: %s", st.get("reason") or f"{st['model']} available")


app = FastAPI(title="PNB KPI / Peer Benchmarking", version=settings.APP_VERSION,
              description="Agentic peer-benchmarking platform: scraping, extraction, validation, maker-checker, "
                          "benchmarking, grounded narratives, chatbot and observability.")


@app.middleware("http")
async def headers_mw(request: Request, call_next):
    try:
        resp = await call_next(request)
    except Exception as exc:  # pragma: no cover
        log.exception("Unhandled error on %s: %s", request.url.path, exc)
        resp = JSONResponse({"detail": "Internal error - see Observability logs"}, status_code=500)
    for k, v in security_headers().items():
        if k == "Cache-Control" and request.url.path.startswith("/static/"):
            continue
        resp.headers.setdefault(k, v)
    return resp


app.include_router(router)
app.include_router(health_router)
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(settings.STATIC_DIR / "index.html")


@app.on_event("startup")
def _startup() -> None:
    bootstrap()
    log.info("%s %s ready on http://%s:%s", settings.APP_NAME, settings.APP_VERSION, settings.HOST, settings.PORT)


@app.on_event("shutdown")
def _shutdown() -> None:
    """Ctrl+C must stop the app even while an agent is mid-run: record the interruption, then exit
    without waiting for background pipeline threads (which can be inside a long model call)."""
    import os
    try:
        from .db import AgentRun, now
        with session_scope() as s:
            for r in s.execute(select(AgentRun).where(AgentRun.status == "running")).scalars():
                r.status, r.ended_at, r.error = "interrupted", now(), "Server stopped while this run was in progress"
    except Exception:  # pragma: no cover
        pass
    if os.environ.get("KPIB_HARD_EXIT") == "1":  # only when started as the real server (python -m app.main)
        print("PNB Peer Benchmarking stopped.", flush=True)
        os._exit(0)


if __name__ == "__main__":
    import uvicorn

    import os

    os.environ["KPIB_HARD_EXIT"] = "1"
    # timeout_graceful_shutdown: do not wait forever for open live-feed (SSE) connections
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, log_level="info",
                timeout_graceful_shutdown=3)
