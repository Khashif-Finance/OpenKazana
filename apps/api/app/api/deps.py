from __future__ import annotations

import secrets
from decimal import Decimal

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import apply_rls_context, get_db
from app.core.security import decode_access_token
from app.models.market import Asset, AssetSymbolAlias
from app.models.portfolio import Portfolio, Watchlist
from app.models.user import TaxProfile, User, UserPreference

LOCAL_OWNER_EMAIL = "local@openkhazana.local"


def _extract_token(authorization: str | None, cookie_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return cookie_token


async def get_or_create_local_owner(db: AsyncSession) -> User:
    """
    Auto-provision and return the single local self-hosted owner workspace when AUTH_MODE=local.
    Ensures TaxProfile, UserPreference, default Portfolio, and default Watchlist exist out of the box.
    """
    await apply_rls_context(db, bypass=True)
    stmt = (
        select(User)
        .options(selectinload(User.tax_profile), selectinload(User.preferences))
        .where(User.email == LOCAL_OWNER_EMAIL)
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        user = User(
            email=LOCAL_OWNER_EMAIL,
            full_name="Local Workspace",
            is_demo=False,
            is_admin=True,
            is_active=True,
            preferred_currency="PKR",
        )
        db.add(user)
        await db.flush()
        db.add(TaxProfile(user_id=user.id, filer_status="ACTIVE_FILER"))
        db.add(UserPreference(user_id=user.id))
        db.add(
            Portfolio(
                user_id=user.id,
                name="Main Portfolio",
                base_currency="PKR",
                auto_manage_cash=True,
                is_default=True,
            )
        )
        db.add(Watchlist(user_id=user.id, name="Core PSX Watchlist"))
        await db.commit()
        user = (await db.execute(stmt)).scalar_one()
    else:
        changed = False
        if user.tax_profile is None:
            db.add(TaxProfile(user_id=user.id, filer_status="ACTIVE_FILER"))
            changed = True
        if user.preferences is None:
            db.add(UserPreference(user_id=user.id))
            changed = True
        if not user.is_admin:
            user.is_admin = True
            changed = True
        if changed:
            await db.commit()
            user = (await db.execute(stmt)).scalar_one()

    await apply_rls_context(db, user_id=user.id, bypass=False)
    return user


async def get_optional_user(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    okz_session: str | None = Cookie(default=None),
) -> User | None:
    token = _extract_token(authorization, okz_session)
    if token:
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            stmt = (
                select(User)
                .options(selectinload(User.tax_profile), selectinload(User.preferences))
                .where(User.id == payload["sub"], User.is_active.is_(True))
            )
            user = (await db.execute(stmt)).scalar_one_or_none()
            if user is not None:
                await apply_rls_context(db, user_id=user.id, bypass=False)
                return user

    if get_settings().is_local_auth_mode:
        return await get_or_create_local_owner(db)
    return None


async def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in or create an account.",
        )
    return user


async def require_admin_or_api_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    user: User | None = Depends(get_optional_user),
) -> User | None:
    """Require either an authenticated admin user (`is_admin=True`), local workspace owner, or matching `X-Admin-Key` header."""
    settings = get_settings()
    if settings.admin_api_key and x_admin_key and secrets.compare_digest(x_admin_key, settings.admin_api_key):
        return user
    if user is not None and user.is_admin:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin privileges or valid X-Admin-Key header required.",
    )


async def get_usd_to_pkr_rate(db: AsyncSession) -> Decimal:
    """Prefer Open Market USD/PKR rate for retail multi-asset valuation with safe fallbacks."""
    stmt = (
        select(Asset.last_price)
        .where(
            Asset.owner_user_id.is_(None),
            Asset.symbol.in_(["USD_PKR_OPEN", "USD_PKR_INTERBANK"]),
        )
        .order_by(Asset.symbol.desc())
    )
    rate = (await db.execute(stmt)).scalars().first()
    if rate is not None and rate > 0:
        return rate
    return Decimal("280.40")


async def resolve_asset_by_symbol(
    symbol: str,
    db: AsyncSession,
    options: list | None = None,
    owner_user_id: str | None = None,
) -> Asset | None:
    """Resolve canonical Asset (public or owned by user) by symbol OR historical PSX alias."""
    sym = symbol.strip().upper()
    owner_filter = (
        (Asset.owner_user_id == owner_user_id) | (Asset.owner_user_id.is_(None))
        if owner_user_id
        else Asset.owner_user_id.is_(None)
    )
    stmt = select(Asset).where(Asset.symbol == sym, owner_filter)
    if options:
        stmt = stmt.options(*options)
    matches = (await db.execute(stmt)).scalars().all()
    if matches:
        if owner_user_id:
            for item in matches:
                if item.owner_user_id == owner_user_id:
                    return item
        return matches[0]

    alias_stmt = select(AssetSymbolAlias.asset_id).where(AssetSymbolAlias.old_symbol == sym)
    aliased_id = (await db.execute(alias_stmt)).scalar_one_or_none()
    if aliased_id is None:
        return None

    stmt2 = select(Asset).where(Asset.id == aliased_id, owner_filter)
    if options:
        stmt2 = stmt2.options(*options)
    return (await db.execute(stmt2)).scalar_one_or_none()


def set_auth_cookie_settings() -> dict:
    settings = get_settings()
    return {
        "key": settings.cookie_name,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
        "max_age": settings.access_token_expire_minutes * 60,
        "path": "/",
    }
