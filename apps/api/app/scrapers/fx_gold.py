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
from app.scrapers.base import BaseScraper, ScraperParseError

logger = logging.getLogger("openkhazana.scrapers.fx_gold")
FX_API_URL = "https://open.er-api.com/v6/latest/USD"
GOLD_SPOT_URL = "https://api.gold-api.com/price/XAU"
SILVER_SPOT_URL = "https://api.gold-api.com/price/XAG"
GRAMS_PER_TROY_OZ = Decimal("31.1034768")
OPEN_MARKET_SPREAD_FACTOR = Decimal("1.0075")  # Typical Pakistan Open Market premium over SBP Interbank


def _fetch_fx_gold_5y_sync() -> dict[str, list[tuple[datetime, Decimal]]]:
    """Fetch 5-year daily FX (PKR/EUR/GBP/AED/SAR) and Gold/Silver (PKR/gram) history via yfinance."""
    import pandas as pd
    import yfinance as yf

    out: dict[str, list[tuple[datetime, Decimal]]] = {}
    direct_map = {
        "USD_PKR_INTERBANK": "PKR=X",
        "EURPKR": "EURPKR=X",
        "GBPPKR": "GBPPKR=X",
        "AEDPKR": "AEDPKR=X",
        "SARPKR": "SARPKR=X",
    }
    pkr_series = None

    for sym, yf_sym in direct_map.items():
        try:
            df = yf.Ticker(yf_sym).history(period="5y", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                continue
            closes = df["Close"].dropna()
            if sym == "USD_PKR_INTERBANK":
                pkr_series = closes
            pts: list[tuple[datetime, Decimal]] = []
            for idx, val in closes.items():
                fv = float(val)
                if fv <= 0:
                    continue
                ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now(timezone.utc)
                ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                pts.append((ts.replace(hour=12, minute=0, second=0, microsecond=0), Decimal(str(round(fv, 2))).quantize(Decimal("0.01"))))
            if pts:
                out[sym] = pts
                if sym == "USD_PKR_INTERBANK":
                    out["USD_PKR_OPEN"] = [
                        (dt, (px * OPEN_MARKET_SPREAD_FACTOR).quantize(Decimal("0.01"))) for dt, px in pts
                    ]
        except Exception as exc:
            logger.debug("5Y FX history skipped for %s: %s", sym, exc)

    if pkr_series is not None and not pkr_series.empty:
        pkr_idx = pkr_series.copy()
        pkr_idx.index = pd.to_datetime(pkr_idx.index).tz_localize(None).normalize()
        for sym, yf_metal in (("GOLD_24K_GRAM", "GC=F"), ("SILVER_GRAM", "SI=F")):
            try:
                df_m = yf.Ticker(yf_metal).history(period="5y", interval="1d", auto_adjust=False)
                if df_m is None or df_m.empty:
                    continue
                m_close = df_m["Close"].dropna().copy()
                m_close.index = pd.to_datetime(m_close.index).tz_localize(None).normalize()
                joined = pd.concat([m_close.rename("metal"), pkr_idx.rename("pkr")], axis=1).sort_index().ffill().dropna()
                pts = []
                for idx, r in joined.iterrows():
                    pkr_g = (float(r["metal"]) * float(r["pkr"]) * float(OPEN_MARKET_SPREAD_FACTOR)) / float(GRAMS_PER_TROY_OZ)
                    if pkr_g > 0:
                        dt = datetime(idx.year, idx.month, idx.day, 12, 0, tzinfo=timezone.utc)
                        pts.append((dt, Decimal(str(round(pkr_g, 2))).quantize(Decimal("0.01"))))
                if pts:
                    out[sym] = pts
            except Exception as exc:
                logger.debug("5Y Metal history skipped for %s: %s", sym, exc)

    return out


def parse_fx_metals_rates(
    fx_rates: dict[str, float | int | str],
    gold_usd_oz: Decimal = Decimal("0"),
    silver_usd_oz: Decimal = Decimal("0"),
) -> dict[str, tuple[str, str, str, Decimal]]:
    """
    Convert upstream USD FX rates and XAU/XAG spot prices into normalized PKR quotes
    (`USD_PKR_INTERBANK`, `USD_PKR_OPEN`, `AEDPKR`, `SARPKR`, `GBPPKR`, `EURPKR`,
    `GOLD_24K_GRAM`, `SILVER_GRAM`).
    """
    usd_pkr_interbank = Decimal(str(fx_rates.get("PKR", 0)))
    if usd_pkr_interbank <= 0:
        return {}

    usd_pkr_open = (usd_pkr_interbank * OPEN_MARKET_SPREAD_FACTOR).quantize(Decimal("0.01"))
    targets: dict[str, tuple[str, str, str, Decimal]] = {
        "USD_PKR_INTERBANK": ("USD / PKR (SBP Interbank)", "FX", "Currencies", usd_pkr_interbank),
        "USD_PKR_OPEN": ("USD / PKR (Open Market)", "FX", "Currencies", usd_pkr_open),
        "AEDPKR": ("UAE Dirham / PKR", "FX", "Currencies", usd_pkr_open / Decimal(str(fx_rates.get("AED", "3.6725")))),
        "SARPKR": ("Saudi Riyal / PKR", "FX", "Currencies", usd_pkr_open / Decimal(str(fx_rates.get("SAR", "3.75")))),
        "GBPPKR": ("British Pound / PKR", "FX", "Currencies", usd_pkr_open / Decimal(str(fx_rates.get("GBP", "0.78")))),
        "EURPKR": ("Euro / PKR", "FX", "Currencies", usd_pkr_open / Decimal(str(fx_rates.get("EUR", "0.92")))),
    }

    if gold_usd_oz > 0:
        targets["GOLD_24K_GRAM"] = (
            "Gold 24K (Per 1 Gram)",
            "GOLD",
            "Precious Metals",
            (gold_usd_oz * usd_pkr_open) / GRAMS_PER_TROY_OZ,
        )
    if silver_usd_oz > 0:
        targets["SILVER_GRAM"] = (
            "Silver 999 (Per 1 Gram)",
            "GOLD",
            "Precious Metals",
            (silver_usd_oz * usd_pkr_open) / GRAMS_PER_TROY_OZ,
        )
    return targets


class FxGoldScraper(BaseScraper):
    """
    Refreshes dual USD/PKR feeds (`USD_PKR_INTERBANK` and `USD_PKR_OPEN`), major remittances FX,
    Gold (`GOLD_24K_GRAM`), and Silver (`SILVER_GRAM`), recording daily `1D` PriceBars for all feeds.
    """

    name = "fx_gold"

    async def run(self, db: AsyncSession) -> int:
        async with httpx.AsyncClient(timeout=15.0) as client:
            fx_resp = await client.get(FX_API_URL)
            fx_resp.raise_for_status()
            payload = fx_resp.json()
            rates = payload.get("rates", {})

            gold_usd_oz = Decimal("0")
            try:
                gold_resp = await client.get(GOLD_SPOT_URL)
                if gold_resp.status_code == 200:
                    gold_usd_oz = Decimal(str(gold_resp.json().get("price", 0)))
            except Exception:
                gold_usd_oz = Decimal("0")

            silver_usd_oz = Decimal("0")
            try:
                silver_resp = await client.get(SILVER_SPOT_URL)
                if silver_resp.status_code == 200:
                    silver_usd_oz = Decimal(str(silver_resp.json().get("price", 0)))
            except Exception:
                silver_usd_oz = Decimal("0")

        targets = parse_fx_metals_rates(rates, gold_usd_oz=gold_usd_oz, silver_usd_oz=silver_usd_oz)
        if not targets:
            raise ScraperParseError("Missing PKR rate in FX payload", raw_payload=fx_resp.text)

        existing = {
            a.symbol: a
            for a in (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.symbol.in_(list(targets.keys())),
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

        upserted = 0
        for symbol, (name, asset_class, sector, price) in targets.items():
            rounded = price.quantize(Decimal("0.01"))
            asset = existing.get(symbol)
            if asset is None:
                asset = Asset(
                    symbol=symbol,
                    name=name,
                    asset_class=asset_class,
                    currency="PKR",
                    sector_or_category=sector,
                    is_shariah_compliant=asset_class == "GOLD",
                    last_price=rounded,
                    prev_close=rounded,
                    source="fx_gold_feed",
                    updated_at=now,
                )
                db.add(asset)
                await db.flush()
            else:
                prev = asset.last_price if asset.last_price > 0 else rounded
                asset.prev_close = prev
                asset.last_price = rounded
                asset.change_pct = (
                    ((rounded - prev) / prev * Decimal("100")).quantize(Decimal("0.0001")) if prev > 0 else Decimal("0")
                )
                asset.source = "fx_gold_feed"
                asset.updated_at = now

            existing_bar = existing_bars.get(asset.id)
            if existing_bar is None:
                db.add(
                    PriceBar(
                        asset_id=asset.id,
                        timeframe="1D",
                        bar_time=bar_day,
                        open=rounded,
                        high=rounded,
                        low=rounded,
                        close=rounded,
                        volume=Decimal("0"),
                    )
                )
            else:
                existing_bar.close = rounded
                existing_bar.high = max(existing_bar.high, rounded)
                existing_bar.low = min(existing_bar.low, rounded)

            await cache_quote(symbol, {"symbol": symbol, "last_price": str(rounded), "updated_at": now.isoformat()})
            existing[symbol] = asset
            upserted += 1

        # Backfill 5Y daily FX/Gold/Silver history via yfinance if any feed has < 400 bars
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
        if any(bar_counts.get(a.id, 0) < 400 for a in existing.values()):
            try:
                hist_map = await asyncio.to_thread(_fetch_fx_gold_5y_sync)
                for sym, pts in hist_map.items():
                    asset = existing.get(sym)
                    if asset is None or bar_counts.get(asset.id, 0) >= 400:
                        continue
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
                    for dt_utc, px in pts:
                        if dt_utc.date() in known_dates:
                            continue
                        known_dates.add(dt_utc.date())
                        db.add(
                            PriceBar(
                                asset_id=asset.id,
                                timeframe="1D",
                                bar_time=dt_utc,
                                open=px,
                                high=px,
                                low=px,
                                close=px,
                                volume=Decimal("0"),
                            )
                        )
            except Exception as exc:
                logger.debug("5Y FX/Gold backfill skipped: %s", exc)

        await db.commit()
        return upserted
