"""Publishing: DOCX report, PPTX deck (native, editable charts) and XLSX workbook with lineage."""
from __future__ import annotations

import io
import math
import re
from pathlib import Path

from ..catalog import BANKS, KPI_BY_CODE, VISIBLE_KPIS
from ..config import settings
from ..db import Document, session_scope
from . import benchmark as bm

MAROON = (0xA6, 0x19, 0x2E)
GOLD = (0xF2, 0xB6, 0x00)
INK = (0x22, 0x22, 0x2A)
KEY_TRENDS = ["GNPA", "NNPA", "NIM", "ROA", "CASA_RATIO", "COST_TO_INCOME", "CRAR", "PCR"]


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % rgb


# ----------------------------------------------------------------------------- charts for the Word report
# matplotlib is optional: some networks block it at install time. Without it the same chart is drawn with
# Pillow, which python-pptx and pdfplumber already require.
def trend_png(kpi: str, include_staged: bool) -> bytes | None:
    try:
        t = bm.trend(kpi, n=12, include_staged=include_staged)
        if not t["quarters"] or not any(v is not None for s in t["series"] for v in s["values"]):
            return None
        try:
            return _trend_png_matplotlib(t)
        except ImportError:
            return _trend_png_pillow(t)
    except Exception as exc:  # a chart must never stop a report
        from ..observability import log
        log.warning("Chart for %s skipped: %s", kpi, exc)
        return None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
             "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
             "arialbd.ttf" if bold else "arial.ttf"]
    dirs = ["", "/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/liberation/", "C:/Windows/Fonts/"]
    for n in names:
        for d in dirs:
            try:
                return ImageFont.truetype(d + n, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[j:j + 2], 16) for j in (1, 3, 5))


def _trend_png_pillow(t: dict) -> bytes:
    from PIL import Image, ImageDraw

    W, H = 1024, 470
    L, R, T, B = 96, 56, 60, 116
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_title, f_ax, f_leg = _font(22, True), _font(15), _font(16)
    d.text((24, 16), f"{t['name']} ({'%' if t['unit'] == '%' else 'Rs crore'})", fill=MAROON, font=f_title)
    # drop leading quarters where no bank has data yet
    first = min(i for s in t["series"] for i, v in enumerate(s["values"]) if v is not None)
    t = {**t, "quarters": t["quarters"][first:],
         "series": [{**s, "values": s["values"][first:]} for s in t["series"]]}
    vals = [v for s in t["series"] for v in s["values"] if v is not None]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / 4
    mag = 10 ** math.floor(math.log10(raw))
    tick = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    lo, hi = math.floor(lo / tick) * tick, math.ceil(hi / tick) * tick
    n = len(t["quarters"])
    iw, ih = W - L - R, H - T - B

    def x(i):
        return L + (iw * i / (n - 1) if n > 1 else iw / 2)

    def y(v):
        return T + ih - (v - lo) / (hi - lo) * ih

    for k in range(int(round((hi - lo) / tick)) + 1):
        v = lo + tick * k
        yy = y(v)
        d.line([(L, yy), (W - R, yy)], fill=(230, 230, 230), width=1)
        d.text((L - 10, yy), f"{v:.2f}" if t["unit"] == "%" else f"{v:,.0f}", fill=(110, 110, 120), font=f_ax,
               anchor="rm")
    step = max(1, (n + 7) // 8)
    for i, q in enumerate(t["quarters"]):
        if i % step == 0 or i == n - 1:
            d.text((x(i), T + ih + 10), q, fill=(110, 110, 120), font=f_ax, anchor="ma")
    for s in sorted(t["series"], key=lambda s: s["bank"] == bm.HOME):
        rgb, home = _rgb(s["color"]), s["bank"] == bm.HOME
        width = 5 if home else 3
        seg: list[tuple[float, float]] = []
        for i, v in enumerate(list(s["values"]) + [None]):
            if v is not None:
                seg.append((x(i), y(v)))
                continue
            if len(seg) == 1:
                cx, cy = seg[0]
                d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=rgb)
            elif len(seg) > 1:
                if s.get("dashed"):
                    for (x0, y0), (x1, y1) in zip(seg, seg[1:]):
                        parts = max(2, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 // 10))
                        for k in range(0, parts, 2):
                            a, b = k / parts, (k + 1) / parts
                            d.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                    (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=rgb, width=width)
                else:
                    d.line(seg, fill=rgb, width=width, joint="curve")
                if home:
                    for cx, cy in seg:
                        d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=rgb)
            seg = []
    lx, ly = L, H - 42
    for s in t["series"]:
        if not any(v is not None for v in s["values"]):
            continue
        d.line([(lx, ly + 10), (lx + 26, ly + 10)], fill=_rgb(s["color"]), width=5 if s["bank"] == bm.HOME else 3)
        d.text((lx + 32, ly), s["name"], fill=INK, font=f_leg)
        lx += 48 + int(d.textlength(s["name"], font=f_leg))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _trend_png_matplotlib(t: dict) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 2.9), dpi=160)
    for s in t["series"]:
        xs = [i for i, v in enumerate(s["values"]) if v is not None]
        ys = [v for v in s["values"] if v is not None]
        if not ys:
            continue
        lw = 2.6 if s["bank"] == bm.HOME else 1.4
        ax.plot(xs, ys, label=s["name"], color=s["color"], lw=lw, ls="--" if s.get("dashed") else "-",
                marker="o" if s["bank"] == bm.HOME else None, ms=3)
    ax.set_xticks(range(len(t["quarters"])))
    ax.set_xticklabels([q.replace(" FY", "\nFY") for q in t["quarters"]], fontsize=6.5)
    ax.tick_params(axis="y", labelsize=7)
    ax.set_title(f"{t['name']} ({'%' if t['unit'] == '%' else 'Rs crore'})", fontsize=9, loc="left",
                 color=_hex(MAROON), fontweight="bold")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", color="#e6e6e6", lw=0.6)
    ax.legend(fontsize=6.5, ncol=6, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.22))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


# ----------------------------------------------------------------------------- shared tables
def scorecard_rows(sc: dict) -> list[list[str]]:
    rows = []
    for s in sc["kpis"]:
        home = s.get("home") or {}
        k = KPI_BY_CODE[s["kpi"]]
        if k.unit == "%":
            pnb = f"{home['value']:.2f}" if home else "-"
            peer = f"{s['peer_avg']:.2f}" if "peer_avg" in s else "-"
            gap = f"{s['gap_bps']:+.0f} bps" if "gap_bps" in s else "-"
        else:
            pnb = f"{home['value']:,.0f}" if home else "-"
            g = home.get("yoy_growth")
            pnb += f" ({g:+.1f}% YoY)" if g is not None else ""
            peer = f"{s['peer_avg_metric']:+.1f}% YoY" if s.get("peer_avg_metric") is not None else "-"
            gap = f"{s['gap']:+.1f} pp" if s.get("gap") is not None else "-"
        best = BANKS[s["best_bank"]]["short"] if s.get("best_bank") else "-"
        rank = f"{home.get('rank', '-')}/{s['n']}" if home else "-"
        rows.append([k.name + (" (%)" if k.unit == "%" else " (₹ cr)"), pnb, peer, best, rank, gap])
    return rows


def sources_used() -> list[list[str]]:
    with session_scope() as s:
        docs = s.query(Document).filter(Document.status == "extracted").order_by(Document.bank, Document.quarter_key).all()
        from ..periods import from_key
        return [[d.bank, from_key(d.quarter_key).label if d.quarter_key else "-", d.title[:80],
                 (d.sha256 or "")[:12], d.source_url or ""] for d in docs]


# ----------------------------------------------------------------------------- DOCX
def _md_to_docx(doc, md: str) -> None:
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif re.match(r"^\s*[-*] ", line):
            p = doc.add_paragraph(style="List Bullet")
            _runs(p, re.sub(r"^\s*[-*] ", "", line))
        elif re.match(r"^\s*\d+\. ", line):
            p = doc.add_paragraph(style="List Number")
            _runs(p, re.sub(r"^\s*\d+\. ", "", line))
        else:
            _runs(doc.add_paragraph(), line)


def _runs(p, text: str) -> None:
    from docx.shared import Pt, RGBColor
    parts = re.split(r"(\*\*[^*]+\*\*|\[F\d+(?:,\s*F\d+)*\])", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = p.add_run(part[2:-2])
            r.bold = True
        elif re.match(r"^\[F\d", part):
            r = p.add_run(part)
            r.font.size = Pt(7)
            r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
            r.font.superscript = True
        else:
            p.add_run(part)


def build_docx(path: Path, title: str, md: str, sc: dict | None, provisional: bool, meta: dict,
               include_charts: bool, include_staged: bool) -> Path:
    from docx import Document as Docx
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Pt, RGBColor

    doc = Docx()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.left_margin = sec.right_margin = Cm(2.0)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    for lvl, sz in ((1, 15), (2, 12.5), (3, 11)):
        hs = doc.styles[f"Heading {lvl}"]
        hs.font.color.rgb = RGBColor(*MAROON)
        hs.font.size = Pt(sz)
    t = doc.add_paragraph()
    r = t.add_run("PUNJAB NATIONAL BANK  |  DATA ANALYTICS & STRATEGIC INNOVATION DIVISION")
    r.font.size, r.font.bold, r.font.color.rgb = Pt(8.5), True, RGBColor(*GOLD)
    h = doc.add_heading(title, level=0)
    for run in h.runs:
        run.font.color.rgb = RGBColor(*MAROON)
    info = doc.add_paragraph()
    info.add_run(f"Reference quarter: {meta.get('quarter')}   |   Peers: SBI, Bank of Baroda, Canara Bank, Union Bank "
                 f"of India   |   Generated: {meta.get('generated')}").font.size = Pt(8.5)
    box = doc.add_paragraph()
    note = ("AI-ASSISTED DRAFT - generated from approved data by the KPI Benchmarking platform. "
            f"Mode: {meta.get('mode')}; grounding check: {meta.get('grounding')}. "
            "Figures carry fact references [F#] traceable to source documents. Verify before external use.")
    if provisional:
        note = "PROVISIONAL - includes values not yet approved by the checker. " + note
    rr = box.add_run(note)
    rr.font.size, rr.font.italic = Pt(8), True
    rr.font.color.rgb = RGBColor(0xB0, 0x3A, 0x2E) if provisional else RGBColor(0x55, 0x55, 0x55)
    _md_to_docx(doc, md)
    if sc and sc.get("kpis"):
        doc.add_heading(f"Peer scorecard - {sc['quarter']}", level=1)
        hdr = ["KPI", "PNB", "Peer average", "Best", "PNB rank", "Gap vs peers"]
        rows = scorecard_rows(sc)
        tbl = doc.add_table(rows=1, cols=len(hdr))
        tbl.style = "Light Grid Accent 2"
        for i, hcell in enumerate(hdr):
            tbl.rows[0].cells[i].text = hcell
        for row in rows:
            cells = tbl.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = v
        for row in tbl.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(8.5)
        doc.add_paragraph("Ranks: ratios ranked on level (direction-aware, e.g. lower GNPA is better); amounts ranked on "
                          "year-on-year growth so that size does not dominate.").runs[0].font.size = Pt(7.5)
    if include_charts:
        doc.add_heading("Five-year trends (last 12 quarters shown)", level=1)
        for k in KEY_TRENDS:
            png = trend_png(k, include_staged)
            if png:
                doc.add_picture(io.BytesIO(png), width=Cm(16.5))
    doc.add_heading("Data sources and method", level=1)
    doc.add_paragraph("Values were collected automatically from each bank's published analyst presentations / press "
                      "releases, extracted with evidence (document, page, line), reconciled across documents, validated "
                      "and approved under maker-checker control. Narrative text is produced only from the numbered fact "
                      "sheet and checked by the compliance guardrail.")
    src = sources_used()
    if src:
        sec2 = doc.add_section()
        sec2.orientation = WD_ORIENT.LANDSCAPE
        sec2.page_width, sec2.page_height = Cm(29.7), Cm(21.0)
        doc.add_heading("Appendix - source documents", level=2)
        tb = doc.add_table(rows=1, cols=4)
        tb.style = "Light List Accent 2"
        for i, hh in enumerate(["Bank", "Quarter", "Document", "SHA-256 (prefix)"]):
            tb.rows[0].cells[i].text = hh
        for b, ql, ti, sh, _url in src[:150]:
            c = tb.add_row().cells
            c[0].text, c[1].text, c[2].text, c[3].text = b, ql, ti, sh
        for row in tb.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(7.5)
    doc.save(path)
    return path


# ----------------------------------------------------------------------------- PPTX
def build_pptx(path: Path, title: str, sc: dict, fs: dict, summary_points: list[str], provisional: bool,
               include_staged: bool) -> Path:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def band(slide, text, sub=None):
        shp = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.95))
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor(*MAROON)
        shp.line.fill.background()
        tf = shp.text_frame
        tf.text = text
        p = tf.paragraphs[0]
        p.runs[0].font.size, p.runs[0].font.bold = Pt(24), True
        p.runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        acc = slide.shapes.add_shape(1, 0, Inches(0.95), prs.slide_width, Inches(0.06))
        acc.fill.solid()
        acc.fill.fore_color.rgb = RGBColor(*GOLD)
        acc.line.fill.background()
        if sub:
            tb = slide.shapes.add_textbox(Inches(0.5), Inches(1.05), Inches(12.3), Inches(0.4))
            tb.text_frame.text = sub
            tb.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
            tb.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        foot = slide.shapes.add_textbox(Inches(0.5), Inches(7.05), Inches(12.3), Inches(0.3))
        foot.text_frame.text = ("PROVISIONAL - contains unapproved values. " if provisional else "") + \
            "Source: banks' published analyst presentations / press releases. AI-assisted; facts grounded and traceable."
        foot.text_frame.paragraphs[0].runs[0].font.size = Pt(9)
        foot.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    # title
    s = prs.slides.add_slide(blank)
    bg = s.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(*MAROON)
    bg.line.fill.background()
    bar = s.shapes.add_shape(1, Inches(0.8), Inches(3.55), Inches(2.2), Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor(*GOLD)
    bar.line.fill.background()
    tb = s.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(11.5), Inches(1.5))
    tb.text_frame.text = title
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(38)
    tb.text_frame.paragraphs[0].runs[0].font.bold = True
    tb.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    st = s.shapes.add_textbox(Inches(0.8), Inches(3.8), Inches(11.5), Inches(1.2))
    st.text_frame.text = (f"PNB vs SBI, Bank of Baroda, Canara Bank, Union Bank of India  |  {sc['quarter']}\n"
                          "Data Analytics & Strategic Innovation Division")
    for p in st.text_frame.paragraphs:
        p.runs[0].font.size = Pt(16)
        p.runs[0].font.color.rgb = RGBColor(0xF6, 0xE7, 0xC1)

    # summary
    s = prs.slides.add_slide(blank)
    band(s, "Executive summary", f"Reference quarter {sc['quarter']}")
    tb = s.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(12.1), Inches(5.3))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, pt in enumerate(summary_points[:8]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = "▪  " + re.sub(r"\s*\[F\d+(?:,\s*F\d+)*\]", "", pt)
        p.space_after = Pt(9)
        p.runs[0].font.size = Pt(16)
        p.runs[0].font.color.rgb = RGBColor(*INK)

    # scorecard
    rows = scorecard_rows(sc)
    for part in range(0, len(rows), 10):
        s = prs.slides.add_slide(blank)
        band(s, "Peer scorecard", f"{sc['quarter']} - PNB position against peers")
        chunk = rows[part: part + 10]
        hdr = ["KPI", "PNB", "Peer avg", "Best", "Rank", "Gap"]
        shape = s.shapes.add_table(len(chunk) + 1, len(hdr), Inches(0.5), Inches(1.6), Inches(12.3), Inches(0.4))
        tbl = shape.table
        widths = [3.6, 2.6, 1.9, 1.4, 1.1, 1.7]
        for i, w in enumerate(widths):
            tbl.columns[i].width = Inches(w)
        for i, h in enumerate(hdr):
            c = tbl.cell(0, i)
            c.text = h
            c.fill.solid()
            c.fill.fore_color.rgb = RGBColor(*MAROON)
            c.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
            c.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        for r_i, row in enumerate(chunk, start=1):
            for c_i, v in enumerate(row):
                c = tbl.cell(r_i, c_i)
                c.text = v
                c.text_frame.paragraphs[0].runs[0].font.size = Pt(11)
                if c_i == 5 and v not in ("-",):
                    good = v.startswith("+")
                    k = KPI_BY_CODE[[kk for kk in VISIBLE_KPIS][part + r_i - 1].code]
                    if k.unit == "%" and k.direction < 0:
                        good = not good
                    c.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x1B, 0x7F, 0x4C) if good else RGBColor(0xB0, 0x3A, 0x2E)

    # trend charts (native)
    for i in range(0, len(KEY_TRENDS), 2):
        s = prs.slides.add_slide(blank)
        band(s, "Five-year trend vs peers", "Quarter-wise, last 12 quarters")
        for j, kpi in enumerate(KEY_TRENDS[i:i + 2]):
            t = bm.trend(kpi, n=12, include_staged=include_staged)
            if not t["quarters"]:
                continue
            cd = CategoryChartData()
            cd.categories = t["quarters"]
            for sr in t["series"]:
                cd.add_series(sr["name"], [v if v is not None else None for v in sr["values"]])
            x = Inches(0.4 + j * 6.35)
            gf = s.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, x, Inches(1.6), Inches(6.2), Inches(5.3), cd)
            ch = gf.chart
            ch.has_title = True
            ch.chart_title.text_frame.text = f"{t['name']} ({'%' if t['unit'] == '%' else '₹ cr'})"
            ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
            ch.has_legend = True
            ch.legend.position = XL_LEGEND_POSITION.BOTTOM
            ch.legend.include_in_layout = False
            ch.legend.font.size = Pt(9)
            ch.category_axis.tick_labels.font.size = Pt(8)
            ch.value_axis.tick_labels.font.size = Pt(9)
            for sr, series in zip(t["series"], ch.series):
                series.format.line.color.rgb = RGBColor.from_string(sr["color"].lstrip("#").upper())
                series.format.line.width = Emu(38100 if sr["bank"] == bm.HOME else 19050)
                series.smooth = False

    # peer bars for latest quarter
    bars = ["GNPA", "NNPA", "PCR", "NIM", "ROA", "ROE", "COST_TO_INCOME", "CASA_RATIO", "CRAR"]
    for i in range(0, len(bars), 3):
        s = prs.slides.add_slide(blank)
        band(s, "Point-in-time comparison", sc["quarter"])
        for j, kpi in enumerate(bars[i:i + 3]):
            snap = next((x for x in sc["kpis"] if x["kpi"] == kpi), None)
            if not snap or not snap["banks"]:
                continue
            cd = CategoryChartData()
            order = sorted(snap["banks"].items(), key=lambda kv: -KPI_BY_CODE[kpi].direction * kv[1]["value"])
            cd.categories = [BANKS[b]["short"] for b, _ in order]
            cd.add_series(KPI_BY_CODE[kpi].short, [round(it["value"], 2) for _, it in order])
            gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.4 + j * 4.2), Inches(1.6), Inches(4.1),
                                    Inches(5.2), cd)
            ch = gf.chart
            ch.has_legend = False
            ch.has_title = True
            ch.chart_title.text_frame.text = KPI_BY_CODE[kpi].name + " (%)"
            ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
            plot = ch.plots[0]
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(10)
            plot.data_labels.number_format = "0.00"
            plot.data_labels.number_format_is_linked = False
            for idx, (b, _) in enumerate(order):
                pt = plot.series[0].points[idx]
                pt.format.fill.solid()
                pt.format.fill.fore_color.rgb = RGBColor(*MAROON) if b == bm.HOME else RGBColor(0xC9, 0xC9, 0xCF)
            ch.category_axis.tick_labels.font.size = Pt(10)
            ch.category_axis.reverse_order = True
            ch.value_axis.visible = False

    # insights + actions
    ins = fs.get("insights", {})
    s = prs.slides.add_slide(blank)
    band(s, "Insights and suggested actions", "For management consideration")
    tb = s.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(12.1), Inches(5.3))
    tf = tb.text_frame
    tf.word_wrap = True
    lines = []
    if ins.get("strengths"):
        lines.append(("Strengths to sustain: " + ", ".join(KPI_BY_CODE[k].name for k in ins["strengths"]), True))
    if ins.get("momentum"):
        lines.append(("Improving faster than peers: " + ", ".join(KPI_BY_CODE[k].name for k in ins["momentum"]), True))
    for a in ins.get("actions", []):
        lines.append((f"{a['kpi_name']}: {a['action']}", False))
    if not lines:
        lines.append(("No material gaps detected against the peer set for this quarter.", False))
    for i, (txt, bold) in enumerate(lines[:9]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = ("★  " if bold else "→  ") + txt
        p.space_after = Pt(8)
        p.runs[0].font.size = Pt(15)
        p.runs[0].font.bold = bold

    # method
    s = prs.slides.add_slide(blank)
    band(s, "How these numbers were produced", "Agentic pipeline with human approval")
    steps = ["Source Scout finds each bank's quarterly decks on its official website",
             "Document Fetcher downloads, verifies and fingerprints every PDF (SHA-256)",
             "KPI Extractor reads tables, charts and text; synonyms mapped (e.g. NNPA = Net NPA)",
             "Validation & Reconciliation cross-checks values across decks and flags conflicts",
             "Checker approves values (maker-checker); every decision is in a tamper-evident audit log",
             "Benchmark Analyst computes ranks and gaps; Insight Writer drafts text from numbered facts only",
             "Compliance Guardrail rejects any sentence whose numbers are not in the fact sheet"]
    tb = s.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(12.1), Inches(5.3))
    tf = tb.text_frame
    for i, st_ in enumerate(steps):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"{i + 1}.  {st_}"
        p.space_after = Pt(8)
        p.runs[0].font.size = Pt(15)
    prs.save(path)
    return path


# ----------------------------------------------------------------------------- XLSX
def build_xlsx(path: Path, sc: dict, fs: dict | None, include_staged: bool) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from ..periods import from_key

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="A6192E")
    hdr_font = Font(color="FFFFFF", bold=True)

    def header(ws, cols):
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.fill, c.font = hdr_fill, hdr_font
            c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.freeze_panes = ws.cell(row=ws.max_row + 1, column=2)

    ws = wb.active
    ws.title = "Scorecard"
    ws.append([f"PNB Peer Scorecard - {sc.get('quarter')}" + ("  (PROVISIONAL)" if sc.get("provisional") else "")])
    ws["A1"].font = Font(bold=True, size=13, color="A6192E")
    header(ws, ["KPI", "Unit"] + [BANKS[b]["short"] for b in BANKS] + ["Peer avg", "PNB rank", "Rank basis", "Best"])
    for s in sc.get("kpis", []):
        row = [s["name"], s["unit"]]
        for b in BANKS:
            it = s["banks"].get(b)
            row.append(round(it["value"], 4) if it else None)
        row += [round(s["peer_avg"], 4) if "peer_avg" in s else None, (s.get("home") or {}).get("rank"),
                s["rank_basis"], BANKS[s["best_bank"]]["short"] if s.get("best_bank") else None]
        ws.append(row)

    ws2 = wb.create_sheet("Trends")
    vals = bm.load_values(include_staged)
    qks = bm.quarters_available(vals)[-20:]
    header(ws2, ["KPI", "Bank"] + [from_key(q).label for q in qks])
    for k in VISIBLE_KPIS:
        for b in BANKS:
            ws2.append([k.name, BANKS[b]["short"]] + [round(vals[(b, k.code, q)].value, 4) if (b, k.code, q) in vals
                                                      else None for q in qks])

    ws3 = wb.create_sheet("Data & lineage")
    header(ws3, ["Bank", "KPI", "Quarter", "Value", "Status", "Method", "Confidence", "Support", "Flags", "Page",
                 "Evidence", "Source document", "Source URL"])
    with session_scope() as s:
        docs = {d.id: d for d in s.query(Document).all()}
        for (b, kc, q), v in sorted(vals.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
            d = docs.get(v.doc_id)
            ws3.append([b, KPI_BY_CODE[kc].name, from_key(q).label, v.value, v.status, v.method, v.confidence,
                        v.support, ", ".join(v.flags or []), v.page_no, (v.evidence or "")[:300],
                        d.title if d else "", d.source_url if d else ""])
    if fs:
        ws4 = wb.create_sheet("Fact sheet")
        header(ws4, ["Fact", "KPI", "Type", "Statement"])
        for f in fs["facts"]:
            ws4.append([f["id"], f["kpi"], f["type"], f["text"]])
    for w in wb.worksheets:
        for i, col in enumerate(w.columns, start=1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            w.column_dimensions[get_column_letter(i)].width = min(60, max(9, width + 2))
    wb.save(path)
    return path


def out_dir() -> Path:
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return settings.OUTPUT_DIR
