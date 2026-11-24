from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.market import Asset
    from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Portfolio(Base):
    """User portfolio container supporting multi-asset holdings and optional auto-managed PKR cash."""

    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(8), default="PKR", nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    auto_manage_cash: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="portfolios")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="Transaction.tx_date.asc(), Transaction.created_at.asc()",
    )
    open_tax_lots: Mapped[list["OpenTaxLot"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )
    daily_snapshots: Mapped[list["PortfolioDailySnapshot"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PortfolioDailySnapshot.snapshot_date.asc()",
    )
    share_links: Mapped[list["PortfolioShareLink"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
    )


class Transaction(Base):
    """Immutable-style ledger event driving same-day netting, FIFO lots, 236Z bonus basis, DRIP, and XIRR."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="RESTRICT"), index=True, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(24), nullable=False)
    tx_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    tax_withheld: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    deemed_ex_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    shariah_purification_pkr: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    fx_rate_to_pkr: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=Decimal("1"), nullable=False)
    corp_action_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("corporate_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="transactions")
    asset: Mapped["Asset"] = relationship()


class OpenTaxLot(Base):
    """Materialized remaining open tax lots per portfolio/asset, rebuilt on ledger mutation."""

    __tablename__ = "open_tax_lots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False)
    acq_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_basis_pkr: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="open_tax_lots")
    asset: Mapped["Asset"] = relationship()


class PortfolioDailySnapshot(Base):
    """Materialized daily valuation and unitized NAV (TWRR) time series per portfolio."""

    __tablename__ = "portfolio_daily_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "snapshot_date", name="uq_portfolio_snapshot_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    total_value_pkr: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    total_cost_basis_pkr: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    net_invested_pkr: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    virtual_units: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    nav_per_unit: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="daily_snapshots")


class PortfolioShareLink(Base):
    """Opt-in read-only shareable snapshot link for a portfolio."""

    __tablename__ = "portfolio_share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    hide_amounts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="share_links")


class Watchlist(Base):
    """User-curated watchlist of PSX equities, mutual funds, or FX/Gold/Crypto."""

    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="watchlists")
    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
    )


class WatchlistItem(Base):
    """Asset entry in a user's watchlist."""

    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "asset_id", name="uq_watchlist_asset"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    watchlist_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    watchlist: Mapped["Watchlist"] = relationship(back_populates="items")
    asset: Mapped["Asset"] = relationship()


class AlertRule(Base):
    """User-defined price or PSX announcement alert rule."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)  # PRICE_ABOVE | PRICE_BELOW | ANNOUNCEMENT
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    channels: Mapped[str] = mapped_column(String(64), default="in_app", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="alert_rules")
    asset: Mapped["Asset"] = relationship()


class Notification(Base):
    """In-app notification inbox record."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="in_app", nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="notifications")
