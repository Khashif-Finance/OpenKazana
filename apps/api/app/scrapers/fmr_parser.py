"""
SECP-Standardized Monthly Fund Manager Report (FMR) PDF Parser & Automated AMC Scraper.

Pakistani AMCs publish monthly FMR booklets following SECP NBFC circular templates
containing per-fund fact sheets with:
  - Asset Allocation / Composition (% of Total Assets)
  - Top 10 Equity Holdings (% of Total Assets) -> auto-resolved to canonical PSX symbols
  - Sector Allocation (% of Total Assets)
  - Credit / Asset Quality Breakdown (AAA, AA+, AA, A1+, Govt Securities)
  - Net Assets (AUM in PKR Mn), WAM (Days), Duration, YTM%, Stability Rating (e.g. AA+(f)),
    Expense Ratio (TER MTD/YTD + Govt Levies), Management Fee, Fund Manager & Commentary.

Supports both:
  1. Automated discovery & download from official open AMC FMR archives
     (Al Meezan, HBL Asset, Atlas Funds Shariah & Conventional, JS Investments, ABL Funds).
  2. Manual drag-and-drop upload of ANY Pakistani AMC's monthly FMR booklet or single-fund PDF.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import pdfplumber
from pdfplumber.utils import extract_text as pdf_extract_text_from_chars
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.market import Asset, FundFmrSnapshot, MutualFundProfile
from app.scrapers.base import BaseScraper

logger = logging.getLogger("openkhazana.scrapers.fmr")
D = Decimal

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/pdf,*/*;q=0.8",
}

AMC_FMR_ARCHIVE_PAGES: list[tuple[str, str, int]] = [
    ("Al Meezan Investment Management", "https://www.almeezangroup.com/download-category/fund-manager-reports/", 1),
    ("HBL Asset Management", "https://hblasset.com/downloadcategories/fund-manager-report/", 1),
    ("Atlas Asset Management", "https://www.atlasfunds.com.pk/downloads/fund-manager-reports/", 2),
    ("JS Investments", "https://jsil.com/all-downloads/fund-manager-reports/", 1),
    ("ABL Asset Management", "https://www.ablfunds.com/type/fund-manager-reports", 1),
]

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

ASSET_BUCKET_PATTERNS: list[tuple[str, str]] = [
    ("Equities", r"\b(?:stock\s*/\s*equities|equities|equity|listed\s+equities|shares)\b"),
    ("T-Bills", r"\b(?:t-?bills?|treasury\s+bills?|mtb)\b"),
    ("GoP Sukuk / PIBs", r"\b(?:gop\s+ijarah|ijarah\s+sukuk|pibs?|pakistan\s+investment\s+bonds?|govt\.?\s+securities|government\s+securities|gis)\b"),
    ("Corporate Sukuk / TFC", r"\b(?:sukuks?|tfcs?|term\s+finance|corporate\s+debt|debt\s+securities)\b"),
    ("Commercial Paper / Placements", r"\b(?:commercial\s+papers?|placements?|lop|tdrs?|term\s+deposits?|mts|margin\s+trading|reverse\s+repo|certificates?\s+of\s+investment|coi|com)\b"),
    ("Gold", r"\b(?:gold|precious\s+metal)\b"),
    ("Cash & Bank", r"\b(?:cash|bank\s+balances?|bank\s+deposits?|cash\s+and\s+bank|cash\s*&\s*bank)\b"),
    ("Others", r"\b(?:others?\s+incl|others?\s+including|other\s+assets|receivables|others)\b"),
]

CANONICAL_PSX_SECTORS = [
    ("Commercial Banks", r"\b(?:commercial\s+banks?|banks?)\b"),
    ("Oil & Gas Exploration", r"\b(?:oil\s*(?:&|and)\s*gas\s+exploration|e&p)\b"),
    ("Oil & Gas Marketing", r"\b(?:oil\s*(?:&|and)\s*gas\s+marketing|omcs?)\b"),
    ("Fertilizer", r"\b(?:fertilizers?)\b"),
    ("Cement", r"\b(?:cements?)\b"),
    ("Power Generation", r"\b(?:power\s+generation|power)\b"),
    ("Technology & Comm.", r"\b(?:technolog(?:y|ies)|tech|telecom)\b"),
    ("Pharmaceuticals", r"\b(?:pharmaceuticals?|pharma)\b"),
    ("Food & Personal Care", r"\b(?:food\s*(?:&|and)\s*personal\s+care|food)\b"),
    ("Automobile Assembler", r"\b(?:automobile|auto\s+assembler|autos?)\b"),
    ("Refinery", r"\b(?:refiner(?:y|ies))\b"),
    ("Insurance", r"\b(?:insurance)\b"),
    ("Chemicals", r"\b(?:chemicals?)\b"),
    ("Textile Composite", r"\b(?:textiles?)\b"),
    ("Engineering / Steel", r"\b(?:engineering|steel)\b"),
    ("Glass & Ceramics", r"\b(?:glass\s*(?:&|and)\s*ceramics?)\b"),
    ("Paper & Board", r"\b(?:paper|packaging)\b"),
    ("Inv. Banks / Conglomerate", r"\b(?:inv\.?\s*banks?|investment\s+companies|conglomerate)\b"),
    ("Others", r"\b(?:others?)\b"),
]

CREDIT_RATING_PATTERNS: list[tuple[str, str]] = [
    ("Govt / Sovereign", r"\b(?:govt\.?\s*securities|government\s+guaranteed|gop|sovereign)\b"),
    ("AAA", r"(?<![A-Za-z])AAA(?![A-Za-z\+\-])"),
    ("AA+", r"(?<![A-Za-z])AA\+(?![A-Za-z])"),
    ("AA", r"(?<![A-Za-z])AA(?![A-Za-z\+\-])"),
    ("AA-", r"(?<![A-Za-z])AA-(?![A-Za-z])"),
    ("A+", r"(?<![A-Za-z])A\+(?![A-Za-z])"),
    ("A", r"(?<![A-Za-z])A(?![A-Za-z0-9\+\-])"),
    ("A-", r"(?<![A-Za-z])A-(?![A-Za-z])"),
    ("A1+", r"(?<![A-Za-z])A1\+(?![A-Za-z])"),
    ("A1", r"(?<![A-Za-z])A1(?![A-Za-z\+])"),
    ("Unrated / Others", r"\b(?:unrated|others?\s*incl|others?)\b"),
]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _clean_num(raw: str | None) -> float | None:
    if not raw:
        return None
    s = raw.replace(",", "").replace("%", "").strip()
    m = re.match(r"^-?\d+(?:\.\d+)?$", s)
    if not m:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _detect_report_month(text: str, filename_hint: str = "", fallback_today: bool = True) -> tuple[str, str]:
    """Return `(YYYY-MM, 'Month YYYY')` from FMR text or filename (handles FMR_Oct-26, FMROctober2026, July-FMR-2026)."""
    clean_hint = re.sub(r"[_\-/]+", " ", filename_hint or "")
    # Split camel/prefix mashups like "FMROctober2026" -> "FMR October 2026"
    clean_hint = re.sub(r"(?i)\bfmr(?=[a-z])", "fmr ", clean_hint)
    combined = f"{text[:1200]} {clean_hint}"
    # Pattern 1: "October 2026" / "October 31, 2026" / "Oct-26" / "July FMR 2026"
    m = re.search(
        r"(?:^|[^a-z])(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[\s\-_.,]*(?:fmr[\s\-_.,]+)?(?:\d{1,2}[\s,]+)?(20\d{2}|\d{2})(?=$|[^0-9])",
        combined,
        re.I,
    )
    if m:
        mon_num = MONTH_MAP.get(m.group(1).lower())
        yr_raw = int(m.group(2))
        yr = 2000 + yr_raw if yr_raw < 100 else yr_raw
        if mon_num and 2020 <= yr <= 2035:
            mon_name = datetime(yr, mon_num, 1).strftime("%B %Y")
            return f"{yr:04d}-{mon_num:02d}", mon_name
    if not fallback_today:
        return "", ""
    today = datetime.now(timezone.utc)
    return today.strftime("%Y-%m"), today.strftime("%B %Y")


def _dedupe_chars_fast(chars: list[dict[str, Any]], tol: float = 1.5) -> list[dict[str, Any]]:
    """O(N) spatial character deduplication to strip PDF bold drop-shadow doubles (e.g. Al Meezan)."""
    seen: set[tuple[str, int, int]] = set()
    out: list[dict[str, Any]] = []
    for c in chars:
        ch = c.get("text", "")
        if not ch:
            continue
        key = (ch, int(round(float(c.get("x0", 0.0)) / tol)), int(round(float(c.get("top", 0.0)) / tol)))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _extract_page_bands(page: Any) -> dict[str, str]:
    """
    Fast char-level O(N) extraction across full page AND spatial column bands without invoking
    expensive vector-curve page.crop() operations. Cleanly separates 2-column (Al Meezan, HBL)
    and 3-column (Atlas) SECP FMR pages.
    """
    raw_chars = page.chars or []
    chars = _dedupe_chars_fast(raw_chars, tol=1.5)
    w = float(page.width or 612.0)

    def _txt(subset: list[dict[str, Any]]) -> str:
        if not subset:
            return ""
        try:
            return pdf_extract_text_from_chars(subset) or ""
        except Exception:
            return ""

    full_txt = _txt(chars)
    if _is_non_fund_page(full_txt):
        return {"full": full_txt, "left": "", "right": "", "col1": "", "col2": "", "col3": ""}

    left_txt = _txt([c for c in chars if float(c.get("x0", 0.0)) < w * 0.47])
    right_txt = _txt([c for c in chars if float(c.get("x0", 0.0)) >= w * 0.43])
    col1_txt = _txt([c for c in chars if float(c.get("x0", 0.0)) < w * 0.34])
    col2_txt = _txt([c for c in chars if w * 0.31 <= float(c.get("x0", 0.0)) < w * 0.67])
    col3_txt = _txt([c for c in chars if float(c.get("x0", 0.0)) >= w * 0.62])
    return {
        "full": full_txt,
        "left": left_txt,
        "right": right_txt,
        "col1": col1_txt,
        "col2": col2_txt,
        "col3": col3_txt,
    }


def _is_non_fund_page(full_txt: str) -> bool:
    low = full_txt[:900].lower()
    if len(full_txt.strip()) < 260:
        return True
    skip_markers = (
        "complaint handling mechanism",
        "capital markets review",
        "risk profile of cis",
        "risk profile of collective investment",
        "economic review",
        "money market review",
        "stock market review",
        "from the desk of",
        "performance summary",
        "table of contents",
        "how to invest",
        "branch network",
    )
    if any(k in low for k in skip_markers):
        return True
    # Must contain at least one SECP FMR quantitative anchor
    body_low = full_txt.lower()
    anchors = ("net assets", "asset allocation", "asset mix", "expense ratio", "fund details", "fund information", "fund facts")
    return sum(1 for a in anchors if a in body_low) < 2


def _match_page_to_fund_catalog(
    header_txt: str,
    full_txt: str,
    fund_catalog: list[dict[str, Any]],
    amc_hint: str = "",
) -> dict[str, Any] | None:
    """Match an FMR page title/header to a registered MutualFundProfile / Asset."""
    top_norm = _norm(header_txt[:520])
    # Also scan first 6 non-empty lines joined
    first_lines = " ".join([ln.strip()[:120] for ln in full_txt.splitlines() if ln.strip()][:6])
    head_norm = f"{top_norm} {_norm(first_lines[:400])}"
    hint_norm = _norm(amc_hint)

    # Filter catalog strictly to AMC when hint is supplied so e.g. Atlas never collides with ABL
    if hint_norm:
        hint_tok = hint_norm.split()[0]
        pool = [
            f for f in fund_catalog
            if hint_norm in _norm(f["amc_name"]) or _norm(f["amc_name"]) in hint_norm or (len(hint_tok) >= 3 and hint_tok in _norm(f["amc_name"]))
        ]
        if not pool:
            return None
    else:
        pool = fund_catalog

    best: tuple[int, dict[str, Any]] | None = None
    for f in pool:
        fnorm = _norm(f["name"])
        sym = str(f["symbol"]).upper()
        if not fnorm or len(fnorm) < 6:
            continue
        score = 0
        if fnorm in head_norm:
            score = 100 + len(fnorm)
        else:
            # Check explicit parenthesized acronym e.g. "(AISF)", "(MRAF)", "(MIF)"
            if len(sym) >= 3 and re.search(rf"\(\s*{re.escape(sym)}\s*\)", header_txt[:500], re.I):
                score = 95 + len(sym)
            else:
                # Word overlap for slightly abbreviated titles (e.g. "HBL Money Market Fund" vs "HBL Money Market")
                stop = {"fund", "scheme", "plan", "limited", "islamic", "mutual", "class", "type", "unit", "the", "of"}
                words = [w for w in fnorm.split() if len(w) > 2]
                key_words = [w for w in words if w not in stop]
                if len(words) >= 3 and all(w in head_norm for w in words):
                    score = 80 + len(fnorm)
                elif len(key_words) >= 2 and all(w in top_norm for w in key_words):
                    # Verify Shariah alignment so Conventional vs Islamic funds never collide
                    is_page_islamic = any(k in top_norm for k in ("islamic", "shariah", "al ameen", "alhamra", "meezan"))
                    if is_page_islamic == bool(f.get("is_shariah_compliant")):
                        score = 60 + len(key_words) * 6
        if score > 0 and (best is None or score > best[0]):
            best = (score, f)
    return best[1] if best else None


def _build_psx_company_matcher(psx_assets: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """
    Build normalized company-name -> (symbol, name, sector, norm_key) matchers sorted longest first
    so "Engro Fertilizers Limited" matches EFERT before "Engro Holdings Limited" -> ENGROH.
    """
    entries: list[tuple[str, str, str, str]] = []
    seen_keys: set[str] = set()
    # Exclude tickers whose names appear as generic words or index benchmarks (e.g. "Pakistan Stock Exchange", "Select")
    skip_syms = {"PSX", "SELECT", "CASH", "BANK", "FUND", "BOND", "UNIT", "PAK", "KSE"}
    for a in psx_assets:
        sym = str(a["symbol"]).upper()
        if sym in skip_syms:
            continue
        name = str(a["name"])
        sec = str(a.get("sector_or_category") or "")
        base = _norm(name)
        variants = {base}
        # Strip legal suffixes: Limited / Ltd / (Pvt) / Pakistan / Company / Co
        short = re.sub(r"\b(?:limited|ltd|company|co|corporation|corp|pvt|the)\b", "", base).strip()
        short = re.sub(r"\s+", " ", short)
        if len(short) >= 6:
            variants.add(short)
        # Common FMR aliases
        alias_map = {
            "OGDC": ["oil gas development", "oil gas dev", "ogdcl"],
            "PPL": ["pakistan petroleum"],
            "MARI": ["mari petroleum", "mari energies"],
            "MEBL": ["meezan bank"],
            "FFC": ["fauji fertilizer co", "fauji fertilizer company"],
            "EFERT": ["engro fertilizers", "engro fertilizer"],
            "ENGROH": ["engro holdings", "engro corporation", "engro corp"],
            "LUCK": ["lucky cement"],
            "HUBC": ["hub power", "hubco"],
            "PSO": ["pakistan state oil"],
            "SYS": ["systems limited", "systems ltd"],
            "UBL": ["united bank"],
            "MCB": ["mcb bank"],
            "HBL": ["habib bank"],
            "BAHL": ["bank al habib"],
            "BAFL": ["bank alfalah"],
            "FABL": ["faysal bank"],
            "POL": ["pakistan oilfields"],
            "ATRL": ["attock refinery"],
            "APL": ["attock petroleum"],
            "MTL": ["millat tractors"],
            "INDU": ["indus motor"],
            "SAZEW": ["sazgar engineering"],
            "MLCF": ["maple leaf cement"],
            "FCCL": ["fauji cement"],
            "CHCC": ["cherat cement"],
            "DGKC": ["d g khan cement", "dg khan cement"],
            "PIOC": ["pioneer cement"],
            "ILP": ["interloop"],
            "NML": ["nishat mills"],
            "SEARL": ["searle company"],
            "GLAXO": ["glaxosmithkline"],
            "HALEON": ["haleon pakistan"],
            "SNGP": ["sui northern gas"],
            "NATF": ["national foods"],
            "AIRLINK": ["air link communication", "airlink"],
        }
        for al in alias_map.get(sym, []):
            variants.add(_norm(al))
        for v in variants:
            if len(v) >= 5 and v not in seen_keys:
                seen_keys.add(v)
                entries.append((v, sym, name, sec))
    entries.sort(key=lambda x: len(x[0]), reverse=True)
    return entries


def parse_secp_fmr_page_text(
    bands: dict[str, str],
    *,
    fund_info: dict[str, Any],
    report_month: str,
    report_label: str,
    psx_matchers: list[tuple[str, str, str, str]],
) -> dict[str, Any] | None:
    """
    Parse one SECP-format FMR fund page into structured composition, Top-10 holdings,
    sector allocation, credit quality, AUM, WAM, stability rating, TER & returns.
    """
    full = bands.get("full") or ""
    left = bands.get("left") or ""
    right = bands.get("right") or ""
    col1 = bands.get("col1") or ""
    col2 = bands.get("col2") or ""
    # Clean joined candidate text streams for robust regex matching across 2-col and 3-col PDFs
    combined = "\n".join([b for b in (left, right, full) if b])

    # 1. Net Assets (AUM in PKR Mn)
    net_assets_mn: float | None = None
    m_bn = re.search(r"net\s+assets[^.\n]{0,70}?(?:rs\.?|pkr)\s*([\d,]+(?:\.\d+)?)\s*billion", full, re.I)
    if not m_bn:
        m_bn = re.search(
            r"(?:net\s+assets|fund\s+size)\s*\(?(?:pkr|rs\.?)?\s*(?:in\s*)?(?:bn|billion)\)?\s*:?\s*(?:pkr|rs\.?)?\s*([\d,]+(?:\.\d+)?)",
            combined,
            re.I,
        )
    if m_bn:
        v = _clean_num(m_bn.group(1))
        if v and 0.01 < v < 5000:
            net_assets_mn = round(v * 1000.0, 2)
    if net_assets_mn is None:
        m_mn = re.search(
            r"(?:net\s+assets|fund\s+size)\s*\(?(?:pkr|rs\.?)?\s*(?:in\s*)?(?:mln|mn|million)\)?\s*:?\s*(?:pkr|rs\.?)?\s*([\d,]+(?:\.\d+)?)",
            combined,
            re.I,
        )
        if m_mn:
            v = _clean_num(m_mn.group(1))
            if v and 0.5 < v < 2_000_000:
                net_assets_mn = round(v, 2)

    # 2. Stability Rating & AMC Rating
    stability_rating: str | None = None
    m_sr = re.search(
        r"(?:fund\s+)?stability\s+rating[^\n]{0,20}?([A]{1,3}[\+\-]?\s*\(f\)(?:\s*\((?:VIS|PACRA)\)|\s*by\s*(?:VIS|PACRA))?)",
        combined,
        re.I,
    )
    if m_sr:
        stability_rating = re.sub(r"\s+", " ", m_sr.group(1)).strip()[:48]

    amc_rating: str | None = None
    m_ar = re.search(r"amc\s+rating[^\n]{0,15}?(AM\d[\+\-]{0,2}(?:\s*\((?:VIS|PACRA)\))?)", combined, re.I)
    if not m_ar:
        m_ar = re.search(r"asset\s+manager\s+rating[^\n]{0,15}?(AM\d[\+\-]{0,2}(?:\s*\((?:VIS|PACRA)\))?)", combined, re.I)
    if m_ar:
        amc_rating = re.sub(r"\s+", " ", m_ar.group(1)).strip()[:48]

    # 3. WAM (Days), Modified Duration & YTM %
    wam_days: float | None = None
    m_wam = re.search(
        r"weighted\s+average\s+(?:time\s+to\s+)?maturity[^\d\n]{0,40}(\d+(?:\.\d+)?)\s*(days?|years?|months?)",
        combined,
        re.I,
    )
    if m_wam:
        v = _clean_num(m_wam.group(1))
        unit = m_wam.group(2).lower()
        if v is not None and v >= 0:
            if "year" in unit:
                wam_days = round(v * 365.0, 1)
            elif "month" in unit:
                wam_days = round(v * 30.0, 1)
            else:
                wam_days = round(v, 1)

    duration_days: float | None = None
    m_dur = re.search(r"modified\s+duration\s*\(?(days?|years?)\)?[^\d\n]{0,15}(\d+(?:\.\d+)?)", combined, re.I)
    if m_dur:
        v = _clean_num(m_dur.group(2))
        if v is not None:
            duration_days = round(v * 365.0 if "year" in m_dur.group(1).lower() else v, 2)

    ytm_pct: float | None = None
    m_ytm = re.search(r"yield\s+to\s+maturity\s*\(?%?\)?[^\d\n]{0,15}(\d{1,2}(?:\.\d+)?)", combined, re.I)
    if m_ytm:
        v = _clean_num(m_ytm.group(1))
        if v is not None and 0 < v < 40:
            ytm_pct = round(v, 2)

    # 4. Expense Ratio (TER MTD/YTD), Govt Levies & Management Fee
    ter_mtd: float | None = None
    ter_ytd: float | None = None
    govt_levy: float | None = None
    mgmt_fee: float | None = None

    # Meezan format: "Expense Ratio * Mtd | 4.28% Ytd | 4.02%" + "Mtd | 0.48% and Ytd | 0.49% representing Levies"
    m_ter_mz = re.search(r"expense\s+ratio[^0-9\n]{0,25}mtd\s*\|?\s*(\d+\.\d+)\s*%[^0-9\n]{0,20}ytd\s*\|?\s*(\d+\.\d+)\s*%", combined, re.I)
    if m_ter_mz:
        ter_mtd = _clean_num(m_ter_mz.group(1))
        ter_ytd = _clean_num(m_ter_mz.group(2))
    m_lev_mz = re.search(r"mtd\s*\|?\s*(\d+\.\d+)\s*%[^.\n]{0,30}levies", combined, re.I)
    if m_lev_mz:
        govt_levy = _clean_num(m_lev_mz.group(1))

    # HBL format: "Monthly Expense Ratio with Levies 1.58%" / "without Levies 1.32%" / "Yearly Expense Ratio with Levies 1.58%"
    if ter_mtd is None:
        m_hm = re.search(r"monthly\s+expense\s+ratio\s+with\s+levies[^\d\n]{0,15}(\d+\.\d+)\s*%", combined, re.I)
        m_hw = re.search(r"monthly\s+expense\s+ratio\s+without\s+levies[^\d\n]{0,15}(\d+\.\d+)\s*%", combined, re.I)
        m_hy = re.search(r"(?:yearly|annualized|ytd)\s+expense\s+ratio\s+with\s+levies[^\d\n]{0,15}(\d+\.\d+)\s*%", combined, re.I)
        if m_hm:
            ter_mtd = _clean_num(m_hm.group(1))
        if m_hy:
            ter_ytd = _clean_num(m_hy.group(1))
        if m_hm and m_hw and govt_levy is None:
            v1, v2 = _clean_num(m_hm.group(1)), _clean_num(m_hw.group(1))
            if v1 is not None and v2 is not None and v1 >= v2:
                govt_levy = round(v1 - v2, 3)

    # Atlas tabular TER format: "MTD 3.000% 0.095% ... 3.914% 3.448%"
    if ter_mtd is None:
        for ln in combined.splitlines():
            if re.match(r"^\s*MTD\s+\d+\.\d+%", ln):
                pcts = [float(x) for x in re.findall(r"(\d+\.\d+)\s*%", ln)]
                if len(pcts) >= 4:
                    mgmt_fee = pcts[0]
                    ter_mtd = pcts[-2]
                    if pcts[-2] >= pcts[-1]:
                        govt_levy = round(pcts[-2] - pcts[-1], 3)
            elif re.match(r"^\s*YTD\s+\d+\.\d+%", ln):
                pcts = [float(x) for x in re.findall(r"(\d+\.\d+)\s*%", ln)]
                if len(pcts) >= 4:
                    ter_ytd = pcts[-2]

    # Generic SECP format: "Total Expense Ratio (MTD): 3.45% (including 0.42% government levies)"
    if ter_mtd is None:
        m_gm = re.search(r"expense\s+ratio\s*\(?mtd\)?\s*:?\s*(\d+\.\d+)\s*%", combined, re.I)
        if m_gm:
            ter_mtd = _clean_num(m_gm.group(1))
    if ter_ytd is None:
        m_gy = re.search(r"expense\s+ratio\s*\(?ytd\)?\s*:?\s*(\d+\.\d+)\s*%", combined, re.I)
        if m_gy:
            ter_ytd = _clean_num(m_gy.group(1))
    if govt_levy is None:
        m_gl = re.search(r"(?:including|incl\.?)\s+(\d+\.\d+)\s*%\s*(?:government|govt\.?)\s*lev", combined, re.I)
        if m_gl:
            govt_levy = _clean_num(m_gl.group(1))

    if mgmt_fee is None:
        m_mf = re.search(r"actual\s+rate\s+of\s+management\s+fee[^\d\n]{0,20}(\d+(?:\.\d+)?)\s*%", combined, re.I)
        if not m_mf:
            m_mf = re.search(r"management\s+fee[^\d\n]{0,40}(\d+(?:\.\d+)?)\s*%", combined, re.I)
        if m_mf:
            v = _clean_num(m_mf.group(1))
            if v is not None and 0 <= v <= 6:
                mgmt_fee = round(v, 3)

    # Fund Manager, Launch Date, Risk Profile, Benchmark, Commentary
    fund_manager: str | None = None
    m_fm = re.search(r"fund\s+manager\s+([A-Z][A-Za-z.\s,]{3,60})", combined)
    if m_fm:
        cand = m_fm.group(1).strip().splitlines()[0].strip()
        if not any(bad in cand.lower() for bad in ("report", "comment", "review")):
            fund_manager = cand[:100]

    launch_date: str | None = None
    m_ld = re.search(r"launch\s+date[^\d\n]{0,15}(\d{1,2}[\-\s][A-Za-z]{3,9}[\-\s]\d{2,4})", combined, re.I)
    if m_ld:
        launch_date = m_ld.group(1).strip()[:32]

    risk_profile: str | None = None
    m_rp = re.search(r"risk\s+profile[^\n]{0,40}?\b(Very\s+High|High|Medium|Moderate|Low)\b", combined, re.I)
    if m_rp:
        risk_profile = m_rp.group(1).title()

    benchmark_name: str | None = None
    m_bm = re.search(r"\bBenchmark\s+([^\n]{3,120})", combined)
    if m_bm:
        bm_txt = m_bm.group(1).strip()
        if not bm_txt.lower().startswith(("return", "index 3.")):
            benchmark_name = bm_txt[:160]

    commentary: str | None = None
    m_cm = re.search(
        r"(?:fund\s+manager['’]s\s+comments|fund\s+review)\s*\n([^\n]{30,500}(?:\n[^\n]{15,350})?)",
        full,
        re.I,
    )
    if m_cm:
        commentary = re.sub(r"\s+", " ", m_cm.group(1)).strip()[:500]

    # 5. Asset Allocation (% of Total Assets)
    alloc_map: dict[str, float] = {}
    for blk in (right, col1, full):
        lines = blk.splitlines()
        in_alloc = False
        for ln in lines:
            low_ln = ln.lower()
            if re.search(r"\b(?:asset\s+allocation|asset\s+mix)\b", low_ln):
                in_alloc = True
                continue
            if in_alloc:
                if any(stop in low_ln for stop in ("sector allocation", "fund returns", "top ten", "top 10", "asset quality", "credit quality", "trailing performance", "monthly performance")):
                    in_alloc = False
                    continue
                pcts = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", ln)
                if not pcts:
                    continue
                val = _clean_num(pcts[0])
                if val is None or val <= 0 or val > 100.5:
                    continue
                for bucket_lbl, pat in ASSET_BUCKET_PATTERNS:
                    if re.search(pat, low_ln):
                        if bucket_lbl not in alloc_map:
                            alloc_map[bucket_lbl] = round(val, 2)
                        break
        if sum(alloc_map.values()) >= 40.0:
            break

    # Commentary fallback (e.g. Al Meezan equity funds state "97.05% invested in equities")
    if not alloc_map:
        m_eq = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*invested\s+in\s+equit", full, re.I)
        if m_eq:
            eq_v = _clean_num(m_eq.group(1))
            if eq_v and 10 <= eq_v <= 100:
                alloc_map["Equities"] = round(eq_v, 2)
                rem = round(max(0.0, 100.0 - eq_v), 2)
                if rem > 0.1:
                    alloc_map["Cash & Bank"] = rem

    asset_allocation = [
        {"label": k, "pct": v}
        for k, v in sorted(alloc_map.items(), key=lambda x: x[1], reverse=True)
    ]

    # 6. Top 10 Equity Holdings (% of Total Assets) — matched directly to canonical PSX tickers!
    # Guard: never extract equity holdings on pure Money Market / Cash / Sovereign Debt funds (Equities < 5%)
    eq_weight_in_alloc = alloc_map.get("Equities", 0.0)
    has_equity_sleeve = (eq_weight_in_alloc >= 5.0) or (not alloc_map and bool(re.search(r"top\s+(?:ten|10)\s+(?:equity|holdings|scrips)", full, re.I)))
    holdings_by_sym: dict[str, dict[str, Any]] = {}
    if has_equity_sleeve:
        for blk in (right, col2, full):
            lines = blk.splitlines()
            in_top = False
            saw_top_header = bool(re.search(r"top\s+(?:ten|10)|equity\s+holdings|major\s+holdings", blk, re.I))
            for ln in lines:
                low_ln = ln.lower()
                if re.search(r"top\s+(?:ten|10)|equity\s+holdings|major\s+holdings", low_ln):
                    in_top = True
                    continue
                if saw_top_header and not in_top:
                    continue
                if in_top and any(stop in low_ln for stop in ("fund returns", "trailing performance", "monthly performance", "credit quality", "asset quality", "details of non-compliant", "non-compliant investment")):
                    in_top = False
                    continue
                # Skip non-compliance / Shariah exclusion footnotes & Sukuk/TFC issuer tables
                if any(bad in low_ln for bad in ("non-shariah", "non-compliance", "non-compliant", "leverage", "sukuk", "tfc", "commercial paper", "benchmark")):
                    continue
                m_pct = re.search(r"(\d{1,2}\.\d{1,2})\s*%?", ln)
                if not m_pct:
                    continue
                wt = _clean_num(m_pct.group(1))
                if wt is None or wt < 0.4 or wt > 28.0:
                    continue
                norm_ln = _norm(ln)
                for key_norm, sym, canonical_name, sec in psx_matchers:
                    if key_norm in norm_ln:
                        if sym not in holdings_by_sym:
                            holdings_by_sym[sym] = {
                                "symbol": sym,
                                "name": canonical_name,
                                "sector": sec,
                                "weight_pct": round(wt, 2),
                            }
                        break
            if len(holdings_by_sym) >= 5:
                break

    top_holdings = sorted(holdings_by_sym.values(), key=lambda x: x["weight_pct"], reverse=True)[:10]

    # 7. Sector Allocation (% of Total Assets)
    sector_map: dict[str, float] = {}
    for blk in (right, col1, full):
        lines = blk.splitlines()
        in_sec = False
        for ln in lines:
            low_ln = ln.lower()
            if "sector allocation" in low_ln:
                in_sec = True
                continue
            if in_sec:
                if any(stop in low_ln for stop in ("top ten", "top 10", "fund returns", "asset quality", "trailing performance", "monthly performance")):
                    in_sec = False
                    continue
                nums = re.findall(r"(\d{1,2}\.\d{1,2})\s*%?", ln)
                if not nums:
                    continue
                val = _clean_num(nums[0])
                if val is None or val <= 0.1 or val > 100.0:
                    continue
                for sec_lbl, pat in CANONICAL_PSX_SECTORS:
                    if re.search(pat, low_ln):
                        if sec_lbl not in sector_map:
                            sector_map[sec_lbl] = round(val, 2)
                        break
        if len(sector_map) >= 2:
            break

    # Derive sector mix from Top-10 holdings when PDF draws sector mix only as a vector pie chart
    if not sector_map and top_holdings:
        for h in top_holdings:
            s_lbl = h.get("sector") or "Others"
            sector_map[s_lbl] = round(sector_map.get(s_lbl, 0.0) + float(h["weight_pct"]), 2)

    sector_allocation = [
        {"sector": k, "pct": v}
        for k, v in sorted(sector_map.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    # 8. Credit / Asset Quality (% of Total Assets) for Money Market / Income / Sovereign funds
    credit_map: dict[str, float] = {}
    for blk in (right, left, full):
        lines = blk.splitlines()
        in_cq = False
        for ln in lines:
            low_ln = ln.lower()
            if re.search(r"\b(?:asset\s+quality|credit\s+quality|rating\s+profile)\b", low_ln):
                in_cq = True
                continue
            if in_cq:
                if any(stop in low_ln for stop in ("expense ratio", "portfolio:", "monthly performance", "fund returns", "investment committee", "disclaimer")):
                    in_cq = False
                    continue
                # Note: Meezan prints multiple rating-pct pairs on one line e.g. "AAA 87.03% Others 1.67%"
                for r_lbl, pat in CREDIT_RATING_PATTERNS:
                    m_r = re.search(rf"{pat}\s*[:\|]?\s*(\d{{1,3}}(?:\.\d+)?)\s*%", ln)
                    if m_r:
                        v = _clean_num(m_r.group(1))
                        if v is not None and 0 < v <= 100.0 and r_lbl not in credit_map:
                            credit_map[r_lbl] = round(v, 2)
        if sum(credit_map.values()) >= 20.0:
            break

    credit_quality = [
        {"rating": k, "pct": v}
        for k, v in sorted(credit_map.items(), key=lambda x: x[1], reverse=True)
    ]

    # 9. Returns summary snippet (1M / FYTD / 1Y / 3Y / 5Y)
    returns_table: dict[str, float] = {}
    for key, pat in (
        ("mtd_pct", r"1\s*month\s+(?:annualized\s+)?return[^\-\d\n]{0,20}(-?\d+(?:\.\d+)?)\s*%"),
        ("fytd_pct", r"(?:year\s+to\s+date|fytd)\s+(?:annualized\s+)?return[^\-\d\n]{0,20}(-?\d+(?:\.\d+)?)\s*%"),
        ("1y_pct", r"1\s*year\s+(?:annualized\s+)?return[^\-\d\n]{0,20}(-?\d+(?:\.\d+)?)\s*%"),
        ("3y_pct", r"3\s*years?\s+(?:annualized\s+)?return[^\-\d\n]{0,20}(-?\d+(?:\.\d+)?)\s*%"),
        ("5y_pct", r"5\s*years?\s+(?:annualized\s+)?return[^\-\d\n]{0,20}(-?\d+(?:\.\d+)?)\s*%"),
    ):
        m_r = re.search(pat, combined, re.I)
        if m_r:
            v = _clean_num(m_r.group(1))
            if v is not None and -95 < v < 5000:
                returns_table[key] = round(v, 2)

    # Require at least some real FMR analytical payload before accepting the page
    has_signal = bool(
        asset_allocation
        or top_holdings
        or credit_quality
        or net_assets_mn is not None
        or wam_days is not None
        or stability_rating is not None
    )
    if not has_signal:
        return None

    return {
        "asset_id": fund_info["asset_id"],
        "symbol": fund_info["symbol"],
        "name": fund_info["name"],
        "amc_name": fund_info["amc_name"],
        "report_month": report_month,
        "report_label": report_label,
        "fund_manager": fund_manager,
        "stability_rating": stability_rating,
        "amc_rating": amc_rating,
        "risk_profile": risk_profile,
        "launch_date": launch_date,
        "benchmark_name": benchmark_name,
        "net_assets_mn": net_assets_mn,
        "wam_days": wam_days,
        "duration_days": duration_days,
        "ytm_pct": ytm_pct,
        "ter_mtd_pct": ter_mtd,
        "ter_ytd_pct": ter_ytd,
        "govt_levy_pct": govt_levy,
        "mgmt_fee_pct": mgmt_fee,
        "asset_allocation_json": asset_allocation,
        "sector_allocation_json": sector_allocation,
        "top_holdings_json": top_holdings,
        "credit_quality_json": credit_quality,
        "returns_table_json": returns_table,
        "manager_commentary": commentary,
    }


def parse_secp_fmr_pdf_bytes_sync(
    pdf_bytes: bytes,
    *,
    fund_catalog: list[dict[str, Any]],
    psx_assets: list[dict[str, Any]],
    amc_hint: str = "",
    filename_hint: str = "",
    target_symbol: str | None = None,
    max_pages: int = 75,
) -> list[dict[str, Any]]:
    """
    Synchronous `pdfplumber` worker that parses an entire multi-fund Pakistani AMC FMR booklet
    OR a single-fund FMR PDF into structured per-fund snapshots.
    """
    psx_matchers = _build_psx_company_matcher(psx_assets)
    target_fund = (
        next((f for f in fund_catalog if str(f["symbol"]).upper() == target_symbol.strip().upper()), None)
        if target_symbol
        else None
    )
    parsed_by_asset: dict[str, dict[str, Any]] = {}
    doc_month = ""
    doc_label = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for _pi, page in enumerate(pdf.pages[:max_pages]):
            try:
                bands = _extract_page_bands(page)
            except Exception:
                continue
            full = bands["full"]
            if not doc_month:
                doc_month, doc_label = _detect_report_month(full, filename_hint=filename_hint)
            if _is_non_fund_page(full):
                continue

            if target_fund is not None and len(pdf.pages) <= 4:
                matched = target_fund
            else:
                matched = _match_page_to_fund_catalog(
                    header_txt=f"{bands['left'][:350]} {full[:400]}",
                    full_txt=full,
                    fund_catalog=fund_catalog,
                    amc_hint=amc_hint,
                )
            if matched is None:
                continue
            if target_fund is not None and matched["asset_id"] != target_fund["asset_id"]:
                continue

            page_month, page_label = _detect_report_month(full, filename_hint=filename_hint)
            snap = parse_secp_fmr_page_text(
                bands,
                fund_info=matched,
                report_month=page_month or doc_month,
                report_label=page_label or doc_label,
                psx_matchers=psx_matchers,
            )
            if snap is None:
                continue

            # If a fund spans two pages or appears twice, keep the richer snapshot (or merge missing lists)
            prev = parsed_by_asset.get(matched["asset_id"])
            if prev is None:
                parsed_by_asset[matched["asset_id"]] = snap
            else:
                for list_key in ("asset_allocation_json", "sector_allocation_json", "top_holdings_json", "credit_quality_json"):
                    if not prev.get(list_key) and snap.get(list_key):
                        prev[list_key] = snap[list_key]
                for scalar_key in ("net_assets_mn", "wam_days", "stability_rating", "ter_mtd_pct", "ter_ytd_pct", "fund_manager"):
                    if prev.get(scalar_key) is None and snap.get(scalar_key) is not None:
                        prev[scalar_key] = snap[scalar_key]

    return list(parsed_by_asset.values())


async def ingest_fmr_pdf_into_db(
    db: AsyncSession,
    pdf_bytes: bytes,
    *,
    amc_hint: str = "",
    filename_hint: str = "",
    source_url: str | None = None,
    source_label: str = "SECP FMR PDF",
    target_symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Parse an FMR PDF (booklet or single fund) and upsert `FundFmrSnapshot` + enrich `MutualFundProfile`."""
    fund_assets = (
        await db.execute(
            select(Asset)
            .options(selectinload(Asset.mutual_fund_profile))
            .where(
                Asset.owner_user_id.is_(None),
                Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
            )
        )
    ).scalars().all()
    fund_catalog = [
        {
            "asset_id": a.id,
            "symbol": a.symbol,
            "name": a.name,
            "amc_name": a.mutual_fund_profile.amc_name if a.mutual_fund_profile else "",
            "is_shariah_compliant": a.is_shariah_compliant,
        }
        for a in fund_assets
        if a.mutual_fund_profile is not None
    ]
    psx_rows = (
        await db.execute(
            select(Asset.symbol, Asset.name, Asset.sector_or_category).where(
                Asset.owner_user_id.is_(None),
                Asset.asset_class == "PSX_EQUITY",
            )
        )
    ).all()
    psx_assets = [{"symbol": s, "name": n, "sector_or_category": sec} for s, n, sec in psx_rows]

    parsed_list = await asyncio.to_thread(
        parse_secp_fmr_pdf_bytes_sync,
        pdf_bytes,
        fund_catalog=fund_catalog,
        psx_assets=psx_assets,
        amc_hint=amc_hint,
        filename_hint=filename_hint,
        target_symbol=target_symbol,
    )
    if not parsed_list:
        return []

    asset_ids = [p["asset_id"] for p in parsed_list]
    existing_snaps = {
        s.asset_id: s
        for s in (
            await db.execute(select(FundFmrSnapshot).where(FundFmrSnapshot.asset_id.in_(asset_ids)))
        ).scalars()
    }
    existing_profs = {
        p.asset_id: p
        for p in (
            await db.execute(select(MutualFundProfile).where(MutualFundProfile.asset_id.in_(asset_ids)))
        ).scalars()
    }

    now = datetime.now(timezone.utc)
    def _dec_opt(v: Any) -> Decimal | None:
        return D(str(v)) if v is not None else None

    for item in parsed_list:
        aid = item["asset_id"]
        snap = existing_snaps.get(aid)
        if snap is None:
            snap = FundFmrSnapshot(asset_id=aid)
            db.add(snap)
        snap.report_month = item["report_month"]
        snap.report_label = item["report_label"]
        snap.amc_name = item["amc_name"]
        snap.fund_manager = item["fund_manager"]
        snap.stability_rating = item["stability_rating"]
        snap.amc_rating = item["amc_rating"]
        snap.risk_profile = item["risk_profile"]
        snap.launch_date = item["launch_date"]
        snap.benchmark_name = item["benchmark_name"]
        snap.net_assets_mn = _dec_opt(item["net_assets_mn"])
        snap.wam_days = _dec_opt(item["wam_days"])
        snap.duration_days = _dec_opt(item["duration_days"])
        snap.ytm_pct = _dec_opt(item["ytm_pct"])
        snap.ter_mtd_pct = _dec_opt(item["ter_mtd_pct"])
        snap.ter_ytd_pct = _dec_opt(item["ter_ytd_pct"])
        snap.govt_levy_pct = _dec_opt(item["govt_levy_pct"])
        snap.mgmt_fee_pct = _dec_opt(item["mgmt_fee_pct"])
        snap.asset_allocation_json = item["asset_allocation_json"]
        snap.sector_allocation_json = item["sector_allocation_json"]
        snap.top_holdings_json = item["top_holdings_json"]
        snap.credit_quality_json = item["credit_quality_json"]
        snap.returns_table_json = item["returns_table_json"]
        snap.manager_commentary = item["manager_commentary"]
        snap.source_url = source_url
        snap.source_label = source_label
        snap.updated_at = now

        # Enrich MutualFundProfile with authoritative FMR AUM, TER, Management Fee & Risk Profile
        prof = existing_profs.get(aid)
        if prof is not None:
            if item["net_assets_mn"] and item["net_assets_mn"] > 0:
                prof.aum_mn = D(str(item["net_assets_mn"])).quantize(D("0.01"))
            best_ter = item["ter_ytd_pct"] or item["ter_mtd_pct"]
            if best_ter and 0 < best_ter < 10:
                prof.ter_pct = D(str(best_ter)).quantize(D("0.01"))
            if item["mgmt_fee_pct"] is not None and 0 <= item["mgmt_fee_pct"] <= 6:
                prof.management_fee_pct = D(str(item["mgmt_fee_pct"])).quantize(D("0.01"))
            if item["risk_profile"]:
                prof.risk_profile = item["risk_profile"]

    await db.commit()
    return parsed_list


def _pick_latest_fmr_pdf_urls(html: str, base_url: str, max_pdfs: int = 1) -> list[str]:
    """Select the newest monthly FMR PDF link(s) from an AMC FMR download archive page."""
    raw_links = list(dict.fromkeys(re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.I)))
    scored: list[tuple[tuple[int, int], str]] = []
    for href in raw_links:
        full_url = urljoin(base_url, href).replace(" ", "%20")
        low = full_url.lower()
        if not any(k in low for k in ("fmr", "fund-manager", "fund_manager", "monthly", "report")):
            continue
        # Score by detected (year, month) from filename only (no fallback to today!)
        fname = full_url.split("?")[0].rsplit("/", 1)[-1]
        ym, _ = _detect_report_month("", filename_hint=fname.replace("%20", " "), fallback_today=False)
        if ym:
            y_i, m_i = int(ym[:4]), int(ym[5:7])
            score = (y_i, m_i)
        else:
            score = (0, 0)
        scored.append((score, full_url))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Deduplicate keeping up to `max_pdfs` (e.g. Atlas publishes separate Shariah & Conventional PDFs for same month!)
    selected: list[str] = []
    seen: set[str] = set()
    for _, url in scored:
        if url in seen:
            continue
        seen.add(url)
        selected.append(url)
        if len(selected) >= max_pdfs:
            break
    return selected


class FmrReportScraper(BaseScraper):
    """
    Automatically discovers and parses the latest official monthly SECP Fund Manager Report (FMR)
    PDF booklets across open Pakistani AMC archives (Al Meezan, HBL, Atlas, JSIL, ABL).
    """

    name = "fmr_reports"

    async def run(self, db: AsyncSession) -> int:
        total_funds_updated = 0
        async with CurlAsyncSession(impersonate="chrome", timeout=30) as session:
            for amc_name, archive_url, max_pdfs in AMC_FMR_ARCHIVE_PAGES:
                try:
                    r_arch = await session.get(archive_url, headers=HEADERS, allow_redirects=True, verify=False)
                    if r_arch.status_code != 200:
                        logger.info("FMR archive %s returned HTTP %s", amc_name, r_arch.status_code)
                        continue
                    pdf_urls = _pick_latest_fmr_pdf_urls(r_arch.text, str(r_arch.url), max_pdfs=max_pdfs)
                    if not pdf_urls:
                        continue
                    for pdf_url in pdf_urls:
                        try:
                            r_pdf = await session.get(pdf_url, headers=HEADERS, allow_redirects=True, verify=False, timeout=45)
                            if r_pdf.status_code != 200 or len(r_pdf.content) < 25_000:
                                continue
                            parsed = await ingest_fmr_pdf_into_db(
                                db,
                                r_pdf.content,
                                amc_hint=amc_name,
                                filename_hint=pdf_url.split("/")[-1],
                                source_url=pdf_url,
                                source_label=f"Auto SECP FMR ({amc_name.split()[0]})",
                            )
                            logger.info("FMR parsed %d funds from %s (%s)", len(parsed), amc_name, pdf_url)
                            total_funds_updated += len(parsed)
                        except Exception as exc:
                            logger.warning("FMR PDF download/parse skipped for %s (%s): %s", amc_name, pdf_url, exc)
                except Exception as exc:
                    logger.warning("FMR archive fetch failed for %s: %s", amc_name, exc)

        return total_funds_updated
