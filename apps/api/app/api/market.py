from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    get_current_user,
    get_optional_user,
    get_usd_to_pkr_rate,
    require_admin_or_api_key,
    resolve_asset_by_symbol,
)
from app.core.cache import api_cache
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.models.market import (
    Announcement,
    Asset,
    CorporateAction,
    EquityFinancialPeriod,
    EquityFinancialSnapshot,
    FundFmrSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
    ScraperRun,
    ShariahMetric,
)
from app.models.portfolio import Transaction
from app.models.user import User
from app.scrapers.intl_stocks import (
    ensure_yfinance_asset_backfilled,
    fetch_lazy_intl_intraday_bars,
    search_yfinance_tickers,
    upsert_yfinance_asset,
)
from app.scrapers.psx import ensure_psx_asset_backfilled, fetch_lazy_intraday_bars
from app.scrapers.financial_reports import seed_periods_from_psx_profile
from app.scrapers.sbp_macro import get_live_risk_free_rate_pct
from app.services.deep_fundamentals import build_stock_valuation_lab
from app.services.pandas_analytics import (
    analyze_financial_statements_pandas,
    compute_cross_sectional_factor_ranks_pandas,
    compute_seasonality_and_rolling_pandas,
    compute_technical_summary_gauge_pandas,
)
from app.schemas.market import (
    AnnouncementOut,
    AssetSummaryOut,
    CorporateActionOut,
    CustomAssetCreate,
    CustomAssetUpdate,
    FundamentalAnalysisOut,
    MarketOverviewOut,
    MutualFundRowOut,
    PriceBarOut,
    PsxScreenerRowOut,
    ScraperRunOut,
    ShariahMetricOut,
    StockDetailOut,
    TechnicalAnalysisOut,
    YFinanceImportRequest,
)
from app.services.quant import compute_fundamental_analysis, compute_technical_indicators
from app.services.total_return import build_adjusted_bars
from app.workers.arq_worker import SCRAPER_REGISTRY, run_scraper_by_name, sync_all_live_sources

logger = logging.getLogger("openkhazana.api.market")
router = APIRouter(prefix="/api/v1/market", tags=["market"])

_background_tasks: set[asyncio.Task] = set()


def _to_asset_summary(a: Asset) -> AssetSummaryOut:
    return AssetSummaryOut(
        id=a.id,
        symbol=a.symbol,
        name=a.name,
        asset_class=a.asset_class,
        currency=a.currency,
        sector_or_category=a.sector_or_category,
        is_shariah_compliant=a.is_shariah_compliant,
        is_custom=bool(a.owner_user_id is not None or a.asset_class == "CUSTOM"),
        last_price=a.last_price,
        prev_close=a.prev_close,
        change_pct=a.change_pct,
        volume=a.volume,
        source=a.source,
        updated_at=a.updated_at,
    )


def _to_screener_row(a: Asset, p: PsxProfile | None) -> PsxScreenerRowOut:
    return PsxScreenerRowOut(
        **_to_asset_summary(a).model_dump(),
        exchange=p.exchange if p else ("NASDAQ" if a.asset_class.startswith("INTL") else "PSX"),
        country=p.country if p else ("US" if a.asset_class.startswith("INTL") else "PK"),
        default_wht_rate_pct=p.default_wht_rate_pct if p else None,
        market_cap_mn=p.market_cap_mn if p else Decimal("0"),
        pe_ratio=p.pe_ratio if p else None,
        pb_ratio=p.pb_ratio if p else None,
        eps=p.eps if p else None,
        roe_pct=p.roe_pct if p else None,
        dividend_yield_pct=p.dividend_yield_pct if p else Decimal("0"),
        high_52w=p.high_52w if p else Decimal("0"),
        low_52w=p.low_52w if p else Decimal("0"),
        is_kse100=p.is_kse100 if p else False,
        is_kmi30=p.is_kmi30 if p else False,
    )


def _to_fund_row(a: Asset, m: MutualFundProfile, fmr: FundFmrSnapshot | None = None) -> MutualFundRowOut:
    alloc = list(fmr.asset_allocation_json or [])[:3] if fmr else []
    top_syms = [str(h.get("symbol")) for h in (fmr.top_holdings_json or [])[:5] if h.get("symbol")] if fmr else []
    eff_ann = m.annualized_yield_pct if (m.annualized_yield_pct and m.annualized_yield_pct > 0) else (fmr.ytm_pct if fmr and fmr.ytm_pct else m.annualized_yield_pct)
    eff_1y = m.return_1y_pct
    if m.is_daily_dividend and (eff_1y is None or eff_1y <= 0) and eff_ann and eff_ann > 0:
        eff_1y = eff_ann
    return MutualFundRowOut(
        **_to_asset_summary(a).model_dump(),
        amc_name=m.amc_name,
        fund_category=m.fund_category,
        risk_profile=m.risk_profile,
        ter_pct=m.ter_pct,
        management_fee_pct=m.management_fee_pct,
        front_end_load_pct=m.front_end_load_pct,
        back_end_load_pct=m.back_end_load_pct,
        aum_mn=m.aum_mn,
        adjusted_nav_multiplier=m.adjusted_nav_multiplier,
        last_distribution_pkr=m.last_distribution_pkr,
        is_daily_dividend=m.is_daily_dividend,
        constant_par_nav=m.constant_par_nav,
        annualized_yield_pct=eff_ann,
        default_payout_mode=m.default_payout_mode,
        return_1m_pct=m.return_1m_pct,
        return_6m_pct=m.return_6m_pct,
        return_1y_pct=eff_1y,
        return_3y_pct=m.return_3y_pct,
        benchmark_name=m.benchmark_name,
        has_fmr=fmr is not None,
        fmr_month=fmr.report_label or fmr.report_month if fmr else None,
        stability_rating=fmr.stability_rating if fmr else None,
        wam_days=fmr.wam_days if fmr else None,
        asset_allocation_top=alloc,
        top_holding_symbols=top_syms,
    )


def _to_scraper_run_out(r: ScraperRun) -> ScraperRunOut:
    return ScraperRunOut(
        id=r.id,
        scraper_name=r.scraper_name,
        started_at=r.started_at,
        finished_at=r.finished_at,
        status=r.status,
        records_upserted=r.records_upserted,
        latency_ms=r.latency_ms,
        raw_payload_hash=r.raw_payload_hash,
        dlq_path=r.dlq_path,
        error_detail=r.error_detail,
    )


@router.get("/overview", response_model=MarketOverviewOut)
async def market_overview(db: AsyncSession = Depends(get_db)):
    async def _load() -> MarketOverviewOut:
        usd_to_pkr = await get_usd_to_pkr_rate(db)
        stmt = select(Asset).options(selectinload(Asset.psx_profile)).where(Asset.owner_user_id.is_(None))
        assets = (await db.execute(stmt)).scalars().all()

        indices = [_to_asset_summary(a) for a in assets if a.asset_class in {"PSX_INDEX", "INTL_INDEX"}]
        fx_rates = [_to_asset_summary(a) for a in assets if a.asset_class == "FX"]
        gold_rates = [_to_asset_summary(a) for a in assets if a.asset_class == "GOLD"]
        crypto_rates = [_to_asset_summary(a) for a in assets if a.asset_class == "CRYPTO"]

        equities = [a for a in assets if a.asset_class in {"PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"}]
        psx_equities = [a for a in equities if a.asset_class in {"PSX_EQUITY", "PSX_ETF"}]
        liquid_psx = [a for a in psx_equities if a.volume > 0] or psx_equities
        top_gainers = [_to_asset_summary(a) for a in sorted(liquid_psx, key=lambda x: x.change_pct, reverse=True)[:5]]
        top_losers = [_to_asset_summary(a) for a in sorted(liquid_psx, key=lambda x: x.change_pct)[:5]]

        div_rows = [
            _to_screener_row(a, a.psx_profile)
            for a in equities
            if a.psx_profile is not None
        ]
        div_rows.sort(key=lambda r: r.dividend_yield_pct, reverse=True)

        ann_stmt = (
            select(Announcement, Asset)
            .join(Asset, Announcement.asset_id == Asset.id)
            .where(Asset.owner_user_id.is_(None))
            .order_by(desc(Announcement.published_at))
            .limit(8)
        )
        anns = [
            AnnouncementOut(
                id=ann.id,
                symbol=asset.symbol,
                asset_name=asset.name,
                title=ann.title,
                category=ann.category,
                published_at=ann.published_at,
                source_url=ann.source_url,
                summary=ann.summary,
            )
            for ann, asset in (await db.execute(ann_stmt)).all()
        ]

        macro_rows = (
            await db.execute(select(MacroIndicator).order_by(MacroIndicator.category.asc(), MacroIndicator.code.asc()))
        ).scalars().all()
        macro_list = [
            {
                "code": m.code,
                "name": m.name,
                "country": m.country,
                "category": m.category,
                "value": float(m.value),
                "unit": m.unit,
                "as_of_date": m.as_of_date.isoformat(),
                "source": m.source,
            }
            for m in macro_rows
        ]

        return MarketOverviewOut(
            indices=indices,
            fx_rates=fx_rates,
            gold_rates=gold_rates,
            crypto_rates=crypto_rates,
            top_gainers=top_gainers,
            top_losers=top_losers,
            top_dividend_yield=div_rows[:5],
            recent_announcements=anns,
            usd_to_pkr=usd_to_pkr,
            macro_indicators=macro_list,
        )

    return await api_cache.get_or_set("overview:main", 60.0, _load)


@router.get("/assets/index")
async def asset_search_index(db: AsyncSession = Depends(get_db)):
    """Compact public catalog of all PSX, Global, Mutual Fund, FX, Gold & Crypto assets for instant <1ms client autocomplete."""
    async def _load() -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(
                    Asset.symbol,
                    Asset.name,
                    Asset.asset_class,
                    Asset.sector_or_category,
                    Asset.currency,
                    Asset.last_price,
                    Asset.change_pct,
                    Asset.is_shariah_compliant,
                )
                .where(Asset.owner_user_id.is_(None))
                .order_by(Asset.symbol.asc())
            )
        ).all()
        return [
            {
                "symbol": sym,
                "name": name,
                "asset_class": a_cls,
                "sector_or_category": sec,
                "currency": cur,
                "last_price": float(px),
                "change_pct": float(chg),
                "is_shariah_compliant": bool(sh),
            }
            for sym, name, a_cls, sec, cur, px, chg, sh in rows
        ]

    return await api_cache.get_or_set("assets:index", 180.0, _load)


@router.get("/assets", response_model=list[AssetSummaryOut])
async def search_assets(
    q: str = Query(default="", max_length=60),
    asset_class: str | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=500),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    owner_filter = (
        (Asset.owner_user_id == user.id) | (Asset.owner_user_id.is_(None))
        if user is not None
        else Asset.owner_user_id.is_(None)
    )
    stmt = select(Asset).where(owner_filter)
    if asset_class:
        stmt = stmt.where(Asset.asset_class == asset_class)
    query_clean = q.strip()
    if query_clean:
        like_term = f"%{query_clean}%"
        stmt = stmt.where((Asset.symbol.ilike(like_term)) | (Asset.name.ilike(like_term)))

    rows = list((await db.execute(stmt.order_by(Asset.symbol).limit(500))).scalars().all())
    if query_clean:
        q_up = query_clean.upper()
        rows.sort(
            key=lambda a: (
                0 if a.symbol.upper() == q_up else 1 if a.symbol.upper().startswith(q_up) else 2,
                0 if a.owner_user_id is not None else 1,
                a.symbol,
            )
        )
    else:
        rows.sort(key=lambda a: (0 if a.owner_user_id is not None else 1, a.symbol))

    return [_to_asset_summary(a) for a in rows[:limit]]


@router.post("/assets/custom", response_model=AssetSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_custom_asset(
    payload: CustomAssetCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a user-private custom asset (e.g. Plot, Sukuk, Gold Bangles, Private Equity)."""
    clean_sym = re.sub(r"[^A-Z0-9_]", "_", payload.symbol.strip().upper()).strip("_")
    if len(clean_sym) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Symbol must have at least 2 alphanumeric chars.")

    existing = (await db.execute(select(Asset).where(Asset.symbol == clean_sym))).scalar_one_or_none()
    if existing is not None:
        if existing.owner_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Public market ticker '{clean_sym}' already exists. Pick a distinct symbol.",
            )
        if existing.owner_user_id == user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You already created a custom asset with symbol '{clean_sym}'.",
            )
        # Preserve cross-user uniqueness without revealing another tenant's private symbol
        clean_sym = f"{clean_sym[:17]}_{secrets.token_hex(2).upper()}"

    now = datetime.now(timezone.utc)
    asset = Asset(
        symbol=clean_sym,
        name=payload.name.strip(),
        asset_class=payload.asset_class,
        currency=payload.currency,
        sector_or_category=payload.sector_or_category.strip() or "Custom Holdings",
        is_shariah_compliant=payload.is_shariah_compliant,
        last_price=payload.last_price,
        prev_close=payload.last_price,
        change_pct=Decimal("0"),
        volume=Decimal("0"),
        source="user_custom",
        owner_user_id=user.id,
        updated_at=now,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return _to_asset_summary(asset)


@router.patch("/assets/custom/{asset_id}", response_model=AssetSummaryOut)
async def update_custom_asset(
    asset_id: str,
    payload: CustomAssetUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's own custom asset (e.g. manual property/plot valuation update)."""
    asset = (
        await db.execute(select(Asset).where(Asset.id == asset_id, Asset.owner_user_id == user.id))
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom asset not found.")

    if payload.name is not None:
        asset.name = payload.name.strip()
    if payload.sector_or_category is not None:
        asset.sector_or_category = payload.sector_or_category.strip() or asset.sector_or_category
    if payload.is_shariah_compliant is not None:
        asset.is_shariah_compliant = payload.is_shariah_compliant
    if payload.last_price is not None:
        prev = asset.last_price if asset.last_price > 0 else payload.last_price
        asset.prev_close = prev
        asset.last_price = payload.last_price
        asset.change_pct = (
            ((payload.last_price - prev) / prev * Decimal("100")).quantize(Decimal("0.0001"))
            if prev > 0
            else Decimal("0")
        )
    asset.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(asset)
    return _to_asset_summary(asset)


@router.delete("/assets/custom/{asset_id}")
async def delete_custom_asset(
    asset_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = (
        await db.execute(select(Asset).where(Asset.id == asset_id, Asset.owner_user_id == user.id))
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom asset not found.")

    used = (await db.execute(select(Transaction.id).where(Transaction.asset_id == asset.id).limit(1))).scalar_one_or_none()
    if used is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete custom asset while transactions reference it.",
        )
    await db.delete(asset)
    await db.commit()
    return {"ok": True}


@router.get("/intl/search")
async def search_intl_tickers(
    q: str = Query(min_length=1, max_length=40),
    limit: int = Query(default=10, ge=1, le=20),
):
    """Live Yahoo Finance global ticker search (US equities, Ireland UCITS .L ETFs, Shariah ETFs, UK/GCC)."""
    return await search_yfinance_tickers(q, limit=limit)


@router.post("/intl/import", response_model=PsxScreenerRowOut)
async def import_yfinance_ticker(
    payload: YFinanceImportRequest,
    _user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch & import any global equity, ETF, or index live from Yahoo Finance (`yfinance`) with 5Y history."""
    raw_sym = payload.symbol.strip().upper()
    if not re.match(r"^[\^A-Z0-9._-]{1,24}$", raw_sym):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticker symbol format.")
    try:
        asset = await upsert_yfinance_asset(db, raw_sym, days=1825)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to import '{raw_sym}' from Yahoo Finance: {exc}",
        ) from exc

    loaded = (
        await db.execute(select(Asset).options(selectinload(Asset.psx_profile)).where(Asset.id == asset.id))
    ).scalar_one()
    api_cache.invalidate_prefix("stocks:")
    api_cache.invalidate_prefix("assets:")
    api_cache.invalidate_prefix("screener:")
    api_cache.invalidate_prefix("sectors:")
    api_cache.invalidate_prefix("overview:")
    return _to_screener_row(loaded, loaded.psx_profile)


@router.get("/stocks", response_model=list[PsxScreenerRowOut])
async def psx_screener(
    q: str = Query(default=""),
    market: str = Query(default="ALL", description="ALL | PSX | INTL"),
    sector: str | None = Query(default=None),
    shariah_only: bool = Query(default=False),
    kse100_only: bool = Query(default=False),
    kmi30_only: bool = Query(default=False),
    max_pe: Decimal | None = Query(default=None),
    min_div_yield: Decimal | None = Query(default=None),
    sort_by: str = Query(default="market_cap_mn"),
    db: AsyncSession = Depends(get_db),
):
    async def _load_all_stocks() -> list[PsxScreenerRowOut]:
        stmt = (
            select(Asset)
            .options(selectinload(Asset.psx_profile))
            .where(
                Asset.owner_user_id.is_(None),
                Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"]),
            )
        )
        assets = (await db.execute(stmt)).scalars().all()
        return [_to_screener_row(a, a.psx_profile) for a in assets]

    universe = await api_cache.get_or_set("stocks:universe", 90.0, _load_all_stocks)

    m_up = market.strip().upper()
    if m_up == "PSX":
        target_classes = {"PSX_EQUITY", "PSX_ETF"}
    elif m_up in {"INTL", "GLOBAL"}:
        target_classes = {"INTL_EQUITY", "INTL_ETF"}
    else:
        target_classes = {"PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"}

    q_l = q.strip().lower()
    rows: list[PsxScreenerRowOut] = []
    for r in universe:
        if r.asset_class not in target_classes:
            continue
        if q_l and q_l not in r.symbol.lower() and q_l not in r.name.lower():
            continue
        if sector and sector != "ALL" and r.sector_or_category != sector:
            continue
        if shariah_only and not r.is_shariah_compliant:
            continue
        if kse100_only and not r.is_kse100:
            continue
        if kmi30_only and not r.is_kmi30:
            continue
        if max_pe is not None and (r.pe_ratio is None or r.pe_ratio > max_pe):
            continue
        if min_div_yield is not None and r.dividend_yield_pct < min_div_yield:
            continue
        rows.append(r)

    sort_key_map = {
        "market_cap_mn": lambda r: r.market_cap_mn,
        "dividend_yield_pct": lambda r: r.dividend_yield_pct,
        "change_pct": lambda r: r.change_pct,
        "pe_ratio": lambda r: r.pe_ratio if r.pe_ratio is not None else Decimal("99999"),
        "volume": lambda r: r.volume,
    }
    key_fn = sort_key_map.get(sort_by, sort_key_map["market_cap_mn"])
    reverse = sort_by != "pe_ratio"
    rows.sort(key=key_fn, reverse=reverse)
    return rows


def _deep_snapshot_to_dict(s: EquityFinancialSnapshot | None) -> dict[str, Any] | None:
    if s is None:
        return None
    m = dict(s.metrics_json or {})
    return {
        "fiscal_period": s.fiscal_period,
        "bvps": float(s.bvps) if s.bvps is not None else None,
        "total_assets_mn": float(s.total_assets_mn) if s.total_assets_mn is not None else None,
        "total_debt_mn": float(s.total_debt_mn) if s.total_debt_mn is not None else None,
        "cash_equivalents_mn": float(s.cash_equivalents_mn) if s.cash_equivalents_mn is not None else None,
        "shareholders_equity_mn": float(s.shareholders_equity_mn) if s.shareholders_equity_mn is not None else None,
        "operating_cashflow_mn": float(s.operating_cashflow_mn) if s.operating_cashflow_mn is not None else None,
        "capex_mn": float(s.capex_mn) if s.capex_mn is not None else None,
        "free_cashflow_mn": float(s.free_cashflow_mn) if s.free_cashflow_mn is not None else None,
        "current_ratio": float(s.current_ratio) if s.current_ratio is not None else None,
        "debt_to_equity": float(s.debt_to_equity) if s.debt_to_equity is not None else None,
        "roe_pct": float(s.roe_pct) if s.roe_pct is not None else None,
        "roa_pct": float(s.roa_pct) if s.roa_pct is not None else None,
        "roic_pct": float(s.roic_pct) if s.roic_pct is not None else None,
        "operating_margin_pct": float(s.operating_margin_pct) if s.operating_margin_pct is not None else None,
        "payout_ratio_pct": float(s.payout_ratio_pct) if s.payout_ratio_pct is not None else None,
        "analyst_target_price": float(s.analyst_target_price) if s.analyst_target_price is not None else None,
        "analyst_rating": s.analyst_rating,
        "piotroski_f_score": s.piotroski_f_score,
        "altman_z_score": float(s.altman_z_score) if s.altman_z_score is not None else None,
        "dcf_intrinsic_value": float(s.dcf_intrinsic_value) if s.dcf_intrinsic_value is not None else None,
        "metrics_json": m,
        "source": s.source,
    }


async def _prepare_bar_dicts(
    db: AsyncSession,
    asset: Asset,
    raw_bars: list[PriceBar],
    *,
    return_mode: str = "PRICE",
    apply_shariah_purification: bool = False,
) -> list[dict[str, Any]]:
    bar_dicts = [
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
    mode_u = (return_mode or "PRICE").upper()
    if mode_u not in {"GROSS_TR", "NET_FILER", "NET_NON_FILER"} or not bar_dicts:
        return bar_dicts

    ca_rows = (
        await db.execute(select(CorporateAction).where(CorporateAction.asset_id == asset.id))
    ).scalars().all()
    mf_prof = (
        await db.execute(select(MutualFundProfile).where(MutualFundProfile.asset_id == asset.id))
    ).scalar_one_or_none()
    psx_prof = (
        await db.execute(select(PsxProfile).where(PsxProfile.asset_id == asset.id))
    ).scalar_one_or_none()
    sh_metric = (
        await db.execute(select(ShariahMetric).where(ShariahMetric.asset_id == asset.id))
    ).scalar_one_or_none()

    actions_in = [
        {
            "action_type": ca.action_type,
            "ex_date": ca.ex_date,
            "payout_per_share": float(ca.payout_per_share),
            "ratio_numerator": float(ca.ratio_numerator),
            "ratio_denominator": float(ca.ratio_denominator),
        }
        for ca in ca_rows
    ]
    is_intl = str(asset.asset_class or "").startswith("INTL_")
    country = psx_prof.country if psx_prof else ("US" if is_intl else "PK")
    is_daily_div = bool(mf_prof and mf_prof.is_daily_dividend)
    ann_yield = float(mf_prof.annualized_yield_pct or 0) if mf_prof else 0.0
    purif_rate = float(sh_metric.purification_rate_pct or 0) if sh_metric else 0.0

    adj_series = build_adjusted_bars(
        bar_dicts,
        actions=actions_in,
        mode=mode_u,
        is_daily_dividend=is_daily_div,
        annualized_yield_pct=ann_yield,
        is_intl=is_intl,
        country=country,
        purification_rate_pct=purif_rate,
        apply_shariah_purification=apply_shariah_purification,
    )
    return [
        {
            "bar_time": p["bar_time"],
            "open": Decimal(str(p["open"])),
            "high": Decimal(str(p["high"])),
            "low": Decimal(str(p["low"])),
            "close": Decimal(str(p["close"])),
            "volume": p.get("volume", Decimal("0")),
        }
        for p in adj_series
    ]


@router.get("/stocks/{symbol}", response_model=StockDetailOut)
async def stock_detail(
    symbol: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    uid = user.id if user else None
    sym_key = symbol.strip().upper()
    if uid is None:
        hit, cached = api_cache.get(f"stock:detail:{sym_key}")
        if hit:
            return cached

    asset = await resolve_asset_by_symbol(
        symbol,
        db,
        options=[
            selectinload(Asset.psx_profile),
            selectinload(Asset.shariah_metric),
            selectinload(Asset.corporate_actions),
            selectinload(Asset.announcements),
            selectinload(Asset.financial_snapshot),
        ],
        owner_user_id=uid,
    )
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")

    if asset.owner_user_id is None:
        hit_pub, cached_pub = api_cache.get(f"stock:detail:{asset.symbol.upper()}")
        if hit_pub:
            return cached_pub

    if get_settings().environment.lower() != "test":
        if asset.asset_class in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
            await ensure_psx_asset_backfilled(db, asset)
        elif asset.asset_class in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"}:
            await ensure_yfinance_asset_backfilled(db, asset)
        db.expire_all()
        asset = await resolve_asset_by_symbol(
            symbol,
            db,
            options=[
                selectinload(Asset.psx_profile),
                selectinload(Asset.shariah_metric),
                selectinload(Asset.corporate_actions),
                selectinload(Asset.announcements),
                selectinload(Asset.financial_snapshot),
            ],
            owner_user_id=uid,
        ) or asset

    profile_dict = None
    if asset.psx_profile:
        p = asset.psx_profile
        profile_dict = {
            "isin": p.isin,
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

    shariah_out = None
    if asset.shariah_metric:
        s = asset.shariah_metric
        shariah_out = ShariahMetricOut(
            fiscal_period=s.fiscal_period,
            core_business_compliant=s.core_business_compliant,
            is_kmi_all_share=s.is_kmi_all_share,
            debt_to_assets_pct=s.debt_to_assets_pct,
            non_compliant_inv_pct=s.non_compliant_inv_pct,
            non_compliant_income_pct=s.non_compliant_income_pct,
            purification_rate_pct=s.purification_rate_pct,
            illiquid_assets_pct=s.illiquid_assets_pct,
            passed=s.passed,
        )

    corp_actions = [
        CorporateActionOut(
            id=ca.id,
            asset_id=ca.asset_id,
            symbol=asset.symbol,
            action_type=ca.action_type,
            ex_date=ca.ex_date,
            payout_per_share=ca.payout_per_share,
            ratio_numerator=ca.ratio_numerator,
            ratio_denominator=ca.ratio_denominator,
            right_price=ca.right_price,
            description=ca.description,
        )
        for ca in sorted(asset.corporate_actions, key=lambda c: c.ex_date, reverse=True)
    ]

    anns = [
        AnnouncementOut(
            id=ann.id,
            symbol=asset.symbol,
            asset_name=asset.name,
            title=ann.title,
            category=ann.category,
            published_at=ann.published_at,
            source_url=ann.source_url,
            summary=ann.summary,
        )
        for ann in sorted(asset.announcements, key=lambda n: n.published_at, reverse=True)
    ]

    bars_stmt = (
        select(PriceBar)
        .where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
        .order_by(PriceBar.bar_time.desc())
        .limit(1250)
    )
    bars_1d = list(reversed((await db.execute(bars_stmt)).scalars().all()))
    bar_dicts = [
        {
            "bar_time": b.bar_time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars_1d
    ]
    tech_data = compute_technical_indicators(
        symbol=asset.symbol,
        bars=bar_dicts[-260:],
        current_price=asset.last_price,
    )
    technicals_out = TechnicalAnalysisOut(**tech_data)

    asset_summary = _to_asset_summary(asset)
    live_rf_pct = float(await get_live_risk_free_rate_pct(db, asset.currency or "PKR"))
    deep_dict = _deep_snapshot_to_dict(asset.financial_snapshot)
    fund_data = compute_fundamental_analysis(
        asset=asset_summary.model_dump(),
        profile=profile_dict,
        shariah=shariah_out.model_dump() if shariah_out else None,
        risk_free_rate_pct=live_rf_pct,
        deep_snapshot=deep_dict,
    )
    fundamentals_out = FundamentalAnalysisOut(**fund_data)

    # Sector Peer Valuation Benchmark (Median P/E, P/B, ROE%, Div Yield + top peers)
    peer_valuation: dict[str, Any] | None = None
    if asset.sector_or_category and asset.asset_class in {"PSX_EQUITY", "INTL_EQUITY"}:
        peer_assets = (
            await db.execute(
                select(Asset)
                .options(selectinload(Asset.psx_profile), selectinload(Asset.financial_snapshot))
                .where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class == asset.asset_class,
                    Asset.sector_or_category == asset.sector_or_category,
                    Asset.last_price > 0,
                )
            )
        ).scalars().all()
        if peer_assets:
            pe_vals: list[float] = []
            pb_vals: list[float] = []
            roe_vals: list[float] = []
            dy_vals: list[float] = []
            peer_rows: list[dict[str, Any]] = []
            for pa in peer_assets:
                pp = pa.psx_profile
                ps = pa.financial_snapshot
                pe_f = float(pp.pe_ratio) if pp and pp.pe_ratio and 0 < float(pp.pe_ratio) < 150 else None
                pb_f = float(pp.pb_ratio) if pp and pp.pb_ratio and 0 < float(pp.pb_ratio) < 50 else None
                if pb_f is None and ps and ps.bvps and float(ps.bvps) > 0 and float(pa.last_price) > 0:
                    pb_f = round(float(pa.last_price) / float(ps.bvps), 2)
                roe_f = (
                    float(ps.roe_pct)
                    if ps and ps.roe_pct is not None and -100 < float(ps.roe_pct) < 250
                    else (float(pp.roe_pct) if pp and pp.roe_pct is not None and -100 < float(pp.roe_pct) < 250 else None)
                )
                dy_f = float(pp.dividend_yield_pct) if pp and pp.dividend_yield_pct and float(pp.dividend_yield_pct) > 0 else None
                if pe_f is not None:
                    pe_vals.append(pe_f)
                if pb_f is not None:
                    pb_vals.append(pb_f)
                if roe_f is not None:
                    roe_vals.append(roe_f)
                if dy_f is not None:
                    dy_vals.append(dy_f)
                peer_rows.append(
                    {
                        "symbol": pa.symbol,
                        "name": pa.name,
                        "is_current": pa.id == asset.id,
                        "last_price": float(pa.last_price),
                        "change_pct": float(pa.change_pct),
                        "market_cap_mn": float(pp.market_cap_mn) if pp and pp.market_cap_mn else 0.0,
                        "pe_ratio": round(pe_f, 2) if pe_f is not None else None,
                        "pb_ratio": round(pb_f, 2) if pb_f is not None else None,
                        "roe_pct": round(roe_f, 2) if roe_f is not None else None,
                        "dividend_yield_pct": round(dy_f, 2) if dy_f is not None else 0.0,
                        "piotroski_f_score": ps.piotroski_f_score if ps else None,
                        "is_shariah_compliant": pa.is_shariah_compliant,
                    }
                )
            peer_rows.sort(key=lambda r: (1 if r["is_current"] else 0, r["market_cap_mn"]), reverse=True)
            med_pe = round(median(pe_vals), 2) if pe_vals else None
            med_pb = round(median(pb_vals), 2) if pb_vals else None
            med_roe = round(median(roe_vals), 2) if roe_vals else None
            med_dy = round(median(dy_vals), 2) if dy_vals else None
            cur_pe = float(asset.psx_profile.pe_ratio) if asset.psx_profile and asset.psx_profile.pe_ratio and float(asset.psx_profile.pe_ratio) > 0 else None
            pe_disc = round(((cur_pe / med_pe) - 1.0) * 100.0, 1) if (cur_pe and med_pe and med_pe > 0) else None
            peer_valuation = {
                "sector": asset.sector_or_category,
                "peer_count": len(peer_assets),
                "sector_median_pe": med_pe,
                "sector_median_pb": med_pb,
                "sector_median_roe_pct": med_roe,
                "sector_median_div_yield_pct": med_dy,
                "pe_vs_sector_pct": pe_disc,
                "peers": peer_rows[:8],
            }

    # Annual Dividend History, Consistency Streak & TTM Yield Radar
    dividend_history: dict[str, Any] | None = None
    cash_divs = [ca for ca in asset.corporate_actions if ca.action_type == "CASH_DIVIDEND" and ca.payout_per_share > 0]
    if cash_divs or any(ca.action_type in {"BONUS_ISSUE", "RIGHT_ISSUE"} for ca in asset.corporate_actions):
        by_year: dict[int, float] = {}
        today_d = date.today()
        ttm_cutoff = today_d - timedelta(days=365)
        ttm_dps = 0.0
        upcoming_ex: str | None = None
        for ca in cash_divs:
            yr = ca.ex_date.year
            amt = float(ca.payout_per_share)
            by_year[yr] = round(by_year.get(yr, 0.0) + amt, 4)
            if ttm_cutoff <= ca.ex_date <= today_d:
                ttm_dps += amt
            if ca.ex_date >= today_d and (upcoming_ex is None or ca.ex_date.isoformat() < upcoming_ex):
                upcoming_ex = ca.ex_date.isoformat()
        sorted_years = sorted(by_year.keys())
        streak = 0
        if sorted_years:
            cur_yr = sorted_years[-1]
            if cur_yr >= today_d.year - 1:
                while cur_yr in by_year and by_year[cur_yr] > 0:
                    streak += 1
                    cur_yr -= 1
        eps_f = float(asset.psx_profile.eps) if asset.psx_profile and asset.psx_profile.eps and float(asset.psx_profile.eps) > 0 else None
        payout_ratio = round((ttm_dps / eps_f) * 100.0, 1) if (eps_f and ttm_dps > 0) else None
        ttm_yield = round((ttm_dps / float(asset.last_price)) * 100.0, 2) if (float(asset.last_price) > 0 and ttm_dps > 0) else None
        dividend_history = {
            "annual_payouts": [{"year": str(y), "dps": round(by_year[y], 2)} for y in sorted_years[-6:]],
            "streak_years": streak,
            "ttm_dps": round(ttm_dps, 2),
            "ttm_yield_pct": ttm_yield,
            "payout_ratio_pct": payout_ratio,
            "upcoming_ex_date": upcoming_ex,
            "bonus_count": sum(1 for ca in asset.corporate_actions if ca.action_type == "BONUS_ISSUE"),
        }

    # Interactive Valuation Lab (DCF 5x5 Sensitivity, 9-Pt Piotroski Checklist, DuPont ROE, Bull/Bear Digest)
    valuation_lab = build_stock_valuation_lab(
        asset=asset_summary.model_dump(),
        profile=profile_dict,
        deep_snapshot=deep_dict,
        shariah=shariah_out.model_dump() if shariah_out else None,
        peer_valuation=peer_valuation,
        dividend_history=dividend_history,
        risk_free_rate_pct=live_rf_pct,
    )

    # Mutual Fund Institutional Ownership cross-linked from parsed SECP FMR Top-10 holdings (cached 180s)
    institutional_holders: list[dict[str, Any]] = []
    if asset.asset_class in {"PSX_EQUITY", "PSX_ETF"}:
        async def _load_fmr_holders_map() -> dict[str, list[dict[str, Any]]]:
            fmr_rows = (
                await db.execute(
                    select(FundFmrSnapshot, Asset)
                    .join(Asset, FundFmrSnapshot.asset_id == Asset.id)
                    .where(Asset.owner_user_id.is_(None))
                )
            ).all()
            by_sym: dict[str, list[dict[str, Any]]] = {}
            for fmr_s, f_asset in fmr_rows:
                aum = float(fmr_s.net_assets_mn) if fmr_s.net_assets_mn else None
                for h in (fmr_s.top_holdings_json or []):
                    h_sym = str(h.get("symbol") or "").upper()
                    if not h_sym:
                        continue
                    wt = float(h.get("weight_pct") or 0.0)
                    est_mn = round((aum * wt) / 100.0, 2) if (aum and wt > 0) else None
                    by_sym.setdefault(h_sym, []).append(
                        {
                            "fund_symbol": f_asset.symbol,
                            "fund_name": f_asset.name,
                            "amc_name": fmr_s.amc_name,
                            "weight_pct": round(wt, 2),
                            "fund_aum_mn": aum,
                            "est_value_mn": est_mn,
                            "report_label": fmr_s.report_label or fmr_s.report_month,
                            "is_shariah_compliant": f_asset.is_shariah_compliant,
                        }
                    )
            for v in by_sym.values():
                v.sort(key=lambda x: (x["est_value_mn"] or 0.0, x["weight_pct"]), reverse=True)
            return by_sym

        holders_map = await api_cache.get_or_set("fmr:holders_map", 180.0, _load_fmr_holders_map)
        institutional_holders = list(holders_map.get(asset.symbol.upper(), []))

    bench_sym = "SPX" if asset.currency == "USD" else "KSE100"
    bench_asset = (
        await db.execute(select(Asset.id).where(Asset.symbol == bench_sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()
    bench_bars: list[dict[str, Any]] = []
    if bench_asset is not None:
        b_rows = list(
            reversed(
                (
                    await db.execute(
                        select(PriceBar.bar_time, PriceBar.close)
                        .where(PriceBar.asset_id == bench_asset, PriceBar.timeframe == "1D")
                        .order_by(PriceBar.bar_time.desc())
                        .limit(260)
                    )
                ).all()
            )
        )
        bench_bars = [{"bar_time": bt, "close": float(cl)} for bt, cl in b_rows if float(cl) > 0]

    factor_rows: list[dict[str, Any]] | None = None
    if asset.asset_class in {"PSX_EQUITY", "INTL_EQUITY"}:
        async def _load_factor_rows() -> list[dict[str, Any]]:
            all_eq = (
                await db.execute(
                    select(Asset)
                    .options(selectinload(Asset.psx_profile), selectinload(Asset.financial_snapshot))
                    .where(Asset.owner_user_id.is_(None), Asset.asset_class == asset.asset_class)
                )
            ).scalars().all()
            return [
                {
                    "symbol": a.symbol,
                    "sector": a.sector_or_category,
                    "pe_ratio": float(a.psx_profile.pe_ratio) if a.psx_profile and a.psx_profile.pe_ratio else None,
                    "pb_ratio": float(a.psx_profile.pb_ratio) if a.psx_profile and a.psx_profile.pb_ratio else None,
                    "roe_pct": float(a.financial_snapshot.roe_pct) if a.financial_snapshot and a.financial_snapshot.roe_pct is not None else (float(a.psx_profile.roe_pct) if a.psx_profile and a.psx_profile.roe_pct is not None else None),
                    "dividend_yield_pct": float(a.psx_profile.dividend_yield_pct) if a.psx_profile and a.psx_profile.dividend_yield_pct else 0.0,
                    "market_cap_mn": float(a.psx_profile.market_cap_mn) if a.psx_profile and a.psx_profile.market_cap_mn else 0.0,
                    "piotroski_f_score": a.financial_snapshot.piotroski_f_score if a.financial_snapshot else None,
                }
                for a in all_eq
            ]

        factor_rows = await api_cache.get_or_set(f"stocks:factor_rows:{asset.asset_class}", 180.0, _load_factor_rows)

    # Multi-Period Financial Reports (auto-seed from DPS financial_summary_json if no periods persisted yet)
    period_rows = (
        await db.execute(
            select(EquityFinancialPeriod)
            .where(EquityFinancialPeriod.asset_id == asset.id)
            .order_by(EquityFinancialPeriod.period_end_date.asc())
        )
    ).scalars().all()
    if not period_rows and asset.psx_profile and asset.psx_profile.financial_summary_json:
        seeded = await seed_periods_from_psx_profile(db, asset)
        if seeded > 0:
            await db.commit()
            period_rows = (
                await db.execute(
                    select(EquityFinancialPeriod)
                    .where(EquityFinancialPeriod.asset_id == asset.id)
                    .order_by(EquityFinancialPeriod.period_end_date.asc())
                )
            ).scalars().all()

    is_fin_sec = any(k in (asset.sector_or_category or "").lower() for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment"))
    period_dicts = [
        {
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
        for p in period_rows
    ]

    def _compute_pandas_bundle() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        tg = compute_technical_summary_gauge_pandas(bar_dicts[-300:])
        seas = compute_seasonality_and_rolling_pandas(bar_dicts, benchmark_bars=bench_bars)
        fr = compute_cross_sectional_factor_ranks_pandas(factor_rows, asset.symbol) if factor_rows else None
        reps = analyze_financial_statements_pandas(period_dicts, is_financial=is_fin_sec)
        return tg, seas, fr, reps

    technical_gauge, seasonality, factor_ranks, financial_reports = await asyncio.to_thread(_compute_pandas_bundle)

    result_out = StockDetailOut(
        asset=asset_summary,
        profile=profile_dict,
        shariah=shariah_out,
        corporate_actions=corp_actions,
        announcements=anns,
        technicals=technicals_out,
        fundamentals=fundamentals_out,
        deep_snapshot=deep_dict,
        peer_valuation=peer_valuation,
        dividend_history=dividend_history,
        valuation_lab=valuation_lab,
        institutional_holders=institutional_holders[:15],
        technical_gauge=technical_gauge,
        seasonality=seasonality,
        factor_ranks=factor_ranks,
        financial_reports=financial_reports,
    )
    if asset.owner_user_id is None:
        api_cache.set(f"stock:detail:{asset.symbol.upper()}", result_out, 60.0)
    return result_out


@router.get("/assets/{symbol}/technicals", response_model=TechnicalAnalysisOut)
async def asset_technical_indicators(
    symbol: str,
    return_mode: str = Query(default="PRICE"),
    apply_shariah_purification: bool = Query(default=False),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await resolve_asset_by_symbol(symbol, db, owner_user_id=user.id if user else None)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")
    if get_settings().environment.lower() != "test":
        if asset.asset_class in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
            await ensure_psx_asset_backfilled(db, asset, fetch_fundamentals=False)
        elif asset.asset_class in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"}:
            await ensure_yfinance_asset_backfilled(db, asset)

    bars_stmt = (
        select(PriceBar)
        .where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
        .order_by(PriceBar.bar_time.desc())
        .limit(350)
    )
    bars_1d = list(reversed((await db.execute(bars_stmt)).scalars().all()))
    bar_dicts = await _prepare_bar_dicts(
        db,
        asset,
        bars_1d,
        return_mode=return_mode,
        apply_shariah_purification=apply_shariah_purification,
    )
    eff_price = bar_dicts[-1]["close"] if bar_dicts and return_mode.upper() != "PRICE" else asset.last_price
    tech_data = compute_technical_indicators(
        symbol=asset.symbol,
        bars=bar_dicts,
        current_price=eff_price,
    )
    return TechnicalAnalysisOut(**tech_data)


@router.get("/assets/{symbol}/bars", response_model=list[PriceBarOut])
async def asset_price_bars(
    symbol: str,
    timeframe: str = Query(default="1D"),
    limit: int = Query(default=120, ge=1, le=1500),
    return_mode: str = Query(default="PRICE"),
    apply_shariah_purification: bool = Query(default=False),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await resolve_asset_by_symbol(symbol, db, owner_user_id=user.id if user else None)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")

    if timeframe == "1D" and get_settings().environment.lower() != "test":
        if asset.asset_class in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
            await ensure_psx_asset_backfilled(db, asset, fetch_fundamentals=False)
        elif asset.asset_class in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"}:
            await ensure_yfinance_asset_backfilled(db, asset)

    stmt = (
        select(PriceBar)
        .where(PriceBar.asset_id == asset.id, PriceBar.timeframe == timeframe)
        .order_by(PriceBar.bar_time.desc())
        .limit(limit)
    )
    bars = list(reversed((await db.execute(stmt)).scalars().all()))
    if not bars and timeframe == "5M":
        if asset.asset_class in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
            lazy = await fetch_lazy_intraday_bars(asset.symbol)
        elif asset.asset_class in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"}:
            lazy = await fetch_lazy_intl_intraday_bars(asset.symbol)
        else:
            lazy = []
        if lazy:
            return [
                PriceBarOut(
                    bar_time=b["bar_time"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                )
                for b in lazy[-limit:]
            ]

    if timeframe == "1D" and return_mode.upper() != "PRICE" and bars:
        adj = await _prepare_bar_dicts(
            db,
            asset,
            bars,
            return_mode=return_mode,
            apply_shariah_purification=apply_shariah_purification,
        )
        return [
            PriceBarOut(
                bar_time=b["bar_time"],
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume"],
            )
            for b in adj
        ]

    return [
        PriceBarOut(
            bar_time=b.bar_time,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
    ]


@router.get("/funds", response_model=list[MutualFundRowOut])
async def mutual_funds_explorer(
    q: str = Query(default=""),
    amc: str | None = Query(default=None),
    category: str | None = Query(default=None),
    shariah_only: bool = Query(default=False),
    daily_dividend_only: bool = Query(default=False),
    max_ter: Decimal | None = Query(default=None),
    sort_by: str = Query(default="return_1y_pct"),
    db: AsyncSession = Depends(get_db),
):
    async def _load_all_funds() -> list[MutualFundRowOut]:
        stmt = (
            select(Asset)
            .options(selectinload(Asset.mutual_fund_profile), selectinload(Asset.fmr_snapshot))
            .where(Asset.owner_user_id.is_(None), Asset.asset_class.in_(["MUTUAL_FUND", "VPS_PENSION"]))
        )
        assets = (await db.execute(stmt)).scalars().all()
        return [
            _to_fund_row(a, a.mutual_fund_profile, a.fmr_snapshot)
            for a in assets
            if a.mutual_fund_profile is not None
        ]

    universe = await api_cache.get_or_set("funds:universe", 120.0, _load_all_funds)

    q_l = q.strip().lower()
    rows: list[MutualFundRowOut] = []
    for r in universe:
        if q_l and q_l not in r.symbol.lower() and q_l not in r.name.lower() and q_l not in r.amc_name.lower():
            continue
        if shariah_only and not r.is_shariah_compliant:
            continue
        if daily_dividend_only and not r.is_daily_dividend:
            continue
        if amc and amc != "ALL" and r.amc_name != amc:
            continue
        if category and category != "ALL" and r.fund_category != category:
            continue
        if max_ter is not None and r.ter_pct > max_ter:
            continue
        rows.append(r)

    sort_map = {
        "return_1y_pct": lambda r: r.return_1y_pct if r.return_1y_pct is not None else Decimal("-999"),
        "return_3y_pct": lambda r: r.return_3y_pct if r.return_3y_pct is not None else Decimal("-999"),
        "annualized_yield_pct": lambda r: r.annualized_yield_pct if r.annualized_yield_pct is not None else Decimal("-999"),
        "aum_mn": lambda r: r.aum_mn,
        "ter_pct": lambda r: r.ter_pct,
    }
    key_fn = sort_map.get(sort_by, sort_map["return_1y_pct"])
    rows.sort(key=key_fn, reverse=(sort_by != "ter_pct"))
    return rows


@router.get("/funds/compare", response_model=list[MutualFundRowOut])
async def compare_funds(
    symbols: str = Query(description="Comma-separated fund symbols, e.g. MIF,UBLSF,AKDIF"),
    db: AsyncSession = Depends(get_db),
):
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()][:5]
    if not sym_list:
        return []
    stmt = (
        select(Asset)
        .options(selectinload(Asset.mutual_fund_profile), selectinload(Asset.fmr_snapshot))
        .where(Asset.owner_user_id.is_(None), Asset.symbol.in_(sym_list))
    )
    assets = (await db.execute(stmt)).scalars().all()
    return [_to_fund_row(a, a.mutual_fund_profile, a.fmr_snapshot) for a in assets if a.mutual_fund_profile is not None]


@router.get("/scraper-status", response_model=list[ScraperRunOut])
async def scraper_status(db: AsyncSession = Depends(get_db)):
    stmt = select(ScraperRun).order_by(desc(ScraperRun.started_at)).limit(40)
    runs = (await db.execute(stmt)).scalars().all()
    return [_to_scraper_run_out(r) for r in runs]


def _schedule_fallback_task(scraper_name: str, run_id: str) -> None:
    async def _runner() -> None:
        try:
            await run_scraper_by_name(scraper_name, existing_run_id=run_id, evaluate_after=True)
        except Exception as exc:
            logger.exception("In-process background scraper '%s' failed: %s", scraper_name, exc)

    task = asyncio.create_task(_runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post("/scrapers/{scraper_name}/trigger", response_model=ScraperRunOut)
async def trigger_scraper(
    scraper_name: str,
    sync: bool = Query(default=False, description="Execute synchronously instead of queuing (admin/test use)"),
    _admin: User | None = Depends(require_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    scraper_cls = SCRAPER_REGISTRY.get(scraper_name)
    if scraper_cls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown scraper '{scraper_name}'.")

    if sync:
        run = await scraper_cls().execute_with_telemetry(db)
        api_cache.invalidate_prefix("")
        return _to_scraper_run_out(run)

    run = ScraperRun(
        scraper_name=scraper_name,
        started_at=datetime.now(timezone.utc),
        status="QUEUED",
        error_detail="Queued for asynchronous execution.",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    enqueued_via_arq = False
    if await get_redis() is not None:
        try:
            redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
            pool = await create_pool(redis_settings)
            try:
                await pool.enqueue_job("run_scraper_job", scraper_name, run.id)
                enqueued_via_arq = True
            finally:
                await pool.aclose()
        except Exception as exc:
            logger.info("Redis/ARQ unavailable (%s); dispatching scraper '%s' via in-process task.", exc, scraper_name)

    if not enqueued_via_arq:
        _schedule_fallback_task(scraper_name, run.id)

    return _to_scraper_run_out(run)


@router.post("/sync-all", response_model=list[ScraperRunOut])
async def trigger_sync_all_live_sources(
    include_eod: bool = Query(default=False),
    _user: User = Depends(get_current_user),
):
    """Trigger a full live market data sync across PSX, MUFAP/Al-Meezan/NBP, FX/Gold, Crypto & Yahoo Finance."""
    runs = await sync_all_live_sources(include_eod=include_eod)
    return [_to_scraper_run_out(r) for r in runs]
