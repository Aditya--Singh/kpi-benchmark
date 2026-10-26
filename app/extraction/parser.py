"""Rule-based KPI extraction from PDF page text.

Four extraction strategies, each producing *candidates* (value + quarter + evidence + confidence):

1. TABLE    - header row with period columns (Sep'25 Jun'26 Sep'26 ...) followed by rows
              "Gross NPA (%)  2.35 1.84 1.57 1.33". Handles interleaved growth columns,
              'Quarterly | Cumulative' paired columns, two-line headers (Q2 / FY26), serial numbers.
2. CHART    - bar/line chart text: a run of values, then a run of period labels, then the
              chart title ("Gross NPA%"). Multi-series charts are split by series and mapped to
              the titles in order. Axis ticks are dropped.
3. KEYVALUE - highlight tiles such as "GNPA 2.42%" (assigned to the document's own quarter).
4. SENTENCE - press-release prose: "Gross NPA Ratio at 1.47% ...", "ROA and ROE ... stand at
              1.11% and 17.87% respectively".

Nothing here trusts a single reading: the Reconciliation agent later compares all candidates
across all documents and picks the value with the strongest, most consistent support.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..catalog import KPIS, KPI, kpi_for_label
from ..periods import Quarter, find_periods

METHOD_CONF = {"table": 0.92, "chart": 0.75, "keyvalue": 0.72, "sentence": 0.80, "llm": 0.60}


@dataclass
class Candidate:
    kpi: str
    quarter: Quarter
    value: float
    method: str
    confidence: float
    page: int
    evidence: str
    alias_idx: int = 0
    note: str = ""


@dataclass
class ParseStats:
    pages: int = 0
    pages_skipped: int = 0
    tables: int = 0
    charts: int = 0
    rows_matched: int = 0
    range_rejects: int = 0
    candidates: int = 0
    skipped_reasons: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- tokens
NUM_RE = re.compile(
    r"^\(?[-+−–]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\)?(?:%|bps|bp|x|cr|crore)?\)?\*?$", re.I)
PLACEHOLDER_RE = re.compile(r"^(?:-|--|–|—|-ve|\+ve|na|n\.a\.|nil|n/a)$", re.I)
SCALE_LAKH_CRORE = re.compile(r"lakh\s*crore|trillion|lac\s*crore|\blakh\s*cr\b", re.I)


def normalise_text(text: str) -> str:
    t = text.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("`", "'").replace("´", "'")
    t = t.replace("₹", " ").replace("−", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"\bRs\.?\s*(?=\d)", " ", t)
    t = re.sub(r"(\d)\s+(bps?)\b", r"\1\2", t, flags=re.I)  # "36 bps" -> "36bps"
    t = re.sub(r"(\d)\s+%", r"\1%", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def is_num_token(tok: str) -> bool:
    return bool(NUM_RE.match(tok)) or bool(PLACEHOLDER_RE.match(tok))


def num_value(tok: str) -> float | None:
    """Parse a numeric token. Returns None for placeholders and bps/x tokens."""
    if PLACEHOLDER_RE.match(tok):
        return None
    t = tok.strip().rstrip("*")
    if re.search(r"(bps|bp|x)\)?$", t, re.I):
        return None
    neg = t.startswith("(") and t.endswith(")") or t.startswith("(") and ")" in t
    t = re.sub(r"[()%,]|cr(ore)?$", "", t, flags=re.I).replace("−", "-").replace("–", "-")
    try:
        v = float(t)
    except ValueError:
        return None
    return -abs(v) if neg else v


def split_label_values(line: str) -> tuple[str, list[str]]:
    """'1 Global Business 2786673 2997970 4.0% 11.9%' -> ('Global Business', [...numbers...])."""
    toks = line.split()
    i = len(toks)
    while i > 0 and is_num_token(toks[i - 1]):
        i -= 1
    label_toks, vals = toks[:i], toks[i:]
    label = " ".join(label_toks)
    label = re.sub(r"^\s*(?:\d+(?:\.\d+)*[a-z]?|[a-z]\)|\([ivx]+\)|[ivx]+\))\s+", "", label, flags=re.I)
    label = label.strip(" :-|")
    return label, vals


def numeric_only(line: str) -> bool:
    toks = line.split()
    return bool(toks) and all(is_num_token(t) for t in toks)


# ----------------------------------------------------------------------------- headers
OTHER_COL_RE = re.compile(
    r"\b(?:Y-?o-?Y|Q-?o-?Q|YOY|QOQ)\b|\bAmt\.?(?=\s|$)|\bAbsolute\b|\bChange\b|%\s*Share\b|\bVar\.?(?=\s|$)", re.I)
PERIOD_FRAGMENT_RE = re.compile(
    r"^(?:Q[1-4]|H[12]|9M|FY\s*'?\d{2,4}|\(\s*\d+M\s*\)|\d+M|Quarterly|Cumulative|[A-Za-z]{3,9}\s*'?\s*\d{2,4}(?:\s*\(\d+M\))?)$", re.I)


@dataclass
class Header:
    cols: list  # Quarter | None (non-quarter period) | "other"
    line_idx: int
    text: str
    stride: int = 1
    scale: float = 1.0
    right_align: bool = False

    @property
    def period_cols(self):
        return [c for c in self.cols if c != "other"]

    @property
    def leading_periods(self) -> int:
        n = 0
        for c in self.cols:
            if c == "other":
                break
            n += 1
        return n


def header_columns(text: str) -> list:
    spans = [(s, e, q) for s, e, q in find_periods(text)]
    items = [(s, q) for s, e, q in spans]
    for m in OTHER_COL_RE.finditer(text):
        if all(not (s <= m.start() < e) for s, e, _ in spans):
            items.append((m.start(), "other"))
    items.sort(key=lambda x: x[0])
    return [c for _, c in items]


def looks_like_header(text: str) -> bool:
    spans = find_periods(text)
    quarters = [q for _, _, q in spans if q is not None]
    if len(spans) < 2 or not quarters:
        return False
    rest = text
    for s, e, _ in sorted(spans, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    nums = [t for t in rest.split() if NUM_RE.match(t) and not re.match(r"^\(?\d+M\)?$", t)]
    return len(nums) < 2


def is_pure_period_line(text: str) -> bool:
    spans = find_periods(text)
    if sum(1 for *_, q in spans if q is not None) < 3:
        return False
    rest = text
    for s, e, _ in sorted(spans, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    rest = re.sub(r"\(\s*\d+M\s*\)|\*|\||\(|\)|-", " ", rest)
    return len(rest.strip()) <= 3


def _is_fragment(line: str) -> bool:
    s = line.strip()
    return bool(s) and len(s) <= 18 and bool(PERIOD_FRAGMENT_RE.match(s))


def preprocess_lines(lines: list[str]) -> list[str]:
    """Join chart axis labels split over lines ('Q2' / "FY'25" ...) and two-line headers
    ('Q2 Q1 Q2 YoY' + 'FY26 FY27 FY27') into single period lines."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        # run of tiny period fragments -> one line
        if _is_fragment(ln):
            j = i
            buf = []
            while j < len(lines) and _is_fragment(lines[j]):
                buf.append(lines[j].strip())
                j += 1
            if j - i >= 2:
                if out:
                    m = re.search(r"\s(Q[1-4]|H[12])$", out[-1])
                    if m:
                        out[-1] = out[-1][: m.start()]
                        buf.insert(0, m.group(1))
                out.append(" ".join(buf))
                i = j
                continue
        # bare Q/H tokens on this line, FY tokens on the next line(s)
        bare = re.findall(r"\b(?:Q[1-4]|H[12])\b(?!\s*(?:FY|'|\d))", ln)
        if len(bare) >= 2 and not find_periods(ln):
            for k in (1, 2):
                if i + k < len(lines):
                    fys = re.findall(r"\bFY\s*'?\s*\d{2,4}\b", lines[i + k])
                    if len(fys) >= len(bare) - 0 or len(fys) >= 2:
                        it = iter(fys)

                        def rep(m, it=it):
                            nxt = next(it, None)
                            return f"{m.group(0)}{nxt.replace(' ', '')}" if nxt else m.group(0)

                        merged = re.sub(r"\b(?:Q[1-4]|H[12])\b(?!\s*(?:FY|'|\d))", rep, ln)
                        out.append(merged)
                        # keep the other lines between (and drop the consumed FY line)
                        for kk in range(1, k):
                            out.append(lines[i + kk])
                        i += k + 1
                        break
            else:
                out.append(ln)
                i += 1
            continue
        out.append(ln)
        i += 1
    return out


# ----------------------------------------------------------------------------- page flags
GUIDANCE_HDR_RE = re.compile(r"guidance(?:\s+for)?\s*(?:FY\s*'?\d{2,4}|march\s*\d{4})?", re.I)
QUALIFIER_RE = re.compile(
    r"(?:[<>]\s*=?\s*|(?:below|above|more\s+than|less\s+than|upto|up\s+to|over)\s+)(?:Rs\.?\s*)?[\d.,]+\s*%?(?:\s*crores?)?"
    r"|[\d.]+\s*%?\s*-\s*[\d.]+\s*%", re.I)


def is_guidance_page(lines: list[str]) -> bool:
    body = " ".join(lines).lower()
    return "guidance" in body and ("actual" in body or " vs " in body)


def page_skip_reason(lines: list[str]) -> str | None:
    head = " ".join(lines[:6]).lower()
    body = " ".join(lines).lower()
    if re.search(r"consolidated", head) and not re.search(r"standalone", head):
        return "consolidated page"
    if re.search(r"subsidiar|joint venture|\brrbs?\b|associates", head):
        return "subsidiary page"
    return None


# ----------------------------------------------------------------------------- mapping
def map_row(header: Header, raw_vals: list[str]) -> list[tuple[Quarter, float, str]]:
    vals = raw_vals[:: header.stride] if header.stride > 1 else raw_vals
    cols = header.cols
    pcols = header.period_cols
    lead = header.leading_periods
    out: list[tuple[Quarter, float, str]] = []
    if header.right_align:
        vals = [v for v in vals if num_value(v) is not None]
        for c, t in zip(pcols[::-1], vals[::-1]):
            if isinstance(c, Quarter):
                out.append((c, num_value(t), t))
        return out

    def emit(col, tok):
        if isinstance(col, Quarter):
            v = num_value(tok)
            if v is not None and not re.search(r"bps?$", tok, re.I):
                out.append((col, v, tok))

    if lead >= 1 and lead == len(pcols) and len(vals) >= lead:
        for c, t in zip(cols[:lead], vals[:lead]):
            emit(c, t)
    elif len(vals) == len(cols):
        for c, t in zip(cols, vals):
            emit(c, t)
    elif len(vals) == len(pcols):
        for c, t in zip(pcols, vals):
            emit(c, t)
    elif lead >= 2 and len(vals) >= lead:
        for c, t in zip(cols[:lead], vals[:lead]):
            emit(c, t)
    # first occurrence of a quarter wins (later duplicates are cumulative/6M columns)
    seen, dedup = set(), []
    for q, v, t in out:
        if q.key not in seen:
            seen.add(q.key)
            dedup.append((q, v, t))
    return dedup


def _scale_for(kpi: KPI, scale: float) -> float:
    return scale if kpi.unit == "cr" else 1.0


# ----------------------------------------------------------------------------- strategies
def _title_slots(title: str) -> list[tuple[int, KPI | None, bool, int]]:
    """Find KPI alias mentions in a chart title / sentence, in reading order:
    (pos, kpi, excluded, alias_idx). Exclusion words are judged only in a small window around each
    mention, bounded by the neighbouring mentions, so 'RoA Core ROA*' keeps RoA but rejects Core ROA."""
    found = []
    for k in KPIS:
        for i, rx in enumerate(k._alias_re):
            for m in rx.finditer(title):
                found.append((m.start(), m.end(), k, i))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0]), x[3]))
    raw, last_end = [], -1
    for s_, e, k, i in found:
        if s_ >= last_end:
            raw.append((s_, e, k, i))
            last_end = e
    slots = []
    for idx, (s_, e, k, i) in enumerate(raw):
        prev_end = raw[idx - 1][1] if idx > 0 else 0
        next_start = raw[idx + 1][0] if idx + 1 < len(raw) else len(title)
        pre = title[max(prev_end, s_ - 12): s_]
        after = title[e: min(next_start, e + 16)]
        if idx + 1 < len(raw) and e + 16 >= next_start:
            # a word glued to the next mention belongs to that mention ("RoA Core ROA*")
            after = re.sub(r"\S+\s*$", "", after)
        window = pre + title[s_:e] + after
        excluded = any(x.search(window) for x in k._excl_re)
        slots.append((s_, k, excluded, i))
    return slots


def _strip_trailing_numbers(line: str) -> tuple[str, list[str]]:
    toks = line.split()
    i = len(toks)
    while i > 0 and is_num_token(toks[i - 1]):
        i -= 1
    return " ".join(toks[:i]), toks[i:]


def parse_chart(lines: list[str], i: int, page_no: int, stats: ParseStats) -> list[Candidate]:
    periods = [q for _, _, q in find_periods(lines[i])]
    P = len(periods)
    # collect numeric block above
    block: list[str] = []
    j = i - 1
    while j >= 0 and i - j <= 18 and numeric_only(lines[j]):
        block.insert(0, lines[j])
        j -= 1
    # drop axis ticks: a lone '-' / '0' line and everything after it
    for bi, b in enumerate(block):
        if b.strip() in {"-", "0", "0.00"}:
            block = block[:bi]
            break
    vals = [t for b in block for t in b.split()]
    title_above = None
    if j >= 0 and not numeric_only(lines[j]):
        lbl, trailing = _strip_trailing_numbers(lines[j])
        if len(vals) % P != 0 and trailing and (len(vals) + len(trailing)) % P == 0:
            vals = trailing + vals
        if lbl and len(lbl) < 90:
            title_above = lbl
    if not vals:
        return []
    if len(vals) % P != 0:
        if P < len(vals) < 2 * P:
            vals = vals[-P:]
        else:
            stats.skipped_reasons["chart_value_mismatch"] = stats.skipped_reasons.get("chart_value_mismatch", 0) + 1
            return []
    k = len(vals) // P
    # title: just below the period line, else just above the values
    title = None
    for t in lines[i + 1: i + 3]:
        lbl, _ = _strip_trailing_numbers(t)
        if lbl and len(lbl) < 110 and _title_slots(lbl):
            title = lbl
            break
    if title is None and title_above and _title_slots(title_above):
        title = title_above
    if title is None:
        return []
    slots = _title_slots(title)
    ctx = " ".join([title] + lines[i + 1: i + 4])
    scale = 1e5 if SCALE_LAKH_CRORE.search(ctx) else 1.0
    stats.charts += 1
    series_map: list[tuple[int, KPI, int, float]] = []  # (series index, kpi, alias idx, confidence)
    if k == 1:
        live = [sl for sl in slots if not sl[2] and sl[1] is not None]
        if len(slots) == 1 and live:
            series_map.append((0, live[0][1], live[0][3], METHOD_CONF["chart"]))
        elif len(slots) > 1:
            nums = [num_value(t) for t in vals]
            nums = [x for x in nums if x is not None]
            fits = [sl for sl in live if nums and all(sl[1].in_range(x * _scale_for(sl[1], scale)) for x in nums)]
            if len(fits) == 1:
                series_map.append((0, fits[0][1], fits[0][3], 0.6))
    elif len(slots) == k:
        for si, (pos, kk, ex, ai) in enumerate(slots):
            if not ex and kk is not None:
                series_map.append((si, kk, ai, 0.58))
    elif slots and slots[0][0] <= 2 and not slots[0][2]:
        series_map.append((0, slots[0][1], slots[0][3], 0.45))
    out = []
    for si, kk, ai, conf in series_map:
        seg = vals[si * P:(si + 1) * P]
        for q, tok in zip(periods, seg):
            if q is None:
                continue
            v = num_value(tok)
            if v is None:
                continue
            v *= _scale_for(kk, scale)
            if not kk.in_range(v):
                stats.range_rejects += 1
                continue
            out.append(Candidate(kk.code, q, v, "chart", conf * (1 - 0.03 * ai), page_no,
                                 f"[chart] {title} | {lines[i]} | values: {' '.join(seg)}", ai))
    return out


SENT_SPLIT = re.compile(r"(?:•|▪|➢|■|●|•|(?<=[a-z%)])\.\s+(?=[A-Z])|;\s+)")
CONNECTOR = re.compile(
    r"(?:\bstands?\s+at|\bstood\s+at|\bat|\bto|\bis|\bwas|:)\s*(?:INR|Rs\.?)?\s*"
    r"(\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*(%|crores?|cr\b|trillion|lakh\s*crores?)?", re.I)


def parse_sentences(text: str, doc_q: Quarter | None, page_no: int) -> list[Candidate]:
    if doc_q is None:
        return []
    out: list[Candidate] = []
    flat = re.sub(r"\s+", " ", text)
    for sent in SENT_SPLIT.split(flat):
        s = sent.strip()
        if len(s) < 12 or len(s) > 400:
            continue
        qs = {q.key for *_, q in find_periods(s) if q is not None}
        if qs and doc_q.key not in qs:
            continue
        matches = []
        for pos, kk, excluded, ai in _title_slots(s):
            if kk is not None and not excluded:
                m = kk._alias_re[ai].search(s, pos)
                matches.append((pos, m.end() if m else pos, kk, ai))
        if not matches:
            continue
        matches.sort(key=lambda x: x[0])
        if re.search(r"\brespectively\b", s, re.I) and len(matches) >= 2:
            tail = s[matches[-1][1]:]
            nums = [mm for mm in CONNECTOR.finditer(tail)]
            nums_all = re.findall(r"(-?\d[\d,]*(?:\.\d+)?)\s*%", tail)
            if len(nums_all) == len(matches):
                for (st, en, k, ai), tok in zip(matches, nums_all):
                    v = num_value(tok)
                    if v is not None and k.in_range(v):
                        out.append(Candidate(k.code, doc_q, v, "sentence", METHOD_CONF["sentence"], page_no,
                                             f"[text] {s[:220]}", ai))
                continue
            del nums
        best_mention: dict[str, tuple] = {}
        for mt in matches:
            if mt[2].code not in best_mention or mt[3] < best_mention[mt[2].code][3]:
                best_mention[mt[2].code] = mt
        for st, en, k, ai in sorted(best_mention.values(), key=lambda x: x[0]):
            window = s[en: en + 110]
            for mm in CONNECTOR.finditer(window):
                tok, unit = mm.group(1), (mm.group(2) or "").lower()
                pre = window[max(0, mm.start() - 14): mm.start()].lower()
                if re.search(r"(growth of|by|increase of|decline of)\s*$", pre):
                    continue
                if k.unit == "%" and unit != "%":
                    continue
                if k.unit == "cr" and unit not in {"crore", "crores", "cr", "trillion", "lakh crore", "lakh crores"}:
                    continue
                v = num_value(tok)
                if v is None:
                    continue
                if k.unit == "cr" and ("trillion" in unit or "lakh" in unit):
                    v *= 1e5
                if k.in_range(v):
                    out.append(Candidate(k.code, doc_q, v, "sentence", METHOD_CONF["sentence"] * (1 - 0.03 * ai),
                                         page_no, f"[text] {s[:220]}", ai))
                break
    return out


def parse_page(text: str, page_no: int, doc_q: Quarter | None, stats: ParseStats) -> list[Candidate]:
    stats.pages += 1
    raw_lines = [ln.strip() for ln in normalise_text(text).split("\n")]
    raw_lines = [ln for ln in raw_lines if ln]
    reason = page_skip_reason(raw_lines)
    if reason:
        stats.pages_skipped += 1
        stats.skipped_reasons[reason] = stats.skipped_reasons.get(reason, 0) + 1
        return []
    guidance = is_guidance_page(raw_lines)
    if guidance:
        raw_lines = [QUALIFIER_RE.sub(" ", GUIDANCE_HDR_RE.sub(" ", ln)).strip() for ln in raw_lines]
        raw_lines = [re.sub(r"\s+", " ", ln) for ln in raw_lines if ln]
    lines = preprocess_lines(raw_lines)
    out: list[Candidate] = []
    header: Header | None = None
    kv_seen: set[str] = set()
    chart_lines: set[int] = set()

    # pass 1: charts
    for i, ln in enumerate(lines):
        if is_pure_period_line(ln) and i > 0 and numeric_only(lines[i - 1]):
            chart_lines.add(i)
            out.extend(parse_chart(lines, i, page_no, stats))

    # pass 2: tables / key-values
    for i, ln in enumerate(lines):
        if i in chart_lines:
            header = None
            continue
        if looks_like_header(ln):
            cols = header_columns(ln)
            stride = 1
            for nxt in lines[i + 1: i + 3]:
                if len(re.findall(r"quarterly", nxt, re.I)) >= 2 and re.search(r"cumulative", nxt, re.I):
                    stride = 2
            ctx = " ".join(lines[max(0, i - 3): i + 1])
            scale = 1e5 if SCALE_LAKH_CRORE.search(ctx) else 1.0
            header = Header(cols, i, ln, stride, scale, right_align=guidance)
            stats.tables += 1
            continue
        if header and i - header.line_idx > 40:
            header = None
        label, vals = split_label_values(ln)
        if not label or not vals or len(label) > 120:
            continue
        matches = kpi_for_label(label)
        if not matches:
            continue
        numeric_vals = [v for v in vals if num_value(v) is not None]
        for kk, ai in matches:
            if header is not None:
                mapped = map_row(header, vals)
                if mapped:
                    stats.rows_matched += 1
                for q, v, tok in mapped:
                    v *= _scale_for(kk, header.scale)
                    if not kk.in_range(v):
                        stats.range_rejects += 1
                        continue
                    conf = METHOD_CONF["table"] * (1 - 0.03 * ai) * (0.65 if guidance else 1.0)
                    out.append(Candidate(kk.code, q, v, "table", conf, page_no,
                                         f"[{'guidance-actual' if guidance else 'table'}] {header.text} || {ln}", ai,
                                         note="guidance page: may be YTD/annualised basis" if guidance else ""))
            elif doc_q is not None and len(numeric_vals) == 1 and kk.code not in kv_seen:
                # key-value tile: label must be short and mostly the alias itself
                if len(label) <= 40 and not re.search(r"\d", label) and len(label.split()) <= 5 and \
                        not re.search(r"\b(stands?|stood|at|as on|grew|improved|declined|of)\b", label, re.I):
                    v = num_value(numeric_vals[0])
                    if v is not None and kk.in_range(v):
                        kv_seen.add(kk.code)
                        out.append(Candidate(kk.code, doc_q, v, "keyvalue", METHOD_CONF["keyvalue"] * (1 - 0.03 * ai),
                                             page_no, f"[tile] {ln}", ai))

    # pass 3: sentences (press releases / highlights)
    if not guidance:
        out.extend(parse_sentences("\n".join(raw_lines), doc_q, page_no))
    return out


def parse_document(pages: list[str], doc_q: Quarter | None) -> tuple[list[Candidate], ParseStats]:
    stats = ParseStats()
    cands: list[Candidate] = []
    for idx, text in enumerate(pages, start=1):
        try:
            cands.extend(parse_page(text or "", idx, doc_q, stats))
        except Exception as exc:  # never let one odd page kill a document
            stats.skipped_reasons[f"page_error:{type(exc).__name__}"] = stats.skipped_reasons.get(
                f"page_error:{type(exc).__name__}", 0) + 1
    # an analyst deck cannot contain actuals for a quarter after its own reporting quarter
    if doc_q is not None:
        future = [c for c in cands if c.quarter.key > doc_q.key]
        if future:
            stats.skipped_reasons["future_quarter_values"] = len(future)
        cands = [c for c in cands if c.quarter.key <= doc_q.key]
    # dedupe identical readings
    best: dict[tuple, Candidate] = {}
    for c in cands:
        key = (c.kpi, c.quarter.key, round(c.value, 4), c.method)
        if key not in best or best[key].confidence < c.confidence:
            best[key] = c
    out = list(best.values())
    stats.candidates = len(out)
    return out, stats
