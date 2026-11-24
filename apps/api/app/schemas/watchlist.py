from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WatchlistOut(BaseModel):
    id: str
    name: str
    items: list[dict]


class AlertRuleCreate(BaseModel):
    asset_symbol: str = Field(min_length=1, max_length=32)
    rule_type: Literal["PRICE_ABOVE", "PRICE_BELOW", "ANNOUNCEMENT"]
    threshold_value: Decimal | None = None
    channels: list[Literal["in_app", "email", "telegram"]] = Field(default=["in_app"])


class AlertRuleUpdate(BaseModel):
    is_active: bool | None = None
    threshold_value: Decimal | None = None
    channels: list[Literal["in_app", "email", "telegram"]] | None = None


class AlertRuleOut(BaseModel):
    id: str
    asset_symbol: str
    asset_name: str
    rule_type: str
    threshold_value: Decimal | None = None
    channels: list[str]
    is_active: bool
    last_triggered_at: datetime | None = None
    created_at: datetime


class NotificationOut(BaseModel):
    id: str
    title: str
    body: str
    channel: str
    is_read: bool
    created_at: datetime
