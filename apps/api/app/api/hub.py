"""
OpenKhazana v6 Market Hub API Router:
  - Pakistan Financial News Engine (GET /news, GET /stocks/{symbol}/news, POST /news/refresh)
  - PSX IPOs & Latest Developments (GET /ipos)
  - Sector & Industry Explorer via Pandas (GET /sectors)
  - Side-by-Side Stock Comparison Matrix via Pandas (GET /compare)
  - Corporate Calendar: Ex-Dividends, Bonus/Rights, Board Meetings & Results (GET /calendar)
  - Filings & Reports Downloads Center + Allowlisted PDF Download Proxy (GET /downloads, GET /downloads/proxy)
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_optional_user, resolve_asset_by_symbol
from app.core.cache import api_cache
from app.core.database import get_db
from app.models.market import (
    Announcement,
    Asset,
    CorporateAction,
    EquityFinancialPeriod,
    FundFmrSnapshot,
    IpoListing,
    NewsArticle,
    PriceBar,
)
from app.models.portfolio import Portfolio, Transaction, Watchlist, WatchlistItem
from app.models.user import User
from app.scrapers.pk_news import PakistanNewsScraper
from app.scrapers.psx_ipos import PSX_IPO_GUIDES, PsxIpoAndListingsScraper
from app.services.pandas_analytics import (
    analyze_financial_statements_pandas,
    compute_technical_summary_gauge_pandas,
)

router = APIRouter(prefix="/api/v1/market", tags=["market-hub"])

ALLOWED_PDF_HOSTS = {
    "dps.psx.com.pk",
    "www.psx.com.pk",
    "psx.com.pk",
    "www.almeezangroup.com",
    "almeezangroup.com",
    "www.hblasset.com",
    "hblasset.com",
    "www.jsil.com",
    "jsil.com",
    "www.ablamc.com",
    "ablamc.com",
    "www.nbpfunds.com",
    "nbpfunds.com",
    "www.ublfunds.com.pk",
    "www.mcbfunds.com",
}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "document").strip())
    clean = re.sub(r"_+", "_", clean).strip("_.")[:120]
    if not clean:
        clean = "openkhazana_document"
    if not clean.lower().endswith(".pdf"):
        clean += ".pdf"
    return clean


def _classify_download_category(title: str, raw_cat: str) -> str:
    low = f"{title} {raw_cat}".lower()
    if any(k in low for k in ("annual report", "quarterly report", "half yearly", "transmission of")):
        return "ANNUAL_QUARTERLY"
    if any(k in low for k in ("financial results", "profit", "accounts", "earnings")):
        return "RESULTS"
    if any(k in low for k in ("dividend", "bonus", "right", "payout", "book closure", "entitlement")):
        return "DIVIDEND"
    if any(k in low for k in ("board meeting", "closed period", "agm", "eogm", "annual general meeting")):
        return "BOARD_MEETING"
    return "MATERIAL"


def _serialize_news(art: NewsArticle) -> dict[str, Any]:
    return {
        "id": art.id,
        "title": art.title,
        "summary": art.summary,
        "url": art.url,
        "source_key": art.source_key,
        "source_name": art.source_name,
        "category": art.category,
        "matched_symbols": list(art.matched_symbols_json or []),
        "matched_sectors": list(art.matched_sectors_json or []),
        "published_at": art.published_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. PAKISTAN FINANCIAL NEWS ENGINE
# ---------------------------------------------------------------------------


async def _get_recent_news_cached(db: AsyncSession) -> list[dict[str, Any]]:
    async def _load() -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(350)
            )
        ).scalars().all()
        return [_serialize_news(art) for art in rows]

    return await api_cache.get_or_set("news:recent_350", 60.0, _load)


@router.get("/news")
async def list_market_news(
    symbol: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    source: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=150),
    db: AsyncSession = Depends(get_db),
):
    """Browse aggregated Pakistan financial news with symbol, sector, source, category, and keyword filters."""
    rows = await _get_recent_news_cached(db)

    sym_u = symbol.strip().upper() if symbol and symbol.strip() else None
    sec_l = sector.strip().lower() if sector and sector.strip() and sector.upper() != "ALL" else None
    src_l = source.strip().lower() if source and source.strip() and source.upper() != "ALL" else None
    cat_l = category.strip().lower() if category and category.strip() and category.upper() != "ALL" else None
    q_l = q.strip().lower()

    source_counts: dict[str, dict[str, Any]] = {}
    sector_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    filtered: list[dict[str, Any]] = []

    for art in rows:
        sc = source_counts.setdefault(
            art["source_key"],
            {"source_key": art["source_key"], "source_name": art["source_name"], "count": 0},
        )
        sc["count"] += 1
        cat_val = art["category"]
        category_counts[cat_val] = category_counts.get(cat_val, 0) + 1
        for s in art["matched_sectors"]:
            sector_counts[s] = sector_counts.get(s, 0) + 1

        if src_l and art["source_key"].lower() != src_l:
            continue
        if cat_l and cat_val.lower() != cat_l:
            continue
        if sym_u:
            syms = [str(x).upper() for x in art["matched_symbols"]]
            if sym_u not in syms and sym_u not in art["title"].upper():
                continue
        if sec_l:
            secs = [str(x).lower() for x in art["matched_sectors"]]
            if not any(sec_l in s for s in secs):
                continue
        if q_l and q_l not in art["title"].lower() and q_l not in (art["summary"] or "").lower():
            continue
        filtered.append(art)

    top_sectors = sorted(sector_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]
    return {
        "total": len(filtered),
        "items": filtered[:limit],
        "sources": sorted(source_counts.values(), key=lambda x: x["count"], reverse=True),
        "categories": [{"category": k, "count": v} for k, v in sorted(category_counts.items(), key=lambda kv: kv[1], reverse=True)],
        "sectors": [{"sector": k, "count": v} for k, v in top_sectors],
    }


@router.get("/stocks/{symbol}/news")
async def stock_and_sector_news(
    symbol: str,
    limit: int = Query(default=20, ge=1, le=60),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Return stock-specific news AND industry/sector-specific news for a given equity."""
    asset = await resolve_asset_by_symbol(symbol, db, owner_user_id=user.id if user else None)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset '{symbol}' not found.")

    rows = await _get_recent_news_cached(db)

    sym_u = asset.symbol.upper()
    name_stem = re.sub(r"\b(limited|ltd\.?|company|corporation|pakistan)\b", "", asset.name.lower()).strip()
    sec_u = (asset.sector_or_category or "").strip().upper()

    stock_news: list[dict[str, Any]] = []
    sector_news: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for art in rows:
        syms = [str(x).upper() for x in art["matched_symbols"]]
        title_low = art["title"].lower()
        is_stock_match = sym_u in syms or (len(name_stem) >= 6 and name_stem in title_low)
        if is_stock_match:
            stock_news.append(art)
            seen_ids.add(art["id"])
            continue

        secs = [str(x).upper() for x in art["matched_sectors"]]
        if sec_u and any(sec_u == s or sec_u in s or s in sec_u for s in secs):
            if art["id"] not in seen_ids:
                sector_news.append(art)

    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "sector": asset.sector_or_category,
        "stock_news": stock_news[:limit],
        "sector_news": sector_news[:limit],
    }


@router.post("/news/refresh")
async def refresh_market_news(db: AsyncSession = Depends(get_db)):
    """Trigger an immediate live RSS refresh across Pakistan financial news publications."""
    added = await PakistanNewsScraper().run(db)
    api_cache.invalidate_prefix("news:")
    api_cache.invalidate_prefix("ipos:")
    return {"ok": True, "articles_added": added}


# ---------------------------------------------------------------------------
# 2. PSX IPOS & LATEST CORPORATE DEVELOPMENTS
# ---------------------------------------------------------------------------


@router.get("/ipos")
async def list_ipos_and_developments(
    board: str = Query(default="ALL", description="ALL | MAIN | GEM"),
    status_filter: str = Query(default="ALL", alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Return PSX Main & GEM Board IPOs with live post-IPO return tracking, IPO news, official guides & PUCARS developments."""
    async def _load_base() -> dict[str, Any]:
        ipo_rows = (await db.execute(select(IpoListing))).scalars().all()
        if not ipo_rows:
            await PsxIpoAndListingsScraper().run(db)
            ipo_rows = (await db.execute(select(IpoListing))).scalars().all()

        syms = [r.symbol.upper() for r in ipo_rows if r.symbol]
        live_assets: dict[str, Asset] = {}
        if syms:
            asset_rows = (
                await db.execute(
                    select(Asset)
                    .options(selectinload(Asset.psx_profile))
                    .where(Asset.owner_user_id.is_(None), Asset.symbol.in_(syms))
                )
            ).scalars().all()
            live_assets = {a.symbol.upper(): a for a in asset_rows}

        status_rank = {"OPEN": 0, "BOOK_BUILDING": 1, "UPCOMING": 2, "LISTED": 3}
        enriched_ipos: list[dict[str, Any]] = []
        for r in ipo_rows:
            asset = live_assets.get(r.symbol.upper()) if r.symbol else None
            live_px = _safe_float(asset.last_price) if asset and float(asset.last_price) > 0 else None
            live_chg = _safe_float(asset.change_pct) if asset else None
            strike = _safe_float(r.strike_price) or _safe_float(r.floor_price)
            post_ret = round(((live_px - strike) / strike) * 100.0, 2) if (live_px and strike and strike > 0) else None
            sub_m = _safe_float(r.subscription_multiple)
            enriched_ipos.append(
                {
                    "id": r.id,
                    "slug": r.slug,
                    "company_name": r.company_name,
                    "symbol": r.symbol,
                    "sector": r.sector,
                    "board": r.board,
                    "status": r.status,
                    "issue_type": r.issue_type,
                    "floor_price": _safe_float(r.floor_price),
                    "strike_price": _safe_float(r.strike_price),
                    "issue_size_mn": _safe_float(r.issue_size_mn),
                    "subscription_multiple": sub_m,
                    "book_building_date": r.book_building_date.isoformat() if r.book_building_date else None,
                    "listing_date": r.listing_date.isoformat() if r.listing_date else None,
                    "lead_manager": r.lead_manager,
                    "prospectus_url": r.prospectus_url,
                    "source_url": r.source_url,
                    "summary": r.summary,
                    "is_shariah_compliant": asset.is_shariah_compliant if asset else r.is_shariah_compliant,
                    "live_price": live_px,
                    "live_change_pct": live_chg,
                    "market_cap_mn": _safe_float(asset.psx_profile.market_cap_mn) if asset and asset.psx_profile else None,
                    "post_ipo_return_pct": post_ret,
                    "in_database": asset is not None,
                }
            )

        enriched_ipos.sort(
            key=lambda x: (
                status_rank.get(x["status"], 9),
                -(date.fromisoformat(x["listing_date"]).toordinal() if x["listing_date"] else 0),
            )
        )

        ann_rows = (
            await db.execute(
                select(Announcement, Asset)
                .join(Asset, Announcement.asset_id == Asset.id)
                .where(Asset.owner_user_id.is_(None))
                .order_by(Announcement.published_at.desc())
                .limit(45)
            )
        ).all()
        developments = [
            {
                "id": ann.id,
                "symbol": asset.symbol,
                "company_name": asset.name,
                "sector": asset.sector_or_category,
                "title": ann.title,
                "category": ann.category,
                "doc_type": _classify_download_category(ann.title, ann.category),
                "published_at": ann.published_at.isoformat(),
                "source_url": ann.source_url,
                "summary": ann.summary,
            }
            for ann, asset in ann_rows
        ]

        ipo_news_rows = (
            await db.execute(
                select(NewsArticle)
                .where(
                    (NewsArticle.category == "IPO")
                    | (NewsArticle.title.ilike("%ipo%"))
                    | (NewsArticle.title.ilike("%listing%"))
                    | (NewsArticle.title.ilike("%psx%"))
                )
                .order_by(NewsArticle.published_at.desc())
                .limit(15)
            )
        ).scalars().all()

        return {
            "ipos": enriched_ipos,
            "developments": developments,
            "ipo_news": [_serialize_news(n) for n in ipo_news_rows],
            "guides": PSX_IPO_GUIDES,
        }

    base = await api_cache.get_or_set("ipos:base", 180.0, _load_base)
    b_up = board.strip().upper()
    s_up = status_filter.strip().upper()
    filtered_ipos = [
        x
        for x in base["ipos"]
        if (b_up == "ALL" or x["board"].upper() == b_up)
        and (s_up == "ALL" or x["status"].upper() == s_up)
    ]
    post_ipo_returns = [x["post_ipo_return_pct"] for x in filtered_ipos if x["post_ipo_return_pct"] is not None]
    oversubs = [x["subscription_multiple"] for x in filtered_ipos if x["subscription_multiple"] is not None]
    return {
        "summary": {
            "total_tracked": len(filtered_ipos),
            "main_board_count": sum(1 for x in filtered_ipos if x["board"] == "MAIN"),
            "gem_board_count": sum(1 for x in filtered_ipos if x["board"] == "GEM"),
            "upcoming_count": sum(1 for x in filtered_ipos if x["status"] in {"UPCOMING", "OPEN", "BOOK_BUILDING"}),
            "avg_oversubscription_x": round(sum(oversubs) / len(oversubs), 2) if oversubs else None,
            "avg_post_ipo_return_pct": round(sum(post_ipo_returns) / len(post_ipo_returns), 2) if post_ipo_returns else None,
        },
        "ipos": filtered_ipos,
        "developments": base["developments"],
        "ipo_news": base["ipo_news"],
        "guides": base["guides"],
    }


# ---------------------------------------------------------------------------
# 3. SECTOR & INDUSTRY EXPLORER (PANDAS CROSS-SECTIONAL ENGINE)
# ---------------------------------------------------------------------------


def _aggregate_sectors_pandas_sync(records: list[dict[str, Any]], m_up: str) -> dict[str, Any]:
    if not records:
        return {"market": m_up, "sector_count": 0, "total_stocks": 0, "total_market_cap_mn": 0.0, "sectors": []}

    df = pd.DataFrame(records)
    sectors_out: list[dict[str, Any]] = []
    for sec_name, grp in df.groupby("sector", sort=False):
        total_mcap = float(grp["market_cap_mn"].sum())
        if total_mcap > 0:
            cap_w_chg = float((grp["change_pct"] * grp["market_cap_mn"]).sum() / total_mcap)
        else:
            cap_w_chg = float(grp["change_pct"].mean())
        eq_w_chg = float(grp["change_pct"].mean())
        adv = int((grp["change_pct"] > 0).sum())
        dec = int((grp["change_pct"] < 0).sum())
        unc = int((grp["change_pct"] == 0).sum())
        sh_cnt = int(grp["is_shariah_compliant"].sum())
        n_stocks = int(len(grp))

        grp_sorted = grp.sort_values(by=["market_cap_mn", "volume"], ascending=[False, False])
        top_leader_row = grp_sorted.iloc[0]
        top_gainer_row = grp.sort_values(by="change_pct", ascending=False).iloc[0]

        stocks_list = [
            {
                "symbol": str(r["symbol"]),
                "name": str(r["name"]),
                "currency": str(r["currency"]),
                "last_price": round(float(r["last_price"]), 2),
                "change_pct": round(float(r["change_pct"]), 2),
                "volume": int(float(r["volume"])),
                "market_cap_mn": round(float(r["market_cap_mn"]), 1),
                "pe_ratio": round(float(r["pe_ratio"]), 2) if pd.notna(r["pe_ratio"]) else None,
                "pb_ratio": round(float(r["pb_ratio"]), 2) if pd.notna(r["pb_ratio"]) else None,
                "roe_pct": round(float(r["roe_pct"]), 1) if pd.notna(r["roe_pct"]) else None,
                "dividend_yield_pct": round(float(r["dividend_yield_pct"]), 2) if pd.notna(r["dividend_yield_pct"]) else 0.0,
                "piotroski_f_score": int(r["piotroski_f_score"]) if pd.notna(r["piotroski_f_score"]) else None,
                "is_shariah_compliant": bool(r["is_shariah_compliant"]),
                "is_kse100": bool(r["is_kse100"]),
            }
            for r in grp_sorted.to_dict(orient="records")
        ]

        med_pe = float(grp["pe_ratio"].median()) if grp["pe_ratio"].notna().any() else None
        med_pb = float(grp["pb_ratio"].median()) if grp["pb_ratio"].notna().any() else None
        med_roe = float(grp["roe_pct"].median()) if grp["roe_pct"].notna().any() else None
        med_dy = float(grp["dividend_yield_pct"].median()) if grp["dividend_yield_pct"].notna().any() else None
        avg_f = float(grp["piotroski_f_score"].mean()) if grp["piotroski_f_score"].notna().any() else None

        sectors_out.append(
            {
                "sector": str(sec_name),
                "stock_count": n_stocks,
                "shariah_count": sh_cnt,
                "shariah_pct": round((sh_cnt / n_stocks) * 100.0, 1) if n_stocks else 0.0,
                "total_market_cap_mn": round(total_mcap, 1),
                "total_volume": int(float(grp["volume"].sum())),
                "cap_weighted_change_pct": round(cap_w_chg, 2),
                "equal_weighted_change_pct": round(eq_w_chg, 2),
                "advancers": adv,
                "decliners": dec,
                "unchanged": unc,
                "median_pe": round(med_pe, 2) if med_pe is not None else None,
                "median_pb": round(med_pb, 2) if med_pb is not None else None,
                "median_roe_pct": round(med_roe, 1) if med_roe is not None else None,
                "median_div_yield_pct": round(med_dy, 2) if med_dy is not None else None,
                "avg_f_score": round(avg_f, 1) if avg_f is not None else None,
                "top_leader": {
                    "symbol": str(top_leader_row["symbol"]),
                    "market_cap_mn": round(float(top_leader_row["market_cap_mn"]), 1),
                },
                "top_gainer": {
                    "symbol": str(top_gainer_row["symbol"]),
                    "change_pct": round(float(top_gainer_row["change_pct"]), 2),
                },
                "stocks": stocks_list,
            }
        )

    sectors_out.sort(key=lambda s: (s["total_market_cap_mn"], s["stock_count"]), reverse=True)
    return {
        "market": m_up,
        "sector_count": len(sectors_out),
        "total_stocks": sum(s["stock_count"] for s in sectors_out),
        "total_market_cap_mn": round(sum(s["total_market_cap_mn"] for s in sectors_out), 1),
        "sectors": sectors_out,
    }


@router.get("/sectors")
async def sector_industry_explorer(
    market: str = Query(default="PSX", description="PSX | INTL | ALL"),
    shariah_only: bool = Query(default=False),
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate all PSX sectors (or Global sectors) with Pandas medians, cap-weighted 1D return, breadth & constituent drilldowns."""
    m_up = market.strip().upper()
    if m_up == "PSX":
        classes = ["PSX_EQUITY", "PSX_ETF"]
    elif m_up in {"INTL", "GLOBAL"}:
        classes = ["INTL_EQUITY", "INTL_ETF"]
    else:
        classes = ["PSX_EQUITY", "PSX_ETF", "INTL_EQUITY", "INTL_ETF"]

    async def _load_base() -> dict[str, Any]:
        stmt = (
            select(Asset)
            .options(selectinload(Asset.psx_profile), selectinload(Asset.financial_snapshot))
            .where(Asset.owner_user_id.is_(None), Asset.asset_class.in_(classes))
        )
        if shariah_only:
            stmt = stmt.where(Asset.is_shariah_compliant.is_(True))
        assets = (await db.execute(stmt)).scalars().all()

        records: list[dict[str, Any]] = []
        for a in assets:
            p = a.psx_profile
            fs = a.financial_snapshot
            sec = (a.sector_or_category or "General").strip() or "General"
            mcap = float(p.market_cap_mn) if p and p.market_cap_mn and float(p.market_cap_mn) > 0 else 0.0
            pe = float(p.pe_ratio) if p and p.pe_ratio and 0 < float(p.pe_ratio) < 150 else np.nan
            pb = float(p.pb_ratio) if p and p.pb_ratio and 0 < float(p.pb_ratio) < 40 else np.nan
            if math.isnan(pb) and fs and fs.bvps and float(fs.bvps) > 0 and float(a.last_price) > 0:
                pb = float(a.last_price) / float(fs.bvps)
            roe = (
                float(fs.roe_pct)
                if fs and fs.roe_pct is not None and -100 < float(fs.roe_pct) < 250
                else (float(p.roe_pct) if p and p.roe_pct is not None and -100 < float(p.roe_pct) < 250 else np.nan)
            )
            dy = float(p.dividend_yield_pct) if p and p.dividend_yield_pct and float(p.dividend_yield_pct) > 0 else np.nan
            records.append(
                {
                    "symbol": a.symbol,
                    "name": a.name,
                    "sector": sec,
                    "currency": a.currency,
                    "is_shariah_compliant": bool(a.is_shariah_compliant),
                    "is_kse100": bool(p.is_kse100) if p else False,
                    "is_kmi30": bool(p.is_kmi30) if p else False,
                    "last_price": float(a.last_price),
                    "change_pct": float(a.change_pct),
                    "volume": float(a.volume),
                    "market_cap_mn": mcap,
                    "pe_ratio": pe,
                    "pb_ratio": pb,
                    "roe_pct": roe,
                    "dividend_yield_pct": dy,
                    "piotroski_f_score": float(fs.piotroski_f_score) if fs and fs.piotroski_f_score is not None else np.nan,
                }
            )
        return await asyncio.to_thread(_aggregate_sectors_pandas_sync, records, m_up)

    base = await api_cache.get_or_set(f"sectors:{m_up}:{shariah_only}", 120.0, _load_base)
    q_l = q.strip().lower()
    if not q_l:
        return base

    filtered_secs = [
        sec
        for sec in base["sectors"]
        if q_l in sec["sector"].lower()
        or any(q_l in st["symbol"].lower() or q_l in st["name"].lower() for st in sec["stocks"])
    ]
    return {
        "market": m_up,
        "sector_count": len(filtered_secs),
        "total_stocks": sum(s["stock_count"] for s in filtered_secs),
        "total_market_cap_mn": round(sum(s["total_market_cap_mn"] for s in filtered_secs), 1),
        "sectors": filtered_secs,
    }


# ---------------------------------------------------------------------------
# 4. SIDE-BY-SIDE STOCK COMPARISON MATRIX (PANDAS)
# ---------------------------------------------------------------------------


@router.get("/compare")
async def compare_stocks_matrix(
    symbols: str = Query(..., description="Comma-separated 2 to 5 stock symbols, e.g. MEBL,MCB,UBL,HBL"),
    days: int = Query(default=252, ge=30, le=750),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare 2 to 5 PSX or global equities head-to-head on indexed performance, valuation, growth CAGRs, quality, dividends & Shariah."""
    raw_syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    unique_syms: list[str] = []
    for s in raw_syms:
        if s not in unique_syms:
            unique_syms.append(s)
    if len(unique_syms) < 2 or len(unique_syms) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide between 2 and 5 unique symbols separated by commas (e.g. MEBL,MCB,UBL).",
        )

    resolved: list[Asset] = []
    for sym in unique_syms:
        asset = await resolve_asset_by_symbol(
            sym,
            db,
            options=[
                selectinload(Asset.psx_profile),
                selectinload(Asset.shariah_metric),
                selectinload(Asset.financial_snapshot),
                selectinload(Asset.corporate_actions),
            ],
            owner_user_id=user.id if user else None,
        )
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Symbol '{sym}' not found.")
        resolved.append(asset)

    series_frames: list[pd.Series] = []
    items_out: list[dict[str, Any]] = []

    for asset in resolved:
        bars_rows = list(
            reversed(
                (
                    await db.execute(
                        select(PriceBar)
                        .where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
                        .order_by(PriceBar.bar_time.desc())
                        .limit(days)
                    )
                ).scalars().all()
            )
        )
        bar_dicts = [
            {
                "bar_time": b.bar_time,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars_rows
            if float(b.close) > 0
        ]

        ret_1m = ret_3m = ret_6m = ret_1y = ann_vol = max_dd = None
        if len(bar_dicts) >= 5:
            s_idx = pd.Series(
                [b["close"] for b in bar_dicts],
                index=pd.to_datetime([b["bar_time"].date() if hasattr(b["bar_time"], "date") else b["bar_time"] for b in bar_dicts]),
                name=asset.symbol,
            )
            s_idx = s_idx[~s_idx.index.duplicated(keep="last")].sort_index()
            series_frames.append(s_idx)

            last_c = float(s_idx.iloc[-1])
            if len(s_idx) >= 21:
                ret_1m = round(((last_c / float(s_idx.iloc[-21])) - 1.0) * 100.0, 2)
            if len(s_idx) >= 63:
                ret_3m = round(((last_c / float(s_idx.iloc[-63])) - 1.0) * 100.0, 2)
            if len(s_idx) >= 126:
                ret_6m = round(((last_c / float(s_idx.iloc[-126])) - 1.0) * 100.0, 2)
            ret_1y = round(((last_c / float(s_idx.iloc[0])) - 1.0) * 100.0, 2)
            pct = s_idx.pct_change().dropna()
            if len(pct) >= 10:
                ann_vol = round(float(pct.std() * math.sqrt(252) * 100.0), 2)
                running_max = s_idx.cummax()
                dd = ((s_idx / running_max) - 1.0) * 100.0
                max_dd = round(float(dd.min()), 2)

        gauge = compute_technical_summary_gauge_pandas(bar_dicts[-260:]) if len(bar_dicts) >= 30 else None

        # Multi-period CAGR & net margin via Pandas
        period_rows = (
            await db.execute(
                select(EquityFinancialPeriod)
                .where(EquityFinancialPeriod.asset_id == asset.id)
                .order_by(EquityFinancialPeriod.period_end_date.asc())
            )
        ).scalars().all()
        p_dicts = [
            {
                "period_type": p.period_type,
                "period_label": p.period_label,
                "period_end_date": p.period_end_date.isoformat(),
                "fiscal_year": p.fiscal_year,
                "revenue_mn": _safe_float(p.revenue_mn),
                "net_profit_mn": _safe_float(p.net_profit_mn),
                "eps": _safe_float(p.eps),
                "shareholders_equity_mn": _safe_float(p.shareholders_equity_mn),
                "total_assets_mn": _safe_float(p.total_assets_mn),
                "operating_cashflow_mn": _safe_float(p.operating_cashflow_mn),
                "free_cashflow_mn": _safe_float(p.free_cashflow_mn),
                "source_label": p.source_label,
            }
            for p in period_rows
        ]
        rep = analyze_financial_statements_pandas(p_dicts) if p_dicts else {}
        cagrs = rep.get("cagrs") or {}
        ttm = rep.get("ttm_summary") or {}

        # Dividend streak
        cash_years = sorted({ca.ex_date.year for ca in asset.corporate_actions if ca.action_type == "CASH_DIVIDEND" and ca.payout_per_share > 0})
        streak = 0
        if cash_years and cash_years[-1] >= date.today().year - 1:
            yr = cash_years[-1]
            yr_set = set(cash_years)
            while yr in yr_set:
                streak += 1
                yr -= 1

        prof = asset.psx_profile
        fs = asset.financial_snapshot
        sh = asset.shariah_metric

        pb_val = _safe_float(prof.pb_ratio) if prof else None
        if pb_val is None and fs and fs.bvps and float(fs.bvps) > 0 and float(asset.last_price) > 0:
            pb_val = round(float(asset.last_price) / float(fs.bvps), 2)
        roe_val = _safe_float(fs.roe_pct) if (fs and fs.roe_pct is not None) else (_safe_float(prof.roe_pct) if prof else None)

        items_out.append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "sector": asset.sector_or_category,
                "currency": asset.currency,
                "last_price": round(float(asset.last_price), 2),
                "change_pct": round(float(asset.change_pct), 2),
                "market_cap_mn": round(float(prof.market_cap_mn), 1) if prof and prof.market_cap_mn else 0.0,
                "pe_ratio": _safe_float(prof.pe_ratio) if prof else None,
                "pb_ratio": pb_val,
                "eps": _safe_float(prof.eps) if prof else None,
                "roe_pct": roe_val,
                "net_margin_pct": ttm.get("latest_net_margin_pct"),
                "operating_margin_pct": _safe_float(fs.operating_margin_pct) if fs else None,
                "dividend_yield_pct": _safe_float(prof.dividend_yield_pct) if prof else 0.0,
                "div_streak_years": streak,
                "revenue_cagr_3y_pct": cagrs.get("revenue_cagr_3y_pct"),
                "net_profit_cagr_3y_pct": cagrs.get("net_profit_cagr_3y_pct"),
                "eps_cagr_3y_pct": cagrs.get("eps_cagr_3y_pct"),
                "debt_to_equity": _safe_float(fs.debt_to_equity) if fs else None,
                "current_ratio": _safe_float(fs.current_ratio) if fs else None,
                "piotroski_f_score": fs.piotroski_f_score if fs else None,
                "altman_z_score": _safe_float(fs.altman_z_score) if fs else None,
                "dcf_intrinsic_value": _safe_float(fs.dcf_intrinsic_value) if fs else None,
                "return_1m_pct": ret_1m,
                "return_3m_pct": ret_3m,
                "return_6m_pct": ret_6m,
                "return_1y_pct": ret_1y,
                "volatility_pct": ann_vol,
                "max_drawdown_pct": max_dd,
                "technical_signal": (gauge or {}).get("overall_verdict"),
                "is_shariah_compliant": bool(asset.is_shariah_compliant),
                "purification_rate_pct": _safe_float(sh.purification_rate_pct) if sh else None,
            }
        )

    # Align & normalize price history to Base = 100 using Pandas
    indexed_series: list[dict[str, Any]] = []
    if series_frames:
        df_px = pd.concat(series_frames, axis=1).sort_index().ffill().dropna(how="all")
        if len(df_px) >= 2:
            # Normalize each column to 100 at its first non-null point
            norm = pd.DataFrame(index=df_px.index)
            for col in df_px.columns:
                first_valid = df_px[col].dropna()
                if not first_valid.empty and float(first_valid.iloc[0]) > 0:
                    norm[col] = (df_px[col] / float(first_valid.iloc[0])) * 100.0
            # Downsample slightly if > 180 points for smooth rendering
            if len(norm) > 180:
                step = max(1, len(norm) // 160)
                norm = norm.iloc[::step]
            for dt_idx, row in norm.iterrows():
                pt: dict[str, Any] = {"date": dt_idx.strftime("%Y-%m-%d")}
                for col in norm.columns:
                    v = row[col]
                    pt[col] = round(float(v), 2) if pd.notna(v) else None
                indexed_series.append(pt)

    def _pick_best(field: str, lowest: bool = False, positive_only: bool = False) -> str | None:
        cands = []
        for it in items_out:
            v = it.get(field)
            if v is None:
                continue
            if positive_only and v <= 0:
                continue
            cands.append((v, it["symbol"]))
        if not cands:
            return None
        cands.sort(key=lambda x: x[0], reverse=not lowest)
        return cands[0][1]

    leaders = {
        "lowest_pe": _pick_best("pe_ratio", lowest=True, positive_only=True),
        "lowest_pb": _pick_best("pb_ratio", lowest=True, positive_only=True),
        "highest_roe": _pick_best("roe_pct", lowest=False),
        "highest_div_yield": _pick_best("dividend_yield_pct", lowest=False, positive_only=True),
        "highest_return_1y": _pick_best("return_1y_pct", lowest=False),
        "highest_pat_cagr": _pick_best("net_profit_cagr_3y_pct", lowest=False),
        "highest_f_score": _pick_best("piotroski_f_score", lowest=False),
        "lowest_volatility": _pick_best("volatility_pct", lowest=True, positive_only=True),
    }

    return {
        "symbols": [it["symbol"] for it in items_out],
        "days": days,
        "stocks": items_out,
        "indexed_series": indexed_series,
        "leaders": leaders,
    }


# ---------------------------------------------------------------------------
# 5. CORPORATE CALENDAR (EX-DIVIDENDS, BONUS/RIGHTS, BOARD MEETINGS, RESULTS)
# ---------------------------------------------------------------------------


@router.get("/calendar")
async def corporate_calendar(
    event_type: str = Query(default="ALL", description="ALL | DIVIDEND | BONUS_RIGHTS | BOARD_MEETING | RESULTS"),
    symbol: str | None = Query(default=None),
    sector: str | None = Query(default=None),
    days_back: int = Query(default=120, ge=7, le=365),
    days_ahead: int = Query(default=90, ge=7, le=365),
    my_stocks_only: bool = Query(default=False),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Unified Pakistan Corporate Calendar combining Ex-Dividends, Bonus/Rights, Board Meetings, and Financial Results."""
    today = date.today()
    start_date = today - timedelta(days=days_back)
    end_date = today + timedelta(days=days_ahead)

    async def _load_calendar_base() -> list[dict[str, Any]]:
        base_events: list[dict[str, Any]] = []
        ca_rows = (
            await db.execute(
                select(CorporateAction, Asset)
                .join(Asset, CorporateAction.asset_id == Asset.id)
                .where(
                    Asset.owner_user_id.is_(None),
                    CorporateAction.ex_date >= start_date,
                    CorporateAction.ex_date <= end_date,
                )
                .order_by(CorporateAction.ex_date.desc())
                .limit(350)
            )
        ).all()
        for ca, asset in ca_rows:
            mapped_type = "DIVIDEND" if ca.action_type == "CASH_DIVIDEND" else "BONUS_RIGHTS"
            detail_badge = (
                f"Rs {float(ca.payout_per_share):.2f}/sh"
                if ca.action_type == "CASH_DIVIDEND"
                else f"{float(ca.ratio_numerator):g}% {ca.action_type.replace('_', ' ').title()}"
            )
            base_events.append(
                {
                    "id": f"ca-{ca.id}",
                    "event_date": ca.ex_date.isoformat(),
                    "is_upcoming": ca.ex_date >= today,
                    "event_type": mapped_type,
                    "symbol": asset.symbol,
                    "company_name": asset.name,
                    "sector": asset.sector_or_category,
                    "title": ca.description or f"{asset.symbol} {ca.action_type.replace('_', ' ').title()}",
                    "badge": detail_badge,
                    "source_url": f"https://dps.psx.com.pk/company/{asset.symbol}",
                    "is_shariah_compliant": asset.is_shariah_compliant,
                }
            )

        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        ann_rows = (
            await db.execute(
                select(Announcement, Asset)
                .join(Asset, Announcement.asset_id == Asset.id)
                .where(
                    Asset.owner_user_id.is_(None),
                    Announcement.published_at >= start_dt,
                )
                .order_by(Announcement.published_at.desc())
                .limit(350)
            )
        ).all()
        for ann, asset in ann_rows:
            doc_cat = _classify_download_category(ann.title, ann.category)
            if doc_cat not in {"BOARD_MEETING", "RESULTS"}:
                continue
            ev_date = ann.published_at.date()
            base_events.append(
                {
                    "id": f"ann-{ann.id}",
                    "event_date": ev_date.isoformat(),
                    "is_upcoming": ev_date >= today,
                    "event_type": doc_cat,
                    "symbol": asset.symbol,
                    "company_name": asset.name,
                    "sector": asset.sector_or_category,
                    "title": ann.title,
                    "badge": "Board Meeting" if doc_cat == "BOARD_MEETING" else "Financial Results",
                    "source_url": ann.source_url,
                    "is_shariah_compliant": asset.is_shariah_compliant,
                }
            )
        return base_events

    raw_events = await api_cache.get_or_set(f"calendar:base:{days_back}:{days_ahead}", 120.0, _load_calendar_base)

    my_symbols: set[str] = set()
    if user is not None:
        wl_syms = (
            await db.execute(
                select(Asset.symbol)
                .join(WatchlistItem, WatchlistItem.asset_id == Asset.id)
                .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
                .where(Watchlist.user_id == user.id)
            )
        ).scalars().all()
        tx_syms = (
            await db.execute(
                select(Asset.symbol)
                .join(Transaction, Transaction.asset_id == Asset.id)
                .join(Portfolio, Transaction.portfolio_id == Portfolio.id)
                .where(Portfolio.user_id == user.id)
            )
        ).scalars().all()
        my_symbols = {s.upper() for s in (*wl_syms, *tx_syms)}

    sym_u = symbol.strip().upper() if symbol and symbol.strip() else None
    sec_l = sector.strip().lower() if sector and sector.strip() and sector.upper() != "ALL" else None
    et_u = event_type.strip().upper()

    events: list[dict[str, Any]] = []
    for ev in raw_events:
        if et_u != "ALL" and ev["event_type"] != et_u:
            continue
        if sym_u and ev["symbol"].upper() != sym_u:
            continue
        if sec_l and sec_l not in (ev["sector"] or "").lower():
            continue
        in_mine = ev["symbol"].upper() in my_symbols
        if my_stocks_only and not in_mine:
            continue
        events.append({**ev, "in_my_stocks": in_mine})

    upcoming = sorted([e for e in events if e["is_upcoming"]], key=lambda e: e["event_date"])
    recent = sorted([e for e in events if not e["is_upcoming"]], key=lambda e: e["event_date"], reverse=True)
    ordered = upcoming + recent

    return {
        "today": today.isoformat(),
        "total": len(ordered),
        "upcoming_count": len(upcoming),
        "recent_count": len(recent),
        "my_symbols_count": len(my_symbols),
        "events": ordered[:200],
    }


# ---------------------------------------------------------------------------
# 6. FILINGS & REPORTS DOWNLOADS HUB + ALLOWLISTED PDF PROXY
# ---------------------------------------------------------------------------


@router.get("/downloads")
async def list_downloadable_filings(
    symbol: str | None = Query(default=None),
    category: str = Query(default="ALL", description="ALL | ANNUAL_QUARTERLY | RESULTS | DIVIDEND | BOARD_MEETING | MATERIAL | FMR"),
    q: str = Query(default=""),
    limit: int = Query(default=80, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Searchable catalog of downloadable PSX PUCARS PDFs, Annual/Quarterly Reports, AMC SECP FMR PDFs & CSV financial exports."""
    sym_u = symbol.strip().upper() if symbol and symbol.strip() else None
    cat_u = category.strip().upper()
    q_l = q.strip().lower()

    async def _load_downloads(target_sym: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ann_stmt = (
            select(Announcement, Asset)
            .join(Asset, Announcement.asset_id == Asset.id)
            .where(Asset.owner_user_id.is_(None), Announcement.source_url.is_not(None))
            .order_by(Announcement.published_at.desc())
            .limit(450 if target_sym is None else 150)
        )
        if target_sym:
            ann_stmt = ann_stmt.where(Asset.symbol == target_sym)
        ann_rows = (await db.execute(ann_stmt)).all()
        for ann, asset in ann_rows:
            if not ann.source_url:
                continue
            doc_cat = _classify_download_category(ann.title, ann.category)
            dt_str = ann.published_at.date().isoformat()
            fname = _sanitize_filename(f"{asset.symbol}_{dt_str}_{ann.title[:48]}")
            out.append(
                {
                    "id": f"ann-{ann.id}",
                    "symbol": asset.symbol,
                    "company_name": asset.name,
                    "sector": asset.sector_or_category,
                    "doc_type": doc_cat,
                    "title": ann.title,
                    "published_date": dt_str,
                    "source_url": ann.source_url,
                    "proxy_download_url": f"/api/v1/market/downloads/proxy?url={quote(ann.source_url, safe='')}&filename={quote(fname, safe='')}",
                    "csv_export_url": f"/api/v1/market/stocks/{asset.symbol}/reports/export",
                    "format": "PDF",
                }
            )

        fmr_stmt = (
            select(FundFmrSnapshot, Asset)
            .join(Asset, FundFmrSnapshot.asset_id == Asset.id)
            .where(Asset.owner_user_id.is_(None), FundFmrSnapshot.source_url.is_not(None))
            .order_by(FundFmrSnapshot.report_month.desc())
            .limit(120)
        )
        if target_sym:
            fmr_stmt = fmr_stmt.where(Asset.symbol == target_sym)
        fmr_rows = (await db.execute(fmr_stmt)).all()
        seen_fmr_urls: set[str] = set()
        for fmr, asset in fmr_rows:
            if not fmr.source_url:
                continue
            dedup_key = f"{fmr.amc_name}:{fmr.source_url}" if not target_sym else f"{asset.symbol}:{fmr.source_url}"
            if dedup_key in seen_fmr_urls:
                continue
            seen_fmr_urls.add(dedup_key)
            title = f"{fmr.amc_name} — SECP Fund Manager Report ({fmr.report_label or fmr.report_month})"
            if target_sym:
                title = f"{asset.symbol} ({asset.name}) — {title}"
            fname = _sanitize_filename(f"{fmr.amc_name}_FMR_{fmr.report_month}")
            out.append(
                {
                    "id": f"fmr-{fmr.id}",
                    "symbol": asset.symbol if target_sym else fmr.amc_name[:12].upper(),
                    "company_name": asset.name if target_sym else fmr.amc_name,
                    "sector": "Mutual Funds / SECP FMR",
                    "doc_type": "FMR",
                    "title": title,
                    "published_date": f"{fmr.report_month}-01" if len(fmr.report_month) == 7 else fmr.report_month,
                    "source_url": fmr.source_url,
                    "proxy_download_url": f"/api/v1/market/downloads/proxy?url={quote(fmr.source_url, safe='')}&filename={quote(fname, safe='')}",
                    "csv_export_url": None,
                    "format": "PDF",
                }
            )
        out.sort(key=lambda x: x["published_date"] or "", reverse=True)
        return out

    cache_key = f"downloads:sym:{sym_u}" if sym_u else "downloads:base"
    all_items = await api_cache.get_or_set(cache_key, 120.0, lambda: _load_downloads(sym_u))

    filtered: list[dict[str, Any]] = []
    for it in all_items:
        if cat_u != "ALL" and it["doc_type"] != cat_u:
            continue
        if q_l and q_l not in it["title"].lower() and q_l not in it["symbol"].lower() and q_l not in it["company_name"].lower():
            continue
        filtered.append(it)

    return {
        "total": len(filtered),
        "items": filtered[:limit],
    }


@router.get("/downloads/proxy")
async def proxy_download_official_pdf(
    url: str = Query(..., min_length=10, max_length=700),
    filename: str = Query(default="openkhazana_document.pdf", max_length=140),
):
    """SSRF-safe allowlisted PDF download proxy adding Content-Disposition: attachment for 1-click downloads."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or host not in ALLOWED_PDF_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Download proxy only permits official PSX and Pakistan AMC document hosts.",
        )

    safe_name = _sanitize_filename(filename)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": f"{parsed.scheme}://{host}/",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url.strip())
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream document host returned HTTP {resp.status_code}.",
                )
            content_type = resp.headers.get("content-type", "application/pdf")
            return Response(
                content=resp.content,
                media_type=content_type.split(";")[0].strip() or "application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}"',
                    "Cache-Control": "private, max-age=3600",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to download document from upstream host: {exc}",
        ) from exc
