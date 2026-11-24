"""
Multi-Period Annual & Quarterly Company Financial Report PDF Parser & Auto-Scraper (`pdfplumber`).

Extracts full 3-Statement financial data (Income Statement, Balance Sheet, Cash Flow) across
BOTH current and comparative prior periods from Pakistani IAS/IFRS PUCARS Annual & Quarterly PDFs
(both non-financial corporates and commercial/Islamic banks), PLUS key qualitative disclosures:
  - Independent Auditor's Opinion & Audit Firm
  - Directors' Report / Chairman's Review quantitative highlights
  - Cash Dividend & Bonus Share declarations
  - Operating Segment breakdown (when disclosed)

Supports:
  1. Automated multi-period ingestion from PSX DPS financial tables + PUCARS Annual & Quarterly PDFs.
  2. Manual drag-and-drop upload of ANY company's Annual or Quarterly PDF report on `/stocks/[symbol]`.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pdfplumber
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from pdfplumber.utils import extract_text as pdf_extract_text_from_chars
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.market import Asset, EquityFinancialPeriod
from app.scrapers.base import BaseScraper
from app.scrapers.psx_deep import (
    HEADERS,
    PSX_BASE_URL,
    _detect_block_scale_div,
    _extract_last_two_numbers,
    _num,
    parse_dps_company_deep_meta,
)

logger = logging.getLogger("openkhazana.scrapers.financial_reports")
D = Decimal
MAX_REPORT_PDF_BYTES = 22_000_000

MONTH_TO_NUM = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

PK_AUDIT_FIRMS = [
    ("A.F. Ferguson & Co. (PwC)", r"a\.?\s*f\.?\s*ferguson"),
    ("KPMG Taseer Hadi & Co.", r"kpmg\s+taseer\s+hadi|kpmg"),
    ("EY Ford Rhodes", r"ey\s+ford\s+rhodes|ernst\s*&\s*young"),
    ("Yousuf Adil (Deloitte)", r"yousuf\s+adil"),
    ("BDO Ebrahim & Co.", r"bdo\s+ebrahim"),
    ("Grant Thornton Anjum Rahman", r"grant\s+thornton"),
    ("Riaz Ahmad & Co.", r"riaz\s+ahmad"),
    ("ShineWing Hameed Chaudhri", r"hameed\s+chaudhri"),
    ("Crowe Hussain Chaudhury", r"crowe\s+hussain"),
]


def _dec(val: float | int | None, places: str = "0.01") -> Decimal | None:
    if val is None:
        return None
    try:
        return D(str(val)).quantize(D(places))
    except Exception:
        return None


def _fast_page_text(page: Any) -> str:
    """Fast O(N) text extraction via page.chars avoiding slow vector-object scans on graphic-heavy PDFs."""
    try:
        chars = page.chars or []
        if not chars:
            return page.extract_text() or ""
        seen: set[tuple[str, int, int]] = set()
        deduped: list[dict[str, Any]] = []
        for c in chars:
            ch = c.get("text", "")
            if not ch:
                continue
            key = (ch, int(round(float(c.get("x0", 0.0)) / 1.4)), int(round(float(c.get("top", 0.0)) / 1.4)))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        return pdf_extract_text_from_chars(deduped) or ""
    except Exception:
        try:
            return page.extract_text() or ""
        except Exception:
            return ""


def _parse_period_date(raw_text: str) -> date | None:
    """Parse dates like 'June 30, 2025', '31 December 2025', '30 Sep 2026', '2025-06-30'."""
    if not raw_text:
        return None
    m_iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", raw_text)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass
    m1 = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(\d{1,2}),?\s+(20\d{2})\b",
        raw_text,
        re.I,
    )
    if m1:
        mo = MONTH_TO_NUM.get(m1.group(1).lower())
        if mo:
            try:
                return date(int(m1.group(3)), mo, int(m1.group(2)))
            except ValueError:
                pass
    m2 = re.search(
        r"\b(\d{1,2})[\s\-]+(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[\s,\-]+(20\d{2})\b",
        raw_text,
        re.I,
    )
    if m2:
        mo = MONTH_TO_NUM.get(m2.group(2).lower())
        if mo:
            try:
                return date(int(m2.group(3)), mo, int(m2.group(1)))
            except ValueError:
                pass
    return None


def _classify_period(
    stmt_header: str,
    filename_hint: str = "",
    period_type_hint: str | None = None,
) -> tuple[str, str, date, int, str, date, int]:
    """
    Determine `(period_type, cur_label, cur_date, cur_fy, prev_label, prev_date, prev_fy)`
    from financial statement headings & filename hints.
    """
    combined = f"{stmt_header[:1200]} {filename_hint}"
    low = combined.lower()
    detected_dt = _parse_period_date(stmt_header) or _parse_period_date(filename_hint)
    if detected_dt is None:
        m_yr = re.search(r"\b(202[0-9]|201[89])\b", combined)
        yr = int(m_yr.group(1)) if m_yr else date.today().year
        detected_dt = date(yr, 6, 30)

    is_quarterly = False
    q_prefix = "Q"
    if period_type_hint and period_type_hint.upper() in {"ANNUAL", "QUARTERLY"}:
        is_quarterly = period_type_hint.upper() == "QUARTERLY"
    else:
        if any(
            k in low
            for k in (
                "nine months ended",
                "9 months",
                "third quarter",
                "six months ended",
                "half year",
                "half-year",
                "three months ended",
                "first quarter",
                "quarterly report",
                "condensed interim",
                "1q",
                "2q",
                "3q",
                "9m",
                "q1",
                "q2",
                "q3",
            )
        ):
            is_quarterly = True
        if "year ended" in low and "condensed interim" not in low and "quarterly" not in low:
            is_quarterly = False

    yr = detected_dt.year
    mo = detected_dt.month
    if is_quarterly:
        if "nine months" in low or "9m" in low or "third quarter" in low or "3q" in low or "q3" in low:
            q_prefix = "9M"
        elif "six months" in low or "half year" in low or "half-year" in low or "hy" in low or "2q" in low or "q2" in low:
            q_prefix = "HY"
        elif "three months" in low or "first quarter" in low or "1q" in low or "q1" in low:
            q_prefix = "Q1"
        else:
            q_prefix = f"Q{((mo - 1) // 3) + 1}"
        cur_label = f"{q_prefix}-{yr}"
        prev_yr = yr - 1
        prev_label = f"{q_prefix}-{prev_yr}"
        try:
            prev_dt = date(prev_yr, detected_dt.month, min(detected_dt.day, 28))
        except ValueError:
            prev_dt = date(prev_yr, 6, 30)
        return "QUARTERLY", cur_label, detected_dt, yr, prev_label, prev_dt, prev_yr

    cur_label = f"FY{yr}"
    prev_yr = yr - 1
    prev_label = f"FY{prev_yr}"
    try:
        prev_dt = date(prev_yr, detected_dt.month, min(detected_dt.day, 28))
    except ValueError:
        prev_dt = date(prev_yr, 6, 30)
    return "ANNUAL", cur_label, detected_dt, yr, prev_label, prev_dt, prev_yr


def _extract_auditor_opinion(pages_text: list[str]) -> str | None:
    for txt in pages_text:
        low = txt.lower()
        if "independent auditor" not in low and "auditors' report" not in low and "auditor's report" not in low:
            continue
        if "opinion" not in low:
            continue
        firm = None
        for firm_name, pat in PK_AUDIT_FIRMS:
            if re.search(pat, low):
                firm = firm_name
                break
        if "adverse opinion" in low:
            tone = "Adverse Opinion"
        elif "qualified opinion" in low or "except for the effects" in low:
            tone = "Qualified Opinion"
        elif "material uncertainty related to going concern" in low:
            tone = "Unmodified (Going Concern Note)"
        elif "emphasis of matter" in low:
            tone = "Unmodified (Emphasis of Matter)"
        elif "true and fair view" in low or "in our opinion" in low:
            tone = "Unmodified / Clean Opinion"
        else:
            tone = "Audited Financial Statements"
        return f"{tone} — {firm}" if firm else tone
    return None


def _extract_directors_highlights(pages_text: list[str]) -> tuple[list[str], str | None]:
    """Extract concise performance bullets & dividend/bonus notes from Directors' Report / Chairman's Review."""
    highlights: list[str] = []
    div_note: str | None = None
    keywords = (
        "revenue", "sales", "turnover", "profit after tax", "net profit", "earnings per share",
        "eps", "dividend", "bonus", "export", "capacity", "expansion", "margin", "deposits",
        "advances", "dispatches", "production", "market share", "outlook",
    )
    for txt in pages_text:
        head = txt[:600].lower()
        if not any(
            m in head
            for m in ("directors' review", "directors’ review", "directors' report", "directors’ report", "chairman's review", "chairman’s review", "financial highlights", "review of operations")
        ):
            continue
        cleaned = re.sub(r"\s+", " ", txt)
        sentences = re.split(r"(?<!\bRs\.)(?<!\bCo\.)(?<!\bNo\.)(?<!\bLtd\.)(?<!\bDr\.)(?<=[.!?])\s+", cleaned)
        for sent in sentences:
            s = sent.strip()
            if len(s) < 45 or len(s) > 280:
                continue
            s_low = s.lower()
            if div_note is None and any(k in s_low for k in ("cash dividend", "final dividend", "interim dividend", "bonus shares")):
                if re.search(r"(?:rs\.?|pkr)\s*\d|\d+\s*%", s_low):
                    div_note = s[:290]
            if len(highlights) < 6 and any(k in s_low for k in keywords) and re.search(r"\d", s):
                # Avoid raw table dump lines with lots of numbers
                if len(re.findall(r"\d+", s)) <= 7 and s not in highlights:
                    highlights.append(s)
        if len(highlights) >= 6 and div_note:
            break
    return highlights, div_note


def _extract_segment_notes(pages_text: list[str]) -> list[dict[str, Any]]:
    """Best-effort extraction of operating segment revenue/profit lines when disclosed."""
    segments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for txt in pages_text:
        low = txt.lower()
        if "segment information" not in low and "segment reporting" not in low and "segment results" not in low:
            continue
        div, _ = _detect_block_scale_div(txt, fallback=(1000.0, "PKR_000"))
        for ln in txt.splitlines():
            s = ln.strip()
            if len(s) < 12 or len(s) > 130:
                continue
            m = re.match(r"^([A-Za-z][A-Za-z &/\-]{3,42})\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)", s)
            if not m:
                continue
            seg_name = m.group(1).strip()
            seg_low = seg_name.lower()
            if any(bad in seg_low for bad in ("total", "note", "rupees", "revenue", "profit", "assets", "liabilities", "elimination", "unallocated")):
                continue
            v1 = _num(m.group(2))
            if v1 and v1 > 10 and seg_name not in seen:
                seen.add(seg_name)
                segments.append({"segment": seg_name, "value_mn": round(v1 / div, 2)})
            if len(segments) >= 6:
                break
        if segments:
            break
    return segments


def parse_company_report_pdf_text(
    pages_text: list[str],
    *,
    symbol: str,
    sector: str = "",
    filename_hint: str = "",
    period_type_hint: str | None = None,
    source_pdf_url: str | None = None,
    source_label: str = "PUCARS PDF",
) -> list[dict[str, Any]]:
    """
    Parse 1–2 structured `EquityFinancialPeriod` dicts (Current + Comparative Prior period)
    from extracted `pdfplumber` pages of a Pakistani Annual or Quarterly company report.
    """
    valid_pages = [t for t in pages_text if t and len(t.strip()) > 100]
    if not valid_pages:
        return []

    bs_text = ""
    pl_text = ""
    cf_text = ""

    for txt in valid_pages:
        low = txt.lower()
        head_low = txt[:750].lower()
        is_narrative = any(
            skip in head_low
            for skip in (
                "contents", "directors' review", "directors’ review", "directors' report",
                "directors’ report", "chairman's review", "chairman’s review",
                "notes to and forming part", "notes to the ", "independent auditor",
            )
        )
        if is_narrative:
            continue

        if ("statement of financial position" in head_low or "balance sheet" in head_low) and "total assets" in low:
            if not bs_text:
                bs_text = txt
        elif (
            "profit and loss" in head_low
            or "profit or loss" in head_low
            or "statement of comprehensive income" in head_low
        ) and any(k in low for k in ("profit after tax", "profit before tax", "taxation", "loss after tax")):
            if not pl_text:
                pl_text = txt
        elif ("statement of cash flows" in head_low or "cash flow statement" in head_low or "cash flows" in head_low) and (
            "operating activities" in low or "cash generated from" in low
        ):
            if not cf_text:
                cf_text = txt

    if not any((bs_text, pl_text, cf_text)):
        return []

    stmt_header = "\n".join(b[:700] for b in (pl_text, bs_text, cf_text) if b)
    p_type, cur_label, cur_dt, cur_fy, prev_label, prev_dt, prev_fy = _classify_period(
        stmt_header, filename_hint=filename_hint, period_type_hint=period_type_hint
    )

    stmt_sample = "\n".join(b for b in (pl_text, bs_text, cf_text) if b)
    fb_scale = _detect_block_scale_div(stmt_sample, fallback=(1000.0, "PKR_000"))
    pl_div, _ = _detect_block_scale_div(pl_text, fallback=fb_scale)
    bs_div, _ = _detect_block_scale_div(bs_text, fallback=fb_scale)
    cf_div, _ = _detect_block_scale_div(cf_text, fallback=fb_scale)

    def _scan(block: str, patterns: list[str], div: float | None = None) -> tuple[float | None, float | None]:
        if not block:
            return None, None
        for ln in block.splitlines():
            low_ln = ln.lower()
            for pat in patterns:
                if re.search(pat, low_ln):
                    c1, c2 = _extract_last_two_numbers(ln)
                    if c1 is not None:
                        if div:
                            return round(c1 / div, 2), (round(c2 / div, 2) if c2 is not None else None)
                        return round(c1, 4), (round(c2, 4) if c2 is not None else None)
        return None, None

    is_bank = any(k in sector.lower() for k in ("bank", "modaraba", "leasing", "investment"))

    # --- Income Statement ---
    rev_pats = (
        [r"mark[\-\s]?up\s*/\s*return\s*/\s*interest\s+earned", r"mark[\-\s]?up\s+earned", r"\btotal\s+income\b"]
        if is_bank
        else [r"\b(?:net\s+)?sales\b", r"\bturnover\b", r"\brevenue\s+from\s+contracts", r"\brevenue\b", r"\btotal\s+income\b"]
    )
    rev_c, rev_p = _scan(pl_text, rev_pats, pl_div)
    cos_c, cos_p = _scan(pl_text, [r"cost\s+of\s+(?:sales|goods\s+sold|revenue)", r"mark[\-\s]?up\s*/\s*return\s*/\s*interest\s+expensed"], pl_div)
    gp_c, gp_p = _scan(pl_text, [r"\bgross\s+profit\b", r"net\s+mark[\-\s]?up\s*/\s*interest\s+income", r"\btotal\s+income\b"], pl_div)
    op_c, op_p = _scan(pl_text, [r"\boperating\s+profit\b", r"profit\s+from\s+operations", r"profit\s+before\s+provisions"], pl_div)
    fin_c, fin_p = _scan(pl_text, [r"\bfinance\s+cost", r"\bfinancial\s+charges\b"], pl_div)
    pbt_c, pbt_p = _scan(pl_text, [r"profit\s+before\s+tax", r"profit\s+before\s+levies", r"loss\s+before\s+tax"], pl_div)
    tax_c, tax_p = _scan(pl_text, [r"\btaxation\b", r"\bincome\s+tax\b"], pl_div)
    pat_c, pat_p = _scan(pl_text, [r"profit\s+after\s+tax", r"profit\s+for\s+the\s+(?:year|period)", r"net\s+profit\b", r"loss\s+after\s+tax"], pl_div)
    eps_c, eps_p = _scan(pl_text, [r"earnings?\s+per\s+share", r"\bbasic\s+(?:and\s+diluted\s+)?earnings?\s+per\s+share"], div=None)

    # Sanity guard on EPS (ignore raw profit values if regex matched a wrong line)
    if eps_c is not None and abs(eps_c) > 2500:
        eps_c, eps_p = None, None

    # --- Balance Sheet ---
    ta_c, ta_p = _scan(bs_text, [r"\btotal\s+assets\b"], bs_div)
    ca_c, ca_p = _scan(bs_text, [r"\btotal\s+current\s+assets\b", r"^current\s+assets\b"], bs_div)
    cash_c, cash_p = _scan(bs_text, [r"cash\s+and\s+bank\s+balances", r"cash\s+and\s+balances\s+with\s+treasury", r"cash\s+and\s+cash\s+equivalents"], bs_div)
    inv_c, inv_p = _scan(bs_text, [r"stock[\-\s]in[\-\s]trade", r"\binventories\b"], bs_div)
    rec_c, rec_p = _scan(bs_text, [r"trade\s+debts", r"trade\s+receivables"], bs_div)
    tl_c, tl_p = _scan(bs_text, [r"\btotal\s+liabilities\b"], bs_div)
    cl_c, cl_p = _scan(bs_text, [r"\btotal\s+current\s+liabilities\b", r"^current\s+liabilities\b"], bs_div)
    eq_c, eq_p = _scan(
        bs_text,
        [r"\btotal\s+shareholders['’]?\s*equity\b", r"\btotal\s+equity\b", r"equity\s+attributable\s+to\s+owners", r"\bnet\s+assets\b"],
        bs_div,
    )
    lt_c, lt_p = _scan(bs_text, [r"long[\-\s]term\s+(?:financing|borrowings|loans|debt)", r"\bborrowings\b"], bs_div)
    st_c, st_p = _scan(bs_text, [r"short[\-\s]term\s+(?:borrowings|financing|running\s+finance)"], bs_div)
    debt_c = round((abs(lt_c or 0.0) + abs(st_c or 0.0)), 2) if (lt_c is not None or st_c is not None) else None
    debt_p = round((abs(lt_p or 0.0) + abs(st_p or 0.0)), 2) if (lt_p is not None or st_p is not None) else None

    if tl_c is None and ta_c is not None and eq_c is not None and ta_c > eq_c:
        tl_c = round(ta_c - eq_c, 2)
    if tl_p is None and ta_p is not None and eq_p is not None and ta_p > eq_p:
        tl_p = round(ta_p - eq_p, 2)

    # --- Cash Flow Statement ---
    ocf_c, ocf_p = _scan(cf_text, [r"net\s+cash\s+(?:generated\s+from|used\s+in|from)\s+operating\s+activities", r"cash\s+flows?\s+from\s+operating\s+activities"], cf_div)
    capex_c, capex_p = _scan(cf_text, [r"(?:fixed\s+)?capital\s+expenditure", r"additions?\s+to\s+property,\s*plant", r"purchase\s+of\s+property,\s*plant"], cf_div)
    divp_c, divp_p = _scan(cf_text, [r"dividends?\s+paid"], cf_div)

    capex_abs_c = abs(capex_c) if capex_c is not None else None
    capex_abs_p = abs(capex_p) if capex_p is not None else None
    fcf_c = round(ocf_c - (capex_abs_c or 0.0), 2) if ocf_c is not None else None
    fcf_p = round(ocf_p - (capex_abs_p or 0.0), 2) if ocf_p is not None else None

    auditor_opinion = _extract_auditor_opinion(valid_pages)
    directors_hl, div_note = _extract_directors_highlights(valid_pages)
    segments = _extract_segment_notes(valid_pages)

    cur_row = {
        "period_type": p_type,
        "period_label": cur_label,
        "period_end_date": cur_dt,
        "fiscal_year": cur_fy,
        "revenue_mn": abs(rev_c) if rev_c is not None else None,
        "cost_of_sales_mn": abs(cos_c) if cos_c is not None else None,
        "gross_profit_mn": gp_c,
        "operating_profit_mn": op_c,
        "finance_cost_mn": abs(fin_c) if fin_c is not None else None,
        "profit_before_tax_mn": pbt_c,
        "taxation_mn": abs(tax_c) if tax_c is not None else None,
        "net_profit_mn": pat_c,
        "eps": eps_c,
        "dps": None,
        "total_assets_mn": ta_c,
        "current_assets_mn": ca_c,
        "cash_equivalents_mn": cash_c,
        "inventory_mn": inv_c,
        "receivables_mn": rec_c,
        "total_liabilities_mn": tl_c,
        "current_liabilities_mn": cl_c,
        "total_debt_mn": debt_c,
        "shareholders_equity_mn": eq_c,
        "operating_cashflow_mn": ocf_c,
        "capex_mn": capex_abs_c,
        "free_cashflow_mn": fcf_c,
        "dividends_paid_mn": abs(divp_c) if divp_c is not None else None,
        "auditor_opinion": auditor_opinion,
        "directors_highlights_json": directors_hl,
        "dividend_bonus_note": div_note,
        "segment_notes_json": segments,
        "source_pdf_url": source_pdf_url,
        "source_label": source_label,
    }

    out: list[dict[str, Any]] = []
    if any(cur_row[k] is not None for k in ("revenue_mn", "net_profit_mn", "total_assets_mn", "shareholders_equity_mn", "operating_cashflow_mn")):
        out.append(cur_row)

    prev_row = {
        "period_type": p_type,
        "period_label": prev_label,
        "period_end_date": prev_dt,
        "fiscal_year": prev_fy,
        "revenue_mn": abs(rev_p) if rev_p is not None else None,
        "cost_of_sales_mn": abs(cos_p) if cos_p is not None else None,
        "gross_profit_mn": gp_p,
        "operating_profit_mn": op_p,
        "finance_cost_mn": abs(fin_p) if fin_p is not None else None,
        "profit_before_tax_mn": pbt_p,
        "taxation_mn": abs(tax_p) if tax_p is not None else None,
        "net_profit_mn": pat_p,
        "eps": eps_p,
        "dps": None,
        "total_assets_mn": ta_p,
        "current_assets_mn": ca_p,
        "cash_equivalents_mn": cash_p,
        "inventory_mn": inv_p,
        "receivables_mn": rec_p,
        "total_liabilities_mn": tl_p,
        "current_liabilities_mn": cl_p,
        "total_debt_mn": debt_p,
        "shareholders_equity_mn": eq_p,
        "operating_cashflow_mn": ocf_p,
        "capex_mn": capex_abs_p,
        "free_cashflow_mn": fcf_p,
        "dividends_paid_mn": abs(divp_p) if divp_p is not None else None,
        "auditor_opinion": None,
        "directors_highlights_json": [],
        "dividend_bonus_note": None,
        "segment_notes_json": [],
        "source_pdf_url": source_pdf_url,
        "source_label": f"{source_label} (Comparative)",
    }
    if any(prev_row[k] is not None for k in ("revenue_mn", "net_profit_mn", "total_assets_mn", "shareholders_equity_mn", "operating_cashflow_mn")):
        out.append(prev_row)

    return out


def parse_company_report_pdf_bytes_sync(
    pdf_bytes: bytes,
    *,
    symbol: str,
    sector: str = "",
    filename_hint: str = "",
    period_type_hint: str | None = None,
    source_pdf_url: str | None = None,
    source_label: str = "PUCARS PDF",
    max_pages: int = 85,
) -> list[dict[str, Any]]:
    """Synchronously parse a PUCARS or uploaded Annual/Quarterly PDF report via `pdfplumber`."""
    pages_text: list[str] = []
    saw_bs = saw_pl = saw_cf = False
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for idx, page in enumerate(pdf.pages[:max_pages]):
            txt = _fast_page_text(page)
            if not txt:
                continue
            pages_text.append(txt)
            head = txt[:650].lower()
            low = txt.lower()
            if ("statement of financial position" in head or "balance sheet" in head) and "total assets" in low:
                saw_bs = True
            if ("profit and loss" in head or "profit or loss" in head) and ("profit after tax" in low or "taxation" in low):
                saw_pl = True
            if ("cash flows" in head or "cash flow statement" in head) and "operating activities" in low:
                saw_cf = True
            # Early exit once all 3 primary financial statements are captured & we passed front-matter narratives
            if saw_bs and saw_pl and saw_cf and idx >= 20:
                break

    return parse_company_report_pdf_text(
        pages_text,
        symbol=symbol,
        sector=sector,
        filename_hint=filename_hint,
        period_type_hint=period_type_hint,
        source_pdf_url=source_pdf_url,
        source_label=source_label,
    )


async def upsert_financial_periods(
    db: AsyncSession,
    asset: Asset,
    parsed_periods: list[dict[str, Any]],
    *,
    overwrite_existing: bool = True,
) -> int:
    """Upsert parsed multi-period records into `equity_financial_periods`, merging non-null fields cleanly."""
    if not parsed_periods:
        return 0
    existing_rows = (
        await db.execute(select(EquityFinancialPeriod).where(EquityFinancialPeriod.asset_id == asset.id))
    ).scalars().all()
    by_key = {(r.period_type, r.period_label): r for r in existing_rows}

    numeric_cols = [
        "revenue_mn", "cost_of_sales_mn", "gross_profit_mn", "operating_profit_mn",
        "finance_cost_mn", "profit_before_tax_mn", "taxation_mn", "net_profit_mn",
        "total_assets_mn", "current_assets_mn", "cash_equivalents_mn", "inventory_mn",
        "receivables_mn", "total_liabilities_mn", "current_liabilities_mn", "total_debt_mn",
        "shareholders_equity_mn", "operating_cashflow_mn", "capex_mn", "free_cashflow_mn",
        "dividends_paid_mn",
    ]
    count = 0
    now = datetime.now(timezone.utc)
    for item in parsed_periods:
        key = (str(item["period_type"]).upper(), str(item["period_label"]).strip())
        row = by_key.get(key)
        if row is None:
            row = EquityFinancialPeriod(
                asset_id=asset.id,
                period_type=key[0],
                period_label=key[1],
                period_end_date=item["period_end_date"],
                fiscal_year=int(item["fiscal_year"]),
            )
            db.add(row)
            by_key[key] = row

        for col in numeric_cols:
            v = item.get(col)
            if v is not None:
                # Guard against 1000x PDF scale-detection misreads overwriting a valid DPS revenue/PAT with < 1 Mn
                if col in {"revenue_mn", "net_profit_mn", "total_assets_mn"} and abs(float(v)) < 1.0:
                    existing_v = getattr(row, col)
                    if existing_v is not None and abs(float(existing_v)) >= 50.0:
                        continue
                if overwrite_existing or getattr(row, col) is None:
                    setattr(row, col, _dec(v, "0.01"))
        for col4 in ("eps", "dps"):
            v = item.get(col4)
            if v is not None and (overwrite_existing or getattr(row, col4) is None):
                setattr(row, col4, _dec(v, "0.0001"))

        if item.get("auditor_opinion") and (overwrite_existing or not row.auditor_opinion):
            row.auditor_opinion = str(item["auditor_opinion"])[:200]
        if item.get("directors_highlights_json") and (overwrite_existing or not row.directors_highlights_json):
            row.directors_highlights_json = list(item["directors_highlights_json"])[:8]
        if item.get("dividend_bonus_note") and (overwrite_existing or not row.dividend_bonus_note):
            row.dividend_bonus_note = str(item["dividend_bonus_note"])[:300]
        if item.get("segment_notes_json") and (overwrite_existing or not row.segment_notes_json):
            row.segment_notes_json = list(item["segment_notes_json"])[:8]
        if item.get("source_pdf_url"):
            row.source_pdf_url = str(item["source_pdf_url"])[:500]
        if item.get("source_label") and (overwrite_existing or row.source_label == "PSX DPS Financials"):
            row.source_label = str(item["source_label"])[:64]
        row.updated_at = now
        count += 1
    return count


async def seed_periods_from_psx_profile(db: AsyncSession, asset: Asset) -> int:
    """
    Convert multi-year Annual & Quarterly rows already stored on `PsxProfile.financial_summary_json`
    into `EquityFinancialPeriod` rows (preserving richer PDF-extracted Balance Sheet & Cash Flow columns).
    """
    prof = asset.psx_profile
    if prof is None or not prof.financial_summary_json:
        return 0
    fs = prof.financial_summary_json
    annual_list = fs if isinstance(fs, list) else (fs.get("annual_periods") or [])
    qtr_list = [] if isinstance(fs, list) else (fs.get("quarterly_periods") or [])

    parsed: list[dict[str, Any]] = []
    for row in annual_list:
        if not isinstance(row, dict):
            continue
        p_raw = str(row.get("period") or row.get("year") or "").strip()
        m_yr = re.search(r"(20\d{2})", p_raw)
        if not m_yr:
            continue
        yr = int(m_yr.group(1))
        rev = row.get("revenue_mn") or (float(row["revenue_bn"]) * 1000.0 if row.get("revenue_bn") else None) or row.get("revenue")
        pat = row.get("net_profit_mn") or (float(row["net_profit_bn"]) * 1000.0 if row.get("net_profit_bn") else None) or row.get("profit_after_tax")
        eps = row.get("eps")
        parsed.append(
            {
                "period_type": "ANNUAL",
                "period_label": f"FY{yr}",
                "period_end_date": date(yr, 6, 30),
                "fiscal_year": yr,
                "revenue_mn": float(rev) if rev is not None else None,
                "net_profit_mn": float(pat) if pat is not None else None,
                "eps": float(eps) if eps is not None else None,
                "source_label": "PSX DPS Financials",
            }
        )

    for row in qtr_list:
        if not isinstance(row, dict):
            continue
        p_raw = str(row.get("period") or "").strip()
        m_yr = re.search(r"(20\d{2})", p_raw)
        if not m_yr:
            continue
        yr = int(m_yr.group(1))
        up = p_raw.upper()
        if "Q1" in up or "1Q" in up or "SEP" in up:
            lbl, mo = f"Q1-{yr}", 9
        elif "Q2" in up or "2Q" in up or "DEC" in up or "HY" in up:
            lbl, mo = f"HY-{yr}", 12
        elif "Q3" in up or "3Q" in up or "MAR" in up or "9M" in up:
            lbl, mo = f"9M-{yr}", 3
        elif "Q4" in up or "4Q" in up or "JUN" in up:
            lbl, mo = f"Q4-{yr}", 6
        else:
            lbl, mo = f"Q-{p_raw[:10]}", 6
        rev = row.get("revenue_mn") or row.get("revenue")
        pat = row.get("net_profit_mn") or row.get("profit_after_tax")
        eps = row.get("eps")
        parsed.append(
            {
                "period_type": "QUARTERLY",
                "period_label": lbl,
                "period_end_date": date(yr, mo, 28),
                "fiscal_year": yr,
                "revenue_mn": float(rev) if rev is not None else None,
                "net_profit_mn": float(pat) if pat is not None else None,
                "eps": float(eps) if eps is not None else None,
                "source_label": "PSX DPS Financials",
            }
        )

    return await upsert_financial_periods(db, asset, parsed, overwrite_existing=False)


async def fetch_and_parse_pucars_reports_for_asset(
    db: AsyncSession,
    asset: Asset,
    session: CurlAsyncSession,
    *,
    max_pdfs: int = 2,
) -> int:
    """Fetch PSX DPS company page, select top Annual + Quarterly PUCARS PDFs, and persist multi-period records."""
    total = await seed_periods_from_psx_profile(db, asset)
    sym = asset.symbol.upper()
    try:
        r = await session.get(f"{PSX_BASE_URL}/company/{sym}", headers=HEADERS, timeout=18)
        if r.status_code != 200:
            return total
    except Exception:
        return total

    meta = parse_dps_company_deep_meta(r.text)
    pdf_urls = meta.get("pdf_urls") or []
    # Pick 1 Annual + 1 Quarterly PDF when available
    chosen: list[tuple[str, str, str | None]] = []
    annual_picked = False
    qtr_picked = False
    for title, url in pdf_urls:
        low = title.lower()
        if "annual" in low and not annual_picked:
            chosen.append((title, url, "ANNUAL"))
            annual_picked = True
        elif any(k in low for k in ("quarterly", "half yearly", "nine month", "first quarter", "third quarter")) and not qtr_picked:
            chosen.append((title, url, "QUARTERLY"))
            qtr_picked = True
        if len(chosen) >= max_pdfs:
            break
    if not chosen and pdf_urls:
        chosen = [(t, u, None) for t, u in pdf_urls[:max_pdfs]]

    for title, pdf_url, hint_type in chosen:
        try:
            rp = await session.get(pdf_url, headers=HEADERS, timeout=28)
            if rp.status_code != 200 or not rp.content[:5].startswith(b"%PDF") or len(rp.content) > MAX_REPORT_PDF_BYTES:
                continue
            parsed = await asyncio.to_thread(
                parse_company_report_pdf_bytes_sync,
                rp.content,
                symbol=sym,
                sector=asset.sector_or_category or "",
                filename_hint=title,
                period_type_hint=hint_type,
                source_pdf_url=pdf_url,
                source_label=f"PUCARS {'Annual' if hint_type == 'ANNUAL' else 'Quarterly' if hint_type == 'QUARTERLY' else 'Financial'} PDF",
            )
            if parsed:
                total += await upsert_financial_periods(db, asset, parsed, overwrite_existing=True)
        except Exception as exc:
            logger.debug("PUCARS report parse skipped for %s (%s): %s", sym, pdf_url, exc)
    return total


class PsxFinancialReportsScraper(BaseScraper):
    """
    Populates `equity_financial_periods` across PSX equities:
      1) Instant multi-year Annual & Quarterly ingestion from PSX DPS `financial_summary_json` across all PSX stocks.
      2) Deep PUCARS Annual + Quarterly PDF 3-statement & disclosure parsing for top KSE-100 stocks.
    """

    name = "psx_reports"

    async def run(self, db: AsyncSession, *, pdf_limit: int = 25) -> int:
        assets = (
            await db.execute(
                select(Asset)
                .options(selectinload(Asset.psx_profile))
                .where(Asset.owner_user_id.is_(None), Asset.asset_class == "PSX_EQUITY")
            )
        ).scalars().all()

        total_periods = 0
        for a in assets:
            total_periods += await seed_periods_from_psx_profile(db, a)
        await db.commit()

        # Prioritize KSE-100 companies by Market Cap for full PUCARS Annual + Quarterly PDF extraction
        kse100 = [a for a in assets if a.psx_profile and a.psx_profile.is_kse100]
        kse100.sort(key=lambda a: float(a.psx_profile.market_cap_mn or 0) if a.psx_profile else 0.0, reverse=True)
        targets = kse100[:pdf_limit]

        async with CurlAsyncSession(impersonate="chrome", timeout=25) as session:
            for a in targets:
                try:
                    n = await fetch_and_parse_pucars_reports_for_asset(db, a, session, max_pdfs=2)
                    total_periods += n
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.warning("PUCARS PDF report sync failed for %s: %s", a.symbol, exc)

        return total_periods
