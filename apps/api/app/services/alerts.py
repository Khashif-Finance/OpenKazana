from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.market import Announcement, Asset
from app.models.portfolio import AlertRule, Notification
from app.models.user import User

logger = logging.getLogger(__name__)


async def _send_email(to_email: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        return
    msg = EmailMessage()
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except Exception as exc:
        logger.warning("Failed sending alert email to %s: %s", to_email, exc)


async def _send_telegram(chat_id: str, text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as exc:
        logger.warning("Failed sending Telegram alert: %s", exc)


async def evaluate_alerts(db: AsyncSession) -> int:
    """Evaluate active price and PSX announcement alert rules and dispatch notifications."""
    now = datetime.now(timezone.utc)
    cooldown_cutoff = now - timedelta(hours=4)

    stmt = (
        select(AlertRule, Asset, User)
        .join(Asset, AlertRule.asset_id == Asset.id)
        .join(User, AlertRule.user_id == User.id)
        .where(AlertRule.is_active.is_(True))
    )
    rows = (await db.execute(stmt)).all()
    triggered_count = 0

    for rule, asset, user in rows:
        if rule.last_triggered_at and rule.last_triggered_at > cooldown_cutoff:
            continue

        title = ""
        body = ""

        if rule.rule_type == "PRICE_ABOVE" and rule.threshold_value is not None:
            if asset.last_price >= rule.threshold_value:
                title = f"{asset.symbol} crossed above {rule.threshold_value}"
                body = f"{asset.name} ({asset.symbol}) is now at {asset.currency} {asset.last_price}."

        elif rule.rule_type == "PRICE_BELOW" and rule.threshold_value is not None:
            if asset.last_price <= rule.threshold_value:
                title = f"{asset.symbol} dropped to/below {rule.threshold_value}"
                body = f"{asset.name} ({asset.symbol}) is now at {asset.currency} {asset.last_price}."

        elif rule.rule_type == "ANNOUNCEMENT":
            recent_stmt = (
                select(Announcement)
                .where(
                    Announcement.asset_id == asset.id,
                    Announcement.published_at >= now - timedelta(hours=6),
                )
                .order_by(Announcement.published_at.desc())
                .limit(1)
            )
            ann = (await db.execute(recent_stmt)).scalar_one_or_none()
            if ann is not None:
                title = f"PSX Announcement: {asset.symbol} — {ann.title}"
                body = ann.summary or ann.title

        if not title:
            continue

        channels = [c.strip() for c in rule.channels.split(",") if c.strip()]
        if "in_app" in channels:
            db.add(Notification(user_id=user.id, title=title, body=body, channel="in_app"))
        if "email" in channels:
            await _send_email(user.email, f"[OpenKhazana] {title}", body)
        if "telegram" in channels and user.telegram_chat_id:
            await _send_telegram(user.telegram_chat_id, f"OpenKhazana Alert\n{title}\n{body}")

        rule.last_triggered_at = now
        triggered_count += 1

    if triggered_count:
        await db.commit()
    return triggered_count
