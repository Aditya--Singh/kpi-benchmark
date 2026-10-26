"""Security: password hashing, signed session tokens, role-based access control, PII redaction,
prompt-injection screening and HTTP security headers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from .config import settings
from .db import Setting, User, now, session_scope

ROLES = {
    "admin": {"label": "Administrator", "can": {"view", "run", "approve", "admin", "report", "chat"}},
    "maker": {"label": "Analyst (Maker)", "can": {"view", "run", "report", "chat"}},
    "checker": {"label": "Approver (Checker)", "can": {"view", "approve", "report", "chat"}},
    "viewer": {"label": "Top Management (Viewer)", "can": {"view", "chat"}},
}


# ----------------------------------------------------------------------------- secret key
def _secret() -> bytes:
    if settings.SECRET_KEY:
        return settings.SECRET_KEY.encode()
    with session_scope() as s:
        row = s.get(Setting, "secret_key")
        if not row:
            row = Setting(key="secret_key", value={"v": secrets.token_hex(32)})
            s.add(row)
        return row.value["v"].encode()


_SECRET_CACHE: bytes | None = None


def secret() -> bytes:
    global _SECRET_CACHE
    if _SECRET_CACHE is None:
        _SECRET_CACHE = _secret()
    return _SECRET_CACHE


# ----------------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return "pbkdf2_sha256$240000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


PASSWORD_POLICY = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")


# ----------------------------------------------------------------------------- tokens
def make_token(username: str, role: str) -> str:
    payload = {"u": username, "r": role, "exp": int(time.time()) + settings.SESSION_HOURS * 3600,
               "n": secrets.token_hex(6)}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def make_ticket(username: str, role: str, seconds: int) -> str:
    """Short-lived, single-use sign-in ticket issued to the Top Management Dashboard (never a session)."""
    payload = {"u": username, "r": role, "exp": int(time.time()) + max(15, seconds), "n": secrets.token_hex(12),
               "t": "ticket"}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


_used_tickets: dict[str, float] = {}


def consume_ticket(ticket: str) -> dict | None:
    p = read_token(ticket)
    if not p or p.get("t") != "ticket":
        return None
    nowt = time.time()
    for k in [k for k, exp in _used_tickets.items() if exp < nowt]:
        _used_tickets.pop(k, None)
    if p["n"] in _used_tickets:
        return None  # single use
    _used_tickets[p["n"]] = p["exp"]
    return p


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
        exp_sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, exp_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


class CurrentUser:
    def __init__(self, username: str, role: str, full_name: str = ""):
        self.username, self.role, self.full_name = username, role, full_name

    def can(self, perm: str) -> bool:
        return perm in ROLES.get(self.role, {}).get("can", set())

    def as_dict(self) -> dict:
        return {"username": self.username, "role": self.role, "role_label": ROLES[self.role]["label"],
                "full_name": self.full_name, "permissions": sorted(ROLES[self.role]["can"])}


def current_user(request: Request) -> CurrentUser:
    token = request.cookies.get("kpib_session")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("token")  # used by EventSource (cannot set headers) in embed mode
    payload = read_token(token) if token else None
    if not payload or payload.get("t") == "ticket":  # a sign-in ticket is not a session
        raise HTTPException(status_code=401, detail="Not signed in")
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == payload["u"])).scalar_one_or_none()
        if not u or not u.active:
            raise HTTPException(status_code=401, detail="User disabled")
        return CurrentUser(u.username, u.role, u.full_name)


def require(perm: str):
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.can(perm):
            raise HTTPException(status_code=403, detail=f"Your role ({user.role}) cannot perform '{perm}'")
        return user
    return dep


# ----------------------------------------------------------------------------- login throttling
LOCK_AFTER = 5
LOCK_MINUTES = 15


def authenticate(username: str, password: str) -> tuple[User | None, str | None]:
    import datetime as dt
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not u or not u.active:
            return None, "Invalid username or password"
        if u.locked_until and u.locked_until > now():
            return None, "Account locked after repeated failures. Try again later."
        if not verify_password(password, u.password_hash):
            u.failed_attempts += 1
            if u.failed_attempts >= LOCK_AFTER:
                u.locked_until = now() + dt.timedelta(minutes=LOCK_MINUTES)
                u.failed_attempts = 0
            return None, "Invalid username or password"
        u.failed_attempts, u.locked_until, u.last_login = 0, None, now()
        s.flush()
        s.expunge(u)
        return u, None


DEFAULT_USERS = [
    ("admin", "System Administrator", "admin"),
    ("analyst", "DASID Analyst (Maker)", "maker"),
    ("approver", "DASID Approver (Checker)", "checker"),
    ("gm", "General Manager (Viewer)", "viewer"),
]


def seed_users() -> list[str]:
    created = []
    with session_scope() as s:
        for username, name, role in DEFAULT_USERS:
            exists = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if not exists:
                s.add(User(username=username, full_name=name, role=role,
                           password_hash=hash_password(settings.DEFAULT_PASSWORD)))
                created.append(username)
    return created


# ----------------------------------------------------------------------------- PII + injection
PII_PATTERNS = [
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("AADHAAR", re.compile(r"\b(?!(?:19|20)\d{2}\s(?:19|20)\d{2}\s(?:19|20)\d{2}\b)[2-9]\d{3}\s?\d{4}\s?\d{4}\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")),
    ("ACCOUNT", re.compile(r"\b\d{11,18}\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
]


def redact_pii(text: str) -> tuple[str, list[str]]:
    found = []
    for name, rx in PII_PATTERNS:
        if rx.search(text):
            found.append(name)
            text = rx.sub(f"[{name}-REDACTED]", text)
    return text, found


INJECTION_PATTERNS = re.compile(
    r"ignore (all|any|the|previous|prior) (instructions|rules)|disregard (the )?(system|previous)|"
    r"you are now|act as (?!an analyst)|reveal (your|the) (system )?prompt|jailbreak|developer mode|"
    r"print (your|the) (instructions|prompt)|override (safety|guardrails)", re.I)


def injection_risk(text: str) -> bool:
    return bool(INJECTION_PATTERNS.search(text or ""))


def is_local_url(url: str) -> bool:
    return bool(re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", url))


# ----------------------------------------------------------------------------- headers
def security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; font-src 'self' data:; object-src 'none'; "
            f"frame-ancestors {settings.FRAME_ANCESTORS}; base-uri 'self'; form-action 'self'"
        ),
        "Cache-Control": "no-store",
    }
