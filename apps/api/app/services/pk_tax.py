from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
Q2 = D("0.01")
Q4 = D("0.0001")

# PSX / NCCPL standard regulatory fee constants
DEFAULT_CDC_FEE_PER_SHARE = D("0.005")
DEFAULT_REGULATORY_LEVY_RATE = D("0.000045")  # ~0.45 bps combined PSX/NCCPL/SECP
DEFAULT_SST_RATE = D("0.13")  # 13% provincial sales tax on brokerage

# Gold / Silver Nisab & conversion constants
GOLD_NISAB_GRAMS = D("87.48")
SILVER_NISAB_GRAMS = D("612.36")
GRAMS_PER_TOLA = D("11.6638038")
ZAKAT_RATE = D("0.025")

# Key FBR acquisition regime cutoff dates
CUTOFF_EXEMPT = date(2013, 7, 1)
CUTOFF_2022 = date(2022, 7, 1)
CUTOFF_2024 = date(2024, 7, 1)


def round_pkr(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def normalize_filer_status(filer_status: str | None) -> str:
    """Normalize legacy or current Filer status to ACTIVE_FILER | LATE_FILER | NON_FILER."""
    s = (filer_status or "ACTIVE_FILER").strip().upper()
    if s in {"FILER", "ACTIVE_FILER"}:
        return "ACTIVE_FILER"
    if s == "LATE_FILER":
        return "LATE_FILER"
    return "NON_FILER"


def convert_gold_from_24k_gram(
    price_per_gram_24k: Decimal,
    unit: str = "TOLA",
    karat: int = 24,
) -> Decimal:
    """Convert normalized 24K per-gram gold price to Tola / 10g / 1g and 24K / 22K purity."""
    purity = D(karat) / D("24")
    u = unit.upper()
    if u == "TOLA":
        grams = GRAMS_PER_TOLA
    elif u == "10G":
        grams = D("10")
    else:
        grams = D("1")
    return round_pkr(price_per_gram_24k * grams * purity)


def compute_psx_trade_charges(
    trade_value_pkr: Decimal,
    quantity: Decimal,
    broker_rate_bps: int = 15,
    sst_rate_pct: Decimal = D("13"),
    min_commission_per_share: Decimal = D("0.03"),
    waive_brokerage: bool = False,
) -> dict[str, Decimal]:
    """Compute PSX brokerage, SST, CDC, and regulatory charges for one trade leg."""
    if trade_value_pkr <= 0 or quantity <= 0:
        return {
            "brokerage": D("0"),
            "sst": D("0"),
            "cdc_fee": D("0"),
            "regulatory_levy": D("0"),
            "total_charges": D("0"),
        }

    if waive_brokerage:
        brokerage = D("0")
        sst = D("0")
    else:
        pct_commission = trade_value_pkr * D(broker_rate_bps) / D("10000")
        floor_commission = quantity * min_commission_per_share
        brokerage = max(pct_commission, floor_commission)
        sst = brokerage * (sst_rate_pct / D("100"))

    cdc_fee = quantity * DEFAULT_CDC_FEE_PER_SHARE
    regulatory_levy = trade_value_pkr * DEFAULT_REGULATORY_LEVY_RATE
    total = brokerage + sst + cdc_fee + regulatory_levy

    return {
        "brokerage": round_pkr(brokerage),
        "sst": round_pkr(sst),
        "cdc_fee": round_pkr(cdc_fee),
        "regulatory_levy": round_pkr(regulatory_levy),
        "total_charges": round_pkr(total),
    }


def compute_psx_round_trip_charges(
    buy_value_pkr: Decimal,
    sell_value_pkr: Decimal,
    quantity: Decimal,
    broker_rate_bps: int = 15,
    sst_rate_pct: Decimal = D("13"),
    is_intraday_square_off: bool = False,
) -> dict[str, Decimal]:
    """Compute buy + sell leg charges, charging brokerage on 1 leg only for PSX same-day square-off."""
    buy_leg = compute_psx_trade_charges(
        trade_value_pkr=buy_value_pkr,
        quantity=quantity,
        broker_rate_bps=broker_rate_bps,
        sst_rate_pct=sst_rate_pct,
        waive_brokerage=False,
    )
    sell_leg = compute_psx_trade_charges(
        trade_value_pkr=sell_value_pkr,
        quantity=quantity,
        broker_rate_bps=broker_rate_bps,
        sst_rate_pct=sst_rate_pct,
        waive_brokerage=is_intraday_square_off,
    )
    return {
        "buy_charges": buy_leg["total_charges"],
        "sell_charges": sell_leg["total_charges"],
        "total_charges": buy_leg["total_charges"] + sell_leg["total_charges"],
    }


def calculate_dividend_wht(
    gross_dividend_pkr: Decimal,
    filer_status: str = "ACTIVE_FILER",
    custom_rate_pct: Decimal | None = None,
) -> Decimal:
    """Calculate Pakistan dividend WHT (15% Active Filer, 22.5% Late Filer, 30% Non-Filer default)."""
    if gross_dividend_pkr <= 0:
        return D("0")
    if custom_rate_pct is not None:
        rate = custom_rate_pct / D("100")
    else:
        norm = normalize_filer_status(filer_status)
        if norm == "ACTIVE_FILER":
            rate = D("0.15")
        elif norm == "LATE_FILER":
            rate = D("0.225")
        else:
            rate = D("0.30")
    return round_pkr(gross_dividend_pkr * rate)


def calculate_intl_dividend_wht(
    gross_dividend: Decimal,
    us_w8ben_active: bool = True,
    country: str = "US",
    custom_rate_pct: Decimal | None = None,
) -> Decimal:
    """
    Calculate foreign dividend withholding tax at source:
    - US-Pakistan Tax Treaty (W-8BEN active): 15.0%
    - US Statutory Non-Resident Alien (no W-8BEN): 30.0%
    - Ireland UCITS ETFs (IE): 0.0%
    """
    if gross_dividend <= 0:
        return D("0")
    if custom_rate_pct is not None:
        rate = custom_rate_pct / D("100")
    elif country.upper() == "IE":
        rate = D("0")
    else:
        rate = D("0.15") if us_w8ben_active else D("0.30")
    return round_pkr(gross_dividend * rate)


def calculate_bonus_236z_tax(
    bonus_quantity: Decimal,
    deemed_ex_price: Decimal,
    filer_status: str = "ACTIVE_FILER",
) -> Decimal:
    """
    Calculate Section 236Z withholding tax on bonus shares (10% Active Filer,
    15% Late Filer estimate, 20% Non-Filer) based on book-closure deemed ex-price.
    """
    if bonus_quantity <= 0 or deemed_ex_price <= 0:
        return D("0")
    norm = normalize_filer_status(filer_status)
    if norm == "ACTIVE_FILER":
        rate = D("0.10")
    elif norm == "LATE_FILER":
        rate = D("0.15")
    else:
        rate = D("0.20")
    return round_pkr(bonus_quantity * deemed_ex_price * rate)


def get_cgt_rate_pct(
    acq_date: date,
    sell_date: date,
    filer_status: str = "ACTIVE_FILER",
    non_filer_effective_rate_pct: Decimal = D("25"),
) -> Decimal:
    """
    Determine Pakistan FBR Capital Gains Tax rate (%) across acquisition-date regimes:
    - Before 2013-07-01: 0%
    - 2013-07-01 .. 2022-06-30: 12.5% Active Filer
    - 2022-07-01 .. 2024-06-30: Holding-period taper (15% down to 0% after 6 years)
    - On/after 2024-07-01: Flat 15% Active Filer regardless of holding period;
      Late Filer (min 20%) and Non-Filer (progressive slab min 15%, default 25% estimate)
    """
    if sell_date < acq_date:
        return D("0")

    if acq_date < CUTOFF_EXEMPT:
        return D("0")

    norm = normalize_filer_status(filer_status)

    if acq_date >= CUTOFF_2024:
        if norm == "ACTIVE_FILER":
            return D("15")
        if norm == "LATE_FILER":
            return D("20")
        return max(D("15"), non_filer_effective_rate_pct)

    holding_years = Decimal((sell_date - acq_date).days) / D("365.25")

    if acq_date >= CUTOFF_2022:
        if holding_years <= D("1"):
            base = D("15")
        elif holding_years <= D("2"):
            base = D("12.5")
        elif holding_years <= D("3"):
            base = D("10")
        elif holding_years <= D("4"):
            base = D("7.5")
        elif holding_years <= D("5"):
            base = D("5")
        elif holding_years <= D("6"):
            base = D("2.5")
        else:
            base = D("0")
    else:
        base = D("12.5")

    if norm == "ACTIVE_FILER":
        return base
    if norm == "LATE_FILER":
        return max(base, D("20"))
    return max(base, non_filer_effective_rate_pct)


def parse_fiscal_year_bounds(fiscal_year: str) -> tuple[date, date]:
    """Parse 'FY2025-26' into Pakistan fiscal period (July 1, 2025 .. June 30, 2026)."""
    cleaned = fiscal_year.upper().replace("FY", "").strip()
    start_year = int(cleaned.split("-")[0])
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def get_pakistan_fiscal_year(ref_date: date) -> str:
    """Return canonical Pakistan fiscal year label (e.g. 'FY2025-26') for any date."""
    start_year = ref_date.year if ref_date.month >= 7 else ref_date.year - 1
    return f"FY{start_year}-{str(start_year + 1)[-2:]}"


def fiscal_year_start_year(fiscal_year: str) -> int:
    """Extract starting calendar year from 'FY2025-26'."""
    cleaned = fiscal_year.upper().replace("FY", "").strip()
    return int(cleaned.split("-")[0])


def calculate_zakat(
    total_zakatable_assets_pkr: Decimal,
    short_term_liabilities_pkr: Decimal,
    nisab_standard: str = "SILVER",
    silver_price_per_gram_pkr: Decimal = D("285"),
    gold_price_per_gram_pkr: Decimal = D("24200"),
) -> dict[str, Decimal | bool]:
    """Compute Net Zakatable Wealth, Nisab threshold, and 2.5% Zakat liability."""
    net_wealth = max(D("0"), total_zakatable_assets_pkr - short_term_liabilities_pkr)
    if nisab_standard.upper() == "GOLD":
        nisab = round_pkr(GOLD_NISAB_GRAMS * gold_price_per_gram_pkr)
    else:
        nisab = round_pkr(SILVER_NISAB_GRAMS * silver_price_per_gram_pkr)

    due = net_wealth >= nisab and net_wealth > 0
    zakat_amount = round_pkr(net_wealth * ZAKAT_RATE) if due else D("0")
    return {
        "net_zakatable_wealth_pkr": round_pkr(net_wealth),
        "nisab_threshold_pkr": nisab,
        "is_zakat_due": due,
        "zakat_payable_pkr": zakat_amount,
    }
