from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.schemas.calculator import (
    BrokerageCgtRequest,
    BrokerageCgtResponse,
    DailyDividendCalculatorRequest,
    DailyDividendCalculatorResponse,
    SipCalculatorRequest,
    SipCalculatorResponse,
    ZakatRequest,
    ZakatResponse,
)
from app.services.pk_tax import (
    calculate_zakat,
    compute_psx_round_trip_charges,
    get_cgt_rate_pct,
    normalize_filer_status,
    round_pkr,
)

D = Decimal


def run_sip_calculator(req: SipCalculatorRequest) -> SipCalculatorResponse:
    """Simulate a Pakistani Mutual Fund / Equity SIP accounting for Front-End Load (FEL) and TER."""
    fel_factor = D("1") - (req.front_end_load_pct / D("100"))
    net_annual_rate = (req.annual_return_pct - req.ter_pct) / D("100")
    monthly_rate = net_annual_rate / D("12")

    balance = req.initial_investment_pkr * fel_factor
    total_invested = req.initial_investment_pkr
    total_load = req.initial_investment_pkr * (req.front_end_load_pct / D("100"))
    yearly_schedule: list[dict[str, Decimal | int]] = []

    for year in range(1, req.years + 1):
        for _ in range(12):
            balance = balance * (D("1") + monthly_rate)
            net_contrib = req.monthly_contribution_pkr * fel_factor
            load_contrib = req.monthly_contribution_pkr * (req.front_end_load_pct / D("100"))
            balance += net_contrib
            total_invested += req.monthly_contribution_pkr
            total_load += load_contrib

        yearly_schedule.append(
            {
                "year": year,
                "invested_pkr": round_pkr(total_invested),
                "projected_value_pkr": round_pkr(balance),
                "gain_pkr": round_pkr(balance - total_invested),
            }
        )

    net_gain = balance - total_invested
    eff_cagr = (
        ((float(balance) / float(total_invested)) ** (1.0 / req.years) - 1.0) * 100.0
        if total_invested > 0 and balance > 0
        else 0.0
    )

    return SipCalculatorResponse(
        total_invested_pkr=round_pkr(total_invested),
        total_load_paid_pkr=round_pkr(total_load),
        projected_value_pkr=round_pkr(balance),
        net_gain_pkr=round_pkr(net_gain),
        effective_cagr_pct=Decimal(str(eff_cagr)).quantize(D("0.01"), rounding=ROUND_HALF_UP),
        yearly_schedule=yearly_schedule,
    )


def run_brokerage_cgt_calculator(req: BrokerageCgtRequest) -> BrokerageCgtResponse:
    """Compute PSX round-trip fees (with 1-leg intraday option), FBR CGT, net profit, and breakeven."""
    gross_buy = req.buy_price * req.quantity
    gross_sell = req.sell_price * req.quantity

    round_trip = compute_psx_round_trip_charges(
        buy_value_pkr=gross_buy,
        sell_value_pkr=gross_sell,
        quantity=req.quantity,
        broker_rate_bps=req.broker_rate_bps,
        sst_rate_pct=req.sst_rate_pct,
        is_intraday_square_off=req.is_intraday_square_off,
    )
    buy_charges = round_trip["buy_charges"]
    sell_charges = round_trip["sell_charges"]
    total_charges = round_trip["total_charges"]

    gross_profit = gross_sell - gross_buy
    taxable_gain = max(D("0"), gross_profit - total_charges)

    sell_dt = date.today()
    if req.acquisition_date is not None:
        acq_dt = req.acquisition_date
    elif req.acquired_after_july_2024:
        acq_dt = max(date(2024, 7, 1), sell_dt - timedelta(days=req.holding_days))
    else:
        acq_dt = min(date(2024, 6, 30), sell_dt - timedelta(days=req.holding_days))

    cgt_rate_pct = get_cgt_rate_pct(acq_dt, sell_dt, filer_status=req.filer_status)
    cgt_amount = round_pkr(taxable_gain * (cgt_rate_pct / D("100")))
    net_profit = gross_profit - total_charges - cgt_amount
    breakeven = (gross_buy + total_charges) / req.quantity

    return BrokerageCgtResponse(
        gross_buy_pkr=round_pkr(gross_buy),
        gross_sell_pkr=round_pkr(gross_sell),
        buy_charges_pkr=round_pkr(buy_charges),
        sell_charges_pkr=round_pkr(sell_charges),
        total_charges_pkr=round_pkr(total_charges),
        gross_profit_pkr=round_pkr(gross_profit),
        cgt_rate_pct=cgt_rate_pct,
        cgt_amount_pkr=cgt_amount,
        net_profit_pkr=round_pkr(net_profit),
        breakeven_sell_price=breakeven.quantize(D("0.01"), rounding=ROUND_HALF_UP),
    )


def run_zakat_calculator(req: ZakatRequest) -> ZakatResponse:
    """Compute annual 2.5% Zakat liability on PKR multi-asset wealth."""
    total_assets = (
        req.cash_and_bank_pkr
        + req.psx_stocks_pkr
        + req.mutual_funds_pkr
        + req.gold_and_silver_pkr
        + req.forex_and_crypto_pkr
    )
    res = calculate_zakat(
        total_zakatable_assets_pkr=total_assets,
        short_term_liabilities_pkr=req.short_term_liabilities_pkr,
        nisab_standard=req.nisab_standard,
        silver_price_per_gram_pkr=req.silver_price_per_gram_pkr,
        gold_price_per_gram_pkr=req.gold_price_per_gram_pkr,
    )
    return ZakatResponse(
        total_zakatable_assets_pkr=round_pkr(total_assets),
        net_zakatable_wealth_pkr=res["net_zakatable_wealth_pkr"],  # type: ignore[arg-type]
        nisab_threshold_pkr=res["nisab_threshold_pkr"],  # type: ignore[arg-type]
        is_zakat_due=bool(res["is_zakat_due"]),
        zakat_payable_pkr=res["zakat_payable_pkr"],  # type: ignore[arg-type]
    )


def run_daily_dividend_calculator(req: DailyDividendCalculatorRequest) -> DailyDividendCalculatorResponse:
    """
    Simulate a constant-NAV Daily Dividend Money Market Fund (e.g. MRAF, NIDDF, MICF, SGOV)
    comparing Daily Unit Reinvestment (DRIP) vs Daily Cash Payout after dividend WHT.
    """
    norm_filer = normalize_filer_status(req.filer_status)
    if norm_filer == "ACTIVE_FILER":
        wht_rate = D("0.15")
    elif norm_filer == "LATE_FILER":
        wht_rate = D("0.225")
    else:
        wht_rate = D("0.30")

    net_annual_yield = max(D("0"), req.annual_yield_pct - req.ter_pct) / D("100")
    daily_gross_rate = net_annual_yield / D("365")

    initial_units = (req.principal_pkr / req.par_nav).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    units_drip = initial_units
    gross_drip_total = D("0")
    wht_drip_total = D("0")
    net_drip_total = D("0")

    gross_cash_total = D("0")
    wht_cash_total = D("0")
    net_cash_total = D("0")

    schedule: list[dict[str, Decimal | int]] = []
    for day in range(1, req.days + 1):
        # Daily DRIP path
        day_gross_drip = units_drip * req.par_nav * daily_gross_rate
        day_wht_drip = day_gross_drip * wht_rate
        day_net_drip = day_gross_drip - day_wht_drip
        units_drip += day_net_drip / req.par_nav
        gross_drip_total += day_gross_drip
        wht_drip_total += day_wht_drip
        net_drip_total += day_net_drip

        # Daily Cash Payout path (units stay constant at initial_units)
        day_gross_cash = initial_units * req.par_nav * daily_gross_rate
        day_wht_cash = day_gross_cash * wht_rate
        day_net_cash = day_gross_cash - day_wht_cash
        gross_cash_total += day_gross_cash
        wht_cash_total += day_wht_cash
        net_cash_total += day_net_cash

        if day % 30 == 0 or day == req.days:
            month_idx = (day + 29) // 30
            if req.mode == "REINVEST":
                val = units_drip * req.par_nav
                u = units_drip
                net_d = net_drip_total
                wht_d = wht_drip_total
            else:
                val = req.principal_pkr + net_cash_total
                u = initial_units
                net_d = net_cash_total
                wht_d = wht_cash_total
            schedule.append(
                {
                    "period_day": day,
                    "month": month_idx,
                    "units": u.quantize(D("0.0001"), rounding=ROUND_HALF_UP),
                    "cumulative_net_dividend_pkr": round_pkr(net_d),
                    "cumulative_wht_pkr": round_pkr(wht_d),
                    "total_value_pkr": round_pkr(val),
                }
            )

    if req.mode == "REINVEST":
        final_units = units_drip.quantize(D("0.0001"), rounding=ROUND_HALF_UP)
        gross_div = gross_drip_total
        total_wht = wht_drip_total
        net_div = net_drip_total
        final_val = units_drip * req.par_nav
    else:
        final_units = initial_units
        gross_div = gross_cash_total
        total_wht = wht_cash_total
        net_div = net_cash_total
        final_val = req.principal_pkr + net_cash_total

    compounding_bonus = max(D("0"), net_drip_total - net_cash_total)
    eff_annual_pct = (
        ((net_div / req.principal_pkr) * (D("365") / D(req.days)) * D("100")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        if req.principal_pkr > 0 and req.days > 0
        else D("0")
    )

    return DailyDividendCalculatorResponse(
        principal_pkr=round_pkr(req.principal_pkr),
        par_nav=req.par_nav.quantize(D("0.0001")),
        initial_units=initial_units,
        final_units=final_units,
        gross_dividend_pkr=round_pkr(gross_div),
        wht_rate_pct=(wht_rate * D("100")).quantize(D("0.01")),
        total_wht_pkr=round_pkr(total_wht),
        net_dividend_pkr=round_pkr(net_div),
        final_value_pkr=round_pkr(final_val),
        effective_net_yield_pct=eff_annual_pct,
        compounding_bonus_vs_cash_pkr=round_pkr(compounding_bonus),
        monthly_schedule=schedule,
    )
