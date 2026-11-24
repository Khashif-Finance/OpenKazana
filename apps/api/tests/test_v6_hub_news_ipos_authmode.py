from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.market import Announcement, Asset, CorporateAction, NewsArticle, PriceBar, PsxProfile
from app.scrapers.pk_news import build_symbol_sector_matcher, parse_rss_xml, tag_article_symbols_and_sectors
from app.scrapers.psx_ipos import PsxIpoAndListingsScraper
from app.workers.seed import seed_if_empty


def test_settings_strips_trailing_whitespace_and_resolves_auth_modes():
    """Regression test for Windows cmd.exe start-local.bat trailing space bug (`SCRAPERS_ENABLED='true '`)."""
    s = Settings(
        database_url="sqlite+aiosqlite:///./openkhazana_local.db ",
        scrapers_enabled="true ",
        auth_mode=" local ",
        environment="development ",
    )
    assert s.database_url == "sqlite+aiosqlite:///./openkhazana_local.db"
    assert s.scrapers_enabled is True
    assert s.effective_auth_mode == "local"
    assert s.is_local_auth_mode is True

    # Auto mode defaults to local in development, multiuser in production & test
    assert Settings(environment="development", auth_mode="").effective_auth_mode == "local"
    assert Settings(environment="production", auth_mode="", secret_key="x" * 40).effective_auth_mode == "multiuser"
    assert Settings(environment="test", auth_mode="").effective_auth_mode == "multiuser"


def test_rss_xml_parser_and_psx_symbol_sector_tagger():
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Business Recorder</title>
      <item>
        <title>Meezan Bank posts record quarterly profit as SBP holds policy rate</title>
        <link>https://www.brecorder.com/news/40399991</link>
        <description>&lt;p&gt;MEBL earnings surge on Islamic banking deposit growth.&lt;/p&gt;</description>
        <pubDate>Tue, 24 Nov 2026 08:30:00 +0000</pubDate>
      </item>
      <item>
        <title>Lucky Cement expands clinker capacity amid cement dispatch rebound - Profit by Pakistan Today</title>
        <link>https://profit.pakistantoday.com.pk/2026/11/24/lucky-cement-expands/</link>
        <description>APCMA reports strong cement dispatches.</description>
        <pubDate>Tue, 24 Nov 2026 07:15:00 +0000</pubDate>
      </item>
    </channel></rss>"""
    parsed = parse_rss_xml(sample_rss, "brecorder", "Business Recorder", "Markets")
    assert len(parsed) == 2
    assert parsed[0]["title"].startswith("Meezan Bank")
    assert not parsed[1]["title"].endswith("Profit by Pakistan Today")

    fake_assets = [
        Asset(symbol="MEBL", name="Meezan Bank Limited", asset_class="PSX_EQUITY", sector_or_category="COMMERCIAL BANKS"),
        Asset(symbol="LUCK", name="Lucky Cement Limited", asset_class="PSX_EQUITY", sector_or_category="CEMENT"),
    ]
    sym_sec, aliases, upper = build_symbol_sector_matcher(fake_assets)
    syms0, secs0 = tag_article_symbols_and_sectors(parsed[0]["title"], parsed[0]["summary"], sym_sec, aliases, upper)
    assert "MEBL" in syms0
    assert "COMMERCIAL BANKS" in secs0

    syms1, secs1 = tag_article_symbols_and_sectors(parsed[1]["title"], parsed[1]["summary"], sym_sec, aliases, upper)
    assert "LUCK" in syms1
    assert "CEMENT" in secs1


@pytest_asyncio.fixture
async def v6_local_client(tmp_path, monkeypatch):
    db_file = tmp_path / "openkhazana_v6.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        await seed_if_empty(db)
        # Seed a few rich bars, news, announcements & an IPO-linked asset (BFAGRO)
        mebl = (await db.execute(select(Asset).where(Asset.symbol == "MEBL"))).scalar_one()
        luck = (await db.execute(select(Asset).where(Asset.symbol == "LUCK"))).scalar_one()
        now = datetime.now(timezone.utc)
        for idx in range(40):
            dt = now - timedelta(days=40 - idx)
            for a, base_px in ((mebl, 210.0), (luck, 900.0)):
                px = Decimal(str(round(base_px * (1 + idx * 0.004), 2)))
                db.add(PriceBar(asset_id=a.id, timeframe="1D", bar_time=dt, open=px, high=px, low=px, close=px, volume=Decimal("100000")))

        bfagro = Asset(
            symbol="BFAGRO",
            name="Barkat Frisian Agro Limited",
            asset_class="PSX_EQUITY",
            currency="PKR",
            sector_or_category="FOOD & PERSONAL CARE PRODUCTS",
            is_shariah_compliant=True,
            last_price=Decimal("27.30"),
            prev_close=Decimal("26.50"),
            change_pct=Decimal("3.02"),
            volume=Decimal("500000"),
            source="psx_dps",
        )
        db.add(bfagro)
        await db.flush()
        db.add(PsxProfile(asset_id=bfagro.id, market_cap_mn=Decimal("8460"), pe_ratio=Decimal("14.5"), dividend_yield_pct=Decimal("0")))

        db.add(
            NewsArticle(
                url_hash="hash_mebl_news_1",
                title="Meezan Bank quarterly earnings jump 18%",
                summary="MEBL continues Islamic banking leadership.",
                url="https://www.brecorder.com/news/test-mebl",
                source_key="brecorder",
                source_name="Business Recorder",
                category="Corporate",
                matched_symbols_json=["MEBL"],
                matched_sectors_json=[mebl.sector_or_category],
                published_at=now,
            )
        )
        db.add(
            NewsArticle(
                url_hash="hash_bank_sector_2",
                title="SBP policy rate outlook lifts banking deposits across Pakistan",
                summary="Commercial Banks sector margins remain resilient.",
                url="https://www.dawn.com/news/test-banks",
                source_key="dawn",
                source_name="Dawn Business",
                category="Macro",
                matched_symbols_json=[],
                matched_sectors_json=[mebl.sector_or_category],
                published_at=now - timedelta(hours=2),
            )
        )
        db.add(
            Announcement(
                asset_id=mebl.id,
                title="TRANSMISSION OF QUARTERLY REPORT OF MEEZAN BANK LIMITED",
                category="Financial Results",
                published_at=now,
                source_url="https://dps.psx.com.pk/download/document/287198.pdf",
                summary="Quarterly accounts PDF.",
            )
        )
        db.add(
            CorporateAction(
                asset_id=mebl.id,
                action_type="CASH_DIVIDEND",
                ex_date=date.today() + timedelta(days=5),
                payout_per_share=Decimal("7.00"),
                description="Interim Cash Dividend Rs 7.00/sh",
            )
        )
        await db.commit()
        await PsxIpoAndListingsScraper().run(db)

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("SEED_DEMO_ON_EMPTY_DB", "false")
    get_settings.cache_clear()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_v6_local_zero_login_preferences_and_workspace_backup_restore(v6_local_client):
    client = v6_local_client

    cfg = (await client.get("/api/v1/auth/config")).json()
    assert cfg["local_mode"] is True
    assert cfg["auth_mode"] == "local"

    # Zero-login auto-provisions local workspace owner without cookies/tokens
    me_res = await client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "local@openkhazana.local"
    assert me_res.json()["is_admin"] is True

    # Update Shariah-first workspace preferences
    pref_res = await client.patch(
        "/api/v1/auth/preferences",
        json={"shariah_only_default": True, "default_market_view": "PSX", "compact_tables": True},
    )
    assert pref_res.status_code == 200
    assert pref_res.json()["shariah_only_default"] is True

    # Export workspace JSON & idempotently restore it
    exp_res = await client.get("/api/v1/auth/workspace/export")
    assert exp_res.status_code == 200
    backup = exp_res.json()
    assert backup["schema_version"] == "openkhazana-workspace-v1"
    assert backup["preferences"]["shariah_only_default"] is True

    imp_res = await client.post("/api/v1/auth/workspace/import", json=backup)
    assert imp_res.status_code == 200
    assert imp_res.json()["ok"] is True


@pytest.mark.asyncio
async def test_v6_news_ipos_sectors_compare_calendar_and_downloads_hub(v6_local_client):
    client = v6_local_client

    # 1. News Hub + Stock & Sector News
    news = (await client.get("/api/v1/market/news")).json()
    assert news["total"] >= 2
    snews = (await client.get("/api/v1/market/stocks/MEBL/news")).json()
    assert len(snews["stock_news"]) >= 1
    assert len(snews["sector_news"]) >= 1

    # 2. IPOs & Post-IPO live return calculation
    ipos = (await client.get("/api/v1/market/ipos")).json()
    assert ipos["summary"]["total_tracked"] >= 10
    bfagro = next(x for x in ipos["ipos"] if x["symbol"] == "BFAGRO")
    assert bfagro["live_price"] == 27.3
    assert bfagro["post_ipo_return_pct"] == 50.0  # (27.30 - 18.20) / 18.20 * 100

    # 3. Pandas Sector Explorer
    secs = (await client.get("/api/v1/market/sectors?market=PSX")).json()
    assert secs["sector_count"] >= 1
    assert secs["total_stocks"] >= 1
    assert len(secs["sectors"][0]["stocks"]) >= 1

    # 4. Side-by-Side Comparison Matrix
    cmp_res = await client.get("/api/v1/market/compare?symbols=MEBL,LUCK")
    assert cmp_res.status_code == 200
    cmp_data = cmp_res.json()
    assert cmp_data["symbols"] == ["MEBL", "LUCK"]
    assert len(cmp_data["indexed_series"]) >= 20
    assert len(cmp_data["stocks"]) == 2

    # 5. Corporate Calendar (includes upcoming dividend)
    cal = (await client.get("/api/v1/market/calendar")).json()
    assert cal["upcoming_count"] >= 1
    assert any(ev["symbol"] == "MEBL" and ev["event_type"] == "DIVIDEND" for ev in cal["events"])

    # 6. Downloads Hub + SSRF protection on proxy
    dls = (await client.get("/api/v1/market/downloads?symbol=MEBL")).json()
    assert dls["total"] >= 1
    assert dls["items"][0]["proxy_download_url"].startswith("/api/v1/market/downloads/proxy?")

    ssrf_attempt = await client.get("/api/v1/market/downloads/proxy?url=http://169.254.169.254/latest/meta-data/")
    assert ssrf_attempt.status_code == 400
