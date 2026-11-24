"""
Institutional Market Analytics, Technical/Fundamental Scanner, Mutual Fund Risk Engine,
SBP Macro Hub & Multi-Asset Strategy Backtester Router (`/api/v1/market/...`).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_optional_user, get_usd_to_pkr_rate, resolve_asset_by_symbol
from app.core.cache import api_cache
from app.core.database import get_db
from app.models.market import (
    Asset,
    EquityFinancialPeriod,
    EquityFinancialSnapshot,
    FundFmrSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
)
from app.models.user import User
from app.scrapers.amc_navs import parse_nav_history_upload_bytes
from app.scrapers.financial_reports import (
    parse_company_report_pdf_bytes_sync,
    seed_periods_from_psx_profile,
    upsert_financial_periods,
)
from app.scrapers.fmr_parser import ingest_fmr_pdf_into_db
from app.scrapers.sbp_macro import SbpMacroScraper, get_live_risk_free_rate_pct
from app.schemas.market import (
    BacktestRequest,
    DataCoverageOut,
    FundAnalyticsOut,
    MacroIndicatorOut,
    ScreenerScanRowOut,
)
from app.services.backtester import compute_monte_carlo_and_stress, run_strategy_backtest
from app.services.pandas_analytics import (
    analyze_financial_statements_pandas,
    compute_market_heatmap_pandas,
    financial_periods_to_csv,
)
from app.services.quant import (
    compute_fundamental_analysis,
    compute_fund_risk_analytics,
    compute_screener_technicals_fast,
)
from app.services.total_return import build_adjusted_bars

router = APIRouter(prefix="/api/v1/market", tags=["analytics"])
D = Decimal


def _snap_to_dict(s: EquityFinancialSnapshot | None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {
        "fiscal_period": s.fiscal_period,
        "bvps": float(s.bvps) if s.bvps is not None else None,
        "roe_pct": float(s.roe_pct) if s.roe_pct is not None else None,
        "roa_pct": float(s.roa_pct) if s.roa_pct is not None else None,
        "roic_pct": float(s.roic_pct) if s.roic_pct is not None else None,
        "operating_margin_pct": float(s.operating_margin_pct) if s.operating_margin_pct is not None else None,
        "dcf_intrinsic_value": float(s.dcf_intrinsic_value) if s.dcf_intrinsic_value is not None else None,
        "piotroski_f_score": s.piotroski_f_score,
        "altman_z_score": float(s.altman_z_score) if s.altman_z_score is not None else None,
        "free_cashflow_mn": float(s.free_cashflow_mn) if s.free_cashflow_mn is not None else None,
        "total_debt_mn": float(s.total_debt_mn) if s.total_debt_mn is not None else None,
        "current_ratio": float(s.current_ratio) if s.current_ratio is not None else None,
        "debt_to_equity": float(s.debt_to_equity) if s.debt_to_equity is not None else None,
    }


def _prof_to_dict(p: PsxProfile | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "exchange": p.exchange,
        "country": p.country,
        "default_wht_rate_pct": p.default_wht_rate_pct,
        "market_cap_mn": p.market_cap_mn,
        "free_float_pct": p.free_float_pct,
        "pe_ratio": p.pe_ratio,
        "pb_ratio": p.pb_ratio,
        "eps": p.eps,
        "roe_pct": p.roe_pct,
        "dividend_yield_pct": p.dividend_yield_pct,
        "high_52w": p.high_52w,
        "low_52w": p.low_52w,
        "is_kse100": p.is_kse100,
        "is_kmi30": p.is_kmi30,
        "financial_summary_json": p.financial_summary_json,
    }


@router.get("/macro", response_model=list[MacroIndicatorOut])
async def get_macro_indicators(db: AsyncSession = Depends(get_db)):
    """Return live SBP Policy Rate, KIBOR, T-Bill, PIB yields, PBS Monthly CPI & Basket, SBP External & US 10Y curve."""
    async def _load() -> list[MacroIndicatorOut]:
        rows = (
            await db.execute(select(MacroIndicator).order_by(MacroIndicator.category.asc(), MacroIndicator.code.asc()))
        ).scalars().all()
        return [
            MacroIndicatorOut(
                code=r.code,
                name=r.name,
                country=r.country,
                category=r.category,
                value=r.value,
                unit=r.unit,
                as_of_date=r.as_of_date,
                source=r.source,
                history=list(r.history_json or []),
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    return await api_cache.get_or_set("macro:list", 120.0, _load)


@router.post("/macro/refresh")
async def refresh_macro_indicators(db: AsyncSession = Depends(get_db)):
    """Trigger immediate live sync from PBS IMF-SDMX CPI + SBP EasyData/EcoData (Rates, FX Reserves, Remittances, BOP)."""
    count = await SbpMacroScraper().run(db)
    api_cache.invalidate_prefix("macro")
    api_cache.invalidate_prefix("overview")
    api_cache.invalidate_prefix("coverage")
    return {"ok": True, "indicators_updated": count}


@router.get("/macro/regime")
async def get_macro_regime_history(db: AsyncSession = Depends(get_db)):
    """
    Return aligned multi-year Pakistan Macro Regime history:
    SBP Policy Rate vs PBS Monthly Pakistan CPI YoY (True Monthly Real Interest Rate spread),
    US 10Y Treasury, USD/PKR FX depreciation, and Gold 24K (PKR per 10g).
    """
    async def _load() -> dict[str, Any]:
        macros = {
            m.code: m
            for m in (
                await db.execute(
                    select(MacroIndicator).where(
                        MacroIndicator.code.in_(
                            [
                                "SBP_POLICY_RATE",
                                "PK_CPI_YOY",
                                "PK_CPI_MOM",
                                "US_10Y_TREASURY",
                                "SBP_FX_RESERVES_BN",
                                "SBP_REMITTANCES_MN",
                                "SBP_CURRENT_ACCOUNT_MN",
                            ]
                        )
                    )
                )
            ).scalars()
        }
        pr_hist = sorted(
            [
                (str(p["date"])[:7], float(p["value"]))
                for p in (macros["SBP_POLICY_RATE"].history_json or [])
                if isinstance(p, dict) and p.get("date") and p.get("value") is not None
            ]
            if "SBP_POLICY_RATE" in macros
            else [],
            key=lambda x: x[0],
        )

        # Prefer exact monthly YYYY-MM CPI points from PBS SDMX; fall back to annual World Bank map if sparse
        cpi_by_ym: dict[str, float] = {}
        cpi_by_year: dict[int, float] = {}
        if "PK_CPI_YOY" in macros:
            for p in (macros["PK_CPI_YOY"].history_json or []):
                if not isinstance(p, dict) or p.get("value") is None:
                    continue
                dt_raw = str(p.get("month") or p.get("date") or "")
                if len(dt_raw) >= 7:
                    ym_k = dt_raw[:7]
                    val_f = float(p["value"])
                    cpi_by_ym[ym_k] = val_f
                    if dt_raw[:4].isdigit():
                        cpi_by_year[int(dt_raw[:4])] = val_f
        use_monthly_cpi = len(cpi_by_ym) >= 12
        latest_cpi_ym = max(cpi_by_ym.keys()) if cpi_by_ym else None

        us10_by_ym = (
            {
                str(p["date"])[:7]: float(p["value"])
                for p in (macros["US_10Y_TREASURY"].history_json or [])
                if isinstance(p, dict) and p.get("date") and p.get("value") is not None
            }
            if "US_10Y_TREASURY" in macros
            else {}
        )

        fx_gold_assets = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.symbol.in_(["USD_PKR_INTERBANK", "USD_PKR_OPEN", "GOLD_24K_GRAM"]),
                    )
                )
            ).scalars()
        }
        fx_by_ym: dict[str, float] = {}
        gold_by_ym: dict[str, float] = {}
        fx_asset = fx_gold_assets.get("USD_PKR_INTERBANK") or fx_gold_assets.get("USD_PKR_OPEN")
        if fx_asset is not None:
            fx_rows = (
                await db.execute(
                    select(PriceBar.bar_time, PriceBar.close)
                    .where(PriceBar.asset_id == fx_asset.id, PriceBar.timeframe == "1D")
                    .order_by(PriceBar.bar_time.asc())
                )
            ).all()
            for bt, cl in fx_rows:
                if float(cl) > 0:
                    fx_by_ym[bt.strftime("%Y-%m")] = round(float(cl), 2)
        gold_asset = fx_gold_assets.get("GOLD_24K_GRAM")
        if gold_asset is not None:
            g_rows = (
                await db.execute(
                    select(PriceBar.bar_time, PriceBar.close)
                    .where(PriceBar.asset_id == gold_asset.id, PriceBar.timeframe == "1D")
                    .order_by(PriceBar.bar_time.asc())
                )
            ).all()
            for bt, cl in g_rows:
                if float(cl) > 0:
                    gold_by_ym[bt.strftime("%Y-%m")] = round(float(cl) * 10.0, 0)

        today = date.today()
        months: list[str] = []
        y, m = today.year - 5, today.month
        while (y, m) <= (today.year, today.month):
            months.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m = 1
                y += 1

        series: list[dict[str, Any]] = []
        last_pr: float | None = None
        last_cpi_monthly: float | None = None
        last_us10: float | None = None
        last_fx: float | None = None
        last_gold: float | None = None
        latest_cpi_yr = max(cpi_by_year.keys()) if cpi_by_year else None

        for ym in months:
            for d_ym, val in pr_hist:
                if d_ym <= ym:
                    last_pr = val

            cpi_val: float | None = None
            if use_monthly_cpi:
                if ym in cpi_by_ym:
                    last_cpi_monthly = cpi_by_ym[ym]
                    cpi_val = last_cpi_monthly
                elif latest_cpi_ym is not None and ym > latest_cpi_ym and last_cpi_monthly is not None:
                    # Carry forward latest released PBS month (e.g. Oct into Nov before release day)
                    cpi_val = last_cpi_monthly
            else:
                yr_num = int(ym[:4])
                cpi_val = cpi_by_year.get(yr_num)
                if cpi_val is None and latest_cpi_yr is not None and yr_num > latest_cpi_yr:
                    cpi_val = cpi_by_year[latest_cpi_yr]

            if ym in us10_by_ym:
                last_us10 = us10_by_ym[ym]
            if ym in fx_by_ym:
                last_fx = fx_by_ym[ym]
            if ym in gold_by_ym:
                last_gold = gold_by_ym[ym]
            real_rate = round(last_pr - cpi_val, 2) if (last_pr is not None and cpi_val is not None) else None
            if any(v is not None for v in (last_pr, cpi_val, last_fx, last_gold)):
                series.append(
                    {
                        "month": ym,
                        "policy_rate_pct": last_pr,
                        "cpi_yoy_pct": cpi_val,
                        "real_rate_pct": real_rate,
                        "us_10y_pct": last_us10,
                        "usd_pkr": last_fx,
                        "gold_10g_pkr": last_gold,
                    }
                )

        return {
            "latest_policy_rate_pct": float(macros["SBP_POLICY_RATE"].value) if "SBP_POLICY_RATE" in macros else None,
            "policy_rate_as_of_date": macros["SBP_POLICY_RATE"].as_of_date.isoformat() if "SBP_POLICY_RATE" in macros else None,
            "latest_cpi_yoy_pct": float(macros["PK_CPI_YOY"].value) if "PK_CPI_YOY" in macros else None,
            "latest_cpi_mom_pct": float(macros["PK_CPI_MOM"].value) if "PK_CPI_MOM" in macros else None,
            "cpi_as_of_date": macros["PK_CPI_YOY"].as_of_date.isoformat() if "PK_CPI_YOY" in macros else None,
            "cpi_source": macros["PK_CPI_YOY"].source if "PK_CPI_YOY" in macros else None,
            "cpi_frequency": "MONTHLY" if use_monthly_cpi else "ANNUAL",
            "latest_real_rate_pct": (
                round(float(macros["SBP_POLICY_RATE"].value) - float(macros["PK_CPI_YOY"].value), 2)
                if "SBP_POLICY_RATE" in macros and "PK_CPI_YOY" in macros
                else None
            ),
            "sbp_fx_reserves_bn": float(macros["SBP_FX_RESERVES_BN"].value) if "SBP_FX_RESERVES_BN" in macros else None,
            "remittances_mn": float(macros["SBP_REMITTANCES_MN"].value) if "SBP_REMITTANCES_MN" in macros else None,
            "current_account_mn": float(macros["SBP_CURRENT_ACCOUNT_MN"].value) if "SBP_CURRENT_ACCOUNT_MN" in macros else None,
            "series": series,
        }

    return await api_cache.get_or_set("macro:regime", 120.0, _load)


@router.get("/coverage", response_model=DataCoverageOut)
async def get_data_coverage(db: AsyncSession = Depends(get_db)):
    """Report live DB coverage across PSX EOD history, PUCARS/.KA deep fundamentals, AMCs, 5Y cross-asset & SBP Macro."""
    async def _load() -> DataCoverageOut:
        psx_ids = list(
            (
                await db.execute(
                    select(Asset.id).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF"]),
                    )
                )
            ).scalars()
        )
        fund_ids = list(
            (
                await db.execute(
                    select(Asset.id).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                    )
                )
            ).scalars()
        )
        intl_cnt = (
            await db.execute(
                select(func.count(Asset.id)).where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class.in_(["INTL_EQUITY", "INTL_ETF", "INTL_INDEX"]),
                )
            )
        ).scalar_one()

        psx_hist_cnt = (
            await db.execute(
                select(func.count())
                .select_from(
                    select(PriceBar.asset_id)
                    .where(PriceBar.timeframe == "1D", PriceBar.asset_id.in_(psx_ids))
                    .group_by(PriceBar.asset_id)
                    .having(func.count(PriceBar.id) >= 30)
                    .subquery()
                )
            )
        ).scalar_one() if psx_ids else 0

        fund_hist_asset_ids = list(
            (
                await db.execute(
                    select(PriceBar.asset_id)
                    .where(PriceBar.timeframe == "1D", PriceBar.asset_id.in_(fund_ids))
                    .group_by(PriceBar.asset_id)
                    .having(func.count(PriceBar.id) >= 10)
                )
            ).scalars()
        ) if fund_ids else []

        amcs_with_hist = (
            await db.execute(
                select(func.count(func.distinct(MutualFundProfile.amc_name))).where(
                    MutualFundProfile.asset_id.in_(fund_hist_asset_ids)
                )
            )
        ).scalar_one() if fund_hist_asset_ids else 0

        psx_deep_cnt = (
            await db.execute(
                select(func.count(EquityFinancialSnapshot.id)).where(
                    EquityFinancialSnapshot.asset_id.in_(psx_ids),
                    EquityFinancialSnapshot.total_assets_mn.isnot(None),
                )
            )
        ).scalar_one() if psx_ids else 0
        psx_report_periods_cnt = (
            await db.execute(
                select(func.count(EquityFinancialPeriod.id)).where(
                    EquityFinancialPeriod.asset_id.in_(psx_ids)
                )
            )
        ).scalar_one() if psx_ids else 0

        all_psx_profs = (
            await db.execute(select(PsxProfile).where(PsxProfile.asset_id.in_(psx_ids)))
        ).scalars().all() if psx_ids else []
        psx_multi_yr_cnt = sum(1 for p in all_psx_profs if p.financial_summary_json and len(p.financial_summary_json) > 0)
        kse100_ids = [p.asset_id for p in all_psx_profs if p.is_kse100 or p.is_kmi30]
        kse100_deep_cnt = (
            await db.execute(
                select(func.count(EquityFinancialSnapshot.id)).where(
                    EquityFinancialSnapshot.asset_id.in_(kse100_ids),
                    EquityFinancialSnapshot.total_assets_mn.isnot(None),
                )
            )
        ).scalar_one() if kse100_ids else 0

        crypto_ids = list((await db.execute(select(Asset.id).where(Asset.owner_user_id.is_(None), Asset.asset_class == "CRYPTO"))).scalars())
        fx_gold_ids = list((await db.execute(select(Asset.id).where(Asset.owner_user_id.is_(None), Asset.asset_class.in_(["FX", "GOLD"])))).scalars())
        crypto_5y = (
            await db.execute(
                select(func.count()).select_from(
                    select(PriceBar.asset_id).where(PriceBar.timeframe == "1D", PriceBar.asset_id.in_(crypto_ids)).group_by(PriceBar.asset_id).having(func.count(PriceBar.id) >= 400).subquery()
                )
            )
        ).scalar_one() if crypto_ids else 0
        fx_gold_5y = (
            await db.execute(
                select(func.count()).select_from(
                    select(PriceBar.asset_id).where(PriceBar.timeframe == "1D", PriceBar.asset_id.in_(fx_gold_ids)).group_by(PriceBar.asset_id).having(func.count(PriceBar.id) >= 400).subquery()
                )
            )
        ).scalar_one() if fx_gold_ids else 0

        amc_count = (await db.execute(select(func.count(func.distinct(MutualFundProfile.amc_name))))).scalar_one()
        funds_with_fmr = (
            await db.execute(
                select(func.count(FundFmrSnapshot.id)).where(FundFmrSnapshot.asset_id.in_(fund_ids))
            )
        ).scalar_one() if fund_ids else 0
        macro_cnt = (await db.execute(select(func.count(MacroIndicator.id)))).scalar_one()
        total_bars = (await db.execute(select(func.count(PriceBar.id)))).scalar_one()

        rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
        rf_usd = float(await get_live_risk_free_rate_pct(db, "USD"))

        return DataCoverageOut(
            psx_total=len(psx_ids),
            psx_with_history=int(psx_hist_cnt),
            psx_deep_fundamentals=int(psx_deep_cnt),
            psx_financial_report_periods=int(psx_report_periods_cnt),
            psx_multi_year_profiles=int(psx_multi_yr_cnt),
            kse100_total=len(kse100_ids),
            kse100_deep_fundamentals=int(kse100_deep_cnt),
            funds_total=len(fund_ids),
            funds_with_history=len(fund_hist_asset_ids),
            funds_with_fmr=int(funds_with_fmr),
            amc_count=int(amc_count),
            amcs_with_history=int(amcs_with_hist),
            intl_total=int(intl_cnt),
            crypto_with_5y=int(crypto_5y),
            fx_gold_with_5y=int(fx_gold_5y),
            macro_indicators=int(macro_cnt),
            total_price_bars=int(total_bars),
            risk_free_rate_pkr_pct=rf_pkr,
            risk_free_rate_usd_pct=rf_usd,
        )

    return await api_cache.get_or_set("coverage:all", 90.0, _load)


def _compute_screener_universe_sync(
    items: list[dict[str, Any]],
    bars_map: dict[str, list[dict[str, Any]]],
    sector_med_pe_map: dict[tuple[str, str], float],
    rf_pkr: float,
    rf_usd: float,
) -> list[ScreenerScanRowOut]:
    """Pure CPU-bound screener scoring executed in a worker thread & cached for 300s."""
    rows_out: list[ScreenerScanRowOut] = []
    cur_yr = date.today().year

    for it in items:
        aid = it["id"]
        bars_list = bars_map.get(aid, [])
        mcap_mn: Decimal = it["market_cap_mn"]
        pe_val: Decimal | None = it["pe_ratio"]
        pb_val: Decimal | None = it["pb_ratio"]
        div_y: Decimal = it["dividend_yield_pct"]
        roe_val: float | None = it["roe_pct"]
        sec_med_pe = sector_med_pe_map.get((it["asset_class"], it["sector_or_category"] or ""))
        pe_vs_sec = (
            round(((float(pe_val) / sec_med_pe) - 1.0) * 100.0, 1)
            if (pe_val is not None and float(pe_val) > 0 and sec_med_pe and sec_med_pe > 0)
            else None
        )

        div_years: list[int] = it["div_years"]
        streak = 0
        if div_years and div_years[-1] >= cur_yr - 1:
            y_ptr = div_years[-1]
            y_set = set(div_years)
            while y_ptr in y_set:
                streak += 1
                y_ptr -= 1

        rf = rf_usd if (it["currency"] or "PKR").upper() in {"USD", "EUR", "GBP"} else rf_pkr
        fund = compute_fundamental_analysis(
            asset={
                "symbol": it["symbol"],
                "name": it["name"],
                "asset_class": it["asset_class"],
                "currency": it["currency"],
                "last_price": float(it["last_price"]),
            },
            profile=it["prof_dict"],
            shariah=it["shariah_dict"],
            risk_free_rate_pct=rf,
            deep_snapshot=it["snap_dict"],
        )
        q_score = int(fund["quality_score"])
        f_score = it["piotroski_f_score"]

        tech = compute_screener_technicals_fast(bars_list[-260:])
        latest = tech.get("latest") or {}
        overall_sig = str(tech.get("overall_signal") or "NEUTRAL")
        rsi_val = latest.get("rsi_14")
        sma50 = latest.get("sma_50")
        sma200 = latest.get("sma_200")
        golden_x = bool(latest.get("golden_cross"))
        st_sig = latest.get("supertrend_signal")
        px_f = float(it["last_price"])

        dcf_upside: float | None = None
        dcf_iv = it["dcf_intrinsic_value"]
        if dcf_iv and px_f > 0:
            dcf_upside = round(((dcf_iv - px_f) / px_f) * 100.0, 2)

        rows_out.append(
            ScreenerScanRowOut(
                symbol=it["symbol"],
                name=it["name"],
                asset_class=it["asset_class"],
                currency=it["currency"],
                sector_or_category=it["sector_or_category"],
                is_shariah_compliant=it["is_shariah_compliant"],
                last_price=it["last_price"],
                change_pct=it["change_pct"],
                volume=it["volume"],
                market_cap_mn=mcap_mn,
                pe_ratio=pe_val,
                pb_ratio=pb_val,
                roe_pct=round(roe_val, 2) if roe_val is not None else None,
                sector_median_pe=sec_med_pe,
                pe_vs_sector_pct=pe_vs_sec,
                is_kse100=it["is_kse100"],
                is_kmi30=it["is_kmi30"],
                div_streak_years=streak,
                deep_source=it["deep_source"],
                dividend_yield_pct=div_y,
                quality_score=q_score,
                piotroski_f_score=f_score,
                dcf_upside_pct=dcf_upside,
                rsi_14=rsi_val,
                adx_14=latest.get("adx_14"),
                macd_hist=latest.get("macd_hist"),
                supertrend_signal=st_sig,
                golden_cross=golden_x,
                above_sma_50=bool(sma50 is not None and px_f > sma50),
                above_sma_200=bool(sma200 is not None and px_f > sma200),
                return_1m_pct=latest.get("return_1m_pct"),
                return_3m_pct=latest.get("return_3m_pct"),
                return_1y_tr_pct=latest.get("return_1y_pct"),
                overall_signal=overall_sig,
                bar_count=len(bars_list),
            )
        )
    return rows_out


@router.get("/screener/scan", response_model=list[ScreenerScanRowOut])
async def cross_market_technical_screener(
    market: str = Query(default="ALL", description="ALL | PSX | INTL"),
    q: str = Query(default=""),
    shariah_only: bool = Query(default=False),
    index_only: str = Query(default="ALL", description="ALL | KSE100 | KMI30"),
    below_sector_pe: bool = Query(default=False),
    min_roe_pct: float | None = Query(default=None),
    min_div_streak: int | None = Query(default=None, ge=0, le=10),
    signal: str | None = Query(default=None, description="BULLISH | BEARISH | NEUTRAL"),
    rsi_min: float | None = Query(default=None, ge=0, le=100),
    rsi_max: float | None = Query(default=None, ge=0, le=100),
    golden_cross_only: bool = Query(default=False),
    supertrend_bull_only: bool = Query(default=False),
    min_piotroski: int | None = Query(default=None, ge=0, le=9),
    min_quality_score: int | None = Query(default=None, ge=0, le=100),
    min_dividend_yield: float | None = Query(default=None, ge=0),
    max_pe: float | None = Query(default=None, gt=0),
    sort_by: str = Query(default="quality_score"),
    limit: int = Query(default=80, ge=1, le=250),
    db: AsyncSession = Depends(get_db),
):
    """Cross-market PSX + Global Technical, Sector-Relative & Fundamental Scanner (`ta`, DCF, Piotroski F-Score)."""
    async def _load_universe() -> list[ScreenerScanRowOut]:
        all_classes = ["PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"]
        stmt = (
            select(Asset)
            .options(
                selectinload(Asset.psx_profile),
                selectinload(Asset.shariah_metric),
                selectinload(Asset.financial_snapshot),
                selectinload(Asset.corporate_actions),
            )
            .where(
                Asset.owner_user_id.is_(None),
                Asset.asset_class.in_(all_classes),
                Asset.last_price > 0,
            )
        )
        candidates = (await db.execute(stmt)).scalars().all()
        if not candidates:
            return []

        sector_pe_buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        for ca_asset in candidates:
            if ca_asset.psx_profile and ca_asset.psx_profile.pe_ratio:
                pf = float(ca_asset.psx_profile.pe_ratio)
                if 0 < pf < 150:
                    sector_pe_buckets[(ca_asset.asset_class, ca_asset.sector_or_category or "")].append(pf)
        sector_med_pe_map = {
            k: round(median(v), 2) for k, v in sector_pe_buckets.items() if len(v) >= 2
        }

        rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
        rf_usd = float(await get_live_risk_free_rate_pct(db, "USD"))

        cutoff = datetime.now(timezone.utc) - timedelta(days=420)
        asset_ids = [a.id for a in candidates]
        bars_map: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for idx in range(0, len(asset_ids), 400):
            chunk_ids = asset_ids[idx : idx + 400]
            bar_stmt = (
                select(
                    PriceBar.asset_id,
                    PriceBar.bar_time,
                    PriceBar.open,
                    PriceBar.high,
                    PriceBar.low,
                    PriceBar.close,
                    PriceBar.volume,
                )
                .where(
                    PriceBar.asset_id.in_(chunk_ids),
                    PriceBar.timeframe == "1D",
                    PriceBar.bar_time >= cutoff,
                )
                .order_by(PriceBar.bar_time.asc())
            )
            for aid, bt, op, hi, lo, cl, vol in (await db.execute(bar_stmt)).all():
                bars_map[aid].append(
                    {
                        "bar_time": bt,
                        "open": op,
                        "high": hi,
                        "low": lo,
                        "close": cl,
                        "volume": vol,
                    }
                )

        plain_items: list[dict[str, Any]] = []
        for a in candidates:
            prof = a.psx_profile
            snap = a.financial_snapshot
            sh = a.shariah_metric
            pb_val = prof.pb_ratio if prof and prof.pb_ratio and prof.pb_ratio > 0 else None
            if pb_val is None and snap and snap.bvps and snap.bvps > 0 and a.last_price > 0:
                pb_val = (a.last_price / snap.bvps).quantize(D("0.01"))
            roe_val = (
                float(snap.roe_pct)
                if snap and snap.roe_pct is not None and -100 < float(snap.roe_pct) < 250
                else (float(prof.roe_pct) if prof and prof.roe_pct is not None and -100 < float(prof.roe_pct) < 250 else None)
            )
            div_years = sorted(
                {ca.ex_date.year for ca in a.corporate_actions if ca.action_type == "CASH_DIVIDEND" and ca.payout_per_share > 0}
            )
            plain_items.append(
                {
                    "id": a.id,
                    "symbol": a.symbol,
                    "name": a.name,
                    "asset_class": a.asset_class,
                    "currency": a.currency,
                    "sector_or_category": a.sector_or_category,
                    "is_shariah_compliant": a.is_shariah_compliant,
                    "last_price": a.last_price,
                    "change_pct": a.change_pct,
                    "volume": a.volume,
                    "market_cap_mn": prof.market_cap_mn if prof else D("0"),
                    "pe_ratio": prof.pe_ratio if prof else None,
                    "pb_ratio": pb_val,
                    "dividend_yield_pct": prof.dividend_yield_pct if prof else D("0"),
                    "roe_pct": roe_val,
                    "is_kse100": bool(prof and prof.is_kse100),
                    "is_kmi30": bool(prof and prof.is_kmi30),
                    "div_years": div_years,
                    "prof_dict": _prof_to_dict(prof),
                    "snap_dict": _snap_to_dict(snap),
                    "shariah_dict": {"passed": sh.passed, "debt_to_assets_pct": float(sh.debt_to_assets_pct)} if sh else None,
                    "piotroski_f_score": snap.piotroski_f_score if snap else None,
                    "dcf_intrinsic_value": float(snap.dcf_intrinsic_value) if snap and snap.dcf_intrinsic_value else None,
                    "deep_source": snap.source if snap else None,
                }
            )

        return await asyncio.to_thread(
            _compute_screener_universe_sync,
            plain_items,
            bars_map,
            sector_med_pe_map,
            rf_pkr,
            rf_usd,
        )

    universe = await api_cache.get_or_set("screener:universe", 300.0, _load_universe)

    mkt_u = (market or "ALL").upper()
    if mkt_u == "PSX":
        allowed_classes = {"PSX_EQUITY", "PSX_ETF"}
    elif mkt_u in {"INTL", "GLOBAL"}:
        allowed_classes = {"INTL_EQUITY", "INTL_ETF"}
    else:
        allowed_classes = {"PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"}

    q_l = q.strip().lower()
    sig_filter = signal.strip().upper() if signal and signal.strip().upper() not in {"ALL", ""} else None
    idx_filter = (index_only or "ALL").strip().upper()

    rows_out: list[ScreenerScanRowOut] = []
    for r in universe:
        if r.asset_class not in allowed_classes:
            continue
        if shariah_only and not r.is_shariah_compliant:
            continue
        if q_l and q_l not in r.symbol.lower() and q_l not in r.name.lower():
            continue
        if idx_filter == "KSE100" and not r.is_kse100:
            continue
        if idx_filter == "KMI30" and not r.is_kmi30:
            continue
        if min_dividend_yield is not None and float(r.dividend_yield_pct) < min_dividend_yield:
            continue
        if max_pe is not None and (r.pe_ratio is None or float(r.pe_ratio) > max_pe):
            continue
        if below_sector_pe and (r.pe_vs_sector_pct is None or r.pe_vs_sector_pct >= 0):
            continue
        if min_roe_pct is not None and (r.roe_pct is None or r.roe_pct < min_roe_pct):
            continue
        if min_div_streak is not None and r.div_streak_years < min_div_streak:
            continue
        if min_quality_score is not None and (r.quality_score or 0) < min_quality_score:
            continue
        if min_piotroski is not None and (r.piotroski_f_score is None or r.piotroski_f_score < min_piotroski):
            continue
        if sig_filter and r.overall_signal != sig_filter:
            continue
        if rsi_min is not None and (r.rsi_14 is None or r.rsi_14 < rsi_min):
            continue
        if rsi_max is not None and (r.rsi_14 is None or r.rsi_14 > rsi_max):
            continue
        if golden_cross_only and not r.golden_cross:
            continue
        if supertrend_bull_only and r.supertrend_signal != "BULL":
            continue
        rows_out.append(r)

    sort_keys = {
        "quality_score": lambda r: (r.quality_score or 0, float(r.market_cap_mn)),
        "rsi_14": lambda r: (r.rsi_14 if r.rsi_14 is not None else -1.0, float(r.volume)),
        "piotroski_f_score": lambda r: (r.piotroski_f_score or 0, r.quality_score or 0),
        "roe_pct": lambda r: (r.roe_pct if r.roe_pct is not None else -999.0),
        "div_streak_years": lambda r: (r.div_streak_years, float(r.dividend_yield_pct)),
        "dcf_upside_pct": lambda r: (r.dcf_upside_pct if r.dcf_upside_pct is not None else -9999.0),
        "dividend_yield_pct": lambda r: float(r.dividend_yield_pct),
        "return_1m_pct": lambda r: (r.return_1m_pct if r.return_1m_pct is not None else -999.0),
        "pe_ratio": lambda r: float(r.pe_ratio) if r.pe_ratio is not None else 99999.0,
        "market_cap_mn": lambda r: float(r.market_cap_mn),
    }
    key_fn = sort_keys.get(sort_by, sort_keys["quality_score"])
    rows_out.sort(key=key_fn, reverse=(sort_by != "pe_ratio"))
    return rows_out[:limit]


def _compute_fund_scatter_sync(
    fund_items: list[dict[str, Any]],
    bars_by_id: dict[str, list[tuple[datetime, float]]],
    rf_pkr: float,
) -> list[dict[str, Any]]:
    import math
    import numpy as np

    points: list[dict[str, Any]] = []
    for f in fund_items:
        series = bars_by_id.get(f["id"], [])
        if len(series) < 15:
            continue
        closes = np.array([px for _, px in series], dtype=np.float64)
        rets = np.diff(closes) / np.maximum(closes[:-1], 1e-9)
        rets = rets[np.isfinite(rets)]
        if len(rets) < 10:
            continue
        span_days = max(1, (series[-1][0] - series[0][0]).days)
        cagr = ((closes[-1] / closes[0]) ** (365.0 / max(span_days, 30)) - 1.0) * 100.0
        if f["is_daily_dividend"] and abs(cagr) < 1.5 and f["annualized_yield_pct"] and f["annualized_yield_pct"] > 0:
            cagr = f["annualized_yield_pct"]
        ann_vol = float(np.std(rets, ddof=1) * math.sqrt(252.0) * 100.0)
        sharpe = round((cagr - rf_pkr) / max(ann_vol, 0.25), 2) if ann_vol > 0.05 else None
        points.append(
            {
                "symbol": f["symbol"],
                "name": f["name"],
                "amc_name": f["amc_name"],
                "fund_category": f["fund_category"],
                "is_shariah_compliant": f["is_shariah_compliant"],
                "is_daily_dividend": f["is_daily_dividend"],
                "nav": f["nav"],
                "ter_pct": f["ter_pct"],
                "cagr_pct": round(float(cagr), 2),
                "volatility_pct": round(ann_vol, 2),
                "sharpe_ratio": sharpe,
                "bar_count": len(series),
            }
        )
    points.sort(key=lambda x: x["cagr_pct"], reverse=True)
    return points


@router.get("/funds/scatter")
async def mutual_funds_risk_return_scatter(
    shariah_only: bool = Query(default=False),
    category: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Compute Category Risk vs Return scatter points (Annualized Volatility % vs Realized Return % & Sharpe)
    across all mutual funds with >= 15 historical daily NAV bars.
    """
    async def _load_base() -> dict[str, Any]:
        rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
        stmt = (
            select(Asset)
            .options(selectinload(Asset.mutual_fund_profile))
            .where(
                Asset.owner_user_id.is_(None),
                Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]),
                Asset.last_price > 0,
            )
        )
        funds = (await db.execute(stmt)).scalars().all()
        if not funds:
            return {"risk_free_rate_pct": rf_pkr, "points": []}

        fund_ids = [f.id for f in funds]
        bars_by_id: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        for idx in range(0, len(fund_ids), 400):
            chunk = fund_ids[idx : idx + 400]
            rows = (
                await db.execute(
                    select(PriceBar.asset_id, PriceBar.bar_time, PriceBar.close)
                    .where(PriceBar.asset_id.in_(chunk), PriceBar.timeframe == "1D")
                    .order_by(PriceBar.bar_time.asc())
                )
            ).all()
            for aid, bt, cl in rows:
                fv = float(cl)
                if fv > 0:
                    bars_by_id[aid].append((bt, fv))

        fund_items = [
            {
                "id": f.id,
                "symbol": f.symbol,
                "name": f.name,
                "amc_name": f.mutual_fund_profile.amc_name,
                "fund_category": f.mutual_fund_profile.fund_category,
                "is_shariah_compliant": f.is_shariah_compliant,
                "is_daily_dividend": f.mutual_fund_profile.is_daily_dividend,
                "annualized_yield_pct": float(f.mutual_fund_profile.annualized_yield_pct) if f.mutual_fund_profile.annualized_yield_pct else 0.0,
                "nav": float(f.last_price),
                "ter_pct": float(f.mutual_fund_profile.ter_pct),
            }
            for f in funds
            if f.mutual_fund_profile is not None
        ]
        points = await asyncio.to_thread(_compute_fund_scatter_sync, fund_items, bars_by_id, rf_pkr)
        return {"risk_free_rate_pct": rf_pkr, "points": points}

    base = await api_cache.get_or_set("funds:scatter:base", 300.0, _load_base)
    pts = list(base["points"])
    if shariah_only:
        pts = [p for p in pts if p["is_shariah_compliant"]]
    if category and category.strip() and category.upper() != "ALL":
        cat_low = category.strip().lower()
        pts = [p for p in pts if cat_low in (p["fund_category"] or "").lower()]
    return {"risk_free_rate_pct": base["risk_free_rate_pct"], "count": len(pts), "points": pts}


@router.post("/funds/{symbol}/import-history")
async def import_mutual_fund_nav_history(
    symbol: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload CSV or XLSX historical NAV spreadsheet for any mutual fund / VPS pension fund
    (useful for AMCs such as Al Meezan / UBL / ABL that do not expose an open history API).
    """
    asset = await resolve_asset_by_symbol(
        symbol,
        db,
        options=[selectinload(Asset.mutual_fund_profile)],
    )
    if asset is None or asset.mutual_fund_profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Mutual fund '{symbol}' not found.")

    raw = await file.read()
    if not raw or len(raw) > 10_000_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty or exceeds 10MB limit.")

    parsed = parse_nav_history_upload_bytes(raw, filename=file.filename or "nav_history.csv")
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse any valid (Date, NAV) rows from the uploaded file. Expected columns: Date, NAV/Redemption.",
        )

    existing_dates = {
        (bt.date() if hasattr(bt, "date") else bt)
        for bt in (
            await db.execute(
                select(PriceBar.bar_time).where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
            )
        ).scalars()
    }
    inserted = 0
    for nav_dt, nav_px in parsed:
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
        inserted += 1

    # Refresh trailing realized returns from combined history
    prof = asset.mutual_fund_profile
    all_pts = sorted(parsed, key=lambda x: x[0])
    if len(all_pts) >= 15:
        latest_dt, latest_px = all_pts[-1]
        first_dt, first_px = all_pts[0]
        span_d = max(1, (latest_dt - first_dt).days)
        if first_px > 0 and span_d >= 150:
            cagr_1y = ((float(latest_px) / float(first_px)) ** (365.0 / span_d) - 1.0) * 100.0
            prof.return_1y_pct = Decimal(str(round(cagr_1y, 2)))

    await db.commit()
    api_cache.invalidate_prefix("funds:")
    api_cache.invalidate_prefix("coverage")
    return {
        "symbol": asset.symbol,
        "rows_parsed": len(parsed),
        "bars_inserted": inserted,
        "date_from": parsed[0][0].isoformat(),
        "date_to": parsed[-1][0].isoformat(),
    }


@router.post("/funds/fmr/upload")
async def upload_secp_fmr_pdf(
    file: UploadFile = File(...),
    symbol: str | None = Query(default=None, description="Optional target fund symbol for single-fund FMR PDFs"),
    amc_hint: str = Query(default="", description="Optional AMC name hint for consolidated FMR booklets"),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload ANY Pakistani AMC's SECP-standardized monthly Fund Manager Report (FMR) PDF
    (either a full multi-fund AMC booklet or a single-fund fact sheet) to parse & persist
    Asset Allocation, Top-10 Equity Holdings, Sector Mix, Credit Quality, WAM & TER breakdown.
    """
    raw = await file.read()
    if not raw or len(raw) > 25_000_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF file is empty or exceeds 25MB limit.")
    if not raw[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file must be a valid PDF document.")

    parsed = await ingest_fmr_pdf_into_db(
        db,
        raw,
        amc_hint=amc_hint,
        filename_hint=file.filename or "fmr.pdf",
        source_url=None,
        source_label=f"Uploaded FMR ({file.filename or 'PDF'})"[:64],
        target_symbol=symbol,
    )
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not match any registered mutual fund pages inside this FMR PDF. Try passing the fund symbol or uploading an AMC monthly FMR with selectable text.",
        )

    api_cache.invalidate_prefix("funds:")
    api_cache.invalidate_prefix("downloads:")
    api_cache.invalidate_prefix("coverage")
    api_cache.invalidate_prefix("stock:detail:")
    return {
        "matched_funds": len(parsed),
        "report_month": parsed[0]["report_month"],
        "report_label": parsed[0]["report_label"],
        "funds": [
            {
                "symbol": p["symbol"],
                "name": p["name"],
                "amc_name": p["amc_name"],
                "net_assets_mn": p["net_assets_mn"],
                "asset_allocation_count": len(p["asset_allocation_json"]),
                "top_holdings_count": len(p["top_holdings_json"]),
                "credit_quality_count": len(p["credit_quality_json"]),
            }
            for p in parsed
        ],
    }


@router.get("/funds/{symbol}/detail")
async def mutual_fund_institutional_detail(
    symbol: str,
    benchmark: str | None = Query(default=None),
    return_mode: str = Query(default="GROSS_TR"),
    db: AsyncSession = Depends(get_db),
):
    """
    Complete institutional Mutual Fund / VPS Fact Sheet for `/funds/[symbol]`:
    combines MutualFundProfile, SECP FMR composition (with live PSX quotes & implied P/E / yield
    across Top-10 holdings), Credit Quality / WAM, Fee Anatomy, Risk Analytics & Category Peers.
    """
    asset = await resolve_asset_by_symbol(
        symbol,
        db,
        options=[
            selectinload(Asset.mutual_fund_profile),
            selectinload(Asset.fmr_snapshot),
            selectinload(Asset.corporate_actions),
        ],
    )
    if asset is None or asset.mutual_fund_profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Mutual fund '{symbol}' not found.")

    prof = asset.mutual_fund_profile
    fmr = asset.fmr_snapshot
    rf_pkr_live = float(await get_live_risk_free_rate_pct(db, "PKR"))
    analytics_out = await mutual_fund_analytics(
        symbol=asset.symbol,
        benchmark=benchmark,
        return_mode=return_mode,
        db=db,
    )

    # Rich Daily Dividend Fund Income Profile & Compounded 1Y Wealth Curve (never ignore daily dividends!)
    eff_dd_yield = 0.0
    dd_yield_source = ""
    if prof.is_daily_dividend or "money market" in (prof.fund_category or "").lower() or "cash" in (prof.fund_category or "").lower():
        if prof.annualized_yield_pct and float(prof.annualized_yield_pct) > 0:
            eff_dd_yield = float(prof.annualized_yield_pct)
            dd_yield_source = "AMC Reported Yield"
        elif fmr and fmr.ytm_pct and float(fmr.ytm_pct) > 0:
            eff_dd_yield = float(fmr.ytm_pct)
            dd_yield_source = "SECP FMR YTM"
        elif prof.return_1y_pct and float(prof.return_1y_pct) > 0:
            eff_dd_yield = float(prof.return_1y_pct)
            dd_yield_source = "1Y Trailing Return"
        else:
            eff_dd_yield = round(max(6.0, rf_pkr_live - 1.5), 2)
            dd_yield_source = "SBP PKRV Money-Market Proxy"

    par_nav_f = float(prof.constant_par_nav or asset.last_price or 50.0)
    daily_dividend_profile: dict[str, Any] | None = None
    if prof.is_daily_dividend and eff_dd_yield > 0:
        r_gross = eff_dd_yield / 100.0
        r_filer = r_gross * 0.85
        r_nonfiler = r_gross * 0.70
        daily_per_unit = (par_nav_f * r_gross) / 365.0
        curve_1y: list[dict[str, Any]] = []
        today_d = date.today()
        for m in range(0, 13):
            days_elapsed = int(round(m * 30.4167))
            dt_pt = today_d - timedelta(days=365 - days_elapsed)
            curve_1y.append(
                {
                    "month": f"M{m}",
                    "date": dt_pt.isoformat(),
                    "gross_value": round(100_000.0 * ((1.0 + r_gross / 365.0) ** days_elapsed), 2),
                    "net_filer_value": round(100_000.0 * ((1.0 + r_filer / 365.0) ** days_elapsed), 2),
                    "net_non_filer_value": round(100_000.0 * ((1.0 + r_nonfiler / 365.0) ** days_elapsed), 2),
                    "flat_nav_only": 100_000.0,
                }
            )
        daily_dividend_profile = {
            "is_daily_dividend": True,
            "effective_annual_yield_pct": round(eff_dd_yield, 2),
            "yield_source": dd_yield_source,
            "par_nav": round(par_nav_f, 4),
            "default_payout_mode": prof.default_payout_mode or "REINVEST",
            "est_daily_payout_per_unit_pkr": round(daily_per_unit, 6),
            "per_100k_summary": {
                "daily_gross_pkr": round((100_000.0 * r_gross) / 365.0, 2),
                "daily_net_filer_pkr": round((100_000.0 * r_filer) / 365.0, 2),
                "daily_net_non_filer_pkr": round((100_000.0 * r_nonfiler) / 365.0, 2),
                "monthly_net_filer_cash_pkr": round((100_000.0 * r_filer) / 12.0, 2),
                "annual_gross_reinvest_pkr": round(100_000.0 * (((1.0 + r_gross / 365.0) ** 365) - 1.0), 2),
                "annual_net_filer_reinvest_pkr": round(100_000.0 * (((1.0 + r_filer / 365.0) ** 365) - 1.0), 2),
                "annual_net_non_filer_reinvest_pkr": round(100_000.0 * (((1.0 + r_nonfiler / 365.0) ** 365) - 1.0), 2),
                "compounding_bonus_filer_pkr": round(
                    100_000.0 * (((1.0 + r_filer / 365.0) ** 365) - 1.0 - r_filer), 2
                ),
            },
            "wealth_curve_1y": curve_1y,
        }

    fmr_payload: dict[str, Any] | None = None
    if fmr is not None:
        raw_holdings = list(fmr.top_holdings_json or [])
        hold_syms = [str(h.get("symbol")).upper() for h in raw_holdings if h.get("symbol")]
        live_psx_map: dict[str, Asset] = {}
        if hold_syms:
            psx_loaded = (
                await db.execute(
                    select(Asset)
                    .options(selectinload(Asset.psx_profile))
                    .where(Asset.owner_user_id.is_(None), Asset.symbol.in_(hold_syms))
                )
            ).scalars().all()
            live_psx_map = {a.symbol.upper(): a for a in psx_loaded}

        enriched_holdings: list[dict[str, Any]] = []
        w_pe_num = 0.0
        w_pe_den = 0.0
        w_dy_num = 0.0
        w_dy_den = 0.0
        for h in raw_holdings:
            sym = str(h.get("symbol") or "").upper()
            wt = float(h.get("weight_pct") or 0.0)
            pa = live_psx_map.get(sym)
            pp = pa.psx_profile if pa else None
            pe_f = float(pp.pe_ratio) if pp and pp.pe_ratio and 0 < float(pp.pe_ratio) < 120 else None
            dy_f = float(pp.dividend_yield_pct) if pp and pp.dividend_yield_pct and float(pp.dividend_yield_pct) > 0 else None
            if pe_f is not None and wt > 0:
                w_pe_num += pe_f * wt
                w_pe_den += wt
            if dy_f is not None and wt > 0:
                w_dy_num += dy_f * wt
                w_dy_den += wt
            est_mn = round((float(fmr.net_assets_mn) * wt) / 100.0, 2) if (fmr.net_assets_mn and wt > 0) else None
            enriched_holdings.append(
                {
                    "symbol": sym,
                    "name": pa.name if pa else h.get("name", sym),
                    "sector": pa.sector_or_category if pa else h.get("sector", ""),
                    "weight_pct": round(wt, 2),
                    "est_value_mn": est_mn,
                    "last_price": float(pa.last_price) if pa else None,
                    "change_pct": float(pa.change_pct) if pa else None,
                    "pe_ratio": round(pe_f, 2) if pe_f is not None else None,
                    "dividend_yield_pct": round(dy_f, 2) if dy_f is not None else None,
                    "is_shariah_compliant": bool(pa.is_shariah_compliant) if pa else None,
                }
            )

        fmr_payload = {
            "report_month": fmr.report_month,
            "report_label": fmr.report_label,
            "amc_name": fmr.amc_name,
            "fund_manager": fmr.fund_manager,
            "stability_rating": fmr.stability_rating,
            "amc_rating": fmr.amc_rating,
            "risk_profile": fmr.risk_profile or prof.risk_profile,
            "launch_date": fmr.launch_date,
            "benchmark_name": fmr.benchmark_name or prof.benchmark_name,
            "net_assets_mn": float(fmr.net_assets_mn) if fmr.net_assets_mn is not None else (float(prof.aum_mn) if prof.aum_mn else None),
            "wam_days": float(fmr.wam_days) if fmr.wam_days is not None else None,
            "duration_days": float(fmr.duration_days) if fmr.duration_days is not None else None,
            "ytm_pct": float(fmr.ytm_pct) if fmr.ytm_pct is not None else None,
            "ter_mtd_pct": float(fmr.ter_mtd_pct) if fmr.ter_mtd_pct is not None else None,
            "ter_ytd_pct": float(fmr.ter_ytd_pct) if fmr.ter_ytd_pct is not None else float(prof.ter_pct),
            "govt_levy_pct": float(fmr.govt_levy_pct) if fmr.govt_levy_pct is not None else None,
            "mgmt_fee_pct": float(fmr.mgmt_fee_pct) if fmr.mgmt_fee_pct is not None else float(prof.management_fee_pct),
            "asset_allocation": list(fmr.asset_allocation_json or []),
            "sector_allocation": list(fmr.sector_allocation_json or []),
            "top_holdings": enriched_holdings,
            "credit_quality": list(fmr.credit_quality_json or []),
            "returns_table": dict(fmr.returns_table_json or {}),
            "manager_commentary": fmr.manager_commentary,
            "implied_portfolio_pe": round(w_pe_num / w_pe_den, 2) if w_pe_den > 0 else None,
            "implied_portfolio_div_yield_pct": round(w_dy_num / w_dy_den, 2) if w_dy_den > 0 else None,
            "top_10_concentration_pct": round(sum(h["weight_pct"] for h in enriched_holdings), 2) if enriched_holdings else None,
            "source_url": fmr.source_url,
            "source_label": fmr.source_label,
            "updated_at": fmr.updated_at.isoformat(),
        }

    return {
        "fund": {
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_class": asset.asset_class,
            "is_shariah_compliant": asset.is_shariah_compliant,
            "last_price": str(asset.last_price),
            "change_pct": str(asset.change_pct),
            "amc_name": prof.amc_name,
            "fund_category": prof.fund_category,
            "risk_profile": prof.risk_profile,
            "ter_pct": str(prof.ter_pct),
            "management_fee_pct": str(prof.management_fee_pct),
            "front_end_load_pct": str(prof.front_end_load_pct),
            "back_end_load_pct": str(prof.back_end_load_pct),
            "aum_mn": str(prof.aum_mn),
            "is_daily_dividend": prof.is_daily_dividend,
            "constant_par_nav": str(prof.constant_par_nav) if prof.constant_par_nav is not None else None,
            "annualized_yield_pct": str(prof.annualized_yield_pct) if prof.annualized_yield_pct is not None else None,
            "default_payout_mode": prof.default_payout_mode,
            "return_1m_pct": str(prof.return_1m_pct) if prof.return_1m_pct is not None else None,
            "return_6m_pct": str(prof.return_6m_pct) if prof.return_6m_pct is not None else None,
            "return_1y_pct": str(prof.return_1y_pct) if prof.return_1y_pct is not None else None,
            "return_3y_pct": str(prof.return_3y_pct) if prof.return_3y_pct is not None else None,
            "benchmark_name": prof.benchmark_name,
        },
        "fmr": fmr_payload,
        "daily_dividend_profile": daily_dividend_profile,
        "analytics": analytics_out.model_dump(),
    }


@router.get("/funds/{symbol}/analytics", response_model=FundAnalyticsOut)
async def mutual_fund_analytics(
    symbol: str,
    benchmark: str | None = Query(default=None),
    return_mode: str = Query(default="GROSS_TR"),
    db: AsyncSession = Depends(get_db),
):
    """Risk-adjusted mutual fund metrics (Sharpe, Sortino, Alpha, Beta, Tracking Error, 5Y TER/FEL drag)."""
    asset = await resolve_asset_by_symbol(
        symbol,
        db,
        options=[
            selectinload(Asset.mutual_fund_profile),
            selectinload(Asset.fmr_snapshot),
            selectinload(Asset.corporate_actions),
        ],
    )
    if asset is None or asset.mutual_fund_profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Mutual fund '{symbol}' not found.")

    prof = asset.mutual_fund_profile
    rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
    bench_sym = (
        benchmark.strip().upper()
        if benchmark and benchmark.strip()
        else ("KMI30" if asset.is_shariah_compliant else "KSE100")
    )

    raw_bars = (
        await db.execute(
            select(PriceBar)
            .where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
            .order_by(PriceBar.bar_time.asc())
        )
    ).scalars().all()

    bars_in = [
        {
            "bar_time": b.bar_time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in raw_bars
    ]
    eff_yield = float(prof.annualized_yield_pct or 0)
    if prof.is_daily_dividend and eff_yield <= 0:
        if asset.fmr_snapshot and asset.fmr_snapshot.ytm_pct and float(asset.fmr_snapshot.ytm_pct) > 0:
            eff_yield = float(asset.fmr_snapshot.ytm_pct)
        elif prof.return_1y_pct and float(prof.return_1y_pct) > 0:
            eff_yield = float(prof.return_1y_pct)
        else:
            eff_yield = round(max(6.0, rf_pkr - 1.5), 2)

    # Constant-NAV daily dividend funds stay at par NAV (e.g. Rs 50); synthesize 1Y daily baseline when AMC has <15 bars
    # so `build_adjusted_bars` compounds daily dividend distributions cleanly into a real Total-Return wealth curve!
    if prof.is_daily_dividend and eff_yield > 0 and len(bars_in) < 15:
        par = float(prof.constant_par_nav or asset.last_price or 50.0)
        today_dt = date.today()
        bars_in = [
            {
                "bar_time": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                "open": par,
                "high": par,
                "low": par,
                "close": par,
                "volume": 0.0,
            }
            for offset in range(365, -1, -2)
            for d in [today_dt - timedelta(days=offset)]
        ]

    actions_in = [
        {
            "action_type": ca.action_type,
            "ex_date": ca.ex_date,
            "payout_per_share": float(ca.payout_per_share),
            "ratio_numerator": float(ca.ratio_numerator),
            "ratio_denominator": float(ca.ratio_denominator),
        }
        for ca in asset.corporate_actions
    ]
    adj_bars = build_adjusted_bars(
        bars_in,
        actions=actions_in,
        mode=return_mode,
        is_daily_dividend=prof.is_daily_dividend,
        annualized_yield_pct=eff_yield,
        is_intl=False,
        country="PK",
    )

    bench_asset = (
        await db.execute(select(Asset).where(Asset.symbol == bench_sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()
    bench_bars: list[dict[str, Any]] = []
    if bench_asset is not None:
        b_rows = (
            await db.execute(
                select(PriceBar)
                .where(PriceBar.asset_id == bench_asset.id, PriceBar.timeframe == "1D")
                .order_by(PriceBar.bar_time.asc())
            )
        ).scalars().all()
        bench_bars = [
            {"bar_time": b.bar_time, "close": b.close}
            for b in b_rows
        ]

    rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
    risk_metrics = compute_fund_risk_analytics(
        adj_bars,
        benchmark_bars=bench_bars,
        risk_free_rate_pct=rf_pkr,
        ter_pct=float(prof.ter_pct),
        front_end_load_pct=float(prof.front_end_load_pct),
    )

    # Category peers ranked by 1Y return (or low TER)
    peer_assets = (
        await db.execute(
            select(Asset, MutualFundProfile)
            .join(MutualFundProfile, MutualFundProfile.asset_id == Asset.id)
            .where(
                Asset.owner_user_id.is_(None),
                MutualFundProfile.fund_category == prof.fund_category,
            )
        )
    ).all()
    amc_peers = [
        {
            "symbol": pa.symbol,
            "name": pa.name,
            "amc_name": pm.amc_name,
            "nav": float(pa.last_price),
            "ter_pct": float(pm.ter_pct),
            "front_end_load_pct": float(pm.front_end_load_pct),
            "return_1y_pct": float(pm.return_1y_pct) if pm.return_1y_pct is not None else None,
            "annualized_yield_pct": float(pm.annualized_yield_pct) if pm.annualized_yield_pct is not None else None,
            "is_daily_dividend": pm.is_daily_dividend,
        }
        for pa, pm in peer_assets
    ]
    amc_peers.sort(
        key=lambda p: (
            p["return_1y_pct"] if p["return_1y_pct"] is not None else (p["annualized_yield_pct"] or -999.0),
            -p["ter_pct"],
        ),
        reverse=True,
    )

    return FundAnalyticsOut(
        symbol=asset.symbol,
        name=asset.name,
        amc_name=prof.amc_name,
        fund_category=prof.fund_category,
        is_shariah_compliant=asset.is_shariah_compliant,
        is_daily_dividend=prof.is_daily_dividend,
        benchmark_symbol=bench_sym,
        risk_free_rate_pct=rf_pkr,
        nav=asset.last_price,
        ter_pct=prof.ter_pct,
        front_end_load_pct=prof.front_end_load_pct,
        bar_count=len(raw_bars),
        total_return_1m_pct=risk_metrics["total_return_1m_pct"],
        total_return_6m_pct=risk_metrics["total_return_6m_pct"],
        total_return_1y_pct=risk_metrics["total_return_1y_pct"] or (float(prof.return_1y_pct) if prof.return_1y_pct is not None else None),
        cagr_pct=risk_metrics["cagr_pct"],
        annualized_volatility_pct=risk_metrics["annualized_volatility_pct"],
        sharpe_ratio=risk_metrics["sharpe_ratio"],
        sortino_ratio=risk_metrics["sortino_ratio"],
        calmar_ratio=risk_metrics["calmar_ratio"],
        max_drawdown_pct=risk_metrics["max_drawdown_pct"],
        alpha_annualized_pct=risk_metrics["alpha_annualized_pct"],
        beta=risk_metrics["beta"],
        tracking_error_pct=risk_metrics["tracking_error_pct"],
        information_ratio=risk_metrics["information_ratio"],
        fee_drag_5y_pct=risk_metrics["fee_drag_5y_pct"],
        nav_series=risk_metrics["nav_series"],
        amc_peers=amc_peers[:12],
    )


@router.post("/backtest")
async def run_portfolio_strategy_backtest(
    payload: BacktestRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Run Total-Return historical strategy backtest (Buy & Hold, Monthly SIP Rebalance,
    SMA 20/50 Crossover, RSI Mean Reversion) + 500-path GBM Monte Carlo & Macro Stress Test.
    """
    owner_id = user.id if user else None
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.lookback_days + 90)
    usd_to_pkr = float(await get_usd_to_pkr_rate(db))
    rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))

    legs_data: list[dict[str, Any]] = []
    stress_holdings: list[dict[str, Any]] = []
    total_wt = sum(leg.weight_pct for leg in payload.legs) or 100.0

    for leg in payload.legs:
        asset = await resolve_asset_by_symbol(
            leg.symbol,
            db,
            options=[
                selectinload(Asset.mutual_fund_profile),
                selectinload(Asset.psx_profile),
                selectinload(Asset.shariah_metric),
                selectinload(Asset.corporate_actions),
            ],
            owner_user_id=owner_id,
        )
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{leg.symbol}' not found.")

        raw_bars = (
            await db.execute(
                select(PriceBar)
                .where(
                    PriceBar.asset_id == asset.id,
                    PriceBar.timeframe == "1D",
                    PriceBar.bar_time >= cutoff,
                )
                .order_by(PriceBar.bar_time.asc())
            )
        ).scalars().all()
        if not raw_bars:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Asset '{asset.symbol}' does not have daily historical bars yet for backtesting.",
            )

        bars_in = [
            {
                "bar_time": b.bar_time,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in raw_bars
        ]
        actions_in = [
            {
                "action_type": ca.action_type,
                "ex_date": ca.ex_date,
                "payout_per_share": float(ca.payout_per_share),
                "ratio_numerator": float(ca.ratio_numerator),
                "ratio_denominator": float(ca.ratio_denominator),
            }
            for ca in asset.corporate_actions
        ]
        mf = asset.mutual_fund_profile
        is_intl = str(asset.asset_class or "").startswith("INTL_")
        country = asset.psx_profile.country if asset.psx_profile else ("US" if is_intl else "PK")
        purif = float(asset.shariah_metric.purification_rate_pct or 0) if asset.shariah_metric else 0.0

        adj_bars = build_adjusted_bars(
            bars_in,
            actions=actions_in,
            mode=payload.return_mode,
            is_daily_dividend=bool(mf and mf.is_daily_dividend),
            annualized_yield_pct=float(mf.annualized_yield_pct or 0) if mf else 0.0,
            is_intl=is_intl,
            country=country,
            purification_rate_pct=purif,
            apply_shariah_purification=payload.apply_shariah_purification,
        )
        legs_data.append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "asset_class": asset.asset_class,
                "currency": asset.currency,
                "weight_pct": leg.weight_pct,
                "front_end_load_pct": float(mf.front_end_load_pct or 0) if mf else 0.0,
                "adj_bars": adj_bars,
            }
        )
        stress_holdings.append(
            {
                "symbol": asset.symbol,
                "asset_class": asset.asset_class,
                "currency": asset.currency,
                "market_value_pkr": payload.initial_capital_pkr * (leg.weight_pct / total_wt),
            }
        )

    bench_sym = (payload.benchmark_symbol or "KSE100").strip().upper()
    bench_asset = (
        await db.execute(select(Asset).where(Asset.symbol == bench_sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()
    bench_adj: list[dict[str, Any]] = []
    if bench_asset is not None:
        b_rows = (
            await db.execute(
                select(PriceBar)
                .where(
                    PriceBar.asset_id == bench_asset.id,
                    PriceBar.timeframe == "1D",
                    PriceBar.bar_time >= cutoff,
                )
                .order_by(PriceBar.bar_time.asc())
            )
        ).scalars().all()
        bench_adj = build_adjusted_bars(
            [{"bar_time": b.bar_time, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in b_rows],
            mode="GROSS_TR",
        )

    # Load historical USD/PKR bars & Pakistan CPI YoY for Real-Return USD / CPI-adjusted backtests
    usd_pkr_bars: list[dict[str, Any]] = []
    if payload.real_return_mode == "REAL_USD":
        fx_asset = (
            await db.execute(
                select(Asset).where(
                    Asset.owner_user_id.is_(None),
                    Asset.symbol.in_(["USD_PKR_INTERBANK", "USD_PKR_OPEN"]),
                )
            )
        ).scalars().first()
        if fx_asset is not None:
            fx_rows = (
                await db.execute(
                    select(PriceBar.bar_time, PriceBar.close)
                    .where(
                        PriceBar.asset_id == fx_asset.id,
                        PriceBar.timeframe == "1D",
                        PriceBar.bar_time >= cutoff,
                    )
                    .order_by(PriceBar.bar_time.asc())
                )
            ).all()
            usd_pkr_bars = [{"bar_time": bt, "close": float(cl)} for bt, cl in fx_rows if float(cl) > 0]

    cpi_row = (
        await db.execute(select(MacroIndicator).where(MacroIndicator.code == "PK_CPI_YOY"))
    ).scalar_one_or_none()
    cpi_annual_pct = float(cpi_row.value) if cpi_row and cpi_row.value is not None else 8.0

    bt = run_strategy_backtest(
        legs_data,
        strategy=payload.strategy,
        benchmark_bars=bench_adj,
        benchmark_symbol=bench_sym,
        initial_capital_pkr=payload.initial_capital_pkr,
        monthly_contribution_pkr=payload.monthly_contribution_pkr,
        return_mode=payload.return_mode,
        real_return_mode=payload.real_return_mode,
        apply_shariah_purification=payload.apply_shariah_purification,
        broker_bps=payload.broker_bps,
        risk_free_rate_pct=rf_pkr,
        usd_to_pkr=usd_to_pkr,
        usd_pkr_bars=usd_pkr_bars,
        cpi_annual_pct=cpi_annual_pct,
    )

    summary = bt.get("summary") or {}
    mc_stress = compute_monte_carlo_and_stress(
        stress_holdings,
        portfolio_value_pkr=float(summary.get("final_value_pkr") or payload.initial_capital_pkr),
        ann_return_pct=float(summary.get("cagr_pct") or 16.0),
        ann_vol_pct=float(summary.get("annualized_volatility_pct") or 18.0),
        horizon_years=3,
        simulations=500,
        beta=summary.get("beta"),
        sbp_policy_rate_pct=rf_pkr,
    )
    bt["monte_carlo"] = mc_stress["monte_carlo"]
    bt["stress_tests"] = mc_stress["stress_tests"]
    return bt


def _period_orm_to_dict(p: EquityFinancialPeriod) -> dict[str, Any]:
    return {
        "period_type": p.period_type,
        "period_label": p.period_label,
        "period_end_date": p.period_end_date.isoformat(),
        "fiscal_year": p.fiscal_year,
        "revenue_mn": float(p.revenue_mn) if p.revenue_mn is not None else None,
        "cost_of_sales_mn": float(p.cost_of_sales_mn) if p.cost_of_sales_mn is not None else None,
        "gross_profit_mn": float(p.gross_profit_mn) if p.gross_profit_mn is not None else None,
        "operating_profit_mn": float(p.operating_profit_mn) if p.operating_profit_mn is not None else None,
        "finance_cost_mn": float(p.finance_cost_mn) if p.finance_cost_mn is not None else None,
        "profit_before_tax_mn": float(p.profit_before_tax_mn) if p.profit_before_tax_mn is not None else None,
        "taxation_mn": float(p.taxation_mn) if p.taxation_mn is not None else None,
        "net_profit_mn": float(p.net_profit_mn) if p.net_profit_mn is not None else None,
        "eps": float(p.eps) if p.eps is not None else None,
        "dps": float(p.dps) if p.dps is not None else None,
        "total_assets_mn": float(p.total_assets_mn) if p.total_assets_mn is not None else None,
        "current_assets_mn": float(p.current_assets_mn) if p.current_assets_mn is not None else None,
        "cash_equivalents_mn": float(p.cash_equivalents_mn) if p.cash_equivalents_mn is not None else None,
        "inventory_mn": float(p.inventory_mn) if p.inventory_mn is not None else None,
        "receivables_mn": float(p.receivables_mn) if p.receivables_mn is not None else None,
        "total_liabilities_mn": float(p.total_liabilities_mn) if p.total_liabilities_mn is not None else None,
        "current_liabilities_mn": float(p.current_liabilities_mn) if p.current_liabilities_mn is not None else None,
        "total_debt_mn": float(p.total_debt_mn) if p.total_debt_mn is not None else None,
        "shareholders_equity_mn": float(p.shareholders_equity_mn) if p.shareholders_equity_mn is not None else None,
        "operating_cashflow_mn": float(p.operating_cashflow_mn) if p.operating_cashflow_mn is not None else None,
        "capex_mn": float(p.capex_mn) if p.capex_mn is not None else None,
        "free_cashflow_mn": float(p.free_cashflow_mn) if p.free_cashflow_mn is not None else None,
        "dividends_paid_mn": float(p.dividends_paid_mn) if p.dividends_paid_mn is not None else None,
        "auditor_opinion": p.auditor_opinion,
        "directors_highlights_json": p.directors_highlights_json,
        "dividend_bonus_note": p.dividend_bonus_note,
        "segment_notes_json": p.segment_notes_json,
        "source_pdf_url": p.source_pdf_url,
        "source_label": p.source_label,
    }


@router.get("/heatmap")
async def get_psx_market_heatmap(
    shariah_only: bool = Query(default=False),
    index_only: str = Query(default="ALL", description="ALL | KSE100 | KMI30"),
    db: AsyncSession = Depends(get_db),
):
    """Sarmaya.pk-style PSX Market Breadth + Market-Cap-Weighted Sector Treemap powered by Pandas."""
    idx_u = (index_only or "ALL").strip().upper()

    async def _load() -> dict[str, Any]:
        stmt = (
            select(Asset)
            .options(selectinload(Asset.psx_profile))
            .where(Asset.owner_user_id.is_(None), Asset.asset_class == "PSX_EQUITY")
        )
        if shariah_only:
            stmt = stmt.where(Asset.is_shariah_compliant.is_(True))
        assets = (await db.execute(stmt)).scalars().all()

        rows: list[dict[str, Any]] = []
        for a in assets:
            p = a.psx_profile
            if idx_u == "KSE100" and not (p and p.is_kse100):
                continue
            if idx_u == "KMI30" and not (p and p.is_kmi30):
                continue
            rows.append(
                {
                    "symbol": a.symbol,
                    "name": a.name,
                    "sector": a.sector_or_category,
                    "last_price": float(a.last_price),
                    "change_pct": float(a.change_pct),
                    "volume": float(a.volume),
                    "market_cap_mn": float(p.market_cap_mn) if p and p.market_cap_mn else 0.0,
                    "is_shariah_compliant": a.is_shariah_compliant,
                }
            )
        return await asyncio.to_thread(compute_market_heatmap_pandas, rows)

    return await api_cache.get_or_set(f"heatmap:{shariah_only}:{idx_u}", 120.0, _load)


@router.get("/stocks/{symbol}/reports")
async def get_stock_financial_reports(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Return Pandas multi-period Annual & Quarterly 3-statement financial analytics & PDF disclosures."""
    asset = await resolve_asset_by_symbol(symbol, db, options=[selectinload(Asset.psx_profile)])
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")

    rows = (
        await db.execute(
            select(EquityFinancialPeriod)
            .where(EquityFinancialPeriod.asset_id == asset.id)
            .order_by(EquityFinancialPeriod.period_end_date.asc())
        )
    ).scalars().all()
    if not rows and asset.psx_profile and asset.psx_profile.financial_summary_json:
        if await seed_periods_from_psx_profile(db, asset) > 0:
            await db.commit()
            rows = (
                await db.execute(
                    select(EquityFinancialPeriod)
                    .where(EquityFinancialPeriod.asset_id == asset.id)
                    .order_by(EquityFinancialPeriod.period_end_date.asc())
                )
            ).scalars().all()

    is_fin = any(k in (asset.sector_or_category or "").lower() for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment"))
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "currency": asset.currency,
        "sector": asset.sector_or_category,
        **analyze_financial_statements_pandas([_period_orm_to_dict(r) for r in rows], is_financial=is_fin),
    }


@router.get("/stocks/{symbol}/reports/export")
async def export_stock_financial_reports_csv(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Export all Annual & Quarterly 3-Statement financial periods for a company as a Pandas CSV."""
    asset = await resolve_asset_by_symbol(symbol, db, options=[selectinload(Asset.psx_profile)])
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")
    rows = (
        await db.execute(
            select(EquityFinancialPeriod)
            .where(EquityFinancialPeriod.asset_id == asset.id)
            .order_by(EquityFinancialPeriod.period_type.asc(), EquityFinancialPeriod.period_end_date.asc())
        )
    ).scalars().all()
    csv_text = financial_periods_to_csv([_period_orm_to_dict(r) for r in rows])
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{asset.symbol}_financial_statements.csv"'},
    )


@router.post("/stocks/{symbol}/reports/upload")
async def upload_company_financial_report_pdf(
    symbol: str,
    file: UploadFile = File(...),
    period_type: str | None = Query(default=None, description="Optional hint: ANNUAL | QUARTERLY"),
    db: AsyncSession = Depends(get_db),
):
    """
    Drag-and-drop upload ANY company's Annual or Quarterly Financial Report PDF (PUCARS / Investor Relations)
    to parse 3-statement comparative tables, Auditor Opinion & Directors' Highlights into Pandas analytics.
    """
    asset = await resolve_asset_by_symbol(symbol, db, options=[selectinload(Asset.psx_profile)])
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")

    raw = await file.read()
    if not raw or len(raw) > 25_000_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF file is empty or exceeds 25MB limit.")
    if not raw[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file must be a valid PDF document.")

    parsed = await asyncio.to_thread(
        parse_company_report_pdf_bytes_sync,
        raw,
        symbol=asset.symbol,
        sector=asset.sector_or_category or "",
        filename_hint=file.filename or "report.pdf",
        period_type_hint=period_type,
        source_pdf_url=None,
        source_label=f"Uploaded PDF ({file.filename or 'Report'})"[:64],
    )
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract Balance Sheet, Income Statement, or Cash Flow tables from this PDF. Ensure it has selectable text (non-scanned).",
        )

    upserted = await upsert_financial_periods(db, asset, parsed, overwrite_existing=True)
    await db.commit()
    api_cache.invalidate_prefix("stock:detail:")
    api_cache.invalidate_prefix("coverage")

    rows = (
        await db.execute(
            select(EquityFinancialPeriod)
            .where(EquityFinancialPeriod.asset_id == asset.id)
            .order_by(EquityFinancialPeriod.period_end_date.asc())
        )
    ).scalars().all()
    is_fin = any(k in (asset.sector_or_category or "").lower() for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment"))
    return {
        "symbol": asset.symbol,
        "periods_extracted": upserted,
        "extracted_labels": [p["period_label"] for p in parsed],
        "report_analysis": analyze_financial_statements_pandas([_period_orm_to_dict(r) for r in rows], is_financial=is_fin),
    }
