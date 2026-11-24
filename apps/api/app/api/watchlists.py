from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, resolve_asset_by_symbol
from app.core.database import get_db
from app.models.portfolio import AlertRule, Notification, Watchlist, WatchlistItem
from app.models.user import User
from app.schemas.watchlist import (
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
    NotificationOut,
    WatchlistCreate,
    WatchlistOut,
)

router = APIRouter(tags=["watchlists-alerts"])


@router.get("/api/v1/watchlists", response_model=list[WatchlistOut])
async def list_watchlists(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Watchlist)
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.asset))
        .where(Watchlist.user_id == user.id)
        .order_by(Watchlist.created_at.asc())
    )
    wls = (await db.execute(stmt)).scalars().all()
    return [
        WatchlistOut(
            id=w.id,
            name=w.name,
            items=[
                {
                    "asset_id": item.asset.id,
                    "symbol": item.asset.symbol,
                    "name": item.asset.name,
                    "asset_class": item.asset.asset_class,
                    "currency": item.asset.currency,
                    "last_price": item.asset.last_price,
                    "change_pct": item.asset.change_pct,
                    "is_shariah_compliant": item.asset.is_shariah_compliant,
                }
                for item in w.items
            ],
        )
        for w in wls
    ]


@router.post("/api/v1/watchlists", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    payload: WatchlistCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wl = Watchlist(user_id=user.id, name=payload.name.strip())
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return WatchlistOut(id=wl.id, name=wl.name, items=[])


@router.delete("/api/v1/watchlists/{watchlist_id}")
async def delete_watchlist(
    watchlist_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wl = (
        await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user.id))
    ).scalar_one_or_none()
    if wl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found.")
    await db.delete(wl)
    await db.commit()
    return {"ok": True}


@router.post("/api/v1/watchlists/{watchlist_id}/items/{symbol}")
async def add_watchlist_item(
    watchlist_id: str,
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wl = (
        await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user.id))
    ).scalar_one_or_none()
    if wl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found.")
    asset = await resolve_asset_by_symbol(symbol, db, owner_user_id=user.id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    existing = (
        await db.execute(
            select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.asset_id == asset.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(WatchlistItem(watchlist_id=wl.id, asset_id=asset.id))
        await db.commit()
    return {"ok": True}


@router.delete("/api/v1/watchlists/{watchlist_id}/items/{symbol}")
async def remove_watchlist_item(
    watchlist_id: str,
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wl = (
        await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user.id))
    ).scalar_one_or_none()
    if wl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found.")
    asset = await resolve_asset_by_symbol(symbol, db, owner_user_id=user.id)
    if asset:
        item = (
            await db.execute(
                select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.asset_id == asset.id)
            )
        ).scalar_one_or_none()
        if item:
            await db.delete(item)
            await db.commit()
    return {"ok": True}


@router.get("/api/v1/alerts", response_model=list[AlertRuleOut])
async def list_alerts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = (
        select(AlertRule)
        .options(selectinload(AlertRule.asset))
        .where(AlertRule.user_id == user.id)
        .order_by(desc(AlertRule.created_at))
    )
    rules = (await db.execute(stmt)).scalars().all()
    return [
        AlertRuleOut(
            id=r.id,
            asset_symbol=r.asset.symbol,
            asset_name=r.asset.name,
            rule_type=r.rule_type,
            threshold_value=r.threshold_value,
            channels=[c.strip() for c in r.channels.split(",") if c.strip()],
            is_active=r.is_active,
            last_triggered_at=r.last_triggered_at,
            created_at=r.created_at,
        )
        for r in rules
    ]


@router.post("/api/v1/alerts", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertRuleCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await resolve_asset_by_symbol(payload.asset_symbol, db, owner_user_id=user.id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    if payload.rule_type in {"PRICE_ABOVE", "PRICE_BELOW"} and payload.threshold_value is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Price threshold is required.")

    rule = AlertRule(
        user_id=user.id,
        asset_id=asset.id,
        rule_type=payload.rule_type,
        threshold_value=payload.threshold_value,
        channels=",".join(payload.channels),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return AlertRuleOut(
        id=rule.id,
        asset_symbol=asset.symbol,
        asset_name=asset.name,
        rule_type=rule.rule_type,
        threshold_value=rule.threshold_value,
        channels=payload.channels,
        is_active=rule.is_active,
        last_triggered_at=rule.last_triggered_at,
        created_at=rule.created_at,
    )


@router.patch("/api/v1/alerts/{alert_id}", response_model=AlertRuleOut)
async def update_alert(
    alert_id: str,
    payload: AlertRuleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AlertRule)
        .options(selectinload(AlertRule.asset))
        .where(AlertRule.id == alert_id, AlertRule.user_id == user.id)
    )
    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found.")

    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.threshold_value is not None:
        rule.threshold_value = payload.threshold_value
    if payload.channels is not None and payload.channels:
        rule.channels = ",".join(payload.channels)

    await db.commit()
    await db.refresh(rule)
    return AlertRuleOut(
        id=rule.id,
        asset_symbol=rule.asset.symbol,
        asset_name=rule.asset.name,
        rule_type=rule.rule_type,
        threshold_value=rule.threshold_value,
        channels=[c.strip() for c in rule.channels.split(",") if c.strip()],
        is_active=rule.is_active,
        last_triggered_at=rule.last_triggered_at,
        created_at=rule.created_at,
    )


@router.delete("/api/v1/alerts/{alert_id}")
async def delete_alert(
    alert_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = (
        await db.execute(select(AlertRule).where(AlertRule.id == alert_id, AlertRule.user_id == user.id))
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found.")
    await db.delete(rule)
    await db.commit()
    return {"ok": True}


@router.get("/api/v1/notifications", response_model=list[NotificationOut])
async def list_notifications(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(desc(Notification.created_at))
        .limit(40)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        NotificationOut(
            id=n.id,
            title=n.title,
            body=n.body,
            channel=n.channel,
            is_read=n.is_read,
            created_at=n.created_at,
        )
        for n in rows
    ]


@router.post("/api/v1/notifications/read-all")
async def mark_all_notifications_read(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notif = (
        await db.execute(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    ).scalar_one_or_none()
    if notif:
        notif.is_read = True
        await db.commit()
    return {"ok": True}
