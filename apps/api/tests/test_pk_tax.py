from datetime import date
from decimal import Decimal

from app.services.pk_tax import (
    calculate_bonus_236z_tax,
    calculate_dividend_wht,
    calculate_zakat,
    compute_psx_round_trip_charges,
    compute_psx_trade_charges,
    convert_gold_from_24k_gram,
    fiscal_year_start_year,
    get_cgt_rate_pct,
    get_pakistan_fiscal_year,
    normalize_filer_status,
    parse_fiscal_year_bounds,
)


def test_filer_status_normalization_and_dividend_wht():
    assert normalize_filer_status("FILER") == "ACTIVE_FILER"
    assert normalize_filer_status("LATE_FILER") == "LATE_FILER"
    assert calculate_dividend_wht(Decimal("10000"), "ACTIVE_FILER") == Decimal("1500.00")
    assert calculate_dividend_wht(Decimal("10000"), "LATE_FILER") == Decimal("2250.00")
    assert calculate_dividend_wht(Decimal("10000"), "NON_FILER") == Decimal("3000.00")


def test_cgt_all_four_acquisition_regimes():
    # Pre July 2013 -> 0% exempt
    assert get_cgt_rate_pct(date(2012, 5, 1), date(2025, 10, 1), "ACTIVE_FILER") == Decimal("0")
    # July 2013 – June 2022 -> flat 12.5% for Active Filer
    assert get_cgt_rate_pct(date(2020, 1, 15), date(2025, 10, 1), "ACTIVE_FILER") == Decimal("12.5")
    assert get_cgt_rate_pct(date(2020, 1, 15), date(2025, 10, 1), "LATE_FILER") == Decimal("20")
    # July 2022 – June 2024 -> holding-period taper (held > 3 yrs -> 7.5%)
    assert get_cgt_rate_pct(date(2022, 8, 1), date(2025, 10, 1), "ACTIVE_FILER") == Decimal("7.5")
    # On/after July 1, 2024 -> flat 15% Active Filer regardless of holding period (0% sunset removed)
    assert get_cgt_rate_pct(date(2024, 8, 1), date(2026, 1, 1), "ACTIVE_FILER") == Decimal("15")
    assert get_cgt_rate_pct(date(2024, 8, 1), date(2032, 9, 1), "ACTIVE_FILER") == Decimal("15")
    assert get_cgt_rate_pct(date(2024, 8, 1), date(2026, 1, 1), "LATE_FILER") == Decimal("20")
    assert get_cgt_rate_pct(date(2024, 8, 1), date(2026, 1, 1), "NON_FILER") == Decimal("25")


def test_section_236z_bonus_tax():
    # 100 bonus shares * Rs. 540 deemed ex-price = Rs. 54,000 FMV
    assert calculate_bonus_236z_tax(Decimal("100"), Decimal("540"), "ACTIVE_FILER") == Decimal("5400.00")
    assert calculate_bonus_236z_tax(Decimal("100"), Decimal("540"), "LATE_FILER") == Decimal("8100.00")
    assert calculate_bonus_236z_tax(Decimal("100"), Decimal("540"), "NON_FILER") == Decimal("10800.00")


def test_psx_trade_charges_and_intraday_one_leg_brokerage():
    # Low-priced penny stock (1000 shares @ PKR 5 = 5000): 0.15% = 7.50 vs 0.03/sh floor = 30.00
    charges = compute_psx_trade_charges(Decimal("5000"), Decimal("1000"), broker_rate_bps=15)
    assert charges["brokerage"] == Decimal("30.00")
    assert charges["cdc_fee"] == Decimal("5.00")
    assert charges["total_charges"] > Decimal("35.00")

    intraday = compute_psx_round_trip_charges(
        buy_value_pkr=Decimal("100000"),
        sell_value_pkr=Decimal("100000"),
        quantity=Decimal("1000"),
        broker_rate_bps=15,
        is_intraday_square_off=True,
    )
    assert intraday["buy_charges"] > Decimal("150.00")
    # Sell leg waives brokerage & SST on same-day intraday square-off
    assert intraday["sell_charges"] < Decimal("20.00")


def test_gold_unit_conversions_and_fiscal_year_bounds():
    assert convert_gold_from_24k_gram(Decimal("25000"), unit="TOLA", karat=24) == Decimal("291595.10")
    assert convert_gold_from_24k_gram(Decimal("25000"), unit="10G", karat=22) == Decimal("229166.67")
    assert parse_fiscal_year_bounds("FY2025-26") == (date(2025, 7, 1), date(2026, 6, 30))


def test_zakat_nisab_thresholds():
    silver_res = calculate_zakat(
        total_zakatable_assets_pkr=Decimal("1000000"),
        short_term_liabilities_pkr=Decimal("200000"),
        nisab_standard="SILVER",
        silver_price_per_gram_pkr=Decimal("300"),
    )
    assert silver_res["is_zakat_due"] is True
    assert silver_res["zakat_payable_pkr"] == Decimal("20000.00")  # 2.5% of 800,000

    gold_res = calculate_zakat(
        total_zakatable_assets_pkr=Decimal("1000000"),
        short_term_liabilities_pkr=Decimal("0"),
        nisab_standard="GOLD",
        gold_price_per_gram_pkr=Decimal("25000"),  # Nisab ~ 2,187,000
    )
    assert gold_res["is_zakat_due"] is False
    assert gold_res["zakat_payable_pkr"] == Decimal("0")


def test_pakistan_fiscal_year_helpers():
    assert get_pakistan_fiscal_year(date(2025, 6, 30)) == "FY2024-25"
    assert get_pakistan_fiscal_year(date(2025, 7, 1)) == "FY2025-26"
    assert fiscal_year_start_year("FY2025-26") == 2025
