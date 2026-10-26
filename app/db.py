"""Database layer (SQLAlchemy 2). SQLite by default; set DB_URL for PostgreSQL/Oracle."""
from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager

from sqlalchemy import (JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

settings.ensure_dirs()
engine = create_engine(
    settings.DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.DB_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
    json_serializer=lambda o: json.dumps(o, default=str),
)

if settings.DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))  # admin | maker | checker | viewer
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)
    last_login: Mapped[dt.datetime | None] = mapped_column(nullable=True)


# ----------------------------------------------------------------------------- sources
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank: Mapped[str] = mapped_column(String(16), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))  # analyst_presentation | press_release | manual_upload
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarter_key: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[int] = mapped_column(Integer, default=2)  # 1 audited results, 2 IR decks/press, 3 manual
    status: Mapped[str] = mapped_column(String(24), default="discovered")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[dt.datetime] = mapped_column(default=now)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    extracted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (UniqueConstraint("bank", "source_url", name="uq_doc_url"),)


class DocPage(Base):
    __tablename__ = "doc_pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Chunk(Base):
    """Retrieval chunks for the chatbot (page windows) + optional embedding."""
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    bank: Mapped[str] = mapped_column(String(16), index=True)
    quarter_key: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)


# ----------------------------------------------------------------------------- KPI data
class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=True)
    bank: Mapped[str] = mapped_column(String(16))
    kpi: Mapped[str] = mapped_column(String(32))
    quarter_key: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(16))  # table|chart|keyvalue|sentence|llm|derived|manual
    confidence: Mapped[float] = mapped_column(Float)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)
    __table_args__ = (Index("ix_cand_bkq", "bank", "kpi", "quarter_key"),)


class KpiValue(Base):
    __tablename__ = "kpi_values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank: Mapped[str] = mapped_column(String(16))
    kpi: Mapped[str] = mapped_column(String(32))
    quarter_key: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="staged")  # staged|approved|rejected|overridden
    method: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    support: Mapped[int] = mapped_column(Integer, default=1)  # independent readings agreeing
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    tier: Mapped[int] = mapped_column(Integer, default=2)
    flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    alternatives: Mapped[list | None] = mapped_column(JSON, nullable=True)
    maker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(default=now, onupdate=now)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    __table_args__ = (UniqueConstraint("bank", "kpi", "quarter_key", name="uq_kpi_value"),)


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    bank: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kpi: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quarter_key: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(8))  # info|warn|error
    rule: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


# ----------------------------------------------------------------------------- outputs
class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rtype: Mapped[str] = mapped_column(String(32))  # exec_summary|quarterly_brief|speech|insights|deck|report
    title: Mapped[str] = mapped_column(String(300))
    quarter_key: Mapped[int] = mapped_column(Integer)
    body_md: Mapped[str] = mapped_column(Text)
    facts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    files: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mode: Mapped[str] = mapped_column(String(16))  # llm | template
    model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    grounding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    guardrail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    username: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


# ----------------------------------------------------------------------------- observability
class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    pipeline: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(default=now)
    ended_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentSpan(Base):
    __tablename__ = "agent_spans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent: Mapped[str] = mapped_column(String(48))
    step: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[dt.datetime] = mapped_column(default=now)
    ended_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    purpose: Mapped[str] = mapped_column(String(48))
    provider: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(96))
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


class AppLog(Base):
    __tablename__ = "app_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(default=now, index=True)
    level: Mapped[str] = mapped_column(String(8))
    logger: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    agent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# ----------------------------------------------------------------------------- governance
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(default=now)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(96))
    provider: Mapped[str] = mapped_column(String(24))
    purpose: Mapped[str] = mapped_column(String(200))
    hosting: Mapped[str] = mapped_column(String(64))
    risk_tier: Mapped[str] = mapped_column(String(16))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_seen: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
