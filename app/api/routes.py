"""REST API. Every endpoint is role-protected; OpenAPI docs at /docs.

This is also the integration contract for the Top Management Dashboard: Aditya's dashboard can call
/api/bench/* with a bearer token, or embed the UI with ?embed=1.
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..agents.orchestrator import orchestrator
from ..agents.reporting import REPORT_TYPES
from ..audit import audit, verify_chain
from ..catalog import BANKS, CATEGORIES, KPI_BY_CODE, KPIS, VISIBLE_KPIS
from ..config import settings
from ..db import (AgentRun, AgentSpan, AppLog, AuditLog, ChatMessage, DocPage, Document, Finding, KpiValue, LlmCall,
                  ModelRegistry, Report, User, now, session_scope)
from ..extraction.pdftext import is_pdf
from ..llm import llm
from ..observability import bus, log
from ..periods import current_reporting_quarter, from_key
from ..scraper.registry import load_registry
from ..security import (PASSWORD_POLICY, ROLES, CurrentUser, authenticate, consume_ticket, current_user, hash_password,
                        make_ticket, make_token, require, verify_password)
from ..services import benchmark as bm
from ..services import publish
from ..services.chatbot import SUGGESTIONS, myra

router = APIRouter(prefix="/api")


def _ip(req: Request) -> str:
    return req.client.host if req.client else "-"


# ============================================================================ auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/auth/login")
def login(body: LoginIn, request: Request):
    u, err = authenticate(body.username.strip(), body.password)
    if not u:
        audit(body.username[:64], "auth.login.failed", "user", body.username[:64], None, _ip(request))
        raise HTTPException(401, err)
    token = make_token(u.username, u.role)
    audit(u.username, "auth.login", "user", u.username, None, _ip(request))
    resp = JSONResponse({"user": CurrentUser(u.username, u.role, u.full_name).as_dict(), "token": token,
                         "default_password": verify_password(settings.DEFAULT_PASSWORD, u.password_hash)})
    resp.set_cookie("kpib_session", token, httponly=True, samesite="strict", secure=settings.COOKIE_SECURE,
                    max_age=settings.SESSION_HOURS * 3600)
    return resp


@router.post("/auth/logout")
def logout(user: CurrentUser = Depends(current_user)):
    audit(user.username, "auth.logout", "user", user.username)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("kpib_session")
    return resp


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)):
    return user.as_dict()


class PwIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/password")
def change_password(body: PwIn, user: CurrentUser = Depends(current_user)):
    if not PASSWORD_POLICY.match(body.new_password):
        raise HTTPException(400, "Password must be 8+ characters with upper, lower, digit and symbol")
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == user.username)).scalar_one()
        if not verify_password(body.old_password, u.password_hash):
            raise HTTPException(400, "Current password is incorrect")
        u.password_hash = hash_password(body.new_password)
    audit(user.username, "auth.password.changed", "user", user.username)
    return {"ok": True}


# ============================================================================ dashboard hand-off (one-click sign-in)
class HandoffIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    full_name: str | None = Field(None, max_length=120)
    tab: str = Field("overview", pattern=r"^(overview|trends|compare|reports)$")
    embed: bool = False


@router.post("/auth/handoff")
def handoff(body: HandoffIn, request: Request):
    """Server-to-server: the Top Management Dashboard vouches for a user it has already signed in and gets
    a single-use ticket (valid HANDOFF_TICKET_SECONDS) to put in the redirect URL. Never call from a browser:
    the shared secret must stay on the dashboard's server."""
    import hmac as _hmac
    import secrets as _secrets
    if len(settings.HANDOFF_SECRET) < 32:
        raise HTTPException(404, "One-click sign-in is not enabled (set HANDOFF_SECRET, 32+ characters)")
    ip = _ip(request)
    if settings.HANDOFF_ALLOWED_IPS and ip not in settings.HANDOFF_ALLOWED_IPS:
        audit("dashboard", "auth.handoff.refused", "user", body.username, {"reason": "ip not allowed"}, ip)
        raise HTTPException(403, "This address may not request sign-in tickets")
    if not _hmac.compare_digest(request.headers.get("x-handoff-secret", ""), settings.HANDOFF_SECRET):
        audit("dashboard", "auth.handoff.refused", "user", body.username, {"reason": "bad secret"}, ip)
        raise HTTPException(401, "Invalid hand-off secret")
    created = False
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
        if u is None:
            if not settings.HANDOFF_AUTO_CREATE:
                raise HTTPException(403, "Unknown user; ask the administrator to create the account")
            u = User(username=body.username, full_name=body.full_name or body.username, role="viewer", active=True,
                     password_hash=hash_password(_secrets.token_urlsafe(24)))  # signs in only via the dashboard
            s.add(u)
            s.flush()
            created = True
        if not u.active:
            raise HTTPException(403, "User is disabled")
        if u.role != "viewer" and not settings.HANDOFF_ALLOW_STAFF:
            audit("dashboard", "auth.handoff.refused", "user", body.username, {"reason": "staff account"}, ip)
            raise HTTPException(403, "Analyst, approver and administrator accounts sign in with their own password")
        username, role = u.username, u.role
    ticket = make_ticket(username, role, settings.HANDOFF_TICKET_SECONDS)
    audit("dashboard", "auth.handoff.issued", "user", username, {"auto_created": created, "role": role}, ip)
    path = f"/?ticket={ticket}{'&embed=1' if body.embed else ''}#/{body.tab}"
    return {"ticket": ticket, "expires_in": settings.HANDOFF_TICKET_SECONDS, "path": path,
            "url": str(request.base_url).rstrip("/") + path, "role": role, "user_created": created}


class ExchangeIn(BaseModel):
    ticket: str = Field(min_length=10, max_length=2000)


@router.post("/auth/exchange")
def exchange(body: ExchangeIn, request: Request):
    """Browser: swaps a hand-off ticket for a normal session (called by the web app on arrival)."""
    p = consume_ticket(body.ticket)
    if not p:
        raise HTTPException(401, "This sign-in link has expired or was already used. Open the tab again from the dashboard.")
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == p["u"])).scalar_one_or_none()
        if not u or not u.active:
            raise HTTPException(401, "User disabled")
        u.last_login = now()
        cu = CurrentUser(u.username, u.role, u.full_name)
    token = make_token(cu.username, cu.role)
    audit(cu.username, "auth.login.handoff", "user", cu.username, None, _ip(request))
    resp = JSONResponse({"user": cu.as_dict(), "token": token, "default_password": False})
    resp.set_cookie("kpib_session", token, httponly=True, samesite="strict", secure=settings.COOKIE_SECURE,
                    max_age=settings.SESSION_HOURS * 3600)
    return resp


# ============================================================================ meta
@router.get("/meta")
def meta(user: CurrentUser = Depends(require("view"))):
    reg = load_registry()
    return {
        "app": settings.APP_NAME, "version": settings.APP_VERSION, "home": bm.HOME,
        "banks": [{"code": c, **v} for c, v in BANKS.items()],
        "kpis": [k.as_dict() for k in KPIS], "categories": CATEGORIES,
        "current_quarter": current_reporting_quarter().label,
        "llm": llm.status(), "roster": orchestrator.roster(), "report_types": {k: v["title"] for k, v in REPORT_TYPES.items()},
        "sources": {c: {"name": s.name, "pages": [p.url for p in s.listing_pages], "doc_type": s.doc_type}
                    for c, s in reg.items()},
        "suggestions": SUGGESTIONS, "user": user.as_dict(),
        "dashboard": {"url": settings.DASHBOARD_URL or None, "name": settings.DASHBOARD_NAME},
    }


@router.get("/llm/status")
def llm_status(user: CurrentUser = Depends(require("view"))):
    return llm.status(force=True)


# ============================================================================ benchmark
def _inc(provisional: bool, user: CurrentUser) -> bool:
    return bool(provisional and user.role in ("admin", "maker", "checker"))


@router.get("/bench/scorecard")
def scorecard(quarter_key: int | None = None, provisional: bool = False, user: CurrentUser = Depends(require("view"))):
    return bm.scorecard(_inc(provisional, user), quarter_key)


@router.get("/bench/trend")
def trend(kpi: str, banks: str | None = None, n: int = Query(20, ge=2, le=28), provisional: bool = False,
          user: CurrentUser = Depends(require("view"))):
    if kpi not in KPI_BY_CODE:
        raise HTTPException(404, "Unknown KPI")
    bl = [b for b in (banks or "").split(",") if b in BANKS] or None
    return bm.trend(kpi, bl, n, _inc(provisional, user))


@router.get("/bench/compare")
def compare(kpi: str, quarter_key: int | None = None, provisional: bool = False,
            user: CurrentUser = Depends(require("view"))):
    if kpi not in KPI_BY_CODE:
        raise HTTPException(404, "Unknown KPI")
    vals = bm.load_values(_inc(provisional, user))
    qk = quarter_key or bm.default_quarter(vals)
    if not qk:
        return {"quarter": None, "banks": {}}
    return bm.kpi_snapshot(vals, kpi, qk)


@router.get("/bench/heatmap")
def heatmap(quarter_key: int | None = None, provisional: bool = False, user: CurrentUser = Depends(require("view"))):
    return bm.heatmap(_inc(provisional, user), quarter_key)


@router.get("/bench/facts")
def facts(quarter_key: int | None = None, provisional: bool = False, user: CurrentUser = Depends(require("view"))):
    return bm.fact_sheet(_inc(provisional, user), quarter_key)


@router.get("/bench/coverage")
def coverage(user: CurrentUser = Depends(require("view"))):
    return bm.coverage()


@router.get("/bench/lineage")
def lineage(bank: str, kpi: str, quarter_key: int, user: CurrentUser = Depends(require("view"))):
    return bm.lineage(bank, kpi, quarter_key)


@router.get("/bench/export.xlsx")
def export_xlsx(quarter_key: int | None = None, provisional: bool = False,
                user: CurrentUser = Depends(require("view"))):
    inc = _inc(provisional, user)
    sc = bm.scorecard(inc, quarter_key)
    if not sc.get("quarter"):
        raise HTTPException(404, "No data")
    p = publish.out_dir() / f"benchmark_{sc['quarter'].replace(' ', '')}.xlsx"
    publish.build_xlsx(p, sc, bm.fact_sheet(inc, quarter_key), inc)
    audit(user.username, "export.xlsx", "scorecard", sc["quarter"])
    return FileResponse(p, filename=p.name)


# ============================================================================ pipeline
class RunIn(BaseModel):
    banks: list[str] | None = None
    quarters: int = Field(20, ge=1, le=28)
    use_llm: bool = True
    offline: bool = False
    reextract: bool = False
    force_download: bool = False


@router.post("/pipeline/run")
def run_pipeline(body: RunIn, user: CurrentUser = Depends(require("run"))):
    try:
        rid = orchestrator.start_collection(user.username, body.banks, body.quarters, body.use_llm, body.offline,
                                            body.reextract, body.force_download)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": rid}


@router.get("/pipeline/status")
def pipeline_status(user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        runs = s.execute(select(AgentRun).where(AgentRun.pipeline != "chat").order_by(AgentRun.started_at.desc())
                         .limit(8)).scalars().all()
        return {"active": list(orchestrator.active.keys()), "busy": orchestrator.collect_lock.locked(),
                "runs": [_run_dict(r) for r in runs]}


@router.get("/pipeline/documents")
def documents(bank: str | None = None, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        q = select(Document).order_by(Document.bank, Document.quarter_key.desc())
        if bank:
            q = q.where(Document.bank == bank)
        out = []
        for d in s.execute(q).scalars():
            st = d.stats or {}
            out.append({"id": d.id, "bank": d.bank, "title": d.title, "doc_type": d.doc_type, "url": d.source_url,
                        "quarter": from_key(d.quarter_key).label if d.quarter_key else None,
                        "quarter_key": d.quarter_key, "status": d.status, "error": d.error, "pages": d.pages,
                        "sha256": d.sha256, "size_kb": (d.size_bytes or 0) // 1024, "tier": d.tier,
                        "kpis_found": st.get("kpis_for_own_quarter"), "candidates": st.get("candidates"),
                        "image_only_pages": len(st.get("image_only_pages") or []),
                        "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None})
        return out


@router.get("/pipeline/documents/{doc_id}/file")
def document_file(doc_id: int, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if not d or not d.file_path or not Path(d.file_path).exists():
            raise HTTPException(404, "File not available")
        path = Path(d.file_path).resolve()
    if settings.DATA_DIR.resolve() not in path.parents:
        raise HTTPException(403, "Outside data directory")
    return FileResponse(path, media_type="application/pdf", filename=path.name,
                        headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@router.get("/pipeline/documents/{doc_id}/page/{page_no}")
def document_page(doc_id: int, page_no: int, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        p = s.execute(select(DocPage).where(DocPage.doc_id == doc_id, DocPage.page_no == page_no)).scalar_one_or_none()
        if not p:
            raise HTTPException(404, "Page not found")
        return {"doc_id": doc_id, "page": page_no, "text": p.text}


@router.post("/pipeline/upload")
async def upload(bank: str = Form(...), file: UploadFile = File(...), user: CurrentUser = Depends(require("run"))):
    if bank not in BANKS:
        raise HTTPException(400, "Unknown bank")
    data = await file.read()
    if len(data) > 60 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 60 MB)")
    if not is_pdf(data):
        raise HTTPException(400, "Only PDF files are accepted")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "upload.pdf").name)[:120]
    dest = settings.INBOX_DIR / bank / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    audit(user.username, "document.upload", "inbox", f"{bank}/{name}", {"bytes": len(data)})
    log.info("Manual upload %s/%s by %s (%d KB)", bank, name, user.username, len(data) // 1024)
    return {"ok": True, "path": f"data/inbox/{bank}/{name}",
            "hint": "Run the pipeline (Offline mode is enough) to process inbox files."}


# ============================================================================ review (maker-checker)
BLOCKING_FLAGS = {"conflict", "basis_mismatch", "low_confidence", "jump", "inconsistent", "range",
                  "changed_after_approval", "llm_extracted"}


def _value_dict(v: KpiValue, docs: dict) -> dict:
    d = docs.get(v.doc_id)
    return {"id": v.id, "bank": v.bank, "kpi": v.kpi, "kpi_name": KPI_BY_CODE[v.kpi].name, "unit": KPI_BY_CODE[v.kpi].unit,
            "quarter": from_key(v.quarter_key).label, "quarter_key": v.quarter_key, "value": v.value,
            "status": v.status, "method": v.method, "confidence": v.confidence, "support": v.support,
            "flags": v.flags or [], "alternatives": v.alternatives or [], "evidence": v.evidence, "page": v.page_no,
            "doc_id": v.doc_id, "doc_title": d.title if d else None, "url": d.source_url if d else None,
            "maker": v.maker, "checker": v.checker, "override_reason": v.override_reason,
            "visible": KPI_BY_CODE[v.kpi].visible}


@router.get("/review/values")
def review_values(status: str | None = "staged", bank: str | None = None, kpi: str | None = None,
                  flag: str | None = None, quarter_key: int | None = None, limit: int = Query(300, le=3000),
                  user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        q = select(KpiValue)
        if status and status != "all":
            q = q.where(KpiValue.status == status)
        if bank:
            q = q.where(KpiValue.bank == bank)
        if kpi:
            q = q.where(KpiValue.kpi == kpi)
        if quarter_key:
            q = q.where(KpiValue.quarter_key == quarter_key)
        rows = s.execute(q.order_by(KpiValue.quarter_key.desc(), KpiValue.bank, KpiValue.kpi)).scalars().all()
        if flag == "any":
            rows = [r for r in rows if set(r.flags or []) & BLOCKING_FLAGS]
        elif flag == "clean":
            rows = [r for r in rows if not set(r.flags or []) & BLOCKING_FLAGS]
        elif flag:
            rows = [r for r in rows if flag in (r.flags or [])]
        docs = {d.id: d for d in s.execute(select(Document)).scalars()}
        counts = dict(s.execute(select(KpiValue.status, func.count()).group_by(KpiValue.status)).all())
        return {"total": len(rows), "counts": counts, "items": [_value_dict(r, docs) for r in rows[:limit]]}


class ApproveIn(BaseModel):
    ids: list[int] | None = None
    all_clean: bool = False
    bank: str | None = None
    quarter_key: int | None = None
    min_confidence: float = 0.7


@router.post("/review/approve")
def approve(body: ApproveIn, request: Request, user: CurrentUser = Depends(require("approve"))):
    approved, skipped_own = [], 0
    with session_scope() as s:
        q = select(KpiValue).where(KpiValue.status == "staged")
        if body.ids:
            q = q.where(KpiValue.id.in_(body.ids))
        elif body.all_clean:
            if body.bank:
                q = q.where(KpiValue.bank == body.bank)
            if body.quarter_key:
                q = q.where(KpiValue.quarter_key == body.quarter_key)
        else:
            raise HTTPException(400, "Provide ids or all_clean=true")
        for v in s.execute(q).scalars():
            if body.all_clean and (set(v.flags or []) & BLOCKING_FLAGS or v.confidence < body.min_confidence):
                continue
            if v.maker == f"pipeline:{user.username}" or v.maker == user.username:
                skipped_own += 1  # four-eyes: you cannot approve your own run
                continue
            v.status, v.checker, v.decided_at = "approved", user.username, now()
            approved.append((v.bank, v.kpi, v.quarter_key, v.value))
    for b, k, qk, val in approved:
        audit(user.username, "value.approve", "kpi_value", f"{b}|{k}|{qk}", {"value": val})
    audit(user.username, "review.approve.batch", "kpi_values", None,
          {"count": len(approved), "all_clean": body.all_clean, "skipped_own_run": skipped_own}, _ip(request))
    return {"approved": len(approved), "skipped_own_run": skipped_own}


class RejectIn(BaseModel):
    ids: list[int]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/review/reject")
def reject(body: RejectIn, user: CurrentUser = Depends(require("approve"))):
    done = []
    with session_scope() as s:
        for v in s.execute(select(KpiValue).where(KpiValue.id.in_(body.ids))).scalars():
            v.status, v.checker, v.decided_at, v.override_reason = "rejected", user.username, now(), body.reason
            done.append((f"{v.bank}|{v.kpi}|{v.quarter_key}", v.value))
    for key, val in done:
        audit(user.username, "value.reject", "kpi_value", key, {"value": val, "reason": body.reason})
    return {"rejected": len(done)}


class OverrideIn(BaseModel):
    id: int | None = None
    bank: str | None = None
    kpi: str | None = None
    quarter_key: int | None = None
    value: float
    reason: str = Field(min_length=5, max_length=500)


@router.post("/review/override")
def override(body: OverrideIn, user: CurrentUser = Depends(require("approve"))):
    with session_scope() as s:
        if body.id:
            v = s.get(KpiValue, body.id)
        else:
            if body.bank not in BANKS or body.kpi not in KPI_BY_CODE or not body.quarter_key:
                raise HTTPException(400, "bank, kpi and quarter_key are required for a new manual value")
            v = s.execute(select(KpiValue).where(KpiValue.bank == body.bank, KpiValue.kpi == body.kpi,
                                                 KpiValue.quarter_key == body.quarter_key)).scalar_one_or_none()
            if v is None:
                v = KpiValue(bank=body.bank, kpi=body.kpi, quarter_key=body.quarter_key, value=body.value,
                             method="manual", confidence=1.0, support=0, tier=3, maker=user.username, flags=[])
                s.add(v)
        if v is None:
            raise HTTPException(404, "Value not found")
        k = KPI_BY_CODE[v.kpi]
        if not k.in_range(body.value):
            raise HTTPException(400, f"{body.value} is outside the plausible range for {k.name}")
        old = v.value
        v.alternatives = [{"value": old, "methods": [v.method], "note": "replaced by manual override"}] + (v.alternatives or [])
        v.value, v.method, v.status, v.checker, v.decided_at = body.value, "manual", "overridden", user.username, now()
        v.override_reason, v.tier = body.reason, 3
        v.flags = sorted(set((v.flags or []) + ["manual_override"]))
        key = f"{v.bank}|{v.kpi}|{v.quarter_key}"
    audit(user.username, "value.override", "kpi_value", key, {"old": old, "new": body.value, "reason": body.reason})
    return {"ok": True}


@router.get("/review/findings")
def findings(severity: str | None = None, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        q = select(Finding).where(Finding.status == "open").order_by(Finding.id.desc())
        if severity:
            q = q.where(Finding.severity == severity)
        rows = s.execute(q.limit(800)).scalars().all()
        return [{"id": f.id, "severity": f.severity, "rule": f.rule, "message": f.message, "bank": f.bank,
                 "kpi": f.kpi, "quarter": from_key(f.quarter_key).label if f.quarter_key else None,
                 "quarter_key": f.quarter_key} for f in rows]


# ============================================================================ reports
class ReportIn(BaseModel):
    rtype: str = "exec_summary"
    quarter_key: int | None = None
    provisional: bool = False
    use_llm: bool = True


@router.post("/reports/generate")
def gen_report(body: ReportIn, user: CurrentUser = Depends(require("report"))):
    if body.rtype not in REPORT_TYPES:
        raise HTTPException(400, "Unknown report type")
    rid = orchestrator.start_report(user.username, body.rtype, body.quarter_key, _inc(body.provisional, user),
                                    body.use_llm)
    return {"run_id": rid}


@router.get("/reports")
def list_reports(user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        rows = s.execute(select(Report).order_by(Report.created_at.desc()).limit(100)).scalars().all()
        return [{"id": r.id, "type": r.rtype, "title": r.title, "quarter": from_key(r.quarter_key).label,
                 "mode": r.mode, "model": r.model, "grounding": r.grounding_score, "status": r.status,
                 "provisional": r.provisional, "files": r.files, "created_by": r.created_by,
                 "approved_by": r.approved_by, "created_at": r.created_at.isoformat(), "run_id": r.run_id}
                for r in rows]


@router.get("/reports/{rid}")
def get_report(rid: int, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        r = s.get(Report, rid)
        if not r:
            raise HTTPException(404)
        return {"id": r.id, "type": r.rtype, "title": r.title, "body_md": r.body_md, "facts": r.facts,
                "guardrail": r.guardrail, "mode": r.mode, "model": r.model, "prompt_version": r.prompt_version,
                "grounding": r.grounding_score, "status": r.status, "provisional": r.provisional, "files": r.files,
                "created_by": r.created_by, "approved_by": r.approved_by, "run_id": r.run_id}


@router.get("/reports/{rid}/download/{fmt}")
def dl_report(rid: int, fmt: str, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        r = s.get(Report, rid)
        if not r or fmt not in (r.files or {}):
            raise HTTPException(404)
        name = r.files[fmt]
    p = (settings.OUTPUT_DIR / name).resolve()
    if settings.OUTPUT_DIR.resolve() not in p.parents or not p.exists():
        raise HTTPException(404)
    audit(user.username, "report.download", "report", rid, {"format": fmt})
    return FileResponse(p, filename=name)


@router.post("/reports/{rid}/approve")
def approve_report(rid: int, user: CurrentUser = Depends(require("approve"))):
    with session_scope() as s:
        r = s.get(Report, rid)
        if not r:
            raise HTTPException(404)
        if r.created_by == user.username:
            raise HTTPException(403, "Four-eyes rule: you cannot approve a report you generated")
        if r.provisional:
            raise HTTPException(400, "Provisional reports (built on unapproved values) cannot be approved")
        r.status, r.approved_by = "approved", user.username
    audit(user.username, "report.approve", "report", rid)
    return {"ok": True}


# ============================================================================ chat
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    provisional: bool = False


@router.post("/chat")
def chat(body: ChatIn, user: CurrentUser = Depends(require("chat"))):
    return myra.answer(body.message, user.username, body.session_id, _inc(body.provisional, user))


# ============================================================================ observability
def _run_dict(r: AgentRun) -> dict:
    dur = int((r.ended_at - r.started_at).total_seconds() * 1000) if r.ended_at else None
    return {"id": r.id, "pipeline": r.pipeline, "status": r.status, "triggered_by": r.triggered_by,
            "params": r.params, "summary": r.summary, "error": r.error, "started_at": r.started_at.isoformat() + "Z",
            "ended_at": r.ended_at.isoformat() + "Z" if r.ended_at else None, "duration_ms": dur}


@router.get("/obs/runs")
def obs_runs(pipeline: str | None = None, limit: int = 40, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(min(limit, 200))
        if pipeline:
            q = q.where(AgentRun.pipeline == pipeline)
        return [_run_dict(r) for r in s.execute(q).scalars()]


@router.get("/obs/runs/{run_id}")
def obs_run(run_id: str, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        r = s.get(AgentRun, run_id)
        if not r:
            raise HTTPException(404)
        spans = s.execute(select(AgentSpan).where(AgentSpan.run_id == run_id).order_by(AgentSpan.started_at)).scalars().all()
        llm_calls = s.execute(select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.id)).scalars().all()
        return {"run": _run_dict(r),
                "spans": [{"id": sp.id, "parent_id": sp.parent_id, "agent": sp.agent, "step": sp.step, "status": sp.status,
                           "started_at": sp.started_at.isoformat() + "Z",
                           "ended_at": sp.ended_at.isoformat() + "Z" if sp.ended_at else None,
                           "duration_ms": sp.duration_ms, "input": sp.input_summary, "output": sp.output_summary,
                           "error": sp.error, "meta": sp.meta} for sp in spans],
                "llm_calls": [{"purpose": c.purpose, "model": c.model, "latency_ms": c.latency_ms, "ok": c.ok,
                               "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens,
                               "prompt_version": c.prompt_version, "error": c.error} for c in llm_calls]}


@router.get("/obs/logs")
def obs_logs(run_id: str | None = None, level: str | None = None, q: str | None = None, limit: int = 300,
             user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        qq = select(AppLog).order_by(AppLog.id.desc()).limit(min(limit, 2000))
        if run_id:
            qq = qq.where(AppLog.run_id == run_id)
        if level:
            qq = qq.where(AppLog.level == level.upper())
        if q:
            qq = qq.where(AppLog.message.ilike(f"%{q[:80]}%"))
        return [{"ts": l.ts.isoformat() + "Z", "level": l.level, "logger": l.logger, "message": l.message,
                 "run_id": l.run_id, "agent": l.agent} for l in s.execute(qq).scalars()][::-1]


@router.get("/obs/stream")
async def obs_stream(request: Request, user: CurrentUser = Depends(require("view"))):
    queue = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            for ev in bus.recent[-60:]:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _metrics() -> dict:
    with session_scope() as s:
        runs = dict(s.execute(select(AgentRun.status, func.count()).group_by(AgentRun.status)).all())
        by_pipe = dict(s.execute(select(AgentRun.pipeline, func.count()).group_by(AgentRun.pipeline)).all())
        agent_rows = s.execute(select(AgentSpan.agent, func.count(), func.avg(AgentSpan.duration_ms),
                                      func.sum(AgentSpan.status == "error")).group_by(AgentSpan.agent)).all()
        lat = [x for (x,) in s.execute(select(LlmCall.latency_ms).order_by(LlmCall.id.desc()).limit(500)).all()]
        llm_ok = s.execute(select(func.count()).where(LlmCall.ok.is_(True))).scalar() or 0
        llm_all = s.execute(select(func.count(LlmCall.id))).scalar() or 0
        docs = dict(s.execute(select(Document.status, func.count()).group_by(Document.status)).all())
        vals = dict(s.execute(select(KpiValue.status, func.count()).group_by(KpiValue.status)).all())
        fnd = dict(s.execute(select(Finding.severity, func.count()).where(Finding.status == "open")
                             .group_by(Finding.severity)).all())
        chats = s.execute(select(func.count(ChatMessage.id), func.avg(ChatMessage.latency_ms))).one()
        grounded = s.execute(select(func.avg(Report.grounding_score))).scalar()
        errors24 = s.execute(select(func.count(AppLog.id)).where(AppLog.level.in_(["ERROR", "CRITICAL"]))).scalar() or 0
        warn = s.execute(select(func.count(AppLog.id)).where(AppLog.level == "WARNING")).scalar() or 0
    lat_sorted = sorted(lat)

    def pct(p):
        return lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * p))] if lat_sorted else None

    return {"runs": runs, "runs_by_pipeline": by_pipe,
            "agents": [{"agent": a, "spans": n, "avg_ms": round(avg or 0), "errors": int(err or 0)}
                       for a, n, avg, err in agent_rows],
            "llm": {"calls": llm_all, "ok": llm_ok, "p50_ms": pct(0.5), "p95_ms": pct(0.95),
                    "mean_ms": round(statistics.mean(lat)) if lat else None},
            "documents": docs, "values": vals, "findings": fnd,
            "chat": {"questions": chats[0], "avg_latency_ms": round(chats[1] or 0)},
            "reports_avg_grounding": round(grounded, 3) if grounded is not None else None,
            "log_errors": errors24, "log_warnings": warn, "sse_clients": len(bus._subs)}


@router.get("/obs/metrics")
def obs_metrics(user: CurrentUser = Depends(require("view"))):
    return _metrics()


@router.get("/obs/llm")
def obs_llm(limit: int = 100, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        rows = s.execute(select(LlmCall).order_by(LlmCall.id.desc()).limit(min(limit, 500))).scalars().all()
        return [{"ts": c.created_at.isoformat() + "Z", "purpose": c.purpose, "model": c.model, "latency_ms": c.latency_ms,
                 "ok": c.ok, "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens,
                 "prompt_version": c.prompt_version, "run_id": c.run_id, "error": c.error} for c in rows]


# ============================================================================ governance
@router.get("/gov/audit")
def gov_audit(limit: int = 300, user: CurrentUser = Depends(require("view"))):
    with session_scope() as s:
        rows = s.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 3000))).scalars().all()
        return [{"id": a.id, "ts": a.ts.isoformat() + "Z", "actor": a.actor, "action": a.action, "entity": a.entity,
                 "entity_id": a.entity_id, "details": a.details, "ip": a.ip, "hash": a.hash[:16],
                 "prev": a.prev_hash[:16]} for a in rows]


@router.get("/gov/audit/verify")
def gov_verify(user: CurrentUser = Depends(require("view"))):
    res = verify_chain()
    audit(user.username, "audit.verify", "audit_log", None, {"ok": res["ok"], "rows": res["rows"]})
    return res


@router.get("/gov/models")
def gov_models(user: CurrentUser = Depends(require("view"))):
    st = llm.status()
    with session_scope() as s:
        rows = s.execute(select(ModelRegistry)).scalars().all()
        return {"status": st, "registry": [{"name": r.name, "provider": r.provider, "purpose": r.purpose,
                                            "hosting": r.hosting, "risk_tier": r.risk_tier, "status": r.status,
                                            "approved_by": r.approved_by} for r in rows]}


@router.get("/gov/prompts")
def gov_prompts(user: CurrentUser = Depends(require("view"))):
    from ..agents.reporting import SYSTEM as WRITER_SYSTEM
    from ..extraction.llm_extract import SYSTEM as EXTRACT_SYSTEM
    from ..llm import prompt_version
    items = [("Insight Writer (system)", WRITER_SYSTEM), ("Extraction fallback (system)", EXTRACT_SYSTEM)]
    for k, v in REPORT_TYPES.items():
        items.append((f"Report spec: {k}", v["spec"]))
    return [{"name": n, "version": prompt_version(t), "text": t} for n, t in items]


@router.get("/gov/kpis")
def gov_kpis(user: CurrentUser = Depends(require("view"))):
    return [k.as_dict() for k in KPIS]


@router.get("/gov/users")
def gov_users(user: CurrentUser = Depends(require("admin"))):
    with session_scope() as s:
        return [{"username": u.username, "full_name": u.full_name, "role": u.role, "role_label": ROLES[u.role]["label"],
                 "active": u.active, "last_login": u.last_login.isoformat() if u.last_login else None}
                for u in s.execute(select(User)).scalars()]


def prometheus_text() -> str:
    m = _metrics()
    lines = ["# HELP kpib_runs_total Pipeline runs by status", "# TYPE kpib_runs_total counter"]
    for st, n in m["runs"].items():
        lines.append(f'kpib_runs_total{{status="{st}"}} {n}')
    lines += ["# TYPE kpib_agent_avg_ms gauge"]
    for a in m["agents"]:
        lines.append(f'kpib_agent_avg_ms{{agent="{a["agent"]}"}} {a["avg_ms"]}')
        lines.append(f'kpib_agent_errors_total{{agent="{a["agent"]}"}} {a["errors"]}')
    lines.append(f"kpib_llm_calls_total {m['llm']['calls']}")
    if m["llm"]["p95_ms"] is not None:
        lines.append(f"kpib_llm_latency_p95_ms {m['llm']['p95_ms']}")
    for st, n in m["values"].items():
        lines.append(f'kpib_kpi_values{{status="{st}"}} {n}')
    for st, n in m["documents"].items():
        lines.append(f'kpib_documents{{status="{st}"}} {n}')
    return "\n".join(lines) + "\n"


health_router = APIRouter()


@health_router.get("/healthz")
def healthz():
    return {"ok": True, "version": settings.APP_VERSION, "llm": llm.status().get("available")}


@health_router.get("/metrics", response_class=PlainTextResponse)
def metrics_prom(request: Request):
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Prometheus metrics are only served to localhost")
    return prometheus_text()
