"""Collection agents: Source Scout -> Document Fetcher -> KPI Extractor."""
from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import delete, select

from ..catalog import KPI_BY_CODE, KPIS
from ..config import settings
from ..db import Candidate as CandRow
from ..db import Chunk, DocPage, Document, now, session_scope
from ..extraction.llm_extract import llm_extract
from ..extraction.parser import parse_document
from ..extraction.pdftext import extract_many, extract_pages, is_pdf
from ..llm import llm
from ..observability import log, tracer
from ..periods import parse_period
from ..scraper.registry import load_registry
from ..scraper.web import PoliteClient, extract_links, inbox_files, render_with_browser, store_pdf
from .base import Agent, PipelineContext


class SourceScoutAgent(Agent):
    name = "scout"
    title = "Source Scout"
    goal = "Find every quarterly analyst presentation / press release PDF on each bank's investor-relations site."
    tools = ["HTTP client (robots.txt aware)", "HTML parser", "period parser", "headless browser (optional)"]
    inputs = ["config/sources.yaml", "data/inbox/<BANK>/"]
    outputs = ["documents (status=discovered)"]

    def execute(self, ctx: PipelineContext):
        reg = load_registry()
        window_keys = {q.key for q in ctx.window}
        offline = ctx.params.get("offline", False)
        found_total = registered = 0
        for code in ctx.banks:
            src = reg.get(code)
            if not src:
                continue
            links = []
            if not offline:
                client = PoliteClient(verify=src.verify_ssl)
                try:
                    for page in src.listing_pages:
                        with tracer.span(self.name, f"{code}: read listing page", input=page.url) as sp:
                            if src.respect_robots and not client.allowed(page.url):
                                log.warning("%s: robots.txt disallows %s - skipped (use data/inbox for manual PDFs)",
                                            code, page.url)
                                sp.set_output("blocked by robots.txt")
                                continue
                            try:
                                r = client.get(page.url, src.delay_sec)
                                html = r.text
                            except Exception as exc:
                                lvl = log.info if page.optional else log.warning
                                lvl("%s: could not open %s (%s)", code, page.url, exc)
                                sp.set_output(f"failed: {exc}")
                                continue
                            got = extract_links(html, str(r.url), src, page.doc_type or src.doc_type)
                            if not got and src.render_js in ("auto", "true", True):
                                html2 = render_with_browser(page.url)
                                if html2:
                                    got = extract_links(html2, page.url, src, page.doc_type or src.doc_type)
                            sp.set_output(f"{len(got)} candidate PDF links")
                            sp.add_meta(links=[{"q": l.quarter.label if l.quarter else None, "label": l.label,
                                                "url": l.url} for l in got][:60])
                            links.extend(got)
                finally:
                    client.close()
            for m in src.manual_pdfs:
                q = parse_period(str(m.get("quarter", "")))
                from ..scraper.web import FoundLink
                links.append(FoundLink(code, m["url"], m.get("label", "manual link"), q, src.doc_type, "sources.yaml"))
            # keep only the requested window
            in_window = [l for l in links if l.quarter and (not window_keys or l.quarter.key in window_keys)]
            no_q = [l for l in links if not l.quarter]
            if no_q:
                log.info("%s: %d links had no recognisable quarter and were ignored", code, len(no_q))
            found_total += len(in_window)
            with session_scope() as s:
                for l in in_window:
                    d = s.execute(select(Document).where(Document.bank == code, Document.source_url == l.url)
                                  ).scalar_one_or_none()
                    if not d:
                        d = Document(bank=code, doc_type=l.doc_type, title=f"{src.name} - {l.label}"[:290],
                                     source_url=l.url, listing_url=l.listing_url, quarter_key=l.quarter.key,
                                     tier=src.tier, status="discovered")
                        s.add(d)
                        s.flush()
                        registered += 1
                    elif d.quarter_key is None:
                        d.quarter_key = l.quarter.key
                    ctx.doc_ids.append(d.id)
            # manual inbox
            for path in inbox_files(code):
                with session_scope() as s:
                    url = f"inbox://{code}/{path.name}"
                    d = s.execute(select(Document).where(Document.bank == code, Document.source_url == url)
                                  ).scalar_one_or_none()
                    if not d:
                        q = parse_period(path.stem.replace("_", " "))
                        if q is None:
                            try:
                                first = extract_pages(str(path)).get("pages", [""])[:2]
                                q = parse_period(" ".join(first)[:3000])
                            except Exception:
                                q = None
                        d = Document(bank=code, doc_type="manual_upload", title=f"{src.name} - {path.name} (inbox)",
                                     source_url=url, listing_url="data/inbox", quarter_key=q.key if q else None,
                                     tier=src.tier, status="discovered", file_path=str(path))
                        s.add(d)
                        s.flush()
                        registered += 1
                        log.info("%s: inbox file %s registered as %s", code, path.name, q.label if q else "UNKNOWN quarter")
                    if not window_keys or (d.quarter_key in window_keys):
                        ctx.doc_ids.append(d.id)
        ctx.doc_ids = list(dict.fromkeys(ctx.doc_ids))
        ctx.bump("links_found", found_total)
        ctx.bump("documents_new", registered)
        return {"links_in_window": found_total, "new_documents": registered, "documents_in_scope": len(ctx.doc_ids)}


class DocumentFetcherAgent(Agent):
    name = "fetcher"
    title = "Document Fetcher"
    goal = "Download each PDF once, verify it is a real PDF, fingerprint it (SHA-256) and store it immutably."
    tools = ["HTTP client", "SHA-256", "content-addressed file store"]
    inputs = ["documents (discovered)"]
    outputs = ["documents (downloaded) + data/raw/<BANK>/*.pdf"]

    def execute(self, ctx: PipelineContext):
        reg = load_registry()
        clients: dict[str, PoliteClient] = {}
        ok = cached = failed = 0
        try:
            for did in ctx.doc_ids:
                with session_scope() as s:
                    d = s.get(Document, did)
                    if d is None:
                        continue
                    if d.file_path and Path(d.file_path).exists() and d.sha256 and not ctx.params.get("force_download"):
                        cached += 1
                        continue
                    if d.source_url.startswith("inbox://"):
                        content = Path(d.file_path).read_bytes()
                        if not is_pdf(content):
                            d.status, d.error = "failed", "Inbox file is not a PDF"
                            failed += 1
                            continue
                        path, digest = store_pdf(d.bank, None if d.quarter_key is None else _q(d.quarter_key), content)
                        d.file_path, d.sha256, d.size_bytes = str(path), digest, len(content)
                        d.status, d.fetched_at = "downloaded", now()
                        ok += 1
                        continue
                    bank, url, qk = d.bank, d.source_url, d.quarter_key
                src = reg[bank]
                client = clients.setdefault(bank, PoliteClient(verify=src.verify_ssl))
                with tracer.span(self.name, f"{bank} {_q(qk).label if qk else ''}: download", input=url) as sp:
                    try:
                        if src.respect_robots and not client.allowed(url):
                            raise RuntimeError("robots.txt disallows this URL")
                        r = client.get(url, src.delay_sec)
                        content = r.content
                        if not is_pdf(content):
                            raise RuntimeError(f"not a PDF (content-type {r.headers.get('content-type')})")
                        path, digest = store_pdf(bank, _q(qk) if qk else None, content)
                        with session_scope() as s:
                            d = s.get(Document, did)
                            dup = s.execute(select(Document).where(Document.sha256 == digest, Document.id != did)
                                            ).scalars().first()
                            d.file_path, d.sha256, d.size_bytes = str(path), digest, len(content)
                            d.status, d.fetched_at, d.error = "downloaded", now(), None
                            if dup:
                                d.error = f"identical content to document #{dup.id}"
                        sp.set_output(f"{len(content)//1024} KB sha256={digest[:12]}")
                        ok += 1
                    except Exception as exc:
                        with session_scope() as s:
                            d = s.get(Document, did)
                            d.status, d.error = "failed", str(exc)[:500]
                        log.warning("%s: download failed for %s: %s", bank, url, exc)
                        sp.set_output(f"failed: {exc}")
                        failed += 1
        finally:
            for c in clients.values():
                c.close()
        ctx.bump("downloaded", ok)
        ctx.bump("download_failed", failed)
        return {"downloaded": ok, "cached": cached, "failed": failed}


def _q(key):
    from ..periods import from_key
    return from_key(key)


class KpiExtractorAgent(Agent):
    name = "extractor"
    title = "KPI Extractor"
    goal = ("Read every page, recognise KPI rows/charts/sentences under any naming (NNPA = Net NPA = Net NPA "
            "Ratio), map each number to its quarter and record it as an evidence-backed candidate.")
    tools = ["pdfplumber (parallel)", "KPI synonym dictionary", "table/chart/sentence parsers",
             "local LLM fallback with grounding check"]
    inputs = ["documents (downloaded)"]
    outputs = ["candidates (value, quarter, page, evidence, confidence)", "page text + retrieval chunks"]

    def execute(self, ctx: PipelineContext):
        use_llm = ctx.params.get("use_llm", True) and llm.available
        todo: list[tuple[int, str]] = []
        with session_scope() as s:
            for did in ctx.doc_ids:
                d = s.get(Document, did)
                if d and d.file_path and d.status in ("downloaded", "extracted") and Path(d.file_path).exists():
                    if d.status == "extracted" and not ctx.params.get("reextract", False):
                        continue
                    todo.append((did, d.file_path))
        if not todo:
            return {"documents": 0, "note": "nothing new to extract (use 'Re-extract' to force)"}
        t0 = time.perf_counter()
        with tracer.span(self.name, f"PDF text extraction ({len(todo)} files, {settings.EXTRACT_WORKERS} workers)") as sp:
            texts = extract_many([p for _, p in todo], settings.EXTRACT_WORKERS)
            sp.set_output(f"{sum(len(v.get('pages', [])) for v in texts.values())} pages in "
                          f"{time.perf_counter() - t0:.1f}s")
        total_c = total_llm = 0
        for did, path in todo:
            res = texts.get(path) or {"ok": False, "error": "missing", "pages": []}
            with session_scope() as s:
                d = s.get(Document, did)
                bank, qk, title = d.bank, d.quarter_key, d.title
            q = _q(qk) if qk else None
            with tracer.span(self.name, f"{bank} {q.label if q else '?'}: parse", input=title) as sp:
                if not res.get("ok"):
                    with session_scope() as s:
                        d = s.get(Document, did)
                        d.status, d.error = "failed", res.get("error")
                    sp.set_output(f"failed: {res.get('error')}")
                    continue
                pages = res["pages"]
                cands, stats = parse_document(pages, q)
                # LLM fallback for KPIs with no reading for the document's own quarter
                llm_hits = 0
                if use_llm and q is not None:
                    have = {c.kpi for c in cands if c.quarter.key == q.key}
                    for k in KPIS:
                        if k.visible and k.code not in have:
                            c = llm_extract(pages, k, q, title)
                            if c:
                                cands.append(c)
                                llm_hits += 1
                with session_scope() as s:
                    s.execute(delete(CandRow).where(CandRow.doc_id == did))
                    s.execute(delete(DocPage).where(DocPage.doc_id == did))
                    s.execute(delete(Chunk).where(Chunk.doc_id == did))
                    for c in cands:
                        s.add(CandRow(doc_id=did, bank=bank, kpi=c.kpi, quarter_key=c.quarter.key, value=c.value,
                                      method=c.method, confidence=round(c.confidence, 3), page_no=c.page,
                                      evidence=c.evidence[:1000], is_primary=bool(q and c.quarter.key == q.key),
                                      note=c.note or None, run_id=ctx.run_id))
                    for i, ptxt in enumerate(pages, start=1):
                        s.add(DocPage(doc_id=did, page_no=i, text=ptxt))
                        for chunk in _chunks(ptxt):
                            s.add(Chunk(doc_id=did, bank=bank, quarter_key=qk, page_no=i, text=chunk))
                    d = s.get(Document, did)
                    d.pages, d.status, d.extracted_at, d.error = len(pages), "extracted", now(), None
                    st = stats.__dict__.copy()
                    st["image_only_pages"] = res.get("image_only", [])
                    st["llm_fallback_hits"] = llm_hits
                    st["kpis_for_own_quarter"] = len({c.kpi for c in cands if q and c.quarter.key == q.key
                                                      and KPI_BY_CODE[c.kpi].visible})
                    d.stats = st
                total_c += len(cands)
                total_llm += llm_hits
                sp.set_output(f"{len(cands)} candidates; {st['kpis_for_own_quarter']} KPIs for {q.label if q else '?'}"
                              f"; tables={stats.tables} charts={stats.charts} llm={llm_hits}")
                sp.add_meta(**{k: v for k, v in st.items() if k != "skipped_reasons"})
        ctx.bump("candidates", total_c)
        return {"documents": len(todo), "candidates": total_c, "llm_fallback_values": total_llm,
                "llm_used": use_llm}


def _chunks(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    t = " ".join((text or "").split())
    if len(t) < 40:
        return []
    out, i = [], 0
    while i < len(t):
        out.append(t[i: i + size])
        i += size - overlap
    return out
