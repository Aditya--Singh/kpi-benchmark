"""Banks and the KPI dictionary.

Every KPI has: a canonical code, aliases (regex, most specific first), exclusion patterns that
disqualify a label (e.g. 'Domestic NIM' is not the Global NIM KPI), a plausible range used by the
Validation agent, and a direction (+1 higher is better, -1 lower is better).

This is where "NNPA" == "Net NPA" == "Net NPA Ratio (%)" is resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BANKS = {
    "PNB": {"name": "Punjab National Bank", "short": "PNB", "group": "PSB", "home": True, "color": "#A6192E"},
    "SBI": {"name": "State Bank of India", "short": "SBI", "group": "PSB", "home": False, "color": "#2F6FB3"},
    "BOB": {"name": "Bank of Baroda", "short": "BoB", "group": "PSB", "home": False, "color": "#E07A1F"},
    "CANARA": {"name": "Canara Bank", "short": "Canara", "group": "PSB", "home": False, "color": "#1E9C8C"},
    "UNION": {"name": "Union Bank of India", "short": "Union", "group": "PSB", "home": False, "color": "#7A5BC1"},
}

BANK_ALIASES = {
    "PNB": [r"\bpnb\b", r"punjab national"],
    "SBI": [r"\bsbi\b", r"state bank"],
    "BOB": [r"\bbob\b", r"baroda", r"bank of baroda"],
    "CANARA": [r"canara", r"\bcnb\b"],
    "UNION": [r"\bunion\b", r"\bubi\b", r"union bank"],
}


@dataclass
class KPI:
    code: str
    name: str
    short: str
    category: str
    unit: str  # '%' or 'cr'
    direction: int  # +1 higher better, -1 lower better
    aliases: list[str]
    exclude: list[str] = field(default_factory=list)
    vmin: float = float("-inf")
    vmax: float = float("inf")
    definition: str = ""
    basis: str = ""
    derived: str | None = None  # formula over other KPI codes
    visible: bool = True  # helper KPIs (components) are hidden from dashboards
    jump_pct: float = 35.0  # QoQ relative change that triggers a review flag
    decimals: int = 2
    chat_terms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._alias_re = [re.compile(a, re.I) for a in self.aliases]
        self._excl_re = [re.compile(e, re.I) for e in self.exclude]

    def match(self, label: str) -> tuple[int, int, int] | None:
        """Return (alias_index, start, end) of the best alias found in label, unless excluded."""
        for i, rx in enumerate(self._alias_re):
            m = rx.search(label)
            if m:
                if any(x.search(label) for x in self._excl_re):
                    return None
                return i, m.start(), m.end()
        return None

    def match_any(self, label: str) -> tuple[int, int, int, bool] | None:
        """Like match() but also reports excluded variants (used to count chart series slots)."""
        for i, rx in enumerate(self._alias_re):
            m = rx.search(label)
            if m:
                excluded = any(x.search(label) for x in self._excl_re)
                return i, m.start(), m.end(), excluded
        return None

    def in_range(self, v: float) -> bool:
        if self.unit == "cr" and abs(v) < 100:
            # amounts for these banks are always in hundreds/thousands of crore; a tiny number is a
            # stray chart label or a growth %, not an amount
            return False
        return self.vmin <= v <= self.vmax

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "short": self.short, "category": self.category,
            "unit": self.unit, "direction": self.direction, "aliases": self.aliases, "exclude": self.exclude,
            "range": [None if self.vmin == float("-inf") else self.vmin, None if self.vmax == float("inf") else self.vmax],
            "definition": self.definition, "basis": self.basis, "derived": self.derived, "visible": self.visible,
            "decimals": self.decimals,
        }


_COMMON_EXCL = [r"guidance", r"target"]

KPIS: list[KPI] = [
    # ---------------- Business ----------------
    KPI("GLOBAL_BUSINESS", "Global Business", "Business", "Business", "cr", +1,
        [r"global\s+(gross\s+)?business", r"^total\s+business", r"^business\s*$"],
        [r"per\s+(employee|branch)", r"domestic", r"overseas", r"international", r"growth", r"mix"],
        100_000, 30_000_000, "Total deposits plus gross advances (global).", "Global, standalone, ₹ crore",
        derived="GLOBAL_DEPOSITS + GLOBAL_ADVANCES", decimals=0,
        chat_terms=["business", "total business", "global business", "size"]),
    KPI("GLOBAL_DEPOSITS", "Global Deposits", "Deposits", "Business", "cr", +1,
        [r"global\s+deposits?", r"^total\s+deposits?$", r"^deposits$", r"^deposits\s*\(", r"^deposits\b(?!.*(per|growth))"],
        [r"domestic", r"overseas", r"international", r"interest", r"cost\s+of", r"term", r"casa", r"bulk",
         r"retail", r"per\s", r"growth", r"mobili", r"green", r"paid", r"saving", r"current", r"mix", r"\bbc\b"],
        50_000, 20_000_000, "Total deposits of the bank (global).", "Global, standalone, ₹ crore", decimals=0,
        chat_terms=["deposits", "deposit"]),
    KPI("GLOBAL_ADVANCES", "Global Gross Advances", "Advances", "Business", "cr", +1,
        [r"global\s+gross\s+advances?", r"global\s+advances?", r"^gross\s+advances?$", r"^total\s+advances?"],
        [r"domestic", r"overseas", r"international", r"yield", r"interest", r"net\s+advances", r"rwa",
         r"%\s*to", r"growth", r"mix", r"share"],
        30_000, 15_000_000, "Gross loans and advances (global).", "Global gross, standalone, ₹ crore", decimals=0,
        chat_terms=["advances", "loans", "credit", "loan book"]),
    KPI("CASA_RATIO", "CASA Ratio", "CASA", "Business", "%", +1,
        [r"domestic\s+casa\s*(share|ratio)", r"casa\s*(ratio|share)", r"domestic\s+casa\s*\(?%", r"casa\s*\(?%\)?\s*$",
         r"casa\s*\(\s*%\s*\)", r"casa\s*\(domestic\)", r"^casa\s*\(domestic\s+casa\s+to"],
        [r"growth", r"deposits\s+grew", r"guidance"],
        15, 60, "Current + savings deposits as % of domestic deposits.", "Domestic", decimals=2,
        derived="CASA_DEPOSITS / DOMESTIC_DEPOSITS * 100",
        chat_terms=["casa", "low cost deposits", "casa ratio"]),
    KPI("CD_RATIO", "Credit-Deposit Ratio", "CD Ratio", "Business", "%", +1,
        [r"global\s+c\s*-?\s*d\s*ratio", r"^c\s*-?\s*d\s*ratio", r"credit[\s-]+deposit\s+ratio", r"\bcd\s*ratio"],
        [r"domestic"], 40, 110, "Gross advances as % of deposits.", "Global", decimals=2,
        derived="GLOBAL_ADVANCES / GLOBAL_DEPOSITS * 100", chat_terms=["cd ratio", "credit deposit"]),
    # ---------------- Profitability ----------------
    KPI("NII", "Net Interest Income", "NII", "Profitability", "cr", +1,
        [r"net\.?\s+interest\s+income", r"^nii\b"],
        [r"margin", r"growth", r"\bgr\b", r"%\s*$", r"to\s+total", r"\bh1\b", r"9m"],
        300, 100_000, "Interest earned minus interest expended, for the quarter.", "Quarterly, standalone, ₹ crore",
        decimals=0, jump_pct=30, chat_terms=["nii", "net interest income", "interest income"]),
    KPI("NIM", "Net Interest Margin", "NIM", "Profitability", "%", +1,
        [r"whole\s+bank\s+nim", r"\bnim\s*\(global\)", r"\bnim\b", r"net\s+interest\s+margin"],
        [r"adj", r"domestic", r"international", r"overseas", r"foreign", r"guidance", r"\bh1\b", r"cumulative"],
        1.0, 5.0, "Net interest income as % of average interest-earning assets (annualised).", "Global, quarterly, annualised",
        jump_pct=15, chat_terms=["nim", "margin", "net interest margin"]),
    KPI("OPERATING_PROFIT", "Operating Profit", "Op. Profit", "Profitability", "cr", +1,
        [r"operating\s+profit"],
        [r"per\s+(branch|employee)", r"growth", r"\bgr\b", r"%", r"\bh1\b", r"core"],
        -10_000, 80_000, "Pre-provision operating profit for the quarter.", "Quarterly, standalone, ₹ crore",
        decimals=0, jump_pct=40, chat_terms=["operating profit", "ppop", "pre-provision"]),
    KPI("NET_PROFIT", "Net Profit", "Net Profit", "Profitability", "cr", +1,
        [r"net\s+profit", r"profit\s+after\s+tax", r"^pat\b"],
        [r"per\s+(branch|employee)", r"growth", r"\bgr\b", r"%", r"margin", r"our\s+share", r"\bh1\b", r"exceptional",
         r"consolidated"],
        -25_000, 60_000, "Profit after tax for the quarter.", "Quarterly, standalone, ₹ crore",
        decimals=0, jump_pct=50, chat_terms=["net profit", "profit", "pat", "earnings"]),
    KPI("ROA", "Return on Assets", "RoA", "Profitability", "%", +1,
        [r"return\s+on\s+(average\s+)?assets", r"\broa\b"],
        [r"core", r"rorwa", r"risk", r"\bh1\b", r"cumulative", r"exceptional"],
        -3.0, 3.0, "Net profit (annualised) as % of average assets.", "Quarterly, annualised",
        jump_pct=40, chat_terms=["roa", "return on assets"]),
    KPI("ROE", "Return on Equity", "RoE", "Profitability", "%", +1,
        [r"return\s+on\s+(average\s+)?equity", r"\broe\b", r"\bronw\b"],
        [r"\bh1\b", r"cumulative", r"exceptional"],
        -60.0, 45.0, "Net profit (annualised) as % of average net worth.", "Quarterly, annualised",
        jump_pct=40, chat_terms=["roe", "return on equity"]),
    KPI("COST_TO_INCOME", "Cost-to-Income Ratio", "C/I", "Profitability", "%", -1,
        [r"cost[\s-]*to[\s-]*income(\s+ratio)?", r"cost[\s-]*income\s+ratio", r"\bc\s*/\s*i\s+ratio"],
        [r"staff", r"other\s+cost", r"\bh1\b", r"cumulative"],
        20.0, 95.0, "Operating expenses as % of operating income.", "Quarterly", jump_pct=20,
        chat_terms=["cost to income", "cost income", "efficiency"]),
    # ---------------- Asset quality ----------------
    KPI("GNPA", "Gross NPA Ratio", "GNPA %", "Asset Quality", "%", -1,
        [r"gross\s+npa\s*(ratio|%|\(\s*%\s*\))", r"\bgnpa\s*(ratio|%|\(\s*%\s*\))", r"^gnpa$", r"^gross\s+npa$",
         r"\bgnpa\b", r"gross\s+npa"],
        [r"amount", r"addition", r"opening", r"domestic", r"sector", r"retail", r"agri", r"msme", r"corporate",
         r"movement", r"reduction"],
        0.1, 25.0, "Gross non-performing advances as % of gross advances.", "Global", jump_pct=25,
        chat_terms=["gnpa", "gross npa", "bad loans", "npa"]),
    KPI("NNPA", "Net NPA Ratio", "NNPA %", "Asset Quality", "%", -1,
        [r"net\s+npa\s*(ratio|%|\(\s*%\s*\))", r"\bnnpa\s*(ratio|%|\(\s*%\s*\))", r"^nnpa$", r"^net\s+npa$",
         r"\bnnpa\b", r"net\s+npa"],
        [r"amount", r"eligible", r"domestic", r"movement"],
        0.0, 12.0, "Net NPAs (after provisions) as % of net advances.", "Global", jump_pct=30,
        chat_terms=["nnpa", "net npa"]),
    KPI("PCR", "Provision Coverage Ratio (incl. TWO)", "PCR", "Asset Quality", "%", +1,
        [r"pcr\s*%?\s*-?\s*\(?\s*(incl|inc\.|including|with)", r"provision\s+coverage\s+ratio\s*\(?\s*(incl|including|with)",
         r"provision\s+coverage\s+ratio", r"\bpcr\b"],
        [r"excl", r"excluding", r"without", r"\bw/o\b", r"\bex\.", r"nclt"],
        30.0, 100.0, "Provisions held (including technical write-offs) as % of gross NPAs.",
        "Including technical write-offs (TWO/AUCA) where disclosed", jump_pct=10,
        chat_terms=["pcr", "provision coverage", "coverage"]),
    KPI("SLIPPAGE", "Slippage Ratio", "Slippage", "Asset Quality", "%", -1,
        [r"(gross\s+)?slippage\s+ratio", r"delinquency\s+ratio"],
        [r"amount", r"fresh"],
        0.0, 10.0, "Fresh NPAs during the period as % of standard advances (annualised).", "Quarterly, annualised",
        jump_pct=60, chat_terms=["slippage", "slippages", "fresh npa"]),
    KPI("CREDIT_COST", "Credit Cost", "Credit Cost", "Asset Quality", "%", -1,
        [r"credit\s+cost"],
        [r"amount", r"\bh1\b"],
        -3.0, 6.0, "Loan-loss provisions as % of average advances (annualised).", "Quarterly, annualised",
        jump_pct=80, chat_terms=["credit cost"]),
    # ---------------- Capital ----------------
    KPI("CRAR", "Capital Adequacy Ratio (CRAR)", "CRAR", "Capital", "%", +1,
        [r"\bcrar\b", r"capital\s+adequacy\s+ratio", r"capital\s+to\s+risk[\s-]+weighted\s+assets\s+ratio",
         r"total\s*\(\s*tier\s*i\s*\+\s*ii\s*\)\s*%"],
        [r"consol", r"board\s+approved", r"rwa\s+to"],
        8.0, 30.0, "Total capital as % of risk-weighted assets (Basel III).", "Standalone, Basel III", jump_pct=12,
        chat_terms=["crar", "capital adequacy", "car", "capital"]),
    KPI("CET1", "CET-1 Ratio", "CET1", "Capital", "%", +1,
        [r"common\s+equity\s+tier\s*[-]?\s*(1|i)\b", r"\bcet\s*[-]?\s*(1|i)\b"],
        [r"consol", r"capital\s*\(", r"\bat\s*-?\s*i\b(?!.*cet)"],
        5.0, 25.0, "Common equity tier-1 capital as % of risk-weighted assets.", "Standalone, Basel III", jump_pct=12,
        chat_terms=["cet1", "cet-1", "cet 1", "common equity"]),
    # ---------------- Hidden helper components ----------------
    KPI("CASA_DEPOSITS", "CASA Deposits (domestic)", "CASA Dep.", "Components", "cr", +1,
        [r"^(domestic\s+)?casa\s+deposits?", r"^casa$", r"casa\s+deposits\s*\(\s*4\s*\+\s*5\s*\)"],
        [r"%", r"ratio", r"share", r"growth", r"yoy", r"\+\s*retail"],
        10_000, 8_000_000, "Current + savings deposits (domestic).", "Domestic, ₹ crore", visible=False, decimals=0),
    KPI("DOMESTIC_DEPOSITS", "Domestic Deposits", "Dom. Dep.", "Components", "cr", +1,
        [r"^(total\s+)?domestic\s+deposits?"],
        [r"%", r"share", r"cost", r"growth", r"casa"],
        30_000, 15_000_000, "Domestic deposits.", "Domestic, ₹ crore", visible=False, decimals=0),
]

for _k in KPIS:
    _k.exclude = list(dict.fromkeys(_k.exclude + _COMMON_EXCL))
    _k.__post_init__()

KPI_BY_CODE = {k.code: k for k in KPIS}
VISIBLE_KPIS = [k for k in KPIS if k.visible]
CATEGORIES = ["Business", "Profitability", "Asset Quality", "Capital"]


def kpi_for_label(label: str) -> list[tuple[KPI, int]]:
    """All KPIs whose alias matches label (not excluded), sorted by alias specificity."""
    out = []
    for k in KPIS:
        m = k.match(label)
        if m:
            out.append((k, m[0]))
    return out


def detect_banks(text: str) -> list[str]:
    t = text.lower()
    found = []
    for code, pats in BANK_ALIASES.items():
        if any(re.search(p, t) for p in pats):
            found.append(code)
    return found


def detect_kpis_in_question(text: str) -> list[str]:
    t = " " + text.lower() + " "
    hits: list[tuple[int, str]] = []
    for k in VISIBLE_KPIS:
        terms = sorted(set(k.chat_terms + [k.short.lower(), k.name.lower()]), key=len, reverse=True)
        for term in terms:
            idx = t.find(term)
            if idx >= 0 and re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t):
                hits.append((idx, k.code))
                break
    # resolve overlaps like 'net npa' vs 'npa'
    codes = [c for _, c in sorted(hits)]
    if "NNPA" in codes and "GNPA" in codes and "gross" not in t and "gnpa" not in t:
        codes.remove("GNPA")
    if "NET_PROFIT" in codes and "OPERATING_PROFIT" in codes and "net profit" not in t:
        codes.remove("NET_PROFIT")
    return list(dict.fromkeys(codes))
