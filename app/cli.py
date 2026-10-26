"""Command-line tools.  Run from the project folder with the virtual environment active:

    python -m app.cli selftest                 # proves the install works (uses a temporary folder)
    python -m app.cli discover                 # lists the PDF links the Source Scout can see on each bank site
    python -m app.cli collect [--offline] [--banks PNB,SBI] [--quarters 20] [--no-llm]
    python -m app.cli reset-password <user> [--password NEW]
    python -m app.cli add-user <user> <admin|maker|checker|viewer> --name "Full Name" [--password NEW]
    python -m app.cli token <user>             # prints a session token (for embedding in the dashboard)
    python -m app.cli link gm --tab trends     # prints a one-time sign-in link for a viewer (hand-off test)
    python -m app.cli netcheck                 # explains why pip cannot download libraries
    python -m app.cli verify-audit
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


_COLOUR = sys.stdout.isatty() and os.name != "nt"


def _ok(msg: str) -> None:
    print(f"  \033[32mOK\033[0m   {msg}" if _COLOUR else f"  OK   {msg}")


def _bad(msg: str) -> None:
    print(f"  \033[31mFAIL\033[0m {msg}" if _COLOUR else f"  FAIL {msg}")


# ----------------------------------------------------------------------------- selftest
def cmd_selftest(args) -> int:
    """Runs the checks in a throw-away data folder so the real database is never touched."""
    tmp = Path(tempfile.mkdtemp(prefix="kpib_selftest_"))
    try:
        env = dict(os.environ, DATA_DIR=str(tmp), DB_URL=f"sqlite:///{tmp}/selftest.db", LLM_PROVIDER="none",
                   EXTRACT_WORKERS="2")
        shutil.copytree(FIXTURES / "pdf", tmp / "inbox")
        r = subprocess.run([sys.executable, "-m", "app.cli", "_selftest_inner"], cwd=ROOT, env=env)
        return r.returncode
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"Kept temporary data in {tmp}")


def cmd_selftest_inner(args) -> int:
    from .extraction.parser import parse_document
    from .periods import Quarter
    import json

    failures = 0
    print("\n1) KPI extraction on text from five real bank presentations")
    truth = json.loads((FIXTURES / "truth.json").read_text(encoding="utf-8"))
    tot = good = 0
    for fn, spec in truth.items():
        q = Quarter(spec["fy"], spec["q"])
        pages = (FIXTURES / "text" / fn).read_text(encoding="utf-8").split("===PAGE===")
        cands, _ = parse_document(pages, q)
        by = defaultdict(list)
        for c in cands:
            if c.quarter == q:
                by[c.kpi].append(c)
        hit = 0
        for k, tv in spec["values"].items():
            cs = sorted(by.get(k, []), key=lambda c: -c.confidence)
            tot += 1
            if cs and abs(cs[0].value - tv) <= max(0.011, abs(tv) * 0.002):
                hit += 1
        good += hit
        (_ok if hit >= len(spec["values"]) - 1 else _bad)(f"{fn}: {hit}/{len(spec['values'])} KPIs correct")
    if good / tot < 0.95:
        failures += 1
    print(f"     overall {good}/{tot} ({good / tot:.0%})")

    print("\n2) Full pipeline: inbox -> agents -> maker-checker -> scorecard -> report -> chat")
    from fastapi.testclient import TestClient
    from .main import app
    with TestClient(app) as c:
        def login(u):
            return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": os.environ.get("DEFAULT_PASSWORD", "Pnb@12345")}).json()["token"]}
        H, H2 = login("analyst"), login("approver")
        rid = c.post("/api/pipeline/run", json={"offline": True, "use_llm": False}, headers=H).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 300:
            run = c.get(f"/api/obs/runs/{rid}", headers=H).json()["run"]
            if run["status"] != "running":
                break
            time.sleep(0.5)
        (_ok if run["status"] == "succeeded" else _bad)(f"collection run {run['status']}: {run.get('summary')}")
        failures += run["status"] != "succeeded"
        code = c.post("/api/review/approve", json={"all_clean": True}, headers=H).status_code
        (_ok if code == 403 else _bad)(f"analyst blocked from approving (HTTP {code})")
        ap = c.post("/api/review/approve", json={"all_clean": True}, headers=H2).json()
        (_ok if ap["approved"] > 100 else _bad)(f"approver approved {ap['approved']} clean values")
        sc = c.get("/api/bench/scorecard", headers=H).json()
        (_ok if sc.get("quarter") else _bad)(f"scorecard built for {sc.get('quarter')} with {len(sc.get('banks_reported', []))} banks")
        rr = c.post("/api/reports/generate", json={"rtype": "board_pack", "use_llm": False}, headers=H).json()
        t0 = time.time()
        while time.time() - t0 < 120:
            run = c.get(f"/api/obs/runs/{rr['run_id']}", headers=H).json()["run"]
            if run["status"] != "running":
                break
            time.sleep(0.5)
        (_ok if run["status"] == "succeeded" else _bad)(f"board pack generated, numbers verified {run.get('summary', {}).get('grounding')}")
        failures += run["status"] != "succeeded"
        a = c.post("/api/chat", json={"message": "Compare GNPA across all five banks"}, headers=H).json()
        (_ok if a["intent"] == "compare" else _bad)(f"Myra answered ({a['intent']}, {a['latency_ms']} ms)")
        v = c.get("/api/gov/audit/verify", headers=H).json()
        (_ok if v["ok"] else _bad)(v["message"])
        failures += not v["ok"]
    print("\nSelf-test " + ("PASSED" if not failures else f"found {failures} problem(s)"))
    return 1 if failures else 0


# ----------------------------------------------------------------------------- netcheck
def cmd_netcheck(args) -> int:
    """Explains why pip could not download libraries: no route, proxy or a private mirror."""
    import urllib.error
    import urllib.request
    ok = True
    for url in ("https://pypi.org/simple/fastapi/", "https://files.pythonhosted.org/"):
        try:
            urllib.request.urlopen(url, timeout=10)
            print(f"  reachable:      {url}")
        except urllib.error.HTTPError:  # the server answered, so the network path works
            print(f"  reachable:      {url}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  NOT reachable:  {url}  ({type(exc).__name__}: {exc})")
    px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "(none)"
    print(f"  proxy setting:  HTTPS_PROXY={px}")
    idx = os.environ.get("PIP_INDEX_URL")
    if idx:
        print(f"  pip mirror:     PIP_INDEX_URL={idx}")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- discover
def cmd_discover(args) -> int:
    from .scraper.registry import load_registry
    from .scraper.web import PoliteClient, extract_links, render_with_browser
    reg = load_registry()
    banks = [b.strip().upper() for b in args.banks.split(",")] if args.banks else list(reg)
    total = 0
    for code in banks:
        src = reg.get(code)
        if not src:
            print(f"{code}: not configured")
            continue
        print(f"\n{src.name}")
        client = PoliteClient(verify=src.verify_ssl)
        try:
            for page in src.listing_pages:
                if src.respect_robots and not client.allowed(page.url):
                    print(f"  {page.url}\n    blocked by robots.txt")
                    continue
                try:
                    r = client.get(page.url, src.delay_sec)
                except Exception as exc:
                    print(f"  {page.url}\n    could not open: {exc}")
                    continue
                got = extract_links(r.text, str(r.url), src, page.doc_type or src.doc_type)
                if not got and src.render_js in ("auto", "true", True):
                    html2 = render_with_browser(page.url)
                    if html2:
                        got = extract_links(html2, page.url, src, page.doc_type or src.doc_type)
                print(f"  {page.url}\n    {len(got)} PDF link(s)")
                for l in sorted(got, key=lambda x: -(x.quarter.key if x.quarter else 0))[: args.show]:
                    print(f"      {l.quarter.label if l.quarter else '??':8s} {l.label[:70]}\n               {l.url}")
                total += len(got)
        finally:
            client.close()
    print(f"\n{total} link(s) found. Quarters marked ?? are ignored by the pipeline; add them under manual_pdfs in config/sources.yaml or drop the PDF in data/inbox/<BANK>/.")
    return 0


# ----------------------------------------------------------------------------- collect
def cmd_collect(args) -> int:
    from .main import bootstrap
    from .agents.orchestrator import orchestrator
    from .db import AgentRun, session_scope
    bootstrap()
    banks = [b.strip().upper() for b in args.banks.split(",")] if args.banks else None
    rid = orchestrator.start_collection("cli", banks, args.quarters, not args.no_llm, args.offline, args.reextract, False)
    print(f"Run {rid} started - follow it live in the web app under Agents & logs.")
    while True:
        time.sleep(2)
        with session_scope() as s:
            r = s.get(AgentRun, rid)
            if r.status != "running":
                print(f"Finished: {r.status}\n{r.summary or r.error}")
                return 0 if r.status == "succeeded" else 1


# ----------------------------------------------------------------------------- users
def _password(prompt_for: str, given: str | None) -> str:
    from .security import PASSWORD_POLICY
    pw = given or getpass.getpass(f"New password for {prompt_for}: ")
    if not PASSWORD_POLICY.match(pw):
        raise SystemExit("Password must be 8+ characters with upper, lower, digit and symbol.")
    return pw


def cmd_reset_password(args) -> int:
    from .main import bootstrap
    from .db import User, session_scope
    from .security import hash_password
    from .audit import audit
    from sqlalchemy import select
    bootstrap()
    pw = _password(args.user, args.password)
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == args.user)).scalar_one_or_none()
        if not u:
            raise SystemExit("No such user")
        u.password_hash, u.failed_attempts, u.locked_until = hash_password(pw), 0, None
    audit("cli", "user.password.reset", "user", args.user)
    print("Password changed.")
    return 0


def cmd_add_user(args) -> int:
    from .main import bootstrap
    from .db import User, session_scope
    from .security import ROLES, hash_password
    from .audit import audit
    from sqlalchemy import select
    bootstrap()
    if args.role not in ROLES:
        raise SystemExit(f"Role must be one of {', '.join(ROLES)}")
    pw = _password(args.user, args.password)
    with session_scope() as s:
        if s.execute(select(User).where(User.username == args.user)).scalar_one_or_none():
            raise SystemExit("User already exists")
        s.add(User(username=args.user, full_name=args.name or args.user, role=args.role, password_hash=hash_password(pw)))
    audit("cli", "user.created", "user", args.user, {"role": args.role})
    print(f"User {args.user} created with role {args.role}.")
    return 0


def cmd_token(args) -> int:
    from .main import bootstrap
    from .db import User, session_scope
    from .security import make_token
    from sqlalchemy import select
    bootstrap()
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == args.user)).scalar_one_or_none()
        if not u:
            raise SystemExit("No such user")
        print(make_token(u.username, u.role))
    return 0


def cmd_link(args) -> int:
    """Prints a one-time sign-in link for a viewer account - the same link the dashboard hand-off produces."""
    import socket
    from .main import bootstrap
    from .config import settings
    from .db import User, session_scope
    from .security import make_ticket
    from sqlalchemy import select
    bootstrap()
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == args.user)).scalar_one_or_none()
        if not u:
            raise SystemExit("No such user (create it with add-user ... viewer)")
        if u.role != "viewer":
            raise SystemExit("Links are for viewer accounts; analysts, approvers and admins sign in with their password")
        username, role = u.username, u.role
    host = args.host
    if not host:
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.connect(("10.255.255.255", 1))
            host = sk.getsockname()[0]
            sk.close()
        except OSError:
            host = "localhost"
    ticket = make_ticket(username, role, settings.HANDOFF_TICKET_SECONDS)
    print(f"http://{host}:{args.port or settings.PORT}/?ticket={ticket}#/{args.tab}")
    print(f"(works once, for {settings.HANDOFF_TICKET_SECONDS} seconds)")
    return 0


def cmd_verify_audit(args) -> int:
    from .main import bootstrap
    from .audit import verify_chain
    bootstrap()
    r = verify_chain()
    print(r["message"])
    return 0 if r["ok"] else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="PNB KPI Benchmarking tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("selftest", help="check the installation end to end (temporary folder)")
    s.add_argument("--keep", action="store_true")
    s.set_defaults(fn=cmd_selftest)
    sub.add_parser("_selftest_inner").set_defaults(fn=cmd_selftest_inner)
    sub.add_parser("netcheck", help="check access to the Python package index").set_defaults(fn=cmd_netcheck)
    s = sub.add_parser("discover", help="list PDF links visible on each bank's investor page")
    s.add_argument("--banks"); s.add_argument("--show", type=int, default=8)
    s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("collect", help="run the data collection pipeline")
    s.add_argument("--banks"); s.add_argument("--quarters", type=int, default=20)
    s.add_argument("--offline", action="store_true"); s.add_argument("--no-llm", action="store_true")
    s.add_argument("--reextract", action="store_true")
    s.set_defaults(fn=cmd_collect)
    s = sub.add_parser("reset-password"); s.add_argument("user"); s.add_argument("--password")
    s.set_defaults(fn=cmd_reset_password)
    s = sub.add_parser("add-user"); s.add_argument("user"); s.add_argument("role"); s.add_argument("--name"); s.add_argument("--password")
    s.set_defaults(fn=cmd_add_user)
    s = sub.add_parser("token"); s.add_argument("user")
    s.set_defaults(fn=cmd_token)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    s = sub.add_parser("link", help="print a one-time sign-in link for a viewer (tests the dashboard hand-off)")
    s.add_argument("user"); s.add_argument("--tab", default="overview", choices=["overview", "trends", "compare", "reports"])
    s.add_argument("--host"); s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_link)
    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
