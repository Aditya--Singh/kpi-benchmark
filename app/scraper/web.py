"""Web access for the Scout and Download agents: polite HTTP client, robots.txt, link discovery,
PDF download with content fingerprinting, and the manual inbox."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from ..config import settings
from ..observability import log
from ..periods import Quarter, parse_period
from .registry import BankSource


@dataclass
class FoundLink:
    bank: str
    url: str
    label: str
    quarter: Quarter | None
    doc_type: str
    listing_url: str
    how_period: str = ""


class PoliteClient:
    """httpx client with a browser-like user agent, per-host delay, retries and robots.txt checks."""

    def __init__(self, verify: bool = True):
        self.client = httpx.Client(
            follow_redirects=True, timeout=settings.HTTP_TIMEOUT, verify=verify,
            headers={"User-Agent": settings.USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                     "Accept-Language": "en-IN,en;q=0.9"})
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def close(self) -> None:
        self.client.close()

    def allowed(self, url: str) -> bool:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(host + "/robots.txt", timeout=15)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
                else:
                    self._robots[host] = None  # no robots.txt -> allowed
            except Exception:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(settings.USER_AGENT, url)

    def get(self, url: str, delay: float, retries: int = 2) -> httpx.Response:
        host = urlparse(url).netloc
        wait = self._last.get(host, 0) + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self.client.get(url)
                self._last[host] = time.time()
                if r.status_code in (429, 503) and attempt < retries:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r
            except Exception as exc:
                last_exc = exc
                self._last[host] = time.time()
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
        raise last_exc  # type: ignore[misc]


# ----------------------------------------------------------------------------- discovery
def _row_text(a: Tag) -> str:
    node = a
    for _ in range(4):
        node = node.parent
        if node is None or not isinstance(node, Tag):
            break
        if node.name in ("tr", "li"):
            return node.get_text(" ", strip=True)
    return ""


def extract_links(html: str, base_url: str, src: BankSource, doc_type: str) -> list[FoundLink]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    inc = [re.compile(p, re.I) for p in src.include]
    exc = [re.compile(p, re.I) for p in src.exclude]
    context = ""
    found: dict[str, FoundLink] = {}
    for el in soup.descendants:
        if isinstance(el, NavigableString):
            txt = " ".join(str(el).split())
            if 3 < len(txt) < 90 and parse_period(txt):
                context = txt
            continue
        if not isinstance(el, Tag) or el.name != "a" or not el.get("href"):
            continue
        href = el["href"].strip()
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = urljoin(base_url, href)
        anchor = " ".join(el.get_text(" ", strip=True).split())
        title = el.get("title", "") or ""
        row = _row_text(el)
        label = " ".join(x for x in [anchor, title, row] if x)[:300]
        hay = f"{label} {url}"
        if not any(r.search(hay) for r in inc) or any(r.search(hay) for r in exc):
            continue
        # does it look like a document?
        if not re.search(r"\.pdf|downloadprocess|/documents/|/pdf/|/media/|presentation|analysts?", url, re.I):
            continue
        q, how = None, ""
        for srcname in src.period_from:
            cand = {"label": f"{anchor} {title} {row}", "context": context, "url": url.rsplit("/", 1)[-1]}[srcname]
            q = parse_period(cand)
            if q:
                how = srcname
                break
        if url not in found or (found[url].quarter is None and q is not None):
            found[url] = FoundLink(src.code, url, (anchor or title or row)[:200], q, doc_type, base_url, how)
    return list(found.values())


def render_with_browser(url: str) -> str | None:
    """Optional: headless Chromium for JavaScript-built pages (pip install playwright; playwright install chromium)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("Page looks JavaScript-built but playwright is not installed; skipping render for %s", url)
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(user_agent=settings.USER_AGENT)
            pg.goto(url, wait_until="networkidle", timeout=60_000)
            # expand year selectors / 'load more' buttons if present
            for sel in pg.query_selector_all("select"):
                try:
                    for opt in sel.query_selector_all("option")[:12]:
                        val = opt.get_attribute("value")
                        if val:
                            sel.select_option(val)
                            pg.wait_for_timeout(800)
                except Exception:
                    pass
            html = pg.content()
            b.close()
            return html
    except Exception as exc:
        log.warning("Headless render failed for %s: %s", url, exc)
        return None


# ----------------------------------------------------------------------------- download
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def store_pdf(bank: str, quarter: Quarter | None, content: bytes) -> tuple[Path, str]:
    digest = sha256_bytes(content)
    qlabel = quarter.label.replace(" ", "") if quarter else "UNKNOWN"
    d = settings.RAW_DIR / bank
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{qlabel}_{digest[:12]}.pdf"
    if not path.exists():
        path.write_bytes(content)
    return path, digest


def inbox_files(bank: str) -> list[Path]:
    d = settings.INBOX_DIR / bank
    d.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in d.iterdir() if p.suffix.lower() == ".pdf")
