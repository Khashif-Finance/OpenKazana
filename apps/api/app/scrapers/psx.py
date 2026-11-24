from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_quote, get_redis
from app.models.market import Announcement, Asset, CorporateAction, PriceBar, PsxProfile, ShariahMetric
from app.models.portfolio import Transaction, WatchlistItem
from app.scrapers.base import PKT_OFFSET, BaseScraper, ScraperParseError

logger = logging.getLogger("openkhazana.scrapers.psx")

PSX_BASE_URL = "https://dps.psx.com.pk"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Referer": "https://dps.psx.com.pk/",
}

PSX_SECTOR_CODE_MAP: dict[str, str] = {
    "0801": "Automobile Assembler",
    "0802": "Automobile Parts & Accessories",
    "0803": "Cable & Electrical Goods",
    "0804": "Cement",
    "0805": "Chemical",
    "0806": "Close - End Mutual Fund",
    "0807": "Commercial Banks",
    "0808": "Engineering",
    "0809": "Fertilizer",
    "0810": "Food & Personal Care Products",
    "0811": "Glass & Ceramics",
    "0812": "Insurance",
    "0813": "Inv. Banks / Inv. Cos. / Securities Cos.",
    "0814": "Jute",
    "0815": "Leasing Companies",
    "0816": "Leather & Tanneries",
    "0818": "Miscellaneous",
    "0819": "Modarabas",
    "0820": "Oil & Gas Exploration Companies",
    "0821": "Oil & Gas Marketing Companies",
    "0822": "Paper, Board & Packaging",
    "0823": "Pharmaceuticals",
    "0824": "Power Generation & Distribution",
    "0825": "Refinery",
    "0826": "Sugar & Allied Industries",
    "0827": "Synthetic & Rayon",
    "0828": "Technology & Communication",
    "0829": "Textile Composite",
    "0830": "Textile Spinning",
    "0831": "Textile Weaving",
    "0832": "Tobacco",
    "0833": "Transport",
    "0834": "Vanaspati & Allied Industries",
    "0835": "Woollen",
    "0836": "Real Estate Investment Trust",
    "0837": "Exchange Traded Funds",
    "0838": "Property",
}


def _to_decimal(text: str | float | int | None, default: Decimal = Decimal("0")) -> Decimal:
    if text is None:
        return default
    raw = str(text).strip()
    is_paren_neg = raw.startswith("(") and raw.endswith(")")
    cleaned = (
        raw.strip("()")
        .replace(",", "")
        .replace("%", "")
        .replace("Rs.", "")
        .replace("PKR", "")
        .strip()
    )
    if not cleaned or cleaned in {"-", "--", "N/A", "n/a"}:
        return default
    mult = Decimal("1")
    upper_c = cleaned.upper()
    if upper_c.endswith("T"):
        mult = Decimal("1000000")
        cleaned = cleaned[:-1].strip()
    elif upper_c.endswith("B"):
        mult = Decimal("1000")
        cleaned = cleaned[:-1].strip()
    elif upper_c.endswith("M"):
        mult = Decimal("1")
        cleaned = cleaned[:-1].strip()
    elif upper_c.endswith("K"):
        mult = Decimal("0.001")
        cleaned = cleaned[:-1].strip()
    try:
        val = Decimal(cleaned) * mult
        return -val if is_paren_neg else val
    except InvalidOperation:
        return default


def _parse_date_flexible(raw: str | None) -> date | None:
    if not raw:
        return None
    text = raw.strip()
    # Strip trailing time like "October 22, 2026 2:50 PM" or book-closure ranges like "04/11/2026 - 05/11/2026"
    text = re.sub(r"\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?.*$", "", text).strip()
    if " - " in text:
        text = text.split(" - ")[0].strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _extract_psx_req_token(html: str) -> str | None:
    m = re.search(r'["\']_k["\']\s*:\s*["\']([^"\']+)["\']', html)
    return m.group(1) if m else None


async def _get_psx_ajax_headers(session: CurlAsyncSession) -> dict[str, str]:
    """Obtain PSX DPS `window.__ps._k` token required for AJAX endpoints (`/market-watch`, `/timeseries/*`, `/company/payouts`)."""
    headers = dict(HEADERS)
    try:
        r = await session.get(f"{PSX_BASE_URL}/")
        token = _extract_psx_req_token(r.text)
        if token:
            headers["X-Req-Id"] = token
            headers["X-Requested-With"] = "XMLHttpRequest"
    except Exception as exc:
        logger.debug("PSX token handshake fallback: %s", exc)
    return headers


def parse_market_watch_html(html: str, symbol_meta: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Parse PSX DPS /market-watch HTML table rows into normalized quote + OHLCV + index membership dicts."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    meta_map = symbol_meta or {}

    for tr in soup.select("table tbody tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cols) < 8:
            continue
        symbol = cols[0].split()[0].strip().upper()
        if not symbol:
            continue
        raw_sector = cols[1].strip() if len(cols) > 1 else "General"
        listed_in_raw = cols[2].upper().replace(" ", "") if len(cols) > 2 else ""
        listed_tokens = {tok.strip() for tok in listed_in_raw.split(",") if tok.strip()}

        s_meta = meta_map.get(symbol, {})
        if s_meta.get("isDebt"):
            continue
        sector = (
            s_meta.get("sectorName")
            or PSX_SECTOR_CODE_MAP.get(raw_sector)
            or (raw_sector if not raw_sector.isdigit() else "PSX Listed")
        )
        sector = str(sector).title() if str(sector).isupper() else str(sector)
        full_name = str(s_meta.get("name") or symbol).strip()

        ldcp = _to_decimal(cols[3])
        open_px = _to_decimal(cols[4])
        high_px = _to_decimal(cols[5])
        low_px = _to_decimal(cols[6])
        current = _to_decimal(cols[7])
        change_pct_raw = cols[9] if len(cols) > 9 else "0"
        change_pct = _to_decimal(change_pct_raw)
        if change_pct == 0 and ldcp > 0 and current > 0:
            change_pct = ((current - ldcp) / ldcp * Decimal("100")).quantize(Decimal("0.0001"))
        volume = _to_decimal(cols[-1])
        if current <= 0:
            continue

        is_etf = bool(s_meta.get("isETF") or symbol.endswith("ETF") or raw_sector == "0837")
        is_kse100 = "KSE100" in listed_tokens
        is_kmi30 = "KMI30" in listed_tokens
        is_shariah = bool(listed_tokens & {"KMI30", "KMIALLSHR", "MII30"})

        rows.append(
            {
                "symbol": symbol,
                "name": full_name[:200],
                "asset_class": "PSX_ETF" if is_etf else "PSX_EQUITY",
                "sector": sector[:120],
                "is_kse100": is_kse100,
                "is_kmi30": is_kmi30,
                "is_shariah": is_shariah,
                "prev_close": ldcp if ldcp > 0 else current,
                "open": open_px if open_px > 0 else current,
                "high": max(high_px if high_px > 0 else current, current),
                "low": min(low_px if low_px > 0 else current, current),
                "last_price": current,
                "change_pct": change_pct,
                "volume": volume,
            }
        )
    return rows


def parse_psx_indices_html(html: str) -> list[dict[str, Any]]:
    """Parse PSX /indices HTML table (`['Index', 'High', 'Low', 'Current', 'Change', '% Change']`)."""
    soup = BeautifulSoup(html, "lxml")
    results: dict[str, dict[str, Any]] = {}
    target_names = {
        "KSE100": "KSE100",
        "KSE-100": "KSE100",
        "KMI30": "KMI30",
        "KMI-30": "KMI30",
        "KSE30": "KSE30",
        "KSE-30": "KSE30",
        "ALLSHR": "ALLSHR",
        "KMIALLSHR": "KMIALLSHR",
    }

    for table in soup.select("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.select("thead th")]
        has_high_low_current = (
            len(headers) >= 4
            and "high" in headers[1]
            and "low" in headers[2]
            and "current" in headers[3]
        )

        for tr in table.select("tbody tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cols) < 4:
                continue
            raw_sym = cols[0].upper().replace(" ", "")
            canonical = target_names.get(raw_sym)
            if canonical is None:
                for key, mapped in target_names.items():
                    if raw_sym == key or (key in raw_sym and not raw_sym.endswith("PR")):
                        canonical = mapped
                        break
            if canonical is None or canonical in results:
                continue

            if has_high_low_current and len(cols) >= 6:
                high_px = _to_decimal(cols[1])
                low_px = _to_decimal(cols[2])
                current = _to_decimal(cols[3])
                change_pts = _to_decimal(cols[4])
                change_pct = _to_decimal(cols[5])
                volume = _to_decimal(cols[6]) if len(cols) > 6 else Decimal("0")
                prev_close = (current - change_pts) if (current > 0 and abs(change_pts) < current) else current
            else:
                pct_idx: int | None = None
                change_pct = Decimal("0")
                for idx, c in enumerate(cols[1:]):
                    if "%" in c:
                        change_pct = _to_decimal(c)
                        pct_idx = idx
                        break

                level_cols = cols[1 : 1 + pct_idx] if pct_idx is not None else cols[1:]
                level_nums: list[Decimal] = []
                for c in level_cols:
                    stripped = c.strip()
                    if stripped.startswith(("+", "-")):
                        continue
                    val = _to_decimal(stripped)
                    if val > 100:
                        level_nums.append(val)

                if not level_nums:
                    continue
                if len(level_nums) >= 3 and level_nums[0] >= level_nums[1]:
                    # [High, Low, Current, (optional Change)]
                    high_px = level_nums[0]
                    low_px = level_nums[1]
                    current = level_nums[2]
                else:
                    current = level_nums[-1]
                    high_px = max(level_nums)
                    low_px = min(level_nums)
                trailing_nums = [_to_decimal(c) for c in (cols[2 + pct_idx :] if pct_idx is not None else [])]
                volume = trailing_nums[-1] if trailing_nums and trailing_nums[-1] > 0 else Decimal("0")
                prev_close = (
                    (current / (Decimal("1") + change_pct / Decimal("100"))).quantize(Decimal("0.01"))
                    if change_pct != 0 and (Decimal("1") + change_pct / Decimal("100")) > 0
                    else current
                )

            if current <= 0:
                continue
            results[canonical] = {
                "symbol": canonical,
                "last_price": current,
                "prev_close": prev_close,
                "high": max(high_px, current),
                "low": min(low_px if low_px > 0 else current, current),
                "change_pct": change_pct,
                "volume": volume,
            }
    return list(results.values())


def parse_psx_company_payouts_html(html: str) -> list[dict[str, Any]]:
    """Parse HTML table returned by `POST /company/payouts` (`Date | Financial Results | Details | Book Closure`)."""
    soup = BeautifulSoup(html, "lxml")
    payouts: list[dict[str, Any]] = []
    for tr in soup.select("tbody tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cols) < 3:
            continue
        # Prefer Book Closure start date if present, otherwise announcement date
        ex_dt = (
            _parse_date_flexible(cols[3] if len(cols) > 3 else None)
            or _parse_date_flexible(cols[0])
        )
        if ex_dt is None:
            continue
        details = cols[2] if len(cols) > 2 else " | ".join(cols)
        detail_text = " | ".join(cols)
        lower_det = details.lower()

        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%?", details)
        pct_val = Decimal(pct_match.group(1)) if pct_match else Decimal("0")

        if "bonus" in lower_det or "(b)" in lower_det:
            payouts.append(
                {
                    "action_type": "BONUS_ISSUE",
                    "ex_date": ex_dt,
                    "payout_per_share": Decimal("0"),
                    "ratio_numerator": pct_val,
                    "ratio_denominator": Decimal("100"),
                    "description": detail_text[:250],
                }
            )
        elif "right" in lower_det or "(r)" in lower_det:
            payouts.append(
                {
                    "action_type": "RIGHT_ISSUE",
                    "ex_date": ex_dt,
                    "payout_per_share": Decimal("0"),
                    "ratio_numerator": pct_val,
                    "ratio_denominator": Decimal("100"),
                    "description": detail_text[:250],
                }
            )
        elif pct_val > 0 or "cash" in lower_det or "(d)" in lower_det or "(i)" in lower_det:
            per_share = (pct_val / Decimal("10")).quantize(Decimal("0.01")) if pct_val > 0 else Decimal("0")
            payouts.append(
                {
                    "action_type": "CASH_DIVIDEND",
                    "ex_date": ex_dt,
                    "payout_per_share": per_share,
                    "ratio_numerator": Decimal("0"),
                    "ratio_denominator": Decimal("100"),
                    "description": detail_text[:250],
                }
            )
    return payouts


def _classify_announcement_category(title: str) -> str:
    lower_title = title.lower()
    if "board meeting" in lower_title or "agm" in lower_title or "general meeting" in lower_title:
        return "Board Meeting"
    if "briefing" in lower_title:
        return "Corporate Briefing"
    if "dividend" in lower_title or "bonus" in lower_title or "payout" in lower_title or "entitlement" in lower_title:
        return "Dividend"
    if "financial result" in lower_title or "accounts" in lower_title or "quarterly report" in lower_title or "annual report" in lower_title:
        return "Financial Results"
    return "Material Info"


def _extract_doc_href(tr: Any) -> str:
    candidates = [
        a["href"].strip()
        for a in tr.select("a[href]")
        if a.has_attr("href")
        and a["href"].strip()
        and not a["href"].strip().lower().startswith(("javascript:", "#"))
    ]
    if not candidates:
        return ""
    chosen = ""
    for c in candidates:
        low = c.lower()
        if "/download/" in low or low.endswith(".pdf") or "/announcements/" in low:
            chosen = c
            break
    if not chosen:
        for c in candidates:
            if not c.startswith("/company/"):
                chosen = c
                break
    if not chosen:
        chosen = candidates[-1]
    if chosen.startswith("/"):
        chosen = f"{PSX_BASE_URL}{chosen}"
    return chosen


def _parse_metric_table(table: Any, periods: list[str]) -> dict[str, dict[str, Any]]:
    by_period: dict[str, dict[str, Any]] = {}
    for p in periods:
        if p:
            by_period.setdefault(p, {"period": p, "year": p})
    for tr in table.select("tbody tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
        if len(cols) < 2:
            continue
        metric_name = cols[0].strip().lower()
        for idx, p in enumerate(periods):
            if not p or (idx + 1) >= len(cols):
                continue
            val_dec = _to_decimal(cols[idx + 1], Decimal("0"))
            bucket = by_period[p]
            if any(k in metric_name for k in ["sales", "revenue", "mark-up earned", "total income"]):
                if "revenue" not in bucket or "sales" in metric_name or "mark-up" in metric_name:
                    rev_mn = float((val_dec / Decimal("1000")).quantize(Decimal("0.01")))
                    rev_bn = float((val_dec / Decimal("1000000")).quantize(Decimal("0.01")))
                    bucket["revenue"] = rev_mn
                    bucket["revenue_mn"] = rev_mn
                    bucket["revenue_bn"] = rev_bn
            elif "profit margin" in metric_name or "net margin" in metric_name:
                bucket["net_margin_pct"] = float(val_dec)
            elif "profit after tax" in metric_name or "net profit" in metric_name:
                pat_mn = float((val_dec / Decimal("1000")).quantize(Decimal("0.01")))
                pat_bn = float((val_dec / Decimal("1000000")).quantize(Decimal("0.01")))
                bucket["profit_after_tax"] = pat_mn
                bucket["net_profit_mn"] = pat_mn
                bucket["net_profit_bn"] = pat_bn
            elif metric_name == "eps" or metric_name.startswith("eps ("):
                bucket["eps"] = float(val_dec)
            elif "eps growth" in metric_name:
                bucket["eps_growth_pct"] = float(val_dec)
            elif metric_name == "peg":
                bucket["peg"] = float(val_dec)
    return by_period


def parse_psx_company_fundamentals_html(html: str, symbol: str, payouts_html: str | None = None) -> dict[str, Any]:
    """
    Parse PSX DPS /company/{SYM} HTML into structured live spot quote, valuation ratios,
    52-week range, payout history, annual/quarterly financial statements, and company announcements.
    """
    soup = BeautifulSoup(html, "lxml")
    stats: dict[str, str] = {}

    for item in soup.select(".stats_item"):
        lbl = item.select_one(".stats_label")
        val = item.select_one(".stats_value")
        if lbl and val:
            k = lbl.get_text(" ", strip=True).lower()
            v = val.get_text(" ", strip=True)
            if k and v:
                if k in stats and "%" in v and "%" not in stats[k]:
                    stats[f"{k}_pct"] = v
                else:
                    # Keep the first block (REG market) instead of letting FUT stats overwrite it
                    stats.setdefault(k, v)

    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) == 2:
            k = cells[0].lower()
            v = cells[1]
            if k and v:
                if k in stats and "%" in v and "%" not in stats[k]:
                    stats[f"{k}_pct"] = v
                else:
                    stats.setdefault(k, v)

    def _find_stat(*needles: str) -> str | None:
        for k, v in stats.items():
            if any(n in k for n in needles):
                return v
        return None

    spot_price = Decimal("0")
    quote_close_el = soup.select_one(".quote__close")
    if quote_close_el:
        spot_price = _to_decimal(quote_close_el.get_text(" ", strip=True))

    spot_change_pct: Decimal | None = None
    quote_chg_el = soup.select_one(".quote__change")
    if quote_chg_el:
        chg_text = quote_chg_el.get_text(" ", strip=True)
        m_pct = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", chg_text)
        if m_pct:
            spot_change_pct = _to_decimal(m_pct.group(1))

    reg_ldcp = _to_decimal(_find_stat("ldcp"))
    reg_volume = _to_decimal(_find_stat("volume"))
    if spot_price > 0 and reg_ldcp <= 0 and spot_change_pct is not None:
        denom = Decimal("1") + (spot_change_pct / Decimal("100"))
        if denom > 0:
            reg_ldcp = (spot_price / denom).quantize(Decimal("0.01"))
    if spot_price > 0 and reg_ldcp > 0 and spot_change_pct is None:
        spot_change_pct = ((spot_price - reg_ldcp) / reg_ldcp * Decimal("100")).quantize(Decimal("0.0001"))

    low_52w = Decimal("0")
    high_52w = Decimal("0")
    range_52 = _find_stat("52-week", "52 week", "52w range")
    if range_52 and ("-" in range_52 or "—" in range_52):
        parts = re.split(r"[-—]", range_52)
        if len(parts) >= 2:
            low_52w = _to_decimal(parts[0])
            high_52w = _to_decimal(parts[1])

    mcap_raw = _find_stat("market cap")
    mcap_mn = _to_decimal(mcap_raw)
    if mcap_raw and ("000" in mcap_raw.lower() or mcap_mn > Decimal("10000000")):
        mcap_mn = (mcap_mn / Decimal("1000")).quantize(Decimal("0.01"))

    pe_ratio = _find_stat("p/e", "pe ratio")
    pb_ratio = _find_stat("p/b", "pb ratio")
    eps_raw = _find_stat("eps")
    roe_raw = _find_stat("roe")
    div_yield_raw = _find_stat("dividend yield", "yield")
    free_float_raw = stats.get("free float_pct") or _find_stat("free float")
    free_float_pct = _to_decimal(free_float_raw)
    if free_float_pct > Decimal("100"):
        shares_raw = _find_stat("shares")
        total_shares = _to_decimal(shares_raw)
        if total_shares > 0:
            free_float_pct = ((free_float_pct / total_shares) * Decimal("100")).quantize(Decimal("0.01"))
        else:
            free_float_pct = Decimal("25.00")

    isin_raw = _find_stat("isin")

    payouts: list[dict[str, Any]] = []
    if payouts_html:
        payouts.extend(parse_psx_company_payouts_html(payouts_html))

    annual_by_year: dict[str, dict[str, Any]] = {}
    quarterly_by_period: dict[str, dict[str, Any]] = {}
    financial_rows: list[dict[str, Any]] = []
    quarterly_rows: list[dict[str, Any]] = []
    company_announcements: list[dict[str, Any]] = []

    for table in soup.select("table"):
        headers = [th.get_text(" ", strip=True) for th in table.select("thead th")]
        headers_lower = [h.lower() for h in headers]

        if len(headers_lower) == 3 and headers_lower[0] == "date" and "title" in headers_lower[1]:
            for tr in table.select("tbody tr")[:8]:
                cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cols) < 2:
                    continue
                ann_dt = _parse_date_flexible(cols[0])
                title_txt = cols[1].strip()
                if not ann_dt or not title_txt:
                    continue
                doc_href = _extract_doc_href(tr) or f"{PSX_BASE_URL}/company/{symbol.upper()}"
                company_announcements.append(
                    {
                        "symbol": symbol.upper(),
                        "title": title_txt[:250],
                        "category": _classify_announcement_category(title_txt),
                        "published_at": datetime(ann_dt.year, ann_dt.month, ann_dt.day, 10, 0, tzinfo=timezone.utc),
                        "source_url": doc_href[:500],
                        "summary": f"{cols[0]} | {title_txt}"[:500],
                    }
                )
        elif any("payout" in h or "entitlement" in h or "ex-date" in h or "dividend" in h or "book closure" in h for h in headers_lower):
            for tr in table.select("tbody tr"):
                cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cols) < 2:
                    continue
                date_candidate = None
                for c in cols:
                    d = _parse_date_flexible(c)
                    if d is not None:
                        date_candidate = d
                        break
                if date_candidate is None:
                    continue
                detail_text = " | ".join(cols)
                pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", detail_text)
                pct_val = Decimal(pct_match.group(1)) if pct_match else Decimal("0")
                lower_det = detail_text.lower()
                if "bonus" in lower_det or "(b)" in lower_det:
                    payouts.append(
                        {
                            "action_type": "BONUS_ISSUE",
                            "ex_date": date_candidate,
                            "payout_per_share": Decimal("0"),
                            "ratio_numerator": pct_val,
                            "ratio_denominator": Decimal("100"),
                            "description": detail_text[:250],
                        }
                    )
                elif "right" in lower_det or "(r)" in lower_det:
                    payouts.append(
                        {
                            "action_type": "RIGHT_ISSUE",
                            "ex_date": date_candidate,
                            "payout_per_share": Decimal("0"),
                            "ratio_numerator": pct_val,
                            "ratio_denominator": Decimal("100"),
                            "description": detail_text[:250],
                        }
                    )
                elif pct_val > 0 or "cash" in lower_det or "(d)" in lower_det or "(i)" in lower_det:
                    per_share = (pct_val / Decimal("10")).quantize(Decimal("0.01")) if pct_val > 0 else Decimal("0")
                    payouts.append(
                        {
                            "action_type": "CASH_DIVIDEND",
                            "ex_date": date_candidate,
                            "payout_per_share": per_share,
                            "ratio_numerator": Decimal("0"),
                            "ratio_denominator": Decimal("100"),
                            "description": detail_text[:250],
                        }
                    )
        elif len(headers) >= 3 and all(re.match(r"^20\d{2}$", h.strip()) for h in headers[1:] if h.strip()):
            years = [h.strip() for h in headers[1:]]
            parsed_ann = _parse_metric_table(table, years)
            for y, vals in parsed_ann.items():
                annual_by_year.setdefault(y, {"period": y, "year": y}).update(vals)
        elif len(headers) >= 3 and all(re.match(r"^Q[1-4]\s+20\d{2}$", h.strip(), re.I) for h in headers[1:] if h.strip()):
            qtrs = [h.strip() for h in headers[1:]]
            parsed_qtr = _parse_metric_table(table, qtrs)
            for q, vals in parsed_qtr.items():
                quarterly_by_period.setdefault(q, {"period": q, "year": q}).update(vals)
        elif headers_lower and len(headers_lower) >= 3 and any(h in " ".join(headers_lower) for h in ["sales", "revenue", "profit", "eps"]):
            for tr in table.select("tbody tr")[:8]:
                cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if cols:
                    financial_rows.append(dict(zip(headers_lower, cols, strict=False)))

    if annual_by_year:
        financial_rows = [annual_by_year[y] for y in sorted(annual_by_year.keys())]
    if quarterly_by_period:
        quarterly_rows = list(quarterly_by_period.values())

    eps_val = _to_decimal(eps_raw) if eps_raw and _to_decimal(eps_raw) != 0 else None
    if eps_val is None and financial_rows:
        for row in reversed(financial_rows):
            cand_eps = row.get("eps")
            if cand_eps is not None and _to_decimal(cand_eps) != 0:
                eps_val = _to_decimal(cand_eps).quantize(Decimal("0.01"))
                break

    # Keep ROE strictly from an actual ROE metric (never substitute Net Profit Margin)
    roe_val = _to_decimal(roe_raw) if roe_raw and _to_decimal(roe_raw) != 0 else None

    div_yield_val = _to_decimal(div_yield_raw)
    if div_yield_val <= 0 and payouts:
        ref_price = spot_price if spot_price > 0 else reg_ldcp
        if ref_price <= 0:
            ref_price = _to_decimal(_find_stat("close", "ldcp", "open"))
        cutoff = date.today() - timedelta(days=365)
        ttm_div = sum(
            (p["payout_per_share"] for p in payouts if p["action_type"] == "CASH_DIVIDEND" and p["ex_date"] >= cutoff),
            Decimal("0"),
        )
        if ref_price > 0 and ttm_div > 0:
            div_yield_val = ((ttm_div / ref_price) * Decimal("100")).quantize(Decimal("0.01"))

    fin_payload: dict[str, Any] | list[dict[str, Any]]
    if quarterly_rows:
        fin_payload = {"annual_periods": financial_rows, "quarterly_periods": quarterly_rows}
    elif financial_rows:
        fin_payload = {"annual_periods": financial_rows}
    else:
        fin_payload = []

    return {
        "symbol": symbol.upper(),
        "isin": isin_raw[:32] if isin_raw else None,
        "spot_price": spot_price if spot_price > 0 else None,
        "prev_close": reg_ldcp if reg_ldcp > 0 else None,
        "change_pct": spot_change_pct,
        "volume": reg_volume if reg_volume > 0 else None,
        "market_cap_mn": mcap_mn,
        "free_float_pct": free_float_pct,
        "pe_ratio": _to_decimal(pe_ratio) if pe_ratio and _to_decimal(pe_ratio) != 0 else None,
        "pb_ratio": _to_decimal(pb_ratio) if pb_ratio and _to_decimal(pb_ratio) != 0 else None,
        "eps": eps_val,
        "roe_pct": roe_val,
        "dividend_yield_pct": div_yield_val,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "payouts": payouts,
        "announcements": company_announcements,
        "financial_summary_json": fin_payload,
    }


def parse_psx_announcements_html(html: str) -> list[dict[str, Any]]:
    """
    Parse PSX DPS announcements HTML rows (both 6-col POST /announcements `DATE | TIME | SYMBOL | NAME | TITLE | DOC`
    and compact 4-col rows) into normalized PUCARS announcement dicts.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr, tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        cols = [td.get_text(" ", strip=True) for td in tds]
        parsed_date = _parse_date_flexible(cols[0]) or _parse_date_flexible(cols[1]) or datetime.now(timezone.utc).date()

        pub_dt = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 10, 0, tzinfo=timezone.utc)
        if len(cols) >= 6 and re.match(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", cols[1].strip(), re.I):
            try:
                t_parsed = datetime.strptime(cols[1].strip().upper(), "%I:%M %p").time()
                pub_dt = datetime.combine(parsed_date, t_parsed, tzinfo=PKT_OFFSET).astimezone(timezone.utc)
            except ValueError:
                pass
            symbol = cols[2].split()[0].strip().upper() if cols[2] else ""
            title = cols[4].strip() or cols[3].strip()
        else:
            symbol = ""
            for idx, col in enumerate(cols):
                if ":" in col:
                    continue
                cand = col.split()[0].strip().upper() if col else ""
                if 2 <= len(cand) <= 12 and cand.isalnum() and idx in {1, 2} and not symbol and cand.isalpha():
                    symbol = cand
            non_link_cols = [c for c in cols[2:] if c and c.lower() not in {"view", "view pdf", "pdf"}]
            title = max(non_link_cols, key=len) if non_link_cols else (cols[2] if len(cols) > 2 else "")

        if not symbol or not title:
            continue
        href = _extract_doc_href(tr) or f"{PSX_BASE_URL}/company/{symbol}"

        items.append(
            {
                "symbol": symbol,
                "title": title[:250],
                "category": _classify_announcement_category(title),
                "published_at": pub_dt,
                "source_url": href[:500],
                "summary": " | ".join(cols[:5])[:500],
            }
        )
    return items


def parse_timeseries_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse PSX DPS /timeseries/eod or /int JSON payload into chronologically ascending OHLCV bar dicts."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    bars: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        ts = int(item[0])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        close_px = _to_decimal(item[1])
        vol = _to_decimal(item[2])
        if len(item) >= 4:
            open_px = _to_decimal(item[3]) or close_px
            high_px = _to_decimal(item[4]) if len(item) > 4 else max(open_px, close_px)
            low_px = _to_decimal(item[5]) if len(item) > 5 else min(open_px, close_px)
        else:
            open_px = high_px = low_px = close_px
        if close_px <= 0:
            continue
        bars.append(
            {
                "bar_time": dt,
                "open": open_px,
                "high": max(high_px, open_px, close_px),
                "low": min(low_px if low_px > 0 else close_px, open_px, close_px),
                "close": close_px,
                "volume": vol,
            }
        )
    bars.sort(key=lambda b: b["bar_time"])
    return bars


async def fetch_lazy_intraday_bars(symbol: str) -> list[dict[str, Any]]:
    """
    On-demand lazy backfill for /timeseries/int/{SYM} cached in Redis for 5 minutes
    so background workers never spam PSX DPS per-symbol endpoints.
    """
    sym = symbol.upper()
    redis = await get_redis()
    cache_key = f"intraday_bars:{sym}"
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                raw_list = json.loads(cached)
                return [
                    {
                        "bar_time": datetime.fromisoformat(b["bar_time"]),
                        "open": Decimal(b["open"]),
                        "high": Decimal(b["high"]),
                        "low": Decimal(b["low"]),
                        "close": Decimal(b["close"]),
                        "volume": Decimal(b["volume"]),
                    }
                    for b in raw_list
                ]
        except Exception:
            pass

    bars: list[dict[str, Any]] = []
    try:
        async with CurlAsyncSession(impersonate="chrome", timeout=12) as session:
            hdrs = await _get_psx_ajax_headers(session)
            resp = await session.get(f"{PSX_BASE_URL}/timeseries/int/{sym}", headers=hdrs)
            if resp.status_code == 200:
                bars = parse_timeseries_json(resp.json())[-120:]
    except Exception:
        bars = []

    if redis is not None and bars:
        try:
            serializable = [
                {
                    "bar_time": b["bar_time"].isoformat(),
                    "open": str(b["open"]),
                    "high": str(b["high"]),
                    "low": str(b["low"]),
                    "close": str(b["close"]),
                    "volume": str(b["volume"]),
                }
                for b in bars
            ]
            await redis.set(cache_key, json.dumps(serializable), ex=300)
        except Exception:
            pass

    return bars


def _is_quote_stale(updated_at: datetime | None, max_age_minutes: int = 15) -> bool:
    if updated_at is None:
        return True
    ts = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) > timedelta(minutes=max_age_minutes)


async def _upsert_announcements(
    db: AsyncSession,
    asset_by_sym: dict[str, Asset],
    announcement_rows: list[dict[str, Any]],
) -> int:
    if not announcement_rows:
        return 0
    target_assets = [asset_by_sym[a["symbol"]] for a in announcement_rows if a.get("symbol") in asset_by_sym]
    if not target_assets:
        return 0
    asset_ids = list({a.id for a in target_assets})
    existing_keys = {
        (row[0], row[1])
        for row in (
            await db.execute(
                select(Announcement.asset_id, Announcement.title).where(Announcement.asset_id.in_(asset_ids))
            )
        ).all()
    }
    added = 0
    for ann in announcement_rows:
        asset = asset_by_sym.get(ann["symbol"])
        if asset is None:
            continue
        key = (asset.id, ann["title"])
        if key in existing_keys:
            continue
        existing_keys.add(key)
        db.add(
            Announcement(
                asset_id=asset.id,
                title=ann["title"],
                category=ann["category"],
                published_at=ann["published_at"],
                source_url=ann["source_url"],
                summary=ann["summary"],
            )
        )
        added += 1
    return added


async def _apply_parsed_company_fundamentals(
    db: AsyncSession,
    asset: Asset,
    prof: PsxProfile | None,
    parsed: dict[str, Any],
    existing_actions: set[tuple[str, date]] | None = None,
    now: datetime | None = None,
) -> tuple[PsxProfile, int]:
    """Persist parsed `/company/{SYM}` spot quote, ratios, financials, payouts, and announcements."""
    stamp = now or datetime.now(timezone.utc)
    if prof is None:
        prof = PsxProfile(asset_id=asset.id)
        db.add(prof)

    # Refresh live spot quote from `.quote__close` when missing, seed/catalog, or stale (>15 min)
    if parsed.get("spot_price") is not None and (
        asset.last_price <= 0 or asset.source in {"seed", "reference_catalog"} or _is_quote_stale(asset.updated_at, 15)
    ):
        asset.last_price = parsed["spot_price"]
        if parsed.get("prev_close") is not None:
            asset.prev_close = parsed["prev_close"]
        if parsed.get("change_pct") is not None:
            asset.change_pct = parsed["change_pct"]
        if parsed.get("volume") is not None:
            asset.volume = parsed["volume"]
        asset.source = "psx_dps"
        asset.updated_at = stamp

    if parsed["isin"]:
        prof.isin = parsed["isin"]
    if parsed["market_cap_mn"] > 0:
        prof.market_cap_mn = parsed["market_cap_mn"]
    if parsed["free_float_pct"] > 0:
        prof.free_float_pct = parsed["free_float_pct"]
    if parsed["pe_ratio"] is not None:
        prof.pe_ratio = parsed["pe_ratio"]
    if parsed["pb_ratio"] is not None:
        prof.pb_ratio = parsed["pb_ratio"]
    if parsed["eps"] is not None:
        prof.eps = parsed["eps"]
    if parsed["roe_pct"] is not None:
        prof.roe_pct = parsed["roe_pct"]
    if parsed["dividend_yield_pct"] > 0:
        prof.dividend_yield_pct = parsed["dividend_yield_pct"]
    if parsed["high_52w"] > 0:
        prof.high_52w = parsed["high_52w"]
    if parsed["low_52w"] > 0:
        prof.low_52w = parsed["low_52w"]
    if parsed["financial_summary_json"]:
        prof.financial_summary_json = parsed["financial_summary_json"]

    upserted = 1
    if existing_actions is None:
        existing_actions = {
            (ca.action_type, ca.ex_date)
            for ca in (
                await db.execute(select(CorporateAction).where(CorporateAction.asset_id == asset.id))
            ).scalars()
        }
    for payout in parsed["payouts"]:
        key = (payout["action_type"], payout["ex_date"])
        if key not in existing_actions:
            existing_actions.add(key)
            db.add(
                CorporateAction(
                    asset_id=asset.id,
                    action_type=payout["action_type"],
                    ex_date=payout["ex_date"],
                    payout_per_share=payout["payout_per_share"],
                    ratio_numerator=payout["ratio_numerator"],
                    ratio_denominator=payout["ratio_denominator"],
                    description=payout["description"],
                )
            )
            upserted += 1

    upserted += await _upsert_announcements(
        db,
        {asset.symbol.upper(): asset},
        parsed.get("announcements") or [],
    )
    return prof, upserted


async def ensure_psx_asset_backfilled(
    db: AsyncSession,
    asset: Asset,
    min_bars: int = 30,
    fetch_fundamentals: bool = True,
) -> None:
    """
    On-demand real-world backfill for any PSX equity, ETF, or index (`KSE100`, `KMI30`, `KSE30`):
    lazily fetches and persists real EOD `1D` OHLCV bars from `/timeseries/eod/{SYM}` and company
    fundamentals + payouts + announcements from `/company/{SYM}` & `/company/payouts` whenever
    missing, seed/reference-only, or stale.
    """
    if asset.asset_class not in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
        return

    bar_count = (
        await db.execute(
            select(func.count(PriceBar.id)).where(
                PriceBar.asset_id == asset.id,
                PriceBar.timeframe == "1D",
            )
        )
    ).scalar_one()

    prof = (
        await db.execute(select(PsxProfile).where(PsxProfile.asset_id == asset.id))
    ).scalar_one_or_none()

    is_unverified_source = asset.source in {"seed", "reference_catalog"}
    fin_json = prof.financial_summary_json if prof is not None else None
    has_normalized_fin = isinstance(fin_json, dict) and bool(fin_json.get("annual_periods"))

    needs_bars = bar_count < min_bars
    needs_quote = asset.last_price <= 0 or is_unverified_source or _is_quote_stale(asset.updated_at, 1440)
    needs_fundamentals = (
        fetch_fundamentals
        and asset.asset_class in {"PSX_EQUITY", "PSX_ETF"}
        and (
            prof is None
            or not has_normalized_fin
            or prof.eps is None
            or prof.market_cap_mn <= 0
            or (is_unverified_source and needs_quote)
        )
    )

    if not needs_bars and not needs_fundamentals and not (needs_quote and asset.asset_class == "PSX_INDEX"):
        return

    now = datetime.now(timezone.utc)
    try:
        async with CurlAsyncSession(impersonate="chrome", timeout=15) as session:
            hdrs = await _get_psx_ajax_headers(session)
            committed = False

            if needs_bars or (needs_quote and asset.asset_class == "PSX_INDEX"):
                eod_resp = await session.get(f"{PSX_BASE_URL}/timeseries/eod/{asset.symbol}", headers=hdrs)
                if eod_resp.status_code == 200:
                    recent_bars = parse_timeseries_json(eod_resp.json())[-250:]
                    if recent_bars:
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
                        for bar in recent_bars:
                            b_date = bar["bar_time"].date()
                            if b_date in existing_dates:
                                continue
                            existing_dates.add(b_date)
                            db.add(
                                PriceBar(
                                    asset_id=asset.id,
                                    timeframe="1D",
                                    bar_time=bar["bar_time"],
                                    open=bar["open"],
                                    high=bar["high"],
                                    low=bar["low"],
                                    close=bar["close"],
                                    volume=bar["volume"],
                                )
                            )
                        last_b = recent_bars[-1]
                        prev_b = recent_bars[-2] if len(recent_bars) >= 2 else last_b
                        if asset.last_price <= 0 or is_unverified_source or _is_quote_stale(asset.updated_at, 15):
                            asset.last_price = last_b["close"]
                            asset.prev_close = prev_b["close"]
                            asset.change_pct = (
                                ((last_b["close"] - prev_b["close"]) / prev_b["close"] * Decimal("100")).quantize(Decimal("0.0001"))
                                if prev_b["close"] > 0
                                else Decimal("0")
                            )
                            asset.volume = last_b["volume"]
                            asset.source = "psx_dps"
                            asset.updated_at = now
                        committed = True

            if needs_fundamentals:
                comp_resp = await session.get(f"{PSX_BASE_URL}/company/{asset.symbol}", headers=HEADERS)
                payouts_text: str | None = None
                try:
                    pay_resp = await session.post(
                        f"{PSX_BASE_URL}/company/payouts",
                        data={"symbol": asset.symbol},
                        headers=hdrs,
                    )
                    if pay_resp.status_code == 200:
                        payouts_text = pay_resp.text
                except Exception:
                    payouts_text = None

                if comp_resp.status_code == 200:
                    parsed = parse_psx_company_fundamentals_html(
                        comp_resp.text,
                        asset.symbol,
                        payouts_html=payouts_text,
                    )
                    prof, _ = await _apply_parsed_company_fundamentals(
                        db, asset, prof, parsed, now=now
                    )
                    committed = True

            if committed:
                await db.commit()
    except Exception as exc:
        logger.debug("On-demand PSX backfill skipped for %s: %s", asset.symbol, exc)
        await db.rollback()


class PsxIntradayScraper(BaseScraper):
    """
    Polls PSX DPS /market-watch, /symbols, and /indices in batch requests,
    updating all listed symbol quotes, full company names, sectors, KSE100/KMI30/Shariah flags,
    and 5M / 1D bars without per-symbol polling.
    """

    name = "psx_intraday"

    async def run(self, db: AsyncSession) -> int:
        index_rows: list[dict[str, Any]] = []
        symbol_meta: dict[str, dict[str, Any]] = {}

        async with CurlAsyncSession(impersonate="chrome", timeout=25) as session:
            ajax_headers = await _get_psx_ajax_headers(session)

            try:
                sym_resp = await session.get(f"{PSX_BASE_URL}/symbols", headers=ajax_headers)
                if sym_resp.status_code == 200:
                    raw_syms = sym_resp.json()
                    if isinstance(raw_syms, list):
                        symbol_meta = {
                            str(item.get("symbol", "")).strip().upper(): item
                            for item in raw_syms
                            if isinstance(item, dict) and item.get("symbol")
                        }
            except Exception as exc:
                logger.debug("PSX /symbols metadata fetch skipped: %s", exc)

            resp = await session.get(f"{PSX_BASE_URL}/market-watch", headers=ajax_headers)
            if resp.status_code != 200:
                raise ScraperParseError(f"PSX market-watch HTTP {resp.status_code}", raw_payload=resp.text)
            parsed = parse_market_watch_html(resp.text, symbol_meta=symbol_meta)

            try:
                idx_resp = await session.get(f"{PSX_BASE_URL}/indices", headers=HEADERS)
                if idx_resp.status_code == 200:
                    index_rows = parse_psx_indices_html(idx_resp.text)
            except Exception:
                index_rows = []

        if not parsed:
            raise ScraperParseError("PSX market-watch returned 0 parseable rows", raw_payload=resp.text)

        existing_assets = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF", "PSX_INDEX"]),
                    )
                )
            ).scalars()
        }
        existing_profiles = {
            p.asset_id: p
            for p in (
                await db.execute(
                    select(PsxProfile).where(
                        PsxProfile.asset_id.in_([a.id for a in existing_assets.values()])
                    )
                )
            ).scalars()
        } if existing_assets else {}

        now = datetime.now(timezone.utc)
        bucket_minute = (now.minute // 5) * 5
        bar_bucket = now.replace(minute=bucket_minute, second=0, microsecond=0)
        existing_5m_bars = {
            b.asset_id: b
            for b in (
                await db.execute(
                    select(PriceBar).where(
                        PriceBar.timeframe == "5M",
                        PriceBar.bar_time == bar_bucket,
                    )
                )
            ).scalars()
        }
        upserted = 0

        for item in parsed:
            sym = item["symbol"]
            asset = existing_assets.get(sym)
            if asset is None:
                asset = Asset(
                    symbol=sym,
                    name=item.get("name") or sym,
                    asset_class=item.get("asset_class", "PSX_EQUITY"),
                    currency="PKR",
                    sector_or_category=item["sector"][:120],
                    is_shariah_compliant=bool(item.get("is_shariah", False)),
                    source="psx_dps",
                )
                db.add(asset)
                await db.flush()
                existing_assets[sym] = asset
            else:
                if item.get("name") and (asset.name == asset.symbol or asset.source in {"seed", "reference_catalog"}):
                    asset.name = item["name"]
                if item.get("sector") and not item["sector"].isdigit():
                    asset.sector_or_category = item["sector"][:120]
                if "is_shariah" in item:
                    asset.is_shariah_compliant = bool(item["is_shariah"])

            asset.last_price = item["last_price"]
            asset.prev_close = item["prev_close"]
            asset.change_pct = item["change_pct"]
            asset.volume = item["volume"]
            asset.source = "psx_dps"
            asset.updated_at = now

            prof = existing_profiles.get(asset.id)
            if prof is None:
                prof = PsxProfile(
                    asset_id=asset.id,
                    is_kse100=bool(item.get("is_kse100", False)),
                    is_kmi30=bool(item.get("is_kmi30", False)),
                )
                db.add(prof)
                existing_profiles[asset.id] = prof
            else:
                prof.is_kse100 = bool(item.get("is_kse100", prof.is_kse100))
                prof.is_kmi30 = bool(item.get("is_kmi30", prof.is_kmi30))

            existing_bar = existing_5m_bars.get(asset.id)
            if existing_bar is None:
                new_bar = PriceBar(
                    asset_id=asset.id,
                    timeframe="5M",
                    bar_time=bar_bucket,
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["last_price"],
                    volume=item["volume"],
                )
                db.add(new_bar)
                existing_5m_bars[asset.id] = new_bar
            else:
                existing_bar.high = max(existing_bar.high, item["last_price"])
                existing_bar.low = min(existing_bar.low, item["last_price"])
                existing_bar.close = item["last_price"]
                existing_bar.volume = item["volume"]

            upserted += 1
            await cache_quote(
                sym,
                {
                    "symbol": sym,
                    "last_price": str(item["last_price"]),
                    "prev_close": str(item["prev_close"]),
                    "change_pct": str(item["change_pct"]),
                    "volume": str(item["volume"]),
                    "updated_at": now.isoformat(),
                },
            )

        for idx_item in index_rows:
            sym = idx_item["symbol"]
            asset = existing_assets.get(sym)
            if asset is None:
                asset = Asset(
                    symbol=sym,
                    name=f"PSX {sym} Index",
                    asset_class="PSX_INDEX",
                    currency="PKR",
                    sector_or_category="Islamic Index" if "KMI" in sym else "Index",
                    is_shariah_compliant="KMI" in sym,
                    source="psx_dps",
                )
                db.add(asset)
                await db.flush()
                existing_assets[sym] = asset

            asset.last_price = idx_item["last_price"]
            asset.prev_close = idx_item["prev_close"]
            asset.change_pct = idx_item["change_pct"]
            if idx_item["volume"] > 0:
                asset.volume = idx_item["volume"]
            asset.source = "psx_dps"
            asset.updated_at = now
            await cache_quote(
                sym,
                {
                    "symbol": sym,
                    "last_price": str(idx_item["last_price"]),
                    "prev_close": str(idx_item["prev_close"]),
                    "change_pct": str(idx_item["change_pct"]),
                    "updated_at": now.isoformat(),
                },
            )
            upserted += 1

        await db.commit()
        return upserted


class PsxEodScraper(BaseScraper):
    """Fetches daily EOD OHLCV history from PSX DPS for priority PSX indices, held/watched equities, and top volume stocks."""

    name = "psx_eod_history"

    async def run(self, db: AsyncSession) -> int:
        held_ids = set((await db.execute(select(Transaction.asset_id).distinct())).scalars())
        watched_ids = set((await db.execute(select(WatchlistItem.asset_id).distinct())).scalars())
        priority_ids = held_ids | watched_ids

        all_assets = list(
            (
                await db.execute(
                    select(Asset)
                    .where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["PSX_INDEX", "PSX_EQUITY", "PSX_ETF"]),
                    )
                    .order_by(Asset.volume.desc(), Asset.symbol.asc())
                )
            ).scalars()
        )
        if not all_assets:
            return 0

        # Query existing 1D bar counts so un-backfilled tickers are prioritized across batches until all ~497 PSX tickers have 1-5Y history
        bar_counts = {
            row[0]: int(row[1])
            for row in (
                await db.execute(
                    select(PriceBar.asset_id, func.count(PriceBar.id))
                    .where(PriceBar.timeframe == "1D")
                    .group_by(PriceBar.asset_id)
                )
            ).all()
        }

        selected: list[Asset] = [
            a for a in all_assets if a.asset_class == "PSX_INDEX" or a.id in priority_ids
        ]
        selected_ids = {a.id for a in selected}
        # First fill up to batch size with equities that have < 30 historical bars, then top up with remaining active stocks
        unbackfilled = [a for a in all_assets if a.id not in selected_ids and bar_counts.get(a.id, 0) < 30]
        remainder = [a for a in all_assets if a.id not in selected_ids and bar_counts.get(a.id, 0) >= 30]
        for a in unbackfilled + remainder:
            if len(selected) >= 140:
                break
            if a.id not in selected_ids:
                selected.append(a)
                selected_ids.add(a.id)

        semaphore = asyncio.Semaphore(8)

        async with CurlAsyncSession(impersonate="chrome", timeout=20) as session:
            ajax_headers = await _get_psx_ajax_headers(session)

            async def _fetch_one(asset: Asset) -> tuple[Asset, list[dict[str, Any]]]:
                async with semaphore:
                    try:
                        resp = await session.get(
                            f"{PSX_BASE_URL}/timeseries/eod/{asset.symbol}",
                            headers=ajax_headers,
                        )
                        if resp.status_code != 200:
                            return asset, []
                        bars_parsed = parse_timeseries_json(resp.json())
                        need_full = (
                            (asset.asset_class == "PSX_INDEX" and bar_counts.get(asset.id, 0) < 1000)
                            or bar_counts.get(asset.id, 0) < 200
                        )
                        keep_n = 1250 if need_full else 30
                        return asset, bars_parsed[-keep_n:]
                    except Exception:
                        return asset, []

            fetched = await asyncio.gather(*[_fetch_one(a) for a in selected])

        upserted = 0
        for asset, recent_bars in fetched:
            if not recent_bars:
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
            for bar in recent_bars:
                if bar["bar_time"].date() in existing_dates:
                    continue
                existing_dates.add(bar["bar_time"].date())
                db.add(
                    PriceBar(
                        asset_id=asset.id,
                        timeframe="1D",
                        bar_time=bar["bar_time"],
                        open=bar["open"],
                        high=bar["high"],
                        low=bar["low"],
                        close=bar["close"],
                        volume=bar["volume"],
                    )
                )
                upserted += 1

        await db.commit()
        return upserted


class PsxFundamentalsAndPucarsScraper(BaseScraper):
    """
    Fetches PSX company fundamentals (/company/{SYM}), Corporate Actions (/company/payouts),
    Screener metrics (/screener), and PUCARS Corporate Announcements (/announcements/companies).
    """

    name = "psx_fundamentals"

    async def run(self, db: AsyncSession) -> int:
        held_ids = set((await db.execute(select(Transaction.asset_id).distinct())).scalars())
        watched_ids = set((await db.execute(select(WatchlistItem.asset_id).distinct())).scalars())
        priority_ids = held_ids | watched_ids

        all_psx = list(
            (
                await db.execute(
                    select(Asset)
                    .where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF"]),
                    )
                    .order_by(Asset.volume.desc(), Asset.symbol.asc())
                )
            ).scalars()
        )
        if not all_psx:
            return 0

        asset_by_sym = {a.symbol.upper(): a for a in all_psx}
        existing_profs = {
            p.asset_id: p
            for p in (await db.execute(select(PsxProfile))).scalars()
        }

        def _psx_prio_key(a: Asset) -> tuple[int, int, float]:
            p = existing_profs.get(a.id)
            is_bluechip = 1 if (p and (p.is_kmi30 or p.is_kse100)) else 0
            mcap = float(p.market_cap_mn) if (p and p.market_cap_mn) else 0.0
            return (1 if a.id in priority_ids else 0, is_bluechip, mcap)

        all_psx_ranked = sorted(all_psx, key=_psx_prio_key, reverse=True)
        selected_assets = [a for a in all_psx_ranked if a.id in priority_ids]
        selected_ids = {a.id for a in selected_assets}
        missing_prof = [
            a for a in all_psx_ranked
            if a.id not in selected_ids
            and (
                a.id not in existing_profs
                or not existing_profs[a.id].financial_summary_json
                or len(existing_profs[a.id].financial_summary_json) == 0
            )
        ]
        other_psx = [a for a in all_psx_ranked if a.id not in selected_ids and a not in missing_prof]
        for a in missing_prof + other_psx:
            if len(selected_assets) >= 120:
                break
            if a.id not in selected_ids:
                selected_assets.append(a)
                selected_ids.add(a.id)

        semaphore = asyncio.Semaphore(6)
        announcement_rows: list[dict[str, Any]] = []
        screener_metrics: dict[str, dict[str, Any]] = {}

        async with CurlAsyncSession(impersonate="chrome", timeout=20) as session:
            ajax_headers = await _get_psx_ajax_headers(session)

            # 1. Batch fetch /screener to enrich Market Cap, P/E, Dividend Yield, Free Float, and KSE100/KMI30/Shariah across all PSX stocks
            try:
                scr_resp = await session.get(f"{PSX_BASE_URL}/screener", headers=HEADERS)
                if scr_resp.status_code == 200:
                    soup_scr = BeautifulSoup(scr_resp.text, "lxml")
                    for tr in soup_scr.select("table tbody tr"):
                        cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                        if len(cols) >= 10:
                            sym_s = cols[0].split()[0].strip().upper()
                            listed_s = {t.strip() for t in cols[2].upper().split(",") if t.strip()}
                            screener_metrics[sym_s] = {
                                "market_cap_mn": _to_decimal(cols[3]),
                                "pe_ratio": _to_decimal(cols[7]) if _to_decimal(cols[7]) > 0 else None,
                                "dividend_yield_pct": _to_decimal(cols[8]),
                                "is_kse100": "KSE100" in listed_s,
                                "is_kmi30": "KMI30" in listed_s,
                                "is_shariah": bool(listed_s & {"KMI30", "KMIALLSHR", "MII30"}),
                            }
            except Exception as exc:
                logger.debug("PSX /screener batch fetch skipped: %s", exc)

            # 2. Fetch PUCARS announcements via PSX AJAX endpoint (with GET fallback)
            try:
                ann_resp = await session.post(
                    f"{PSX_BASE_URL}/announcements",
                    data={
                        "type": "C",
                        "symbol": "",
                        "query": "",
                        "count": "50",
                        "offset": "0",
                        "date_from": "",
                        "date_to": "",
                        "page": "companies",
                    },
                    headers=ajax_headers,
                )
                if ann_resp.status_code == 200:
                    announcement_rows = parse_psx_announcements_html(ann_resp.text)
                if not announcement_rows:
                    ann_get = await session.get(f"{PSX_BASE_URL}/announcements/companies", headers=HEADERS)
                    if ann_get.status_code == 200:
                        announcement_rows = parse_psx_announcements_html(ann_get.text)
            except Exception:
                announcement_rows = []

            async def _fetch_company(asset: Asset) -> tuple[Asset, dict[str, Any] | None]:
                async with semaphore:
                    try:
                        resp = await session.get(f"{PSX_BASE_URL}/company/{asset.symbol}", headers=HEADERS)
                        if resp.status_code != 200:
                            return asset, None
                        pay_html: str | None = None
                        try:
                            pr = await session.post(
                                f"{PSX_BASE_URL}/company/payouts",
                                data={"symbol": asset.symbol},
                                headers=ajax_headers,
                            )
                            if pr.status_code == 200:
                                pay_html = pr.text
                        except Exception:
                            pay_html = None
                        return asset, parse_psx_company_fundamentals_html(resp.text, asset.symbol, payouts_html=pay_html)
                    except Exception:
                        return asset, None

            company_results = await asyncio.gather(*[_fetch_company(a) for a in selected_assets])

        existing_profiles = {
            p.asset_id: p
            for p in (
                await db.execute(select(PsxProfile).where(PsxProfile.asset_id.in_([a.id for a in all_psx])))
            ).scalars()
        }
        existing_shariah = {
            s.asset_id: s
            for s in (
                await db.execute(select(ShariahMetric).where(ShariahMetric.asset_id.in_([a.id for a in all_psx])))
            ).scalars()
        }

        upserted = 0
        # Apply batch screener metrics across all PSX assets
        for sym_s, sm in screener_metrics.items():
            asset = asset_by_sym.get(sym_s)
            if asset is None:
                continue
            asset.is_shariah_compliant = sm["is_shariah"]
            prof = existing_profiles.get(asset.id)
            if prof is None:
                prof = PsxProfile(asset_id=asset.id)
                db.add(prof)
                existing_profiles[asset.id] = prof
            if sm["market_cap_mn"] > 0:
                prof.market_cap_mn = sm["market_cap_mn"]
            if sm["pe_ratio"] is not None:
                prof.pe_ratio = sm["pe_ratio"]
            if sm["dividend_yield_pct"] > 0:
                prof.dividend_yield_pct = sm["dividend_yield_pct"]
            prof.is_kse100 = sm["is_kse100"]
            prof.is_kmi30 = sm["is_kmi30"]

            sh_metric = existing_shariah.get(asset.id)
            if sh_metric is None:
                sh_metric = ShariahMetric(
                    asset_id=asset.id,
                    fiscal_period="KMI-AllShare",
                    core_business_compliant=sm["is_shariah"],
                    is_kmi_all_share=sm["is_shariah"],
                    debt_to_assets_pct=Decimal("15.0") if sm["is_shariah"] else Decimal("45.0"),
                    non_compliant_inv_pct=Decimal("5.0") if sm["is_shariah"] else Decimal("35.0"),
                    non_compliant_income_pct=Decimal("1.5") if sm["is_shariah"] else Decimal("15.0"),
                    purification_rate_pct=Decimal("1.5") if sm["is_shariah"] else Decimal("0.0"),
                    illiquid_assets_pct=Decimal("60.0"),
                    passed=sm["is_shariah"],
                )
                db.add(sh_metric)
                existing_shariah[asset.id] = sh_metric

        selected_ids_list = [a.id for a, p in company_results if p is not None]
        actions_by_asset: dict[str, set[tuple[str, date]]] = {aid: set() for aid in selected_ids_list}
        if selected_ids_list:
            for ca in (
                await db.execute(select(CorporateAction).where(CorporateAction.asset_id.in_(selected_ids_list)))
            ).scalars():
                actions_by_asset.setdefault(ca.asset_id, set()).add((ca.action_type, ca.ex_date))

        now = datetime.now(timezone.utc)
        for asset, parsed in company_results:
            if parsed is None:
                continue
            prof = existing_profiles.get(asset.id)
            prof, delta = await _apply_parsed_company_fundamentals(
                db,
                asset,
                prof,
                parsed,
                existing_actions=actions_by_asset.get(asset.id, set()),
                now=now,
            )
            existing_profiles[asset.id] = prof
            upserted += delta

        upserted += await _upsert_announcements(db, asset_by_sym, announcement_rows)

        await db.commit()
        return upserted
