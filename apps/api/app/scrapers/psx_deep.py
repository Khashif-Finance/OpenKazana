"""
Deep PSX Equity Fundamentals & PUCARS Financial Statement PDF Extractor (`pdfplumber`).

Extracts authoritative Balance Sheet, Cash Flow, and Valuation metrics for Pakistan
listed equities from:
1. PSX DPS Company Financials, Ratios, Shares & Financial Report PDF links (`/company/{SYM}`)
2. Official PUCARS Quarterly / Annual Financial Statement PDFs (`/download/document/{ID}.pdf`)
   using `pdfplumber`, computing:
   - Total Assets, Shareholders Equity, BVPS, Total Debt, Cash, OCF, CapEx, Free Cash Flow
   - Real P/B Ratio, ROE%, ROA%, Current Ratio, Debt/Equity, Debt/Assets (for KMI Shariah screening)
   - DCF Intrinsic Value (anchored to live SBP Risk-Free Rate), Piotroski F-Score, Altman Z-Score & DuPont ROE
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pdfplumber
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Asset, EquityFinancialSnapshot, PsxProfile, ShariahMetric
from app.models.portfolio import Transaction, WatchlistItem
from app.scrapers.base import BaseScraper
from app.scrapers.sbp_macro import get_live_risk_free_rate_pct
from app.services.deep_fundamentals import (
    compute_altman_z_score,
    compute_dcf_intrinsic_value,
    compute_dupont_roe,
    compute_piotroski_f_score,
)

logger = logging.getLogger("openkhazana.scrapers.psx_deep")
D = Decimal

PSX_BASE_URL = "https://dps.psx.com.pk"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/pdf,*/*;q=0.8",
    "Referer": "https://dps.psx.com.pk/",
}

MAX_PDF_BYTES = 12_000_000  # Skip giant image-heavy annual reports > 12MB during fast trickle runs


def _num(raw: str | None) -> float | None:
    if not raw:
        return None
    s = raw.strip()
    if s in {"-", "--", "N/A", ""}:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace("%", "").strip()
    m = re.match(r"^-?\d+(?:\.\d+)?$", s)
    if not m:
        return None
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


def _extract_last_two_numbers(line: str) -> tuple[float | None, float | None]:
    """
    Extract numeric columns at the end of a financial statement PDF line.
    Skips percentages and note references (single 1-3 digit integer before large formatted
    numbers) when multiple columns appear on the row.
    """
    tokens = re.findall(r"\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?(?!\s*%)|\b-\b", line)
    parsed: list[float] = []
    for tok in tokens:
        if tok == "-":
            parsed.append(0.0)
            continue
        val = _num(tok)
        if val is not None:
            parsed.append(val)
    if not parsed:
        return None, None
    if len(parsed) >= 3:
        # Often [note_number, current_period, prior_period] or [qtr_cur, qtr_prev, 9m_cur, 9m_prev]
        # Prefer the first two full-period figures if 4 columns exist without a note, else last two
        last_two = parsed[-2:]
        return last_two[0], last_two[1]
    if len(parsed) == 2:
        # Detect [note_number, single_value] vs [current, prior]
        if abs(parsed[0]) < 100 and abs(parsed[1]) >= 1000 and "." not in line.split()[0]:
            return parsed[1], None
        return parsed[0], parsed[1]
    return parsed[0], None


def _detect_block_scale_div(block: str, fallback: tuple[float, str] = (1000.0, "PKR_000")) -> tuple[float, str]:
    """Detect statement unit scaling (in '000 -> 1000, in millions -> 1, raw Rupees -> 1e6)."""
    if not block:
        return fallback
    head = block[:1800].lower()
    if re.search(
        r"(?:rupees|rs\.?|amount)\s+in\s+[‘'\"\(]?\s*000|\(\s*(?:rupees|rs\.?)\s+in\s+[‘'\"]?000\s*\)|rs\.?\s*000\s*\)|(?:rupees|rs\.?)\s+in\s+thousands?|nearest\s+thousand\s+rupees",
        head,
    ):
        return 1000.0, "PKR_000"
    if re.search(r"(?:rupees|rs\.?|amount)\s+in\s+millions?", head):
        return 1.0, "PKR_MN"
    if re.search(r"(?:^|[\s\-|])(?:rupees|rs\.?)(?:[\s\-|]+(?:rupees|rs\.?))*[\s\-|]*$", head, re.M):
        # Explicit column headers printing raw "Rupees   Rupees" without '000 or million
        if "per share" not in head[:400]:
            return 1_000_000.0, "PKR_RAW"
    return fallback


def parse_dps_company_deep_meta(html: str) -> dict[str, Any]:
    """
    Parse Shares Outstanding, Ratios table (GPM%, NPM%, EPS Growth%, PEG), and best
    Quarterly/Annual Financial Report PDF URL from `https://dps.psx.com.pk/company/{SYM}`.
    """
    soup = BeautifulSoup(html, "lxml")
    shares_mn: float | None = None
    market_cap_mn: float | None = None
    gross_margin_pct: float | None = None
    net_margin_pct: float | None = None
    annual_rev_mn: float | None = None
    annual_pat_mn: float | None = None

    # Profile stat boxes
    stat_items = soup.select(".stats_item")
    for box in stat_items:
        lbl = (box.select_one(".stats_label") or box).get_text(" ", strip=True).lower()
        val_el = box.select_one(".stats_value")
        val_txt = val_el.get_text(" ", strip=True) if val_el else ""
        num_val = _num(val_txt.split()[0] if val_txt else "")
        if "shares" in lbl and "free" not in lbl and num_val:
            # DPS prints raw shares e.g. 1,466,875,000 or 1,466.88M
            if "m" in val_txt.lower():
                shares_mn = round(num_val, 4)
            elif "b" in val_txt.lower():
                shares_mn = round(num_val * 1000.0, 4)
            elif num_val > 1_000_000:
                shares_mn = round(num_val / 1_000_000.0, 4)
        elif "market cap" in lbl and num_val:
            if num_val > 1_000_000:
                market_cap_mn = round(num_val / 1_000_000.0, 2)
            else:
                market_cap_mn = round(num_val, 2)

    # Scan HTML tables for Annual Income & Ratios
    for tbl in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in tbl.find_all("tr")]
        for r in rows:
            if len(r) < 2:
                continue
            key = r[0].lower()
            v = _num(r[1])
            if v is None:
                continue
            if ("sales" in key or "total income" in key or "mark-up earned" in key) and annual_rev_mn is None:
                # PSX financials table is in Rupees '000
                annual_rev_mn = round(v / 1000.0, 2)
            elif "profit after tax" in key and annual_pat_mn is None:
                annual_pat_mn = round(v / 1000.0, 2)
            elif "gross profit margin" in key and gross_margin_pct is None:
                gross_margin_pct = round(v, 2)
            elif "net profit margin" in key and net_margin_pct is None:
                net_margin_pct = round(v, 2)

    # Locate candidate financial statement PDF links (prefer Annual / Quarterly Reports over 1-page notices)
    pdf_candidates: list[tuple[int, str, str]] = []
    for tr in soup.select("table tr"):
        txt = tr.get_text(" ", strip=True)
        low = txt.lower()
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if "/download/document/" not in href or not href.lower().endswith(".pdf"):
                continue
            priority = 0
            if "annual report" in low:
                priority = 110
            elif "quarterly report" in low or "half yearly" in low or "financial statements" in low or "accounts" in low:
                priority = 100
            elif "financial results" in low:
                priority = 40
            else:
                continue
            full_url = f"{PSX_BASE_URL}{href}" if href.startswith("/") else href
            pdf_candidates.append((priority, txt[:140], full_url))

    pdf_candidates.sort(key=lambda x: x[0], reverse=True)
    return {
        "shares_mn": shares_mn,
        "market_cap_mn": market_cap_mn,
        "gross_margin_pct": gross_margin_pct,
        "net_margin_pct": net_margin_pct,
        "annual_rev_mn": annual_rev_mn,
        "annual_pat_mn": annual_pat_mn,
        "pdf_urls": [(title, url) for _, title, url in pdf_candidates[:6]],
    }


def parse_pucars_financial_pdf_text(
    pages_text: list[str],
    symbol: str,
    *,
    sector: str = "",
    last_price: float | None = None,
    default_shares_mn: float | None = None,
    market_cap_mn: float | None = None,
    dps_meta: dict[str, Any] | None = None,
    risk_free_rate_pct: float = 12.0,
) -> dict[str, Any] | None:
    """
    Parse Balance Sheet, Income Statement & Cash Flow metrics from extracted `pdfplumber`
    PUCARS report page strings, handling both non-financial corporates and Pakistani banks.
    """
    relevant_pages = [
        t for t in pages_text
        if t and len(t.strip()) > 120
    ]
    if not relevant_pages and not dps_meta:
        return None

    bs_text = ""
    pl_text = ""
    cf_text = ""
    period_label = "PUCARS-Latest"

    for txt in relevant_pages:
        low = txt.lower()
        head_low = txt[:650].lower()
        is_narrative = any(
            skip in head_low
            for skip in (
                "contents",
                "directors' review",
                "directors’ review",
                "directors' report",
                "directors’ report",
                "chairman's review",
                "chairman’s review",
                "notes to and forming part",
                "notes to the ",
            )
        )
        if is_narrative:
            continue

        if ("statement of financial position" in head_low or "balance sheet" in head_low) and "total assets" in low:
            if not bs_text:
                bs_text = txt
                m_dt = re.search(
                    r"(?:as\s+at|ended)\s+([a-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[a-z]+\s+\d{4})",
                    low,
                )
                if m_dt:
                    period_label = m_dt.group(1).title()[:24]
        elif (
            "profit and loss" in head_low
            or "profit or loss" in head_low
            or "statement of comprehensive income" in head_low
        ) and ("profit after tax" in low or "profit before tax" in low or "taxation" in low or "loss after tax" in low):
            if not pl_text:
                pl_text = txt
        elif ("statement of cash flows" in head_low or "cash flow statement" in head_low or "cash flows" in head_low) and (
            "operating activities" in low or "cash generated from" in low
        ):
            if not cf_text:
                cf_text = txt

    # Detect document fallback scale from primary statement pages first (avoiding Directors' Review text)
    stmt_sample = "\n".join([b for b in (bs_text, pl_text, cf_text) if b]) or "\n".join(relevant_pages[:25])
    fallback_scale = _detect_block_scale_div(stmt_sample, fallback=(1000.0, "PKR_000"))
    bs_div, bs_scale_tag = _detect_block_scale_div(bs_text, fallback=fallback_scale)
    pl_div, pl_scale_tag = _detect_block_scale_div(pl_text, fallback=fallback_scale)
    cf_div, cf_scale_tag = _detect_block_scale_div(cf_text, fallback=fallback_scale)

    def _scaled_with(v: float | None, div: float) -> float | None:
        return round(v / div, 2) if v is not None else None

    def _scan_lines(block: str, patterns: list[str]) -> tuple[float | None, float | None]:
        for ln in block.splitlines():
            low_ln = ln.lower()
            for pat in patterns:
                if re.search(pat, low_ln):
                    c1, c2 = _extract_last_two_numbers(ln)
                    if c1 is not None:
                        return c1, c2
        return None, None

    total_assets, prev_assets = _scan_lines(bs_text, [r"\btotal\s+assets\b"])
    cur_assets, prev_cur_assets = _scan_lines(bs_text, [r"\btotal\s+current\s+assets\b", r"^current\s+assets\b"])
    total_liab, _ = _scan_lines(bs_text, [r"\btotal\s+liabilities\b"])
    cur_liab, prev_cur_liab = _scan_lines(bs_text, [r"\btotal\s+current\s+liabilities\b", r"^current\s+liabilities\b"])
    equity, _ = _scan_lines(
        bs_text,
        [
            r"\btotal\s+shareholders['’]?\s*equity\b",
            r"\btotal\s+equity\s+attributable\b",
            r"\bequity\s+attributable\s+to\b",
            r"\btotal\s+equity(?!\s*(?:&|and)\s+liabilities)\b",
            r"\bshareholders['’]?\s*equity\b",
            r"\bcapital\s+and\s+reserves\b",
            r"\bnet\s+assets\b",
        ],
    )
    share_cap, _ = _scan_lines(bs_text, [r"\b(?:issued,\s+subscribed\s+and\s+paid.?up\s+)?share\s+capital\b"])
    retained, _ = _scan_lines(bs_text, [r"\bunappropriated\s+profit\b", r"\bretained\s+earnings\b"])

    # Cash & Bank Balances
    cash_1, _ = _scan_lines(
        bs_text,
        [
            r"\bcash\s+and\s+cash\s+equivalents\b",
            r"\bcash\s+and\s+bank\s+balances\b",
            r"\bcash\s+and\s+balances\s+with\s+treasury\s+banks\b",
        ],
    )
    cash_2, _ = _scan_lines(bs_text, [r"\bbalances\s+with\s+other\s+banks\b"])
    cash_tot = (cash_1 or 0.0) + (cash_2 or 0.0) if (cash_1 is not None or cash_2 is not None) else None

    # Debt items (sum non-overlapping debt lines safely)
    lt_debt, prev_lt_debt = _scan_lines(
        bs_text,
        [
            r"\blong.?term\s+financing\b",
            r"\blong.?term\s+borrowings?\b",
            r"\bsub.?ordinated\s+sukuks?\b",
            r"\bterm\s+finance\s+certificates?\b",
        ],
    )
    st_debt, _ = _scan_lines(
        bs_text,
        [
            r"\bshort.?term\s+borrowings?\b",
            r"\brunning\s+finance\b",
            r"\blease\s+liabilit",
        ],
    )
    tot_debt = ((lt_debt or 0.0) + (st_debt or 0.0)) if (lt_debt is not None or st_debt is not None) else None

    # Income statement & Cash Flow
    rev, prev_rev = _scan_lines(
        pl_text,
        [
            r"\bsales\s*[-–]?\s*net\b",
            r"\bnet\s+sales\b",
            r"\brevenue\s+from\s+contracts?\b",
            r"\btotal\s+income\b",
            r"\brevenue\b",
        ],
    )
    gp, prev_gp = _scan_lines(pl_text, [r"\bgross\s+profit\b"])
    ebit, _ = _scan_lines(pl_text, [r"\boperating\s+profit\b", r"\bprofit\s+from\s+operations\b", r"\bprofit\s+before\s+taxation\b"])
    int_exp, _ = _scan_lines(pl_text, [r"\bfinance\s+cost\b", r"\bfinance\s+charges\b"])
    pat, prev_pat = _scan_lines(
        pl_text,
        [
            r"\bprofit\s*/\s*\(loss\)\s+after\s+taxation\b",
            r"\b(?:profit|loss)\s+after\s+taxation\b",
            r"\bprofit\s+for\s+the\s+(?:year|period)\b",
        ],
    )

    ocf, _ = _scan_lines(
        cf_text,
        [
            r"\bnet\s+cash\b.*?\boperating\s+activities\b",
            r"\bcash\s+(?:generated\s+from|flows\s+from)\s+operations\b",
        ],
    )
    capex_raw, _ = _scan_lines(
        cf_text,
        [
            r"\badditions?\s+to\s+property,\s+plant\s+and\s+equipment\b",
            r"\bcapital\s+expenditure\b",
            r"\bpurchase\s+of\s+property,\s+plant\b",
        ],
    )

    tot_assets_mn = _scaled_with(total_assets, bs_div)
    prev_assets_mn = _scaled_with(prev_assets, bs_div)
    cur_assets_mn = _scaled_with(cur_assets, bs_div)
    prev_cur_assets_mn = _scaled_with(prev_cur_assets, bs_div)
    tot_liab_mn = _scaled_with(total_liab, bs_div)
    cur_liab_mn = _scaled_with(cur_liab, bs_div)
    prev_cur_liab_mn = _scaled_with(prev_cur_liab, bs_div)
    eq_mn = _scaled_with(equity, bs_div)
    if tot_liab_mn is None and tot_assets_mn is not None and eq_mn is not None and tot_assets_mn >= eq_mn:
        tot_liab_mn = round(tot_assets_mn - eq_mn, 2)
    share_cap_mn = _scaled_with(share_cap, bs_div)
    retained_mn = _scaled_with(retained, bs_div)
    cash_mn = _scaled_with(cash_tot, bs_div)
    lt_debt_mn = _scaled_with(lt_debt, bs_div)
    prev_lt_debt_mn = _scaled_with(prev_lt_debt, bs_div)
    tot_debt_mn = _scaled_with(tot_debt, bs_div)

    rev_mn = _scaled_with(rev, pl_div) or (dps_meta.get("annual_rev_mn") if dps_meta else None)
    prev_rev_mn = _scaled_with(prev_rev, pl_div)
    gp_mn = _scaled_with(gp, pl_div)
    ebit_mn = _scaled_with(ebit, pl_div)
    int_exp_mn = _scaled_with(int_exp, pl_div)
    ni_mn = _scaled_with(pat, pl_div) or (dps_meta.get("annual_pat_mn") if dps_meta else None)
    prev_ni_mn = _scaled_with(prev_pat, pl_div)
    ocf_mn = _scaled_with(ocf, cf_div)
    capex_mn = abs(_scaled_with(capex_raw, cf_div)) if capex_raw is not None else None
    fcf_mn = round(ocf_mn - (capex_mn or 0.0), 2) if ocf_mn is not None else None

    shares_mn = default_shares_mn or (dps_meta.get("shares_mn") if dps_meta else None)
    if shares_mn is None and share_cap_mn and share_cap_mn > 0:
        # PSX standard par value is Rs 10/share -> Share Capital (PKR Mn) / 10 = Millions of shares
        shares_mn = round(share_cap_mn / 10.0, 4)

    if tot_assets_mn is None and eq_mn is None and rev_mn is None:
        return None

    bvps = round(eq_mn / shares_mn, 2) if eq_mn is not None and shares_mn and shares_mn > 0 else None
    gm_pct = (
        round((gp_mn / rev_mn) * 100.0, 2)
        if gp_mn is not None and rev_mn and rev_mn > 0
        else (dps_meta.get("gross_margin_pct") if dps_meta else None)
    )
    om_pct = round((ebit_mn / rev_mn) * 100.0, 2) if ebit_mn is not None and rev_mn and rev_mn > 0 else None
    nm_pct = (
        round((ni_mn / rev_mn) * 100.0, 2)
        if ni_mn is not None and rev_mn and rev_mn > 0
        else (dps_meta.get("net_margin_pct") if dps_meta else None)
    )
    roe_f = round((ni_mn / eq_mn) * 100.0, 2) if ni_mn is not None and eq_mn and eq_mn > 0 else None
    roa_f = round((ni_mn / tot_assets_mn) * 100.0, 2) if ni_mn is not None and tot_assets_mn and tot_assets_mn > 0 else None
    cur_ratio = round(cur_assets_mn / cur_liab_mn, 2) if cur_assets_mn is not None and cur_liab_mn and cur_liab_mn > 0 else None
    d_to_e = round(tot_debt_mn / eq_mn, 2) if tot_debt_mn is not None and eq_mn and eq_mn > 0 else None
    d_to_a = round((tot_debt_mn / tot_assets_mn) * 100.0, 2) if tot_debt_mn is not None and tot_assets_mn and tot_assets_mn > 0 else None
    int_cov = round(ebit_mn / abs(int_exp_mn), 2) if ebit_mn is not None and int_exp_mn and abs(int_exp_mn) > 0 else None
    eff_mcap = market_cap_mn or (round(last_price * shares_mn, 2) if last_price and shares_mn else None)
    fcf_yield = round((fcf_mn / eff_mcap) * 100.0, 2) if fcf_mn is not None and eff_mcap and eff_mcap > 0 else None

    invested_cap = (eq_mn or 0.0) + (tot_debt_mn or 0.0) - (cash_mn or 0.0)
    roic_f = round(((ebit_mn * 0.71) / invested_cap) * 100.0, 2) if ebit_mn is not None and invested_cap > 0 else None

    sec_low = (sector or "").lower()
    is_financial_sector = any(
        k in sec_low for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment")
    )
    dcf_val = (
        None
        if is_financial_sector
        else compute_dcf_intrinsic_value(
            free_cashflow_mn=fcf_mn,
            shares_mn=shares_mn,
            total_debt_mn=tot_debt_mn or 0.0,
            cash_mn=cash_mn or 0.0,
            growth_rate_pct=12.0,
            risk_free_rate_pct=risk_free_rate_pct,
            erp_pct=5.5,
        )
    )
    prev_nm_pct = (
        round((prev_ni_mn / prev_rev_mn) * 100.0, 2)
        if prev_ni_mn is not None and prev_rev_mn and prev_rev_mn > 0
        else None
    )
    f_score, _ = compute_piotroski_f_score(
        net_income=ni_mn,
        prev_net_income=prev_ni_mn,
        operating_cashflow=ocf_mn,
        total_assets=tot_assets_mn,
        prev_total_assets=prev_assets_mn,
        long_term_debt=lt_debt_mn,
        prev_long_term_debt=prev_lt_debt_mn,
        current_assets=cur_assets_mn,
        prev_current_assets=prev_cur_assets_mn,
        current_liabilities=cur_liab_mn,
        prev_current_liabilities=prev_cur_liab_mn,
        net_margin_pct=nm_pct,
        prev_net_margin_pct=prev_nm_pct,
        shares=shares_mn,
        prev_shares=shares_mn,
        revenue=rev_mn,
        prev_revenue=prev_rev_mn,
    )
    z_score, _ = compute_altman_z_score(
        sector=sector,
        current_assets_mn=cur_assets_mn,
        current_liabilities_mn=cur_liab_mn,
        retained_earnings_mn=retained_mn,
        ebit_mn=ebit_mn,
        market_cap_mn=eff_mcap,
        total_liabilities_mn=tot_liab_mn,
        revenue_mn=rev_mn,
        total_assets_mn=tot_assets_mn,
    )
    dupont = compute_dupont_roe(
        net_income_mn=ni_mn,
        revenue_mn=rev_mn,
        total_assets_mn=tot_assets_mn,
        shareholders_equity_mn=eq_mn,
    )

    return {
        "source": "PUCARS PDF + PSX DPS",
        "fiscal_period": period_label,
        "unit_scale": "PKR_MN",
        "revenue_mn": rev_mn,
        "gross_profit_mn": gp_mn,
        "operating_income_mn": ebit_mn,
        "net_income_mn": ni_mn,
        "total_assets_mn": tot_assets_mn,
        "current_assets_mn": cur_assets_mn,
        "total_liabilities_mn": tot_liab_mn,
        "current_liabilities_mn": cur_liab_mn,
        "shareholders_equity_mn": eq_mn,
        "total_debt_mn": tot_debt_mn,
        "cash_and_equivalents_mn": cash_mn,
        "operating_cashflow_mn": ocf_mn,
        "capex_mn": capex_mn,
        "free_cashflow_mn": fcf_mn,
        "shares_outstanding_mn": shares_mn,
        "bvps": bvps,
        "gross_margin_pct": gm_pct,
        "operating_margin_pct": om_pct,
        "net_margin_pct": nm_pct,
        "roe_pct": roe_f,
        "roa_pct": roa_f,
        "roic_pct": roic_f,
        "current_ratio": cur_ratio,
        "debt_to_equity": d_to_e,
        "debt_to_assets_pct": d_to_a,
        "interest_coverage": int_cov,
        "payout_ratio_pct": None,
        "fcf_yield_pct": fcf_yield,
        "dcf_intrinsic_value": dcf_val,
        "piotroski_f_score": f_score,
        "altman_z_score": z_score,
        "dupont_breakdown": dupont,
        "analyst_target_price": None,
        "analyst_recommendation": None,
        "extra_metrics": {
            "scale_detected": bs_scale_tag if bs_text else fallback_scale[1],
            "statement_scales": {"bs": bs_scale_tag, "pl": pl_scale_tag, "cf": cf_scale_tag},
        },
    }


def _extract_pdf_pages_sync(pdf_bytes: bytes, max_pages: int = 40) -> list[str]:
    pages_txt: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:max_pages]:
            txt = page.extract_text() or ""
            # Augment sparse text with extracted table rows when PDF uses bordered grids
            if len(txt.strip()) < 250:
                try:
                    tbls = page.extract_tables() or []
                    extra_lines: list[str] = []
                    for tbl in tbls:
                        for row in tbl:
                            cells = [str(c).strip() for c in row if c and str(c).strip()]
                            if cells:
                                extra_lines.append("  ".join(cells))
                    if extra_lines:
                        txt = (txt + "\n" + "\n".join(extra_lines)).strip()
                except Exception:
                    pass
            pages_txt.append(txt)
    return pages_txt


def fetch_psx_yfinance_ka_fallback(
    symbol: str,
    *,
    sector: str = "",
    last_price: float | None = None,
    default_shares_mn: float | None = None,
    market_cap_mn: float | None = None,
    risk_free_rate_pct: float = 12.0,
) -> dict[str, Any] | None:
    """
    Secondary Balance Sheet & Cash Flow fallback via Yahoo Finance `{SYM}.KA` when a PSX
    company's official PUCARS PDF is image-scanned (no text layer) or omits Balance Sheet / Cash Flow.
    Always explicitly source-tagged so users can distinguish PUCARS vs Yahoo Finance (.KA).
    """
    import yfinance as yf

    ka_sym = f"{symbol.strip().upper()}.KA"
    try:
        tk = yf.Ticker(ka_sym)
        bs = getattr(tk, "balance_sheet", None)
        cf = getattr(tk, "cashflow", None)
        inc = getattr(tk, "income_stmt", None)
        info: dict[str, Any] = {}
        try:
            raw_info = tk.info
            if isinstance(raw_info, dict):
                info = raw_info
        except Exception:
            pass
    except Exception:
        return None

    def _pick(series: Any, *labels: str) -> float | None:
        if series is None:
            return None
        for lbl in labels:
            if lbl in series.index:
                v = series.get(lbl)
                if v is not None:
                    try:
                        fv = float(v)
                        if fv == fv:  # not NaN
                            return fv
                    except (TypeError, ValueError):
                        pass
        return None

    def _to_mn(val: float | None) -> float | None:
        return round(val / 1_000_000.0, 2) if val is not None else None

    bs_cols = sorted(list(bs.columns), reverse=True) if bs is not None and not bs.empty else []
    cf_cols = sorted(list(cf.columns), reverse=True) if cf is not None and not cf.empty else []
    inc_cols = sorted(list(inc.columns), reverse=True) if inc is not None and not inc.empty else []
    if not bs_cols and not cf_cols and not inc_cols:
        return None

    bs0 = bs[bs_cols[0]] if bs_cols else None
    bs1 = bs[bs_cols[1]] if len(bs_cols) > 1 else None
    cf0 = cf[cf_cols[0]] if cf_cols else None
    inc0 = inc[inc_cols[0]] if inc_cols else None
    inc1 = inc[inc_cols[1]] if len(inc_cols) > 1 else None

    period_label = f"FY{bs_cols[0].year}" if bs_cols and hasattr(bs_cols[0], "year") else "TTM (.KA)"

    tot_assets_mn = _to_mn(_pick(bs0, "Total Assets"))
    prev_assets_mn = _to_mn(_pick(bs1, "Total Assets"))
    cur_assets_mn = _to_mn(_pick(bs0, "Current Assets"))
    prev_cur_assets_mn = _to_mn(_pick(bs1, "Current Assets"))
    tot_liab_mn = _to_mn(_pick(bs0, "Total Liabilities Net Minority Interest", "Total Liabilities"))
    cur_liab_mn = _to_mn(_pick(bs0, "Current Liabilities"))
    prev_cur_liab_mn = _to_mn(_pick(bs1, "Current Liabilities"))
    eq_mn = _to_mn(_pick(bs0, "Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"))
    retained_mn = _to_mn(_pick(bs0, "Retained Earnings"))
    cash_mn = _to_mn(_pick(bs0, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    tot_debt_mn = _to_mn(_pick(bs0, "Total Debt", "Long Term Debt And Capital Lease Obligation"))
    lt_debt_mn = _to_mn(_pick(bs0, "Long Term Debt"))
    prev_lt_debt_mn = _to_mn(_pick(bs1, "Long Term Debt"))

    rev_mn = _to_mn(_pick(inc0, "Total Revenue", "Operating Revenue"))
    prev_rev_mn = _to_mn(_pick(inc1, "Total Revenue", "Operating Revenue"))
    gp_mn = _to_mn(_pick(inc0, "Gross Profit"))
    ebit_mn = _to_mn(_pick(inc0, "Operating Income", "EBIT", "Pretax Income"))
    int_exp_mn = _to_mn(_pick(inc0, "Interest Expense", "Interest Expense Non Operating"))
    ni_mn = _to_mn(_pick(inc0, "Net Income", "Net Income Common Stockholders"))
    prev_ni_mn = _to_mn(_pick(inc1, "Net Income", "Net Income Common Stockholders"))

    ocf_mn = _to_mn(_pick(cf0, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    capex_raw = _to_mn(_pick(cf0, "Capital Expenditure", "Purchase Of PPE"))
    capex_mn = abs(capex_raw) if capex_raw is not None else None
    fcf_mn = _to_mn(_pick(cf0, "Free Cash Flow"))
    if fcf_mn is None and ocf_mn is not None:
        fcf_mn = round(ocf_mn - (capex_mn or 0.0), 2)

    shares_raw = _pick(bs0, "Ordinary Shares Number", "Share Issued") or info.get("sharesOutstanding")
    shares_mn = default_shares_mn or (_to_mn(float(shares_raw)) if shares_raw else None)
    if tot_assets_mn is None and eq_mn is None and ocf_mn is None and rev_mn is None:
        return None

    bvps = round(eq_mn / shares_mn, 2) if eq_mn is not None and shares_mn and shares_mn > 0 else None
    gm_pct = round((gp_mn / rev_mn) * 100.0, 2) if gp_mn is not None and rev_mn and rev_mn > 0 else None
    om_pct = round((ebit_mn / rev_mn) * 100.0, 2) if ebit_mn is not None and rev_mn and rev_mn > 0 else None
    nm_pct = round((ni_mn / rev_mn) * 100.0, 2) if ni_mn is not None and rev_mn and rev_mn > 0 else None
    prev_nm_pct = round((prev_ni_mn / prev_rev_mn) * 100.0, 2) if prev_ni_mn is not None and prev_rev_mn and prev_rev_mn > 0 else None
    roe_f = round((ni_mn / eq_mn) * 100.0, 2) if ni_mn is not None and eq_mn and eq_mn > 0 else None
    roa_f = round((ni_mn / tot_assets_mn) * 100.0, 2) if ni_mn is not None and tot_assets_mn and tot_assets_mn > 0 else None
    cur_ratio = round(cur_assets_mn / cur_liab_mn, 2) if cur_assets_mn is not None and cur_liab_mn and cur_liab_mn > 0 else None
    d_to_e = round(tot_debt_mn / eq_mn, 2) if tot_debt_mn is not None and eq_mn and eq_mn > 0 else None
    d_to_a = round((tot_debt_mn / tot_assets_mn) * 100.0, 2) if tot_debt_mn is not None and tot_assets_mn and tot_assets_mn > 0 else None
    int_cov = round(ebit_mn / abs(int_exp_mn), 2) if ebit_mn is not None and int_exp_mn and abs(int_exp_mn) > 0 else None
    eff_mcap = market_cap_mn or (round(last_price * shares_mn, 2) if last_price and shares_mn else None)
    fcf_yield = round((fcf_mn / eff_mcap) * 100.0, 2) if fcf_mn is not None and eff_mcap and eff_mcap > 0 else None
    invested_cap = (eq_mn or 0.0) + (tot_debt_mn or 0.0) - (cash_mn or 0.0)
    roic_f = round(((ebit_mn * 0.71) / invested_cap) * 100.0, 2) if ebit_mn is not None and invested_cap > 0 else None

    sec_low = (sector or "").lower()
    is_financial_sector = any(k in sec_low for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment"))
    dcf_val = (
        None
        if is_financial_sector
        else compute_dcf_intrinsic_value(
            free_cashflow_mn=fcf_mn,
            shares_mn=shares_mn,
            total_debt_mn=tot_debt_mn or 0.0,
            cash_mn=cash_mn or 0.0,
            growth_rate_pct=12.0,
            risk_free_rate_pct=risk_free_rate_pct,
            erp_pct=5.5,
        )
    )
    f_score, _ = compute_piotroski_f_score(
        net_income=ni_mn,
        prev_net_income=prev_ni_mn,
        operating_cashflow=ocf_mn,
        total_assets=tot_assets_mn,
        prev_total_assets=prev_assets_mn,
        long_term_debt=lt_debt_mn,
        prev_long_term_debt=prev_lt_debt_mn,
        current_assets=cur_assets_mn,
        prev_current_assets=prev_cur_assets_mn,
        current_liabilities=cur_liab_mn,
        prev_current_liabilities=prev_cur_liab_mn,
        net_margin_pct=nm_pct,
        prev_net_margin_pct=prev_nm_pct,
        shares=shares_mn,
        prev_shares=shares_mn,
        revenue=rev_mn,
        prev_revenue=prev_rev_mn,
    )
    z_score, _ = compute_altman_z_score(
        sector=sector,
        current_assets_mn=cur_assets_mn,
        current_liabilities_mn=cur_liab_mn,
        retained_earnings_mn=retained_mn,
        ebit_mn=ebit_mn,
        market_cap_mn=eff_mcap,
        total_liabilities_mn=tot_liab_mn,
        revenue_mn=rev_mn,
        total_assets_mn=tot_assets_mn,
    )
    dupont = compute_dupont_roe(
        net_income_mn=ni_mn,
        revenue_mn=rev_mn,
        total_assets_mn=tot_assets_mn,
        shareholders_equity_mn=eq_mn,
    )
    return {
        "source": "PSX DPS + Yahoo Finance (.KA)",
        "fiscal_period": period_label,
        "unit_scale": "PKR_MN",
        "revenue_mn": rev_mn,
        "gross_profit_mn": gp_mn,
        "operating_income_mn": ebit_mn,
        "net_income_mn": ni_mn,
        "total_assets_mn": tot_assets_mn,
        "current_assets_mn": cur_assets_mn,
        "total_liabilities_mn": tot_liab_mn,
        "current_liabilities_mn": cur_liab_mn,
        "shareholders_equity_mn": eq_mn,
        "total_debt_mn": tot_debt_mn,
        "cash_and_equivalents_mn": cash_mn,
        "operating_cashflow_mn": ocf_mn,
        "capex_mn": capex_mn,
        "free_cashflow_mn": fcf_mn,
        "shares_outstanding_mn": shares_mn,
        "bvps": bvps,
        "gross_margin_pct": gm_pct,
        "operating_margin_pct": om_pct,
        "net_margin_pct": nm_pct,
        "roe_pct": roe_f,
        "roa_pct": roa_f,
        "roic_pct": roic_f,
        "current_ratio": cur_ratio,
        "debt_to_equity": d_to_e,
        "debt_to_assets_pct": d_to_a,
        "interest_coverage": int_cov,
        "payout_ratio_pct": None,
        "fcf_yield_pct": fcf_yield,
        "dcf_intrinsic_value": dcf_val,
        "piotroski_f_score": f_score,
        "altman_z_score": z_score,
        "dupont_breakdown": dupont,
        "analyst_target_price": float(info["targetMeanPrice"]) if info.get("targetMeanPrice") else None,
        "analyst_recommendation": str(info.get("recommendationKey") or "").upper() or None,
        "extra_metrics": {"ka_ticker": ka_sym, "fallback_used": True},
    }


def _merge_pucars_with_ka_fallback(
    pucars: dict[str, Any] | None,
    ka: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep official PUCARS PDF values primary, filling missing BS/CF/Valuation fields from `.KA`."""
    if not pucars and not ka:
        return None
    if pucars and not ka:
        return pucars
    if ka and not pucars:
        return ka
    assert pucars is not None and ka is not None
    merged = dict(pucars)
    supplemented = False
    for key in (
        "total_assets_mn",
        "current_assets_mn",
        "total_liabilities_mn",
        "current_liabilities_mn",
        "shareholders_equity_mn",
        "total_debt_mn",
        "cash_and_equivalents_mn",
        "operating_cashflow_mn",
        "capex_mn",
        "free_cashflow_mn",
        "bvps",
        "roe_pct",
        "roa_pct",
        "roic_pct",
        "current_ratio",
        "debt_to_equity",
        "debt_to_assets_pct",
        "interest_coverage",
        "fcf_yield_pct",
        "dcf_intrinsic_value",
        "piotroski_f_score",
        "altman_z_score",
        "analyst_target_price",
        "analyst_recommendation",
    ):
        if merged.get(key) is None and ka.get(key) is not None:
            merged[key] = ka[key]
            supplemented = True
    if not merged.get("dupont_breakdown") and ka.get("dupont_breakdown"):
        merged["dupont_breakdown"] = ka["dupont_breakdown"]
        supplemented = True
    if supplemented:
        merged["source"] = "PUCARS PDF + Yahoo Finance (.KA)"
        extra = dict(merged.get("extra_metrics") or {})
        extra["ka_supplemented"] = True
        merged["extra_metrics"] = extra
    return merged


class PsxDeepFundamentalsScraper(BaseScraper):
    """
    Fetches DPS company ratios/shares, parses PUCARS financial PDFs via `pdfplumber`, and
    uses clearly source-tagged Yahoo Finance `.KA` BS/CF fallback when PUCARS PDFs are
    image-scanned or omit Balance Sheet / Cash Flow tables.
    Prioritizes held/watched stocks, then KSE-100 & KMI-30 blue chips by market cap.
    """

    name = "psx_deep_fundamentals"

    async def run(self, db: AsyncSession, batch_size: int = 24) -> int:
        rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
        held_ids = set((await db.execute(select(Transaction.asset_id).distinct())).scalars())
        watched_ids = set((await db.execute(select(WatchlistItem.asset_id).distinct())).scalars())
        priority_ids = held_ids | watched_ids

        all_psx = list(
            (
                await db.execute(
                    select(Asset)
                    .where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class == "PSX_EQUITY",
                    )
                )
            ).scalars()
        )
        if not all_psx:
            return 0

        existing_snaps = {
            s.asset_id: s
            for s in (await db.execute(select(EquityFinancialSnapshot))).scalars()
        }
        existing_profiles = {
            p.asset_id: p
            for p in (await db.execute(select(PsxProfile))).scalars()
        }
        existing_shariah = {
            s.asset_id: s
            for s in (await db.execute(select(ShariahMetric))).scalars()
        }

        def _is_complete(snap: EquityFinancialSnapshot | None) -> bool:
            return bool(
                snap is not None
                and snap.total_assets_mn is not None
                and snap.total_assets_mn > 0
                and snap.shareholders_equity_mn is not None
                and snap.shareholders_equity_mn > 0
            )

        def _rank_key(a: Asset) -> tuple[int, int, float, float]:
            p = existing_profiles.get(a.id)
            is_bluechip = 1 if (p and (p.is_kmi30 or p.is_kse100)) else 0
            mcap = float(p.market_cap_mn) if (p and p.market_cap_mn) else 0.0
            vol = float(a.volume or 0)
            return (1 if a.id in priority_ids else 0, is_bluechip, mcap, vol)

        ranked_psx = sorted(all_psx, key=_rank_key, reverse=True)
        missing_prio = [a for a in ranked_psx if a.id in priority_ids and not _is_complete(existing_snaps.get(a.id))]
        missing_top = [a for a in ranked_psx if a.id not in priority_ids and not _is_complete(existing_snaps.get(a.id))]
        refresh_prio = [a for a in ranked_psx if a.id in priority_ids and _is_complete(existing_snaps.get(a.id))]
        batch: list[Asset] = []
        seen_ids: set[str] = set()
        for a in missing_prio + missing_top + refresh_prio:
            if len(batch) >= batch_size:
                break
            if a.id not in seen_ids:
                batch.append(a)
                seen_ids.add(a.id)

        if not batch:
            return 0

        sem = asyncio.Semaphore(4)
        now = datetime.now(timezone.utc)
        upserted = 0

        async with CurlAsyncSession(impersonate="chrome", timeout=30) as session:
            async def _process(asset: Asset) -> tuple[Asset, dict[str, Any] | None]:
                async with sem:
                    try:
                        r_comp = await session.get(f"{PSX_BASE_URL}/company/{asset.symbol}", headers=HEADERS, timeout=15)
                        dps_meta = parse_dps_company_deep_meta(r_comp.text) if r_comp.status_code == 200 else {}
                        pages_text: list[str] = []
                        chosen_pdf_url: str | None = None

                        for _, pdf_url in dps_meta.get("pdf_urls", []):
                            try:
                                r_head = await session.head(pdf_url, headers=HEADERS, timeout=10)
                                cl = int(r_head.headers.get("content-length") or 0)
                                if cl > MAX_PDF_BYTES:
                                    continue
                                r_pdf = await session.get(pdf_url, headers=HEADERS, timeout=30)
                                if r_pdf.status_code == 200 and len(r_pdf.content) <= MAX_PDF_BYTES:
                                    cand_pages = await asyncio.to_thread(_extract_pdf_pages_sync, r_pdf.content, 40)
                                    joined_low = " ".join(cand_pages).lower()
                                    has_bs = (
                                        "total assets" in joined_low
                                        and ("statement of financial position" in joined_low or "balance sheet" in joined_low)
                                    )
                                    if has_bs:
                                        pages_text = cand_pages
                                        chosen_pdf_url = pdf_url
                                        break
                                    elif not pages_text and sum(len(p.strip()) for p in cand_pages) > 800:
                                        pages_text = cand_pages
                                        chosen_pdf_url = pdf_url
                            except Exception:
                                continue

                        prof = existing_profiles.get(asset.id)
                        mcap = (
                            float(prof.market_cap_mn)
                            if prof and prof.market_cap_mn and prof.market_cap_mn > 0
                            else dps_meta.get("market_cap_mn")
                        )
                        last_px = float(asset.last_price) if asset.last_price else None
                        parsed = parse_pucars_financial_pdf_text(
                            pages_text,
                            asset.symbol,
                            sector=asset.sector_or_category or "",
                            last_price=last_px,
                            default_shares_mn=dps_meta.get("shares_mn"),
                            market_cap_mn=mcap,
                            dps_meta=dps_meta,
                            risk_free_rate_pct=rf_pkr,
                        )
                        if parsed and chosen_pdf_url:
                            parsed["document_url"] = chosen_pdf_url

                        # If PUCARS PDF was image-scanned or missing Balance Sheet/Equity/Cash Flow, supplement via `.KA`
                        needs_ka = (
                            parsed is None
                            or parsed.get("total_assets_mn") is None
                            or parsed.get("shareholders_equity_mn") is None
                            or parsed.get("operating_cashflow_mn") is None
                        )
                        if needs_ka:
                            ka_data = await asyncio.to_thread(
                                fetch_psx_yfinance_ka_fallback,
                                asset.symbol,
                                sector=asset.sector_or_category or "",
                                last_price=last_px,
                                default_shares_mn=dps_meta.get("shares_mn"),
                                market_cap_mn=mcap,
                                risk_free_rate_pct=rf_pkr,
                            )
                            parsed = _merge_pucars_with_ka_fallback(parsed, ka_data)
                            if parsed and chosen_pdf_url and not parsed.get("document_url"):
                                parsed["document_url"] = chosen_pdf_url

                        return asset, parsed
                    except Exception as exc:
                        logger.debug("Deep PSX scrape failed for %s: %s", asset.symbol, exc)
                        return asset, None

            results = await asyncio.gather(*[_process(a) for a in batch])

        def _dec_opt(v: Any) -> Decimal | None:
            return D(str(v)) if v is not None else None

        for asset, data in results:
            if not data:
                continue
            snap = existing_snaps.get(asset.id)
            if snap is None:
                snap = EquityFinancialSnapshot(asset_id=asset.id)
                db.add(snap)
            snap.source = str(data.get("source") or "PUCARS PDF + PSX DPS")
            snap.fiscal_period = str(data.get("fiscal_period") or "PUCARS-Latest")
            snap.bvps = _dec_opt(data.get("bvps"))
            snap.total_assets_mn = _dec_opt(data.get("total_assets_mn"))
            snap.total_debt_mn = _dec_opt(data.get("total_debt_mn"))
            snap.cash_equivalents_mn = _dec_opt(data.get("cash_and_equivalents_mn"))
            snap.shareholders_equity_mn = _dec_opt(data.get("shareholders_equity_mn"))
            snap.operating_cashflow_mn = _dec_opt(data.get("operating_cashflow_mn"))
            snap.capex_mn = _dec_opt(data.get("capex_mn"))
            snap.free_cashflow_mn = _dec_opt(data.get("free_cashflow_mn"))
            snap.current_ratio = _dec_opt(data.get("current_ratio"))
            snap.debt_to_equity = _dec_opt(data.get("debt_to_equity"))
            snap.roe_pct = _dec_opt(data.get("roe_pct"))
            snap.roa_pct = _dec_opt(data.get("roa_pct"))
            snap.roic_pct = _dec_opt(data.get("roic_pct"))
            snap.operating_margin_pct = _dec_opt(data.get("operating_margin_pct"))
            snap.payout_ratio_pct = _dec_opt(data.get("payout_ratio_pct"))
            snap.analyst_target_price = _dec_opt(data.get("analyst_target_price"))
            snap.analyst_rating = data.get("analyst_recommendation")
            snap.piotroski_f_score = data.get("piotroski_f_score")
            snap.altman_z_score = _dec_opt(data.get("altman_z_score"))
            snap.dcf_intrinsic_value = _dec_opt(data.get("dcf_intrinsic_value"))
            snap.metrics_json = {
                "unit_scale": data.get("unit_scale", "PKR_MN"),
                "document_url": data.get("document_url"),
                "revenue_mn": data.get("revenue_mn"),
                "gross_profit_mn": data.get("gross_profit_mn"),
                "operating_income_mn": data.get("operating_income_mn"),
                "net_income_mn": data.get("net_income_mn"),
                "current_assets_mn": data.get("current_assets_mn"),
                "total_liabilities_mn": data.get("total_liabilities_mn"),
                "current_liabilities_mn": data.get("current_liabilities_mn"),
                "shares_outstanding_mn": data.get("shares_outstanding_mn"),
                "gross_margin_pct": data.get("gross_margin_pct"),
                "net_margin_pct": data.get("net_margin_pct"),
                "debt_to_assets_pct": data.get("debt_to_assets_pct"),
                "interest_coverage": data.get("interest_coverage"),
                "fcf_yield_pct": data.get("fcf_yield_pct"),
                "dupont_breakdown": data.get("dupont_breakdown"),
                **(data.get("extra_metrics") or {}),
            }
            snap.updated_at = now

            # Enrich PsxProfile if PSX DPS omitted or zeroed P/B or ROE%
            prof = existing_profiles.get(asset.id)
            if prof is not None:
                if (prof.pb_ratio is None or prof.pb_ratio <= 0) and data.get("bvps") and data["bvps"] > 0 and asset.last_price and asset.last_price > 0:
                    prof.pb_ratio = (asset.last_price / D(str(data["bvps"]))).quantize(D("0.01"))
                if (prof.roe_pct is None or prof.roe_pct == 0) and data.get("roe_pct") is not None:
                    prof.roe_pct = D(str(data["roe_pct"])).quantize(D("0.01"))

            # Update non-bank KMI Shariah Debt-to-Assets ratio if real PDF data was extracted
            sh = existing_shariah.get(asset.id)
            if sh is not None and data.get("debt_to_assets_pct") is not None:
                is_bank = "bank" in (asset.sector_or_category or "").lower()
                if not is_bank:
                    sh.debt_to_assets_pct = D(str(data["debt_to_assets_pct"])).quantize(D("0.01"))

            upserted += 1

        await db.commit()
        return upserted
