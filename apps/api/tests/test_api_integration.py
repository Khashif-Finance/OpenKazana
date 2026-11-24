from __future__ import annotations

import io
from decimal import Decimal

import openpyxl
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.market import Asset
from app.models.portfolio import Notification
from app.models.user import User
from app.workers.seed import seed_if_empty


@pytest_asyncio.fixture
async def api_client(tmp_path, monkeypatch):
    """Isolated async FastAPI TestClient backed by a fresh per-test SQLite database."""
    db_file = tmp_path / "openkhazana_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as seed_db:
        await seed_if_empty(seed_db)

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ADMIN_API_KEY", "test-super-secret-admin-key-123456")
    monkeypatch.setenv("SECRET_KEY", "test-jwt-secret-key-must-be-at-least-32-chars")
    monkeypatch.setenv("SEED_DEMO_ON_EMPTY_DB", "false")
    get_settings.cache_clear()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()


def test_production_security_guard():
    with pytest.raises(RuntimeError, match="Insecure SECRET_KEY"):
        Settings(environment="production", secret_key="dev-only-insecure-change-me-in-production-32chars").validate_production_security()

    with pytest.raises(RuntimeError, match="Insecure SECRET_KEY"):
        Settings(environment="production", secret_key="too-short").validate_production_security()

    # Valid production secret should pass cleanly
    Settings(environment="production", secret_key="x" * 40).validate_production_security()


@pytest.mark.asyncio
async def test_real_account_onboarding_multi_tenant_isolation_and_no_demo_data(api_client):
    _client, session_factory = api_client
    transport = ASGITransport(app=app)

    # Demo Sandbox endpoint must be disabled (410 Gone) and no synthetic demo user should exist
    async with session_factory() as db:
        canonical = (await db.execute(select(User).where(User.email == "demo@openkhazana.local"))).scalar_one_or_none()
        assert canonical is None

    async with AsyncClient(transport=transport, base_url="http://testserver") as client_a, AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client_b:
        demo_attempt = await client_a.post("/api/v1/auth/demo")
        assert demo_attempt.status_code == 410

        res_a = await client_a.post(
            "/api/v1/auth/register",
            json={"email": "investor_a@example.com", "password": "StrongPassword123", "full_name": "Investor A"},
        )
        res_b = await client_b.post(
            "/api/v1/auth/register",
            json={"email": "investor_b@example.com", "password": "StrongPassword123", "full_name": "Investor B"},
        )
        assert res_a.status_code == 201
        assert res_b.status_code == 201
        user_a = res_a.json()["user"]
        user_b = res_b.json()["user"]
        assert user_a["id"] != user_b["id"]
        assert user_a["is_demo"] is False
        # First registered permanent user is auto-promoted to admin; second user is regular user
        assert user_a["is_admin"] is True
        assert user_b["is_admin"] is False

        # Create a private portfolio in User A
        created = await client_a.post(
            "/api/v1/portfolios",
            json={"name": "Investor A Private Portfolio", "base_currency": "PKR", "description": "Isolated"},
        )
        assert created.status_code == 201

        # User B must NOT see User A's portfolio
        list_b = await client_b.get("/api/v1/portfolios")
        assert list_b.status_code == 200
        names_b = [p["name"] for p in list_b.json()]
        assert "Investor A Private Portfolio" not in names_b


@pytest.mark.asyncio
async def test_scraper_trigger_rbac_enforcement(api_client, monkeypatch):
    client, _session_factory = api_client

    class _FakePool:
        async def enqueue_job(self, *args, **kwargs):
            return None

        async def aclose(self):
            return None

    async def _fake_create_pool(*args, **kwargs):
        return _FakePool()

    monkeypatch.setattr("app.api.market.create_pool", _fake_create_pool)

    # Unauthenticated -> 403
    anon_res = await client.post("/api/v1/market/scrapers/fx_gold/trigger")
    assert anon_res.status_code == 403

    # First registered user is auto-admin; second registered user is non-admin
    await client.post(
        "/api/v1/auth/register",
        json={"email": "first_owner@example.com", "password": "AdminPassword123", "full_name": "First Owner"},
    )
    admin_res = await client.post("/api/v1/market/scrapers/psx_fundamentals/trigger")
    assert admin_res.status_code == 200
    assert admin_res.json()["scraper_name"] == "psx_fundamentals"

    # Second (non-admin) user -> 403 without X-Admin-Key
    await client.post(
        "/api/v1/auth/register",
        json={"email": "regular@example.com", "password": "UserPassword123", "full_name": "Regular User"},
    )
    reg_res = await client.post("/api/v1/market/scrapers/fx_gold/trigger")
    assert reg_res.status_code == 403

    # Valid X-Admin-Key header -> 200 QUEUED
    key_res = await client.post(
        "/api/v1/market/scrapers/fx_gold/trigger",
        headers={"X-Admin-Key": "test-super-secret-admin-key-123456"},
    )
    assert key_res.status_code == 200
    assert key_res.json()["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_portfolio_crud_tx_edit_benchmark_and_share_links(api_client):
    client, _ = api_client
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "trader@example.com", "password": "Password1234", "full_name": "PSX Trader"},
    )
    assert reg.status_code == 201

    p1 = await client.post("/api/v1/portfolios", json={"name": "Primary Core", "base_currency": "PKR"})
    p2 = await client.post("/api/v1/portfolios", json={"name": "Disposable Fund", "base_currency": "PKR"})
    assert p1.status_code == 201 and p2.status_code == 201
    p1_id = p1.json()["id"]
    p2_id = p2.json()["id"]

    # Update name & delete disposable portfolio
    upd = await client.patch(f"/api/v1/portfolios/{p1_id}", json={"name": "Renamed Core"})
    assert upd.status_code == 200 and upd.json()["name"] == "Renamed Core"
    del_p2 = await client.delete(f"/api/v1/portfolios/{p2_id}")
    assert del_p2.status_code == 200

    # Add BUY transaction & edit it via PATCH
    tx_res = await client.post(
        f"/api/v1/portfolios/{p1_id}/transactions",
        json={
            "asset_symbol": "MEBL",
            "tx_type": "BUY",
            "tx_date": "2025-10-01",
            "quantity": "100",
            "unit_price": "220",
            "notes": "Initial order",
        },
    )
    assert tx_res.status_code == 201
    tx_id = tx_res.json()["id"]

    patch_tx = await client.patch(
        f"/api/v1/portfolios/{p1_id}/transactions/{tx_id}",
        json={"quantity": "150", "unit_price": "210", "notes": "Corrected fill"},
    )
    assert patch_tx.status_code == 200
    assert Decimal(patch_tx.json()["quantity"]) == Decimal("150")
    assert patch_tx.json()["notes"] == "Corrected fill"

    # Benchmark comparison endpoint
    bm_res = await client.get(f"/api/v1/portfolios/{p1_id}/benchmark?symbol=KSE100")
    assert bm_res.status_code == 200
    assert bm_res.json()["benchmark_symbol"] == "KSE100"
    assert isinstance(bm_res.json()["series"], list)

    # Share Links create / list / view / revoke
    link_res = await client.post(f"/api/v1/portfolios/{p1_id}/share-links", json={"hide_amounts": True})
    assert link_res.status_code == 200
    link_data = link_res.json()

    listed = await client.get(f"/api/v1/portfolios/{p1_id}/share-links")
    assert len(listed.json()) == 1

    pub = await client.get(f"/api/v1/share/{link_data['token']}")
    assert pub.status_code == 200
    assert pub.json()["hide_amounts"] is True

    rev = await client.delete(f"/api/v1/portfolios/{p1_id}/share-links/{link_data['id']}")
    assert rev.status_code == 200

    pub_after = await client.get(f"/api/v1/share/{link_data['token']}")
    assert pub_after.status_code == 404


@pytest.mark.asyncio
async def test_custom_asset_cross_tenant_isolation(api_client):
    _client, _ = api_client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c1, AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c2:
        await c1.post(
            "/api/v1/auth/register",
            json={"email": "owner1@example.com", "password": "Password1234", "full_name": "Owner One"},
        )
        await c2.post(
            "/api/v1/auth/register",
            json={"email": "owner2@example.com", "password": "Password1234", "full_name": "Owner Two"},
        )

        created = await c1.post(
            "/api/v1/market/assets/custom",
            json={
                "symbol": "PRIVATE_SUKUK_99",
                "name": "Private Corporate Sukuk Tranche 1",
                "asset_class": "CUSTOM",
                "currency": "PKR",
                "sector_or_category": "Fixed Income",
                "is_shariah_compliant": True,
                "last_price": "15000000",
            },
        )
        assert created.status_code == 201
        asset_id = created.json()["id"]
        assert created.json()["is_custom"] is True

        # Owner1 can see and update custom asset
        search1 = await c1.get("/api/v1/market/assets?q=PRIVATE_SUKUK_99")
        assert any(a["symbol"] == "PRIVATE_SUKUK_99" for a in search1.json())
        upd1 = await c1.patch(f"/api/v1/market/assets/custom/{asset_id}", json={"last_price": "16500000"})
        assert upd1.status_code == 200 and Decimal(upd1.json()["last_price"]) == Decimal("16500000")

        # Owner2 cannot discover, fetch bars, or update Owner1's private asset
        search2 = await c2.get("/api/v1/market/assets?q=PRIVATE_SUKUK_99")
        assert all(a["symbol"] != "PRIVATE_SUKUK_99" for a in search2.json())
        bars2 = await c2.get("/api/v1/market/assets/PRIVATE_SUKUK_99/bars")
        assert bars2.status_code == 404
        upd2 = await c2.patch(f"/api/v1/market/assets/custom/{asset_id}", json={"last_price": "1"})
        assert upd2.status_code == 404


@pytest.mark.asyncio
async def test_fbr_summary_historical_mtm_and_xlsx_import_dedup(api_client):
    from datetime import datetime, timezone

    from app.models.market import PriceBar

    client, session_factory = api_client
    await client.post(
        "/api/v1/auth/register",
        json={"email": "taxuser@example.com", "password": "Password1234", "full_name": "Tax Filers"},
    )
    pf_res = await client.post("/api/v1/portfolios", json={"name": "FBR Ledger", "base_currency": "PKR"})
    pf_id = pf_res.json()["id"]

    # Seed historical June 30, 2025 PriceBar distinct from spot price to verify historical cutoff MTM
    async with session_factory() as db:
        mebl = (await db.execute(select(Asset).where(Asset.symbol == "MEBL"))).scalar_one()
        mebl.last_price = Decimal("220.00")
        db.add(
            PriceBar(
                asset_id=mebl.id,
                timeframe="1D",
                bar_time=datetime(2025, 6, 30, 15, 0, tzinfo=timezone.utc),
                open=Decimal("310.00"),
                high=Decimal("312.00"),
                low=Decimal("305.00"),
                close=Decimal("310.00"),
                volume=Decimal("100000"),
            )
        )
        await db.commit()

    # Deposit cash so Auto-Cash stays positive
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "PKR_CASH", "tx_type": "DEPOSIT", "tx_date": "2024-07-15", "quantity": "100000", "unit_price": "1"},
    )

    # Build .xlsx broker statement in memory & preview via multipart endpoint
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Trade Date", "Symbol", "Side", "Volume", "Price", "Commission"])
    ws.append(["2024-09-10", "MEBL", "BUY", 100, 200.0, 0])
    buf = io.BytesIO()
    wb.save(buf)

    prev_res = await client.post(
        "/api/v1/portfolios/import/preview-file",
        files={"file": ("ktrade.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"preset": "ktrade"},
    )
    assert prev_res.status_code == 200
    prev = prev_res.json()
    assert "normalized_csv_text" in prev

    commit_payload = {
        "portfolio_id": pf_id,
        "csv_text": prev["normalized_csv_text"],
        "mapping": prev["suggested_mapping"],
        "skip_duplicates": True,
        "auto_calculate_charges": True,
    }
    first_commit = await client.post("/api/v1/portfolios/csv/commit", json=commit_payload)
    assert first_commit.status_code == 200
    assert first_commit.json()["inserted"] == 1

    second_commit = await client.post("/api/v1/portfolios/csv/commit", json=commit_payload)
    assert second_commit.status_code == 200
    assert second_commit.json()["inserted"] == 0
    assert second_commit.json()["skipped_duplicates"] == 1

    # Add a FY2024-25 loss trade & verify FY2024-25 + FY2025-26 FBR reports
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "SYS", "tx_type": "BUY", "tx_date": "2024-08-01", "quantity": "10", "unit_price": "500"},
    )
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "SYS", "tx_type": "SELL", "tx_date": "2025-05-01", "quantity": "10", "unit_price": "400"},
    )
    fbr_25 = await client.get(f"/api/v1/portfolios/{pf_id}/fbr-summary?fiscal_year=FY2024-25")
    assert fbr_25.status_code == 200
    data_25 = fbr_25.json()
    assert Decimal(data_25["gross_loss_pkr"]) > Decimal("0")
    assert Decimal(data_25["unused_loss_carryforward_pkr"]) > Decimal("0")
    assert Decimal(data_25["closing_market_value_pkr"]) > Decimal("0")
    mebl_sched = next(r for r in data_25["holdings_cost_schedule"] if r["symbol"] == "MEBL")
    assert Decimal(mebl_sched["market_value_pkr"]) == Decimal("31000.00")


@pytest.mark.asyncio
async def test_watchlist_alert_patch_and_notifications_read_all(api_client):
    client, session_factory = api_client
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "watcher@example.com", "password": "Password1234", "full_name": "Alert Watcher"},
    )
    user_id = reg.json()["user"]["id"]

    wl = await client.post("/api/v1/watchlists", json={"name": "Dividend Picks"})
    wl_id = wl.json()["id"]
    await client.post(f"/api/v1/watchlists/{wl_id}/items/MEBL")
    await client.delete(f"/api/v1/watchlists/{wl_id}/items/MEBL")

    alert = await client.post(
        "/api/v1/alerts",
        json={"asset_symbol": "MEBL", "rule_type": "PRICE_ABOVE", "threshold_value": "250", "channels": ["in_app"]},
    )
    alert_id = alert.json()["id"]
    patched = await client.patch(f"/api/v1/alerts/{alert_id}", json={"is_active": False, "threshold_value": "275"})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False
    assert Decimal(patched.json()["threshold_value"]) == Decimal("275")

    async with session_factory() as db:
        db.add(Notification(user_id=user_id, title="MEBL Target", body="Crossed 250", is_read=False))
        db.add(Notification(user_id=user_id, title="SYS Notice", body="Board meeting", is_read=False))
        await db.commit()

    read_all = await client.post("/api/v1/notifications/read-all")
    assert read_all.status_code == 200
    notifs = (await client.get("/api/v1/notifications")).json()
    assert len(notifs) == 2
    assert all(n["is_read"] is True for n in notifs)

    del_wl = await client.delete(f"/api/v1/watchlists/{wl_id}")
    assert del_wl.status_code == 200
