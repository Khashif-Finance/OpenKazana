from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.pk_tax import (
    fiscal_year_start_year,
    get_cgt_rate_pct,
    get_pakistan_fiscal_year,
    round_pkr,
)
from app.services.quant import compute_xirr_scipy

D = Decimal
Q8 = D("0.00000001")
Q4 = D("0.0001")
EPS = D("0.0000001")


@dataclass
class LedgerEvent:
    """Normalized transaction event passed into the deterministic accounting engine."""

    asset_id: str
    symbol: str
    name: str
    asset_class: str
    sector_or_category: str
    is_shariah_compliant: bool
    current_price_native: Decimal
    asset_currency: str
    tx_type: str
    tx_date: date
    quantity: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    fee_amount: Decimal
    tax_withheld: Decimal
    fx_rate_to_pkr: Decimal
    deemed_ex_price: Decimal = D("0")
    shariah_purification_rate_pct: Decimal = D("0")
    shariah_purification_pkr: Decimal = D("0")


@dataclass
class FifoLot:
    """Open tax lot tracked for FIFO cost basis and FBR acquisition-regime CGT."""

    asset_id: str
    acq_date: date
    quantity: Decimal
    cost_basis_pkr: Decimal
    cost_basis_native: Decimal = D("0")


@dataclass
class DailySnapshotPoint:
    """Unitized daily NAV point for Time-Weighted Rate of Return (TWRR)."""

    snapshot_date: date
    total_value_pkr: Decimal
    total_cost_basis_pkr: Decimal
    net_invested_pkr: Decimal
    virtual_units: Decimal
    nav_per_unit: Decimal


@dataclass
class FiscalYearCgtSummary:
    """Pakistan fiscal-year CGT reconciliation with FBR Sec 37A 3-year loss carryforward."""

    fiscal_year: str
    gross_gain_pkr: Decimal
    gross_loss_pkr: Decimal
    prior_loss_applied_pkr: Decimal
    net_taxable_gain_pkr: Decimal
    estimated_cgt_pkr: Decimal
    unused_loss_carryforward_pkr: Decimal


@dataclass
class _DisposalRecord:
    tx_date: date
    fiscal_year: str
    fy_start_year: int
    asset_id: str
    gain_pkr: Decimal
    cgt_rate_pct: Decimal


@dataclass
class AssetPositionResult:
    asset_id: str
    symbol: str
    name: str
    asset_class: str
    sector_or_category: str
    is_shariah_compliant: bool
    quantity: Decimal
    avg_cost_pkr: Decimal
    cost_basis_pkr: Decimal
    current_price_pkr: Decimal
    market_value_pkr: Decimal
    unrealized_pnl_pkr: Decimal
    unrealized_pnl_pct: Decimal
    realized_pnl_pkr: Decimal
    dividends_net_pkr: Decimal
    shariah_purification_pkr: Decimal
    estimated_cgt_pkr: Decimal
    currency: str = "PKR"
    avg_cost_native: Decimal = D("0")
    current_price_native: Decimal = D("0")
    native_return_pct: Decimal = D("0")
    fx_return_pct: Decimal = D("0")


@dataclass
class PortfolioValuationResult:
    total_market_value_pkr: Decimal
    total_cost_basis_pkr: Decimal
    total_unrealized_pnl_pkr: Decimal
    total_unrealized_pnl_pct: Decimal
    total_realized_pnl_pkr: Decimal
    total_net_dividends_pkr: Decimal
    total_shariah_purification_pkr: Decimal
    total_fees_pkr: Decimal
    total_tax_withheld_pkr: Decimal
    estimated_cgt_pkr: Decimal
    xirr_pct: Decimal | None
    twrr_pct: Decimal | None
    nav_per_unit: Decimal
    shariah_compliant_pct: Decimal
    allocation_by_class: list[dict]
    daily_nav_series: list[DailySnapshotPoint]
    open_lots: list[FifoLot]
    holdings: list[AssetPositionResult]
    cgt_by_fy: dict[str, FiscalYearCgtSummary] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class _PriceSeriesIndex:
    """Fast sorted bisect index for native closing price history."""

    def __init__(self, series: list[tuple[date, Decimal]] | None) -> None:
        if not series:
            self.dates: list[date] = []
            self.prices: list[Decimal] = []
            return
        ordered = sorted(series, key=lambda item: item[0])
        self.dates = [item[0] for item in ordered]
        self.prices = [item[1] for item in ordered]

    def lookup(self, target_date: date) -> Decimal | None:
        if not self.dates:
            return None
        idx = bisect_right(self.dates, target_date) - 1
        if idx < 0:
            return None
        px = self.prices[idx]
        return px if px > 0 else None


def compute_xirr(cashflows: list[tuple[date, float]], guess: float = 0.15) -> Decimal | None:
    """Compute annualized Money-Weighted IRR (XIRR) via `scipy.optimize` with Newton-Raphson/bisection fallback."""
    scipy_res = compute_xirr_scipy(cashflows, guess=guess)
    if scipy_res is not None:
        return scipy_res
    if len(cashflows) < 2:
        return None
    has_pos = any(amt > 0 for _, amt in cashflows)
    has_neg = any(amt < 0 for _, amt in cashflows)
    if not (has_pos and has_neg):
        return None

    d0 = min(d for d, _ in cashflows)
    if max((d - d0).days for d, _ in cashflows) < 1:
        return None

    def npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 1e-9:
            return float("inf")
        return sum(amt / (base ** ((dt - d0).days / 365.0)) for dt, amt in cashflows)

    def d_npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 1e-9:
            return 0.0
        total = 0.0
        for dt, amt in cashflows:
            years = (dt - d0).days / 365.0
            if years > 0:
                total -= years * amt / (base ** (years + 1.0))
        return total

    rate = guess
    for _ in range(80):
        val = npv(rate)
        if abs(val) < 1e-6:
            return Decimal(str(rate * 100.0)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        deriv = d_npv(rate)
        if abs(deriv) < 1e-12:
            break
        next_rate = rate - val / deriv
        if next_rate <= -0.99 or next_rate > 50.0:
            break
        if abs(next_rate - rate) < 1e-7:
            return Decimal(str(next_rate * 100.0)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        rate = next_rate

    lo, hi = -0.95, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6 or (hi - lo) < 1e-7:
            return Decimal(str(mid * 100.0)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid
    return None


def _scale_lots_proportionally(lots: deque[FifoLot], additional_shares: Decimal) -> None:
    """Scale open lots proportionally for stock splits while preserving cost basis."""
    if not lots or additional_shares <= 0:
        return
    total_qty = sum((lot.quantity for lot in lots), D("0"))
    if total_qty <= 0:
        return
    factor = (total_qty + additional_shares) / total_qty
    assigned = D("0")
    lot_list = list(lots)
    for idx, lot in enumerate(lot_list):
        if idx == len(lot_list) - 1:
            lot.quantity = (total_qty + additional_shares - assigned).quantize(Q8, rounding=ROUND_HALF_UP)
        else:
            new_qty = (lot.quantity * factor).quantize(Q8, rounding=ROUND_HALF_UP)
            assigned += new_qty
            lot.quantity = new_qty


def _extract_same_day_intraday_matches(
    sorted_events: list[LedgerEvent],
) -> tuple[dict[tuple[str, date], dict], list[LedgerEvent]]:
    """
    Match same-day BUY + SELL pairs for the same asset BEFORE FIFO lot consumption,
    preventing intraday square-offs from depleting long-term tax-exempt FIFO lots.
    """
    buys_by_day: dict[tuple[str, date], list[LedgerEvent]] = defaultdict(list)
    sells_by_day: dict[tuple[str, date], list[LedgerEvent]] = defaultdict(list)
    passthrough: list[LedgerEvent] = []

    for ev in sorted_events:
        if ev.tx_type == "BUY" and ev.quantity > 0:
            buys_by_day[(ev.asset_id, ev.tx_date)].append(ev)
        elif ev.tx_type == "SELL" and ev.quantity > 0:
            sells_by_day[(ev.asset_id, ev.tx_date)].append(ev)
        else:
            passthrough.append(ev)

    intraday_matches: dict[tuple[str, date], dict] = {}
    remaining_events: list[LedgerEvent] = list(passthrough)

    all_keys = set(buys_by_day.keys()) | set(sells_by_day.keys())
    for key in sorted(all_keys, key=lambda k: k[1]):
        day_buys = buys_by_day.get(key, [])
        day_sells = sells_by_day.get(key, [])

        if not (day_buys and day_sells):
            remaining_events.extend(day_buys)
            remaining_events.extend(day_sells)
            continue

        total_buy_qty = sum((b.quantity for b in day_buys), D("0"))
        total_sell_qty = sum((s.quantity for s in day_sells), D("0"))
        matched_qty = min(total_buy_qty, total_sell_qty)

        def _sum_cost_and_fee(evs: list[LedgerEvent]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
            gross_sum_pkr = D("0")
            fee_sum_pkr = D("0")
            gross_sum_nat = D("0")
            fee_sum_nat = D("0")
            for item in evs:
                fx = item.fx_rate_to_pkr if item.fx_rate_to_pkr > 0 else D("1")
                g_nat = item.gross_amount if item.gross_amount > 0 else item.quantity * item.unit_price
                gross_sum_nat += g_nat
                fee_sum_nat += item.fee_amount
                gross_sum_pkr += g_nat * fx
                fee_sum_pkr += item.fee_amount * fx
            return gross_sum_pkr, fee_sum_pkr, gross_sum_nat, fee_sum_nat

        buy_gross, buy_fee, buy_gross_nat, buy_fee_nat = _sum_cost_and_fee(day_buys)
        sell_gross, sell_fee, sell_gross_nat, sell_fee_nat = _sum_cost_and_fee(day_sells)

        matched_buy_ratio = matched_qty / total_buy_qty
        matched_sell_ratio = matched_qty / total_sell_qty
        matched_buy_cost = (buy_gross + buy_fee) * matched_buy_ratio
        matched_sell_proceeds = (sell_gross - sell_fee) * matched_sell_ratio

        intraday_matches[key] = {
            "asset_id": key[0],
            "tx_date": key[1],
            "meta": day_buys[0],
            "matched_qty": matched_qty,
            "buy_cost_pkr": matched_buy_cost,
            "sell_proceeds_pkr": matched_sell_proceeds,
            "fees_pkr": (buy_fee * matched_buy_ratio) + (sell_fee * matched_sell_ratio),
            "tax_pkr": sum((b.tax_withheld * b.fx_rate_to_pkr for b in day_buys), D("0")) * matched_buy_ratio
            + sum((s.tax_withheld * s.fx_rate_to_pkr for s in day_sells), D("0")) * matched_sell_ratio,
        }

        if total_buy_qty > matched_qty:
            rem_ratio = (total_buy_qty - matched_qty) / total_buy_qty
            template = day_buys[-1]
            rem_qty = total_buy_qty - matched_qty
            rem_gross_nat = buy_gross_nat * rem_ratio
            rem_fee_nat = buy_fee_nat * rem_ratio
            eff_fx = (buy_gross / buy_gross_nat) if buy_gross_nat > 0 else template.fx_rate_to_pkr
            remaining_events.append(
                LedgerEvent(
                    **{
                        **template.__dict__,
                        "quantity": rem_qty,
                        "gross_amount": rem_gross_nat,
                        "unit_price": rem_gross_nat / rem_qty,
                        "fee_amount": rem_fee_nat,
                        "tax_withheld": D("0"),
                        "fx_rate_to_pkr": eff_fx,
                    }
                )
            )
        elif total_sell_qty > matched_qty:
            rem_ratio = (total_sell_qty - matched_qty) / total_sell_qty
            template = day_sells[-1]
            rem_qty = total_sell_qty - matched_qty
            rem_gross_nat = sell_gross_nat * rem_ratio
            rem_fee_nat = sell_fee_nat * rem_ratio
            eff_fx = (sell_gross / sell_gross_nat) if sell_gross_nat > 0 else template.fx_rate_to_pkr
            remaining_events.append(
                LedgerEvent(
                    **{
                        **template.__dict__,
                        "quantity": rem_qty,
                        "gross_amount": rem_gross_nat,
                        "unit_price": rem_gross_nat / rem_qty,
                        "fee_amount": rem_fee_nat,
                        "tax_withheld": D("0"),
                        "fx_rate_to_pkr": eff_fx,
                    }
                )
            )

    remaining_events.sort(key=lambda e: e.tx_date)
    return intraday_matches, remaining_events


def _compute_cgt_by_fiscal_year(disposals: list[_DisposalRecord]) -> dict[str, FiscalYearCgtSummary]:
    """
    Aggregate realized gains/losses per Pakistan fiscal year (July 1 - June 30) and apply
    FBR Section 37A(5) loss netting (same-FY offset + up to 3 succeeding tax-year carryforward).
    """
    if not disposals:
        return {}

    grouped: dict[str, list[_DisposalRecord]] = defaultdict(list)
    for rec in disposals:
        grouped[rec.fiscal_year].append(rec)

    ordered_fys = sorted(grouped.keys(), key=fiscal_year_start_year)
    # Queue of [loss_origin_start_year, remaining_loss_pkr]
    carryforward_pool: deque[list] = deque()
    summaries: dict[str, FiscalYearCgtSummary] = {}

    for fy in ordered_fys:
        fy_start = fiscal_year_start_year(fy)
        recs = grouped[fy]

        gross_gain = D("0")
        gross_loss = D("0")
        gross_cgt = D("0")
        for r in recs:
            if r.gain_pkr > 0:
                gross_gain += r.gain_pkr
                gross_cgt += r.gain_pkr * (r.cgt_rate_pct / D("100"))
            elif r.gain_pkr < 0:
                gross_loss += -r.gain_pkr

        # Expire losses older than 3 succeeding tax years (Sec 37A(5))
        while carryforward_pool and (fy_start - carryforward_pool[0][0]) > 3:
            carryforward_pool.popleft()

        net_before_carry = gross_gain - gross_loss
        prior_applied = D("0")
        if net_before_carry > 0:
            to_absorb = net_before_carry
            while to_absorb > 0 and carryforward_pool:
                head = carryforward_pool[0]
                if head[1] <= to_absorb:
                    prior_applied += head[1]
                    to_absorb -= head[1]
                    carryforward_pool.popleft()
                else:
                    prior_applied += to_absorb
                    head[1] -= to_absorb
                    to_absorb = D("0")
            net_taxable_gain = net_before_carry - prior_applied
        else:
            net_taxable_gain = D("0")
            excess_loss = gross_loss - gross_gain
            if excess_loss > 0:
                carryforward_pool.append([fy_start, excess_loss])

        if gross_gain > 0 and net_taxable_gain > 0:
            estimated_cgt = gross_cgt * (net_taxable_gain / gross_gain)
        else:
            estimated_cgt = D("0")

        unused_carryforward = sum((item[1] for item in carryforward_pool), D("0"))

        summaries[fy] = FiscalYearCgtSummary(
            fiscal_year=fy,
            gross_gain_pkr=round_pkr(gross_gain),
            gross_loss_pkr=round_pkr(gross_loss),
            prior_loss_applied_pkr=round_pkr(prior_applied),
            net_taxable_gain_pkr=round_pkr(net_taxable_gain),
            estimated_cgt_pkr=round_pkr(estimated_cgt),
            unused_loss_carryforward_pkr=round_pkr(unused_carryforward),
        )

    return summaries


def evaluate_portfolio(
    events: list[LedgerEvent],
    usd_to_pkr: Decimal,
    filer_status: str = "ACTIVE_FILER",
    as_of_date: date | None = None,
    auto_manage_cash: bool = False,
    price_history_by_asset: dict[str, list[tuple[date, Decimal]]] | None = None,
    usd_pkr_history: list[tuple[date, Decimal]] | None = None,
    foreign_cgt_rate_pct: Decimal | None = None,
) -> PortfolioValuationResult:
    """
    Replay ledger transactions chronologically using:
    1. Same-Day Intraday Netting before FIFO lot depletion
    2. Section 236Z non-zero-cost Bonus Share lots
    3. DRIP (`REINVEST_DIVIDEND`) atomic unit lot creation & Shariah purification tracking
    4. Optional Auto-Linked Broker Cash Wallet (`PKR_CASH`, never drained by `OPENING_BALANCE`)
    5. Historical `PriceBar` daily Mark-to-Market valuation for TWRR & historical FBR snapshots
    6. FBR Sec 37A capital loss netting with 3-year carryforward, foreign CGT rate support & oversell warnings
    """
    def _effective_cgt_rate(meta_ev: LedgerEvent, acq_dt: date, sell_dt: date) -> Decimal:
        if foreign_cgt_rate_pct is not None and (
            meta_ev.asset_class in {"INTL_EQUITY", "INTL_ETF", "CRYPTO"}
            or (meta_ev.asset_currency or "PKR").upper() != "PKR"
        ):
            return D(foreign_cgt_rate_pct)
        return get_cgt_rate_pct(acq_dt, sell_dt, filer_status)
    valuation_date = as_of_date or date.today()
    is_historical_cutoff = (
        price_history_by_asset is not None
        and as_of_date is not None
        and as_of_date < date.today()
    )

    sorted_input = sorted((ev for ev in events if ev.tx_date <= valuation_date), key=lambda e: e.tx_date)
    intraday_matches, fifo_events = _extract_same_day_intraday_matches(sorted_input)

    history_index_by_asset: dict[str, _PriceSeriesIndex] = {
        aid: _PriceSeriesIndex(series) for aid, series in (price_history_by_asset or {}).items()
    }
    usd_history_index = _PriceSeriesIndex(usd_pkr_history)

    lots_by_asset: dict[str, deque[FifoLot]] = defaultdict(deque)
    meta_by_asset: dict[str, LedgerEvent] = {}
    realized_by_asset: dict[str, Decimal] = defaultdict(D)
    dividends_by_asset: dict[str, Decimal] = defaultdict(D)
    purification_by_asset: dict[str, Decimal] = defaultdict(D)
    cgt_by_asset: dict[str, Decimal] = defaultdict(D)
    unmatched_sell_by_asset: dict[str, Decimal] = defaultdict(D)
    disposals: list[_DisposalRecord] = []
    warnings: list[str] = []

    total_fees = D("0")
    total_tax_withheld = D("0")
    linked_cash_delta_pkr = D("0")
    cashflows: list[tuple[date, float]] = []

    virtual_units = D("0")
    nav_per_unit = D("100.0000")
    net_invested_pkr = D("0")
    daily_snapshots: list[DailySnapshotPoint] = []
    last_known_price_pkr: dict[str, Decimal] = {}

    def _fx_on_date(currency: str, target_date: date) -> Decimal:
        if currency.upper() == "USD":
            hist_fx = usd_history_index.lookup(target_date)
            return hist_fx if hist_fx is not None else usd_to_pkr
        return D("1")

    def _market_price_pkr_on_date(aid: str, target_date: date, allow_live_price: bool = False) -> Decimal:
        meta = meta_by_asset.get(aid)
        if meta is not None and meta.symbol == "PKR_CASH":
            return D("1")
        if meta is not None and allow_live_price:
            return meta.current_price_native * (usd_to_pkr if meta.asset_currency.upper() == "USD" else D("1"))

        idx = history_index_by_asset.get(aid)
        if idx is not None:
            bar_px = idx.lookup(target_date)
            if bar_px is not None:
                curr = meta.asset_currency if meta is not None else "PKR"
                return bar_px * _fx_on_date(curr, target_date)

        tx_px = last_known_price_pkr.get(aid)
        if tx_px is not None and tx_px > 0:
            return tx_px

        if meta is not None:
            return meta.current_price_native * (usd_to_pkr if meta.asset_currency.upper() == "USD" else D("1"))
        return D("0")

    def _book_mv_on_date(extra_cash_pkr: Decimal, target_date: date, allow_live_price: bool = False) -> tuple[Decimal, Decimal]:
        mv = extra_cash_pkr
        cb = extra_cash_pkr
        for aid, q in lots_by_asset.items():
            qty_sum = sum((lot.quantity for lot in q), D("0"))
            cb_sum = sum((lot.cost_basis_pkr for lot in q), D("0"))
            px = _market_price_pkr_on_date(aid, target_date, allow_live_price=allow_live_price)
            mv += qty_sum * px
            cb += cb_sum
        return mv, max(D("0"), cb)

    events_by_date: dict[date, list[tuple[str, object]]] = defaultdict(list)
    for k, match in intraday_matches.items():
        events_by_date[k[1]].append(("INTRADAY", match))
    for ev in fifo_events:
        events_by_date[ev.tx_date].append(("EVENT", ev))

    if events_by_date:
        first_tx_date = min(events_by_date.keys())
        all_dates: set[date] = set(events_by_date.keys())
        for idx in history_index_by_asset.values():
            for bar_d in idx.dates:
                if first_tx_date <= bar_d <= valuation_date:
                    all_dates.add(bar_d)
        if first_tx_date <= valuation_date:
            all_dates.add(valuation_date)

        for cur_date in sorted(all_dates):
            day_items = events_by_date.get(cur_date, [])
            for kind, payload in day_items:
                if kind == "INTRADAY":
                    m = payload  # type: ignore[assignment]
                    aid = m["asset_id"]
                    meta: LedgerEvent = m["meta"]
                    meta_by_asset[aid] = meta
                    if m["matched_qty"] > 0:
                        last_known_price_pkr[aid] = m["sell_proceeds_pkr"] / m["matched_qty"]
                    gain = m["sell_proceeds_pkr"] - m["buy_cost_pkr"]
                    realized_by_asset[aid] += gain
                    total_fees += m["fees_pkr"]
                    total_tax_withheld += m["tax_pkr"]

                    cgt_rate = _effective_cgt_rate(meta, cur_date, cur_date)
                    if gain > 0:
                        cgt_by_asset[aid] += gain * (cgt_rate / D("100"))
                    disposals.append(
                        _DisposalRecord(
                            tx_date=cur_date,
                            fiscal_year=get_pakistan_fiscal_year(cur_date),
                            fy_start_year=fiscal_year_start_year(get_pakistan_fiscal_year(cur_date)),
                            asset_id=aid,
                            gain_pkr=gain,
                            cgt_rate_pct=cgt_rate,
                        )
                    )
                    cashflows.append((cur_date, float(m["sell_proceeds_pkr"] - m["buy_cost_pkr"] - m["tax_pkr"])))
                    if auto_manage_cash and meta.symbol != "PKR_CASH":
                        linked_cash_delta_pkr += m["sell_proceeds_pkr"] - m["buy_cost_pkr"] - m["tax_pkr"]
                    continue

                ev: LedgerEvent = payload  # type: ignore[assignment]
                meta_by_asset[ev.asset_id] = ev
                fx = ev.fx_rate_to_pkr if ev.fx_rate_to_pkr > 0 else D("1")
                fee_pkr = ev.fee_amount * fx
                tax_pkr = ev.tax_withheld * fx
                total_fees += fee_pkr
                total_tax_withheld += tax_pkr

                if ev.unit_price > 0:
                    last_known_price_pkr[ev.asset_id] = ev.unit_price * fx

                # Revalue virtual NAV prior to capital flows on cur_date
                pre_mv, _ = _book_mv_on_date(linked_cash_delta_pkr if auto_manage_cash else D("0"), cur_date)
                if virtual_units > 0 and pre_mv > 0:
                    nav_per_unit = (pre_mv / virtual_units).quantize(Q4, rounding=ROUND_HALF_UP)

                if ev.tx_type == "OPENING_BALANCE":
                    gross_native = ev.gross_amount if ev.gross_amount > 0 else ev.quantity * ev.unit_price
                    cost_native = gross_native + ev.fee_amount
                    gross_pkr = gross_native * fx
                    cost_pkr = gross_pkr + fee_pkr
                    if ev.quantity > 0:
                        lots_by_asset[ev.asset_id].append(
                            FifoLot(
                                asset_id=ev.asset_id,
                                acq_date=ev.tx_date,
                                quantity=ev.quantity,
                                cost_basis_pkr=cost_pkr,
                                cost_basis_native=cost_native,
                            )
                        )
                    cashflows.append((ev.tx_date, -float(cost_pkr)))
                    # Opening balances represent pre-existing positions or cash brought into the tracker:
                    # they NEVER deduct from PKR_CASH and always mint virtual TWRR units.
                    if nav_per_unit > 0 and cost_pkr > 0:
                        virtual_units += cost_pkr / nav_per_unit
                        net_invested_pkr += cost_pkr

                elif ev.tx_type in {"BUY", "RIGHTS"}:
                    gross_native = ev.gross_amount if ev.gross_amount > 0 else ev.quantity * ev.unit_price
                    cost_native = gross_native + ev.fee_amount
                    gross_pkr = gross_native * fx
                    cost_pkr = gross_pkr + fee_pkr
                    if ev.quantity > 0:
                        lots_by_asset[ev.asset_id].append(
                            FifoLot(
                                asset_id=ev.asset_id,
                                acq_date=ev.tx_date,
                                quantity=ev.quantity,
                                cost_basis_pkr=cost_pkr,
                                cost_basis_native=cost_native,
                            )
                        )
                    cashflows.append((ev.tx_date, -float(cost_pkr)))
                    if auto_manage_cash and ev.symbol != "PKR_CASH":
                        linked_cash_delta_pkr -= cost_pkr
                    elif nav_per_unit > 0 and cost_pkr > 0:
                        virtual_units += cost_pkr / nav_per_unit
                        net_invested_pkr += cost_pkr

                elif ev.tx_type == "BONUS":
                    if ev.quantity > 0:
                        deemed_native = ev.quantity * ev.deemed_ex_price if ev.deemed_ex_price > 0 else ev.tax_withheld
                        deemed_val_pkr = ev.quantity * ev.deemed_ex_price * fx if ev.deemed_ex_price > 0 else D("0")
                        bonus_lot_cost = deemed_val_pkr if deemed_val_pkr > 0 else tax_pkr
                        lots_by_asset[ev.asset_id].append(
                            FifoLot(
                                asset_id=ev.asset_id,
                                acq_date=ev.tx_date,
                                quantity=ev.quantity,
                                cost_basis_pkr=bonus_lot_cost,
                                cost_basis_native=deemed_native,
                            )
                        )
                    if tax_pkr > 0:
                        cashflows.append((ev.tx_date, -float(tax_pkr)))
                        if auto_manage_cash and ev.symbol != "PKR_CASH":
                            linked_cash_delta_pkr -= tax_pkr

                elif ev.tx_type == "SPLIT":
                    if lots_by_asset[ev.asset_id]:
                        _scale_lots_proportionally(lots_by_asset[ev.asset_id], ev.quantity)
                    elif ev.quantity > 0:
                        lots_by_asset[ev.asset_id].append(
                            FifoLot(
                                asset_id=ev.asset_id,
                                acq_date=ev.tx_date,
                                quantity=ev.quantity,
                                cost_basis_pkr=D("0"),
                                cost_basis_native=D("0"),
                            )
                        )

                elif ev.tx_type == "SELL":
                    gross_pkr = ev.gross_amount * fx if ev.gross_amount > 0 else ev.quantity * ev.unit_price * fx
                    net_proceeds_pkr = gross_pkr - fee_pkr
                    remaining_to_sell = ev.quantity
                    consumed_cost = D("0")
                    lots = lots_by_asset[ev.asset_id]

                    matched_lot_records: list[tuple[Decimal, Decimal, date]] = []
                    while remaining_to_sell > 0 and lots:
                        head = lots[0]
                        if head.quantity <= remaining_to_sell:
                            lot_cost = head.cost_basis_pkr
                            lot_qty = head.quantity
                            acq_dt = head.acq_date
                            lots.popleft()
                        else:
                            proportion = remaining_to_sell / head.quantity
                            lot_cost = head.cost_basis_pkr * proportion
                            lot_cost_nat = head.cost_basis_native * proportion
                            lot_qty = remaining_to_sell
                            acq_dt = head.acq_date
                            head.quantity -= remaining_to_sell
                            head.cost_basis_pkr -= lot_cost
                            head.cost_basis_native -= lot_cost_nat

                        consumed_cost += lot_cost
                        remaining_to_sell -= lot_qty
                        matched_lot_records.append((lot_qty, lot_cost, acq_dt))

                    if remaining_to_sell > EPS:
                        unmatched_sell_by_asset[ev.asset_id] += remaining_to_sell
                        warnings.append(
                            f"Unmatched SELL of {remaining_to_sell.quantize(Q4, rounding=ROUND_HALF_UP)} "
                            f"{ev.symbol} units on {ev.tx_date.isoformat()} exceeded open FIFO lots; "
                            f"unmatched portion was excluded from realized gain and CGT until an OPENING_BALANCE or BUY is logged."
                        )

                    matched_qty = ev.quantity - max(D("0"), remaining_to_sell)
                    matched_ratio = (matched_qty / ev.quantity) if ev.quantity > 0 else D("0")
                    matched_proceeds = net_proceeds_pkr * matched_ratio

                    for lot_qty, lot_cost, acq_dt in matched_lot_records:
                        lot_share = (lot_qty / ev.quantity) if ev.quantity > 0 else D("0")
                        lot_gain = (net_proceeds_pkr * lot_share) - lot_cost
                        cgt_rate = _effective_cgt_rate(ev, acq_dt, ev.tx_date)
                        if lot_gain > 0:
                            cgt_by_asset[ev.asset_id] += lot_gain * (cgt_rate / D("100"))
                        fy = get_pakistan_fiscal_year(ev.tx_date)
                        disposals.append(
                            _DisposalRecord(
                                tx_date=ev.tx_date,
                                fiscal_year=fy,
                                fy_start_year=fiscal_year_start_year(fy),
                                asset_id=ev.asset_id,
                                gain_pkr=lot_gain,
                                cgt_rate_pct=cgt_rate,
                            )
                        )

                    realized_gain = matched_proceeds - consumed_cost
                    realized_by_asset[ev.asset_id] += realized_gain
                    cashflows.append((ev.tx_date, float(net_proceeds_pkr - tax_pkr)))
                    if auto_manage_cash and ev.symbol != "PKR_CASH":
                        linked_cash_delta_pkr += net_proceeds_pkr - tax_pkr
                    elif nav_per_unit > 0:
                        redeemed = min(virtual_units, net_proceeds_pkr / nav_per_unit)
                        virtual_units -= redeemed
                        net_invested_pkr -= net_proceeds_pkr

                elif ev.tx_type in {"DIVIDEND", "REINVEST_DIVIDEND"}:
                    gross_pkr = ev.gross_amount * fx if ev.gross_amount > 0 else ev.quantity * ev.unit_price * fx
                    net_div = gross_pkr - tax_pkr - fee_pkr
                    dividends_by_asset[ev.asset_id] += net_div

                    purif = (
                        ev.shariah_purification_pkr * fx
                        if ev.shariah_purification_pkr > 0
                        else round_pkr(gross_pkr * (ev.shariah_purification_rate_pct / D("100")))
                    )
                    purification_by_asset[ev.asset_id] += purif

                    if ev.tx_type == "REINVEST_DIVIDEND":
                        units = (
                            ev.quantity
                            if ev.quantity > 0
                            else (
                                (net_div / (ev.unit_price * fx)).quantize(Q8, rounding=ROUND_HALF_UP)
                                if ev.unit_price > 0
                                else D("0")
                            )
                        )
                        if units > 0:
                            net_div_native = max(D("0"), (net_div / fx) if fx > 0 else net_div)
                            lots_by_asset[ev.asset_id].append(
                                FifoLot(
                                    asset_id=ev.asset_id,
                                    acq_date=ev.tx_date,
                                    quantity=units,
                                    cost_basis_pkr=max(D("0"), net_div),
                                    cost_basis_native=net_div_native,
                                )
                            )
                    else:
                        if net_div != 0:
                            cashflows.append((ev.tx_date, float(net_div)))
                        if auto_manage_cash and ev.symbol != "PKR_CASH":
                            linked_cash_delta_pkr += net_div

                elif ev.tx_type == "DEPOSIT":
                    gross_native = ev.gross_amount if ev.gross_amount > 0 else ev.quantity * ev.unit_price
                    gross_pkr = gross_native * fx
                    if ev.quantity > 0:
                        lots_by_asset[ev.asset_id].append(
                            FifoLot(
                                asset_id=ev.asset_id,
                                acq_date=ev.tx_date,
                                quantity=ev.quantity,
                                cost_basis_pkr=gross_pkr,
                                cost_basis_native=gross_native,
                            )
                        )
                    cashflows.append((ev.tx_date, -float(gross_pkr)))
                    if nav_per_unit > 0 and gross_pkr > 0:
                        virtual_units += gross_pkr / nav_per_unit
                        net_invested_pkr += gross_pkr

                elif ev.tx_type in {"WITHDRAWAL", "ZAKAT_PAID"}:
                    gross_pkr = ev.gross_amount * fx if ev.gross_amount > 0 else ev.quantity * ev.unit_price * fx
                    remaining = ev.quantity
                    lots = lots_by_asset[ev.asset_id]
                    while remaining > 0 and lots:
                        head = lots[0]
                        if head.quantity <= remaining:
                            remaining -= head.quantity
                            lots.popleft()
                        else:
                            ratio = remaining / head.quantity
                            head.cost_basis_pkr -= head.cost_basis_pkr * ratio
                            head.cost_basis_native -= head.cost_basis_native * ratio
                            head.quantity -= remaining
                            remaining = D("0")
                    outflow = gross_pkr - fee_pkr if ev.tx_type == "WITHDRAWAL" else gross_pkr
                    cashflows.append((ev.tx_date, float(outflow)))
                    if ev.tx_type == "ZAKAT_PAID" and auto_manage_cash and ev.symbol != "PKR_CASH":
                        linked_cash_delta_pkr -= gross_pkr
                    elif nav_per_unit > 0 and gross_pkr > 0:
                        redeemed = min(virtual_units, gross_pkr / nav_per_unit)
                        virtual_units -= redeemed
                        net_invested_pkr -= gross_pkr

                elif ev.tx_type == "FEE":
                    cashflows.append((ev.tx_date, -float(fee_pkr)))
                    if auto_manage_cash and ev.symbol != "PKR_CASH":
                        linked_cash_delta_pkr -= fee_pkr

            # Record end-of-day mark-to-market snapshot
            use_live_today = (cur_date == valuation_date) and not is_historical_cutoff
            post_mv, post_cb = _book_mv_on_date(
                linked_cash_delta_pkr if auto_manage_cash else D("0"),
                cur_date,
                allow_live_price=use_live_today,
            )
            if virtual_units > 0:
                nav_per_unit = (post_mv / virtual_units).quantize(Q4, rounding=ROUND_HALF_UP)
                snap = DailySnapshotPoint(
                    snapshot_date=cur_date,
                    total_value_pkr=round_pkr(post_mv),
                    total_cost_basis_pkr=round_pkr(post_cb),
                    net_invested_pkr=round_pkr(net_invested_pkr),
                    virtual_units=virtual_units.quantize(Q8, rounding=ROUND_HALF_UP),
                    nav_per_unit=nav_per_unit,
                )
                if not daily_snapshots or daily_snapshots[-1].snapshot_date != cur_date:
                    daily_snapshots.append(snap)
                else:
                    daily_snapshots[-1] = snap

    # Compute FY CGT summaries with Sec 37A loss netting + 3-year carryforward
    cgt_by_fy = _compute_cgt_by_fiscal_year(disposals)
    total_netted_cgt = (
        sum((summary.estimated_cgt_pkr for summary in cgt_by_fy.values()), D("0"))
        if cgt_by_fy
        else D("0")
    )

    # Aggregate open positions & final valuation
    preliminary_positions: list[AssetPositionResult] = []
    all_open_lots: list[FifoLot] = []
    total_mv = D("0")
    total_cb = D("0")
    shariah_mv = D("0")
    class_totals: dict[str, Decimal] = defaultdict(D)
    pkr_cash_handled = False
    pkr_cash_final_qty = D("0")

    for asset_id, meta in meta_by_asset.items():
        lots = lots_by_asset[asset_id]
        qty = sum((lot.quantity for lot in lots), D("0"))
        cost_basis = sum((lot.cost_basis_pkr for lot in lots), D("0"))
        cost_basis_nat = sum((lot.cost_basis_native for lot in lots), D("0"))
        all_open_lots.extend(lots)

        if auto_manage_cash and meta.symbol == "PKR_CASH":
            pkr_cash_handled = True
            qty += linked_cash_delta_pkr
            cost_basis += linked_cash_delta_pkr
            cost_basis_nat += linked_cash_delta_pkr
            pkr_cash_final_qty = qty

        curr_price_pkr = _market_price_pkr_on_date(
            asset_id,
            valuation_date,
            allow_live_price=not is_historical_cutoff,
        )
        market_value = qty * curr_price_pkr

        if (
            abs(qty) <= EPS
            and realized_by_asset[asset_id] == 0
            and dividends_by_asset[asset_id] == 0
        ):
            continue

        avg_cost = (cost_basis / qty) if qty > 0 else D("0")
        unrealized = market_value - cost_basis
        unrealized_pct = (unrealized / cost_basis * D("100")) if cost_basis > 0 else D("0")

        asset_ccy = (meta.asset_currency or "PKR").upper()
        if asset_ccy == "USD" and qty > 0 and cost_basis_nat > 0:
            avg_cost_nat = cost_basis_nat / qty
            avg_cost_fx = cost_basis / cost_basis_nat
            curr_fx = _fx_on_date("USD", valuation_date) if is_historical_cutoff else usd_to_pkr
            curr_px_nat = (curr_price_pkr / curr_fx) if curr_fx > 0 else meta.current_price_native
            nat_ret_pct = ((curr_px_nat - avg_cost_nat) / avg_cost_nat) * D("100") if avg_cost_nat > 0 else D("0")
            fx_ret_pct = (
                ((curr_fx - avg_cost_fx) / avg_cost_fx) * D("100")
                if avg_cost_fx > 0
                else (unrealized_pct - nat_ret_pct)
            )
        else:
            avg_cost_nat = avg_cost
            curr_px_nat = curr_price_pkr
            nat_ret_pct = unrealized_pct
            fx_ret_pct = D("0")

        total_mv += market_value
        total_cb += cost_basis
        if meta.is_shariah_compliant and market_value > 0:
            shariah_mv += market_value
        if market_value > 0:
            class_totals[meta.asset_class] += market_value

        preliminary_positions.append(
            AssetPositionResult(
                asset_id=asset_id,
                symbol=meta.symbol,
                name=meta.name,
                asset_class=meta.asset_class,
                sector_or_category=meta.sector_or_category,
                is_shariah_compliant=meta.is_shariah_compliant,
                quantity=qty.quantize(Q8, rounding=ROUND_HALF_UP),
                avg_cost_pkr=avg_cost.quantize(Q4, rounding=ROUND_HALF_UP),
                cost_basis_pkr=round_pkr(cost_basis),
                current_price_pkr=curr_price_pkr.quantize(Q4, rounding=ROUND_HALF_UP),
                market_value_pkr=round_pkr(market_value),
                unrealized_pnl_pkr=round_pkr(unrealized),
                unrealized_pnl_pct=unrealized_pct.quantize(D("0.01"), rounding=ROUND_HALF_UP),
                realized_pnl_pkr=round_pkr(realized_by_asset[asset_id]),
                dividends_net_pkr=round_pkr(dividends_by_asset[asset_id]),
                shariah_purification_pkr=round_pkr(purification_by_asset[asset_id]),
                estimated_cgt_pkr=round_pkr(cgt_by_asset[asset_id]),
                currency=asset_ccy,
                avg_cost_native=avg_cost_nat.quantize(Q4, rounding=ROUND_HALF_UP),
                current_price_native=curr_px_nat.quantize(Q4, rounding=ROUND_HALF_UP),
                native_return_pct=nat_ret_pct.quantize(D("0.01"), rounding=ROUND_HALF_UP),
                fx_return_pct=fx_ret_pct.quantize(D("0.01"), rounding=ROUND_HALF_UP),
            )
        )

    if auto_manage_cash and not pkr_cash_handled and linked_cash_delta_pkr != 0:
        pkr_cash_final_qty = linked_cash_delta_pkr
        total_mv += linked_cash_delta_pkr
        total_cb += linked_cash_delta_pkr
        if linked_cash_delta_pkr > 0:
            class_totals["CASH"] += linked_cash_delta_pkr
        preliminary_positions.append(
            AssetPositionResult(
                asset_id="virtual-pkr-cash",
                symbol="PKR_CASH",
                name="Auto-Managed PKR Wallet",
                asset_class="CASH",
                sector_or_category="Cash & Savings",
                is_shariah_compliant=True,
                quantity=linked_cash_delta_pkr.quantize(Q8, rounding=ROUND_HALF_UP),
                avg_cost_pkr=D("1.0000"),
                cost_basis_pkr=round_pkr(linked_cash_delta_pkr),
                current_price_pkr=D("1.0000"),
                market_value_pkr=round_pkr(linked_cash_delta_pkr),
                unrealized_pnl_pkr=D("0.00"),
                unrealized_pnl_pct=D("0.00"),
                realized_pnl_pkr=D("0.00"),
                dividends_net_pkr=D("0.00"),
                shariah_purification_pkr=D("0.00"),
                estimated_cgt_pkr=D("0.00"),
            )
        )

    if auto_manage_cash and pkr_cash_final_qty < D("-0.01"):
        warnings.append(
            f"Auto-managed PKR_CASH balance is negative ({round_pkr(pkr_cash_final_qty)} PKR); "
            "log a PKR_CASH DEPOSIT or OPENING_BALANCE to cover purchases."
        )

    preliminary_positions.sort(key=lambda p: p.market_value_pkr, reverse=True)

    total_unrealized = total_mv - total_cb
    total_unrealized_pct = (total_unrealized / total_cb * D("100")) if total_cb > 0 else D("0")
    total_realized = sum(realized_by_asset.values(), D("0"))
    total_divs = sum(dividends_by_asset.values(), D("0"))
    total_purification = sum(purification_by_asset.values(), D("0"))
    shariah_pct = (shariah_mv / total_mv * D("100")) if total_mv > 0 else D("0")

    allocation_list = [
        {
            "asset_class": cls_name,
            "market_value_pkr": round_pkr(val),
            "allocation_pct": ((val / total_mv) * D("100")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
            if total_mv > 0
            else D("0"),
        }
        for cls_name, val in sorted(class_totals.items(), key=lambda x: x[1], reverse=True)
    ]

    if total_mv > 0:
        cashflows.append((valuation_date, float(total_mv)))

    final_nav = (total_mv / virtual_units).quantize(Q4, rounding=ROUND_HALF_UP) if virtual_units > 0 else D("100.0000")
    twrr_pct = (final_nav - D("100")).quantize(D("0.01"), rounding=ROUND_HALF_UP) if virtual_units > 0 else None

    if virtual_units > 0:
        final_point = DailySnapshotPoint(
            snapshot_date=valuation_date,
            total_value_pkr=round_pkr(total_mv),
            total_cost_basis_pkr=round_pkr(total_cb),
            net_invested_pkr=round_pkr(net_invested_pkr),
            virtual_units=virtual_units.quantize(Q8, rounding=ROUND_HALF_UP),
            nav_per_unit=final_nav,
        )
        if not daily_snapshots or daily_snapshots[-1].snapshot_date != valuation_date:
            daily_snapshots.append(final_point)
        else:
            daily_snapshots[-1] = final_point

    return PortfolioValuationResult(
        total_market_value_pkr=round_pkr(total_mv),
        total_cost_basis_pkr=round_pkr(total_cb),
        total_unrealized_pnl_pkr=round_pkr(total_unrealized),
        total_unrealized_pnl_pct=total_unrealized_pct.quantize(D("0.01"), rounding=ROUND_HALF_UP),
        total_realized_pnl_pkr=round_pkr(total_realized),
        total_net_dividends_pkr=round_pkr(total_divs),
        total_shariah_purification_pkr=round_pkr(total_purification),
        total_fees_pkr=round_pkr(total_fees),
        total_tax_withheld_pkr=round_pkr(total_tax_withheld),
        estimated_cgt_pkr=round_pkr(total_netted_cgt),
        xirr_pct=compute_xirr(cashflows),
        twrr_pct=twrr_pct,
        nav_per_unit=final_nav,
        shariah_compliant_pct=shariah_pct.quantize(D("0.01"), rounding=ROUND_HALF_UP),
        allocation_by_class=allocation_list,
        daily_nav_series=daily_snapshots,
        open_lots=all_open_lots,
        holdings=preliminary_positions,
        cgt_by_fy=cgt_by_fy,
        warnings=warnings,
    )
