"""
OpenKhazana v8 Multi-Asset Expansion Router (`/api/v1/market/...`):
  1. Forex & Remittance Hub (`GET /forex`): PKR <-> 14+ FCY corridors (Interbank & Open Market),
     9 Global Major FX Pairs + DXY, and 5Y historical PKR currency trends.
  2. PMEX & Global Commodities Hub (`GET /commodities`): Live COMEX/NYMEX/ICE/CBOT futures &
     precious metals mapped to official PMEX PKR contract equivalents, tick/margin estimates,
     90D chart series, and PSX Sector Impact Matrix.
  3. Fixed Income, GoP Ijarah Sukuk & National Savings (NSS) Hub (`GET /fixed-income`):
     Live SBP T-Bills/PIBs/KIBOR + CDNS NSS profit rates + GoP Ijarah Sukuk + RDA Naya Pakistan
     Certificates + Top Daily Dividend / Money Market Mutual Funds for After-Tax comparison.
  4. Mutual Funds Lab Upgrades:
     - AMC Leaderboard & Category Champions (`GET /funds/leaderboard`)
     - SECP FMR Top-10 Holdings Overlap Detector (`GET /funds/overlap`)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_usd_to_pkr_rate
from app.core.cache import api_cache
from app.core.database import get_db
from app.models.market import (
    Asset,
    FundFmrSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
)

logger = logging.getLogger("openkhazana.api.markets_v8")
router = APIRouter(prefix="/api/v1/market", tags=["markets-v8"])

FX_OPEN_API_URL = "https://open.er-api.com/v6/latest/USD"
OPEN_MARKET_SPREAD = 1.0075  # Typical Pakistan Open Market premium over SBP Interbank
GRAMS_PER_TROY_OZ = 31.1034768
GRAMS_PER_TOLA = 11.6638038

PKR_CORRIDORS: list[tuple[str, str, str, str | None]] = [
    ("USD", "US Dollar", "USA / Global Reserve", "USD_PKR_INTERBANK"),
    ("EUR", "Euro", "Eurozone", "EURPKR"),
    ("GBP", "British Pound Sterling", "United Kingdom", "GBPPKR"),
    ("AED", "UAE Dirham", "United Arab Emirates", "AEDPKR"),
    ("SAR", "Saudi Riyal", "Saudi Arabia", "SARPKR"),
    ("CAD", "Canadian Dollar", "Canada", None),
    ("AUD", "Australian Dollar", "Australia", None),
    ("CNY", "Chinese Yuan (Renminbi)", "China (CPEC Trade)", None),
    ("CHF", "Swiss Franc", "Switzerland", None),
    ("JPY", "Japanese Yen", "Japan", None),
    ("KWD", "Kuwaiti Dinar", "Kuwait", None),
    ("QAR", "Qatari Riyal", "Qatar", None),
    ("OMR", "Omani Rial", "Oman", None),
    ("BHD", "Bahraini Dinar", "Bahrain", None),
]

GLOBAL_FX_MAJORS: list[tuple[str, str, str, str]] = [
    ("EUR/USD", "EUR", "USD", "Euro / US Dollar"),
    ("GBP/USD", "GBP", "USD", "British Pound / US Dollar"),
    ("USD/JPY", "USD", "JPY", "US Dollar / Japanese Yen"),
    ("USD/CHF", "USD", "CHF", "US Dollar / Swiss Franc"),
    ("AUD/USD", "AUD", "USD", "Australian Dollar / US Dollar"),
    ("USD/CAD", "USD", "CAD", "US Dollar / Canadian Dollar"),
    ("USD/CNY", "USD", "CNY", "US Dollar / Chinese Yuan"),
    ("EUR/GBP", "EUR", "GBP", "Euro / British Pound"),
    ("USD/SAR", "USD", "SAR", "US Dollar / Saudi Riyal (Pegged)"),
]

PMEX_COMMODITY_SPECS: list[dict[str, Any]] = [
    {
        "code": "GOLD",
        "yf_symbol": "GC=F",
        "name": "Gold (COMEX / PMEX)",
        "category": "Precious Metals",
        "unit_label": "USD / Troy Oz",
        "pmex_contract": "PMEX MTOLA (10 Tola) / GOLD1OZ",
        "pmex_lot_units": 3.75,  # 10 Tola = 116.638g = 3.75 troy oz
        "pmex_lot_desc": "10 Tola (3.75 Troy Oz)",
        "margin_pct": 6.0,
        "psx_impact": "PKR inflation hedge & safe haven; boosts gold-backed wealth & jewelry demand",
        "psx_tickers": ["MEBL", "LUCK"],
    },
    {
        "code": "SILVER",
        "yf_symbol": "SI=F",
        "name": "Silver (COMEX / PMEX)",
        "category": "Precious Metals",
        "unit_label": "USD / Troy Oz",
        "pmex_contract": "PMEX SL100OZ / SL10OZ",
        "pmex_lot_units": 100.0,
        "pmex_lot_desc": "100 Troy Oz",
        "margin_pct": 8.0,
        "psx_impact": "Industrial + precious metal dual beta; solar panel & electronics supply chain",
        "psx_tickers": ["PAEL"],
    },
    {
        "code": "PLATINUM",
        "yf_symbol": "PL=F",
        "name": "Platinum (NYMEX / PMEX)",
        "category": "Precious Metals",
        "unit_label": "USD / Troy Oz",
        "pmex_contract": "PMEX PLAT5OZ",
        "pmex_lot_units": 5.0,
        "pmex_lot_desc": "5 Troy Oz",
        "margin_pct": 8.0,
        "psx_impact": "Global automotive catalytic converters & precious metals diversification",
        "psx_tickers": ["INDU", "HCAR"],
    },
    {
        "code": "BRENT",
        "yf_symbol": "BZ=F",
        "name": "Brent Crude Oil (ICE / PMEX)",
        "category": "Energy",
        "unit_label": "USD / Barrel",
        "pmex_contract": "PMEX BRENT10",
        "pmex_lot_units": 10.0,
        "pmex_lot_desc": "10 Barrels",
        "margin_pct": 9.0,
        "psx_impact": "Higher Brent boosts E&P realizations (OGDC/PPL/POL) but pressures Pakistan oil import bill & CPI",
        "psx_tickers": ["OGDC", "PPL", "MARI", "POL", "PSO", "ATRL"],
    },
    {
        "code": "WTI",
        "yf_symbol": "CL=F",
        "name": "WTI Crude Oil (NYMEX / PMEX)",
        "category": "Energy",
        "unit_label": "USD / Barrel",
        "pmex_contract": "PMEX CRUDE10 / CRUDE100",
        "pmex_lot_units": 10.0,
        "pmex_lot_desc": "10 Barrels (Mini)",
        "margin_pct": 9.0,
        "psx_impact": "Key global oil benchmark traded actively on PMEX; drives refinery cracks & transport inflation",
        "psx_tickers": ["OGDC", "PPL", "PRL", "NRL", "PSO"],
    },
    {
        "code": "NATGAS",
        "yf_symbol": "NG=F",
        "name": "Natural Gas (Henry Hub / PMEX)",
        "category": "Energy",
        "unit_label": "USD / MMBtu",
        "pmex_contract": "PMEX NGAS1K",
        "pmex_lot_units": 1000.0,
        "pmex_lot_desc": "1,000 MMBtu",
        "margin_pct": 12.0,
        "psx_impact": "Global LNG benchmark affects Pakistan RLNG import costs, Fertilizer feedstock & Power IPPs",
        "psx_tickers": ["SNGP", "SSGC", "FFC", "EFERT", "HUBC"],
    },
    {
        "code": "COPPER",
        "yf_symbol": "HG=F",
        "name": "Copper (COMEX / PMEX)",
        "category": "Industrial Metals",
        "unit_label": "USD / lb",
        "pmex_contract": "PMEX COPPER1K",
        "pmex_lot_units": 1000.0,
        "pmex_lot_desc": "1,000 lbs",
        "margin_pct": 8.0,
        "psx_impact": "Input cost for cables & electricals (FCL/PCAL/PAEL); long-term Reko Diq copper upside for OGDC/PPL",
        "psx_tickers": ["FCL", "PCAL", "PAEL", "OGDC", "PPL"],
    },
    {
        "code": "COTTON",
        "yf_symbol": "CT=F",
        "name": "Cotton No. 2 (ICE / PMEX)",
        "category": "Agriculture",
        "unit_label": "US Cents / lb",
        "price_div": 100.0,
        "pmex_contract": "PMEX ICOTTON",
        "pmex_lot_units": 5000.0,
        "pmex_lot_desc": "5,000 lbs",
        "margin_pct": 8.0,
        "psx_impact": "Primary raw material for Pakistan's $16B+ Textile export sector — lower cotton expands spinner margins",
        "psx_tickers": ["ILP", "NML", "GATM", "NCL", "KTML"],
    },
    {
        "code": "WHEAT",
        "yf_symbol": "ZW=F",
        "name": "Wheat (CBOT / PMEX)",
        "category": "Agriculture",
        "unit_label": "US Cents / Bushel",
        "price_div": 100.0,
        "pmex_contract": "PMEX WHEAT",
        "pmex_lot_units": 500.0,
        "pmex_lot_desc": "500 Bushels",
        "margin_pct": 8.0,
        "psx_impact": "Core weight in Pakistan PBS Food CPI basket & flour/food processors",
        "psx_tickers": ["NATF", "NESTLE", "UNITY"],
    },
    {
        "code": "CORN",
        "yf_symbol": "ZC=F",
        "name": "Corn (CBOT / PMEX)",
        "category": "Agriculture",
        "unit_label": "US Cents / Bushel",
        "price_div": 100.0,
        "pmex_contract": "PMEX CORN",
        "pmex_lot_units": 500.0,
        "pmex_lot_desc": "500 Bushels",
        "margin_pct": 8.0,
        "psx_impact": "Key poultry, dairy & starch feedstock cost (Rafhan Maize, poultry/foods)",
        "psx_tickers": ["RMPL", "FCEPL", "FFL"],
    },
    {
        "code": "SOYBEAN",
        "yf_symbol": "ZS=F",
        "name": "Soybeans (CBOT / PMEX)",
        "category": "Agriculture",
        "unit_label": "US Cents / Bushel",
        "price_div": 100.0,
        "pmex_contract": "PMEX SOYBEAN",
        "pmex_lot_units": 500.0,
        "pmex_lot_desc": "500 Bushels",
        "margin_pct": 8.0,
        "psx_impact": "Edible oil & poultry feed cost driver in Pakistan food inflation",
        "psx_tickers": ["UNITY", "MFL"],
    },
]

# Official CDNS National Savings Schemes (NSS) reference schedule (updated dynamically from savings.gov.pk when reachable)
CDNS_NSS_SCHEMES: list[dict[str, Any]] = [
    {
        "code": "NSS_BSC",
        "name": "Behbood Savings Certificates (BSC)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "10 Years (Monthly Profit)",
        "rate_pct": 13.68,
        "is_shariah": False,
        "wht_exempt": True,
        "zakat_exempt": True,
        "payout_frequency": "Monthly",
        "eligibility": "Senior citizens (60+), widows & differently-abled (max Rs 7.5M)",
    },
    {
        "code": "NSS_PBA",
        "name": "Pensioners' Benefit Account (PBA)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "10 Years (Monthly Profit)",
        "rate_pct": 13.68,
        "is_shariah": False,
        "wht_exempt": True,
        "zakat_exempt": True,
        "payout_frequency": "Monthly",
        "eligibility": "Retired govt / armed forces / semi-govt pensioners",
    },
    {
        "code": "NSS_DSC",
        "name": "Defence Savings Certificates (DSC)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "10 Years (Compounded)",
        "rate_pct": 12.15,
        "is_shariah": False,
        "wht_exempt": False,
        "zakat_exempt": False,
        "payout_frequency": "At Maturity (Compounded)",
        "eligibility": "All Pakistani & Overseas Pakistani individuals",
    },
    {
        "code": "NSS_RIC",
        "name": "Regular Income Certificates (RIC)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "5 Years (Monthly Profit)",
        "rate_pct": 11.76,
        "is_shariah": False,
        "wht_exempt": False,
        "zakat_exempt": True,
        "payout_frequency": "Monthly",
        "eligibility": "All individuals & institutions (Zakat exempt)",
    },
    {
        "code": "NSS_SSC",
        "name": "Special Savings Certificates (SSC)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "3 Years (6-Monthly Profit)",
        "rate_pct": 11.60,
        "is_shariah": False,
        "wht_exempt": False,
        "zakat_exempt": False,
        "payout_frequency": "Semi-Annual",
        "eligibility": "All individuals (step-up rate in Year 3)",
    },
    {
        "code": "NSS_SARWA_1Y",
        "name": "Sarwa Islamic Term Account (SITA 1-Year)",
        "issuer": "CDNS (Shariah Compliant)",
        "category": "Islamic NSS (Sarwa)",
        "tenor": "1 Year",
        "rate_pct": 11.16,
        "is_shariah": True,
        "wht_exempt": False,
        "zakat_exempt": False,
        "payout_frequency": "At Maturity",
        "eligibility": "Shariah-compliant Govt. backed Islamic certificate",
    },
    {
        "code": "NSS_SARWA_5Y",
        "name": "Sarwa Islamic Term Account (SITA 5-Year)",
        "issuer": "CDNS (Shariah Compliant)",
        "category": "Islamic NSS (Sarwa)",
        "tenor": "5 Years (Monthly Profit)",
        "rate_pct": 11.40,
        "is_shariah": True,
        "wht_exempt": False,
        "zakat_exempt": False,
        "payout_frequency": "Monthly",
        "eligibility": "Shariah-compliant Govt. backed monthly income",
    },
    {
        "code": "NSS_STSC_3M",
        "name": "Short Term Savings Certificates (3-Month)",
        "issuer": "CDNS (Govt. of Pakistan)",
        "category": "National Savings (NSS)",
        "tenor": "3 Months",
        "rate_pct": 11.44,
        "is_shariah": False,
        "wht_exempt": False,
        "zakat_exempt": True,
        "payout_frequency": "At Maturity",
        "eligibility": "Short-term sovereign liquidity (Zakat exempt)",
    },
]

# Official SBP Roshan Digital Account (RDA) Naya Pakistan Certificates (NPC) & Islamic NPC rates
RDA_NPC_RATES: list[dict[str, Any]] = [
    {"code": "NPC_USD_1Y", "name": "Naya Pakistan Certificate (USD · 1Y)", "currency": "USD", "tenor": "1 Year", "rate_pct": 8.00, "is_shariah": False, "wht_pct": 10.0},
    {"code": "INPC_USD_1Y", "name": "Islamic Naya Pakistan Certificate (USD · 1Y)", "currency": "USD", "tenor": "1 Year", "rate_pct": 7.50, "is_shariah": True, "wht_pct": 10.0},
    {"code": "INPC_USD_5Y", "name": "Islamic Naya Pakistan Certificate (USD · 5Y)", "currency": "USD", "tenor": "5 Years", "rate_pct": 8.00, "is_shariah": True, "wht_pct": 10.0},
    {"code": "INPC_GBP_1Y", "name": "Islamic Naya Pakistan Certificate (GBP · 1Y)", "currency": "GBP", "tenor": "1 Year", "rate_pct": 7.00, "is_shariah": True, "wht_pct": 10.0},
    {"code": "INPC_EUR_1Y", "name": "Islamic Naya Pakistan Certificate (EUR · 1Y)", "currency": "EUR", "tenor": "1 Year", "rate_pct": 5.50, "is_shariah": True, "wht_pct": 10.0},
    {"code": "INPC_PKR_1Y", "name": "Islamic Naya Pakistan Certificate (PKR · 1Y)", "currency": "PKR", "tenor": "1 Year", "rate_pct": 13.50, "is_shariah": True, "wht_pct": 10.0},
]


def _fetch_commodities_yfinance_sync() -> dict[str, dict[str, Any]]:
    """Fetch live global futures quotes + 1D/1M/1Y returns + 90D chart points via yfinance."""
    import yfinance as yf

    out: dict[str, dict[str, Any]] = {}
    yf_syms = [spec["yf_symbol"] for spec in PMEX_COMMODITY_SPECS] + ["DX-Y.NYB"]
    try:
        data = yf.download(
            tickers=" ".join(yf_syms),
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.debug("yfinance batch commodities download error: %s", exc)
        return out

    if data is None or data.empty:
        return out

    for sym in yf_syms:
        try:
            sub = data[sym]["Close"].dropna() if isinstance(data.columns, pd.MultiIndex) else data["Close"].dropna()
            if sub.empty:
                continue
            last_px = float(sub.iloc[-1])
            prev_px = float(sub.iloc[-2]) if len(sub) >= 2 else last_px
            chg_1d = round(((last_px / prev_px) - 1.0) * 100.0, 2) if prev_px > 0 else 0.0
            ret_1m = round(((last_px / float(sub.iloc[-22])) - 1.0) * 100.0, 2) if len(sub) >= 22 and float(sub.iloc[-22]) > 0 else None
            ret_1y = round(((last_px / float(sub.iloc[0])) - 1.0) * 100.0, 2) if float(sub.iloc[0]) > 0 else None
            hi_52w = float(sub.max())
            lo_52w = float(sub.min())
            series = [
                {"date": idx.strftime("%Y-%m-%d"), "price": round(float(v), 4)}
                for idx, v in sub.tail(90).items()
            ]
            out[sym] = {
                "price": last_px,
                "change_1d_pct": chg_1d,
                "return_1m_pct": ret_1m,
                "return_1y_pct": ret_1y,
                "high_52w": hi_52w,
                "low_52w": lo_52w,
                "series": series,
            }
        except Exception:
            continue
    return out


async def _fetch_live_fx_rates(fallback_usd_pkr: float) -> tuple[dict[str, float], str]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(FX_OPEN_API_URL)
            if r.status_code == 200:
                payload = r.json()
                rates = payload.get("rates") or {}
                if float(rates.get("PKR", 0)) > 0:
                    return {k: float(v) for k, v in rates.items() if isinstance(v, (int, float))}, str(
                        payload.get("time_last_update_utc") or datetime.now(timezone.utc).isoformat()
                    )
    except Exception as exc:
        logger.debug("Live FX fetch skipped, using DB fallback: %s", exc)
    # Deterministic fallback rates if completely offline
    return (
        {
            "USD": 1.0,
            "PKR": fallback_usd_pkr or 278.5,
            "EUR": 0.92,
            "GBP": 0.78,
            "AED": 3.6725,
            "SAR": 3.75,
            "CAD": 1.38,
            "AUD": 1.52,
            "CNY": 7.22,
            "CHF": 0.88,
            "JPY": 152.0,
            "KWD": 0.307,
            "QAR": 3.64,
            "OMR": 0.385,
            "BHD": 0.376,
        },
        datetime.now(timezone.utc).isoformat(),
    )


def parse_cdns_nss_rates_html(html_text: str) -> dict[str, float]:
    """Best-effort live rate extractor from CDNS `savings.gov.pk` HTML."""
    if not html_text:
        return {}
    text = BeautifulSoup(html_text, "lxml").get_text(" ", strip=True)
    found: dict[str, float] = {}
    patterns = [
        ("NSS_BSC", r"Behbood\s+Savings.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
        ("NSS_PBA", r"Pensioners['’]?\s+Benefit.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
        ("NSS_DSC", r"Defence\s+Savings.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
        ("NSS_RIC", r"Regular\s+Income.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
        ("NSS_SSC", r"Special\s+Savings.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
        ("NSS_SARWA_1Y", r"Sarwa\s+Islamic\s+Term.{0,65}?(\d{1,2}\.\d{1,2})\s*%"),
    ]
    for code, pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1))
                if 4.0 <= v <= 28.0:
                    found[code] = round(v, 2)
            except ValueError:
                pass
    return found


@router.get("/forex")
async def forex_and_remittance_hub(db: AsyncSession = Depends(get_db)):
    """
    Pakistan Forex & Remittance Hub:
      - PKR <-> 14 major FCY corridors (SBP Interbank vs Open Market Buy/Sell)
      - 9 Global Major FX Pairs + DXY US Dollar Index
      - 5Y historical PKR trend charts from local DB
    """
    async def _load() -> dict[str, Any]:
        db_usd_pkr = float(await get_usd_to_pkr_rate(db))
        rates, updated_utc = await _fetch_live_fx_rates(db_usd_pkr)
        usd_pkr_ib = float(rates.get("PKR") or db_usd_pkr or 278.5)

        fx_assets = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class == "FX",
                    )
                )
            ).scalars()
        }

        # Load downsampled 5Y history for DB-tracked PKR corridors
        history_series: dict[str, list[dict[str, Any]]] = {}
        for sym_key in ("USD_PKR_INTERBANK", "EURPKR", "GBPPKR", "AEDPKR", "SARPKR"):
            a = fx_assets.get(sym_key)
            if a is None:
                continue
            rows = (
                await db.execute(
                    select(PriceBar.bar_time, PriceBar.close)
                    .where(PriceBar.asset_id == a.id, PriceBar.timeframe == "1D")
                    .order_by(PriceBar.bar_time.asc())
                )
            ).all()
            if rows:
                step = max(1, len(rows) // 140)
                sampled = rows[::step]
                if sampled[-1] != rows[-1]:
                    sampled.append(rows[-1])
                history_series[sym_key] = [
                    {"date": bt.strftime("%Y-%m-%d"), "rate": round(float(cl), 2)}
                    for bt, cl in sampled
                    if float(cl) > 0
                ]

        corridors: list[dict[str, Any]] = []
        for code, name, region, db_sym in PKR_CORRIDORS:
            per_usd = float(rates.get(code, 0))
            if per_usd <= 0:
                continue
            ib_pkr = usd_pkr_ib / per_usd
            open_buy = ib_pkr * (OPEN_MARKET_SPREAD - 0.0025)
            open_sell = ib_pkr * (OPEN_MARKET_SPREAD + 0.0035)
            db_a = fx_assets.get(db_sym) if db_sym else None
            chg = float(db_a.change_pct) if db_a else 0.0
            corridors.append(
                {
                    "code": code,
                    "pair": f"{code}/PKR",
                    "name": name,
                    "region": region,
                    "interbank_pkr": round(ib_pkr, 2 if ib_pkr >= 10 else 4),
                    "open_market_buy_pkr": round(open_buy, 2 if ib_pkr >= 10 else 4),
                    "open_market_sell_pkr": round(open_sell, 2 if ib_pkr >= 10 else 4),
                    "usd_cross": round(per_usd, 4),
                    "change_pct": round(chg, 2),
                    "history_key": db_sym if (db_sym and db_sym in history_series) else None,
                }
            )

        majors: list[dict[str, Any]] = []
        for pair, base_c, quote_c, label in GLOBAL_FX_MAJORS:
            r_base = float(rates.get(base_c, 0))
            r_quote = float(rates.get(quote_c, 0))
            if r_base > 0 and r_quote > 0:
                px = r_quote / r_base
                majors.append(
                    {
                        "pair": pair,
                        "base": base_c,
                        "quote": quote_c,
                        "name": label,
                        "rate": round(px, 4 if px < 20 else 2),
                    }
                )

        # Attach DXY from commodities batch cache if available (or quick fetch)
        yf_batch = await api_cache.get_or_set(
            "v8:yf_commodities_batch",
            300.0,
            lambda: asyncio.to_thread(_fetch_commodities_yfinance_sync),
        )
        dxy = yf_batch.get("DX-Y.NYB")

        return {
            "updated_at": updated_utc,
            "usd_pkr_interbank": round(usd_pkr_ib, 2),
            "usd_pkr_open_sell": round(usd_pkr_ib * (OPEN_MARKET_SPREAD + 0.0035), 2),
            "dxy": {
                "symbol": "DXY",
                "name": "US Dollar Index (ICE DXY)",
                "price": round(dxy["price"], 2) if dxy else None,
                "change_1d_pct": dxy["change_1d_pct"] if dxy else None,
                "return_1m_pct": dxy["return_1m_pct"] if dxy else None,
                "return_1y_pct": dxy["return_1y_pct"] if dxy else None,
            },
            "corridors": corridors,
            "global_majors": majors,
            "rates_per_usd": {c["code"]: c["usd_cross"] for c in corridors} | {"PKR": round(usd_pkr_ib, 4)},
            "history": history_series,
        }

    return await api_cache.get_or_set("v8:forex_hub", 180.0, _load)


@router.get("/commodities")
async def pmex_and_global_commodities_hub(db: AsyncSession = Depends(get_db)):
    """
    PMEX & Global Commodities Hub:
      - 11 live global futures contracts (Gold, Silver, Platinum, Brent, WTI, NatGas, Copper, Cotton, Wheat, Corn, Soybean)
      - Converted live to PKR, Pakistan Gold Tola/10g/1g (24K & 22K), and PMEX Contract Notional & Est. Margin
      - PSX Sector & Ticker Impact Map + 90D price trajectories
    """
    async def _load() -> dict[str, Any]:
        usd_pkr_ib = float(await get_usd_to_pkr_rate(db))
        usd_pkr_open = usd_pkr_ib * OPEN_MARKET_SPREAD

        yf_batch = await api_cache.get_or_set(
            "v8:yf_commodities_batch",
            300.0,
            lambda: asyncio.to_thread(_fetch_commodities_yfinance_sync),
        )

        # Fallback DB gold/silver gram quotes if yfinance is temporarily unavailable
        metal_assets = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.symbol.in_(["GOLD_24K_GRAM", "SILVER_GRAM"]),
                    )
                )
            ).scalars()
        }

        items: list[dict[str, Any]] = []
        gold_pkr_breakdown: dict[str, float] | None = None

        for spec in PMEX_COMMODITY_SPECS:
            q = yf_batch.get(spec["yf_symbol"])
            raw_px = q["price"] if q else None
            price_div = float(spec.get("price_div", 1.0))

            if raw_px is None and spec["code"] == "GOLD" and "GOLD_24K_GRAM" in metal_assets:
                raw_px = (float(metal_assets["GOLD_24K_GRAM"].last_price) * GRAMS_PER_TROY_OZ) / max(usd_pkr_open, 1.0)
            elif raw_px is None and spec["code"] == "SILVER" and "SILVER_GRAM" in metal_assets:
                raw_px = (float(metal_assets["SILVER_GRAM"].last_price) * GRAMS_PER_TROY_OZ) / max(usd_pkr_open, 1.0)

            usd_unit_px = (raw_px / price_div) if raw_px is not None else None
            pkr_unit_px = (usd_unit_px * usd_pkr_open) if usd_unit_px is not None else None
            pmex_notional_pkr = (pkr_unit_px * spec["pmex_lot_units"]) if pkr_unit_px is not None else None
            est_margin_pkr = (pmex_notional_pkr * (spec["margin_pct"] / 100.0)) if pmex_notional_pkr is not None else None

            if spec["code"] == "GOLD" and pkr_unit_px is not None:
                pkr_per_g_24k = pkr_unit_px / GRAMS_PER_TROY_OZ
                gold_pkr_breakdown = {
                    "gold_24k_1g_pkr": round(pkr_per_g_24k, 0),
                    "gold_24k_10g_pkr": round(pkr_per_g_24k * 10.0, 0),
                    "gold_24k_tola_pkr": round(pkr_per_g_24k * GRAMS_PER_TOLA, 0),
                    "gold_22k_tola_pkr": round(pkr_per_g_24k * GRAMS_PER_TOLA * (22.0 / 24.0), 0),
                    "gold_21k_tola_pkr": round(pkr_per_g_24k * GRAMS_PER_TOLA * (21.0 / 24.0), 0),
                    "usd_per_oz": round(usd_unit_px or 0.0, 2),
                }

            items.append(
                {
                    "code": spec["code"],
                    "yf_symbol": spec["yf_symbol"],
                    "name": spec["name"],
                    "category": spec["category"],
                    "unit_label": spec["unit_label"],
                    "price_usd": round(raw_px, 2) if raw_px is not None else None,
                    "price_pkr_unit": round(pkr_unit_px, 2) if pkr_unit_px is not None else None,
                    "change_1d_pct": q["change_1d_pct"] if q else 0.0,
                    "return_1m_pct": q["return_1m_pct"] if q else None,
                    "return_1y_pct": q["return_1y_pct"] if q else None,
                    "high_52w": round(q["high_52w"], 2) if q else None,
                    "low_52w": round(q["low_52w"], 2) if q else None,
                    "pmex_contract": spec["pmex_contract"],
                    "pmex_lot_desc": spec["pmex_lot_desc"],
                    "pmex_notional_pkr": round(pmex_notional_pkr, 0) if pmex_notional_pkr is not None else None,
                    "pmex_est_margin_pkr": round(est_margin_pkr, 0) if est_margin_pkr is not None else None,
                    "margin_pct": spec["margin_pct"],
                    "psx_impact": spec["psx_impact"],
                    "psx_tickers": spec["psx_tickers"],
                    "series": q["series"] if q else [],
                }
            )

        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "usd_pkr_rate": round(usd_pkr_open, 2),
            "gold_pkr_breakdown": gold_pkr_breakdown,
            "commodities": items,
            "pmex_resources": [
                {"label": "PMEX Official Market Summary", "url": "https://pmex.com.pk/market-summary/"},
                {"label": "PMEX Listed Contract Specifications", "url": "https://pmex.com.pk/trading/contracts/"},
                {"label": "PMEX Margin & Fee Schedule", "url": "https://pmex.com.pk/trading/margins/"},
            ],
        }

    return await api_cache.get_or_set("v8:commodities_hub", 300.0, _load)


async def _fetch_live_cdns_nss_overrides() -> dict[str, float]:
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, verify=False) as hc:
            r = await hc.get("https://savings.gov.pk/", headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                return parse_cdns_nss_rates_html(r.text)
    except Exception:
        pass
    return {}


@router.get("/fixed-income")
async def fixed_income_nss_and_sukuk_hub(db: AsyncSession = Depends(get_db)):
    """
    Pakistan Fixed Income, GoP Ijarah Sukuk, SBP T-Bills/PIBs, CDNS National Savings (NSS)
    & RDA Naya Pakistan Certificates Hub + Top Money-Market/Daily-Dividend Mutual Funds.
    """
    async def _load() -> dict[str, Any]:
        macros = {
            m.code: m
            for m in (
                await db.execute(select(MacroIndicator))
            ).scalars()
        }

        # Best-effort live check of CDNS savings.gov.pk rates
        live_nss_overrides = await _fetch_live_cdns_nss_overrides()

        nss_schemes = [
            {
                **s,
                "rate_pct": live_nss_overrides.get(s["code"], s["rate_pct"]),
                "rate_source": "CDNS Live" if s["code"] in live_nss_overrides else "CDNS Official Schedule",
            }
            for s in CDNS_NSS_SCHEMES
        ]

        # Build Sovereign T-Bills & PIBs directly from live SBP MacroIndicator table
        sovereign_instruments: list[dict[str, Any]] = []
        for code, label, tenor, is_shariah in [
            ("TBILL_1M", "Market T-Bill (1-Month)", "1 Month", False),
            ("TBILL_3M", "Market T-Bill (3-Month)", "3 Months", False),
            ("TBILL_6M", "Market T-Bill (6-Month)", "6 Months", False),
            ("TBILL_12M", "Market T-Bill (12-Month)", "12 Months", False),
            ("PIB_2Y", "Pakistan Investment Bond (2Y PIB)", "2 Years", False),
            ("PIB_3Y", "Pakistan Investment Bond (3Y PIB)", "3 Years", False),
            ("PIB_5Y", "Pakistan Investment Bond (5Y PIB)", "5 Years", False),
            ("PIB_10Y", "Pakistan Investment Bond (10Y PIB)", "10 Years", False),
        ]:
            m = macros.get(code)
            if m is not None:
                sovereign_instruments.append(
                    {
                        "code": code,
                        "name": label,
                        "issuer": "State Bank of Pakistan / GoP",
                        "category": "T-Bill" if code.startswith("TBILL") else "PIB Bond",
                        "tenor": tenor,
                        "rate_pct": round(float(m.value), 2),
                        "is_shariah": is_shariah,
                        "wht_exempt": False,
                        "zakat_exempt": True,
                        "as_of_date": m.as_of_date.isoformat(),
                    }
                )

        # GoP Ijarah Sukuk (PSX-listed GIS) benchmarked to live SBP T-Bill / PIB yields
        tb12 = float(macros["TBILL_12M"].value) if "TBILL_12M" in macros else 12.2
        pib3 = float(macros["PIB_3Y"].value) if "PIB_3Y" in macros else 12.6
        pib5 = float(macros["PIB_5Y"].value) if "PIB_5Y" in macros else 12.8
        pib10 = float(macros["PIB_10Y"].value) if "PIB_10Y" in macros else 12.85
        sukuk_instruments = [
            {
                "code": "GIS_1Y_DISC",
                "name": "GoP Ijarah Sukuk (1-Year Discounted · PSX)",
                "issuer": "Govt. of Pakistan (PSX GIS)",
                "category": "GoP Ijarah Sukuk",
                "tenor": "1 Year (Zero-Coupon Discounted)",
                "rate_pct": round(tb12 - 0.45, 2),
                "is_shariah": True,
                "wht_exempt": False,
                "zakat_exempt": True,
                "payout_frequency": "At Maturity (Discounted)",
            },
            {
                "code": "GIS_3Y_FRR",
                "name": "GoP Ijarah Sukuk (3-Year Fixed Rental · PSX)",
                "issuer": "Govt. of Pakistan (PSX GIS)",
                "category": "GoP Ijarah Sukuk",
                "tenor": "3 Years (Semi-Annual Rental)",
                "rate_pct": round(pib3 - 0.35, 2),
                "is_shariah": True,
                "wht_exempt": False,
                "zakat_exempt": True,
                "payout_frequency": "Semi-Annual",
            },
            {
                "code": "GIS_5Y_VRR",
                "name": "GoP Ijarah Sukuk (5-Year Variable Rental · PSX)",
                "issuer": "Govt. of Pakistan (PSX GIS)",
                "category": "GoP Ijarah Sukuk",
                "tenor": "5 Years (6M T-Bill Linked)",
                "rate_pct": round(pib5 - 0.25, 2),
                "is_shariah": True,
                "wht_exempt": False,
                "zakat_exempt": True,
                "payout_frequency": "Semi-Annual",
            },
            {
                "code": "GIS_10Y_FRR",
                "name": "GoP Ijarah Sukuk (10-Year Fixed Rental · PSX)",
                "issuer": "Govt. of Pakistan (PSX GIS)",
                "category": "GoP Ijarah Sukuk",
                "tenor": "10 Years (Semi-Annual Rental)",
                "rate_pct": round(pib10 - 0.20, 2),
                "is_shariah": True,
                "wht_exempt": False,
                "zakat_exempt": True,
                "payout_frequency": "Semi-Annual",
            },
        ]

        # Top Money Market & Daily Dividend Mutual Funds from DB for side-by-side yield benchmarking
        mm_rows = (
            await db.execute(
                select(Asset, MutualFundProfile)
                .join(MutualFundProfile, MutualFundProfile.asset_id == Asset.id)
                .where(Asset.owner_user_id.is_(None))
            )
        ).all()
        top_cash_funds: list[dict[str, Any]] = []
        for a, m in mm_rows:
            cat_l = (m.fund_category or "").lower()
            if not (m.is_daily_dividend or "money market" in cat_l or "cash" in cat_l or "income" in cat_l):
                continue
            y = float(m.annualized_yield_pct or m.return_1y_pct or 0)
            if 5.0 <= y <= 30.0:
                top_cash_funds.append(
                    {
                        "symbol": a.symbol,
                        "name": a.name,
                        "amc_name": m.amc_name,
                        "fund_category": m.fund_category,
                        "is_shariah": bool(a.is_shariah_compliant),
                        "is_daily_dividend": bool(m.is_daily_dividend),
                        "rate_pct": round(y, 2),
                        "ter_pct": round(float(m.ter_pct), 2),
                    }
                )
        top_cash_funds.sort(key=lambda x: x["rate_pct"], reverse=True)

        return {
            "policy_rate_pct": round(float(macros["SBP_POLICY_RATE"].value), 2) if "SBP_POLICY_RATE" in macros else 12.0,
            "cpi_yoy_pct": round(float(macros["PK_CPI_YOY"].value), 2) if "PK_CPI_YOY" in macros else None,
            "kibor_6m_pct": round(float(macros["KIBOR_6M"].value), 2) if "KIBOR_6M" in macros else None,
            "sovereign": sovereign_instruments,
            "sukuk": sukuk_instruments,
            "nss": nss_schemes,
            "rda_npc": RDA_NPC_RATES,
            "top_money_market_funds": top_cash_funds[:12],
        }

    return await api_cache.get_or_set("v8:fixed_income_hub", 300.0, _load)


@router.get("/funds/leaderboard")
async def mutual_funds_amc_leaderboard(db: AsyncSession = Depends(get_db)):
    """
    AMC Leaderboard & Category Champions Scorecard computed via Pandas across all MUFAP/AMC mutual funds.
    """
    async def _load() -> dict[str, Any]:
        assets = (
            await db.execute(
                select(Asset)
                .options(selectinload(Asset.mutual_fund_profile), selectinload(Asset.fmr_snapshot))
                .where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                )
            )
        ).scalars().all()

        rows: list[dict[str, Any]] = []
        for a in assets:
            m = a.mutual_fund_profile
            if m is None:
                continue
            r1y = float(m.return_1y_pct) if m.return_1y_pct is not None else (
                float(m.annualized_yield_pct) if (m.is_daily_dividend and m.annualized_yield_pct) else None
            )
            rows.append(
                {
                    "symbol": a.symbol,
                    "name": a.name,
                    "amc_name": m.amc_name or "Other AMC",
                    "fund_category": m.fund_category or "General",
                    "is_shariah": bool(a.is_shariah_compliant),
                    "is_daily_dividend": bool(m.is_daily_dividend),
                    "has_fmr": a.fmr_snapshot is not None,
                    "ter_pct": float(m.ter_pct or 0),
                    "fel_pct": float(m.front_end_load_pct or 0),
                    "aum_mn": float(m.aum_mn or 0),
                    "return_1y_pct": r1y,
                    "nav": float(a.last_price),
                }
            )

        if not rows:
            return {"amc_leaderboard": [], "category_champions": []}

        def _compute_sync(recs: list[dict[str, Any]]) -> dict[str, Any]:
            df = pd.DataFrame(recs)
            amc_board: list[dict[str, Any]] = []
            for amc_name, grp in df.groupby("amc_name"):
                valid_r = grp["return_1y_pct"].dropna()
                best_row = grp.sort_values(by="return_1y_pct", ascending=False, na_position="last").iloc[0]
                amc_board.append(
                    {
                        "amc_name": str(amc_name),
                        "fund_count": int(len(grp)),
                        "shariah_count": int(grp["is_shariah"].sum()),
                        "daily_div_count": int(grp["is_daily_dividend"].sum()),
                        "fmr_count": int(grp["has_fmr"].sum()),
                        "avg_return_1y_pct": round(float(valid_r.mean()), 2) if len(valid_r) > 0 else None,
                        "best_return_1y_pct": round(float(valid_r.max()), 2) if len(valid_r) > 0 else None,
                        "best_fund_symbol": str(best_row["symbol"]),
                        "best_fund_name": str(best_row["name"]),
                        "avg_ter_pct": round(float(grp["ter_pct"].mean()), 2),
                        "lowest_ter_pct": round(float(grp["ter_pct"].min()), 2),
                        "total_aum_mn": round(float(grp["aum_mn"].sum()), 1),
                    }
                )
            amc_board.sort(key=lambda x: (x["avg_return_1y_pct"] or -999, x["fund_count"]), reverse=True)

            # Group into clean investor buckets for Category Champions
            def _bucket(cat: str, is_sh: bool) -> str:
                cl = cat.lower()
                prefix = "Shariah " if is_sh else "Conventional "
                if "pension" in cl or "vps" in cl:
                    return f"{prefix}VPS Pension"
                if "money market" in cl or "cash" in cl:
                    return f"{prefix}Money Market"
                if "equity" in cl or "index" in cl:
                    return f"{prefix}Equity"
                if "income" in cl or "sovereign" in cl or "fixed" in cl:
                    return f"{prefix}Income"
                return f"{prefix}Asset Allocation & Balanced"

            df["bucket"] = [
                _bucket(str(c), bool(s)) for c, s in zip(df["fund_category"], df["is_shariah"], strict=True)
            ]
            champions: list[dict[str, Any]] = []
            for bkt, grp in df.groupby("bucket"):
                top3 = grp.dropna(subset=["return_1y_pct"]).sort_values(
                    by=["return_1y_pct", "ter_pct"], ascending=[False, True]
                ).head(3)
                if top3.empty:
                    continue
                champions.append(
                    {
                        "bucket": str(bkt),
                        "total_funds": int(len(grp)),
                        "avg_ter_pct": round(float(grp["ter_pct"].mean()), 2),
                        "leaders": [
                            {
                                "symbol": str(r["symbol"]),
                                "name": str(r["name"]),
                                "amc_name": str(r["amc_name"]),
                                "return_1y_pct": round(float(r["return_1y_pct"]), 2),
                                "ter_pct": round(float(r["ter_pct"]), 2),
                                "fel_pct": round(float(r["fel_pct"]), 2),
                                "is_daily_dividend": bool(r["is_daily_dividend"]),
                                "has_fmr": bool(r["has_fmr"]),
                            }
                            for r in top3.to_dict(orient="records")
                        ],
                    }
                )
            champions.sort(key=lambda x: x["leaders"][0]["return_1y_pct"], reverse=True)
            return {"amc_leaderboard": amc_board, "category_champions": champions}

        return await asyncio.to_thread(_compute_sync, rows)

    return await api_cache.get_or_set("funds:leaderboard", 180.0, _load)


@router.get("/funds/overlap")
async def mutual_funds_fmr_holdings_overlap(
    symbols: str = Query(default="", description="Comma-separated 2 to 4 fund symbols with SECP FMR holdings"),
    db: AsyncSession = Depends(get_db),
):
    """
    SECP FMR Top-10 Portfolio Overlap & Redundant Fee Detector across 2-4 equity/allocation mutual funds.
    """
    fmr_rows = (
        await db.execute(
            select(FundFmrSnapshot, Asset, MutualFundProfile)
            .join(Asset, FundFmrSnapshot.asset_id == Asset.id)
            .join(MutualFundProfile, MutualFundProfile.asset_id == Asset.id)
            .where(Asset.owner_user_id.is_(None))
        )
    ).all()

    eligible = [
        (fmr, asset, prof)
        for fmr, asset, prof in fmr_rows
        if fmr.top_holdings_json and len(fmr.top_holdings_json) >= 3
    ]
    by_sym = {a.symbol.upper(): (fmr, a, prof) for fmr, a, prof in eligible}

    req_syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    picked_syms = [s for s in req_syms if s in by_sym][:4]
    if len(picked_syms) < 2:
        # Auto-pick top 3 equity funds with the richest FMR top-holdings lists
        sorted_el = sorted(eligible, key=lambda t: len(t[0].top_holdings_json or []), reverse=True)
        picked_syms = [t[1].symbol.upper() for t in sorted_el[:3]]

    available_fmr_funds = sorted(
        [
            {
                "symbol": a.symbol,
                "name": a.name,
                "amc_name": p.amc_name,
                "ter_pct": float(p.ter_pct or 0),
                "holdings_count": len(fmr.top_holdings_json or []),
                "report_label": fmr.report_label or fmr.report_month,
            }
            for fmr, a, p in eligible
        ],
        key=lambda x: x["symbol"],
    )

    if len(picked_syms) < 2:
        return {
            "selected_funds": [],
            "pairwise_overlap": [],
            "shared_holdings": [],
            "available_fmr_funds": available_fmr_funds,
        }

    selected_meta: list[dict[str, Any]] = []
    weights_by_fund: dict[str, dict[str, float]] = {}
    stock_names: dict[str, str] = {}

    for sym in picked_syms:
        fmr, asset, prof = by_sym[sym]
        w_map: dict[str, float] = {}
        for h in (fmr.top_holdings_json or []):
            h_sym = str(h.get("symbol") or "").strip().upper()
            wt = float(h.get("weight_pct") or 0.0)
            if h_sym and wt > 0:
                w_map[h_sym] = round(wt, 2)
                if h.get("name"):
                    stock_names[h_sym] = str(h["name"])
        weights_by_fund[sym] = w_map
        selected_meta.append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "amc_name": prof.amc_name,
                "ter_pct": round(float(prof.ter_pct or 0), 2),
                "fel_pct": round(float(prof.front_end_load_pct or 0), 2),
                "return_1y_pct": round(float(prof.return_1y_pct), 2) if prof.return_1y_pct is not None else None,
                "report_label": fmr.report_label or fmr.report_month,
                "holdings_count": len(w_map),
            }
        )

    # Pairwise weighted overlap % = sum of min(w_a, w_b) normalized over min(sum_a, sum_b)
    pairwise: list[dict[str, Any]] = []
    for i in range(len(picked_syms)):
        for j in range(i + 1, len(picked_syms)):
            fa, fb = picked_syms[i], picked_syms[j]
            wa, wb = weights_by_fund[fa], weights_by_fund[fb]
            common = sorted(set(wa.keys()) & set(wb.keys()))
            union = set(wa.keys()) | set(wb.keys())
            min_sum = sum(min(wa[s], wb[s]) for s in common)
            denom = max(min(sum(wa.values()), sum(wb.values())), 1.0)
            weighted_overlap_pct = round(min(100.0, (min_sum / denom) * 100.0), 1)
            jaccard_pct = round((len(common) / max(len(union), 1)) * 100.0, 1)
            verdict = (
                "HIGH_OVERLAP"
                if weighted_overlap_pct >= 60
                else ("MODERATE_OVERLAP" if weighted_overlap_pct >= 30 else "WELL_DIVERSIFIED")
            )
            pairwise.append(
                {
                    "fund_a": fa,
                    "fund_b": fb,
                    "common_stocks_count": len(common),
                    "common_symbols": common,
                    "weighted_overlap_pct": weighted_overlap_pct,
                    "jaccard_overlap_pct": jaccard_pct,
                    "verdict": verdict,
                }
            )

    # Shared holdings table across all selected funds
    all_syms = sorted({s for wm in weights_by_fund.values() for s in wm})
    shared_rows: list[dict[str, Any]] = []
    for st_sym in all_syms:
        present_in = [f for f in picked_syms if st_sym in weights_by_fund[f]]
        weights = {f: weights_by_fund[f].get(st_sym, 0.0) for f in picked_syms}
        shared_rows.append(
            {
                "symbol": st_sym,
                "name": stock_names.get(st_sym, st_sym),
                "fund_count": len(present_in),
                "avg_weight_pct": round(sum(weights.values()) / len(picked_syms), 2),
                "weights": weights,
            }
        )
    shared_rows.sort(key=lambda r: (r["fund_count"], r["avg_weight_pct"]), reverse=True)

    return {
        "selected_funds": selected_meta,
        "pairwise_overlap": pairwise,
        "shared_holdings": shared_rows[:20],
        "available_fmr_funds": available_fmr_funds,
    }


@router.get("/index-movers")
async def kse100_index_point_contributors(
    index_symbol: str = Query(default="KSE100", description="KSE100 | KMI30"),
    db: AsyncSession = Depends(get_db),
):
    """
    KSE-100 / KMI-30 Index Point Contributors & Sector Attribution Waterfall computed via Pandas.
    Uses free-float / market-cap constituent weights x daily % move x index previous close to
    estimate exact positive & negative index point movers and sector attribution.
    """
    idx_u = "KMI30" if index_symbol.strip().upper() == "KMI30" else "KSE100"

    async def _load() -> dict[str, Any]:
        idx_asset = (
            await db.execute(
                select(Asset).where(Asset.owner_user_id.is_(None), Asset.symbol == idx_u)
            )
        ).scalar_one_or_none()

        idx_price = float(idx_asset.last_price) if idx_asset and float(idx_asset.last_price) > 0 else 115000.0
        idx_chg_pct = float(idx_asset.change_pct) if idx_asset else 0.0
        idx_prev = idx_price / (1.0 + idx_chg_pct / 100.0) if (1.0 + idx_chg_pct / 100.0) > 0 else idx_price
        idx_chg_pts = round(idx_price - idx_prev, 2)

        equities = (
            await db.execute(
                select(Asset)
                .options(selectinload(Asset.psx_profile))
                .where(Asset.owner_user_id.is_(None), Asset.asset_class == "PSX_EQUITY")
            )
        ).scalars().all()

        # Filter to tagged index constituents; gracefully fall back to top 100/30 by market cap if tags sparse
        tagged = [
            a
            for a in equities
            if a.psx_profile is not None
            and (a.psx_profile.is_kmi30 if idx_u == "KMI30" else a.psx_profile.is_kse100)
            and float(a.psx_profile.market_cap_mn or 0) > 0
        ]
        if len(tagged) < 15:
            pool = [
                a
                for a in equities
                if a.psx_profile is not None
                and float(a.psx_profile.market_cap_mn or 0) > 0
                and (idx_u != "KMI30" or a.is_shariah_compliant)
            ]
            pool.sort(key=lambda a: float(a.psx_profile.market_cap_mn or 0), reverse=True)
            tagged = pool[: (30 if idx_u == "KMI30" else 100)]

        if not tagged:
            return {
                "index_symbol": idx_u,
                "index_value": round(idx_price, 2),
                "index_change_pts": idx_chg_pts,
                "index_change_pct": round(idx_chg_pct, 2),
                "constituents_evaluated": 0,
                "advancers": 0,
                "decliners": 0,
                "unchanged": 0,
                "top_positive": [],
                "top_negative": [],
                "sector_attribution": [],
            }

        rows = []
        for a in tagged:
            p = a.psx_profile
            mcap = float(p.market_cap_mn or 0)
            # Guard against occasional PSX DPS unit glitches where market cap is reported in Thousands instead of Millions
            if mcap > 2_500_000.0:
                mcap = mcap / 1000.0
            ff = float(p.free_float_pct or 0)
            ff_mcap = mcap * (ff / 100.0) if 5.0 <= ff <= 100.0 else mcap * 0.35
            rows.append(
                {
                    "symbol": a.symbol,
                    "name": a.name,
                    "sector": a.sector_or_category or "General",
                    "last_price": round(float(a.last_price), 2),
                    "change_pct": round(float(a.change_pct), 2),
                    "weight_basis": max(ff_mcap, 1.0),
                    "is_shariah_compliant": bool(a.is_shariah_compliant),
                }
            )

        def _compute_movers_sync(recs: list[dict[str, Any]], prev_val: float) -> dict[str, Any]:
            df = pd.DataFrame(recs)
            total_w = float(df["weight_basis"].sum()) or 1.0
            df["weight_pct"] = (df["weight_basis"] / total_w) * 100.0
            # Apply PSX's 8% single-constituent index weight cap on full index universes
            if len(df) >= 20:
                for _ in range(4):
                    over = df["weight_pct"] > 8.0
                    if not over.any():
                        break
                    excess = float((df.loc[over, "weight_pct"] - 8.0).sum())
                    df.loc[over, "weight_pct"] = 8.0
                    under_sum = float(df.loc[~over, "weight_pct"].sum())
                    if under_sum > 0:
                        df.loc[~over, "weight_pct"] = df.loc[~over, "weight_pct"] + excess * (
                            df.loc[~over, "weight_pct"] / under_sum
                        )
            df["points"] = prev_val * (df["weight_pct"] / 100.0) * (df["change_pct"] / 100.0)

            pos_df = df[df["points"] > 0].sort_values(by="points", ascending=False).head(8)
            neg_df = df[df["points"] < 0].sort_values(by="points", ascending=True).head(8)

            def _fmt(sub: pd.DataFrame) -> list[dict[str, Any]]:
                return [
                    {
                        "symbol": str(r["symbol"]),
                        "name": str(r["name"]),
                        "sector": str(r["sector"]),
                        "last_price": float(r["last_price"]),
                        "change_pct": float(r["change_pct"]),
                        "weight_pct": round(float(r["weight_pct"]), 2),
                        "points": round(float(r["points"]), 2),
                        "is_shariah_compliant": bool(r["is_shariah_compliant"]),
                    }
                    for r in sub.to_dict(orient="records")
                ]

            sec_rows: list[dict[str, Any]] = []
            for sec_name, grp in df.groupby("sector"):
                sec_pts = float(grp["points"].sum())
                sec_rows.append(
                    {
                        "sector": str(sec_name),
                        "points": round(sec_pts, 2),
                        "weight_pct": round(float(grp["weight_pct"].sum()), 2),
                        "stock_count": int(len(grp)),
                    }
                )
            sec_rows.sort(key=lambda x: abs(x["points"]), reverse=True)

            return {
                "constituents_evaluated": int(len(df)),
                "advancers": int((df["change_pct"] > 0).sum()),
                "decliners": int((df["change_pct"] < 0).sum()),
                "unchanged": int((df["change_pct"] == 0).sum()),
                "top_positive": _fmt(pos_df),
                "top_negative": _fmt(neg_df),
                "sector_attribution": sec_rows[:10],
            }

        computed = await asyncio.to_thread(_compute_movers_sync, rows, idx_prev)
        return {
            "index_symbol": idx_u,
            "index_value": round(idx_price, 2),
            "index_change_pts": idx_chg_pts,
            "index_change_pct": round(idx_chg_pct, 2),
            **computed,
        }

    return await api_cache.get_or_set(f"v10:index_movers:{idx_u}", 60.0, _load)
