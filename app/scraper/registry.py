"""Loads config/sources.yaml (the source registry)."""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from ..config import settings


@dataclass
class ListingPage:
    url: str
    optional: bool = False
    doc_type: str | None = None


@dataclass
class BankSource:
    code: str
    name: str
    doc_type: str
    tier: int
    listing_pages: list[ListingPage]
    include: list[str]
    exclude: list[str]
    period_from: list[str]
    manual_pdfs: list[dict] = field(default_factory=list)
    respect_robots: bool = True
    verify_ssl: bool = True
    delay_sec: float = 1.5
    render_js: str = "auto"


def load_registry() -> dict[str, BankSource]:
    data = yaml.safe_load(settings.SOURCES_FILE.read_text(encoding="utf-8")) or {}
    d = data.get("defaults", {})
    out: dict[str, BankSource] = {}
    for code, b in (data.get("banks") or {}).items():
        pages = []
        for p in b.get("listing_pages", []):
            if isinstance(p, str):
                pages.append(ListingPage(p))
            else:
                pages.append(ListingPage(p["url"], bool(p.get("optional")), p.get("doc_type")))
        out[code] = BankSource(
            code=code, name=b.get("name", code), doc_type=b.get("doc_type", "analyst_presentation"),
            tier=int(b.get("tier", 2)), listing_pages=pages, include=b.get("include", []),
            exclude=b.get("exclude", []), period_from=b.get("period_from", ["label", "context", "url"]),
            manual_pdfs=b.get("manual_pdfs") or [],
            respect_robots=bool(b.get("respect_robots", d.get("respect_robots", True))) and settings.RESPECT_ROBOTS,
            verify_ssl=bool(b.get("verify_ssl", d.get("verify_ssl", True))),
            delay_sec=float(b.get("delay_sec", d.get("delay_sec", settings.REQUEST_DELAY_SEC))),
            render_js=str(b.get("render_js", d.get("render_js", "auto"))),
        )
    return out
