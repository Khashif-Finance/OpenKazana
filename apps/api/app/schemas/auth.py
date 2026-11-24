from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


FilerStatus = Literal["ACTIVE_FILER", "LATE_FILER", "NON_FILER"]
PreferredCurrency = Literal["PKR", "USD"]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    filer_status: FilerStatus = "ACTIVE_FILER"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleOAuthRequest(BaseModel):
    id_token: str | None = None
    code: str | None = None


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    preferred_currency: PreferredCurrency | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=128)


class DemoUpgradeRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class AuthConfigOut(BaseModel):
    auth_mode: str = "local"
    local_mode: bool = True
    allow_registration: bool = True
    google_client_id: str | None = None
    demo_ttl_hours: int


class UserPreferenceOut(BaseModel):
    shariah_only_default: bool = False
    default_market_view: str = "PSX"
    compact_tables: bool = False
    show_news_ticker: bool = True
    preferred_news_sources: str = "ALL"


class UserPreferenceUpdate(BaseModel):
    shariah_only_default: bool | None = None
    default_market_view: Literal["PSX", "ALL", "INTL"] | None = None
    compact_tables: bool | None = None
    show_news_ticker: bool | None = None
    preferred_news_sources: str | None = Field(default=None, max_length=400)


class TaxProfileOut(BaseModel):
    filer_status: str
    fiscal_year: str
    default_broker_rate_bps: int
    zakat_exempt: bool
    us_w8ben_active: bool = True
    foreign_cgt_rate_pct: Decimal = Decimal("15.00")


class TaxProfileUpdate(BaseModel):
    filer_status: FilerStatus | None = None
    fiscal_year: str | None = None
    default_broker_rate_bps: int | None = Field(default=None, ge=0, le=500)
    zakat_exempt: bool | None = None
    us_w8ben_active: bool | None = None
    foreign_cgt_rate_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("50"))


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_demo: bool
    is_admin: bool = False
    demo_expires_at: datetime | None = None
    preferred_currency: str
    telegram_chat_id: str | None = None
    created_at: datetime
    tax_profile: TaxProfileOut | None = None
    preferences: UserPreferenceOut | None = None


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


class WorkspaceImportRequest(BaseModel):
    profile: dict | None = None
    tax_profile: dict | None = None
    preferences: dict | None = None
    portfolios: list[dict] = Field(default_factory=list)
    watchlists: list[dict] = Field(default_factory=list)
    alert_rules: list[dict] = Field(default_factory=list)
