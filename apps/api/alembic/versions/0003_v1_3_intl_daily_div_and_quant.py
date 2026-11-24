"""v1.3 International Stocks, Daily Dividend Funds & Quant/Tax Profile Columns

Revision ID: 0003_v1_3_intl_daily_div
Revises: 0002_v1_2_hardening
Create Date: 2026-11-14 17:32:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_v1_3_intl_daily_div"
down_revision: Union[str, None] = "0002_v1_2_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("psx_profiles", sa.Column("exchange", sa.String(length=32), nullable=False, server_default="PSX"))
    op.add_column("psx_profiles", sa.Column("country", sa.String(length=16), nullable=False, server_default="PK"))
    op.add_column("psx_profiles", sa.Column("default_wht_rate_pct", sa.Numeric(8, 2), nullable=True))

    op.add_column(
        "mutual_fund_profiles",
        sa.Column("is_daily_dividend", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("mutual_fund_profiles", sa.Column("constant_par_nav", sa.Numeric(14, 4), nullable=True))
    op.add_column(
        "mutual_fund_profiles",
        sa.Column("annualized_yield_pct", sa.Numeric(10, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "mutual_fund_profiles",
        sa.Column("default_payout_mode", sa.String(length=24), nullable=False, server_default="REINVEST"),
    )
    op.create_index("ix_mutual_fund_profiles_is_daily_dividend", "mutual_fund_profiles", ["is_daily_dividend"])

    op.add_column("tax_profiles", sa.Column("us_w8ben_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column(
        "tax_profiles",
        sa.Column("foreign_cgt_rate_pct", sa.Numeric(6, 2), nullable=False, server_default="15.00"),
    )


def downgrade() -> None:
    op.drop_column("tax_profiles", "foreign_cgt_rate_pct")
    op.drop_column("tax_profiles", "us_w8ben_active")

    op.drop_index("ix_mutual_fund_profiles_is_daily_dividend", table_name="mutual_fund_profiles")
    op.drop_column("mutual_fund_profiles", "default_payout_mode")
    op.drop_column("mutual_fund_profiles", "annualized_yield_pct")
    op.drop_column("mutual_fund_profiles", "constant_par_nav")
    op.drop_column("mutual_fund_profiles", "is_daily_dividend")

    op.drop_column("psx_profiles", "default_wht_rate_pct")
    op.drop_column("psx_profiles", "country")
    op.drop_column("psx_profiles", "exchange")
