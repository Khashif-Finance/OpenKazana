from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

TxType = Literal[
    "OPENING_BALANCE",
    "BUY",
    "SELL",
    "DIVIDEND",
    "REINVEST_DIVIDEND",
    "BONUS",
    "RIGHTS",
    "SPLIT",
    "FEE",
    "DEPOSIT",
    "WITHDRAWAL",
    "ZAKAT_PAID",
]


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(default="PKR", max_length=8)
    description: str = Field(default="", max_length=255)
    auto_manage_cash: bool = True


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=255)
    auto_manage_cash: bool | None = None
    is_default: bool | None = None


class PortfolioOut(BaseModel):
    id: str
    name: str
    base_currency: str
    description: str
    auto_manage_cash: bool
    is_default: bool
    created_at: datetime


class TransactionCreate(BaseModel):
    asset_symbol: str = Field(min_length=1, max_length=32)
    tx_type: TxType
    tx_date: date
    quantity: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    unit_price: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    gross_amount: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    fee_amount: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    tax_withheld: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    deemed_ex_price: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    fx_rate_to_pkr: Decimal = Field(default=Decimal("1"), gt=Decimal("0"))
    corp_action_id: str | None = None
    notes: str = ""


class TransactionUpdate(BaseModel):
    tx_type: TxType | None = None
    tx_date: date | None = None
    quantity: Decimal | None = Field(default=None, ge=Decimal("0"))
    unit_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    gross_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    fee_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    tax_withheld: Decimal | None = Field(default=None, ge=Decimal("0"))
    deemed_ex_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    fx_rate_to_pkr: Decimal | None = Field(default=None, gt=Decimal("0"))
    notes: str | None = None


class TransactionOut(BaseModel):
    id: str
    portfolio_id: str
    asset_id: str
    asset_symbol: str
    asset_name: str
    asset_class: str
    tx_type: str
    tx_date: date
    quantity: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    fee_amount: Decimal
    tax_withheld: Decimal
    deemed_ex_price: Decimal
    shariah_purification_pkr: Decimal
    fx_rate_to_pkr: Decimal
    corp_action_id: str | None = None
    notes: str
    created_at: datetime


class HoldingPositionOut(BaseModel):
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
    allocation_pct: Decimal
    currency: str = "PKR"
    avg_cost_native: Decimal = Decimal("0")
    current_price_native: Decimal = Decimal("0")
    native_return_pct: Decimal = Decimal("0")
    fx_return_pct: Decimal = Decimal("0")
    is_daily_dividend_fund: bool = False
    annualized_yield_pct: Decimal | None = None
    accrued_daily_dividend_pkr: Decimal = Decimal("0")
    est_daily_income_pkr: Decimal = Decimal("0")
    est_monthly_income_pkr: Decimal = Decimal("0")


class DailyFundAccrualRequest(BaseModel):
    asset_symbol: str | None = None
    mode: Literal["REINVEST", "CASH"] = "REINVEST"
    annual_yield_override_pct: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("100"))
    annualized_yield_override_pct: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("100"))
    rollup: Literal["DAILY", "MONTHLY", "SINGLE"] = "MONTHLY"
    as_of_date: date | None = None


class DailyFundAccrualOut(BaseModel):
    accrued_count: int
    days_accrued: int
    mode: str
    rollup: str
    total_gross_pkr: Decimal
    total_wht_pkr: Decimal
    total_net_pkr: Decimal
    total_reinvested_units: Decimal
    transactions: list[TransactionOut]


class PortfolioQuantOut(BaseModel):
    benchmark_symbol: str
    risk_free_rate_pct: float
    metrics: dict[str, Any]
    drawdown_series: list[dict[str, Any]]
    monthly_returns: list[dict[str, Any]]
    correlation_matrix: dict[str, Any]
    optimization: list[dict[str, Any]]
    efficient_frontier: list[dict[str, Any]] = Field(default_factory=list)
    monte_carlo: list[dict[str, Any]] = Field(default_factory=list)
    stress_tests: list[dict[str, Any]] = Field(default_factory=list)


class PortfolioSummaryOut(BaseModel):
    portfolio: PortfolioOut
    usd_to_pkr: Decimal
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
    xirr_pct: Decimal | None = None
    twrr_pct: Decimal | None = None
    nav_per_unit: Decimal = Decimal("100.0000")
    shariah_compliant_pct: Decimal
    allocation_by_class: list[dict[str, Any]]
    daily_nav_series: list[dict[str, Any]]
    holdings: list[HoldingPositionOut]
    cgt_by_fy: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    daily_dividend_auto_accrued_pkr: Decimal = Decimal("0")
    daily_dividend_est_monthly_pkr: Decimal = Decimal("0")


class PendingCorporateActionOut(BaseModel):
    corp_action_id: str
    asset_id: str
    symbol: str
    asset_name: str
    action_type: str
    ex_date: date
    eligible_quantity: Decimal
    estimated_gross_pkr: Decimal
    estimated_wht_pkr: Decimal
    estimated_bonus_shares: Decimal
    deemed_ex_price: Decimal = Decimal("0")
    estimated_236z_tax_pkr: Decimal = Decimal("0")
    description: str


class FbrTaxYearSummaryOut(BaseModel):
    fiscal_year: str
    period_start: date
    period_end: date
    filer_status: str
    opening_cost_basis_pkr: Decimal
    closing_cost_basis_pkr: Decimal
    closing_market_value_pkr: Decimal
    total_gross_dividends_pkr: Decimal
    total_dividend_wht_pkr: Decimal
    total_236z_bonus_tax_pkr: Decimal
    total_realized_gain_pkr: Decimal
    gross_gain_pkr: Decimal = Decimal("0")
    gross_loss_pkr: Decimal = Decimal("0")
    prior_loss_applied_pkr: Decimal = Decimal("0")
    net_taxable_gain_pkr: Decimal = Decimal("0")
    estimated_cgt_pkr: Decimal
    unused_loss_carryforward_pkr: Decimal = Decimal("0")
    total_shariah_purification_pkr: Decimal
    holdings_cost_schedule: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class CsvPreviewRequest(BaseModel):
    preset: str = "generic"
    csv_text: str


class CsvColumnMapping(BaseModel):
    tx_date: str
    symbol: str
    tx_type: str
    quantity: str
    unit_price: str
    fee_amount: str | None = None
    tax_withheld: str | None = None
    notes: str | None = None


class CsvCommitRequest(BaseModel):
    portfolio_id: str
    csv_text: str
    mapping: CsvColumnMapping
    default_fx_rate_to_pkr: Decimal = Decimal("1")
    skip_duplicates: bool = True
    auto_calculate_charges: bool = False


class ShareLinkCreate(BaseModel):
    hide_amounts: bool = True


class ShareLinkOut(BaseModel):
    id: str
    token: str
    hide_amounts: bool
    is_active: bool
    created_at: datetime


class SharedPortfolioOut(BaseModel):
    portfolio_name: str
    hide_amounts: bool
    shariah_compliant_pct: Decimal
    total_unrealized_pnl_pct: Decimal
    xirr_pct: Decimal | None = None
    twrr_pct: Decimal | None = None
    total_market_value_pkr: Decimal | None = None
    allocation_by_class: list[dict[str, Any]]
    holdings: list[dict[str, Any]]
