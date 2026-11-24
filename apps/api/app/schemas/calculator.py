from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class SipCalculatorRequest(BaseModel):
    initial_investment_pkr: Decimal = Field(default=Decimal("100000"), ge=Decimal("0"))
    monthly_contribution_pkr: Decimal = Field(default=Decimal("25000"), ge=Decimal("0"))
    annual_return_pct: Decimal = Field(default=Decimal("18"), ge=Decimal("-50"), le=Decimal("200"))
    years: int = Field(default=10, ge=1, le=50)
    front_end_load_pct: Decimal = Field(default=Decimal("1.5"), ge=Decimal("0"), le=Decimal("10"))
    ter_pct: Decimal = Field(default=Decimal("2.0"), ge=Decimal("0"), le=Decimal("10"))


class SipCalculatorResponse(BaseModel):
    total_invested_pkr: Decimal
    total_load_paid_pkr: Decimal
    projected_value_pkr: Decimal
    net_gain_pkr: Decimal
    effective_cagr_pct: Decimal
    yearly_schedule: list[dict[str, Decimal | int]]


class BrokerageCgtRequest(BaseModel):
    buy_price: Decimal = Field(gt=Decimal("0"))
    sell_price: Decimal = Field(gt=Decimal("0"))
    quantity: Decimal = Field(gt=Decimal("0"))
    broker_rate_bps: int = Field(default=15, ge=0, le=500)
    sst_rate_pct: Decimal = Field(default=Decimal("13"), ge=Decimal("0"), le=Decimal("30"))
    filer_status: Literal["ACTIVE_FILER", "LATE_FILER", "NON_FILER"] = "ACTIVE_FILER"
    acquisition_date: date | None = None
    holding_days: int = Field(default=180, ge=0)
    acquired_after_july_2024: bool = True
    is_intraday_square_off: bool = False


class BrokerageCgtResponse(BaseModel):
    gross_buy_pkr: Decimal
    gross_sell_pkr: Decimal
    buy_charges_pkr: Decimal
    sell_charges_pkr: Decimal
    total_charges_pkr: Decimal
    gross_profit_pkr: Decimal
    cgt_rate_pct: Decimal
    cgt_amount_pkr: Decimal
    net_profit_pkr: Decimal
    breakeven_sell_price: Decimal


class ZakatRequest(BaseModel):
    cash_and_bank_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    psx_stocks_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    mutual_funds_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    gold_and_silver_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    forex_and_crypto_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    short_term_liabilities_pkr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    nisab_standard: Literal["SILVER", "GOLD"] = "SILVER"
    silver_price_per_gram_pkr: Decimal = Field(default=Decimal("285"), gt=Decimal("0"))
    gold_price_per_gram_pkr: Decimal = Field(default=Decimal("24200"), gt=Decimal("0"))


class ZakatResponse(BaseModel):
    total_zakatable_assets_pkr: Decimal
    net_zakatable_wealth_pkr: Decimal
    nisab_threshold_pkr: Decimal
    is_zakat_due: bool
    zakat_payable_pkr: Decimal


class DailyDividendCalculatorRequest(BaseModel):
    principal_pkr: Decimal = Field(default=Decimal("500000"), gt=Decimal("0"))
    par_nav: Decimal = Field(default=Decimal("50.00"), gt=Decimal("0"))
    annual_yield_pct: Decimal = Field(default=Decimal("17.80"), gt=Decimal("0"), le=Decimal("100"))
    ter_pct: Decimal = Field(default=Decimal("0.85"), ge=Decimal("0"), le=Decimal("10"))
    days: int = Field(default=365, ge=1, le=3650)
    filer_status: Literal["ACTIVE_FILER", "LATE_FILER", "NON_FILER"] = "ACTIVE_FILER"
    mode: Literal["REINVEST", "CASH"] = "REINVEST"


class DailyDividendCalculatorResponse(BaseModel):
    principal_pkr: Decimal
    par_nav: Decimal
    initial_units: Decimal
    final_units: Decimal
    gross_dividend_pkr: Decimal
    wht_rate_pct: Decimal
    total_wht_pkr: Decimal
    net_dividend_pkr: Decimal
    final_value_pkr: Decimal
    effective_net_yield_pct: Decimal
    compounding_bonus_vs_cash_pkr: Decimal
    monthly_schedule: list[dict[str, Decimal | int]]
