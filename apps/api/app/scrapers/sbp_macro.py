"""
State Bank of Pakistan (SBP) & Global Macro Rate Scraper.

Fetches live authoritative Pakistani macro indicators from State Bank of Pakistan:
- SBP Monetary Policy Rate (`https://www.sbp.org.pk/`)
- KIBOR Bid/Offer (3M, 6M, 12M), Market Treasury Bills (1M, 3M, 6M, 12M),
  and Pakistan Investment Bonds / PKRV yields (2Y, 3Y, 5Y, 10Y) from
  `https://www.sbp.org.pk/ecodata/kibor_index.asp` (fallback `/dfmd/pkrv.asp`)
- Headline CPI YoY inflation when published on SBP pages / ecodata
- US 10-Year Treasury Yield (`^TNX`) via yfinance for USD/Global CAPM & Sharpe benchmarks
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import MacroIndicator
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)
D = Decimal

SBP_HOME_URL = "https://www.sbp.org.pk/"
SBP_KIBOR_URL = "https://www.sbp.org.pk/ecodata/kibor_index.asp"
SBP_PKRV_URL = "https://www.sbp.org.pk/dfmd/pkrv.asp"
SBP_ECODATA_INDEX_URL = "https://www.sbp.org.pk/ecodata/index2.asp"

SBP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_PKR_RISK_FREE_PCT = D("12.00")
DEFAULT_USD_RISK_FREE_PCT = D("4.25")


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

        # Table 1: KIBOR (3 columns: Tenor | Bid | Offer)
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
                        "extra_json": {"bid": float(bid) if bid is not None else None, "offer": float(offer)},
                    }
                )
            continue

        # Distinguish T-Bill vs PIB tables (2 columns: Tenor | Yield%)
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
                        "extra_json": {"instrument": "PIB"},
                    }
                )

    # De-duplicate keeping the last/most specific occurrence per series_key
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
            "extra_json": {"snippet": m.group(0)[:120]},
        }
    ]


WORLDBANK_PK_CPI_URL = "https://api.worldbank.org/v2/country/PAK/indicator/FP.CPI.TOTL.ZG?format=json&per_page=15"

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
    """Parse World Bank `FP.CPI.TOTL.ZG` JSON array into PK_CPI_YOY with multi-year history."""
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
        "source_label": "World Bank / PBS (FP.CPI.TOTL.ZG)",
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
    """Fetches SBP Policy Rate, KIBOR, T-Bill, PIB curve, CPI, and US 10Y yield."""

    name = "sbp_macro"

    async def run(self, db: AsyncSession) -> int:
        collected: list[dict[str, Any]] = []

        async with CurlAsyncSession(impersonate="chrome", timeout=20) as session:
            # 1. SBP Homepage -> Policy Rate (+ Inflation link if available)
            try:
                r_home = await session.get(SBP_HOME_URL, headers=SBP_HEADERS)
                if r_home.status_code == 200:
                    collected.extend(parse_sbp_policy_rate_html(r_home.text))
                    collected.extend(parse_sbp_cpi_html(r_home.text, SBP_HOME_URL))
            except Exception as exc:
                logger.warning("SBP home scrape failed: %s", exc)

            # 2. SBP KIBOR / T-Bills / PIB curve
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

            # 3. Official Pakistan CPI YoY fallback via World Bank / PBS open API (with 10Y history)
            wb_cpi: dict[str, Any] | None = None
            try:
                r_wb = await session.get(WORLDBANK_PK_CPI_URL, headers=SBP_HEADERS)
                if r_wb.status_code == 200:
                    wb_cpi = parse_worldbank_cpi_json(r_wb.json())
            except Exception as exc:
                logger.debug("World Bank Pakistan CPI fetch skipped: %s", exc)

            if not any(item["series_key"] == "PK_CPI_YOY" for item in collected):
                try:
                    r_eco = await session.get(SBP_ECODATA_INDEX_URL, headers=SBP_HEADERS)
                    if r_eco.status_code == 200:
                        collected.extend(parse_sbp_cpi_html(r_eco.text, SBP_ECODATA_INDEX_URL))
                except Exception:
                    pass
            if wb_cpi is not None:
                existing_cpi = next((item for item in collected if item["series_key"] == "PK_CPI_YOY"), None)
                if existing_cpi is None:
                    collected.append(wb_cpi)
                else:
                    existing_cpi["history_points"] = wb_cpi.get("history_points", [])

        # 4. US 10Y Treasury Yield (5Y monthly) via yfinance
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
        return upserted
