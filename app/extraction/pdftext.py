"""PDF -> per-page text. Runs in a process pool so a 50-page deck uses several CPU cores.

pdfplumber groups characters into lines by their vertical position, so a table row such as
'Gross NPA (%)  2.35  1.84  1.57  1.33' comes out as one line - exactly what the parser expects.
Pages with almost no text (slides rendered as images) are flagged so the Validation agent can
report 'needs OCR' instead of silently missing values.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def extract_pages(path: str) -> dict:
    import pdfplumber  # imported inside the worker process

    pages: list[str] = []
    image_only: list[int] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    txt = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                except Exception:
                    txt = ""
                if len(txt.strip()) < 25 and page.images:
                    image_only.append(i)
                pages.append(txt)
        return {"ok": True, "pages": pages, "image_only": image_only}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "pages": [], "image_only": []}


def extract_many(paths: list[str], workers: int) -> dict[str, dict]:
    if not paths:
        return {}
    if workers <= 1 or len(paths) == 1:
        return {p: extract_pages(p) for p in paths}
    out: dict[str, dict] = {}
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for p, res in zip(paths, ex.map(extract_pages, paths)):
                out[p] = res
    except Exception:  # e.g. a restricted Windows service account cannot start worker processes
        for p in paths:
            if p not in out:
                out[p] = extract_pages(p)
    return out


def is_pdf(path_or_bytes) -> bool:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return bytes(path_or_bytes[:5]) == b"%PDF-"
    with open(Path(path_or_bytes), "rb") as fh:
        return fh.read(5) == b"%PDF-"
