"""
OpenKhazana v1.0 SaaS & Self-Hosted Administration API (`/api/v1/admin/...`).
All endpoints require an Admin session (`is_admin=True`, automatic for Local Owner in `AUTH_MODE=local`)
or a matching `X-Admin-Key` header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_or_api_key
from app.core.config import (
    get_settings,
    is_public_registration_allowed,
    set_public_registration_allowed,
)
from app.core.database import apply_rls_context, get_db
from app.models.market import (
    Asset,
    EquityFinancialPeriod,
    FundFmrSnapshot,
    IpoListing,
    NewsArticle,
    PriceBar,
    ResearchReport,
    ScraperRun,
)
from app.models.portfolio import Portfolio, Transaction
from app.models.user import User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class AdminSettingsPatch(BaseModel):
    allow_public_registration: bool


class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


@router.get("/overview")
async def admin_instance_overview(
    _admin: User | None = Depends(require_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Return DB health, live table row counts, SaaS auth toggles, and registered user directory."""
    settings = get_settings()
    await apply_rls_context(db, bypass=True)

    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    counts = {
        "users": int((await db.execute(select(func.count(User.id)))).scalar_one() or 0),
        "portfolios": int((await db.execute(select(func.count(Portfolio.id)))).scalar_one() or 0),
        "transactions": int((await db.execute(select(func.count(Transaction.id)))).scalar_one() or 0),
        "assets": int((await db.execute(select(func.count(Asset.id)))).scalar_one() or 0),
        "price_bars": int((await db.execute(select(func.count(PriceBar.id)))).scalar_one() or 0),
        "news_articles": int((await db.execute(select(func.count(NewsArticle.id)))).scalar_one() or 0),
        "research_reports": int((await db.execute(select(func.count(ResearchReport.id)))).scalar_one() or 0),
        "fund_fmr_snapshots": int((await db.execute(select(func.count(FundFmrSnapshot.id)))).scalar_one() or 0),
        "equity_financial_periods": int((await db.execute(select(func.count(EquityFinancialPeriod.id)))).scalar_one() or 0),
        "ipo_listings": int((await db.execute(select(func.count(IpoListing.id)))).scalar_one() or 0),
    }

    # Portfolio count per user
    port_counts_rows = (
        await db.execute(select(Portfolio.user_id, func.count(Portfolio.id)).group_by(Portfolio.user_id))
    ).all()
    port_count_map = {str(uid): int(cnt) for uid, cnt in port_counts_rows}

    users = (
        await db.execute(select(User).order_by(User.created_at.desc()).limit(50))
    ).scalars().all()

    failed_runs = int(
        (await db.execute(select(func.count(ScraperRun.id)).where(ScraperRun.status == "FAILED"))).scalar_one() or 0
    )

    return {
        "version": settings.app_version,
        "environment": settings.environment,
        "auth_mode": settings.effective_auth_mode,
        "database_connected": db_ok,
        "database_dialect": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "allow_public_registration": is_public_registration_allowed(),
        "rate_limit_enabled": settings.rate_limit_enabled,
        "scrapers_enabled": settings.scrapers_enabled,
        "failed_scraper_runs": failed_runs,
        "table_counts": counts,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "is_admin": bool(u.is_admin),
                "is_active": bool(u.is_active),
                "is_demo": bool(u.is_demo),
                "preferred_currency": u.preferred_currency,
                "portfolio_count": port_count_map.get(u.id, 0),
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ],
    }


@router.patch("/settings")
async def update_admin_settings(
    payload: AdminSettingsPatch,
    _admin: User | None = Depends(require_admin_or_api_key),
):
    """Toggle public user registration at runtime without restarting the server."""
    set_public_registration_allowed(payload.allow_public_registration)
    return {
        "ok": True,
        "allow_public_registration": is_public_registration_allowed(),
    }


@router.patch("/users/{user_id}")
async def update_admin_user(
    user_id: str,
    payload: AdminUserPatch,
    _admin: User | None = Depends(require_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Enable/disable a user account or grant/revoke Admin role (protects last active admin)."""
    await apply_rls_context(db, bypass=True)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    active_admins = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True))
            )
        ).scalar_one()
        or 0
    )

    if target.is_admin and target.is_active and active_admins <= 1:
        if payload.is_active is False or payload.is_admin is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot disable or demote the last active administrator.",
            )

    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.is_admin is not None:
        target.is_admin = payload.is_admin

    await db.commit()
    await db.refresh(target)
    return {
        "id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "is_admin": bool(target.is_admin),
        "is_active": bool(target.is_active),
    }
