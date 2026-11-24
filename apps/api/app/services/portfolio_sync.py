from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.market import Asset, PriceBar
from app.models.portfolio import OpenTaxLot, Portfolio, PortfolioDailySnapshot, Transaction
from app.services.accounting import LedgerEvent, PortfolioValuationResult, evaluate_portfolio
from app.services.pk_tax import calculate_dividend_wht, calculate_intl_dividend_wht, round_pkr

D = Decimal
Q8 = D("0.00000001")
DAILY_DIV_SYMBOLS = {"MRAF", "NIDDF", "MDIP1", "MMMP", "MCBADDUB", "MICF", "MCF", "SGOV"}


def resolve_daily_fund_yield_pct(asset: Asset, fallback_rf_pct: float = 11.5) -> tuple[Decimal, str]:
    """Return `(effective_annual_yield_pct, source_label)` for a daily-dividend / money-market fund."""
    mf = getattr(asset, "mutual_fund_profile", None)
    fmr = getattr(asset, "fmr_snapshot", None)
    if mf and mf.annualized_yield_pct and mf.annualized_yield_pct > 0:
        return mf.annualized_yield_pct, "AMC Reported Yield"
    if fmr and fmr.ytm_pct and fmr.ytm_pct > 0:
        return fmr.ytm_pct, "SECP FMR YTM"
    if mf and mf.return_1y_pct and mf.return_1y_pct > 0:
        return mf.return_1y_pct, "1Y Trailing Return"
    if getattr(asset, "psx_profile", None) and asset.psx_profile.dividend_yield_pct > 0:
        return asset.psx_profile.dividend_yield_pct, "ETF Dividend Yield"
    est = D(str(round(max(6.0, fallback_rf_pct - 1.5), 2)))
    return est, "SBP PKRV Proxy"


def synthesize_accrued_daily_dividend_events(
    transactions: list[Transaction],
    *,
    as_of_date: date | None = None,
    filer_status: str = "ACTIVE_FILER",
    usd_to_pkr: Decimal = D("278.50"),
    fallback_rf_pct: float = 11.5,
) -> tuple[list[LedgerEvent], dict[str, dict[str, Decimal]]]:
    """
    Automatically synthesize virtual daily-dividend accrual events for held constant-NAV /
    daily-dividend funds (`MRAF`, `MDIP1`, `NIDDF`, `MCBADDUB`, `MCF`, `MICF`, `SGOV`, etc.)
    strictly AFTER the latest posted dividend transaction date through `as_of_date`.

    Guarantees ZERO double-counting: once explicit ledger DIVIDEND/REINVEST_DIVIDEND entries
    are recorded, only subsequent unposted days accrue automatically.
    """
    target_date = as_of_date or date.today()
    by_asset: dict[str, list[Transaction]] = defaultdict(list)
    for tx in transactions:
        asset = tx.asset
        mf = getattr(asset, "mutual_fund_profile", None)
        if bool((mf and mf.is_daily_dividend) or asset.symbol.upper() in DAILY_DIV_SYMBOLS):
            by_asset[tx.asset_id].append(tx)

    virtual_events: list[LedgerEvent] = []
    meta_by_asset: dict[str, dict[str, Decimal]] = {}

    for asset_id, asset_txs in by_asset.items():
        asset = asset_txs[0].asset
        mf = getattr(asset, "mutual_fund_profile", None)
        eff_yield, _ = resolve_daily_fund_yield_pct(asset, fallback_rf_pct=fallback_rf_pct)
        if eff_yield <= 0:
            continue

        par_nav = (
            mf.constant_par_nav
            if (mf and mf.constant_par_nav and mf.constant_par_nav > 0)
            else (asset.last_price if asset.last_price > 0 else D("50.00"))
        )
        mode = (mf.default_payout_mode if mf and mf.default_payout_mode else "REINVEST").upper()
        if mode not in {"REINVEST", "CASH"}:
            mode = "REINVEST"

        is_intl = str(asset.asset_class or "").startswith("INTL_") or asset.currency == "USD"
        fx = usd_to_pkr if asset.currency == "USD" else D("1")
        purif_rate = (
            asset.shariah_metric.purification_rate_pct
            if getattr(asset, "shariah_metric", None) is not None
            else D("0")
        )

        # Compute daily unit deltas from real ledger events
        tx_sorted = sorted(asset_txs, key=lambda t: t.tx_date)
        acq_dates = [t.tx_date for t in tx_sorted if t.tx_type in {"BUY", "OPENING_BALANCE", "DEPOSIT"}]
        if not acq_dates:
            continue
        first_acq = min(acq_dates)
        div_dates = [t.tx_date for t in tx_sorted if t.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}]
        last_posted_div = max(div_dates) if div_dates else None

        start_d = (last_posted_div + timedelta(days=1)) if (last_posted_div and last_posted_div >= first_acq) else (first_acq + timedelta(days=1))

        # Track real-ledger units held prior to start_d and daily deltas on/after start_d
        units_before = D("0")
        delta_by_day: dict[date, Decimal] = defaultdict(lambda: D("0"))
        for t in tx_sorted:
            if t.tx_date > target_date:
                continue
            q_delta = D("0")
            if t.tx_type in {"BUY", "OPENING_BALANCE", "BONUS", "REINVEST_DIVIDEND"}:
                q_delta = t.quantity
            elif t.tx_type == "SELL":
                q_delta = -t.quantity
            if t.tx_date < start_d:
                units_before += q_delta
            else:
                delta_by_day[t.tx_date] += q_delta

        cur_units = max(D("0"), units_before)
        daily_rate = (eff_yield / D("100")) / D("365")
        monthly_buckets: dict[tuple[int, int], dict[str, object]] = {}
        total_accrued_net_pkr = D("0")

        if start_d <= target_date:
            d = start_d
            # Cap synthetic lookback to 5 years to prevent runaway loops on bad dates
            if (target_date - d).days > 1825:
                d = target_date - timedelta(days=1825)
            while d <= target_date:
                if d in delta_by_day:
                    cur_units = max(D("0"), cur_units + delta_by_day[d])
                if cur_units > 0:
                    gross_native = cur_units * par_nav * daily_rate
                    if is_intl:
                        wht_native = calculate_intl_dividend_wht(
                            gross_native,
                            us_w8ben_active=(filer_status == "ACTIVE_FILER"),
                            country="US",
                        )
                    else:
                        wht_native = calculate_dividend_wht(gross_native, filer_status=filer_status)
                    net_native = max(D("0"), gross_native - wht_native)
                    reinv_units = (net_native / par_nav).quantize(Q8) if (mode == "REINVEST" and par_nav > 0) else D("0")
                    if mode == "REINVEST":
                        cur_units += reinv_units

                    total_accrued_net_pkr += net_native * fx
                    ym = (d.year, d.month)
                    b = monthly_buckets.setdefault(
                        ym,
                        {"last_date": d, "gross": D("0"), "wht": D("0"), "units": D("0")},
                    )
                    b["last_date"] = d
                    b["gross"] = b["gross"] + gross_native  # type: ignore[operator]
                    b["wht"] = b["wht"] + wht_native  # type: ignore[operator]
                    b["units"] = b["units"] + reinv_units  # type: ignore[operator]
                d += timedelta(days=1)

            for (_, _), b in sorted(monthly_buckets.items()):
                g_nat = round_pkr(b["gross"])  # type: ignore[arg-type]
                w_nat = round_pkr(b["wht"])  # type: ignore[arg-type]
                u_reinv = b["units"]  # type: ignore[assignment]
                if g_nat <= 0:
                    continue
                virtual_events.append(
                    LedgerEvent(
                        asset_id=asset.id,
                        symbol=asset.symbol,
                        name=asset.name,
                        asset_class=asset.asset_class,
                        sector_or_category=asset.sector_or_category,
                        is_shariah_compliant=asset.is_shariah_compliant,
                        current_price_native=asset.last_price,
                        asset_currency=asset.currency,
                        tx_type="REINVEST_DIVIDEND" if mode == "REINVEST" else "DIVIDEND",
                        tx_date=b["last_date"],  # type: ignore[arg-type]
                        quantity=u_reinv if mode == "REINVEST" else D("1"),
                        unit_price=par_nav if mode == "REINVEST" else g_nat,
                        gross_amount=g_nat,
                        fee_amount=D("0"),
                        tax_withheld=w_nat,
                        deemed_ex_price=D("0"),
                        shariah_purification_rate_pct=purif_rate,
                        shariah_purification_pkr=D("0"),
                        fx_rate_to_pkr=fx,
                    )
                )

        # Estimate current live daily & monthly net income on current units
        if cur_units > 0:
            est_daily_gross = cur_units * par_nav * daily_rate
            est_daily_wht = (
                calculate_intl_dividend_wht(est_daily_gross, us_w8ben_active=(filer_status == "ACTIVE_FILER"), country="US")
                if is_intl
                else calculate_dividend_wht(est_daily_gross, filer_status=filer_status)
            )
            est_daily_net_pkr = round_pkr(max(D("0"), est_daily_gross - est_daily_wht) * fx)
            est_monthly_net_pkr = round_pkr(est_daily_net_pkr * D("30"))
        else:
            est_daily_net_pkr = D("0")
            est_monthly_net_pkr = D("0")

        meta_by_asset[asset_id] = {
            "accrued_unposted_pkr": round_pkr(total_accrued_net_pkr),
            "est_daily_income_pkr": est_daily_net_pkr,
            "est_monthly_income_pkr": est_monthly_net_pkr,
            "effective_yield_pct": eff_yield,
        }

    return virtual_events, meta_by_asset


def transactions_to_events(transactions: list[Transaction]) -> list[LedgerEvent]:
    """Convert ORM Transaction rows (with Asset + ShariahMetric loaded) to LedgerEvents."""
    events: list[LedgerEvent] = []
    for tx in transactions:
        purif_rate = (
            tx.asset.shariah_metric.purification_rate_pct
            if tx.asset.shariah_metric is not None
            else Decimal("0")
        )
        events.append(
            LedgerEvent(
                asset_id=tx.asset.id,
                symbol=tx.asset.symbol,
                name=tx.asset.name,
                asset_class=tx.asset.asset_class,
                sector_or_category=tx.asset.sector_or_category,
                is_shariah_compliant=tx.asset.is_shariah_compliant,
                current_price_native=tx.asset.last_price,
                asset_currency=tx.asset.currency,
                tx_type=tx.tx_type,
                tx_date=tx.tx_date,
                quantity=tx.quantity,
                unit_price=tx.unit_price,
                gross_amount=tx.gross_amount,
                fee_amount=tx.fee_amount,
                tax_withheld=tx.tax_withheld,
                deemed_ex_price=tx.deemed_ex_price,
                shariah_purification_rate_pct=purif_rate,
                shariah_purification_pkr=tx.shariah_purification_pkr,
                fx_rate_to_pkr=tx.fx_rate_to_pkr,
            )
        )
    return events


async def load_price_history_for_assets(
    asset_ids: set[str],
    db: AsyncSession,
    min_date: date | None = None,
) -> tuple[dict[str, list[tuple[date, Decimal]]], list[tuple[date, Decimal]]]:
    """Load daily (`1D`) historical closes for held assets + `USD_PKR` for historical valuation."""
    if not asset_ids:
        return {}, []

    usd_asset_id = (
        await db.execute(
            select(Asset.id)
            .where(Asset.symbol.in_(["USD_PKR_OPEN", "USD_PKR_INTERBANK", "USD_PKR"]))
            .order_by(Asset.symbol.desc())
        )
    ).scalars().first()

    target_ids = set(asset_ids)
    if usd_asset_id:
        target_ids.add(usd_asset_id)

    stmt = (
        select(PriceBar.asset_id, PriceBar.bar_time, PriceBar.close)
        .where(
            PriceBar.asset_id.in_(target_ids),
            PriceBar.timeframe == "1D",
        )
        .order_by(PriceBar.bar_time.asc())
    )
    if min_date is not None:
        min_dt = datetime.combine(min_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(PriceBar.bar_time >= min_dt)

    rows = (await db.execute(stmt)).all()
    history_by_asset: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    usd_history: list[tuple[date, Decimal]] = []

    for aid, ts, close in rows:
        bar_date = ts.date() if isinstance(ts, datetime) else ts
        if close is None or close <= 0:
            continue
        if usd_asset_id and aid == usd_asset_id:
            usd_history.append((bar_date, close))
        if aid in asset_ids:
            history_by_asset[aid].append((bar_date, close))

    return dict(history_by_asset), usd_history


async def rebuild_portfolio_materializations(
    portfolio: Portfolio,
    db: AsyncSession,
    usd_to_pkr: Decimal,
    filer_status: str,
    commit: bool = True,
    foreign_cgt_rate_pct: Decimal | None = None,
) -> PortfolioValuationResult:
    """
    Recompute valuation using historical PriceBar data and materialize `open_tax_lots` +
    `portfolio_daily_snapshots` after any ledger mutation.
    """
    tx_stmt = (
        select(Transaction)
        .options(selectinload(Transaction.asset).selectinload(Asset.shariah_metric))
        .where(Transaction.portfolio_id == portfolio.id)
        .order_by(Transaction.tx_date.asc(), Transaction.created_at.asc())
    )
    txs = list((await db.execute(tx_stmt)).scalars().all())
    events = transactions_to_events(txs)

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
        foreign_cgt_rate_pct=foreign_cgt_rate_pct,
    )

    await db.execute(delete(OpenTaxLot).where(OpenTaxLot.portfolio_id == portfolio.id))
    await db.execute(delete(PortfolioDailySnapshot).where(PortfolioDailySnapshot.portfolio_id == portfolio.id))

    now = datetime.now(timezone.utc)
    for lot in val.open_lots:
        if lot.quantity > 0:
            db.add(
                OpenTaxLot(
                    portfolio_id=portfolio.id,
                    asset_id=lot.asset_id,
                    acq_date=lot.acq_date,
                    quantity_remaining=lot.quantity,
                    cost_basis_pkr=lot.cost_basis_pkr,
                    updated_at=now,
                )
            )

    for snap in val.daily_nav_series:
        db.add(
            PortfolioDailySnapshot(
                portfolio_id=portfolio.id,
                snapshot_date=snap.snapshot_date,
                total_value_pkr=snap.total_value_pkr,
                total_cost_basis_pkr=snap.total_cost_basis_pkr,
                net_invested_pkr=snap.net_invested_pkr,
                virtual_units=snap.virtual_units,
                nav_per_unit=snap.nav_per_unit,
            )
        )

    if commit:
        await db.commit()
    else:
        await db.flush()
    return val
