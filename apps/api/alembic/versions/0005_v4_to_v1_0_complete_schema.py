"""v1.0 Complete Schema: FMR Snapshots, Multi-Period Financials, News, IPOs, User Preferences & Brokerage Research

Revision ID: 0005_v4_to_v1_0_complete_schema
Revises: 0004_v2_macro_and_deep_fundamentals
Create Date: 2026-11-25 21:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_v4_to_v1_0_complete_schema"
down_revision: Union[str, None] = "0004_v2_macro_and_deep_fundamentals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Per-user display & research preferences (v6)
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("shariah_only_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_market_view", sa.String(length=24), nullable=False, server_default="PSX"),
        sa.Column("compact_tables", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("show_news_ticker", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("preferred_news_sources", sa.String(length=400), nullable=False, server_default="ALL"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. SECP Monthly Fund Manager Report (FMR) composition snapshots (v4)
    op.create_table(
        "fund_fmr_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("report_month", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("report_label", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("amc_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("fund_manager", sa.String(length=120), nullable=True),
        sa.Column("stability_rating", sa.String(length=48), nullable=True),
        sa.Column("amc_rating", sa.String(length=48), nullable=True),
        sa.Column("risk_profile", sa.String(length=48), nullable=True),
        sa.Column("launch_date", sa.String(length=32), nullable=True),
        sa.Column("benchmark_name", sa.String(length=200), nullable=True),
        sa.Column("net_assets_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("wam_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("duration_days", sa.Numeric(12, 2), nullable=True),
        sa.Column("ytm_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("ter_mtd_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("ter_ytd_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("govt_levy_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("mgmt_fee_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("asset_allocation_json", sa.JSON(), nullable=False),
        sa.Column("sector_allocation_json", sa.JSON(), nullable=False),
        sa.Column("top_holdings_json", sa.JSON(), nullable=False),
        sa.Column("credit_quality_json", sa.JSON(), nullable=False),
        sa.Column("returns_table_json", sa.JSON(), nullable=False),
        sa.Column("manager_commentary", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source_label", sa.String(length=64), nullable=False, server_default="SECP FMR PDF"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fund_fmr_snapshots_report_month", "fund_fmr_snapshots", ["report_month"])

    # 3. Multi-period Annual & Quarterly 3-Statement financial records (v5)
    op.create_table(
        "equity_financial_periods",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_type", sa.String(length=16), nullable=False, server_default="ANNUAL"),
        sa.Column("period_label", sa.String(length=32), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("revenue_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("cost_of_sales_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("gross_profit_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("operating_profit_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("finance_cost_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("profit_before_tax_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("taxation_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("net_profit_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("eps", sa.Numeric(14, 4), nullable=True),
        sa.Column("dps", sa.Numeric(14, 4), nullable=True),
        sa.Column("total_assets_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("current_assets_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("cash_equivalents_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("inventory_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("receivables_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_liabilities_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("current_liabilities_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_debt_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("shareholders_equity_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("operating_cashflow_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("capex_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("free_cashflow_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("dividends_paid_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("auditor_opinion", sa.String(length=200), nullable=True),
        sa.Column("directors_highlights_json", sa.JSON(), nullable=False),
        sa.Column("dividend_bonus_note", sa.String(length=300), nullable=True),
        sa.Column("segment_notes_json", sa.JSON(), nullable=False),
        sa.Column("source_pdf_url", sa.String(length=500), nullable=True),
        sa.Column("source_label", sa.String(length=64), nullable=False, server_default="PUCARS PDF"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "period_type", "period_label", name="uq_eq_fin_period_asset_type_label"),
    )
    op.create_index("ix_equity_financial_periods_asset_id", "equity_financial_periods", ["asset_id"])
    op.create_index("ix_equity_financial_periods_period_end_date", "equity_financial_periods", ["period_end_date"])
    op.create_index("ix_equity_financial_periods_fiscal_year", "equity_financial_periods", ["fiscal_year"])
    op.create_index(
        "ix_eq_fin_period_lookup",
        "equity_financial_periods",
        ["asset_id", "period_type", "period_end_date"],
    )

    # 4. Pakistan & Global Financial News Hub (v6)
    op.create_table(
        "news_articles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("url_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=700), nullable=False),
        sa.Column("source_key", sa.String(length=48), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="Markets"),
        sa.Column("matched_symbols_json", sa.JSON(), nullable=False),
        sa.Column("matched_sectors_json", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_news_articles_url_hash", "news_articles", ["url_hash"])
    op.create_index("ix_news_articles_source_key", "news_articles", ["source_key"])
    op.create_index("ix_news_articles_category", "news_articles", ["category"])
    op.create_index("ix_news_articles_published_at", "news_articles", ["published_at"])
    op.create_index("ix_news_published_source", "news_articles", ["published_at", "source_key"])

    # 5. PSX Main & GEM Board IPOs (v6)
    op.create_table(
        "ipo_listings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False, unique=True),
        sa.Column("company_name", sa.String(length=220), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=False, server_default="General"),
        sa.Column("board", sa.String(length=24), nullable=False, server_default="MAIN"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="LISTED"),
        sa.Column("issue_type", sa.String(length=64), nullable=False, server_default="Book Building + Public Offer"),
        sa.Column("floor_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("strike_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("issue_size_mn", sa.Numeric(18, 2), nullable=True),
        sa.Column("subscription_multiple", sa.Numeric(10, 2), nullable=True),
        sa.Column("book_building_date", sa.Date(), nullable=True),
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("lead_manager", sa.String(length=180), nullable=True),
        sa.Column("prospectus_url", sa.String(length=600), nullable=True),
        sa.Column("source_url", sa.String(length=600), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_shariah_compliant", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ipo_listings_slug", "ipo_listings", ["slug"])
    op.create_index("ix_ipo_listings_symbol", "ipo_listings", ["symbol"])
    op.create_index("ix_ipo_listings_status", "ipo_listings", ["status"])
    op.create_index("ix_ipo_listings_listing_date", "ipo_listings", ["listing_date"])

    # 6. Pakistan 5-Brokerage Equity Research Reports + User Bookmarks (v8 / v1.0)
    op.create_table(
        "research_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("url_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("broker_key", sa.String(length=32), nullable=False),
        sa.Column("broker_name", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=700), nullable=False),
        sa.Column("pdf_url", sa.String(length=700), nullable=True),
        sa.Column("report_type", sa.String(length=40), nullable=False, server_default="COMPANY"),
        sa.Column("stance", sa.String(length=24), nullable=True),
        sa.Column("target_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("matched_symbols_json", sa.JSON(), nullable=False),
        sa.Column("matched_sectors_json", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_user_bookmark", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_reports_url_hash", "research_reports", ["url_hash"])
    op.create_index("ix_research_reports_broker_key", "research_reports", ["broker_key"])
    op.create_index("ix_research_reports_report_type", "research_reports", ["report_type"])
    op.create_index("ix_research_reports_published_at", "research_reports", ["published_at"])
    op.create_index("ix_research_reports_owner_user_id", "research_reports", ["owner_user_id"])
    op.create_index("ix_research_published_broker", "research_reports", ["published_at", "broker_key"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE user_preferences FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY rls_user_preferences_isolation ON user_preferences
            USING (
                COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')
            )
            WITH CHECK (
                COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')
            );
            """
        )
        op.execute("ALTER TABLE research_reports ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE research_reports FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY rls_research_reports_isolation ON research_reports
            USING (
                owner_user_id IS NULL
                OR COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')
            )
            WITH CHECK (
                COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR owner_user_id IS NULL
                OR owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')
            );
            """
        )


def downgrade() -> None:
    op.drop_index("ix_research_published_broker", table_name="research_reports")
    op.drop_index("ix_research_reports_owner_user_id", table_name="research_reports")
    op.drop_index("ix_research_reports_published_at", table_name="research_reports")
    op.drop_index("ix_research_reports_report_type", table_name="research_reports")
    op.drop_index("ix_research_reports_broker_key", table_name="research_reports")
    op.drop_index("ix_research_reports_url_hash", table_name="research_reports")
    op.drop_table("research_reports")

    op.drop_index("ix_ipo_listings_listing_date", table_name="ipo_listings")
    op.drop_index("ix_ipo_listings_status", table_name="ipo_listings")
    op.drop_index("ix_ipo_listings_symbol", table_name="ipo_listings")
    op.drop_index("ix_ipo_listings_slug", table_name="ipo_listings")
    op.drop_table("ipo_listings")

    op.drop_index("ix_news_published_source", table_name="news_articles")
    op.drop_index("ix_news_articles_published_at", table_name="news_articles")
    op.drop_index("ix_news_articles_category", table_name="news_articles")
    op.drop_index("ix_news_articles_source_key", table_name="news_articles")
    op.drop_index("ix_news_articles_url_hash", table_name="news_articles")
    op.drop_table("news_articles")

    op.drop_index("ix_eq_fin_period_lookup", table_name="equity_financial_periods")
    op.drop_index("ix_equity_financial_periods_fiscal_year", table_name="equity_financial_periods")
    op.drop_index("ix_equity_financial_periods_period_end_date", table_name="equity_financial_periods")
    op.drop_index("ix_equity_financial_periods_asset_id", table_name="equity_financial_periods")
    op.drop_table("equity_financial_periods")

    op.drop_index("ix_fund_fmr_snapshots_report_month", table_name="fund_fmr_snapshots")
    op.drop_table("fund_fmr_snapshots")

    op.drop_table("user_preferences")
