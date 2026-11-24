from datetime import date
from decimal import Decimal

from app.services.accounting import LedgerEvent, compute_xirr, evaluate_portfolio


def _ev(
    symbol: str,
    tx_type: str,
    tx_date: date,
    qty: str,
    price: str,
    current_price: str = "200",
    asset_currency: str = "PKR",
    fx: str = "1",
    fee: str = "0",
    tax: str = "0",
    gross: str = "0",
    deemed_ex_price: str = "0",
    purification_rate: str = "0",
    purification_pkr: str = "0",
    shariah: bool = True,
) -> LedgerEvent:
    asset_class = "CASH" if symbol == "PKR_CASH" else ("CRYPTO" if asset_currency == "USD" else "PSX_EQUITY")
    return LedgerEvent(
        asset_id=f"id-{symbol}",
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        sector_or_category="Test",
        is_shariah_compliant=shariah,
        current_price_native=Decimal(current_price),
        asset_currency=asset_currency,
        tx_type=tx_type,
        tx_date=tx_date,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        gross_amount=Decimal(gross),
        fee_amount=Decimal(fee),
        tax_withheld=Decimal(tax),
        fx_rate_to_pkr=Decimal(fx),
        deemed_ex_price=Decimal(deemed_ex_price),
        shariah_purification_rate_pct=Decimal(purification_rate),
        shariah_purification_pkr=Decimal(purification_pkr),
    )


def test_fifo_partial_sell_and_realized_gain():
    events = [
        _ev("MEBL", "BUY", date(2025, 1, 10), "100", "150", current_price="240"),
        _ev("MEBL", "BUY", date(2025, 3, 10), "100", "200", current_price="240"),
        _ev("MEBL", "SELL", date(2025, 6, 10), "150", "230", current_price="240", fee="100"),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("278"), filer_status="ACTIVE_FILER", as_of_date=date(2025, 7, 1))
    pos = res.holdings[0]
    # Sold 100 @150 + 50 @200 = cost 25,000; net proceeds = 150*230 - 100 = 34,400 -> realized = 9,400
    assert pos.quantity == Decimal("50.00000000")
    assert pos.cost_basis_pkr == Decimal("10000.00")
    assert pos.realized_pnl_pkr == Decimal("9400.00")
    assert pos.market_value_pkr == Decimal("12000.00")
    assert res.estimated_cgt_pkr == Decimal("1410.00")  # 15% of 9,400


def test_bonus_share_dilutes_cost_basis_when_no_deemed_value():
    events = [
        _ev("SYS", "OPENING_BALANCE", date(2025, 1, 1), "100", "500", current_price="500"),
        _ev("SYS", "BONUS", date(2025, 4, 1), "25", "0", current_price="500"),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("278"), as_of_date=date(2025, 6, 1))
    pos = res.holdings[0]
    assert pos.quantity == Decimal("125.00000000")
    assert pos.cost_basis_pkr == Decimal("50000.00")
    assert pos.avg_cost_pkr == Decimal("400.0000")


def test_bonus_236z_deemed_cost_basis_prevents_double_tax():
    events = [
        _ev("SYS", "BUY", date(2025, 1, 1), "100", "500", current_price="600"),
        _ev("SYS", "BONUS", date(2025, 4, 1), "25", "0", current_price="600", deemed_ex_price="540", tax="1350"),
        _ev("SYS", "SELL", date(2025, 6, 1), "125", "600", current_price="600"),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("280"), filer_status="ACTIVE_FILER", as_of_date=date(2025, 6, 15))
    pos = res.holdings[0]
    # Cost basis = 50,000 + (25 * 540 = 13,500) = 63,500; proceeds = 125 * 600 = 75,000 -> gain = 11,500
    assert pos.quantity == Decimal("0.00000000")
    assert pos.realized_pnl_pkr == Decimal("11500.00")
    assert res.total_tax_withheld_pkr == Decimal("1350.00")
    assert res.estimated_cgt_pkr == Decimal("1725.00")  # 15% of 11,500


def test_same_day_intraday_netting_preserves_older_lot():
    events = [
        _ev("TRG", "BUY", date(2023, 8, 1), "5000", "100", current_price="155"),
        # Same-day intraday trade should net against itself, leaving 2023 lot untouched
        _ev("TRG", "BUY", date(2025, 6, 10), "1000", "140", current_price="155"),
        _ev("TRG", "SELL", date(2025, 6, 10), "1000", "150", current_price="155"),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("280"), filer_status="ACTIVE_FILER", as_of_date=date(2025, 6, 15))
    pos = res.holdings[0]
    assert pos.quantity == Decimal("5000.00000000")
    assert pos.cost_basis_pkr == Decimal("500000.00")
    assert pos.realized_pnl_pkr == Decimal("10000.00")
    assert len(res.open_lots) == 1
    assert res.open_lots[0].acq_date == date(2023, 8, 1)


def test_drip_reinvest_dividend_and_shariah_purification():
    events = [
        _ev("EFERT", "BUY", date(2025, 1, 1), "1000", "200", current_price="210", purification_rate="1.5"),
        _ev(
            "EFERT",
            "REINVEST_DIVIDEND",
            date(2025, 5, 1),
            "21.25",
            "200",
            gross="5000",
            tax="750",
            current_price="210",
            purification_rate="1.5",
        ),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("280"), filer_status="ACTIVE_FILER", as_of_date=date(2025, 6, 1))
    pos = res.holdings[0]
    assert pos.quantity == Decimal("1021.25000000")
    assert pos.cost_basis_pkr == Decimal("204250.00")
    assert res.total_net_dividends_pkr == Decimal("4250.00")
    assert res.total_shariah_purification_pkr == Decimal("75.00")


def test_auto_managed_cash_wallet_and_twrr():
    events = [
        _ev("PKR_CASH", "DEPOSIT", date(2025, 1, 1), "500000", "1", current_price="1", gross="500000"),
        _ev("MEBL", "BUY", date(2025, 1, 5), "1000", "200", current_price="240"),
    ]
    res = evaluate_portfolio(
        events,
        usd_to_pkr=Decimal("280"),
        filer_status="ACTIVE_FILER",
        as_of_date=date(2025, 6, 1),
        auto_manage_cash=True,
    )
    by_sym = {h.symbol: h for h in res.holdings}
    assert by_sym["PKR_CASH"].market_value_pkr == Decimal("300000.00")
    assert by_sym["MEBL"].market_value_pkr == Decimal("240000.00")
    assert res.total_market_value_pkr == Decimal("540000.00")
    assert res.nav_per_unit == Decimal("108.0000")
    assert res.twrr_pct == Decimal("8.00")


def test_multicurrency_btc_and_dividends():
    events = [
        _ev("BTC", "BUY", date(2025, 1, 1), "0.1", "60000", current_price="80000", asset_currency="USD", fx="280", shariah=False),
        _ev("EFERT", "BUY", date(2025, 1, 1), "100", "200", current_price="210", shariah=True),
        _ev("EFERT", "DIVIDEND", date(2025, 5, 1), "100", "10", gross="1000", tax="150", current_price="210", shariah=True),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("280"), as_of_date=date(2025, 6, 1))
    # BTC MV = 0.1 * 80000 * 280 = 2,240,000; EFERT MV = 21,000
    assert res.total_market_value_pkr == Decimal("2261000.00")
    assert res.total_net_dividends_pkr == Decimal("850.00")
    assert res.shariah_compliant_pct < Decimal("2.00")


def test_xirr_one_year_double():
    # -100,000 on 2024-01-01 and +120,000 on 2024-12-31 (~365 days) -> ~20.00%
    xirr = compute_xirr([(date(2024, 1, 1), -100000.0), (date(2024, 12, 31), 120000.0)])
    assert xirr is not None
    assert Decimal("19.90") <= xirr <= Decimal("20.10")


def test_historical_price_series_cutoff_and_twrr():
    events = [
        _ev("MEBL", "BUY", date(2025, 1, 5), "100", "200", current_price="350"),
    ]
    hist = {
        "id-MEBL": [
            (date(2025, 1, 5), Decimal("200")),
            (date(2025, 3, 15), Decimal("230")),
            (date(2025, 6, 30), Decimal("250")),
            (date(2025, 8, 1), Decimal("350")),
        ]
    }
    res = evaluate_portfolio(
        events,
        usd_to_pkr=Decimal("280"),
        as_of_date=date(2025, 6, 30),
        price_history_by_asset=hist,
    )
    pos = res.holdings[0]
    assert pos.current_price_pkr == Decimal("250.0000")
    assert pos.market_value_pkr == Decimal("25000.00")
    assert res.total_market_value_pkr == Decimal("25000.00")
    assert res.nav_per_unit == Decimal("125.0000")


def test_opening_balance_preserves_cash_wallet_and_warnings():
    events = [
        _ev("SYS", "OPENING_BALANCE", date(2025, 1, 1), "100", "400", current_price="440"),
        _ev("SYS", "SELL", date(2025, 2, 1), "150", "450", current_price="440"),  # Oversells by 50 shares
    ]
    res = evaluate_portfolio(
        events,
        usd_to_pkr=Decimal("280"),
        as_of_date=date(2025, 6, 1),
        auto_manage_cash=True,
    )
    by_sym = {h.symbol: h for h in res.holdings}
    # OPENING_BALANCE must NOT charge cash wallet; SELL proceeds +67,500 added to PKR_CASH
    assert by_sym["PKR_CASH"].market_value_pkr == Decimal("67500.00")
    assert any("Oversell" in w or "exceed" in w.lower() for w in res.warnings)


def test_sec_37a_loss_netting_and_3yr_carryforward_expiry():
    events = [
        # FY2020-21 loss of 10,000 -> carryforward valid FY2021-22 (1), FY2022-23 (2), FY2023-24 (3), EXPIRED FY2024-25 (4)
        _ev("LUCK", "BUY", date(2020, 8, 1), "100", "500", current_price="600"),
        _ev("LUCK", "SELL", date(2021, 5, 1), "100", "400", current_price="600"),
        # FY2022-23 gain of 6,000 -> absorbs 6,000 of FY2020-21 loss (4,000 left)
        _ev("MEBL", "BUY", date(2022, 8, 1), "100", "100", current_price="200"),
        _ev("MEBL", "SELL", date(2023, 2, 1), "100", "160", current_price="200"),
        # FY2024-25 gain of 10,000 -> remaining 4,000 from FY2020-21 expired! (2024 - 2020 = 4 > 3)
        _ev("SYS", "BUY", date(2024, 8, 1), "100", "300", current_price="400"),
        _ev("SYS", "SELL", date(2025, 2, 1), "100", "400", current_price="400"),
    ]
    res = evaluate_portfolio(events, usd_to_pkr=Decimal("280"), filer_status="ACTIVE_FILER", as_of_date=date(2025, 6, 30))

    fy21 = res.cgt_by_fy["FY2020-21"]
    assert fy21.gross_loss_pkr == Decimal("10000.00")
    assert fy21.unused_loss_carryforward_pkr == Decimal("10000.00")

    fy23 = res.cgt_by_fy["FY2022-23"]
    assert fy23.gross_gain_pkr == Decimal("6000.00")
    assert fy23.prior_loss_applied_pkr == Decimal("6000.00")
    assert fy23.net_taxable_gain_pkr == Decimal("0.00")
    assert fy23.estimated_cgt_pkr == Decimal("0.00")

    fy25 = res.cgt_by_fy["FY2024-25"]
    assert fy25.gross_gain_pkr == Decimal("10000.00")
    assert fy25.prior_loss_applied_pkr == Decimal("0.00")
    assert fy25.net_taxable_gain_pkr == Decimal("10000.00")
    assert fy25.estimated_cgt_pkr == Decimal("1500.00")


def test_intraday_partial_usd_match_preserves_native_cost_and_foreign_cgt():
    # Buy 10 NVDA @ $100 (FX 280) and sell 4 NVDA @ $110 (FX 280) on the same day.
    # Remaining 6 NVDA must preserve native cost $100.00 (NOT 28,000 PKR!), and later sell uses foreign_cgt_rate_pct.
    ev_buy = _ev("NVDA", "BUY", date(2025, 3, 10), "10", "100", current_price="125", asset_currency="USD", fx="280")
    ev_buy.asset_class = "INTL_EQUITY"
    ev_sell_same_day = _ev("NVDA", "SELL", date(2025, 3, 10), "4", "110", current_price="125", asset_currency="USD", fx="280")
    ev_sell_same_day.asset_class = "INTL_EQUITY"
    ev_sell_later = _ev("NVDA", "SELL", date(2025, 4, 15), "2", "120", current_price="125", asset_currency="USD", fx="285")
    ev_sell_later.asset_class = "INTL_EQUITY"

    res = evaluate_portfolio(
        [ev_buy, ev_sell_same_day, ev_sell_later],
        usd_to_pkr=Decimal("290"),
        filer_status="ACTIVE_FILER",
        as_of_date=date(2025, 5, 1),
        foreign_cgt_rate_pct=Decimal("20.00"),
    )
    pos = res.holdings[0]
    assert pos.quantity == Decimal("4.00000000")
    # Remaining 4 shares must have avg_cost_native = $100.0000 and avg_cost_pkr = 28,000.0000
    assert pos.avg_cost_native == Decimal("100.0000")
    assert pos.avg_cost_pkr == Decimal("28000.0000")
    # Native return: ($125 - $100) / $100 = +25.00%
    assert pos.native_return_pct == Decimal("25.00")
    # FX return: (290 - 280) / 280 = +3.57%
    assert pos.fx_return_pct == Decimal("3.57")
    # Realized gain = intraday 4 * ($110 - $100) * 280 = 11,200 + later 2 * (120*285 - 100*280) = 12,400 -> total 23,600
    assert pos.realized_pnl_pkr == Decimal("23600.00")
    # Foreign CGT @ 20% of 23,600 = 4,720.00
    assert res.estimated_cgt_pkr == Decimal("4720.00")
