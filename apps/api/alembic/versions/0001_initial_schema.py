"""Initial OpenKhazana schema (Partitioned price_bars, BRIN, RLS, Materialized Lots & TWRR)

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-13 23:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("oauth_provider", sa.String(length=40), nullable=True),
        sa.Column("oauth_subject", sa.String(length=255), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("preferred_currency", sa.String(length=8), nullable=False, server_default="PKR"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "tax_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filer_status", sa.String(length=16), nullable=False, server_default="ACTIVE_FILER"),
        sa.Column("fiscal_year", sa.String(length=16), nullable=False, server_default="FY2025-26"),
        sa.Column("default_broker_rate_bps", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("zakat_exempt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_tax_profiles_user_id"),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("asset_class", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="PKR"),
        sa.Column("sector_or_category", sa.String(length=120), nullable=False, server_default="General"),
        sa.Column("is_shariah_compliant", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_price", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("prev_close", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("change_pct", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("volume", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="seed"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", name="uq_assets_symbol"),
    )
    op.create_index("ix_assets_symbol", "assets", ["symbol"])
    op.create_index("ix_assets_asset_class", "assets", ["asset_class"])
    op.create_index("ix_assets_is_shariah_compliant", "assets", ["is_shariah_compliant"])

    op.create_table(
        "asset_symbol_aliases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_symbol", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.UniqueConstraint("old_symbol", name="uq_asset_symbol_aliases_old_symbol"),
    )
    op.create_index("ix_asset_symbol_aliases_asset_id", "asset_symbol_aliases", ["asset_id"])
    op.create_index("ix_asset_symbol_aliases_old_symbol", "asset_symbol_aliases", ["old_symbol"])

    op.create_table(
        "psx_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("market_cap_mn", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("free_float_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("pe_ratio", sa.Numeric(12, 2), nullable=True),
        sa.Column("pb_ratio", sa.Numeric(12, 2), nullable=True),
        sa.Column("eps", sa.Numeric(12, 2), nullable=True),
        sa.Column("roe_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("dividend_yield_pct", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("high_52w", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("low_52w", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("is_kse100", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_kmi30", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("financial_summary_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("asset_id", name="uq_psx_profiles_asset_id"),
    )

    op.create_table(
        "mutual_fund_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amc_name", sa.String(length=160), nullable=False),
        sa.Column("fund_category", sa.String(length=120), nullable=False),
        sa.Column("risk_profile", sa.String(length=40), nullable=False, server_default="Medium"),
        sa.Column("ter_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("management_fee_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("front_end_load_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("back_end_load_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("aum_mn", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("adjusted_nav_multiplier", sa.Numeric(12, 6), nullable=False, server_default="1.000000"),
        sa.Column("last_distribution_pkr", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("return_1m_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_6m_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_1y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("return_3y_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("benchmark_name", sa.String(length=120), nullable=False, server_default="KSE-100"),
        sa.UniqueConstraint("asset_id", name="uq_mutual_fund_profiles_asset_id"),
    )
    op.create_index("ix_mutual_fund_profiles_amc_name", "mutual_fund_profiles", ["amc_name"])
    op.create_index("ix_mutual_fund_profiles_fund_category", "mutual_fund_profiles", ["fund_category"])

    op.create_table(
        "fixed_income_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_type", sa.String(length=40), nullable=False),
        sa.Column("annual_profit_rate_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("payout_frequency", sa.String(length=24), nullable=False, server_default="MONTHLY"),
        sa.Column("is_wht_exempt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.UniqueConstraint("asset_id", name="uq_fixed_income_profiles_asset_id"),
    )

    op.create_table(
        "shariah_metrics",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fiscal_period", sa.String(length=32), nullable=False, server_default="FY2025"),
        sa.Column("core_business_compliant", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_kmi_all_share", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("debt_to_assets_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("non_compliant_inv_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("non_compliant_income_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("purification_rate_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("illiquid_assets_pct", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("asset_id", name="uq_shariah_metrics_asset_id"),
    )

    price_bars_kwargs = {"postgresql_partition_by": "RANGE (bar_time)"} if is_pg else {}
    op.create_table(
        "price_bars",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False, server_default="1D"),
        sa.Column("open", sa.Numeric(20, 4), nullable=False),
        sa.Column("high", sa.Numeric(20, 4), nullable=False),
        sa.Column("low", sa.Numeric(20, 4), nullable=False),
        sa.Column("close", sa.Numeric(20, 4), nullable=False),
        sa.Column("volume", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", "bar_time", name="pk_price_bars"),
        sa.UniqueConstraint("asset_id", "timeframe", "bar_time", name="uq_price_bar_asset_tf_time"),
        **price_bars_kwargs,
    )
    op.create_index("ix_price_bar_lookup", "price_bars", ["asset_id", "timeframe", "bar_time"])

    if is_pg:
        op.execute(
            """
            CREATE TABLE price_bars_2025 PARTITION OF price_bars
                FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');
            CREATE TABLE price_bars_2026 PARTITION OF price_bars
                FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
            CREATE TABLE price_bars_2027 PARTITION OF price_bars
                FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2028-01-01 00:00:00+00');
            CREATE TABLE price_bars_default PARTITION OF price_bars DEFAULT;
            CREATE INDEX ix_price_bars_brin_time ON price_bars USING brin (bar_time);
            """
        )

    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(length=24), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("payout_per_share", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("ratio_numerator", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("ratio_denominator", sa.Numeric(12, 4), nullable=False, server_default="100"),
        sa.Column("right_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.UniqueConstraint("asset_id", "action_type", "ex_date", name="uq_corp_action_asset_type_exdate"),
    )
    op.create_index("ix_corporate_actions_asset_id", "corporate_actions", ["asset_id"])
    op.create_index("ix_corporate_actions_ex_date", "corporate_actions", ["ex_date"])

    op.create_table(
        "announcements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="Notice"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_announcements_asset_id", "announcements", ["asset_id"])
    op.create_index("ix_announcements_published_at", "announcements", ["published_at"])

    op.create_table(
        "scraper_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scraper_name", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="RUNNING"),
        sa.Column("records_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("dlq_path", sa.String(length=500), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_scraper_runs_scraper_name", "scraper_runs", ["scraper_name"])

    op.create_table(
        "portfolios",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_currency", sa.String(length=8), nullable=False, server_default="PKR"),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("auto_manage_cash", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("portfolio_id", sa.String(length=36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tx_type", sa.String(length=24), nullable=False),
        sa.Column("tx_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("gross_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("fee_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("tax_withheld", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("deemed_ex_price", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("shariah_purification_pkr", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("fx_rate_to_pkr", sa.Numeric(16, 4), nullable=False, server_default="1"),
        sa.Column("corp_action_id", sa.String(length=36), sa.ForeignKey("corporate_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transactions_portfolio_id", "transactions", ["portfolio_id"])
    op.create_index("ix_transactions_asset_id", "transactions", ["asset_id"])
    op.create_index("ix_transactions_tx_date", "transactions", ["tx_date"])

    op.create_table(
        "open_tax_lots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("portfolio_id", sa.String(length=36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("acq_date", sa.Date(), nullable=False),
        sa.Column("quantity_remaining", sa.Numeric(20, 8), nullable=False),
        sa.Column("cost_basis_pkr", sa.Numeric(20, 4), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_open_tax_lots_portfolio_id", "open_tax_lots", ["portfolio_id"])
    op.create_index("ix_open_tax_lots_asset_id", "open_tax_lots", ["asset_id"])

    op.create_table(
        "portfolio_daily_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("portfolio_id", sa.String(length=36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_value_pkr", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_cost_basis_pkr", sa.Numeric(20, 4), nullable=False),
        sa.Column("net_invested_pkr", sa.Numeric(20, 4), nullable=False),
        sa.Column("virtual_units", sa.Numeric(24, 8), nullable=False),
        sa.Column("nav_per_unit", sa.Numeric(16, 4), nullable=False),
        sa.UniqueConstraint("portfolio_id", "snapshot_date", name="uq_portfolio_snapshot_date"),
    )
    op.create_index("ix_portfolio_daily_snapshots_portfolio_id", "portfolio_daily_snapshots", ["portfolio_id"])
    op.create_index("ix_portfolio_daily_snapshots_snapshot_date", "portfolio_daily_snapshots", ["snapshot_date"])

    op.create_table(
        "portfolio_share_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("portfolio_id", sa.String(length=36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("hide_amounts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_portfolio_share_links_token"),
    )
    op.create_index("ix_portfolio_share_links_portfolio_id", "portfolio_share_links", ["portfolio_id"])
    op.create_index("ix_portfolio_share_links_token", "portfolio_share_links", ["token"])

    op.create_table(
        "watchlists",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("watchlist_id", sa.String(length=36), sa.ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("watchlist_id", "asset_id", name="uq_watchlist_asset"),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("threshold_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("channels", sa.String(length=64), nullable=False, server_default="in_app"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_rules_user_id", "alert_rules", ["user_id"])
    op.create_index("ix_alert_rules_asset_id", "alert_rules", ["asset_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False, server_default="in_app"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    if is_pg:
        # Row-Level Security enforcement for multi-tenant isolation
        for tbl in ["tax_profiles", "portfolios", "watchlists", "alert_rules", "notifications"]:
            op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY rls_{tbl}_isolation ON {tbl}
                USING (
                    COALESCE(current_setting('app.rls_bypass', true), 'on') = 'on'
                    OR user_id = current_setting('app.current_user_id', true)
                )
                WITH CHECK (
                    COALESCE(current_setting('app.rls_bypass', true), 'on') = 'on'
                    OR user_id = current_setting('app.current_user_id', true)
                );
                """
            )

        for child_tbl in ["transactions", "open_tax_lots", "portfolio_daily_snapshots"]:
            op.execute(f"ALTER TABLE {child_tbl} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {child_tbl} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY rls_{child_tbl}_isolation ON {child_tbl}
                USING (
                    COALESCE(current_setting('app.rls_bypass', true), 'on') = 'on'
                    OR EXISTS (
                        SELECT 1 FROM portfolios p
                        WHERE p.id = {child_tbl}.portfolio_id
                          AND p.user_id = current_setting('app.current_user_id', true)
                    )
                )
                WITH CHECK (
                    COALESCE(current_setting('app.rls_bypass', true), 'on') = 'on'
                    OR EXISTS (
                        SELECT 1 FROM portfolios p
                        WHERE p.id = {child_tbl}.portfolio_id
                          AND p.user_id = current_setting('app.current_user_id', true)
                    )
                );
                """
            )


def downgrade() -> None:
    for table in [
        "notifications",
        "alert_rules",
        "watchlist_items",
        "watchlists",
        "portfolio_share_links",
        "portfolio_daily_snapshots",
        "open_tax_lots",
        "transactions",
        "portfolios",
        "scraper_runs",
        "announcements",
        "corporate_actions",
        "price_bars",
        "shariah_metrics",
        "fixed_income_profiles",
        "mutual_fund_profiles",
        "psx_profiles",
        "asset_symbol_aliases",
        "assets",
        "tax_profiles",
        "users",
    ]:
        op.drop_table(table)
