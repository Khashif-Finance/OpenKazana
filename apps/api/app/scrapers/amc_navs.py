"""
Direct Official Pakistan AMC Mutual Fund Scrapers, Playwright MUFAP Fallback & Historical NAV Backfill.

Covers Asset Management Companies directly when MUFAP.com.pk returns 503/Challenge:
- UBL Funds (`ublfunds.com.pk`) - Conventional, Al-Ameen Islamic & VPS (with TER & FEL)
- MCB Funds (`mcbfunds.com`) - MCB & Alhamra Islamic Mutual Funds (+ historical NAV backfill)
- HBL Asset Management (`hblasset.com`) - Conventional & Islamic Mutual Funds
- ABL Funds (`ablfunds.com`) - Conventional & ABL Islamic Funds (+ nav-archive support)
- JS Investments (`jsil.com`) - JS Conventional & Islamic Funds
- Headless Playwright MUFAP fallback (`mufap.com.pk`) - aborts cleanly if Cloudflare/503 gate appears
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_quote
from app.models.market import Asset, MutualFundProfile, PriceBar
from app.scrapers.base import BaseScraper

logger = logging.getLogger("openkhazana.scrapers.amc_navs")
D = Decimal

UBL_NAV_URL = "https://www.ublfunds.com.pk/resources-tools/fund-performance-tools/latest-fund-prices/"
MCB_NAV_URL = "https://www.mcbfunds.com/fund-prices/"
MCB_HISTORY_URL = "https://www.mcbfunds.com/fund-price-history/"
HBL_NAV_URL = "https://hblasset.com/fund-prices/"
HBL_HISTORY_PAGE_URL = "https://hblasset.com/fund-history/"
HBL_AJAX_URL = "https://hblasset.com/wp-admin/admin-ajax.php"
NBP_AJAX_URL = "https://www.nbpfunds.com/wp-admin/admin-ajax.php"
ABL_NAV_URL = "https://www.ablfunds.com/nav"
JSIL_NAV_URL = "https://jsil.com/fund-prices/"
LAKSON_NAV_URL = "https://www.li.com.pk/"
ATLAS_HISTORY_URL = "https://www.atlasfunds.com.pk/fund-price-history/"
MUFAP_DAILY_URL = "https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _d(val: str | None, default: Decimal = D("0")) -> Decimal:
    if not val:
        return default
    cleaned = re.sub(r"[^0-9.\-]", "", str(val).replace(",", ""))
    if not cleaned or cleaned in {"-", "."}:
        return default
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return default


def _parse_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    s = re.sub(r"\s+", " ", raw.strip())
    if s.startswith("0000-00-00"):
        return date.today()
    # Strip trailing timestamp e.g. "2026-11-20 00:00:00"
    if len(s) >= 19 and s[4] == "-" and s[10] == " ":
        s = s[:10]
    for fmt in (
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d %B, %Y",
        "%d %b, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _clean_symbol(prefix: str, fund_name: str, existing_symbols: set[str] | None = None) -> str:
    stop = {"fund", "of", "the", "and", "plan", "scheme", "mutual", "limited"}
    words = [w for w in re.split(r"[^A-Za-z0-9]+", fund_name) if w]
    initials = "".join(
        w[0].upper() if not w.isdigit() and w not in {"I", "II", "III", "IV", "V"} else w.upper()
        for w in words
        if w.lower() not in stop
    )
    digest = hashlib.sha1(fund_name.lower().encode("utf-8")).hexdigest()[:4].upper()
    core = re.sub(r"[^A-Z0-9]", "", f"{prefix}{initials}")[:12]
    sym = f"{core}_{digest}" if len(core) < 4 else core
    if existing_symbols and sym in existing_symbols:
        sym = f"{core[:10]}_{digest}"
    return sym[:24]


def _infer_fund_classification(name: str, section_hint: str = "") -> tuple[str, bool, bool, str]:
    """Return (fund_category, is_shariah, is_daily_dividend, asset_class)."""
    combined = f"{name} {section_hint}".lower()
    is_shariah = any(
        k in combined
        for k in ("islamic", "shariah", "sharia", "al-ameen", "al ameen", "alhamra", "meezan", "kmi", "halal", "takaful", "tahaffuz")
    )
    is_pension = any(k in combined for k in ("pension", "vps", "retirement", "tahaffuz"))
    is_daily_div = any(k in combined for k in ("rozana", "amdani", "daily dividend", "daily income", "mahana amdani"))

    if is_pension:
        cat = "Shariah Voluntary Pension" if is_shariah else "Voluntary Pension"
    elif any(k in combined for k in ("money market", "cash fund", "liquidity", "mahana", "rozana")):
        cat = "Shariah Money Market" if is_shariah else "Money Market"
    elif any(k in combined for k in ("sovereign", "income", "sukuk", "gokp", "fixed rate", "save", "mts")):
        cat = "Shariah Income" if is_shariah else "Income"
    elif any(k in combined for k in ("equity", "stock", "energy", "kmi-30", "kse-100", "dedicated")):
        cat = "Shariah Equity" if is_shariah else "Equity"
    elif any(k in combined for k in ("balanced", "asset allocation")):
        cat = "Shariah Asset Allocation" if is_shariah else "Asset Allocation"
    elif any(k in combined for k in ("commodities", "gold")):
        cat = "Commodities"
    elif any(k in combined for k in ("fund of funds", "allocation plan", "capital preservation", "financial planning")):
        cat = "Shariah Fund of Funds" if is_shariah else "Fund of Funds"
    else:
        cat = "Shariah Income" if is_shariah else "Income"

    asset_class = "VPS_PENSION" if is_pension else "MUTUAL_FUND"
    return cat, is_shariah, is_daily_div, asset_class


def parse_ubl_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse UBL Funds (`ublfunds.com.pk`) Conventional, Al-Ameen Islamic & VPS price tables."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tbl in soup.find_all("table"):
        section_hint = ""
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if not cells:
                continue
            first_low = cells[0].lower()
            if len(cells) <= 2 and ("conventional" in first_low or "islamic" in first_low or "pension" in first_low):
                section_hint = cells[0]
                continue
            if "management fee" in first_low or "fund name" in first_low or first_low == "fund" or len(cells) < 4:
                continue

            name = re.sub(r"\s+", " ", cells[0]).strip()
            if len(name) < 5:
                continue
            norm = _norm_name(name)
            if norm in seen_names:
                continue

            nav_date = _parse_date(cells[1])
            offer = _d(cells[2])
            redemption = _d(cells[3])
            nav = redemption if redemption > 0 else offer
            if nav <= 0 or nav > D("100000"):
                continue

            ter_pct = _d(cells[4], D("1.00")) if len(cells) > 4 else D("1.00")
            fel_pct = _d(cells[7], D("0.00")) if len(cells) > 7 else D("0.00")
            bel_pct = _d(cells[8], D("0.00")) if len(cells) > 8 else D("0.00")
            if ter_pct <= 0 or ter_pct > D("10"):
                ter_pct = D("1.00")

            seen_names.add(norm)
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(name, section_hint)
            amc = "Al-Ameen Islamic (UBL)" if is_shariah or "al-ameen" in name.lower() else "UBL Fund Managers"

            rows_out.append(
                {
                    "symbol": _clean_symbol("UBL", name),
                    "name": name[:200],
                    "amc_name": amc,
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": ter_pct.quantize(D("0.01")),
                    "front_end_load_pct": fel_pct.quantize(D("0.01")),
                    "back_end_load_pct": bel_pct.quantize(D("0.01")),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_mcb_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse MCB Funds (`mcbfunds.com/fund-prices/`) Mutual & Alhamra Islamic Fund prices."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    section_hint = ""

    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if not cells:
                continue
            if len(cells) == 1:
                section_hint = cells[0]
                continue
            if len(cells) < 5 or "fund name" in cells[0].lower():
                continue

            name = re.sub(r"\s+", " ", cells[0]).strip()
            if len(name) < 5:
                continue
            unit_type = cells[6] if len(cells) > 6 else ""
            full_label = f"{name} ({unit_type})" if unit_type and "growth" not in unit_type.lower() and unit_type != "N/A" else name
            norm = _norm_name(full_label)
            if norm in seen_names:
                continue

            nav_date = _parse_date(cells[1])
            nav = _d(cells[2])
            offer = _d(cells[3], nav)
            redemption = _d(cells[4], nav)
            if nav <= 0 or nav > D("100000"):
                continue

            fel_pct = _d(cells[7], D("0.00")) if len(cells) > 7 else D("0.00")
            bel_pct = _d(cells[8], D("0.00")) if len(cells) > 8 else D("0.00")
            seen_names.add(norm)
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(full_label, section_hint)
            amc = "Alhamra Islamic (MCB)" if is_shariah else "MCB Funds"

            rows_out.append(
                {
                    "symbol": _clean_symbol("MCB", full_label),
                    "name": full_label[:200],
                    "amc_name": amc,
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": D("0.85") if "Money Market" in cat else D("1.80"),
                    "front_end_load_pct": fel_pct.quantize(D("0.01")),
                    "back_end_load_pct": bel_pct.quantize(D("0.01")),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_hbl_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse HBL Asset Management (`hblasset.com/fund-prices/`) NAV table."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    section_hint = ""

    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if not cells:
                continue
            if len(cells) == 1:
                section_hint = cells[0]
                continue
            if len(cells) < 4 or "fund name" in cells[0].lower():
                continue

            name = re.sub(r"\s+", " ", cells[0]).strip()
            if len(name) < 5 or "note" in name.lower():
                continue
            norm = _norm_name(name)
            if norm in seen_names:
                continue

            offer = _d(cells[1])
            redemption = _d(cells[2])
            nav = redemption if redemption > 0 else offer
            if nav <= 0 or nav > D("100000"):
                continue

            nav_date = _parse_date(cells[3])
            seen_names.add(norm)
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(name, section_hint)
            rows_out.append(
                {
                    "symbol": _clean_symbol("HBL", name),
                    "name": name[:200],
                    "amc_name": "HBL Asset Management",
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": D("0.85") if "Money Market" in cat else D("1.75"),
                    "front_end_load_pct": D("0.00") if "Money Market" in cat else D("2.00"),
                    "back_end_load_pct": D("0.00"),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_abl_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse ABL Funds (`ablfunds.com/nav`) Mutual & Pension Fund NAV table."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) < 5 or "funds" == cells[0].lower() or "fund" == cells[0].lower():
                continue
            name = re.sub(r"\s+", " ", cells[0]).strip()
            if len(name) < 5:
                continue
            norm = _norm_name(name)
            if norm in seen_names:
                continue

            nav = _d(cells[1])
            offer = _d(cells[2], nav)
            redemption = _d(cells[3], nav)
            if nav <= 0 or nav > D("100000"):
                continue

            nav_date = _parse_date(cells[4])
            seen_names.add(norm)
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(name)
            rows_out.append(
                {
                    "symbol": _clean_symbol("ABL", name),
                    "name": name[:200],
                    "amc_name": "ABL Asset Management",
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": D("0.85") if "Money Market" in cat else D("1.75"),
                    "front_end_load_pct": D("0.00") if "Money Market" in cat else D("2.00"),
                    "back_end_load_pct": D("0.00"),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_jsil_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse JS Investments (`jsil.com/fund-prices/`) NAV table."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) < 5 or "fund" == cells[0].lower() or "processed" in cells[0].lower():
                continue
            name = re.sub(r"\s+", " ", cells[0]).strip()
            if len(name) < 5:
                continue
            norm = _norm_name(name)
            if norm in seen_names:
                continue

            nav_date = _parse_date(cells[1])
            offer = _d(cells[2])
            redemption = _d(cells[3])
            nav = _d(cells[4], redemption if redemption > 0 else offer)
            if nav <= 0 or nav > D("100000"):
                continue

            seen_names.add(norm)
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(name)
            rows_out.append(
                {
                    "symbol": _clean_symbol("JS", name),
                    "name": name[:200],
                    "amc_name": "JS Investments",
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": D("0.90") if "Money Market" in cat else D("1.80"),
                    "front_end_load_pct": D("0.00") if "Money Market" in cat else D("2.00"),
                    "back_end_load_pct": D("0.00"),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_lakson_nav_html(html: str) -> list[dict[str, Any]]:
    """Parse Lakson Investments (`li.com.pk`) NAV table."""
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tbl in soup.find_all("table"):
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) < 4:
                continue
            # Lakson home table columns: [id, Fund, Offer, Redemption, Applicable Date, ter]
            offset = 1 if cells[0].isdigit() else 0
            if len(cells) < offset + 3:
                continue
            name = re.sub(r"\s+", " ", cells[offset]).strip()
            if len(name) < 5 or name.lower() in {"fund", "fund name", "funds"}:
                continue
            norm = _norm_name(name)
            if norm in seen_names:
                continue

            offer = _d(cells[offset + 1])
            redemption = _d(cells[offset + 2], offer)
            nav = redemption if redemption > 0 else offer
            if nav <= 0 or nav > D("100000"):
                continue

            nav_date = _parse_date(cells[offset + 3]) if len(cells) > offset + 3 else date.today()
            ter = _d(cells[offset + 4]) if len(cells) > offset + 4 else D("0")
            cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(name)
            seen_names.add(norm)
            rows_out.append(
                {
                    "symbol": _clean_symbol("LAK", name),
                    "name": name[:200],
                    "amc_name": "Lakson Investments",
                    "fund_category": cat,
                    "nav": nav.quantize(D("0.0001")),
                    "offer_price": (offer if offer > 0 else nav).quantize(D("0.0001")),
                    "redemption_price": (redemption if redemption > 0 else nav).quantize(D("0.0001")),
                    "ter_pct": ter if D("0") < ter < D("10") else (D("0.85") if "Money Market" in cat else D("1.80")),
                    "front_end_load_pct": (
                        (((offer - redemption) / redemption) * D("100")).quantize(D("0.01"))
                        if offer > redemption > 0 and ((offer - redemption) / redemption) < D("0.06")
                        else D("0.00")
                    ),
                    "back_end_load_pct": D("0.00"),
                    "return_ytd_pct": None,
                    "return_1y_pct": None,
                    "return_3y_pct": None,
                    "annualized_yield_pct": None,
                    "aum_mn": D("0.00"),
                    "is_daily_dividend": is_daily_div,
                    "is_shariah_compliant": is_shariah,
                    "nav_date": nav_date,
                    "asset_class": asset_class,
                }
            )
    return rows_out


def parse_atlas_history_table(html: str) -> list[tuple[date, Decimal, Decimal]]:
    """
    Parse Atlas Funds historical NAV table (`atlasfunds.com.pk/fund-price-history/`).
    Returns sorted list of `(applicable_date, nav, ter_wl_pct)`.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[tuple[date, Decimal, Decimal]] = []
    seen_dates: set[date] = set()
    for tbl in soup.find_all("table"):
        trs = tbl.find_all("tr")
        if len(trs) < 2:
            continue
        hdr = [c.get_text(" ", strip=True).upper() for c in trs[0].find_all(["th", "td"])]
        if not any("DATE" in h for h in hdr) or not any("NAV" in h or "REDEMPTION" in h for h in hdr):
            continue
        for tr in trs[1:]:
            cols = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cols) < 4:
                continue
            dt = _parse_date(cols[0])
            nav_px = _d(cols[3], _d(cols[2], _d(cols[1])))
            ter_wl = _d(cols[4]) if len(cols) > 4 else D("0")
            if nav_px > 0 and nav_px < D("100000") and dt not in seen_dates:
                seen_dates.add(dt)
                rows.append((dt, nav_px.quantize(D("0.0001")), ter_wl if D("0") < ter_wl < D("10") else D("0")))
    rows.sort(key=lambda x: x[0])
    return rows


def parse_nav_history_upload_bytes(raw_bytes: bytes, filename: str = "nav_history.csv") -> list[tuple[date, Decimal]]:
    """
    Parse user-uploaded CSV or XLSX mutual fund NAV history file.
    Auto-detects date and NAV/Redemption/Price columns flexibly across AMC export formats.
    """
    import csv
    import io

    rows_raw: list[list[str]] = []
    lower_fn = filename.lower()
    if lower_fn.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        ws = wb.active
        for r in ws.iter_rows(values_only=True):
            rows_raw.append([str(c).strip() if c is not None else "" for c in r])
        wb.close()
    else:
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for r in reader:
            if any(cell.strip() for cell in r):
                rows_raw.append([cell.strip() for cell in r])

    if not rows_raw:
        return []

    date_col = 0
    nav_col = 1
    start_idx = 0
    for idx, r in enumerate(rows_raw[:5]):
        low = [c.lower() for c in r]
        if any(k in " ".join(low) for k in ("date", "nav", "redemption", "repurchase", "price")):
            start_idx = idx + 1
            for ci, h in enumerate(low):
                if "date" in h:
                    date_col = ci
                    break
            for ci, h in enumerate(low):
                if any(k in h for k in ("nav", "redemption", "repurchase", "close", "price")):
                    nav_col = ci
                    if "nav" in h or "redemption" in h or "repurchase" in h:
                        break
            break

    parsed: dict[date, Decimal] = {}
    for r in rows_raw[start_idx:]:
        if len(r) <= max(date_col, nav_col):
            continue
        raw_dt = r[date_col]
        if not raw_dt or not re.search(r"\d", raw_dt):
            continue
        dt = _parse_date(raw_dt)
        nav_px = _d(r[nav_col])
        if nav_px > 0 and nav_px < D("100000"):
            parsed[dt] = nav_px.quantize(D("0.0001"))

    return sorted(parsed.items(), key=lambda x: x[0])


async def fetch_mufap_via_playwright(url: str = MUFAP_DAILY_URL) -> str | None:
    """
    Attempt rendering MUFAP JS page via headless Chromium.
    Aborts immediately if upstream returns HTTP >= 500 or an anti-bot challenge page.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                resp = await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                if resp is not None and resp.status >= 400:
                    return None
                title = (await page.title()).lower()
                if "just a moment" in title or "service unavailable" in title:
                    return None
                await page.wait_for_timeout(1200)
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        logger.debug("Playwright MUFAP render skipped: %s", exc)
        return None


async def fetch_additional_amc_navs() -> list[dict[str, Any]]:
    """Concurrently fetch live NAV tables from UBL, MCB, HBL, ABL, JS Investments, and Lakson."""
    sources = [
        ("UBL", UBL_NAV_URL, parse_ubl_nav_html),
        ("MCB", MCB_NAV_URL, parse_mcb_nav_html),
        ("HBL", HBL_NAV_URL, parse_hbl_nav_html),
        ("ABL", ABL_NAV_URL, parse_abl_nav_html),
        ("JSIL", JSIL_NAV_URL, parse_jsil_nav_html),
        ("LAKSON", LAKSON_NAV_URL, parse_lakson_nav_html),
    ]
    all_rows: list[dict[str, Any]] = []

    async with CurlAsyncSession(impersonate="chrome", timeout=20) as session:
        async def _pull(label: str, url: str, parser: Any) -> list[dict[str, Any]]:
            try:
                resp = await session.get(url, headers=HEADERS, verify=False)
                if resp.status_code != 200:
                    logger.info("AMC %s returned HTTP %s", label, resp.status_code)
                    return []
                parsed = parser(resp.text)
                logger.info("AMC %s parsed %d funds", label, len(parsed))
                return parsed
            except Exception as exc:
                logger.warning("AMC %s scrape failed: %s", label, exc)
                return []

        batches = await asyncio.gather(*[_pull(lbl, u, fn) for lbl, u, fn in sources])
        for batch in batches:
            all_rows.extend(batch)

    return all_rows


async def upsert_mutual_fund_rows(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """
    Idempotently upsert mutual fund Assets, MutualFundProfiles, and daily NAV PriceBars.
    Matches existing funds by symbol OR normalized name so AMCs never create duplicate rows.
    """
    if not rows:
        return 0

    existing_assets = list(
        (
            await db.execute(
                select(Asset)
                .where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                )
            )
        ).scalars()
    )
    by_symbol = {a.symbol.upper(): a for a in existing_assets}
    by_norm_name = {_norm_name(a.name): a for a in existing_assets}
    all_symbols = {
        s.upper()
        for s in (await db.execute(select(Asset.symbol).where(Asset.owner_user_id.is_(None)))).scalars()
    }

    existing_profiles = {
        p.asset_id: p
        for p in (await db.execute(select(MutualFundProfile))).scalars()
    }

    now = datetime.now(timezone.utc)
    upserted = 0

    for item in rows:
        norm = _norm_name(item["name"])
        asset = by_symbol.get(item["symbol"].upper()) or by_norm_name.get(norm)
        if asset is None:
            sym = item["symbol"].upper()
            if sym in all_symbols:
                sym = _clean_symbol(sym[:3], item["name"], all_symbols)
            all_symbols.add(sym)
            asset = Asset(
                symbol=sym,
                name=item["name"],
                asset_class=item.get("asset_class", "MUTUAL_FUND"),
                sector_or_category=item["fund_category"],
                currency="PKR",
                is_shariah_compliant=item["is_shariah_compliant"],
                last_price=item["nav"],
                prev_close=item["nav"],
                change_pct=D("0"),
                volume=D("0"),
                source="amc_direct",
                updated_at=now,
            )
            db.add(asset)
            await db.flush()
            by_symbol[sym] = asset
            by_norm_name[norm] = asset
        else:
            prev_px = asset.last_price if asset.last_price and asset.last_price > 0 else item["nav"]
            if item["nav"] != prev_px and prev_px > 0:
                asset.prev_close = prev_px
                asset.change_pct = (((item["nav"] - prev_px) / prev_px) * D("100")).quantize(D("0.0001"))
            asset.last_price = item["nav"]
            asset.is_shariah_compliant = item["is_shariah_compliant"]
            asset.sector_or_category = item["fund_category"]
            asset.updated_at = now

        prof = existing_profiles.get(asset.id)
        if prof is None:
            prof = MutualFundProfile(
                asset_id=asset.id,
                amc_name=item["amc_name"],
                fund_category=item["fund_category"],
                ter_pct=item["ter_pct"],
                front_end_load_pct=item["front_end_load_pct"],
                return_1y_pct=item.get("return_1y_pct"),
                return_3y_pct=item.get("return_3y_pct"),
                annualized_yield_pct=item.get("annualized_yield_pct") or D("0.00"),
                aum_mn=item.get("aum_mn") or D("0.00"),
                is_daily_dividend=item["is_daily_dividend"],
                constant_par_nav=D("100.0000") if item["is_daily_dividend"] else None,
            )
            db.add(prof)
            existing_profiles[asset.id] = prof
        else:
            if item["ter_pct"] > 0 and ("UBL" in item["amc_name"] or "Al-Ameen" in item["amc_name"] or prof.ter_pct == 0):
                prof.ter_pct = item["ter_pct"]
            if item["front_end_load_pct"] > 0:
                prof.front_end_load_pct = item["front_end_load_pct"]
            if item.get("return_1y_pct") is not None:
                prof.return_1y_pct = item["return_1y_pct"]
            if item.get("return_3y_pct") is not None:
                prof.return_3y_pct = item["return_3y_pct"]
            if item.get("annualized_yield_pct") is not None:
                prof.annualized_yield_pct = item["annualized_yield_pct"]
            if (item.get("aum_mn") or D("0")) > 0:
                prof.aum_mn = item["aum_mn"]
            prof.is_daily_dividend = prof.is_daily_dividend or item["is_daily_dividend"]

        # Append/update daily NAV PriceBar so every fund accumulates real historical NAV history
        bar_dt = datetime(item["nav_date"].year, item["nav_date"].month, item["nav_date"].day, tzinfo=timezone.utc)
        existing_bar = (
            await db.execute(
                select(PriceBar).where(
                    PriceBar.asset_id == asset.id,
                    PriceBar.timeframe == "1D",
                    PriceBar.bar_time == bar_dt,
                )
            )
        ).scalar_one_or_none()
        if existing_bar is None:
            db.add(
                PriceBar(
                    asset_id=asset.id,
                    timeframe="1D",
                    bar_time=bar_dt,
                    open=item["nav"],
                    high=item["nav"],
                    low=item["nav"],
                    close=item["nav"],
                    volume=D("0"),
                )
            )
        else:
            existing_bar.close = item["nav"]
            existing_bar.high = max(existing_bar.high, item["nav"])
            existing_bar.low = min(existing_bar.low, item["nav"])

        await cache_quote(
            asset.symbol,
            {
                "symbol": asset.symbol,
                "last_price": str(item["nav"]),
                "currency": "PKR",
                "updated_at": now.isoformat(),
            },
        )
        upserted += 1

    await db.commit()
    return upserted


def _match_fund_asset(
    fund_name: str,
    by_norm: dict[str, Asset],
    amc_hint: str = "",
) -> Asset | None:
    norm = _norm_name(fund_name)
    if norm in by_norm:
        return by_norm[norm]
    # Also try stripping trailing plan/unit abbreviations or matching distinctive words
    fwords = [w for w in norm.split() if len(w) > 2 and w not in {"fund", "funds", "plan", "unit", "class", "type", "the"}]
    if len(fwords) >= 2:
        best: tuple[int, Asset] | None = None
        hint_low = amc_hint.lower()
        for nk, cand in by_norm.items():
            if hint_low and hint_low not in nk and hint_low not in (cand.name or "").lower():
                continue
            overlap = sum(1 for w in fwords if w in nk)
            if overlap == len(fwords) and (best is None or len(nk) < best[0]):
                best = (len(nk), cand)
        if best is not None:
            return best[1]
    return None


class MufapHistoricalNavScraper(BaseScraper):
    """
    Backfills up to 370 days of historical daily NAV PriceBars across four official open AMC
    history portals:
      1. MCB / Alhamra Islamic (`mcbfunds.com/fund-price-history/`)
      2. NBP Fund Management (`nbpfunds.com/wp-admin/admin-ajax.php` `get_nav_history`)
      3. HBL Asset Management (`hblasset.com/wp-admin/admin-ajax.php` `nav_history_show`)
      4. Atlas Asset Management (`atlasfunds.com.pk/fund-price-history/`, also registers Atlas funds + live TER)
    Recomputes 1M, 6M, and 1Y realized fund returns directly from historical NAV curves when missing.
    """

    name = "mufap_history"

    async def run(self, db: AsyncSession) -> int:
        today = date.today()
        start_dt = today - timedelta(days=370)
        from_mdy = start_dt.strftime("%m/%d/%Y")
        to_mdy = today.strftime("%m/%d/%Y")
        from_iso = start_dt.strftime("%Y-%m-%d")
        to_iso = today.strftime("%Y-%m-%d")

        all_funds = list(
            (
                await db.execute(
                    select(Asset)
                    .where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                    )
                )
            ).scalars()
        )
        by_norm = {_norm_name(a.name): a for a in all_funds}
        profiles = {
            p.asset_id: p
            for p in (await db.execute(select(MutualFundProfile))).scalars()
        }

        collected_series: list[tuple[str, str, list[tuple[date, Decimal]], Decimal]] = []
        atlas_daily_rows: list[dict[str, Any]] = []

        async with CurlAsyncSession(impersonate="chrome", timeout=25) as session:
            sem = asyncio.Semaphore(5)

            # 1. MCB / Alhamra Historical NAVs
            try:
                page = await session.get(MCB_HISTORY_URL, headers=HEADERS, verify=False)
                if page.status_code == 200:
                    soup = BeautifulSoup(page.text, "lxml")
                    sel = soup.find("select", {"name": "a[cat]"})
                    options = [
                        (o.get("value"), re.sub(r"\s+", " ", o.get_text(" ", strip=True)).strip())
                        for o in (sel.find_all("option") if sel else [])
                        if o.get("value")
                    ]

                    async def _fetch_mcb(fid: str, fname: str) -> tuple[str, list[tuple[date, Decimal]]]:
                        async with sem:
                            try:
                                rp = await session.post(
                                    MCB_HISTORY_URL,
                                    data={"a[cat]": fid, "a[from]": from_mdy, "a[to]": to_mdy, "btn_submit": "Submit"},
                                    headers=HEADERS,
                                    verify=False,
                                )
                                if rp.status_code != 200:
                                    return fname, []
                                sp = BeautifulSoup(rp.text, "lxml")
                                pts: list[tuple[date, Decimal]] = []
                                for tr in sp.select("table tr")[1:]:
                                    cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                                    if len(cols) < 2:
                                        continue
                                    nav_dt = _parse_date(cols[0])
                                    nav_px = _d(cols[1])
                                    if nav_px > 0 and nav_px < D("100000"):
                                        pts.append((nav_dt, nav_px.quantize(D("0.0001"))))
                                return fname, pts
                            except Exception:
                                return fname, []

                    mcb_res = await asyncio.gather(*[_fetch_mcb(fid, fn) for fid, fn in options[:50]])
                    for fn, pts in mcb_res:
                        if pts:
                            collected_series.append(("MCB", fn, pts, D("0")))
            except Exception as exc:
                logger.warning("MCB history scrape skipped: %s", exc)

            # 2. NBP Fund Management Historical NAVs (Mutual Funds cat=1 & Pension cat=2)
            try:
                nbp_targets: list[tuple[str, str, str]] = []
                for cat_id in ("1", "2"):
                    rf = await session.post(
                        NBP_AJAX_URL,
                        data={"action": "get_funds_by_category", "category_id": cat_id},
                        headers=HEADERS,
                        verify=False,
                    )
                    if rf.status_code == 200:
                        try:
                            items = rf.json()
                            if isinstance(items, list):
                                for it in items:
                                    fid = str(it.get("id") or "").strip()
                                    fname = str(it.get("fund_name") or "").strip()
                                    if fid and fname:
                                        nbp_targets.append((cat_id, fid, fname))
                        except Exception:
                            pass

                async def _fetch_nbp(cat_id: str, fid: str, fname: str) -> tuple[str, list[tuple[date, Decimal]]]:
                    async with sem:
                        try:
                            rh = await session.post(
                                NBP_AJAX_URL,
                                data={
                                    "action": "get_nav_history",
                                    "category_id": cat_id,
                                    "fund_id": fid,
                                    "start_date": from_iso,
                                    "end_date": to_iso,
                                },
                                headers=HEADERS,
                                verify=False,
                            )
                            if rh.status_code != 200:
                                return fname, []
                            payload = rh.json()
                            if not isinstance(payload, list):
                                return fname, []
                            pts: list[tuple[date, Decimal]] = []
                            for row in payload:
                                nav_px = _d(row.get("redemption") or row.get("nav") or row.get("offer"))
                                if nav_px > 0 and nav_px < D("100000"):
                                    pts.append((_parse_date(row.get("created_at")), nav_px.quantize(D("0.0001"))))
                            return fname, pts
                        except Exception:
                            return fname, []

                nbp_res = await asyncio.gather(*[_fetch_nbp(c, fid, fn) for c, fid, fn in nbp_targets[:60]])
                for fn, pts in nbp_res:
                    if pts:
                        collected_series.append(("NBP", fn, pts, D("0")))
            except Exception as exc:
                logger.warning("NBP history scrape skipped: %s", exc)

            # 3. HBL Asset Management Historical NAVs (DataTable server-side JSON across all funds)
            try:
                rh_page = await session.get(HBL_HISTORY_PAGE_URL, headers=HEADERS, verify=False)
                nm = re.search(r'"nonce":"([^"]+)"', rh_page.text) if rh_page.status_code == 200 else None
                if nm:
                    nonce = nm.group(1)
                    hbl_grouped: dict[str, list[tuple[date, Decimal]]] = {}
                    start_off = 0
                    page_len = 2500
                    for _ in range(8):
                        rj = await session.get(
                            HBL_AJAX_URL,
                            params={
                                "action": "nav_history_show",
                                "nonce": nonce,
                                "cat": "",
                                "fund": "",
                                "from_date": from_iso,
                                "to_date": to_iso,
                                "start": str(start_off),
                                "length": str(page_len),
                                "draw": "1",
                            },
                            headers=HEADERS,
                            verify=False,
                        )
                        if rj.status_code != 200:
                            break
                        data_rows = (rj.json() or {}).get("data") or []
                        if not data_rows:
                            break
                        for row in data_rows:
                            if not isinstance(row, list) or len(row) < 5:
                                continue
                            fname = BeautifulSoup(str(row[1]), "lxml").get_text(" ", strip=True)
                            nav_px = _d(row[3], _d(row[2]))
                            nav_dt = _parse_date(str(row[4]))
                            if fname and nav_px > 0 and nav_px < D("100000"):
                                hbl_grouped.setdefault(fname, []).append((nav_dt, nav_px.quantize(D("0.0001"))))
                        start_off += len(data_rows)
                        if len(data_rows) < page_len:
                            break
                    for fn, pts in hbl_grouped.items():
                        collected_series.append(("HBL", fn, pts, D("0")))
            except Exception as exc:
                logger.warning("HBL history scrape skipped: %s", exc)

            # 4. Atlas Asset Management Daily + Historical NAVs + TER
            try:
                ra_page = await session.get(ATLAS_HISTORY_URL, headers=HEADERS, verify=False)
                if ra_page.status_code == 200:
                    sp_a = BeautifulSoup(ra_page.text, "lxml")
                    fsel = sp_a.find("select", {"name": "fundselect"})
                    atlas_opts = [
                        (o.get("value"), re.sub(r"\s+", " ", o.get_text(" ", strip=True)).strip())
                        for o in (fsel.find_all("option") if fsel else [])
                        if o.get("value") and o.get("value").isdigit()
                    ]

                    async def _fetch_atlas(fid: str, fname: str) -> tuple[str, list[tuple[date, Decimal, Decimal]]]:
                        async with sem:
                            try:
                                rp = await session.post(
                                    ATLAS_HISTORY_URL,
                                    data={
                                        "catselect": "",
                                        "fundselect": fid,
                                        "from": from_iso,
                                        "to": to_iso,
                                        "search": "Search",
                                    },
                                    headers=HEADERS,
                                    verify=False,
                                )
                                if rp.status_code != 200:
                                    return fname, []
                                return fname, parse_atlas_history_table(rp.text)
                            except Exception:
                                return fname, []

                    atlas_res = await asyncio.gather(*[_fetch_atlas(fid, fn) for fid, fn in atlas_opts[:35]])
                    for fn, pts_ter in atlas_res:
                        if not pts_ter:
                            continue
                        latest_dt, latest_nav, latest_ter = pts_ter[-1]
                        cat, is_shariah, is_daily_div, asset_class = _infer_fund_classification(fn)
                        atlas_daily_rows.append(
                            {
                                "symbol": _clean_symbol("ATLAS", fn),
                                "name": fn[:200],
                                "amc_name": "Atlas Asset Management",
                                "fund_category": cat,
                                "nav": latest_nav,
                                "offer_price": latest_nav,
                                "redemption_price": latest_nav,
                                "ter_pct": latest_ter if latest_ter > 0 else (D("0.85") if "Money Market" in cat else D("1.80")),
                                "front_end_load_pct": D("0.00"),
                                "back_end_load_pct": D("0.00"),
                                "return_ytd_pct": None,
                                "return_1y_pct": None,
                                "return_3y_pct": None,
                                "annualized_yield_pct": None,
                                "aum_mn": D("0.00"),
                                "is_daily_dividend": is_daily_div,
                                "is_shariah_compliant": is_shariah,
                                "nav_date": latest_dt,
                                "asset_class": asset_class,
                            }
                        )
                        collected_series.append(
                            ("Atlas", fn, [(d, px) for d, px, _ in pts_ter], latest_ter)
                        )
            except Exception as exc:
                logger.warning("Atlas history scrape skipped: %s", exc)

        # Register Atlas funds in DB first so their historical bars link cleanly
        if atlas_daily_rows:
            await upsert_mutual_fund_rows(db, atlas_daily_rows)
            all_funds = list(
                (
                    await db.execute(
                        select(Asset).where(
                            Asset.owner_user_id.is_(None),
                            Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                        )
                    )
                ).scalars()
            )
            by_norm = {_norm_name(a.name): a for a in all_funds}
            profiles = {
                p.asset_id: p
                for p in (await db.execute(select(MutualFundProfile))).scalars()
            }

        total_bars = 0
        for amc_hint, fname, series, latest_ter in collected_series:
            if not series:
                continue
            asset = _match_fund_asset(fname, by_norm, amc_hint=amc_hint)
            if asset is None:
                asset = _match_fund_asset(fname, by_norm, amc_hint="")
            if asset is None:
                continue

            existing_dates = {
                (bt.date() if hasattr(bt, "date") else bt)
                for bt in (
                    await db.execute(
                        select(PriceBar.bar_time).where(
                            PriceBar.asset_id == asset.id,
                            PriceBar.timeframe == "1D",
                        )
                    )
                ).scalars()
            }
            for nav_dt, nav_px in series:
                if nav_dt in existing_dates:
                    continue
                existing_dates.add(nav_dt)
                dt_utc = datetime(nav_dt.year, nav_dt.month, nav_dt.day, tzinfo=timezone.utc)
                db.add(
                    PriceBar(
                        asset_id=asset.id,
                        timeframe="1D",
                        bar_time=dt_utc,
                        open=nav_px,
                        high=nav_px,
                        low=nav_px,
                        close=nav_px,
                        volume=D("0"),
                    )
                )
                total_bars += 1

            # Recompute trailing 1M / 6M / 1Y realized returns from historical NAV curve
            prof = profiles.get(asset.id)
            if prof is not None and len(series) >= 15:
                series_sorted = sorted({d: px for d, px in series}.items(), key=lambda x: x[0])
                latest_dt, latest_px = series_sorted[-1]
                if latest_ter > 0 and (prof.ter_pct == 0 or "Atlas" in (prof.amc_name or "")):
                    prof.ter_pct = latest_ter
                if latest_px > 0:
                    def _ret_for_days(
                        lookback_d: int,
                        _latest_dt=latest_dt,
                        _series_sorted=series_sorted,
                        _latest_px=latest_px,
                    ) -> Decimal | None:
                        target = _latest_dt - timedelta(days=lookback_d)
                        cands = [px for d, px in _series_sorted if d <= target]
                        if not cands:
                            return None
                        base = cands[-1]
                        if base <= 0:
                            return None
                        return Decimal(str(round(((float(_latest_px) / float(base)) - 1.0) * 100.0, 2)))

                    r1m = _ret_for_days(30)
                    r6m = _ret_for_days(180)
                    r1y = _ret_for_days(355)
                    if r1m is not None and prof.return_1m_pct is None:
                        prof.return_1m_pct = r1m
                    if r6m is not None and prof.return_6m_pct is None:
                        prof.return_6m_pct = r6m
                    if r1y is not None:
                        prof.return_1y_pct = r1y
                    else:
                        first_dt, first_px = series_sorted[0]
                        days_span = max(1, (latest_dt - first_dt).days)
                        if first_px > 0 and prof.return_1y_pct is None and days_span >= 150:
                            cagr_approx = ((float(latest_px) / float(first_px)) ** (365.0 / days_span) - 1.0) * 100.0
                            prof.return_1y_pct = Decimal(str(round(cagr_approx, 2)))

        await db.commit()
        return total_bars
