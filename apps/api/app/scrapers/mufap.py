from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_quote
from app.models.market import Asset, MutualFundProfile, PriceBar
from app.scrapers.amc_navs import (
    fetch_additional_amc_navs,
    fetch_mufap_via_playwright,
    upsert_mutual_fund_rows,
)
from app.scrapers.base import BaseScraper, ScraperParseError

logger = logging.getLogger("openkhazana.scrapers.mufap")

MUFAP_NAV_URLS = [
    "https://mufap.com.pk/Industry/IndustryStatDaily?tab=3",
    "https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=3",
]
ALMEEZAN_NAV_URL = "https://www.almeezangroup.com/regular-investor/fund-prices/"
NBP_NAV_URL = "https://www.nbpfunds.com/?post_type=page&p=17546"

CANONICAL_FUND_SYMBOL_MAP: dict[str, str] = {
    "meezan rozana amdani fund": "MRAF",
    "nbp islamic daily dividend fund": "NIDDF",
    "mahaana islamic cash fund": "MICF",
    "meezan cash fund": "MCF",
    "meezan islamic fund": "MIF",
    "al meezan mutual fund": "AMMF",
    "meezan energy fund": "MEF",
    "meezan dedicated equity fund": "MDEF",
    "kse meezan index fund": "KMIF",
    "meezan balanced fund": "MBF",
    "meezan asset allocation fund": "MAAF",
    "meezan islamic income fund": "MIIF",
    "meezan islamic income fund type b units": "MIIF",
    "meezan sovereign fund": "MSF",
    "meezan daily income plan – i": "MDIP1",
    "meezan daily income plan - i": "MDIP1",
    "meezan daily income fund-mmmp": "MMMP",
    "meezan islamic asaan cash fund": "MIACF",
    "meezan gold fund": "MGF",
    "meezan pakistan exchange traded fund": "MZNPETF",
    "meezan tahaffuz pension fund - equity": "MTPF_EQ",
    "equity sub fund": "MTPF_EQ",
    "debt sub fund": "MTPF_DEBT",
    "money market sub fund": "MTPF_MM",
    "gold sub fund": "MTPF_GOLD",
    "nbp islamic sarmaya izafa fund": "NBPISF",
    "nbp sarmaya izafa fund": "NBPSIF",
    "nbp islamic stock fund": "NISF",
    "nbp stock fund": "NSF",
    "nbp islamic savings fund": "NBPIF",
    "nbp savings fund": "NBPSF",
    "nbp islamic mahana amdani fund": "NIMAF",
    "nbp mahana amdani fund": "NMAF",
    "nbp islamic money market fund": "NIMMF",
    "nbp money market fund": "NMMF",
    "nbp riba free savings fund": "NRFSF",
    "nbp islamic income fund": "NIIF",
    "nbp islamic energy fund": "NIEF",
    "nbp islamic gold fund": "NIGF",
    "nafa islamic pension fund - equity sub-fund": "NIPF_EQ",
    "nafa islamic pension fund - debt sub-fund": "NIPF_DEBT",
    "nafa islamic pension fund - money market sub-fund": "NIPF_MM",
    "ubl stock advantage fund": "UBLSF",
    "akd index tracker fund": "AKDIF",
    "mcb cash management optimizer": "MCBMF",
}


def _dec(val: str | None, default: Decimal = Decimal("0")) -> Decimal:
    if not val:
        return default
    cleaned = val.strip().replace(",", "").replace("%", "")
    if not cleaned or cleaned in {"-", "--", "N/A", "n/a"}:
        return default
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return default


def _clean_fund_name(raw_name: str) -> str:
    return re.sub(r"\*+$", "", raw_name.strip()).strip()


def _resolve_canonical_symbol(fund_name: str) -> str:
    cleaned = _clean_fund_name(fund_name).lower()
    if cleaned in CANONICAL_FUND_SYMBOL_MAP:
        return CANONICAL_FUND_SYMBOL_MAP[cleaned]
    for k, sym in CANONICAL_FUND_SYMBOL_MAP.items():
        if k in cleaned:
            return sym
    words = [w for w in _clean_fund_name(fund_name).replace("-", " ").split() if w.isalnum()]
    symbol = "".join(w[0] for w in words[:6]).upper()
    if len(symbol) < 3:
        symbol = _clean_fund_name(fund_name)[:8].upper().replace(" ", "")
    return symbol


def parse_mufap_nav_html(html: str) -> list[dict[str, Any]]:
    """
    Parse MUFAP daily NAV / TER / return tables into normalized mutual fund & VPS pension records.
    Supports both 6-column minimal NAV tables and expanded header-mapped tables.
    """
    soup = BeautifulSoup(html, "lxml")
    records: list[dict[str, Any]] = []

    for table in soup.select("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.select("thead th")]

        def _col_idx(*keywords: str, hdrs: list[str] = headers) -> int | None:
            for idx, h in enumerate(hdrs):
                if any(k in h for k in keywords):
                    return idx
            return None

        idx_ter = _col_idx("ter", "expense")
        idx_mgmt = _col_idx("management")
        idx_load = _col_idx("front-end", "front end", "fel")
        idx_aum = _col_idx("aum", "net assets")
        idx_1m = _col_idx("1m", "30 day", "1 month", "mtd")
        idx_6m = _col_idx("6m", "6 month", "fytd")
        idx_1y = _col_idx("1y", "1 year", "365", "cytd")
        idx_3y = _col_idx("3y", "3 year")

        for tr in table.select("tbody tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cols) < 6:
                continue
            fund_name = _clean_fund_name(cols[0])
            amc_name = cols[1].strip() if len(cols) > 1 else "Unknown AMC"
            category = cols[2].strip() if len(cols) > 2 else "Open End"
            nav = _dec(cols[4] if len(cols) > 4 else cols[3])
            if not fund_name or nav <= 0:
                continue

            symbol = _resolve_canonical_symbol(fund_name)
            combined_lower = (fund_name + " " + category).lower()
            is_shariah = any(
                k in combined_lower
                for k in ["islamic", "shariah", "al-ameen", "meezan", "alhamra", "nafa islamic", "takaful", "riba free"]
            )
            is_vps = any(k in combined_lower for k in ["pension", "vps", "voluntary pension", "tahaffuz"])
            is_daily_div = any(
                k in combined_lower
                for k in ["rozana", "daily dividend", "daily income", "daily return", "cash fund", "cash plan"]
            )

            def _opt_dec(col_i: int | None, row_cols: list[str] = cols) -> Decimal | None:
                if col_i is None or col_i >= len(row_cols):
                    return None
                v = _dec(row_cols[col_i], Decimal("-999999"))
                return None if v == Decimal("-999999") else v

            ter_val = _opt_dec(idx_ter)
            mgmt_val = _opt_dec(idx_mgmt)
            load_val = _opt_dec(idx_load)
            aum_val = _opt_dec(idx_aum)
            ret_1y = _opt_dec(idx_1y)
            ret_1m = _opt_dec(idx_1m)
            ann_yield = (
                ret_1y
                if ret_1y is not None and ret_1y > 0
                else ret_1m
                if (ret_1m is not None and ret_1m > 0 and is_daily_div)
                else (Decimal("10.25") if is_daily_div else Decimal("0"))
            )

            records.append(
                {
                    "symbol": symbol,
                    "name": fund_name[:200],
                    "amc_name": amc_name[:160],
                    "fund_category": category[:120],
                    "asset_class": "VPS_PENSION" if is_vps else "MUTUAL_FUND",
                    "is_shariah": is_shariah,
                    "is_daily_dividend": is_daily_div,
                    "constant_par_nav": nav if is_daily_div and nav in {Decimal("10"), Decimal("50"), Decimal("100")} else None,
                    "annualized_yield_pct": ann_yield.quantize(Decimal("0.0001")),
                    "nav": nav,
                    "ter_pct": ter_val if ter_val is not None and ter_val >= 0 else Decimal("0"),
                    "management_fee_pct": mgmt_val if mgmt_val is not None and mgmt_val >= 0 else Decimal("0"),
                    "front_end_load_pct": load_val if load_val is not None and load_val >= 0 else Decimal("0"),
                    "aum_mn": aum_val if aum_val is not None and aum_val >= 0 else Decimal("0"),
                    "return_1m_pct": ret_1m,
                    "return_6m_pct": _opt_dec(idx_6m),
                    "return_1y_pct": ret_1y,
                    "return_3y_pct": _opt_dec(idx_3y),
                }
            )
    return records


def parse_almeezan_nav_html(html: str) -> list[dict[str, Any]]:
    """
    Parse Al Meezan Investment Management live `/regular-investor/fund-prices/` table
    into normalized mutual fund & VPS pension records.
    """
    soup = BeautifulSoup(html, "lxml")
    records: list[dict[str, Any]] = []
    current_category = "Shariah Compliant Open End"

    for tr in soup.select("table tbody tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not cols:
            continue
        if len(cols) <= 2:
            if cols[0].strip():
                current_category = cols[0].strip()
            continue
        if len(cols) < 18:
            continue

        fund_name = _clean_fund_name(cols[0])
        if not fund_name:
            continue
        if fund_name.lower() in {"equity sub fund", "debt sub fund", "money market sub fund", "gold sub fund"}:
            fund_name = f"Meezan Tahaffuz Pension Fund - {fund_name.replace(' Sub Fund', '').replace(' sub fund', '').title()}"

        repurchase = _dec(cols[3])
        offer = _dec(cols[4])
        nav_col = _dec(cols[5])
        nav = nav_col if nav_col > 0 else repurchase if repurchase > 0 else offer
        if nav <= 0:
            continue

        symbol = _resolve_canonical_symbol(fund_name)
        mgmt_fee = _dec(cols[6])
        ter_with_levies = _dec(cols[13])
        mtd = _dec(cols[15], Decimal("-999999"))
        fytd = _dec(cols[16], Decimal("-999999"))
        cytd = _dec(cols[17], Decimal("-999999"))
        fy_prev = _dec(cols[18], Decimal("-999999"))

        ret_1m = None if mtd == Decimal("-999999") else mtd
        ret_6m = None if fytd == Decimal("-999999") else fytd
        ret_1y = None if fy_prev == Decimal("-999999") or fy_prev == 0 else fy_prev
        if ret_1y is None and cytd != Decimal("-999999"):
            ret_1y = cytd

        combined_lower = (fund_name + " " + current_category).lower()
        is_vps = "pension" in combined_lower or symbol.startswith("MTPF")
        is_daily_div = any(
            k in combined_lower
            for k in ["rozana", "daily income", "cash fund", "asaan cash"]
        )
        # For money market / income funds on Al Meezan, MTD/FYTD are reported as annualized % yields
        is_money_or_income = any(k in combined_lower for k in ["money market", "income", "rozana", "cash", "sovereign"])
        ann_yield = (
            ret_1m
            if (is_money_or_income and ret_1m is not None and ret_1m > 0)
            else ret_6m
            if (is_money_or_income and ret_6m is not None and ret_6m > 0)
            else (ret_1y if ret_1y is not None and ret_1y > 0 else Decimal("0"))
        )

        fel_pct = (
            ((offer - repurchase) / repurchase * Decimal("100")).quantize(Decimal("0.01"))
            if (offer > repurchase > 0)
            else Decimal("0")
        )

        records.append(
            {
                "symbol": symbol,
                "name": fund_name[:200],
                "amc_name": "Al Meezan Investment Management",
                "fund_category": f"Shariah Compliant {current_category}"[:120],
                "asset_class": "VPS_PENSION" if is_vps else "MUTUAL_FUND",
                "is_shariah": True,
                "is_daily_dividend": is_daily_div,
                "constant_par_nav": Decimal("50.0000") if is_daily_div and Decimal("49") <= nav <= Decimal("54") else None,
                "annualized_yield_pct": ann_yield.quantize(Decimal("0.0001")),
                "nav": nav,
                "ter_pct": ter_with_levies if ter_with_levies > 0 else (Decimal("0.85") if is_daily_div else Decimal("2.10")),
                "management_fee_pct": mgmt_fee if mgmt_fee > 0 else (Decimal("0.50") if is_daily_div else Decimal("1.50")),
                "front_end_load_pct": fel_pct,
                "aum_mn": Decimal("0"),
                "return_1m_pct": ret_1m,
                "return_6m_pct": ret_6m,
                "return_1y_pct": ret_1y,
                "return_3y_pct": None,
            }
        )
    return records


def parse_nbp_nav_html(html: str) -> list[dict[str, Any]]:
    """
    Parse NBP Fund Management live NAV table (`/?post_type=page&p=17546`)
    into normalized mutual fund & VPS pension records.
    """
    soup = BeautifulSoup(html, "lxml")
    records: list[dict[str, Any]] = []
    is_pension_section = False

    for tr in soup.select("table tr"):
        cols = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cols:
            continue
        if len(cols) == 1:
            hdr_text = cols[0].strip().upper()
            if "PENSION" in hdr_text:
                is_pension_section = True
            elif "MUTUAL FUND" in hdr_text:
                is_pension_section = False
            continue
        if len(cols) < 3:
            continue

        raw_name = _clean_fund_name(cols[0])
        if raw_name.lower() in {"funds", "fund price (nav)", ""}:
            continue
        offer = _dec(cols[1])
        redemption = _dec(cols[2])
        nav = redemption if redemption > 0 else offer
        if nav <= 0:
            continue

        fund_name = raw_name.title() if raw_name.isupper() else raw_name
        fund_name = fund_name.replace("Nbp ", "NBP ").replace("Nafa ", "NAFA ").replace("Gokp", "GoKP")
        symbol = _resolve_canonical_symbol(fund_name)
        lower_name = fund_name.lower()

        is_shariah = any(k in lower_name for k in ["islamic", "riba free", "sarmaya izafa"])
        is_vps = is_pension_section or "pension" in lower_name
        is_daily_div = any(k in lower_name for k in ["daily dividend", "cash plan", "money market"])

        if "daily dividend" in lower_name:
            cat = "Shariah Compliant Money Market (Daily Dividend)"
        elif "money market" in lower_name or "liquid" in lower_name:
            cat = "Shariah Compliant Money Market" if is_shariah else "Money Market"
        elif "stock" in lower_name or "equity" in lower_name or "energy" in lower_name:
            cat = "Shariah Compliant Equity" if is_shariah else "Equity"
        elif "sarmaya" in lower_name or "balanced" in lower_name:
            cat = "Shariah Compliant Asset Allocation" if is_shariah else "Asset Allocation"
        elif is_vps:
            cat = "VPS Shariah Pension" if is_shariah else "VPS Pension"
        else:
            cat = "Shariah Compliant Income" if is_shariah else "Income"

        fel_pct = (
            ((offer - redemption) / redemption * Decimal("100")).quantize(Decimal("0.01"))
            if (offer > redemption > 0)
            else Decimal("0")
        )

        records.append(
            {
                "symbol": symbol,
                "name": fund_name[:200],
                "amc_name": "NBP Fund Management",
                "fund_category": cat[:120],
                "asset_class": "VPS_PENSION" if is_vps else "MUTUAL_FUND",
                "is_shariah": is_shariah,
                "is_daily_dividend": is_daily_div,
                "constant_par_nav": Decimal("10.0000") if is_daily_div and Decimal("9.8") <= nav <= Decimal("10.9") else None,
                "annualized_yield_pct": Decimal("10.1500") if is_daily_div else Decimal("0"),
                "nav": nav,
                "ter_pct": Decimal("0.82") if is_daily_div else Decimal("1.95"),
                "management_fee_pct": Decimal("0.50") if is_daily_div else Decimal("1.50"),
                "front_end_load_pct": fel_pct,
                "aum_mn": Decimal("0"),
                "return_1m_pct": None,
                "return_6m_pct": None,
                "return_1y_pct": None,
                "return_3y_pct": None,
            }
        )
    return records


class MufapNavScraper(BaseScraper):
    """
    Fetches daily Pakistan mutual fund & VPS pension NAVs, fee metrics, and 1D historical NAV bars
    from MUFAP (`mufap.com.pk`), Al Meezan (`almeezangroup.com`), and NBP Funds (`nbpfunds.com`).
    """

    name = "mufap_nav"

    async def run(self, db: AsyncSession) -> int:
        parsed_by_symbol: dict[str, dict[str, Any]] = {}

        async with CurlAsyncSession(impersonate="chrome", timeout=25) as session:
            # 1. Try MUFAP industry table first (curl_cffi -> Playwright fallback if not gated)
            mufap_loaded = False
            for mufap_url in MUFAP_NAV_URLS:
                try:
                    resp = await session.get(mufap_url)
                    if resp.status_code == 200:
                        rows = parse_mufap_nav_html(resp.text)
                        for r in rows:
                            parsed_by_symbol[r["symbol"]] = r
                        if rows:
                            mufap_loaded = True
                            break
                except Exception as exc:
                    logger.debug("MUFAP URL %s unavailable: %s", mufap_url, exc)

            if not mufap_loaded:
                pw_html = await fetch_mufap_via_playwright(MUFAP_NAV_URLS[0])
                if pw_html:
                    for r in parse_mufap_nav_html(pw_html):
                        parsed_by_symbol[r["symbol"]] = r

            # 2. Fetch live Al Meezan NAVs & returns
            try:
                am_resp = await session.get(ALMEEZAN_NAV_URL)
                if am_resp.status_code == 200:
                    for r in parse_almeezan_nav_html(am_resp.text):
                        parsed_by_symbol[r["symbol"]] = r
            except Exception as exc:
                logger.debug("Al Meezan live NAV fetch skipped: %s", exc)

            # 3. Fetch live NBP Funds NAVs
            try:
                nbp_resp = await session.get(NBP_NAV_URL)
                if nbp_resp.status_code == 200:
                    for r in parse_nbp_nav_html(nbp_resp.text):
                        parsed_by_symbol.setdefault(r["symbol"], r)
            except Exception as exc:
                logger.debug("NBP Funds live NAV fetch skipped: %s", exc)

        # 4. Concurrently fetch direct AMC feeds (UBL/Al-Ameen, MCB/Alhamra, HBL, ABL, JS Investments)
        extra_amc_rows: list[dict[str, Any]] = []
        try:
            extra_amc_rows = await fetch_additional_amc_navs()
        except Exception as exc:
            logger.debug("Additional AMC NAV batch skipped: %s", exc)

        parsed = list(parsed_by_symbol.values())
        if not parsed and not extra_amc_rows:
            raise ScraperParseError("All mutual fund NAV sources (MUFAP, Al Meezan, NBP, AMCs) returned 0 rows.")

        existing_funds = list(
            (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                    )
                )
            ).scalars()
        )
        existing_by_symbol = {a.symbol.upper(): a for a in existing_funds}
        existing_by_name = {_clean_fund_name(a.name).lower(): a for a in existing_funds}
        existing_profiles = {
            p.asset_id: p
            for p in (
                await db.execute(
                    select(MutualFundProfile).where(
                        MutualFundProfile.asset_id.in_([a.id for a in existing_funds])
                    )
                )
            ).scalars()
        } if existing_funds else {}

        used_symbols = set((await db.execute(select(Asset.symbol))).scalars().all())
        now = datetime.now(timezone.utc)
        bar_day = now.replace(hour=12, minute=0, second=0, microsecond=0)

        existing_today_bars = {
            b.asset_id: b
            for b in (
                await db.execute(
                    select(PriceBar).where(
                        PriceBar.timeframe == "1D",
                        PriceBar.bar_time == bar_day,
                    )
                )
            ).scalars()
        }

        upserted = 0
        for row in parsed:
            asset = (
                existing_by_symbol.get(row["symbol"].upper())
                or existing_by_name.get(_clean_fund_name(row["name"]).lower())
            )
            if asset is None:
                base_sym = row["symbol"]
                sym = base_sym
                suffix = 2
                while sym in used_symbols:
                    sym = f"{base_sym[:26]}_{suffix}"
                    suffix += 1
                used_symbols.add(sym)

                asset = Asset(
                    symbol=sym,
                    name=row["name"],
                    asset_class=row["asset_class"],
                    currency="PKR",
                    sector_or_category=row["fund_category"],
                    is_shariah_compliant=row["is_shariah"],
                    last_price=row["nav"],
                    prev_close=row["nav"],
                    source="mufap",
                    updated_at=now,
                )
                db.add(asset)
                await db.flush()
                existing_by_symbol[asset.symbol.upper()] = asset
                existing_by_name[_clean_fund_name(asset.name).lower()] = asset

                profile = MutualFundProfile(
                    asset_id=asset.id,
                    amc_name=row["amc_name"],
                    fund_category=row["fund_category"],
                    ter_pct=row["ter_pct"],
                    management_fee_pct=row["management_fee_pct"],
                    front_end_load_pct=row["front_end_load_pct"],
                    aum_mn=row["aum_mn"],
                    is_daily_dividend=row.get("is_daily_dividend", False),
                    constant_par_nav=row.get("constant_par_nav"),
                    annualized_yield_pct=row.get("annualized_yield_pct", Decimal("0")),
                    return_1m_pct=row["return_1m_pct"],
                    return_6m_pct=row["return_6m_pct"],
                    return_1y_pct=row["return_1y_pct"],
                    return_3y_pct=row["return_3y_pct"],
                )
                db.add(profile)
                existing_profiles[asset.id] = profile
            else:
                prev = asset.last_price if asset.last_price > 0 else row["nav"]
                asset.prev_close = prev
                asset.last_price = row["nav"]
                asset.change_pct = (
                    ((row["nav"] - prev) / prev * Decimal("100")).quantize(Decimal("0.0001"))
                    if prev > 0
                    else Decimal("0")
                )
                asset.source = "mufap"
                asset.updated_at = now

                profile = existing_profiles.get(asset.id)
                if profile is not None:
                    if row["ter_pct"] > 0:
                        profile.ter_pct = row["ter_pct"]
                    if row["management_fee_pct"] > 0:
                        profile.management_fee_pct = row["management_fee_pct"]
                    if row["front_end_load_pct"] > 0:
                        profile.front_end_load_pct = row["front_end_load_pct"]
                    if row["aum_mn"] > 0:
                        profile.aum_mn = row["aum_mn"]
                    if row.get("is_daily_dividend"):
                        profile.is_daily_dividend = True
                    if row.get("constant_par_nav") is not None:
                        profile.constant_par_nav = row["constant_par_nav"]
                    if row.get("annualized_yield_pct") and row["annualized_yield_pct"] > 0:
                        profile.annualized_yield_pct = row["annualized_yield_pct"]
                    for field_name in ("return_1m_pct", "return_6m_pct", "return_1y_pct", "return_3y_pct"):
                        if row[field_name] is not None:
                            setattr(profile, field_name, row[field_name])

            bar = existing_today_bars.get(asset.id)
            if bar is None:
                db.add(
                    PriceBar(
                        asset_id=asset.id,
                        timeframe="1D",
                        bar_time=bar_day,
                        open=row["nav"],
                        high=row["nav"],
                        low=row["nav"],
                        close=row["nav"],
                        volume=Decimal("0"),
                    )
                )
            else:
                bar.close = row["nav"]
                bar.high = max(bar.high, row["nav"])
                bar.low = min(bar.low, row["nav"])

            await cache_quote(
                asset.symbol,
                {"symbol": asset.symbol, "last_price": str(row["nav"]), "updated_at": now.isoformat()},
            )
            upserted += 1

        await db.commit()
        if extra_amc_rows:
            upserted += await upsert_mutual_fund_rows(db, extra_amc_rows)
        return upserted
