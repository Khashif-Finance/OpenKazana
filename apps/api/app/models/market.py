from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Asset(Base):
    """Unified multi-asset instrument registry across PSX, MUFAP, NSS, FX, Gold, Crypto, Cash, and Custom."""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="PKR", nullable=False)
    sector_or_category: Mapped[str] = mapped_column(String(120), default="General", nullable=False)
    is_shariah_compliant: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    prev_close: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    change_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="seed", nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    symbol_aliases: Mapped[list["AssetSymbolAlias"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    psx_profile: Mapped["PsxProfile | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )
    mutual_fund_profile: Mapped["MutualFundProfile | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )
    fixed_income_profile: Mapped["FixedIncomeProfile | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )
    shariah_metric: Mapped["ShariahMetric | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )
    price_bars: Mapped[list["PriceBar"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    financial_snapshot: Mapped["EquityFinancialSnapshot | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )
    financial_periods: Mapped[list["EquityFinancialPeriod"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    fmr_snapshot: Mapped["FundFmrSnapshot | None"] = relationship(
        back_populates="asset",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AssetSymbolAlias(Base):
    """Maps historical or merged PSX tickers (e.g. DAWH -> ENGROH, FFBL -> FFC) to canonical asset_id."""

    __tablename__ = "asset_symbol_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False)
    old_symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    asset: Mapped["Asset"] = relationship(back_populates="symbol_aliases")


class PsxProfile(Base):
    """PSX & International equity/ETF fundamental metrics and annual/quarterly summary data."""

    __tablename__ = "psx_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange: Mapped[str] = mapped_column(String(32), default="PSX", nullable=False)
    country: Mapped[str] = mapped_column(String(16), default="PK", nullable=False)
    default_wht_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    market_cap_mn: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), nullable=False)
    free_float_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    pb_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    roe_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    dividend_yield_pct: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), nullable=False)
    high_52w: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    low_52w: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    is_kse100: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_kmi30: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    financial_summary_json: Mapped[list[dict] | dict] = mapped_column(JSON, default=list, nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="psx_profile")


class MutualFundProfile(Base):
    """MUFAP Mutual Fund / VPS metadata, fees, adjusted NAV factor, daily dividend settings, and trailing returns."""

    __tablename__ = "mutual_fund_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    amc_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    fund_category: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    risk_profile: Mapped[str] = mapped_column(String(40), default="Medium", nullable=False)
    ter_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"), nullable=False)
    management_fee_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"), nullable=False)
    front_end_load_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"), nullable=False)
    back_end_load_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"), nullable=False)
    aum_mn: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), nullable=False)
    adjusted_nav_multiplier: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("1.000000"), nullable=False)
    last_distribution_pkr: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"), nullable=False)
    is_daily_dividend: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    constant_par_nav: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    annualized_yield_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"), nullable=False)
    default_payout_mode: Mapped[str] = mapped_column(String(24), default="REINVEST", nullable=False)
    return_1m_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    return_6m_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    return_1y_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    return_3y_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    benchmark_name: Mapped[str] = mapped_column(String(120), default="KSE-100", nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="mutual_fund_profile")


class FixedIncomeProfile(Base):
    """National Savings (NSS) & Government Fixed-Income accrual metadata."""

    __tablename__ = "fixed_income_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    instrument_type: Mapped[str] = mapped_column(String(40), nullable=False)  # BEHBOOD | SARWA_ISLAMIC | REGULAR_INCOME | DEFENCE | SUKUK
    annual_profit_rate_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    payout_frequency: Mapped[str] = mapped_column(String(24), default="MONTHLY", nullable=False)  # MONTHLY | SEMI_ANNUAL | MATURITY
    is_wht_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    asset: Mapped["Asset"] = relationship(back_populates="fixed_income_profile")


class ShariahMetric(Base):
    """Transparent KMI / AAOIFI screening breakdown and dividend purification rate per PSX equity."""

    __tablename__ = "shariah_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    fiscal_period: Mapped[str] = mapped_column(String(32), default="FY2025", nullable=False)
    core_business_compliant: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_kmi_all_share: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    debt_to_assets_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    non_compliant_inv_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    non_compliant_income_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    purification_rate_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    illiquid_assets_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="shariah_metric")


class PriceBar(Base):
    """OHLCV price or NAV historical bars (range-partitioned by bar_time in PostgreSQL 16)."""

    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "bar_time", name="uq_price_bar_asset_tf_time"),
        Index("ix_price_bar_lookup", "asset_id", "timeframe", "bar_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), default="1D", nullable=False)  # 5M | 1D
    open: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="price_bars")


class CorporateAction(Base):
    """PSX / Mutual Fund payouts, bonus shares, right issues, and splits."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("asset_id", "action_type", "ex_date", name="uq_corp_action_asset_type_exdate"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)  # CASH_DIVIDEND | BONUS_ISSUE | RIGHT_ISSUE | SPLIT
    ex_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    payout_per_share: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"), nullable=False)
    ratio_numerator: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"), nullable=False)
    ratio_denominator: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("100"), nullable=False)
    right_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="corporate_actions")


class Announcement(Base):
    """PUCARS corporate announcements & board notices."""

    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Notice", nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="announcements")


class ScraperRun(Base):
    """Telemetry log and DLQ pointer for every scheduled/manual scraper execution."""

    __tablename__ = "scraper_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scraper_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", nullable=False)  # RUNNING | SUCCESS | PARTIAL | FAILED
    records_upserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dlq_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class MacroIndicator(Base):
    """Macroeconomic benchmark rates (SBP Policy Rate, KIBOR, PKRV T-Bills/PIBs, CPI, US 10Y)."""

    __tablename__ = "macro_indicators"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country: Mapped[str] = mapped_column(String(8), default="PK", index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="PKRV", index=True, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="%", nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(48), default="sbp", nullable=False)
    history_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class EquityFinancialSnapshot(Base):
    """Deep Balance Sheet, Cash Flow, Valuation, and Quality snapshot for PSX & INTL equities."""

    __tablename__ = "equity_financial_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    fiscal_period: Mapped[str] = mapped_column(String(32), default="TTM", nullable=False)
    bvps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    total_assets_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_debt_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cash_equivalents_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    shareholders_equity_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    operating_cashflow_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    capex_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    free_cashflow_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    roe_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    roa_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    roic_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    operating_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    payout_ratio_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    analyst_target_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    analyst_rating: Mapped[str | None] = mapped_column(String(32), nullable=True)
    piotroski_f_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    altman_z_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    dcf_intrinsic_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(48), default="extracted", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="financial_snapshot")


class FundFmrSnapshot(Base):
    """
    SECP-standardized monthly Fund Manager Report (FMR) composition snapshot per Mutual Fund / VPS.
    Stores Asset Allocation %, Top 10 Equity Holdings %, Sector Allocation %, Credit Quality %,
    WAM/Duration, Stability Rating, Fee Anatomy & Benchmark Returns.
    """

    __tablename__ = "fund_fmr_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    report_month: Mapped[str] = mapped_column(String(16), default="", index=True, nullable=False)  # e.g. "2026-10"
    report_label: Mapped[str] = mapped_column(String(48), default="", nullable=False)  # e.g. "October 2026"
    amc_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    fund_manager: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stability_rating: Mapped[str | None] = mapped_column(String(48), nullable=True)  # e.g. "AA+(f) (VIS)"
    amc_rating: Mapped[str | None] = mapped_column(String(48), nullable=True)  # e.g. "AM1 (VIS)"
    risk_profile: Mapped[str | None] = mapped_column(String(48), nullable=True)
    launch_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    net_assets_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    wam_days: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    duration_days: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    ytm_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    ter_mtd_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    ter_ytd_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    govt_levy_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    mgmt_fee_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    asset_allocation_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    sector_allocation_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    top_holdings_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    credit_quality_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    returns_table_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    manager_commentary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_label: Mapped[str] = mapped_column(String(64), default="SECP FMR PDF", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="fmr_snapshot")


class EquityFinancialPeriod(Base):
    """
    Multi-period Annual & Quarterly 3-Statement financial records parsed from official
    PUCARS Annual/Quarterly PDF reports, user-uploaded PDFs, and PSX DPS financial tables.
    Powers Pandas multi-year growth, common-size, TTM, margin & cash-flow quality analytics.
    """

    __tablename__ = "equity_financial_periods"
    __table_args__ = (
        UniqueConstraint("asset_id", "period_type", "period_label", name="uq_eq_fin_period_asset_type_label"),
        Index("ix_eq_fin_period_lookup", "asset_id", "period_type", "period_end_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    period_type: Mapped[str] = mapped_column(String(16), default="ANNUAL", nullable=False)  # ANNUAL | QUARTERLY
    period_label: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. FY2025, Q3-2026, 9M-2026, HY-2026
    period_end_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    # Income Statement (in PKR Mn or native currency Mn)
    revenue_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cost_of_sales_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    gross_profit_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    operating_profit_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    finance_cost_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    profit_before_tax_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    taxation_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    net_profit_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    dps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    # Balance Sheet (in Mn)
    total_assets_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    current_assets_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    cash_equivalents_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    inventory_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    receivables_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_liabilities_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    current_liabilities_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_debt_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    shareholders_equity_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)

    # Cash Flow Statement (in Mn)
    operating_cashflow_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    capex_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    free_cashflow_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    dividends_paid_mn: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)

    # Key Qualitative Disclosures extracted from Annual / Quarterly PDFs
    auditor_opinion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    directors_highlights_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dividend_bonus_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    segment_notes_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    source_pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_label: Mapped[str] = mapped_column(String(64), default="PUCARS PDF", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    asset: Mapped["Asset"] = relationship(back_populates="financial_periods")


class NewsArticle(Base):
    """
    Aggregated Pakistan & Global financial news from Business Recorder, Profit (Pakistan Today),
    Dawn Business, Express Tribune Business, Investing.com, and PSX PUCARS, auto-tagged with
    PSX ticker symbols and official PSX sectors.
    """

    __tablename__ = "news_articles"
    __table_args__ = (
        Index("ix_news_published_source", "published_at", "source_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(700), nullable=False)
    source_key: Mapped[str] = mapped_column(String(48), index=True, nullable=False)  # brecorder | profit | dawn | tribune | investing
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Markets", index=True, nullable=False)  # Markets | Economy | Corporate | IPO | Macro | Commodities
    matched_symbols_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    matched_sectors_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class IpoListing(Base):
    """
    Pakistan Stock Exchange (PSX) Main Board & GEM Board IPOs, Book Building, and Recent Listings.
    """

    __tablename__ = "ipo_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(220), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    sector: Mapped[str] = mapped_column(String(120), default="General", nullable=False)
    board: Mapped[str] = mapped_column(String(24), default="MAIN", nullable=False)  # MAIN | GEM | DEBT
    status: Mapped[str] = mapped_column(String(24), default="LISTED", index=True, nullable=False)  # UPCOMING | OPEN | BOOK_BUILDING | LISTED
    issue_type: Mapped[str] = mapped_column(String(64), default="Book Building + Public Offer", nullable=False)
    floor_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    strike_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    issue_size_mn: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    subscription_multiple: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    book_building_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    lead_manager: Mapped[str | None] = mapped_column(String(180), nullable=True)
    prospectus_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_shariah_compliant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
