from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_quote
from app.models.market import Asset, PriceBar
from app.scrapers.base import BaseScraper

logger = logging.getLogger("openkhazana.scrapers.crypto")
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COIN_MAP = {
    "bitcoin": ("BTC", "Bitcoin", "BTC-USD"),
    "ethereum": ("ETH", "Ethereum", "ETH-USD"),
    "solana": ("SOL", "Solana", "SOL-USD"),
    "tether": ("USDT", "Tether USD", "USDT-USD"),
}


def _fetch_crypto_5y_sync(yf_ticker: str) -> list[dict[str, object]]:
    import yfinance as yf

    df = yf.Ticker(yf_ticker).history(period="5y", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return []
    out: list[dict[str, object]] = []
    for idx, row in df.iterrows():
        close_v = row.get("Close")
        if close_v is None or close_v != close_v or float(close_v) <= 0:
            continue
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        c = Decimal(str(round(float(close_v), 4))).quantize(Decimal("0.0001"))
        o = Decimal(str(round(float(row.get("Open", close_v)), 4))).quantize(Decimal("0.0001"))
        h = Decimal(str(round(float(row.get("High", close_v)), 4))).quantize(Decimal("0.0001"))
        lo = Decimal(str(round(float(row.get("Low", close_v)), 4))).quantize(Decimal("0.0001"))
        vol = Decimal(str(round(max(0.0, float(row.get("Volume", 0) or 0)), 2))).quantize(Decimal("0.01"))
        out.append(
            {
                "bar_time": ts.replace(hour=12, minute=0, second=0, microsecond=0),
                "open": o,
                "high": max(o, h, c),
                "low": min(o, lo, c),
                "close": c,
                "volume": vol,
            }
        )
    return out


class CryptoSpotScraper(BaseScraper):
    """Fetches spot USD prices via CoinGecko and backfills 5Y daily OHLCV bars via `yfinance`."""

    name = "crypto_spot"

    async def run(self, db: AsyncSession) -> int:
        params = {"ids": ",".join(COIN_MAP.keys()), "vs_currencies": "usd", "include_24hr_change": "true"}
        payload: dict = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(COINGECKO_URL, params=params)
                if resp.status_code == 200:
                    payload = resp.json()
        except Exception as exc:
            logger.debug("CoinGecko spot fetch skipped, using yfinance fallback: %s", exc)

        symbols = [sym for sym, _, _ in COIN_MAP.values()]
        existing = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.symbol.in_(symbols),
                    )
                )
            ).scalars()
        }
        now = datetime.now(timezone.utc)
        bar_day = now.replace(hour=12, minute=0, second=0, microsecond=0)
        existing_bars = {
            b.asset_id: b
            for b in (
                await db.execute(
                    select(PriceBar).where(
                        PriceBar.asset_id.in_([a.id for a in existing.values()]),
                        PriceBar.timeframe == "1D",
                        PriceBar.bar_time == bar_day,
                    )
                )
            ).scalars()
        } if existing else {}

        bar_counts = {
            r[0]: int(r[1])
            for r in (
                await db.execute(
                    select(PriceBar.asset_id, func.count(PriceBar.id))
                    .where(
                        PriceBar.asset_id.in_([a.id for a in existing.values()]),
                        PriceBar.timeframe == "1D",
                    )
                    .group_by(PriceBar.asset_id)
                )
            ).all()
        } if existing else {}

        upserted = 0
        for cg_id, (symbol, name, yf_sym) in COIN_MAP.items():
            entry = payload.get(cg_id)
            asset = existing.get(symbol)
            need_backfill = asset is None or bar_counts.get(asset.id, 0) < 400
            hist_bars: list[dict[str, object]] = []
            if need_backfill or not entry or "usd" not in entry:
                try:
                    hist_bars = await asyncio.to_thread(_fetch_crypto_5y_sync, yf_sym)
                except Exception as exc:
                    logger.debug("Crypto 5Y backfill skipped for %s: %s", symbol, exc)

            if entry and "usd" in entry:
                price = Decimal(str(entry["usd"])).quantize(Decimal("0.0001"))
                chg = Decimal(str(entry.get("usd_24h_change", 0))).quantize(Decimal("0.0001"))
            elif hist_bars:
                price = hist_bars[-1]["close"]  # type: ignore[assignment]
                prev_c = hist_bars[-2]["close"] if len(hist_bars) >= 2 else price  # type: ignore[assignment]
                chg = (((price - prev_c) / prev_c) * Decimal("100")).quantize(Decimal("0.0001")) if prev_c > 0 else Decimal("0")
            else:
                continue

            if asset is None:
                asset = Asset(
                    symbol=symbol,
                    name=name,
                    asset_class="CRYPTO",
                    currency="USD",
                    sector_or_category="Digital Assets",
                    last_price=price,
                    prev_close=price,
                    change_pct=chg,
                    source="coingecko",
                    updated_at=now,
                )
                db.add(asset)
                await db.flush()
                existing[symbol] = asset
            else:
                asset.prev_close = asset.last_price if asset.last_price > 0 else price
                asset.last_price = price
                asset.change_pct = chg
                asset.source = "coingecko"
                asset.updated_at = now

            if hist_bars:
                known_dates = {
                    (bt.date() if hasattr(bt, "date") else bt)
                    for bt in (
                        await db.execute(
                            select(PriceBar.bar_time).where(
                                PriceBar.asset_id == asset.id,
                                PriceBar.timeframe == "1D",
                            )
                        )
                    ).scalars()
                }
                for hb in hist_bars:
                    hb_dt = hb["bar_time"]
                    if hb_dt.date() in known_dates:  # type: ignore[union-attr]
                        continue
                    known_dates.add(hb_dt.date())  # type: ignore[union-attr]
                    db.add(
                        PriceBar(
                            asset_id=asset.id,
                            timeframe="1D",
                            bar_time=hb_dt,
                            open=hb["open"],
                            high=hb["high"],
                            low=hb["low"],
                            close=hb["close"],
                            volume=hb["volume"],
                        )
                    )

            bar = existing_bars.get(asset.id)
            if bar is None and not hist_bars:
                db.add(
                    PriceBar(
                        asset_id=asset.id,
                        timeframe="1D",
                        bar_time=bar_day,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=Decimal("0"),
                    )
                )
            elif bar is not None:
                bar.close = price
                bar.high = max(bar.high, price)
                bar.low = min(bar.low, price)

            await cache_quote(symbol, {"symbol": symbol, "last_price": str(price), "updated_at": now.isoformat()})
            upserted += 1

        await db.commit()
        return upserted
