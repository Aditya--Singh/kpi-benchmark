"""Tamper-evident audit trail.

Each audit row stores hash = SHA256(prev_hash + canonical JSON of the row). Changing or deleting any
historical row breaks the chain from that point, which `verify_chain()` detects and reports.
"""
from __future__ import annotations

import hashlib
import json
import threading

from sqlalchemy import select

from .db import AuditLog, now, session_scope

_lock = threading.Lock()
GENESIS = "0" * 64


def _digest(prev: str, ts: str, actor: str, action: str, entity, entity_id, details) -> str:
    payload = json.dumps({"prev": prev, "ts": ts, "actor": actor, "action": action, "entity": entity,
                          "entity_id": entity_id, "details": details}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(actor: str, action: str, entity: str | None = None, entity_id=None, details: dict | None = None,
          ip: str | None = None) -> None:
    with _lock, session_scope() as s:
        last = s.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
        prev = last.hash if last else GENESIS
        ts = now()
        eid = None if entity_id is None else str(entity_id)
        h = _digest(prev, ts.isoformat(), actor, action, entity, eid, details)
        s.add(AuditLog(ts=ts, actor=actor, action=action, entity=entity, entity_id=eid, details=details, ip=ip,
                       prev_hash=prev, hash=h))


def verify_chain() -> dict:
    with session_scope() as s:
        rows = s.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
        prev = GENESIS
        for r in rows:
            expect = _digest(prev, r.ts.isoformat(), r.actor, r.action, r.entity, r.entity_id, r.details)
            if r.prev_hash != prev or r.hash != expect:
                return {"ok": False, "rows": len(rows), "broken_at": r.id,
                        "message": f"Audit chain broken at entry #{r.id} ({r.action} by {r.actor})."}
            prev = r.hash
        return {"ok": True, "rows": len(rows), "head": prev,
                "message": f"All {len(rows)} audit entries verified; chain intact."}
