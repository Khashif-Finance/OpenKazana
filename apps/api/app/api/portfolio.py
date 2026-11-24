from __future__ import annotations

import calendar
import csv
import io
import secrets
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_usd_to_pkr_rate, resolve_asset_by_symbol
from app.core.config import get_settings
from app.core.database import apply_rls_context, get_db
from app.models.market import Asset, AssetSymbolAlias, CorporateAction, FundFmrSnapshot, PriceBar
from app.models.portfolio import Portfolio, PortfolioShareLink, Transaction
from app.models.user import User
from app.scrapers.intl_stocks import ensure_yfinance_asset_backfilled
from app.scrapers.psx import ensure_psx_asset_backfilled
from app.schemas.portfolio import (
    CsvCommitRequest,
    CsvPreviewRequest,
    DailyFundAccrualOut,
    DailyFundAccrualRequest,
    FbrTaxYearSummaryOut,
    HoldingPositionOut,
    PendingCorporateActionOut,
    PortfolioCreate,
    PortfolioOut,
    PortfolioQuantOut,
    PortfolioSummaryOut,
    PortfolioUpdate,
    SharedPortfolioOut,
    ShareLinkCreate,
    ShareLinkOut,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)
from app.services.accounting import evaluate_portfolio
from app.services.csv_importer import extract_csv_text_from_upload, make_tx_signature, parse_mapped_csv, preview_csv
from app.services.pk_tax import (
    calculate_bonus_236z_tax,
    calculate_dividend_wht,
    calculate_intl_dividend_wht,
    compute_psx_trade_charges,
    fiscal_year_start_year,
    normalize_filer_status,
    parse_fiscal_year_bounds,
    round_pkr,
)
from app.services.portfolio_sync import (
    load_price_history_for_assets,
    rebuild_portfolio_materializations,
    synthesize_accrued_daily_dividend_events,
    transactions_to_events,
)
from app.scrapers.sbp_macro import get_live_risk_free_rate_pct
from app.services.quant import compute_portfolio_quant_analytics

router = APIRouter(tags=["portfolio"])

D = Decimal
Q8 = D("0.00000001")


def _filer_status(user: User | None) -> str:
    return normalize_filer_status(user.tax_profile.filer_status if user and user.tax_profile else "ACTIVE_FILER")


def _foreign_cgt_rate_pct(user: User | None) -> Decimal:
    if user and user.tax_profile and user.tax_profile.foreign_cgt_rate_pct is not None:
        return D(str(user.tax_profile.foreign_cgt_rate_pct))
    return D("15.00")


def _us_w8ben_active(user: User | None) -> bool:
    return bool(user.tax_profile.us_w8ben_active) if user and user.tax_profile is not None else True


def _dividend_wht_for_asset(gross: Decimal, asset: Asset, user: User | None) -> Decimal:
    if gross <= 0:
        return D("0.00")
    if asset.fixed_income_profile and asset.fixed_income_profile.is_wht_exempt:
        return D("0.00")
    if asset.asset_class in {"INTL_EQUITY", "INTL_ETF"} or asset.currency == "USD":
        country = (asset.psx_profile.country if asset.psx_profile else "US").upper()
        custom_rate = (
            asset.psx_profile.default_wht_rate_pct
            if asset.psx_profile and country not in {"US", "USA", "IE", "IRELAND"}
            else None
        )
        return calculate_intl_dividend_wht(
            gross,
            country=country,
            us_w8ben_active=_us_w8ben_active(user),
            custom_wht_rate_pct=custom_rate,
        )
    return calculate_dividend_wht(gross, filer_status=_filer_status(user))


def _default_fiscal_year(ref_date: date | None = None) -> str:
    d = ref_date or date.today()
    start_year = d.year if d.month >= 7 else d.year - 1
    return f"FY{start_year}-{str(start_year + 1)[-2:]}"


def _add_months(src: date, months: int) -> date:
    zero_month = (src.month - 1) + months
    year = src.year + zero_month // 12
    month = (zero_month % 12) + 1
    day = min(src.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _serialize_portfolio(p: Portfolio) -> PortfolioOut:
    return PortfolioOut(
        id=p.id,
        name=p.name,
        base_currency=p.base_currency,
        description=p.description,
        auto_manage_cash=p.auto_manage_cash,
        is_default=p.is_default,
        created_at=p.created_at,
    )


def _serialize_transaction(tx: Transaction, asset: Asset) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        portfolio_id=tx.portfolio_id,
        asset_id=tx.asset_id,
        asset_symbol=asset.symbol,
        asset_name=asset.name,
        asset_class=asset.asset_class,
        tx_type=tx.tx_type,
        tx_date=tx.tx_date,
        quantity=tx.quantity,
        unit_price=tx.unit_price,
        gross_amount=tx.gross_amount,
        fee_amount=tx.fee_amount,
        tax_withheld=tx.tax_withheld,
        deemed_ex_price=tx.deemed_ex_price,
        shariah_purification_pkr=tx.shariah_purification_pkr,
        fx_rate_to_pkr=tx.fx_rate_to_pkr,
        corp_action_id=tx.corp_action_id,
        notes=tx.notes,
        created_at=tx.created_at,
    )


async def _get_owned_portfolio(portfolio_id: str, user: User, db: AsyncSession) -> Portfolio:
    stmt = select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user.id)
    portfolio = (await db.execute(stmt)).scalar_one_or_none()
    if portfolio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")
    return portfolio


async def _load_portfolio_transactions(portfolio_id: str, db: AsyncSession) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .options(
            selectinload(Transaction.asset).selectinload(Asset.shariah_metric),
            selectinload(Transaction.asset).selectinload(Asset.fixed_income_profile),
            selectinload(Transaction.asset).selectinload(Asset.mutual_fund_profile),
            selectinload(Transaction.asset).selectinload(Asset.fmr_snapshot),
            selectinload(Transaction.asset).selectinload(Asset.psx_profile),
        )
        .where(Transaction.portfolio_id == portfolio_id)
        .order_by(Transaction.tx_date.asc(), Transaction.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/api/v1/portfolios", response_model=list[PortfolioOut])
async def list_portfolios(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.created_at.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_portfolio(p) for p in rows]


@router.post("/api/v1/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    payload: PortfolioCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = Portfolio(
        user_id=user.id,
        name=payload.name.strip(),
        base_currency=payload.base_currency.upper(),
        description=payload.description.strip(),
        auto_manage_cash=payload.auto_manage_cash,
        is_default=False,
    )
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return _serialize_portfolio(portfolio)


@router.patch("/api/v1/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def update_portfolio(
    portfolio_id: str,
    payload: PortfolioUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    auto_cash_changed = False

    if payload.name is not None:
        portfolio.name = payload.name.strip()
    if payload.base_currency is not None:
        portfolio.base_currency = payload.base_currency.upper()
    if payload.description is not None:
        portfolio.description = payload.description.strip()
    if payload.auto_manage_cash is not None and payload.auto_manage_cash != portfolio.auto_manage_cash:
        portfolio.auto_manage_cash = payload.auto_manage_cash
        auto_cash_changed = True
    if payload.is_default is True:
        others = (
            await db.execute(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.id != portfolio.id))
        ).scalars().all()
        for other in others:
            other.is_default = False
        portfolio.is_default = True

    if auto_cash_changed:
        usd_to_pkr = await get_usd_to_pkr_rate(db)
        await rebuild_portfolio_materializations(
            portfolio,
            db,
            usd_to_pkr=usd_to_pkr,
            filer_status=_filer_status(user),
            commit=False,
            foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
        )

    await db.commit()
    await db.refresh(portfolio)
    return _serialize_portfolio(portfolio)


@router.delete("/api/v1/portfolios/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    total_count = (
        await db.execute(select(func.count(Portfolio.id)).where(Portfolio.user_id == user.id))
    ).scalar_one()
    if total_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your only remaining portfolio.",
        )

    was_default = portfolio.is_default
    await db.delete(portfolio)
    await db.flush()
    if was_default:
        replacement = (
            await db.execute(
                select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.created_at.asc()).limit(1)
            )
        ).scalar_one_or_none()
        if replacement is not None:
            replacement.is_default = True
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/portfolios/{portfolio_id}/summary", response_model=PortfolioSummaryOut)
async def portfolio_summary(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)

    txs = await _load_portfolio_transactions(portfolio.id, db)
    assets_by_id = {tx.asset_id: tx.asset for tx in txs}
    rf_pkr = float(await get_live_risk_free_rate_pct(db, "PKR"))
    base_events = transactions_to_events(txs)
    dd_events, dd_meta = synthesize_accrued_daily_dividend_events(
        txs,
        filer_status=filer_status,
        usd_to_pkr=usd_to_pkr,
        fallback_rf_pct=rf_pkr,
    )
    events = sorted(base_events + dd_events, key=lambda e: e.tx_date)
    asset_ids = {tx.asset_id for tx in txs}
    min_date = min((tx.tx_date for tx in txs), default=None)
    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=min_date)

    val = evaluate_portfolio(
        events,
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )
    total_mv = val.total_market_value_pkr

    holdings_out: list[HoldingPositionOut] = []
    total_dd_accrued = D("0")
    total_dd_monthly = D("0")
    for h in val.holdings:
        asset_obj = assets_by_id.get(h.asset_id)
        mf = asset_obj.mutual_fund_profile if asset_obj else None
        dm = dd_meta.get(h.asset_id)
        is_daily_div = bool((mf and mf.is_daily_dividend) or h.symbol in {"MRAF", "NIDDF", "MDIP1", "MMMP", "MCBADDUB", "MICF", "MCF", "SGOV"})
        ann_yield = (
            dm["effective_yield_pct"]
            if dm and dm.get("effective_yield_pct") is not None
            else (
                mf.annualized_yield_pct
                if mf and mf.annualized_yield_pct is not None and mf.annualized_yield_pct > 0
                else (
                    asset_obj.psx_profile.dividend_yield_pct
                    if asset_obj and asset_obj.psx_profile and h.symbol == "SGOV"
                    else None
                )
            )
        )
        acc_pkr = dm["accrued_unposted_pkr"] if dm else D("0")
        day_inc = dm["est_daily_income_pkr"] if dm else D("0")
        mon_inc = dm["est_monthly_income_pkr"] if dm else D("0")
        total_dd_accrued += acc_pkr
        total_dd_monthly += mon_inc
        holdings_out.append(
            HoldingPositionOut(
                asset_id=h.asset_id,
                symbol=h.symbol,
                name=h.name,
                asset_class=h.asset_class,
                sector_or_category=h.sector_or_category,
                is_shariah_compliant=h.is_shariah_compliant,
                quantity=h.quantity,
                avg_cost_pkr=h.avg_cost_pkr,
                cost_basis_pkr=h.cost_basis_pkr,
                current_price_pkr=h.current_price_pkr,
                market_value_pkr=h.market_value_pkr,
                unrealized_pnl_pkr=h.unrealized_pnl_pkr,
                unrealized_pnl_pct=h.unrealized_pnl_pct,
                realized_pnl_pkr=h.realized_pnl_pkr,
                dividends_net_pkr=h.dividends_net_pkr,
                shariah_purification_pkr=h.shariah_purification_pkr,
                allocation_pct=round_pkr((h.market_value_pkr / total_mv) * D("100")) if total_mv > 0 else D("0"),
                currency=h.currency,
                avg_cost_native=h.avg_cost_native,
                current_price_native=h.current_price_native,
                native_return_pct=h.native_return_pct,
                fx_return_pct=h.fx_return_pct,
                is_daily_dividend_fund=is_daily_div,
                annualized_yield_pct=ann_yield,
                accrued_daily_dividend_pkr=acc_pkr,
                est_daily_income_pkr=day_inc,
                est_monthly_income_pkr=mon_inc,
            )
        )

    nav_series_payload = [
        {
            "date": pt.snapshot_date.isoformat(),
            "total_value_pkr": float(pt.total_value_pkr),
            "total_cost_basis_pkr": float(pt.total_cost_basis_pkr),
            "net_invested_pkr": float(pt.net_invested_pkr),
            "virtual_units": float(pt.virtual_units),
            "nav_per_unit": float(pt.nav_per_unit),
        }
        for pt in val.daily_nav_series
    ]

    return PortfolioSummaryOut(
        portfolio=_serialize_portfolio(portfolio),
        usd_to_pkr=usd_to_pkr,
        total_market_value_pkr=val.total_market_value_pkr,
        total_cost_basis_pkr=val.total_cost_basis_pkr,
        total_unrealized_pnl_pkr=val.total_unrealized_pnl_pkr,
        total_unrealized_pnl_pct=val.total_unrealized_pnl_pct,
        total_realized_pnl_pkr=val.total_realized_pnl_pkr,
        total_net_dividends_pkr=val.total_net_dividends_pkr,
        total_shariah_purification_pkr=val.total_shariah_purification_pkr,
        total_fees_pkr=val.total_fees_pkr,
        total_tax_withheld_pkr=val.total_tax_withheld_pkr,
        estimated_cgt_pkr=val.estimated_cgt_pkr,
        xirr_pct=val.xirr_pct,
        twrr_pct=val.twrr_pct,
        nav_per_unit=val.nav_per_unit,
        shariah_compliant_pct=val.shariah_compliant_pct,
        allocation_by_class=val.allocation_by_class,
        daily_nav_series=nav_series_payload,
        holdings=holdings_out,
        cgt_by_fy=[asdict(s) for s in val.cgt_by_fy.values()],
        warnings=val.warnings,
        daily_dividend_auto_accrued_pkr=round_pkr(total_dd_accrued),
        daily_dividend_est_monthly_pkr=round_pkr(total_dd_monthly),
    )


@router.get("/api/v1/portfolios/{portfolio_id}/benchmark")
async def portfolio_benchmark_comparison(
    portfolio_id: str,
    symbol: str = Query(default="KSE100"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare portfolio unitized TWRR NAV against a PSX or Global benchmark index (KSE100, KMI30, SPX) rebased to 100."""
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    bench_sym = symbol.strip().upper()
    if bench_sym not in {"KSE100", "KMI30", "KSE30", "SPX"}:
        bench_sym = "KSE100"

    bench_asset = (
        await db.execute(select(Asset).where(Asset.symbol == bench_sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()
    if bench_asset is None:
        return {"benchmark_symbol": bench_sym, "series": []}

    usd_to_pkr = await get_usd_to_pkr_rate(db)
    txs = await _load_portfolio_transactions(portfolio.id, db)
    if not txs:
        return {"benchmark_symbol": bench_sym, "series": []}

    asset_ids = {tx.asset_id for tx in txs}
    min_date = min(tx.tx_date for tx in txs)
    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=min_date)
    val = evaluate_portfolio(
        transactions_to_events(txs),
        usd_to_pkr=usd_to_pkr,
        filer_status=_filer_status(user),
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
    )
    if not val.daily_nav_series:
        return {"benchmark_symbol": bench_sym, "series": []}

    if get_settings().environment.lower() != "test":
        if bench_asset.asset_class in {"PSX_INDEX", "PSX_EQUITY", "PSX_ETF"}:
            await ensure_psx_asset_backfilled(db, bench_asset, need_fundamentals=False)
        elif bench_asset.asset_class in {"INTL_INDEX", "INTL_EQUITY", "INTL_ETF"}:
            await ensure_yfinance_asset_backfilled(db, bench_asset)

    bars = (
        await db.execute(
            select(PriceBar.bar_time, PriceBar.close)
            .where(PriceBar.asset_id == bench_asset.id, PriceBar.timeframe == "1D")
            .order_by(PriceBar.bar_time.asc())
        )
    ).all()
    if not bars:
        return {"benchmark_symbol": bench_sym, "series": []}

    bench_by_date: list[tuple[date, float]] = [
        (bt.date() if hasattr(bt, "date") else bt, float(c)) for bt, c in bars if c > 0
    ]
    if not bench_by_date:
        return {"benchmark_symbol": bench_sym, "series": []}

    start_date = max(val.daily_nav_series[0].snapshot_date, bench_by_date[0][0])
    aligned_snaps = [s for s in val.daily_nav_series if s.snapshot_date >= start_date]
    if not aligned_snaps:
        return {"benchmark_symbol": bench_sym, "series": []}

    def _bench_at(d: date) -> float:
        chosen = bench_by_date[0][1]
        for bd, px in bench_by_date:
            if bd <= d:
                chosen = px
            else:
                break
        return chosen

    base_port = float(aligned_snaps[0].nav_per_unit) or 100.0
    base_bench = _bench_at(aligned_snaps[0].snapshot_date) or 1.0

    series = [
        {
            "date": s.snapshot_date.isoformat(),
            "portfolio_index": round((float(s.nav_per_unit) / base_port) * 100.0, 2),
            "benchmark_index": round((_bench_at(s.snapshot_date) / base_bench) * 100.0, 2),
        }
        for s in aligned_snaps
    ]
    return {"benchmark_symbol": bench_sym, "series": series}


@router.get("/api/v1/portfolios/{portfolio_id}/transactions", response_model=list[TransactionOut])
async def list_transactions(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.asset))
        .where(Transaction.portfolio_id == portfolio.id)
        .order_by(desc(Transaction.tx_date), desc(Transaction.created_at))
    )
    txs = (await db.execute(stmt)).scalars().all()
    return [_serialize_transaction(tx, tx.asset) for tx in txs]


@router.post(
    "/api/v1/portfolios/{portfolio_id}/transactions",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_transaction(
    portfolio_id: str,
    payload: TransactionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    asset = await resolve_asset_by_symbol(
        payload.asset_symbol,
        db,
        options=[
            selectinload(Asset.shariah_metric),
            selectinload(Asset.fixed_income_profile),
            selectinload(Asset.psx_profile),
            selectinload(Asset.mutual_fund_profile),
        ],
        owner_user_id=user.id,
    )
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{payload.asset_symbol}' not found.")

    filer_status = _filer_status(user)
    usd_to_pkr = await get_usd_to_pkr_rate(db)

    fx_rate = payload.fx_rate_to_pkr
    if asset.currency == "USD" and fx_rate == D("1"):
        fx_rate = usd_to_pkr

    fee = payload.fee_amount
    if fee == 0 and payload.tx_type in {"BUY", "SELL"} and asset.asset_class in {"PSX_EQUITY", "PSX_ETF"}:
        bps = user.tax_profile.default_broker_rate_bps if user.tax_profile else 15
        waive_brokerage = False
        if payload.tx_type == "SELL":
            same_day_buy = (
                await db.execute(
                    select(Transaction.id)
                    .where(
                        Transaction.portfolio_id == portfolio.id,
                        Transaction.asset_id == asset.id,
                        Transaction.tx_date == payload.tx_date,
                        Transaction.tx_type == "BUY",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            waive_brokerage = same_day_buy is not None
        trade_val = payload.gross_amount if payload.gross_amount > 0 else payload.quantity * payload.unit_price
        fee = compute_psx_trade_charges(
            trade_val,
            payload.quantity,
            broker_rate_bps=bps,
            waive_brokerage=waive_brokerage,
        )["total_charges"]

    tax_wht = payload.tax_withheld
    deemed_ex_price = payload.deemed_ex_price
    quantity = payload.quantity
    purification_pkr = D("0")

    if payload.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}:
        gross = payload.gross_amount if payload.gross_amount > 0 else payload.quantity * payload.unit_price
        if tax_wht == 0 and gross > 0:
            tax_wht = _dividend_wht_for_asset(gross, asset, user)
        if asset.shariah_metric and asset.shariah_metric.purification_rate_pct > 0 and gross > 0:
            purification_pkr = round_pkr(
                gross * fx_rate * (asset.shariah_metric.purification_rate_pct / D("100"))
            )
        if payload.tx_type == "REINVEST_DIVIDEND" and quantity == 0 and payload.unit_price > 0 and gross > 0:
            net_div = max(D("0"), gross - tax_wht - fee)
            quantity = (net_div / payload.unit_price).quantize(Q8)

    elif payload.tx_type == "BONUS":
        if deemed_ex_price == 0 and asset.last_price > 0:
            deemed_ex_price = asset.last_price
        if tax_wht == 0 and quantity > 0 and deemed_ex_price > 0:
            tax_wht = calculate_bonus_236z_tax(quantity, deemed_ex_price, filer_status=filer_status)

    tx = Transaction(
        portfolio_id=portfolio.id,
        asset_id=asset.id,
        tx_type=payload.tx_type,
        tx_date=payload.tx_date,
        quantity=quantity,
        unit_price=payload.unit_price,
        gross_amount=payload.gross_amount,
        fee_amount=fee,
        tax_withheld=tax_wht,
        deemed_ex_price=deemed_ex_price,
        shariah_purification_pkr=purification_pkr,
        fx_rate_to_pkr=fx_rate,
        corp_action_id=payload.corp_action_id,
        notes=payload.notes.strip(),
    )
    db.add(tx)
    await db.flush()
    await rebuild_portfolio_materializations(
        portfolio,
        db,
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        commit=False,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )
    await db.commit()
    await db.refresh(tx)

    return _serialize_transaction(tx, asset)


@router.patch("/api/v1/portfolios/{portfolio_id}/transactions/{tx_id}", response_model=TransactionOut)
async def update_transaction(
    portfolio_id: str,
    tx_id: str,
    payload: TransactionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    tx = (
        await db.execute(
            select(Transaction)
            .options(selectinload(Transaction.asset).selectinload(Asset.shariah_metric))
            .where(Transaction.id == tx_id, Transaction.portfolio_id == portfolio.id)
        )
    ).scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")

    if payload.tx_type is not None:
        tx.tx_type = payload.tx_type
    if payload.tx_date is not None:
        tx.tx_date = payload.tx_date
    if payload.quantity is not None:
        tx.quantity = payload.quantity
    if payload.unit_price is not None:
        tx.unit_price = payload.unit_price
    if payload.gross_amount is not None:
        tx.gross_amount = payload.gross_amount
    if payload.fee_amount is not None:
        tx.fee_amount = payload.fee_amount
    if payload.tax_withheld is not None:
        tx.tax_withheld = payload.tax_withheld
    if payload.deemed_ex_price is not None:
        tx.deemed_ex_price = payload.deemed_ex_price
    if payload.fx_rate_to_pkr is not None:
        tx.fx_rate_to_pkr = payload.fx_rate_to_pkr
    if payload.notes is not None:
        tx.notes = payload.notes.strip()

    if tx.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}:
        gross = tx.gross_amount if tx.gross_amount > 0 else tx.quantity * tx.unit_price
        if tx.asset.shariah_metric and tx.asset.shariah_metric.purification_rate_pct > 0 and gross > 0:
            tx.shariah_purification_pkr = round_pkr(
                gross * tx.fx_rate_to_pkr * (tx.asset.shariah_metric.purification_rate_pct / D("100"))
            )

    await db.flush()
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    await rebuild_portfolio_materializations(
        portfolio,
        db,
        usd_to_pkr=usd_to_pkr,
        filer_status=_filer_status(user),
        commit=False,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )
    await db.commit()
    await db.refresh(tx)
    return _serialize_transaction(tx, tx.asset)


@router.delete("/api/v1/portfolios/{portfolio_id}/transactions/{tx_id}")
async def delete_transaction(
    portfolio_id: str,
    tx_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    tx = (
        await db.execute(select(Transaction).where(Transaction.id == tx_id, Transaction.portfolio_id == portfolio.id))
    ).scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")

    await db.delete(tx)
    await db.flush()
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    await rebuild_portfolio_materializations(
        portfolio,
        db,
        usd_to_pkr=usd_to_pkr,
        filer_status=_filer_status(user),
        commit=False,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/portfolios/{portfolio_id}/pending-actions", response_model=list[PendingCorporateActionOut])
async def pending_corporate_actions(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect PSX/MUFAP corporate actions prior to ex-date that have not been applied to this ledger yet."""
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)

    txs = await _load_portfolio_transactions(portfolio.id, db)
    applied_ca_ids = {tx.corp_action_id for tx in txs if tx.corp_action_id}
    held_asset_ids = {tx.asset_id for tx in txs}
    if not held_asset_ids:
        return []

    actions = (
        await db.execute(
            select(CorporateAction)
            .options(
                selectinload(CorporateAction.asset).selectinload(Asset.psx_profile),
                selectinload(CorporateAction.asset).selectinload(Asset.fixed_income_profile),
            )
            .where(CorporateAction.asset_id.in_(list(held_asset_ids)))
            .order_by(desc(CorporateAction.ex_date))
        )
    ).scalars().all()

    events = transactions_to_events(txs)
    pending: list[PendingCorporateActionOut] = []
    for ca in actions:
        if ca.id in applied_ca_ids:
            continue
        prior_events = [e for e in events if e.asset_id == ca.asset_id and e.tx_date < ca.ex_date]
        if not prior_events:
            continue
        val_before_ex = evaluate_portfolio(
            prior_events,
            usd_to_pkr=usd_to_pkr,
            filer_status=filer_status,
            as_of_date=ca.ex_date - timedelta(days=1),
            auto_manage_cash=False,
        )
        pos = next((h for h in val_before_ex.holdings if h.asset_id == ca.asset_id), None)
        if pos is None or pos.quantity <= 0:
            continue

        est_gross = D("0")
        est_wht = D("0")
        est_bonus = D("0")
        deemed_ex_px = D("0")
        est_236z_tax = D("0")
        fx = usd_to_pkr if ca.asset.currency == "USD" else D("1")

        if ca.action_type == "CASH_DIVIDEND":
            gross_native = round_pkr(pos.quantity * ca.payout_per_share)
            wht_native = _dividend_wht_for_asset(gross_native, ca.asset, user)
            est_gross = round_pkr(gross_native * fx)
            est_wht = round_pkr(wht_native * fx)
        elif ca.action_type == "BONUS_ISSUE" and ca.ratio_denominator > 0:
            est_bonus = (pos.quantity * (ca.ratio_numerator / ca.ratio_denominator)).quantize(D("0.0001"))
            deemed_ex_px = ca.right_price if ca.right_price and ca.right_price > 0 else ca.asset.last_price
            est_236z_tax = calculate_bonus_236z_tax(est_bonus, deemed_ex_px, filer_status=filer_status)

        pending.append(
            PendingCorporateActionOut(
                corp_action_id=ca.id,
                asset_id=ca.asset_id,
                symbol=ca.asset.symbol,
                asset_name=ca.asset.name,
                action_type=ca.action_type,
                ex_date=ca.ex_date,
                eligible_quantity=pos.quantity,
                estimated_gross_pkr=est_gross,
                estimated_wht_pkr=est_wht,
                estimated_bonus_shares=est_bonus,
                deemed_ex_price=deemed_ex_px,
                estimated_236z_tax_pkr=est_236z_tax,
                description=ca.description,
            )
        )
    return pending


@router.post("/api/v1/portfolios/{portfolio_id}/apply-action/{corp_action_id}", response_model=TransactionOut)
async def apply_corporate_action(
    portfolio_id: str,
    corp_action_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply a pending PSX/Global cash dividend or Section 236Z bonus issue to the portfolio in 1 click."""
    pending = await pending_corporate_actions(portfolio_id, user, db)
    target = next((p for p in pending if p.corp_action_id == corp_action_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corporate action is not pending or portfolio had no eligible shares prior to ex-date.",
        )

    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    ca = (await db.execute(select(CorporateAction).where(CorporateAction.id == corp_action_id))).scalar_one()
    asset = (
        await db.execute(
            select(Asset)
            .options(
                selectinload(Asset.shariah_metric),
                selectinload(Asset.psx_profile),
                selectinload(Asset.fixed_income_profile),
            )
            .where(Asset.id == ca.asset_id)
        )
    ).scalar_one()

    fx = usd_to_pkr if asset.currency == "USD" else D("1")
    purification_pkr = D("0")
    deemed_ex_price = D("0")
    if ca.action_type == "CASH_DIVIDEND":
        tx_type = "DIVIDEND"
        qty = target.eligible_quantity
        unit_price = ca.payout_per_share
        gross = round_pkr(qty * ca.payout_per_share)
        wht = _dividend_wht_for_asset(gross, asset, user)
        if asset.shariah_metric and asset.shariah_metric.purification_rate_pct > 0 and gross > 0:
            purification_pkr = round_pkr(gross * fx * (asset.shariah_metric.purification_rate_pct / D("100")))
    elif ca.action_type == "BONUS_ISSUE":
        tx_type = "BONUS"
        qty = target.estimated_bonus_shares
        unit_price = D("0")
        gross = D("0")
        deemed_ex_price = target.deemed_ex_price
        wht = target.estimated_236z_tax_pkr
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported 1-click action type.")

    tx = Transaction(
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        tx_type=tx_type,
        tx_date=ca.ex_date,
        quantity=qty,
        unit_price=unit_price,
        gross_amount=gross,
        fee_amount=D("0"),
        tax_withheld=wht,
        deemed_ex_price=deemed_ex_price,
        shariah_purification_pkr=purification_pkr,
        fx_rate_to_pkr=fx,
        corp_action_id=ca.id,
        notes=f"Auto-applied corporate action: {ca.description}",
    )
    db.add(tx)
    await db.flush()
    await rebuild_portfolio_materializations(portfolio, db, usd_to_pkr=usd_to_pkr, filer_status=_filer_status(user))
    await db.commit()
    await db.refresh(tx)

    return _serialize_transaction(tx, asset)


@router.post("/api/v1/portfolios/{portfolio_id}/accrue-nss")
async def accrue_nss_coupons(
    portfolio_id: str,
    as_of_date: date | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate due National Savings (NSS) monthly/semi-annual coupon payouts as DIVIDEND transactions."""
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    target_date = as_of_date or date.today()
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)

    txs = await _load_portfolio_transactions(portfolio.id, db)
    events = transactions_to_events(txs)

    by_asset: dict[str, list[Transaction]] = {}
    for tx in txs:
        if tx.asset.fixed_income_profile is not None:
            by_asset.setdefault(tx.asset_id, []).append(tx)

    created_txs: list[TransactionOut] = []
    total_gross = D("0")
    total_wht = D("0")

    for asset_id, asset_txs in by_asset.items():
        asset = asset_txs[0].asset
        profile = asset.fixed_income_profile
        assert profile is not None

        acq_dates = [t.tx_date for t in asset_txs if t.tx_type in {"BUY", "OPENING_BALANCE", "DEPOSIT"}]
        if not acq_dates:
            continue
        first_acq = min(acq_dates)
        existing_coupon_dates = {t.tx_date for t in asset_txs if t.tx_type == "DIVIDEND"}

        if profile.payout_frequency == "MONTHLY":
            step_months = 1
            period_factor = D("1") / D("12")
        elif profile.payout_frequency == "SEMI_ANNUAL":
            step_months = 6
            period_factor = D("0.5")
        else:
            step_months = 12
            period_factor = D("1")

        k = 1
        while k <= 120:
            coupon_date = _add_months(first_acq, k * step_months)
            k += 1
            if coupon_date > target_date:
                break
            if profile.maturity_date and coupon_date > profile.maturity_date:
                break
            if coupon_date in existing_coupon_dates:
                continue

            prior_events = [e for e in events if e.asset_id == asset_id and e.tx_date <= coupon_date]
            val_at_date = evaluate_portfolio(
                prior_events,
                usd_to_pkr=usd_to_pkr,
                filer_status=filer_status,
                as_of_date=coupon_date,
                auto_manage_cash=False,
            )
            pos = next((h for h in val_at_date.holdings if h.asset_id == asset_id), None)
            if pos is None or pos.cost_basis_pkr <= 0:
                continue

            gross_profit = round_pkr(pos.cost_basis_pkr * (profile.annual_profit_rate_pct / D("100")) * period_factor)
            if gross_profit <= 0:
                continue

            wht = D("0") if profile.is_wht_exempt else calculate_dividend_wht(gross_profit, filer_status=filer_status)
            new_tx = Transaction(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                tx_type="DIVIDEND",
                tx_date=coupon_date,
                quantity=D("1"),
                unit_price=gross_profit,
                gross_amount=gross_profit,
                fee_amount=D("0"),
                tax_withheld=wht,
                deemed_ex_price=D("0"),
                shariah_purification_pkr=D("0"),
                fx_rate_to_pkr=D("1"),
                notes=f"NSS {profile.instrument_type} {profile.payout_frequency} coupon accrual ({profile.annual_profit_rate_pct}% p.a.)",
            )
            db.add(new_tx)
            existing_coupon_dates.add(coupon_date)
            total_gross += gross_profit
            total_wht += wht
            await db.flush()
            created_txs.append(_serialize_transaction(new_tx, asset))

    if created_txs:
        await rebuild_portfolio_materializations(portfolio, db, usd_to_pkr=usd_to_pkr, filer_status=filer_status)
        await db.commit()

    return {
        "accrued_count": len(created_txs),
        "total_gross_pkr": round_pkr(total_gross),
        "total_wht_pkr": round_pkr(total_wht),
        "transactions": created_txs,
    }


@router.post("/api/v1/portfolios/{portfolio_id}/accrue-daily-funds", response_model=DailyFundAccrualOut)
async def accrue_daily_dividend_funds(
    portfolio_id: str,
    payload: DailyFundAccrualRequest = DailyFundAccrualRequest(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Auto-Accrual Engine for Constant-NAV Daily Dividend Funds (MRAF, NIDDF, MICF, MCF, SGOV).
    Simulates daily dividend accrual (after 15%/22.5%/30% WHT or US W-8BEN WHT), either compounding
    new units daily at Constant Par NAV (`REINVEST`) or paying cash (`CASH`), rolled up cleanly
    into `MONTHLY`, `SINGLE`, or `DAILY` ledger entries.
    """
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    target_date = payload.as_of_date or date.today()
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)

    txs = await _load_portfolio_transactions(portfolio.id, db)
    events = transactions_to_events(txs)

    target_sym = payload.asset_symbol.strip().upper() if payload.asset_symbol else None
    by_asset: dict[str, list[Transaction]] = {}
    for tx in txs:
        asset = tx.asset
        mf = asset.mutual_fund_profile
        is_candidate = (
            (asset.symbol == target_sym)
            if target_sym
            else bool((mf and mf.is_daily_dividend) or asset.symbol in {"MRAF", "NIDDF", "MICF", "MCF", "ABLF", "SGOV"})
        )
        if is_candidate:
            by_asset.setdefault(tx.asset_id, []).append(tx)

    created_txs: list[TransactionOut] = []
    total_days_accrued = 0
    total_gross_pkr = D("0")
    total_wht_pkr = D("0")
    total_net_pkr = D("0")
    total_reinvested_units = D("0")

    mode = (payload.mode or "REINVEST").strip().upper()
    if mode not in {"REINVEST", "CASH"}:
        mode = "REINVEST"
    rollup = (payload.rollup or "MONTHLY").strip().upper()
    if rollup not in {"MONTHLY", "SINGLE", "DAILY"}:
        rollup = "MONTHLY"

    for asset_id, asset_txs in by_asset.items():
        asset = asset_txs[0].asset
        mf = asset.mutual_fund_profile
        fx = usd_to_pkr if asset.currency == "USD" else D("1")

        acq_dates = [t.tx_date for t in asset_txs if t.tx_type in {"BUY", "OPENING_BALANCE", "DEPOSIT"}]
        if not acq_dates:
            continue
        first_acq = min(acq_dates)
        div_dates = [t.tx_date for t in asset_txs if t.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}]
        last_div = max(div_dates) if div_dates else None

        start_d = (last_div + timedelta(days=1)) if (last_div and last_div >= first_acq) else (first_acq + timedelta(days=1))
        if start_d > target_date:
            continue

        # Determine annual yield & constant par NAV
        if payload.annual_yield_override_pct is not None and payload.annual_yield_override_pct > 0:
            annual_yield_pct = payload.annual_yield_override_pct
        elif mf and mf.annualized_yield_pct and mf.annualized_yield_pct > 0:
            annual_yield_pct = mf.annualized_yield_pct
        elif mf and mf.return_1y_pct and mf.return_1y_pct > 0:
            annual_yield_pct = mf.return_1y_pct
        elif asset.psx_profile and asset.psx_profile.dividend_yield_pct > 0:
            annual_yield_pct = asset.psx_profile.dividend_yield_pct
        else:
            annual_yield_pct = D("17.50")

        par_nav = (
            mf.constant_par_nav
            if (mf and mf.constant_par_nav and mf.constant_par_nav > 0)
            else (asset.last_price if asset.last_price > 0 else D("50.00"))
        )

        # Evaluate initial units held on start_d - 1 day
        prior_events = [e for e in events if e.asset_id == asset_id and e.tx_date < start_d]
        val_init = evaluate_portfolio(
            prior_events,
            usd_to_pkr=usd_to_pkr,
            filer_status=filer_status,
            as_of_date=start_d - timedelta(days=1),
            auto_manage_cash=False,
        )
        pos_init = next((h for h in val_init.holdings if h.asset_id == asset_id), None)
        current_units = pos_init.quantity if pos_init else D("0")

        # Map any BUY/SELL unit deltas occurring inside [start_d, target_date]
        daily_deltas: dict[date, Decimal] = {}
        for t in asset_txs:
            if start_d <= t.tx_date <= target_date:
                if t.tx_type in {"BUY", "OPENING_BALANCE", "BONUS"}:
                    daily_deltas[t.tx_date] = daily_deltas.get(t.tx_date, D("0")) + t.quantity
                elif t.tx_type == "SELL":
                    daily_deltas[t.tx_date] = daily_deltas.get(t.tx_date, D("0")) - t.quantity

        buckets: dict[tuple, dict] = {}
        cur_d = start_d
        while cur_d <= target_date:
            if cur_d in daily_deltas:
                current_units = max(D("0"), current_units + daily_deltas[cur_d])
            if current_units > 0:
                daily_gross = (current_units * par_nav * (annual_yield_pct / D("100"))) / D("365")
                daily_wht = _dividend_wht_for_asset(daily_gross, asset, user)
                daily_net = max(D("0"), daily_gross - daily_wht)
                reinvested_u = (daily_net / par_nav).quantize(Q8) if (mode == "REINVEST" and par_nav > 0) else D("0")

                if rollup == "MONTHLY":
                    b_key = (cur_d.year, cur_d.month)
                elif rollup == "SINGLE":
                    b_key = ("SINGLE",)
                else:
                    b_key = (cur_d.isoformat(),)

                b = buckets.setdefault(
                    b_key,
                    {
                        "tx_date": cur_d,
                        "days": 0,
                        "gross_native": D("0"),
                        "wht_native": D("0"),
                        "net_native": D("0"),
                        "reinvested_units": D("0"),
                        "end_units": current_units,
                    },
                )
                b["tx_date"] = cur_d
                b["days"] += 1
                b["gross_native"] += daily_gross
                b["wht_native"] += daily_wht
                b["net_native"] += daily_net
                b["reinvested_units"] += reinvested_u
                if mode == "REINVEST":
                    current_units += reinvested_u
                b["end_units"] = current_units
                total_days_accrued += 1
            cur_d += timedelta(days=1)

        for b in buckets.values():
            gross_nat = round_pkr(b["gross_native"])
            if gross_nat <= 0:
                continue
            wht_nat = _dividend_wht_for_asset(gross_nat, asset, user)
            net_nat = max(D("0"), gross_nat - wht_nat)
            reinv_u = (net_nat / par_nav).quantize(Q8) if (mode == "REINVEST" and par_nav > 0) else D("0")

            tx_type = "REINVEST_DIVIDEND" if mode == "REINVEST" else "DIVIDEND"
            qty = reinv_u if mode == "REINVEST" else b["end_units"].quantize(D("0.0001"))
            unit_px = par_nav if mode == "REINVEST" else (gross_nat / max(qty, D("1"))).quantize(D("0.0001"))

            new_tx = Transaction(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                tx_type=tx_type,
                tx_date=b["tx_date"],
                quantity=qty,
                unit_price=unit_px,
                gross_amount=gross_nat,
                fee_amount=D("0"),
                tax_withheld=wht_nat,
                deemed_ex_price=D("0"),
                shariah_purification_pkr=D("0"),
                fx_rate_to_pkr=fx,
                notes=(
                    f"Daily Dividend Auto-Accrual ({mode}, {b['days']}d @ {annual_yield_pct}% p.a., "
                    f"Par NAV {par_nav})"
                ),
            )
            db.add(new_tx)
            await db.flush()
            created_txs.append(_serialize_transaction(new_tx, asset))
            total_gross_pkr += round_pkr(gross_nat * fx)
            total_wht_pkr += round_pkr(wht_nat * fx)
            total_net_pkr += round_pkr(net_nat * fx)
            total_reinvested_units += reinv_u

    if created_txs:
        await rebuild_portfolio_materializations(portfolio, db, usd_to_pkr=usd_to_pkr, filer_status=filer_status)
        await db.commit()

    return DailyFundAccrualOut(
        accrued_count=len(created_txs),
        days_accrued=total_days_accrued,
        mode=mode,
        rollup=rollup,
        total_gross_pkr=round_pkr(total_gross_pkr),
        total_wht_pkr=round_pkr(total_wht_pkr),
        total_net_pkr=round_pkr(total_net_pkr),
        total_reinvested_units=total_reinvested_units.quantize(D("0.0001")),
        transactions=created_txs,
    )


@router.get("/api/v1/portfolios/{portfolio_id}/xray")
async def portfolio_look_through_xray(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    SECP FMR Look-Through Portfolio X-Ray:
    Explodes Mutual Fund & VPS holdings through parsed monthly Fund Manager Reports to reveal
    true underlying Asset Allocation, combined Sector Concentration, and Hidden Single-Stock Overlap
    between direct PSX holdings and mutual fund Top-10 equity positions.
    """
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)
    txs = await _load_portfolio_transactions(portfolio.id, db)

    if not txs:
        return {
            "portfolio_id": portfolio.id,
            "total_value_pkr": 0.0,
            "mutual_fund_value_pkr": 0.0,
            "fmr_covered_fund_pct": 0.0,
            "funds_with_fmr": [],
            "funds_missing_fmr": [],
            "look_through_asset_mix": [],
            "look_through_sectors": [],
            "stock_overlap": [],
        }

    asset_ids = {tx.asset_id for tx in txs}
    assets_by_id = {tx.asset_id: tx.asset for tx in txs}
    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=None)
    val = evaluate_portfolio(
        transactions_to_events(txs),
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )

    total_mv = float(val.total_market_value_pkr)
    if total_mv <= 0:
        return {
            "portfolio_id": portfolio.id,
            "total_value_pkr": 0.0,
            "mutual_fund_value_pkr": 0.0,
            "fmr_covered_fund_pct": 0.0,
            "funds_with_fmr": [],
            "funds_missing_fmr": [],
            "look_through_asset_mix": [],
            "look_through_sectors": [],
            "stock_overlap": [],
        }

    open_asset_ids = [h.asset_id for h in val.holdings if float(h.market_value_pkr) > 0]
    fmr_by_aid = {
        s.asset_id: s
        for s in (
            await db.execute(select(FundFmrSnapshot).where(FundFmrSnapshot.asset_id.in_(open_asset_ids)))
        ).scalars()
    } if open_asset_ids else {}

    asset_mix_pkr: dict[str, float] = {}
    sector_pkr: dict[str, float] = {}
    stock_exp: dict[str, dict[str, object]] = {}
    mf_total_pkr = 0.0
    mf_covered_pkr = 0.0
    funds_with_fmr: list[dict[str, object]] = []
    funds_missing_fmr: list[dict[str, object]] = []

    def _add_bucket(d: dict[str, float], key: str, amt: float) -> None:
        if amt > 0:
            d[key] = d.get(key, 0.0) + amt

    for h in val.holdings:
        mv = float(h.market_value_pkr)
        if mv <= 0:
            continue
        ac = str(h.asset_class or "")
        a_obj = assets_by_id.get(h.asset_id)
        sec = (a_obj.sector_or_category if a_obj else None) or ac

        if ac in {"MUTUAL_FUND", "VPS_PENSION"}:
            mf_total_pkr += mv
            fmr = fmr_by_aid.get(h.asset_id)
            if fmr is not None and (fmr.asset_allocation_json or fmr.top_holdings_json):
                mf_covered_pkr += mv
                funds_with_fmr.append(
                    {"symbol": h.symbol, "name": h.name, "report_label": fmr.report_label or fmr.report_month, "value_pkr": round(mv, 2)}
                )
                allocs = list(fmr.asset_allocation_json or [])
                alloc_sum = sum(float(x.get("pct") or 0.0) for x in allocs)
                if alloc_sum > 0:
                    for al in allocs:
                        lbl = str(al.get("label") or "Others")
                        share = float(al.get("pct") or 0.0) / max(alloc_sum, 100.0)
                        _add_bucket(asset_mix_pkr, lbl, mv * share)
                    if alloc_sum < 99.5:
                        _add_bucket(asset_mix_pkr, "Others", mv * ((100.0 - alloc_sum) / 100.0))
                else:
                    _add_bucket(asset_mix_pkr, "Mutual Funds (Unclassified)", mv)

                for sec_row in (fmr.sector_allocation_json or []):
                    s_name = str(sec_row.get("sector") or "Others")
                    s_pct = float(sec_row.get("pct") or 0.0)
                    if s_pct > 0:
                        _add_bucket(sector_pkr, s_name, mv * (s_pct / 100.0))

                for hold in (fmr.top_holdings_json or []):
                    sym = str(hold.get("symbol") or "").upper()
                    wt = float(hold.get("weight_pct") or 0.0)
                    if not sym or wt <= 0:
                        continue
                    indirect = mv * (wt / 100.0)
                    entry = stock_exp.setdefault(
                        sym,
                        {
                            "symbol": sym,
                            "name": str(hold.get("name") or sym),
                            "sector": str(hold.get("sector") or ""),
                            "direct_pkr": 0.0,
                            "via_funds_pkr": 0.0,
                            "contributing_funds": [],
                        },
                    )
                    entry["via_funds_pkr"] = float(entry["via_funds_pkr"]) + indirect  # type: ignore[arg-type]
                    entry["contributing_funds"].append(  # type: ignore[union-attr]
                        {"fund_symbol": h.symbol, "weight_in_fund_pct": round(wt, 2), "exposure_pkr": round(indirect, 2)}
                    )
            else:
                funds_missing_fmr.append({"symbol": h.symbol, "name": h.name, "value_pkr": round(mv, 2)})
                _add_bucket(asset_mix_pkr, "Mutual Funds (No FMR)", mv)
        elif ac in {"PSX_EQUITY", "PSX_ETF"}:
            _add_bucket(asset_mix_pkr, "Equities", mv)
            _add_bucket(sector_pkr, sec, mv)
            sym = h.symbol.upper()
            entry = stock_exp.setdefault(
                sym,
                {"symbol": sym, "name": h.name, "sector": sec, "direct_pkr": 0.0, "via_funds_pkr": 0.0, "contributing_funds": []},
            )
            entry["direct_pkr"] = float(entry["direct_pkr"]) + mv  # type: ignore[arg-type]
        elif ac in {"INTL_EQUITY", "INTL_ETF"}:
            _add_bucket(asset_mix_pkr, "Global Equities & ETFs", mv)
            _add_bucket(sector_pkr, f"Global: {sec}", mv)
        elif ac == "GOLD":
            _add_bucket(asset_mix_pkr, "Gold", mv)
        elif ac == "CRYPTO":
            _add_bucket(asset_mix_pkr, "Crypto", mv)
        elif ac == "NSS":
            _add_bucket(asset_mix_pkr, "GoP Sukuk / PIBs", mv)
        elif ac == "CASH":
            _add_bucket(asset_mix_pkr, "Cash & Bank", mv)
        else:
            _add_bucket(asset_mix_pkr, "Others", mv)

    look_through_mix = [
        {"label": k, "value_pkr": round(v, 2), "weight_pct": round((v / total_mv) * 100.0, 2)}
        for k, v in sorted(asset_mix_pkr.items(), key=lambda x: x[1], reverse=True)
    ]
    look_through_sectors = [
        {"sector": k, "value_pkr": round(v, 2), "weight_pct": round((v / total_mv) * 100.0, 2)}
        for k, v in sorted(sector_pkr.items(), key=lambda x: x[1], reverse=True)[:12]
    ]
    overlap_rows: list[dict[str, object]] = []
    for entry in stock_exp.values():
        d_pkr = float(entry["direct_pkr"])  # type: ignore[arg-type]
        f_pkr = float(entry["via_funds_pkr"])  # type: ignore[arg-type]
        tot_pkr = d_pkr + f_pkr
        overlap_rows.append(
            {
                "symbol": entry["symbol"],
                "name": entry["name"],
                "sector": entry["sector"],
                "direct_pkr": round(d_pkr, 2),
                "via_funds_pkr": round(f_pkr, 2),
                "total_pkr": round(tot_pkr, 2),
                "total_weight_pct": round((tot_pkr / total_mv) * 100.0, 2),
                "is_overlap": d_pkr > 0 and f_pkr > 0,
                "contributing_funds": entry["contributing_funds"],
            }
        )
    overlap_rows.sort(key=lambda x: (1 if x["is_overlap"] else 0, float(x["total_pkr"])), reverse=True)  # type: ignore[arg-type]

    return {
        "portfolio_id": portfolio.id,
        "total_value_pkr": round(total_mv, 2),
        "mutual_fund_value_pkr": round(mf_total_pkr, 2),
        "fmr_covered_fund_pct": round((mf_covered_pkr / mf_total_pkr) * 100.0, 1) if mf_total_pkr > 0 else 0.0,
        "funds_with_fmr": funds_with_fmr,
        "funds_missing_fmr": funds_missing_fmr,
        "look_through_asset_mix": look_through_mix,
        "look_through_sectors": look_through_sectors,
        "stock_overlap": overlap_rows[:15],
    }


@router.get("/api/v1/portfolios/{portfolio_id}/quant", response_model=PortfolioQuantOut)
async def portfolio_quant_analytics(
    portfolio_id: str,
    benchmark: str = Query(default="KSE100"),
    risk_free_rate_pct: float | None = Query(default=None, ge=0.0, le=50.0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Quantitative Risk & Modern Portfolio Theory Analytics powered by NumPy, Pandas, PyPortfolioOpt, and SciPy:
    Sharpe, Sortino, Calmar, Max Drawdown, Historical 95% VaR & CVaR, Beta/Alpha vs Benchmark,
    Pairwise Asset Correlation Matrix, Markowitz + HRP Weights, Efficient Frontier & Monte Carlo Stress Tests.
    """
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    resolved_rf = (
        float(risk_free_rate_pct)
        if risk_free_rate_pct is not None
        else float(await get_live_risk_free_rate_pct(db, portfolio.base_currency or "PKR"))
    )
    bench_sym = benchmark.strip().upper()
    if bench_sym not in {"KSE100", "KMI30", "KSE30", "SPX"}:
        bench_sym = "KSE100"

    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)
    txs = await _load_portfolio_transactions(portfolio.id, db)

    if not txs:
        empty = compute_portfolio_quant_analytics(
            portfolio_id=portfolio.id,
            daily_nav_series=[],
            holdings=[],
            price_history_by_symbol={},
            benchmark_symbol=bench_sym,
            benchmark_bars=[],
            risk_free_rate_pct=resolved_rf,
        )
        return PortfolioQuantOut(**empty)

    asset_ids = {tx.asset_id for tx in txs}
    assets_by_id = {tx.asset_id: tx.asset for tx in txs}
    if get_settings().environment.lower() != "test":
        for a_obj in assets_by_id.values():
            if a_obj.asset_class in {"PSX_EQUITY", "PSX_ETF"}:
                await ensure_psx_asset_backfilled(db, a_obj, need_fundamentals=False)
            elif a_obj.asset_class in {"INTL_EQUITY", "INTL_ETF"}:
                await ensure_yfinance_asset_backfilled(db, a_obj)

    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=None)
    val = evaluate_portfolio(
        transactions_to_events(txs),
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )

    nav_series = [
        {"date": s.snapshot_date, "nav_per_unit": float(s.nav_per_unit)}
        for s in val.daily_nav_series
    ]
    total_mv = val.total_market_value_pkr
    holdings_dicts = [
        {
            "asset_id": h.asset_id,
            "symbol": h.symbol,
            "name": h.name,
            "asset_class": h.asset_class,
            "currency": h.currency,
            "current_price_pkr": float(h.current_price_pkr),
            "market_value_pkr": float(h.market_value_pkr),
            "allocation_pct": float(round_pkr((h.market_value_pkr / total_mv) * D("100"))) if total_mv > 0 else 0.0,
        }
        for h in val.holdings
    ]

    # Map price_history (keyed by asset_id) to symbol and convert USD price series to PKR
    # so Markowitz covariance & correlation matrices use a unified PKR currency basis.
    usd_fx_by_date = {d: float(r) for d, r in usd_history}
    fallback_fx = float(usd_to_pkr)
    price_history_by_symbol: dict[str, list[tuple[date, float]]] = {}
    for h in val.holdings:
        aid = h.asset_id
        sym = h.symbol
        if aid in price_history:
            is_usd = (h.currency or "PKR").upper() == "USD"
            pts: list[tuple[date, float]] = []
            for dt, px in price_history[aid]:
                fx_mult = usd_fx_by_date.get(dt, fallback_fx) if is_usd else 1.0
                pts.append((dt, float(px) * fx_mult))
            price_history_by_symbol[sym] = pts

    # Load benchmark 1D price bars (lazily backfilling if needed)
    bench_bars: list[tuple[date, float]] = []
    bench_asset = (
        await db.execute(select(Asset).where(Asset.symbol == bench_sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()
    if bench_asset is not None:
        if get_settings().environment.lower() != "test":
            if bench_asset.asset_class in {"PSX_INDEX", "PSX_EQUITY"}:
                await ensure_psx_asset_backfilled(db, bench_asset, need_fundamentals=False)
            elif bench_asset.asset_class in {"INTL_INDEX", "INTL_EQUITY"}:
                await ensure_yfinance_asset_backfilled(db, bench_asset)
        raw_bars = (
            await db.execute(
                select(PriceBar.bar_time, PriceBar.close)
                .where(PriceBar.asset_id == bench_asset.id, PriceBar.timeframe == "1D")
                .order_by(PriceBar.bar_time.asc())
            )
        ).all()
        bench_bars = [
            (bt.date() if hasattr(bt, "date") else bt, float(c))
            for bt, c in raw_bars
            if c > 0
        ]

    quant_data = compute_portfolio_quant_analytics(
        portfolio_id=portfolio.id,
        daily_nav_series=nav_series,
        holdings=holdings_dicts,
        price_history_by_symbol=price_history_by_symbol,
        benchmark_symbol=bench_sym,
        benchmark_bars=bench_bars,
        risk_free_rate_pct=resolved_rf,
    )
    return PortfolioQuantOut(**quant_data)


@router.get("/api/v1/portfolios/{portfolio_id}/fbr-summary", response_model=FbrTaxYearSummaryOut)
async def fbr_tax_year_summary(
    portfolio_id: str,
    fiscal_year: str | None = Query(default=None),
    format: str = Query(default="json"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FBR Section 116(2) Wealth Statement Cost-Basis & Tax Year Reconciliation Export (July 1 – June 30)."""
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    fy = (fiscal_year or (user.tax_profile.fiscal_year if user.tax_profile else None) or _default_fiscal_year()).upper()
    try:
        period_start, period_end = parse_fiscal_year_bounds(fy)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid fiscal_year format. Expected e.g. FY2025-26.",
        ) from exc

    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(user)
    txs = await _load_portfolio_transactions(portfolio.id, db)
    all_events = transactions_to_events(txs)
    asset_ids = {tx.asset_id for tx in txs}
    min_date = min((tx.tx_date for tx in txs), default=None)
    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=min_date)

    opening_events = [e for e in all_events if e.tx_date < period_start]
    closing_events = [e for e in all_events if e.tx_date <= period_end]

    val_open = evaluate_portfolio(
        opening_events,
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        as_of_date=period_start - timedelta(days=1),
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )
    val_close = evaluate_portfolio(
        closing_events,
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        as_of_date=period_end,
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
        foreign_cgt_rate_pct=_foreign_cgt_rate_pct(user),
    )

    gross_divs = D("0")
    div_wht = D("0")
    bonus_236z = D("0")
    for tx in txs:
        if period_start <= tx.tx_date <= period_end:
            fx = tx.fx_rate_to_pkr if tx.fx_rate_to_pkr > 0 else D("1")
            if tx.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}:
                g = (tx.gross_amount if tx.gross_amount > 0 else tx.quantity * tx.unit_price) * fx
                gross_divs += g
                div_wht += tx.tax_withheld * fx
            elif tx.tx_type == "BONUS":
                bonus_236z += tx.tax_withheld * fx

    fy_summary = val_close.cgt_by_fy.get(fy)
    target_start = fiscal_year_start_year(fy)
    if fy_summary is not None:
        gross_gain = fy_summary.gross_gain_pkr
        gross_loss = fy_summary.gross_loss_pkr
        prior_loss_applied = fy_summary.prior_loss_applied_pkr
        net_taxable_gain = fy_summary.net_taxable_gain_pkr
        year_cgt = fy_summary.estimated_cgt_pkr
        unused_carry = fy_summary.unused_loss_carryforward_pkr
    else:
        gross_gain = gross_loss = prior_loss_applied = net_taxable_gain = year_cgt = D("0.00")
        unused_carry = D("0.00")
        prior_candidates = [
            (fiscal_year_start_year(k), v)
            for k, v in val_close.cgt_by_fy.items()
            if fiscal_year_start_year(k) < target_start and (target_start - fiscal_year_start_year(k)) <= 3
        ]
        if prior_candidates:
            prior_candidates.sort(key=lambda item: item[0], reverse=True)
            unused_carry = prior_candidates[0][1].unused_loss_carryforward_pkr

    year_realized_gain = round_pkr(gross_gain - gross_loss)
    year_purification = round_pkr(
        max(D("0"), val_close.total_shariah_purification_pkr - val_open.total_shariah_purification_pkr)
    )

    schedule = [
        {
            "symbol": h.symbol,
            "name": h.name,
            "asset_class": h.asset_class,
            "quantity": str(h.quantity),
            "avg_cost_pkr": str(h.avg_cost_pkr),
            "cost_basis_pkr": str(h.cost_basis_pkr),
            "market_value_pkr": str(h.market_value_pkr),
            "is_shariah_compliant": h.is_shariah_compliant,
        }
        for h in val_close.holdings
    ]

    summary = FbrTaxYearSummaryOut(
        fiscal_year=fy,
        period_start=period_start,
        period_end=period_end,
        filer_status=filer_status,
        opening_cost_basis_pkr=val_open.total_cost_basis_pkr,
        closing_cost_basis_pkr=val_close.total_cost_basis_pkr,
        closing_market_value_pkr=val_close.total_market_value_pkr,
        total_gross_dividends_pkr=round_pkr(gross_divs),
        total_dividend_wht_pkr=round_pkr(div_wht),
        total_236z_bonus_tax_pkr=round_pkr(bonus_236z),
        total_realized_gain_pkr=year_realized_gain,
        gross_gain_pkr=gross_gain,
        gross_loss_pkr=gross_loss,
        prior_loss_applied_pkr=prior_loss_applied,
        net_taxable_gain_pkr=net_taxable_gain,
        estimated_cgt_pkr=year_cgt,
        unused_loss_carryforward_pkr=unused_carry,
        total_shariah_purification_pkr=year_purification,
        holdings_cost_schedule=schedule,
        warnings=val_close.warnings,
    )

    if format.lower() == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["OpenKhazana FBR Tax Year & Sec 116(2) Wealth Reconciliation", fy])
        writer.writerow(["Portfolio", portfolio.name])
        writer.writerow(["Filer Status", filer_status])
        writer.writerow(["Period", f"{period_start.isoformat()} to {period_end.isoformat()}"])
        writer.writerow(["Opening Cost Basis (PKR)", str(summary.opening_cost_basis_pkr)])
        writer.writerow(["Closing Cost Basis Sec 116(2) (PKR)", str(summary.closing_cost_basis_pkr)])
        writer.writerow(["Closing Market Value (PKR)", str(summary.closing_market_value_pkr)])
        writer.writerow(["Gross Dividends & NSS Profit (PKR)", str(summary.total_gross_dividends_pkr)])
        writer.writerow(["Dividend WHT Paid (PKR)", str(summary.total_dividend_wht_pkr)])
        writer.writerow(["Section 236Z Bonus Tax Paid (PKR)", str(summary.total_236z_bonus_tax_pkr)])
        writer.writerow(["Gross Realized Capital Gains (PKR)", str(summary.gross_gain_pkr)])
        writer.writerow(["Realized Capital Losses (PKR)", str(summary.gross_loss_pkr)])
        writer.writerow(["Prior 3-Yr Loss Carryforward Applied (PKR)", str(summary.prior_loss_applied_pkr)])
        writer.writerow(["Net Taxable Capital Gain (PKR)", str(summary.net_taxable_gain_pkr)])
        writer.writerow(["Net Realized Capital Gain/Loss (PKR)", str(summary.total_realized_gain_pkr)])
        writer.writerow(["Estimated FBR CGT Liability (PKR)", str(summary.estimated_cgt_pkr)])
        writer.writerow(["Unused Loss Carryforward Remaining (PKR)", str(summary.unused_loss_carryforward_pkr)])
        writer.writerow(["Shariah Dividend Purification Due (PKR)", str(summary.total_shariah_purification_pkr)])
        writer.writerow([])
        writer.writerow(["Symbol", "Name", "Asset Class", "Quantity", "Avg Cost (PKR)", "Cost Basis Sec 116 (PKR)", "Market Value (PKR)", "Shariah"])
        for row in schedule:
            writer.writerow(
                [
                    row["symbol"],
                    row["name"],
                    row["asset_class"],
                    row["quantity"],
                    row["avg_cost_pkr"],
                    row["cost_basis_pkr"],
                    row["market_value_pkr"],
                    "YES" if row["is_shariah_compliant"] else "NO",
                ]
            )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="openkhazana_fbr_{fy.lower()}.csv"'},
        )

    return summary


@router.post("/api/v1/portfolios/csv/preview")
async def csv_preview(payload: CsvPreviewRequest, user: User = Depends(get_current_user)):
    return preview_csv(payload.csv_text, preset=payload.preset)


@router.post("/api/v1/portfolios/import/preview-file")
async def import_file_preview(
    file: UploadFile = File(...),
    preset: str = Form(default="generic"),
    user: User = Depends(get_current_user),
):
    """Parse an uploaded `.csv` or Excel `.xlsx`/`.xlsm` broker statement (up to 5 MB) for mapping preview."""
    raw_bytes = await file.read(5 * 1024 * 1024 + 1)
    if len(raw_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 5 MB upload limit.")
    try:
        csv_text = extract_csv_text_from_upload(file.filename or "statement.csv", raw_bytes)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unable to read upload: {exc}") from exc

    preview = preview_csv(csv_text, preset=preset)
    preview["normalized_csv_text"] = csv_text
    return preview


@router.post("/api/v1/portfolios/csv/commit")
async def csv_commit(
    payload: CsvCommitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(payload.portfolio_id, user, db)
    rows, errors = parse_mapped_csv(payload.csv_text, payload.mapping)

    all_assets = (
        await db.execute(
            select(Asset)
            .options(
                selectinload(Asset.shariah_metric),
                selectinload(Asset.fixed_income_profile),
                selectinload(Asset.psx_profile),
            )
            .where((Asset.owner_user_id.is_(None)) | (Asset.owner_user_id == user.id))
        )
    ).scalars().all()
    assets_by_id = {a.id: a for a in all_assets}
    assets_by_sym: dict[str, Asset] = {}
    for a in all_assets:
        if a.symbol not in assets_by_sym or a.owner_user_id == user.id:
            assets_by_sym[a.symbol] = a

    aliases = (await db.execute(select(AssetSymbolAlias))).scalars().all()
    for alias in aliases:
        target = assets_by_id.get(alias.asset_id)
        if target is not None and alias.old_symbol not in assets_by_sym:
            assets_by_sym[alias.old_symbol] = target

    existing_txs = await _load_portfolio_transactions(portfolio.id, db)
    seen_signatures = {
        make_tx_signature(tx.tx_date, tx.asset.symbol, tx.tx_type, tx.quantity, tx.unit_price)
        for tx in existing_txs
    }

    filer_status = _filer_status(user)
    broker_bps = user.tax_profile.default_broker_rate_bps if user.tax_profile else 15
    inserted = 0
    skipped_duplicates = 0

    for idx, r in enumerate(rows, start=1):
        asset = assets_by_sym.get(r["symbol"])
        if asset is None:
            errors.append(f"Mapped row {idx}: Unknown symbol '{r['symbol']}'")
            continue

        sig = make_tx_signature(r["tx_date"], asset.symbol, r["tx_type"], r["quantity"], r["unit_price"])
        if payload.skip_duplicates and sig in seen_signatures:
            skipped_duplicates += 1
            continue
        seen_signatures.add(sig)

        is_div = r["tx_type"] in {"DIVIDEND", "REINVEST_DIVIDEND"}
        gross = r["quantity"] * r["unit_price"] if is_div else D("0")
        fx = payload.default_fx_rate_to_pkr if asset.currency == "USD" else D("1")

        fee_amt = r["fee_amount"]
        if payload.auto_calculate_charges and fee_amt == 0 and r["tx_type"] in {"BUY", "SELL"} and asset.asset_class in {"PSX_EQUITY", "PSX_ETF"}:
            fee_amt = compute_psx_trade_charges(
                r["quantity"] * r["unit_price"],
                r["quantity"],
                broker_rate_bps=broker_bps,
            )["total_charges"]

        tax_wht = r["tax_withheld"]
        if payload.auto_calculate_charges and tax_wht == 0 and is_div and gross > 0:
            tax_wht = _dividend_wht_for_asset(gross, asset, user)

        purif = (
            round_pkr(gross * fx * (asset.shariah_metric.purification_rate_pct / D("100")))
            if is_div and asset.shariah_metric and asset.shariah_metric.purification_rate_pct > 0
            else D("0")
        )
        deemed_ex = asset.last_price if r["tx_type"] == "BONUS" else D("0")

        db.add(
            Transaction(
                portfolio_id=portfolio.id,
                asset_id=asset.id,
                tx_type=r["tx_type"],
                tx_date=r["tx_date"],
                quantity=r["quantity"],
                unit_price=r["unit_price"],
                gross_amount=gross,
                fee_amount=fee_amt,
                tax_withheld=tax_wht,
                deemed_ex_price=deemed_ex,
                shariah_purification_pkr=purif,
                fx_rate_to_pkr=fx,
                notes=r["notes"],
            )
        )
        inserted += 1

    if inserted:
        await db.flush()
        usd_to_pkr = await get_usd_to_pkr_rate(db)
        await rebuild_portfolio_materializations(
            portfolio, db, usd_to_pkr=usd_to_pkr, filer_status=filer_status, commit=False
        )
        await db.commit()
    return {"inserted": inserted, "skipped_duplicates": skipped_duplicates, "errors": errors}


@router.get("/api/v1/portfolios/{portfolio_id}/share-links", response_model=list[ShareLinkOut])
async def list_share_links(
    portfolio_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    links = (
        await db.execute(
            select(PortfolioShareLink)
            .where(PortfolioShareLink.portfolio_id == portfolio.id, PortfolioShareLink.is_active.is_(True))
            .order_by(desc(PortfolioShareLink.created_at))
        )
    ).scalars().all()
    return [
        ShareLinkOut(
            id=lnk.id,
            token=lnk.token,
            hide_amounts=lnk.hide_amounts,
            is_active=lnk.is_active,
            created_at=lnk.created_at,
        )
        for lnk in links
    ]


@router.post("/api/v1/portfolios/{portfolio_id}/share-links", response_model=ShareLinkOut)
async def create_share_link(
    portfolio_id: str,
    payload: ShareLinkCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    link = PortfolioShareLink(
        portfolio_id=portfolio.id,
        token=secrets.token_urlsafe(16),
        hide_amounts=payload.hide_amounts,
        is_active=True,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return ShareLinkOut(
        id=link.id,
        token=link.token,
        hide_amounts=link.hide_amounts,
        is_active=link.is_active,
        created_at=link.created_at,
    )


@router.delete("/api/v1/portfolios/{portfolio_id}/share-links/{link_id}")
async def revoke_share_link(
    portfolio_id: str,
    link_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_owned_portfolio(portfolio_id, user, db)
    link = (
        await db.execute(
            select(PortfolioShareLink).where(
                PortfolioShareLink.id == link_id, PortfolioShareLink.portfolio_id == portfolio.id
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found.")
    link.is_active = False
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/share/{token}", response_model=SharedPortfolioOut)
async def view_shared_portfolio(token: str, db: AsyncSession = Depends(get_db)):
    # Public share links evaluate the owner's portfolio under controlled RLS bypass.
    await apply_rls_context(db, bypass=True)
    stmt = (
        select(PortfolioShareLink)
        .options(selectinload(PortfolioShareLink.portfolio).selectinload(Portfolio.user).selectinload(User.tax_profile))
        .where(PortfolioShareLink.token == token, PortfolioShareLink.is_active.is_(True))
    )
    link = (await db.execute(stmt)).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared portfolio link not found or inactive.")

    portfolio = link.portfolio
    usd_to_pkr = await get_usd_to_pkr_rate(db)
    filer_status = _filer_status(portfolio.user)

    txs = await _load_portfolio_transactions(portfolio.id, db)
    asset_ids = {tx.asset_id for tx in txs}
    min_date = min((tx.tx_date for tx in txs), default=None)
    price_history, usd_history = await load_price_history_for_assets(asset_ids, db, min_date=min_date)
    val = evaluate_portfolio(
        transactions_to_events(txs),
        usd_to_pkr=usd_to_pkr,
        filer_status=filer_status,
        auto_manage_cash=portfolio.auto_manage_cash,
        price_history_by_asset=price_history,
        usd_pkr_history=usd_history,
    )
    total_mv = val.total_market_value_pkr

    holdings_payload = []
    for h in val.holdings:
        alloc = round_pkr((h.market_value_pkr / total_mv) * D("100")) if total_mv > 0 else D("0")
        holdings_payload.append(
            {
                "symbol": h.symbol,
                "name": h.name,
                "asset_class": h.asset_class,
                "is_shariah_compliant": h.is_shariah_compliant,
                "allocation_pct": alloc,
                "unrealized_pnl_pct": h.unrealized_pnl_pct,
                "market_value_pkr": None if link.hide_amounts else h.market_value_pkr,
            }
        )

    alloc_payload = [
        {
            "asset_class": item["asset_class"],
            "allocation_pct": item["allocation_pct"],
            "market_value_pkr": None if link.hide_amounts else item["market_value_pkr"],
        }
        for item in val.allocation_by_class
    ]

    return SharedPortfolioOut(
        portfolio_name=portfolio.name,
        hide_amounts=link.hide_amounts,
        shariah_compliant_pct=val.shariah_compliant_pct,
        total_unrealized_pnl_pct=val.total_unrealized_pnl_pct,
        xirr_pct=val.xirr_pct,
        twrr_pct=val.twrr_pct,
        total_market_value_pkr=None if link.hide_amounts else total_mv,
        allocation_by_class=alloc_payload,
        holdings=holdings_payload,
    )
