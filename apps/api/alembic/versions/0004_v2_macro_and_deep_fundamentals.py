"""v2.0 Macro Indicators and Deep Equity Financial Snapshots

Revision ID: 0004_v2_macro_and_deep_fundamentals
Revises: 0003_v1_3_intl_daily_div
Create Date: 2026-11-15 20:15:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_v2_macro_and_deep_fundamentals"
down_revision: Union[str, None] = "0003_v1_3_intl_daily_div"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "macro_indicators",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False, server_default="PK"),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="PKRV"),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False, server_default="%"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False, server_default="sbp"),
        sa.Column("history_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_macro_indicators_code"),
    )
    op.create_index("ix_macro_indicators_code", "macro_indicators", ["code"])
    op.create_index("ix_macro_indicators_country", "macro_indicators", ["country"])
    op.create_index("ix_macro_indicators_category", "macro_indicators", ["category"])

    op.create_table(
        "equity_financial_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("fiscal_period", sa.String(length=32), nullable=False, server_default="TTM"),
        sa.Column("bvps", sa.Numeric(14, 4), nullable=True),
        sa.Column("total_assets_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_debt_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("cash_equivalents_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("shareholders_equity_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("operating_cashflow_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("capex_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("free_cashflow_mn", sa.Numeric(20, 2), nullable=True),
        sa.Column("current_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(10, 4), nullable=True),
        sa.Column("roe_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("roa_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("roic_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("operating_margin_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("payout_ratio_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("analyst_target_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("analyst_rating", sa.String(length=32), nullable=True),
        sa.Column("piotroski_f_score", sa.Integer(), nullable=True),
        sa.Column("altman_z_score", sa.Numeric(10, 2), nullable=True),
        sa.Column("dcf_intrinsic_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False, server_default="extracted"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("equity_financial_snapshots")
    op.drop_index("ix_macro_indicators_category", table_name="macro_indicators")
    op.drop_index("ix_macro_indicators_country", table_name="macro_indicators")
    op.drop_index("ix_macro_indicators_code", table_name="macro_indicators")
    op.drop_table("macro_indicators")
