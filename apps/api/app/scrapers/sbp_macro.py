"""
State Bank of Pakistan (SBP EasyData / EcoData) & Pakistan Bureau of Statistics (PBS) Macro Scraper.

Fetches live authoritative Pakistani & Global macro indicators:
  1. PBS IMF-SDMX Monthly CPI (`https://www.pbs.gov.pk/cpi`):
     - Exact monthly Headline CPI YoY % (`PK_CPI_YOY`, 40+ consecutive months), MoM % (`PK_CPI_MOM`),
       and all 12 COICOP Inflation Basket sub-indices (`PK_CPI_BASKET`: Food, Housing/Utilities,
       Transport, Health, Education, etc. with official weights & YoY %).
  2. SBP Homepage & KIBOR/PKRV (`sbp.org.pk`):
     - SBP Monetary Policy Rate (`SBP_POLICY_RATE`)
     - KIBOR Offer (3M, 6M, 12M), Market T-Bills (1M, 3M, 6M, 12M), PIB Yields (2Y, 3Y, 5Y, 10Y)
  3. SBP EasyData / EcoData Official Excel Datasets (`sbp.org.pk/assets/document/`):
     - Liquid Foreign Exchange Reserves (`SBP_FX_RESERVES_BN` & `PK_TOTAL_FX_RESERVES_BN`)
     - Monthly Workers' Remittances (`SBP_REMITTANCES_MN` + top country corridors)
     - Monthly Current Account Balance (`SBP_CURRENT_ACCOUNT_MN`)
  4. US 10-Year Treasury Yield (`^TNX`) via yfinance for USD/Global CAPM & Sharpe benchmarks.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import openpyxl
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import api_cache
from app.models.market import MacroIndicator
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)
D = Decimal

SBP_HOME_URL = "https://www.sbp.org.pk/"
SBP_KIBOR_URL = "https://www.sbp.org.pk/ecodata/kibor_index.asp"
SBP_PKRV_URL = "https://www.sbp.org.pk/dfmd/pkrv.asp"
SBP_ECODATA_INDEX_URL = "https://www.sbp.org.pk/ecodata/index2.asp"
SBP_FOREX_XLSX_URL = "https://www.sbp.org.pk/assets/document/Forex_Arch.xlsx"
SBP_REMIT_XLSX_URL = "https://www.sbp.org.pk/assets/document/Homeremit_Arch.xlsx"
SBP_BOP_XLSX_URL = "https://www.sbp.org.pk/assets/document/bop.xlsx"
PBS_SDMX_CPI_URL = "https://www.pbs.gov.pk/cpi"
WORLDBANK_PK_CPI_URL = "https://api.worldbank.org/v2/country/PAK/indicator/FP.CPI.TOTL.ZG?format=json&per_page=15"

SBP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_PKR_RISK_FREE_PCT = D("12.00")
DEFAULT_USD_RISK_FREE_PCT = D("4.25")

COICOP_GROUP_LABELS: dict[str, str] = {
    "PCPI_CP_01": "Food & Non-Alcoholic Beverages",
    "PCPI_CP_02": "Tobacco & Beverages",
    "PCPI_CP_03": "Clothing & Footwear",
    "PCPI_CP_04": "Housing, Electricity & Gas",
    "PCPI_CP_05": "Furnishing & Household",
    "PCPI_CP_06": "Health",
    "PCPI_CP_07": "Transport",
    "PCPI_CP_08": "Communication",
    "PCPI_CP_09": "Recreation & Culture",
    "PCPI_CP_10": "Education",
    "PCPI_CP_11": "Restaurants & Hotels",
    "PCPI_CP_12": "Miscellaneous Goods & Services",
}

MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _clean_pct(raw: str | float | int | None) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).replace("%", "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(m.group(0)).quantize(D("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _parse_as_of_date(text: str) -> date:
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}",
        text,
        re.IGNORECASE,
    )
    if m:
        raw = m.group(0).replace(",", "")
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return date.today()


def _parse_excel_month_header(cell_val: Any) -> date | None:
    """Parse SBP Excel header values like datetime(2026, 7, 17), 'Oct-26', 'Sep-26R', 'Aug-2026'."""
    if isinstance(cell_val, datetime):
        return date(cell_val.year, cell_val.month, 1)
    if isinstance(cell_val, date):
        return date(cell_val.year, cell_val.month, 1)
    if not isinstance(cell_val, str):
        return None
    s = re.sub(r"[RP*#\s]+$", "", cell_val.strip())
    m = re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s](\d{2,4})$", s, re.I)
    if not m:
        return None
    mo = MONTH_ABBR.get(m.group(1)[:3].lower())
    yr = int(m.group(2))
    if yr < 100:
        yr = 2000 + yr if yr < 70 else 1900 + yr
    if mo and 1970 <= yr <= 2100:
        return date(yr, mo, 1)
    return None


def parse_sbp_policy_rate_html(html: str) -> list[dict[str, Any]]:
    """Extract SBP Policy Rate (and MPC effective date if present) from SBP homepage HTML."""
    results: list[dict[str, Any]] = []
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)

    patterns = [
        r"policy\s+rate[^0-9]{0,40}(?:by\s+\d+\s*bps\s+to\s+)?(\d{1,2}(?:\.\d{1,2})?)\s*%",
        r"SBP\s+Policy\s+Rate[^0-9]{0,30}(\d{1,2}(?:\.\d{1,2})?)\s*%",
    ]
    rate_val: Decimal | None = None
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = _clean_pct(m.group(1))
            if candidate is not None and D("1") <= candidate <= D("40"):
                rate_val = candidate
                break

    if rate_val is not None:
        eff_match = re.search(
            r"w\.e\.f\.?\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})",
            text,
            re.IGNORECASE,
        )
        as_of = _parse_as_of_date(eff_match.group(1)) if eff_match else date.today()
        results.append(
            {
                "series_key": "SBP_POLICY_RATE",
                "name": "SBP Monetary Policy Rate",
                "category": "POLICY",
                "tenor": "Policy",
                "value": rate_val,
                "unit": "%",
                "as_of_date": as_of,
                "source_url": SBP_HOME_URL,
                "source_label": "State Bank of Pakistan",
                "extra_json": {"note": eff_match.group(0)[:120] if eff_match else "SBP MPC Target Rate"},
            }
        )
    return results


def parse_sbp_kibor_pkrv_html(html: str, source_url: str = SBP_KIBOR_URL) -> list[dict[str, Any]]:
    """
    Parse KIBOR (3M, 6M, 12M), Market T-Bills (1M, 3M, 6M, 12M), and PIBs (2Y, 3Y, 5Y, 10Y)
    tables published on `sbp.org.pk/ecodata/kibor_index.asp` and `/dfmd/pkrv.asp`.
    """
    soup = BeautifulSoup(html, "lxml")
    full_text = soup.get_text(" ", strip=True)
    global_as_of = _parse_as_of_date(full_text)
    rows_out: list[dict[str, Any]] = []

    for tbl in soup.find_all("table"):
        tbl_text = tbl.get_text(" ", strip=True)
        tbl_as_of = _parse_as_of_date(tbl_text)
        as_of = tbl_as_of if tbl_as_of != date.today() else global_as_of

        trs = tbl.find_all("tr")
        parsed_rows: list[list[str]] = []
        for tr in trs:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                parsed_rows.append(cells)

        if not parsed_rows:
            continue

        joined = " ".join(" ".join(r) for r in parsed_rows).upper()

        if any(len(r) >= 3 and re.match(r"^(3|6|9|12)\s*-?\s*M", r[0], re.I) for r in parsed_rows) and (
            "KIBOR" in joined or "BID" in joined or "OFFER" in joined or len(parsed_rows[0]) == 3
        ):
            for r in parsed_rows:
                m = re.match(r"^(\d{1,2})\s*-?\s*M", r[0], re.I)
                if not m or len(r) < 3:
                    continue
                months = int(m.group(1))
                bid = _clean_pct(r[1])
                offer = _clean_pct(r[2])
                if offer is None or not (D("1") <= offer <= D("40")):
                    continue
                rows_out.append(
                    {
                        "series_key": f"KIBOR_{months}M",
                        "name": f"KIBOR {months}-Month Offer",
                        "category": "KIBOR",
                        "tenor": f"{months}M",
                        "value": offer,
                        "unit": "%",
                        "as_of_date": as_of,
                        "source_url": source_url,
                        "source_label": "State Bank of Pakistan",
                        "extra_json": {"bid": float(bid) if bid is not None else None, "offer": float(offer)},
                    }
                )
            continue

        for r in parsed_rows:
            if len(r) < 2:
                continue
            m_month = re.match(r"^(\d{1,2})\s*-?\s*M$", r[0].strip(), re.I)
            m_year = re.match(r"^(\d{1,2})\s*-?\s*Y$", r[0].strip(), re.I)
            val = _clean_pct(r[1])
            if val is None or not (D("1") <= val <= D("40")):
                continue
            if m_month:
                months = int(m_month.group(1))
                rows_out.append(
                    {
                        "series_key": f"TBILL_{months}M",
                        "name": f"PK {months}-Month T-Bill Cut-Off/PKRV",
                        "category": "TBILL",
                        "tenor": f"{months}M",
                        "value": val,
                        "unit": "%",
                        "as_of_date": as_of,
                        "source_url": source_url,
                        "source_label": "State Bank of Pakistan",
                        "extra_json": {"instrument": "MTB"},
                    }
                )
            elif m_year:
                yrs = int(m_year.group(1))
                rows_out.append(
                    {
                        "series_key": f"PIB_{yrs}Y",
                        "name": f"PK {yrs}-Year PIB Yield",
                        "category": "PIB",
                        "tenor": f"{yrs}Y",
                        "value": val,
                        "unit": "%",
                        "as_of_date": as_of,
                        "source_url": source_url,
                        "source_label": "State Bank of Pakistan",
                        "extra_json": {"instrument": "PIB"},
                    }
                )

    dedup: dict[str, dict[str, Any]] = {}
    for item in rows_out:
        dedup[item["series_key"]] = item
    return list(dedup.values())


def parse_sbp_cpi_html(html: str, source_url: str = SBP_ECODATA_INDEX_URL) -> list[dict[str, Any]]:
    """Best-effort extraction of Pakistan National CPI YoY inflation from SBP pages."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    m = re.search(
        r"(?:National\s+CPI|Headline\s+CPI|CPI\s+Inflation\s*\(YoY\)|CPI\s*\(Y/Y\))[^0-9]{0,30}(\d{1,2}(?:\.\d{1,2})?)\s*%",
        text,
        re.IGNORECASE,
    )
    if not m:
        return []
    val = _clean_pct(m.group(1))
    if val is None or not (D("0") <= val <= D("60")):
        return []
    return [
        {
            "series_key": "PK_CPI_YOY",
            "name": "Pakistan National CPI Inflation (YoY)",
            "category": "INFLATION",
            "tenor": "YoY",
            "value": val,
            "unit": "%",
            "as_of_date": date.today(),
            "source_url": source_url,
            "source_label": "State Bank of Pakistan",
            "extra_json": {"snippet": m.group(0)[:120]},
        }
    ]


def parse_pbs_sdmx_cpi_xml(xml_text: str) -> list[dict[str, Any]]:
    """
    Parse Pakistan Bureau of Statistics (PBS) IMF-SDMX CPI XML (`https://www.pbs.gov.pk/cpi`).
    Computes exact monthly Headline CPI YoY %, MoM %, 40+ month series, AND 12-group COICOP
    inflation breakdown (Food, Housing/Utilities, Transport, Health, Education, etc.).
    """
    if not xml_text or "StructureSpecificData" not in xml_text[:600]:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    index_series: dict[str, dict[str, float]] = {}
    weight_map: dict[str, float] = {}

    for elem in root.iter():
        if not elem.tag.endswith("Series"):
            continue
        ind = str(elem.attrib.get("INDICATOR") or "").strip()
        if not ind:
            continue
        obs_dict: dict[str, float] = {}
        for child in elem:
            if not child.tag.endswith("Obs"):
                continue
            tp = str(child.attrib.get("TIME_PERIOD") or "").strip()
            ov = child.attrib.get("OBS_VALUE")
            if not re.match(r"^\d{4}-\d{2}$", tp) or ov is None:
                continue
            try:
                obs_dict[tp] = float(ov)
            except ValueError:
                continue
        if not obs_dict:
            continue
        if ind.endswith("_WT"):
            latest_k = max(obs_dict.keys())
            weight_map[ind[:-3]] = round(obs_dict[latest_k], 2)
        elif ind.endswith("_IX"):
            index_series[ind] = obs_dict

    headline = index_series.get("PCPI_IX")
    if not headline or len(headline) < 13:
        return []

    def _prev_year_month(ym: str) -> str:
        y, m = int(ym[:4]), int(ym[5:7])
        return f"{y - 1:04d}-{m:02d}"

    def _prev_month(ym: str) -> str:
        y, m = int(ym[:4]), int(ym[5:7])
        if m == 1:
            return f"{y - 1:04d}-12"
        return f"{y:04d}-{m - 1:02d}"

    sorted_yms = sorted(headline.keys())
    yoy_pts: list[dict[str, Any]] = []
    mom_pts: list[dict[str, Any]] = []

    for ym in sorted_yms:
        cur = headline[ym]
        py = headline.get(_prev_year_month(ym))
        pm = headline.get(_prev_month(ym))
        if py and py > 0:
            yoy = round(((cur / py) - 1.0) * 100.0, 2)
            yoy_pts.append({"date": f"{ym}-01", "month": ym, "value": yoy, "index": round(cur, 2)})
        if pm and pm > 0:
            mom = round(((cur / pm) - 1.0) * 100.0, 2)
            mom_pts.append({"date": f"{ym}-01", "month": ym, "value": mom, "index": round(cur, 2)})

    if not yoy_pts:
        return []

    latest_ym = yoy_pts[-1]["month"]
    latest_dt = date(int(latest_ym[:4]), int(latest_ym[5:7]), 1)

    # Build 12-group COICOP inflation breakdown for the latest month
    basket_rows: list[dict[str, Any]] = []
    for base_code, label in COICOP_GROUP_LABELS.items():
        ser = index_series.get(f"{base_code}_IX")
        if not ser:
            continue
        cur_v = ser.get(latest_ym)
        py_v = ser.get(_prev_year_month(latest_ym))
        pm_v = ser.get(_prev_month(latest_ym))
        wt = weight_map.get(base_code, 0.0)
        if cur_v is None or py_v is None or py_v <= 0:
            continue
        grp_yoy = round(((cur_v / py_v) - 1.0) * 100.0, 2)
        grp_mom = round(((cur_v / pm_v) - 1.0) * 100.0, 2) if (pm_v and pm_v > 0) else None
        contrib_pct = round((wt / 100.0) * grp_yoy, 2)
        basket_rows.append(
            {
                "code": base_code,
                "group": label,
                "weight_pct": wt,
                "index": round(cur_v, 2),
                "yoy_pct": grp_yoy,
                "mom_pct": grp_mom,
                "contribution_pct": contrib_pct,
            }
        )
    basket_rows.sort(key=lambda x: x["weight_pct"], reverse=True)

    results: list[dict[str, Any]] = [
        {
            "series_key": "PK_CPI_YOY",
            "name": "Pakistan National CPI Inflation (YoY)",
            "category": "INFLATION",
            "tenor": "MONTHLY",
            "value": Decimal(str(yoy_pts[-1]["value"])).quantize(D("0.0001")),
            "unit": "%",
            "as_of_date": latest_dt,
            "source_url": PBS_SDMX_CPI_URL,
            "source_label": "PBS SDMX (Monthly National CPI)",
            "replace_history": True,
            "extra_json": {"month": latest_ym, "index": yoy_pts[-1]["index"]},
            "history_points": yoy_pts[-60:],
        }
    ]
    if mom_pts:
        results.append(
            {
                "series_key": "PK_CPI_MOM",
                "name": "Pakistan National CPI Inflation (MoM)",
                "category": "INFLATION",
                "tenor": "MONTHLY",
                "value": Decimal(str(mom_pts[-1]["value"])).quantize(D("0.0001")),
                "unit": "%",
                "as_of_date": latest_dt,
                "source_url": PBS_SDMX_CPI_URL,
                "source_label": "PBS SDMX (Monthly National CPI)",
                "replace_history": True,
                "extra_json": {"month": latest_ym},
                "history_points": mom_pts[-36:],
            }
        )
    if basket_rows:
        results.append(
            {
                "series_key": "PK_CPI_BASKET",
                "name": "PBS 12-Group CPI Inflation Basket (YoY)",
                "category": "INFLATION_BASKET",
                "tenor": latest_ym,
                "value": Decimal(str(yoy_pts[-1]["value"])).quantize(D("0.0001")),
                "unit": "%",
                "as_of_date": latest_dt,
                "source_url": PBS_SDMX_CPI_URL,
                "source_label": "PBS SDMX (COICOP 12 Groups)",
                "replace_history": True,
                "extra_json": {"month": latest_ym},
                "history_points": basket_rows,
            }
        )
    return results


def parse_sbp_remittances_xlsx(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse SBP `Homeremit_Arch.xlsx` into monthly Workers' Remittances (`SBP_REMITTANCES_MN`)."""
    if not raw_bytes:
        return []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
    except Exception:
        return []

    ws = wb[wb.sheetnames[-1]]
    rows = list(ws.iter_rows(values_only=True))
    header_row_idx = None
    month_cols: list[tuple[int, date]] = []

    for r_idx, row in enumerate(rows[:15]):
        found: list[tuple[int, date]] = []
        for c_idx, cell in enumerate(row):
            dt = _parse_excel_month_header(cell)
            if dt is not None:
                found.append((c_idx, dt))
        if len(found) >= 12:
            header_row_idx = r_idx
            month_cols = found
            break

    if header_row_idx is None or not month_cols:
        return []

    # Find total Cash remittances row right below header
    total_row = None
    corridors: dict[str, float] = {}
    last_col_idx = month_cols[-1][0]
    for row in rows[header_row_idx + 1 : header_row_idx + 25]:
        first_txt = " ".join(str(x or "").strip() for x in row[:3]).lower()
        if total_row is None and ("cash" in first_txt or "total" in first_txt):
            total_row = row
            continue
        for c_name in ("saudi arabia", "u.a.e.", "u.k.", "usa"):
            if c_name in first_txt and last_col_idx < len(row) and isinstance(row[last_col_idx], (int, float)):
                corridors[c_name.upper().rstrip(".")] = round(float(row[last_col_idx]), 1)

    if total_row is None:
        return []

    pts: list[dict[str, Any]] = []
    for c_idx, dt in month_cols:
        if c_idx >= len(total_row):
            continue
        v = total_row[c_idx]
        if isinstance(v, (int, float)) and v > 100:
            pts.append({"date": dt.isoformat(), "month": dt.strftime("%Y-%m"), "value": round(float(v), 1)})

    if not pts:
        return []
    pts.sort(key=lambda x: x["date"])
    latest = pts[-1]
    return [
        {
            "series_key": "SBP_REMITTANCES_MN",
            "name": "Workers' Remittances (Monthly)",
            "category": "EXTERNAL",
            "tenor": "MONTHLY",
            "value": Decimal(str(latest["value"])).quantize(D("0.01")),
            "unit": "USD Mn",
            "as_of_date": date.fromisoformat(latest["date"]),
            "source_url": SBP_REMIT_XLSX_URL,
            "source_label": "SBP EasyData / EcoData",
            "replace_history": True,
            "extra_json": {"corridors_usd_mn": corridors},
            "history_points": pts[-60:],
        }
    ]


def parse_sbp_forex_reserves_xlsx(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse SBP `Forex_Arch.xlsx` (Week-end sheet) into Net SBP & Total Liquid FX Reserves (USD Bn)."""
    if not raw_bytes:
        return []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
    except Exception:
        return []

    target_sheet = next((s for s in wb.sheetnames if "week" in s.lower()), wb.sheetnames[-1])
    ws = wb[target_sheet]
    sbp_pts: list[dict[str, Any]] = []
    tot_pts: list[dict[str, Any]] = []
    today_d = date.today()

    for row in ws.iter_rows(values_only=True):
        if len(row) < 5:
            continue
        dt_cell = row[1]
        dt_val: date | None = None
        if isinstance(dt_cell, datetime):
            dt_val = dt_cell.date()
        elif isinstance(dt_cell, date):
            dt_val = dt_cell
        elif isinstance(dt_cell, str):
            # Strip trailing SBP footnote markers like "20-Apr-23 16/" or "14-Aug-26*"
            cleaned_dt = re.sub(r"\s+\d+/\s*$", "", dt_cell.strip())
            m = re.match(r"^(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{2}|\d{4})(?:\D|$)", cleaned_dt)
            if m:
                mo = MONTH_ABBR.get(m.group(2).lower())
                yr = int(m.group(3))
                if yr < 100:
                    yr += 2000
                if mo and 2015 <= yr <= today_d.year:
                    try:
                        dt_val = date(yr, mo, int(m.group(1)))
                    except ValueError:
                        dt_val = None
        if dt_val is None or dt_val.year < 2015 or dt_val > today_d:
            continue
        sbp_mn = row[2]
        tot_mn = row[4]
        if isinstance(sbp_mn, (int, float)) and sbp_mn > 500:
            sbp_pts.append({"date": dt_val.isoformat(), "value": round(float(sbp_mn) / 1000.0, 2)})
        if isinstance(tot_mn, (int, float)) and tot_mn > 1000:
            tot_pts.append({"date": dt_val.isoformat(), "value": round(float(tot_mn) / 1000.0, 2)})

    out: list[dict[str, Any]] = []
    if sbp_pts:
        sbp_pts.sort(key=lambda x: x["date"])
        latest = sbp_pts[-1]
        out.append(
            {
                "series_key": "SBP_FX_RESERVES_BN",
                "name": "SBP Net FX Reserves",
                "category": "EXTERNAL",
                "tenor": "WEEKLY",
                "value": Decimal(str(latest["value"])).quantize(D("0.01")),
                "unit": "USD Bn",
                "as_of_date": date.fromisoformat(latest["date"]),
                "source_url": SBP_FOREX_XLSX_URL,
                "source_label": "SBP EasyData / EcoData",
                "replace_history": True,
                "history_points": sbp_pts[-60:],
            }
        )
    if tot_pts:
        tot_pts.sort(key=lambda x: x["date"])
        latest_t = tot_pts[-1]
        out.append(
            {
                "series_key": "PK_TOTAL_FX_RESERVES_BN",
                "name": "Total Liquid FX Reserves (SBP + Banks)",
                "category": "EXTERNAL",
                "tenor": "WEEKLY",
                "value": Decimal(str(latest_t["value"])).quantize(D("0.01")),
                "unit": "USD Bn",
                "as_of_date": date.fromisoformat(latest_t["date"]),
                "source_url": SBP_FOREX_XLSX_URL,
                "source_label": "SBP EasyData / EcoData",
                "replace_history": True,
                "history_points": tot_pts[-60:],
            }
        )
    return out


def parse_sbp_bop_xlsx(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse SBP `bop.xlsx` (`1.Summary BOP`) into monthly Current Account Balance (`SBP_CURRENT_ACCOUNT_MN`)."""
    if not raw_bytes:
        return []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
    except Exception:
        return []

    ws = wb[wb.sheetnames[0]]
    cab_pts: dict[str, dict[str, Any]] = {}
    today_d = date.today()
    for row in ws.iter_rows(values_only=True):
        if len(row) < 12:
            continue
        dt_cell = row[0]
        if not isinstance(dt_cell, (datetime, date)):
            continue
        dt_val = dt_cell.date() if isinstance(dt_cell, datetime) else dt_cell
        if dt_val.year < 2018 or dt_val > today_d:
            continue
        # Skip future FY placeholder rows where Current Account Credit & Debit are both zero/empty
        cred = row[1] if isinstance(row[1], (int, float)) else 0.0
        debt = row[2] if isinstance(row[2], (int, float)) else 0.0
        if abs(float(cred)) < 100.0 and abs(float(debt)) < 100.0:
            continue
        # Col 1 = Total Current Account Credit, Col 2 = Debit, Col 11 = Net Balance
        bal = row[11]
        if not isinstance(bal, (int, float)):
            bal = float(cred) - float(debt)
        ym = dt_val.strftime("%Y-%m")
        if ym not in cab_pts:
            cab_pts[ym] = {"date": dt_val.isoformat(), "month": ym, "value": round(float(bal), 1)}

    if not cab_pts:
        return []
    pts = [cab_pts[k] for k in sorted(cab_pts.keys())]
    latest = pts[-1]
    return [
        {
            "series_key": "SBP_CURRENT_ACCOUNT_MN",
            "name": "Current Account Balance (Monthly)",
            "category": "EXTERNAL",
            "tenor": "MONTHLY",
            "value": Decimal(str(latest["value"])).quantize(D("0.01")),
            "unit": "USD Mn",
            "as_of_date": date.fromisoformat(latest["date"]),
            "source_url": SBP_BOP_XLSX_URL,
            "source_label": "SBP EasyData / EcoData",
            "replace_history": True,
            "history_points": pts[-48:],
        }
    ]


# Official published State Bank of Pakistan MPC Policy Rate decisions (2020-present)
# Appended with live scraped SBP homepage Policy Rate decision on every run.
SBP_POLICY_RATE_HISTORICAL_DECISIONS: list[dict[str, Any]] = [
    {"date": "2020-03-17", "value": 12.50, "note": "MPC cut 75 bps"},
    {"date": "2020-03-24", "value": 11.00, "note": "Emergency MPC cut 150 bps"},
    {"date": "2020-04-16", "value": 9.00, "note": "MPC cut 200 bps"},
    {"date": "2020-05-15", "value": 8.00, "note": "MPC cut 100 bps"},
    {"date": "2020-06-25", "value": 7.00, "note": "MPC cut 100 bps (COVID trough)"},
    {"date": "2021-09-20", "value": 7.25, "note": "MPC hike 25 bps"},
    {"date": "2021-11-19", "value": 8.75, "note": "MPC hike 150 bps"},
    {"date": "2021-12-14", "value": 9.75, "note": "MPC hike 100 bps"},
    {"date": "2022-04-07", "value": 12.25, "note": "Emergency MPC hike 250 bps"},
    {"date": "2022-05-23", "value": 13.75, "note": "MPC hike 150 bps"},
    {"date": "2022-07-07", "value": 15.00, "note": "MPC hike 125 bps"},
    {"date": "2022-11-25", "value": 16.00, "note": "MPC hike 100 bps"},
    {"date": "2023-01-23", "value": 17.00, "note": "MPC hike 100 bps"},
    {"date": "2023-03-02", "value": 20.00, "note": "MPC hike 300 bps"},
    {"date": "2023-04-04", "value": 21.00, "note": "MPC hike 100 bps"},
    {"date": "2023-06-26", "value": 22.00, "note": "Emergency MPC hike to 22% cycle peak"},
    {"date": "2024-06-10", "value": 20.50, "note": "Easing cycle begins (-150 bps)"},
    {"date": "2024-07-29", "value": 19.50, "note": "MPC cut 100 bps"},
    {"date": "2024-09-12", "value": 17.50, "note": "MPC cut 200 bps"},
    {"date": "2024-11-04", "value": 15.00, "note": "MPC cut 250 bps"},
    {"date": "2024-12-16", "value": 13.00, "note": "MPC cut 200 bps"},
    {"date": "2025-01-27", "value": 12.00, "note": "MPC cut 100 bps"},
    {"date": "2025-05-05", "value": 11.00, "note": "MPC cut 100 bps"},
]


def _fetch_us_10y_treasury_sync() -> dict[str, Any] | None:
    try:
        import yfinance as yf

        tkr = yf.Ticker("^TNX")
        hist = tkr.history(period="5y", interval="1mo", auto_adjust=False)
        if hist is None or hist.empty:
            hist = tkr.history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna()
        last_close = _clean_pct(closes.iloc[-1])
        if last_close is None or last_close <= 0:
            return None
        last_dt = closes.index[-1].date() if hasattr(closes.index[-1], "date") else date.today()
        hist_pts: list[dict[str, Any]] = []
        for idx, val in closes.tail(60).items():
            fv = _clean_pct(val)
            if fv is not None and fv > 0:
                dt_i = idx.date() if hasattr(idx, "date") else last_dt
                hist_pts.append({"date": dt_i.isoformat(), "value": float(fv)})
        return {
            "series_key": "US_10Y_TREASURY",
            "name": "US 10-Year Treasury Yield (^TNX)",
            "category": "GLOBAL",
            "tenor": "10Y",
            "value": last_close,
            "unit": "%",
            "as_of_date": last_dt,
            "source_url": "https://finance.yahoo.com/quote/%5ETNX",
            "source_label": "yfinance (^TNX)",
            "extra_json": {"ticker": "^TNX"},
            "history_points": hist_pts,
        }
    except Exception as exc:
        logger.debug("US 10Y Treasury (^TNX) fetch skipped: %s", exc)
        return None


def parse_worldbank_cpi_json(payload: Any) -> dict[str, Any] | None:
    """Emergency fallback parser for World Bank `FP.CPI.TOTL.ZG` JSON array if PBS SDMX XML is unreachable."""
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return None
    pts: list[dict[str, Any]] = []
    for row in payload[1]:
        yr_str = str(row.get("date") or "").strip()
        val = row.get("value")
        if not yr_str.isdigit() or val is None:
            continue
        fv = _clean_pct(val)
        if fv is None:
            continue
        pts.append({"date": f"{yr_str}-06-30", "year": int(yr_str), "value": round(float(fv), 2)})
    if not pts:
        return None
    pts.sort(key=lambda x: x["date"])
    latest = pts[-1]
    return {
        "series_key": "PK_CPI_YOY",
        "name": "Pakistan Headline CPI Inflation (YoY)",
        "category": "INFLATION",
        "tenor": "ANNUAL",
        "value": Decimal(str(latest["value"])).quantize(D("0.0001")),
        "unit": "%",
        "as_of_date": date(latest["year"], 6, 30),
        "source_url": WORLDBANK_PK_CPI_URL,
        "source_label": "World Bank / PBS (Fallback)",
        "extra_json": {"indicator": "FP.CPI.TOTL.ZG"},
        "history_points": [{"date": p["date"], "value": p["value"]} for p in pts[-12:]],
    }


async def get_live_risk_free_rate_pct(db: AsyncSession, currency: str = "PKR") -> Decimal:
    """
    Return the authoritative live risk-free rate from DB `macro_indicators`.
    Uses US 10Y Treasury for USD/global equities and 6M T-Bill / SBP Policy Rate for PKR.
    """
    cur = (currency or "PKR").upper()
    preferred_keys = (
        ["US_10Y_TREASURY"]
        if cur in {"USD", "EUR", "GBP", "GBX"}
        else ["TBILL_6M", "SBP_POLICY_RATE", "TBILL_3M", "TBILL_12M", "KIBOR_6M"]
    )
    rows = {
        r.code: r.value
        for r in (
            await db.execute(
                select(MacroIndicator).where(MacroIndicator.code.in_(preferred_keys))
            )
        ).scalars()
    }
    for key in preferred_keys:
        val = rows.get(key)
        if val is not None and val > 0:
            return Decimal(val)
    return DEFAULT_USD_RISK_FREE_PCT if cur in {"USD", "EUR", "GBP", "GBX"} else DEFAULT_PKR_RISK_FREE_PCT


class SbpMacroScraper(BaseScraper):
    """Fetches SBP Policy Rate, KIBOR, T-Bill, PIB curve, PBS Monthly CPI/Basket, SBP FX Reserves/Remittances/CAB & US 10Y."""

    name = "sbp_macro"

    async def run(self, db: AsyncSession) -> int:
        collected: list[dict[str, Any]] = []

        # 1. PBS IMF-SDMX Monthly CPI + 12-Group COICOP Inflation Basket (authoritative monthly CPI!)
        has_pbs_cpi = False
        try:
            async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, verify=False, headers=SBP_HEADERS) as ac:
                r_pbs = await ac.get(PBS_SDMX_CPI_URL)
                if r_pbs.status_code == 200:
                    pbs_items = await asyncio.to_thread(parse_pbs_sdmx_cpi_xml, r_pbs.text)
                    if pbs_items:
                        collected.extend(pbs_items)
                        has_pbs_cpi = True
        except Exception as exc:
            logger.warning("PBS SDMX CPI fetch warning: %s", exc)

        async with CurlAsyncSession(impersonate="chrome", timeout=20) as session:
            # 2. SBP Homepage -> Policy Rate
            try:
                r_home = await session.get(SBP_HOME_URL, headers=SBP_HEADERS)
                if r_home.status_code == 200:
                    collected.extend(parse_sbp_policy_rate_html(r_home.text))
                    if not has_pbs_cpi:
                        collected.extend(parse_sbp_cpi_html(r_home.text, SBP_HOME_URL))
            except Exception as exc:
                logger.warning("SBP home scrape failed: %s", exc)

            # 3. SBP KIBOR / T-Bills / PIB curve
            for url in (SBP_KIBOR_URL, SBP_PKRV_URL):
                try:
                    r_rates = await session.get(url, headers=SBP_HEADERS)
                    if r_rates.status_code == 200:
                        parsed = parse_sbp_kibor_pkrv_html(r_rates.text, url)
                        if parsed:
                            collected.extend(parsed)
                            break
                except Exception as exc:
                    logger.warning("SBP rates scrape failed on %s: %s", url, exc)

            # 4. SBP EasyData / EcoData Official Excel Datasets (FX Reserves, Workers' Remittances, Current Account)
            async def _fetch_sbp_xlsx(url: str, parser_fn):
                try:
                    rx = await session.get(url, headers=SBP_HEADERS)
                    if rx.status_code == 200 and len(rx.content) > 5000:
                        return await asyncio.to_thread(parser_fn, rx.content)
                except Exception as exc:
                    logger.debug("SBP Excel fetch skipped (%s): %s", url, exc)
                return []

            sbp_ext_batches = await asyncio.gather(
                _fetch_sbp_xlsx(SBP_FOREX_XLSX_URL, parse_sbp_forex_reserves_xlsx),
                _fetch_sbp_xlsx(SBP_REMIT_XLSX_URL, parse_sbp_remittances_xlsx),
                _fetch_sbp_xlsx(SBP_BOP_XLSX_URL, parse_sbp_bop_xlsx),
            )
            for batch in sbp_ext_batches:
                collected.extend(batch)

            # 5. Emergency World Bank fallback ONLY if PBS monthly CPI failed
            if not has_pbs_cpi and not any(item["series_key"] == "PK_CPI_YOY" for item in collected):
                try:
                    r_wb = await session.get(WORLDBANK_PK_CPI_URL, headers=SBP_HEADERS)
                    if r_wb.status_code == 200:
                        wb_cpi = parse_worldbank_cpi_json(r_wb.json())
                        if wb_cpi is not None:
                            collected.append(wb_cpi)
                except Exception as exc:
                    logger.debug("World Bank Pakistan CPI fallback skipped: %s", exc)

        # 6. US 10Y Treasury Yield (5Y monthly) via yfinance
        us10y = await asyncio.to_thread(_fetch_us_10y_treasury_sync)
        if us10y is not None:
            collected.append(us10y)

        if not collected:
            return 0

        dedup: dict[str, dict[str, Any]] = {item["series_key"]: item for item in collected}
        existing = {
            row.code: row
            for row in (
                await db.execute(
                    select(MacroIndicator).where(MacroIndicator.code.in_(list(dedup.keys())))
                )
            ).scalars()
        }

        now = datetime.now(timezone.utc)
        upserted = 0
        for code_key, payload in dedup.items():
            row = existing.get(code_key)
            country = "US" if code_key == "US_10Y_TREASURY" else "PK"
            source_lbl = payload.get("source_label") or (
                "yfinance (^TNX)" if code_key == "US_10Y_TREASURY" else "State Bank of Pakistan"
            )

            if payload.get("replace_history"):
                final_hist = list(payload.get("history_points") or [])
            else:
                hist_entry = {"date": payload["as_of_date"].isoformat(), "value": float(payload["value"])}
                if payload.get("extra_json"):
                    hist_entry.update(payload["extra_json"])
                merged_by_date: dict[str, dict[str, Any]] = {}
                if code_key == "SBP_POLICY_RATE":
                    for pt in SBP_POLICY_RATE_HISTORICAL_DECISIONS:
                        merged_by_date[pt["date"]] = dict(pt)
                for pt in (payload.get("history_points") or []):
                    if pt.get("date"):
                        merged_by_date[str(pt["date"])] = dict(pt)
                if row is not None:
                    for pt in (row.history_json or []):
                        if isinstance(pt, dict) and pt.get("date"):
                            merged_by_date[str(pt["date"])] = dict(pt)
                merged_by_date[hist_entry["date"]] = hist_entry
                final_hist = [merged_by_date[k] for k in sorted(merged_by_date.keys())][-80:]

            if row is None:
                db.add(
                    MacroIndicator(
                        code=code_key,
                        name=payload["name"],
                        country=country,
                        category=payload["category"],
                        value=payload["value"],
                        unit=payload["unit"],
                        as_of_date=payload["as_of_date"],
                        source=source_lbl,
                        history_json=final_hist,
                        updated_at=now,
                    )
                )
            else:
                row.name = payload["name"]
                row.country = country
                row.category = payload["category"]
                row.value = payload["value"]
                row.unit = payload["unit"]
                row.as_of_date = payload["as_of_date"]
                row.source = source_lbl
                row.history_json = final_hist
                row.updated_at = now
            upserted += 1

        await db.commit()
        api_cache.invalidate_prefix("macro")
        api_cache.invalidate_prefix("overview")
        return upserted
