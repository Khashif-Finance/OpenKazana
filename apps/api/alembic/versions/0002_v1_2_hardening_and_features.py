"""v1.2 Security Hardening, Ephemeral Demo TTL, Custom Assets & Tightened RLS

Revision ID: 0002_v1_2_hardening
Revises: 0001_initial_schema
Create Date: 2026-11-13 23:36:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_v1_2_hardening"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("demo_expires_at", sa.DateTime(timezone=True), nullable=True))

    if is_pg:
        op.add_column(
            "assets",
            sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        )
    else:
        with op.batch_alter_table("assets") as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
            batch_op.create_foreign_key("fk_assets_owner_user_id", "users", ["owner_user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_assets_owner_user_id", "assets", ["owner_user_id"])

    if is_pg:
        # Tighten RLS policies so unconfigured connections fail closed ('off') instead of open ('on')
        for tbl in ["tax_profiles", "portfolios", "watchlists", "alert_rules", "notifications"]:
            op.execute(f"DROP POLICY IF EXISTS rls_{tbl}_isolation ON {tbl};")
            op.execute(
                f"""
                CREATE POLICY rls_{tbl}_isolation ON {tbl}
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

        for child_tbl in ["transactions", "open_tax_lots", "portfolio_daily_snapshots"]:
            op.execute(f"DROP POLICY IF EXISTS rls_{child_tbl}_isolation ON {child_tbl};")
            op.execute(
                f"""
                CREATE POLICY rls_{child_tbl}_isolation ON {child_tbl}
                USING (
                    COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                    OR EXISTS (
                        SELECT 1 FROM portfolios p
                        WHERE p.id = {child_tbl}.portfolio_id
                          AND p.user_id = NULLIF(current_setting('app.current_user_id', true), '')
                    )
                )
                WITH CHECK (
                    COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                    OR EXISTS (
                        SELECT 1 FROM portfolios p
                        WHERE p.id = {child_tbl}.portfolio_id
                          AND p.user_id = NULLIF(current_setting('app.current_user_id', true), '')
                    )
                );
                """
            )

        op.execute("ALTER TABLE assets ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE assets FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY rls_assets_isolation ON assets
            USING (
                owner_user_id IS NULL
                OR COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')
            )
            WITH CHECK (
                COALESCE(current_setting('app.rls_bypass', true), 'off') = 'on'
                OR (
                    owner_user_id IS NOT NULL
                    AND owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')
                )
            );
            """
        )

        # Ensure additional price_bars partitions exist safely if default partition has no conflicts
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'price_bars_2028') THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM price_bars_default
                        WHERE bar_time >= '2028-01-01 00:00:00+00' AND bar_time < '2030-01-01 00:00:00+00'
                    ) THEN
                        CREATE TABLE price_bars_2028 PARTITION OF price_bars
                            FOR VALUES FROM ('2028-01-01 00:00:00+00') TO ('2029-01-01 00:00:00+00');
                        CREATE TABLE price_bars_2029 PARTITION OF price_bars
                            FOR VALUES FROM ('2029-01-01 00:00:00+00') TO ('2030-01-01 00:00:00+00');
                    END IF;
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS rls_assets_isolation ON assets;")
        op.execute("ALTER TABLE assets NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE assets DISABLE ROW LEVEL SECURITY;")

    op.drop_index("ix_assets_owner_user_id", table_name="assets")
    op.drop_column("assets", "owner_user_id")
    op.drop_column("users", "demo_expires_at")
    op.drop_column("users", "is_admin")
