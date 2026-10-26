"""Indian financial-year quarter handling.

FY27 = 1 Apr 2026 .. 31 Mar 2027.  Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar.
Every period string seen in bank documents ("Q2FY27", "Q2 FY'27", "Sept'26", "Sep-26",
"Jun 30, 2026", "30.09.2026", "September 2026", "Sep26") is normalised to (fy, q).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
QUARTER_END_MONTH = {6: 1, 9: 2, 12: 3, 3: 4}  # month -> fiscal quarter

APOS = r"[\'`’‘´]"
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

# Order matters: more specific patterns first.
RE_QFY = re.compile(
    rf"\bQ\s*([1-4])\s*[-/]?\s*(?:FY|F\.Y\.)\s*{APOS}?\s*(\d{{4}}\s*[-/]\s*\d{{2,4}}|\d{{4}}|\d{{2}})(?!\d)", re.I)
RE_FYQ = re.compile(rf"\bFY\s*{APOS}?\s*(\d{{2}}|\d{{4}})\s*[-/]?\s*Q\s*([1-4])\b", re.I)
RE_MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b", re.I)
RE_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)
RE_MONTH_YEAR = re.compile(rf"\b({_MONTH_ALT})\.?\s*(?:{APOS}|-|/|\s|,)?\s*{APOS}?(\d{{4}}|\d{{2}})(?!\d)", re.I)
RE_DATE_NUM = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
# Non-quarter column labels (half year, nine months, full year) -> placeholders
RE_NONQ = re.compile(rf"\b(?:H[12]|9M|6M|12M)\s*-?\s*(?:FY)?\s*{APOS}?\s*\d{{2,4}}\b|\bFY\s*{APOS}?\s*\d{{2}}(?:\d{{2}})?(?!\s*[-/]?\s*Q\s*[1-4]\b)\b", re.I)


@dataclass(frozen=True, order=True)
class Quarter:
    fy: int  # 2-digit fiscal year, e.g. 27 for FY2026-27
    q: int

    @property
    def key(self) -> int:
        return self.fy * 10 + self.q

    @property
    def label(self) -> str:
        return f"Q{self.q} FY{self.fy:02d}"

    @property
    def end_label(self) -> str:
        m = {1: "Jun", 2: "Sep", 3: "Dec", 4: "Mar"}[self.q]
        year = 2000 + (self.fy if self.q == 4 else self.fy - 1)
        return f"{m}'{year % 100:02d}"

    def shift(self, n: int) -> "Quarter":
        idx = self.fy * 4 + (self.q - 1) + n
        return Quarter(idx // 4, idx % 4 + 1)

    def __str__(self) -> str:  # pragma: no cover
        return self.label


def from_key(key: int) -> Quarter:
    return Quarter(key // 10, key % 10)


def from_label(label: str) -> Quarter | None:
    return parse_period(label)


def _norm_year(y: str) -> int:
    y = y.strip()
    if len(y) == 4:
        return int(y)
    return 2000 + int(y)


def _fy_from_token(tok: str) -> int:
    tok = re.sub(r"\s", "", tok)
    if "-" in tok or "/" in tok:  # 2026-27 / 2026-2027 -> 27
        b = re.split(r"[-/]", tok)[1]
        return int(b[-2:])
    if len(tok) == 4:
        return int(tok[-2:])
    return int(tok)


def quarter_from_month_year(month: int, year: int) -> Quarter | None:
    q = QUARTER_END_MONTH.get(month)
    if q is None:
        return None
    fy = year if month == 3 else year + 1
    return Quarter(fy % 100, q)


def parse_period(text: str) -> Quarter | None:
    """Return the first quarter mentioned in *text*, or None."""
    if not text:
        return None
    hits = find_periods(text)
    for _, _, qt in hits:
        if qt is not None:
            return qt
    return None


def find_periods(text: str) -> list[tuple[int, int, Quarter | None]]:
    """All period-like tokens in text as (start, end, Quarter|None). None = non-quarter column
    (H1, FY26, 9M ...).  Overlaps are resolved left-to-right, most specific first."""
    spans: list[tuple[int, int, Quarter | None]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _ in spans)

    for m in RE_QFY.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end(), Quarter(_fy_from_token(m.group(2)), int(m.group(1)))))
    for m in RE_FYQ.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end(), Quarter(_fy_from_token(m.group(1)), int(m.group(2)))))
    for m in RE_NONQ.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end(), None))
    for m in RE_MONTH_DAY_YEAR.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end(), quarter_from_month_year(MONTHS[m.group(1).lower()], int(m.group(3)))))
    for m in RE_DAY_MONTH_YEAR.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end(), quarter_from_month_year(MONTHS[m.group(2).lower()], int(m.group(3)))))
    for m in RE_DATE_NUM.finditer(text):
        if free(m.start(), m.end()):
            mo = int(m.group(2))
            if 1 <= mo <= 12:
                spans.append((m.start(), m.end(), quarter_from_month_year(mo, int(m.group(3)))))
    for m in RE_MONTH_YEAR.finditer(text):
        if free(m.start(), m.end()):
            mon = MONTHS[m.group(1).lower()]
            yr = _norm_year(m.group(2))
            if 2000 <= yr <= 2099:
                spans.append((m.start(), m.end(), quarter_from_month_year(mon, yr)))
    spans.sort(key=lambda s: s[0])
    return spans


def quarter_window(latest: Quarter, n: int) -> list[Quarter]:
    return [latest.shift(-i) for i in range(n - 1, -1, -1)]


def current_reporting_quarter(today=None) -> Quarter:
    """Latest quarter that has *ended* (results may still be awaited)."""
    import datetime as _dt

    today = today or _dt.date.today()
    m, y = today.month, today.year
    if m <= 3:
        return quarter_from_month_year(12, y - 1)
    if m <= 6:
        return quarter_from_month_year(3, y)
    if m <= 9:
        return quarter_from_month_year(6, y)
    return quarter_from_month_year(9, y)
