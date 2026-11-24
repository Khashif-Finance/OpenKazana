from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class AssetSummaryOut(BaseModel):
    id: str
    symbol: str
    name: str
    asset_class: str
    currency: str
    sector_or_category: str
    is_shariah_compliant: bool
    is_custom: bool = False
    last_price: Decimal
    prev_close: Decimal
    change_pct: Decimal
    volume: Decimal
    source: str
    updated_at: datetime


class CustomAssetCreate(BaseModel):
    symbol: str = Field(min_length=2, max_length=24)
    name: str = Field(min_length=1, max_length=200)
    asset_class: Literal[
        "CUSTOM",
        "PSX_EQUITY",
        "INTL_EQUITY",
        "INTL_ETF",
        "MUTUAL_FUND",
        "FX",
        "GOLD",
        "CRYPTO",
        "CASH",
        "NSS",
    ] = "CUSTOM"
    currency: Literal["PKR", "USD"] = "PKR"
    sector_or_category: str = Field(default="Custom Holdings", max_length=120)
    is_shariah_compliant: bool = True
    last_price: Decimal = Field(gt=Decimal("0"))


class YFinanceImportRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24, description="Global ticker symbol on Yahoo Finance (e.g. AAPL, NVDA, SPUS, SGOV)")
    is_shariah_compliant: bool | None = None


class CustomAssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sector_or_category: str | None = Field(default=None, max_length=120)
    is_shariah_compliant: bool | None = None
    last_price: Decimal | None = Field(default=None, gt=Decimal("0"))


class PsxScreenerRowOut(AssetSummaryOut):
    exchange: str = "PSX"
    country: str = "PK"
    default_wht_rate_pct: Decimal | None = None
    market_cap_mn: Decimal
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    eps: Decimal | None = None
    roe_pct: Decimal | None = None
    dividend_yield_pct: Decimal
    high_52w: Decimal
    low_52w: Decimal
    is_kse100: bool
    is_kmi30: bool


class ShariahMetricOut(BaseModel):
    fiscal_period: str
    core_business_compliant: bool
    is_kmi_all_share: bool
    debt_to_assets_pct: Decimal
    non_compliant_inv_pct: Decimal
    non_compliant_income_pct: Decimal
    purification_rate_pct: Decimal
    illiquid_assets_pct: Decimal
    passed: bool


class CorporateActionOut(BaseModel):
    id: str
    asset_id: str
    symbol: str | None = None
    action_type: str
    ex_date: date
    payout_per_share: Decimal
    ratio_numerator: Decimal
    ratio_denominator: Decimal
    right_price: Decimal | None = None
    description: str


class AnnouncementOut(BaseModel):
    id: str
    symbol: str
    asset_name: str
    title: str
    category: str
    published_at: datetime
    source_url: str | None = None
    summary: str


class PriceBarOut(BaseModel):
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class MutualFundRowOut(AssetSummaryOut):
    amc_name: str
    fund_category: str
    risk_profile: str
    ter_pct: Decimal
    management_fee_pct: Decimal
    front_end_load_pct: Decimal
    back_end_load_pct: Decimal
    aum_mn: Decimal
    adjusted_nav_multiplier: Decimal = Decimal("1.000000")
    last_distribution_pkr: Decimal = Decimal("0")
    is_daily_dividend: bool = False
    constant_par_nav: Decimal | None = None
    annualized_yield_pct: Decimal | None = None
    default_payout_mode: str = "REINVEST"
    return_1m_pct: Decimal | None = None
    return_6m_pct: Decimal | None = None
    return_1y_pct: Decimal | None = None
    return_3y_pct: Decimal | None = None
    benchmark_name: str
    has_fmr: bool = False
    fmr_month: str | None = None
    stability_rating: str | None = None
    wam_days: Decimal | None = None
    asset_allocation_top: list[dict[str, Any]] = Field(default_factory=list)
    top_holding_symbols: list[str] = Field(default_factory=list)


class TechnicalAnalysisOut(BaseModel):
    overall_signal: str
    bull_score: int = 0
    bear_score: int = 0
    signals: list[str] = Field(default_factory=list)
    latest: dict[str, Any] = Field(default_factory=dict)
    series: list[dict[str, Any]] = Field(default_factory=list)


class FundamentalAnalysisOut(BaseModel):
    quality_score: int = 50
    valuation_score: int = 12
    profitability_score: int = 13
    growth_score: int = 12
    solvency_score: int = 13
    valuation_verdict: str = "FAIR_VALUE"
    graham_intrinsic_value: float | None = None
    margin_of_safety_pct: float | None = None
    implied_bvps: float | None = None
    implied_pe: float | None = None
    implied_pb: float | None = None
    earnings_yield_pct: float | None = None
    risk_free_rate_pct: float | None = None
    equity_risk_premium_pct: float | None = None
    revenue_cagr_pct: float | None = None
    eps_cagr_pct: float | None = None
    latest_net_margin_pct: float | None = None
    net_margin_trend: str = "STABLE"
    annual_financials: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    dcf_intrinsic_value: float | None = None
    piotroski_f_score: int | None = None
    altman_z_score: float | None = None


class StockDetailOut(BaseModel):
    asset: AssetSummaryOut
    profile: dict[str, Any] | None = None
    shariah: ShariahMetricOut | None = None
    corporate_actions: list[CorporateActionOut]
    announcements: list[AnnouncementOut]
    technicals: TechnicalAnalysisOut | None = None
    fundamentals: FundamentalAnalysisOut | None = None
    deep_snapshot: dict[str, Any] | None = None
    peer_valuation: dict[str, Any] | None = None
    dividend_history: dict[str, Any] | None = None
    valuation_lab: dict[str, Any] | None = None
    institutional_holders: list[dict[str, Any]] = Field(default_factory=list)
    technical_gauge: dict[str, Any] | None = None
    seasonality: dict[str, Any] | None = None
    factor_ranks: dict[str, Any] | None = None
    financial_reports: dict[str, Any] | None = None


class MarketOverviewOut(BaseModel):
    indices: list[AssetSummaryOut]
    fx_rates: list[AssetSummaryOut]
    gold_rates: list[AssetSummaryOut]
    crypto_rates: list[AssetSummaryOut]
    top_gainers: list[AssetSummaryOut]
    top_losers: list[AssetSummaryOut]
    top_dividend_yield: list[PsxScreenerRowOut]
    recent_announcements: list[AnnouncementOut]
    usd_to_pkr: Decimal
    macro_indicators: list[dict[str, Any]] = Field(default_factory=list)


class ScraperRunOut(BaseModel):
    id: str
    scraper_name: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    records_upserted: int
    latency_ms: int
    raw_payload_hash: str | None = None
    dlq_path: str | None = None
    error_detail: str | None = None


class MacroIndicatorOut(BaseModel):
    code: str
    name: str
    country: str
    category: str
    value: Decimal
    unit: str
    as_of_date: date
    source: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime


class EquityDeepSnapshotOut(BaseModel):
    fiscal_period: str = "TTM"
    bvps: Decimal | None = None
    total_assets_mn: Decimal | None = None
    total_debt_mn: Decimal | None = None
    cash_equivalents_mn: Decimal | None = None
    shareholders_equity_mn: Decimal | None = None
    operating_cashflow_mn: Decimal | None = None
    capex_mn: Decimal | None = None
    free_cashflow_mn: Decimal | None = None
    current_ratio: Decimal | None = None
    debt_to_equity: Decimal | None = None
    roe_pct: Decimal | None = None
    roa_pct: Decimal | None = None
    roic_pct: Decimal | None = None
    operating_margin_pct: Decimal | None = None
    payout_ratio_pct: Decimal | None = None
    analyst_target_price: Decimal | None = None
    analyst_rating: str | None = None
    piotroski_f_score: int | None = None
    altman_z_score: Decimal | None = None
    dcf_intrinsic_value: Decimal | None = None
    metrics_json: dict[str, Any] = Field(default_factory=dict)
    source: str = "extracted"


class ScreenerScanRowOut(BaseModel):
    symbol: str
    name: str
    asset_class: str
    currency: str
    sector_or_category: str
    is_shariah_compliant: bool
    last_price: Decimal
    change_pct: Decimal
    volume: Decimal
    market_cap_mn: Decimal
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    roe_pct: float | None = None
    sector_median_pe: float | None = None
    pe_vs_sector_pct: float | None = None
    is_kse100: bool = False
    is_kmi30: bool = False
    div_streak_years: int = 0
    deep_source: str | None = None
    dividend_yield_pct: Decimal = Decimal("0")
    quality_score: int | None = None
    piotroski_f_score: int | None = None
    dcf_upside_pct: float | None = None
    rsi_14: float | None = None
    adx_14: float | None = None
    macd_hist: float | None = None
    supertrend_signal: str | None = None
    golden_cross: bool = False
    above_sma_50: bool = False
    above_sma_200: bool = False
    return_1m_pct: float | None = None
    return_3m_pct: float | None = None
    return_1y_tr_pct: float | None = None
    overall_signal: str = "NEUTRAL"
    bar_count: int = 0


class FundAnalyticsOut(BaseModel):
    symbol: str
    name: str
    amc_name: str
    fund_category: str
    is_shariah_compliant: bool
    is_daily_dividend: bool
    benchmark_symbol: str
    risk_free_rate_pct: float
    nav: Decimal
    ter_pct: Decimal
    front_end_load_pct: Decimal
    bar_count: int
    total_return_1m_pct: float | None = None
    total_return_6m_pct: float | None = None
    total_return_1y_pct: float | None = None
    cagr_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    max_drawdown_pct: float | None = None
    alpha_annualized_pct: float | None = None
    beta: float | None = None
    tracking_error_pct: float | None = None
    information_ratio: float | None = None
    fee_drag_5y_pct: float | None = None
    nav_series: list[dict[str, Any]] = Field(default_factory=list)
    amc_peers: list[dict[str, Any]] = Field(default_factory=list)


class BacktestLegInput(BaseModel):
    symbol: str
    weight_pct: float = Field(gt=0, le=100)


class BacktestRequest(BaseModel):
    legs: list[BacktestLegInput] = Field(min_length=1, max_length=12)
    strategy: Literal[
        "BUY_AND_HOLD",
        "MONTHLY_SIP_REBALANCE",
        "RSI_MEAN_REVERSION",
        "SMA_CROSSOVER",
    ] = "BUY_AND_HOLD"
    benchmark_symbol: str = "KSE100"
    initial_capital_pkr: float = Field(default=1000000.0, gt=0)
    monthly_contribution_pkr: float = Field(default=0.0, ge=0)
    lookback_days: int = Field(default=365, ge=30, le=1825)
    return_mode: Literal["PRICE", "GROSS_TR", "NET_FILER", "NET_NON_FILER"] = "NET_FILER"
    real_return_mode: Literal["NOMINAL_PKR", "REAL_USD", "REAL_CPI_PKR"] = "NOMINAL_PKR"
    apply_shariah_purification: bool = False
    broker_bps: int = Field(default=15, ge=0, le=100)


class DataCoverageOut(BaseModel):
    psx_total: int
    psx_with_history: int
    psx_deep_fundamentals: int
    psx_financial_report_periods: int = 0
    psx_multi_year_profiles: int = 0
    kse100_total: int = 0
    kse100_deep_fundamentals: int = 0
    funds_total: int
    funds_with_history: int
    funds_with_fmr: int = 0
    amc_count: int
    amcs_with_history: int = 0
    intl_total: int = 0
    crypto_with_5y: int = 0
    fx_gold_with_5y: int = 0
    macro_indicators: int
    total_price_bars: int
    risk_free_rate_pkr_pct: float
    risk_free_rate_usd_pct: float
