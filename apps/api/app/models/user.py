from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.portfolio import AlertRule, Notification, Portfolio, Watchlist


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Registered user or guest demo account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Investor")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    demo_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_currency: Mapped[str] = mapped_column(String(8), default="PKR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tax_profile: Mapped["TaxProfile | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    portfolios: Mapped[list["Portfolio"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    watchlists: Mapped[list["Watchlist"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    alert_rules: Mapped[list["AlertRule"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    preferences: Mapped["UserPreference | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class UserPreference(Base):
    """Workspace display & research preferences per user (Shariah default, compact mode, news sources)."""

    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    shariah_only_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_market_view: Mapped[str] = mapped_column(String(24), default="PSX", nullable=False)
    compact_tables: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_news_ticker: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferred_news_sources: Mapped[str] = mapped_column(String(400), default="ALL", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="preferences")


class TaxProfile(Base):
    """Pakistan-specific tax & brokerage configuration per user."""

    __tablename__ = "tax_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    filer_status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE_FILER", nullable=False
    )  # ACTIVE_FILER | LATE_FILER | NON_FILER
    fiscal_year: Mapped[str] = mapped_column(String(16), default="FY2025-26", nullable=False)
    default_broker_rate_bps: Mapped[int] = mapped_column(Integer, default=15, nullable=False)  # 0.15%
    zakat_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    us_w8ben_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    foreign_cgt_rate_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("15.00"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="tax_profile")
