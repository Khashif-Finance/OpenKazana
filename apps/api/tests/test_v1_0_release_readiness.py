"""
OpenKhazana v1.0.0 Production & Flagship Readiness Test Suite:
  - Sliding-window rate limiter enforcement & window reset
  - DB-aware `/health` check & v1.0.0 version reporting
  - KSE-100 / KMI-30 Index Point Contributors & Sector Attribution (`/api/v1/market/index-movers`)
  - 12-Month Portfolio Passive Income & Dividend Snowball Calendar (`/api/v1/portfolios/{id}/dividend-calendar`)
  - Admin SaaS Control API (`/api/v1/admin/overview`, `/settings` public signup toggle, `/users/{id}` RBAC & last-admin guard)
  - Multi-user cross-tenant research bookmark isolation
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cache import api_cache
from app.core.config import get_settings, reset_runtime_overrides
from app.core.database import Base, get_db
from app.core.rate_limit import SlidingWindowRateLimiter
from app.main import app
from app.models.market import (
    Asset,
    CorporateAction,
    MutualFundProfile,
    PsxProfile,
    ShariahMetric,
)
from app.models.portfolio import Portfolio, Transaction


def test_sliding_window_rate_limiter():
    rl = SlidingWindowRateLimiter()
    ok1, _ = rl.check("login:1.2.3.4", limit=2, window_seconds=60.0)
    ok2, _ = rl.check("login:1.2.3.4", limit=2, window_seconds=60.0)
    ok3, retry_after = rl.check("login:1.2.3.4", limit=2, window_seconds=60.0)
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert retry_after >= 1

    # Different IP is unaffected
    ok_other, _ = rl.check("login:5.6.7.8", limit=2, window_seconds=60.0)
    assert ok_other is True


@pytest.mark.asyncio
async def test_v1_0_index_movers_dividend_calendar_admin_and_bookmark_isolation(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "multiuser")
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()
    api_cache.invalidate_prefix("")
    reset_runtime_overrides()
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with session_factory() as db:
            kse = Asset(
                symbol="KSE100",
                name="KSE-100 Index",
                asset_class="PSX_INDEX",
                sector_or_category="Index",
                currency="PKR",
                last_price=Decimal("120000.00"),
                prev_close=Decimal("119000.00"),
                change_pct=Decimal("0.84"),
            )
            ogdc = Asset(
                symbol="OGDC",
                name="Oil & Gas Development Company",
                asset_class="PSX_EQUITY",
                sector_or_category="Oil & Gas Exploration",
                currency="PKR",
                last_price=Decimal("210.00"),
                prev_close=Decimal("200.00"),
                change_pct=Decimal("5.00"),
                is_shariah_compliant=True,
            )
            mebl = Asset(
                symbol="MEBL",
                name="Meezan Bank Limited",
                asset_class="PSX_EQUITY",
                sector_or_category="Commercial Banks",
                currency="PKR",
                last_price=Decimal("240.00"),
                prev_close=Decimal("250.00"),
                change_pct=Decimal("-4.00"),
                is_shariah_compliant=True,
            )
            mcf = Asset(
                symbol="MCF",
                name="Meezan Cash Fund",
                asset_class="MUTUAL_FUND",
                sector_or_category="Shariah Compliant Money Market",
                currency="PKR",
                last_price=Decimal("50.00"),
                prev_close=Decimal("50.00"),
                change_pct=Decimal("0.00"),
                is_shariah_compliant=True,
            )
            usd = Asset(
                symbol="USD_PKR_INTERBANK",
                name="USD/PKR",
                asset_class="FX",
                sector_or_category="FX",
                currency="PKR",
                last_price=Decimal("278.50"),
                prev_close=Decimal("278.50"),
                change_pct=Decimal("0.00"),
            )
            db.add_all([kse, ogdc, mebl, mcf, usd])
            await db.flush()

            db.add_all(
                [
                    PsxProfile(
                        asset_id=ogdc.id,
                        market_cap_mn=Decimal("900000"),
                        free_float_pct=Decimal("20"),
                        dividend_yield_pct=Decimal("6.50"),
                        is_kse100=True,
                        is_kmi30=True,
                    ),
                    PsxProfile(
                        asset_id=mebl.id,
                        market_cap_mn=Decimal("430000"),
                        free_float_pct=Decimal("25"),
                        dividend_yield_pct=Decimal("10.00"),
                        is_kse100=True,
                        is_kmi30=True,
                    ),
                    ShariahMetric(
                        asset_id=ogdc.id,
                        passed=True,
                        purification_rate_pct=Decimal("2.00"),
                    ),
                    CorporateAction(
                        asset_id=ogdc.id,
                        action_type="CASH_DIVIDEND",
                        ex_date=date.today() - timedelta(days=45),
                        payout_per_share=Decimal("5.00"),
                        description="Interim Cash Dividend",
                    ),
                    CorporateAction(
                        asset_id=ogdc.id,
                        action_type="CASH_DIVIDEND",
                        ex_date=date.today() - timedelta(days=180),
                        payout_per_share=Decimal("4.00"),
                        description="Final Cash Dividend",
                    ),
                    MutualFundProfile(
                        asset_id=mcf.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Money Market",
                        ter_pct=Decimal("1.10"),
                        is_daily_dividend=True,
                        annualized_yield_pct=Decimal("12.00"),
                        return_1y_pct=Decimal("12.00"),
                    ),
                ]
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. KSE-100 Index Point Contributors
            r_mov = await client.get("/api/v1/market/index-movers?index_symbol=KSE100")
            assert r_mov.status_code == 200
            mov = r_mov.json()
            assert mov["index_symbol"] == "KSE100"
            assert mov["constituents_evaluated"] == 2
            assert mov["top_positive"][0]["symbol"] == "OGDC"
            assert mov["top_positive"][0]["points"] > 0
            assert mov["top_negative"][0]["symbol"] == "MEBL"
            assert mov["top_negative"][0]["points"] < 0
            assert len(mov["sector_attribution"]) == 2

            # 2. Register Admin (1st user) & Regular User (2nd user)
            r_u1 = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "admin1@example.com",
                    "password": "AdminPassword123!",
                    "full_name": "Khashif Admin",
                    "filer_status": "ACTIVE_FILER",
                },
            )
            assert r_u1.status_code == 201
            admin_token = r_u1.json()["access_token"]
            admin_id = r_u1.json()["user"]["id"]
            assert r_u1.json()["user"]["is_admin"] is True

            r_u2 = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "member2@example.com",
                    "password": "MemberPassword123!",
                    "full_name": "Member Two",
                    "filer_status": "ACTIVE_FILER",
                },
            )
            assert r_u2.status_code == 201
            user2_token = r_u2.json()["access_token"]
            user2_id = r_u2.json()["user"]["id"]
            assert r_u2.json()["user"]["is_admin"] is False

            # 3. 12-Month Portfolio Passive Income & Dividend Snowball Calendar
            async with session_factory() as db:
                p_row = (
                    await db.execute(Portfolio.__table__.select().where(Portfolio.user_id == admin_id))
                ).first()
                port_id = p_row.id
                db.add_all(
                    [
                        Transaction(
                            portfolio_id=port_id,
                            asset_id=ogdc.id,
                            tx_type="BUY",
                            tx_date=date.today() - timedelta(days=300),
                            quantity=Decimal("1000"),
                            unit_price=Decimal("180.00"),
                            gross_amount=Decimal("180000.00"),
                            fx_rate_to_pkr=Decimal("1"),
                        ),
                        Transaction(
                            portfolio_id=port_id,
                            asset_id=mcf.id,
                            tx_type="BUY",
                            tx_date=date.today() - timedelta(days=200),
                            quantity=Decimal("2000"),
                            unit_price=Decimal("50.00"),
                            gross_amount=Decimal("100000.00"),
                            fx_rate_to_pkr=Decimal("1"),
                        ),
                    ]
                )
                await db.commit()

            r_cal = await client.get(
                f"/api/v1/portfolios/{port_id}/dividend-calendar",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert r_cal.status_code == 200
            cal = r_cal.json()
            assert len(cal["months"]) == 12
            # OGDC 1000 * (5 + 4) = Rs 9,000 gross + MCF Rs 100,000 * 12% = Rs 12,000 gross -> Rs 21,000 total gross
            assert cal["annual_gross_pkr"] == pytest.approx(21000.0, abs=50.0)
            assert cal["annual_wht_pkr"] > 0
            assert cal["annual_purification_pkr"] > 0
            assert cal["annual_net_pkr"] < cal["annual_gross_pkr"]
            assert cal["monthly_avg_net_pkr"] > 0

            # 4. Admin Overview, RBAC, Public Signup Toggle & Last-Admin Guard
            r_adm_deny = await client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": f"Bearer {user2_token}"},
            )
            assert r_adm_deny.status_code == 403

            r_adm_ok = await client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert r_adm_ok.status_code == 200
            adm_data = r_adm_ok.json()
            assert adm_data["version"] == "1.0.0"
            assert adm_data["database_connected"] is True
            assert adm_data["table_counts"]["users"] == 2

            # Cannot demote last active admin
            r_last_adm = await client.patch(
                f"/api/v1/admin/users/{admin_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"is_admin": False},
            )
            assert r_last_adm.status_code == 400

            # Disable public registration live -> 3rd user signup blocked (403)
            r_lock = await client.patch(
                "/api/v1/admin/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"allow_public_registration": False},
            )
            assert r_lock.status_code == 200
            assert r_lock.json()["allow_public_registration"] is False

            r_u3_blocked = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "blocked3@example.com",
                    "password": "BlockedPassword123!",
                    "full_name": "Blocked User",
                    "filer_status": "ACTIVE_FILER",
                },
            )
            assert r_u3_blocked.status_code == 403

            # Admin can disable user2 cleanly
            r_dis = await client.patch(
                f"/api/v1/admin/users/{user2_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"is_active": False},
            )
            assert r_dis.status_code == 200
            assert r_dis.json()["is_active"] is False

            # Re-enable user2 to test cross-tenant research bookmark isolation
            await client.patch(
                f"/api/v1/admin/users/{user2_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"is_active": True},
            )
            r_bm = await client.post(
                "/api/v1/market/research/bookmark",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "title": "Admin Private OGDC Note",
                    "url": "https://imsecurities.com.pk/reports/ogdc-private.pdf",
                    "symbols": ["OGDC"],
                },
            )
            assert r_bm.status_code == 201
            bm_id = r_bm.json()["id"]

            r_cross_del = await client.delete(
                f"/api/v1/market/research/bookmark/{bm_id}",
                headers={"Authorization": f"Bearer {user2_token}"},
            )
            assert r_cross_del.status_code == 403
    finally:
        reset_runtime_overrides()
        get_settings.cache_clear()
        app.dependency_overrides.pop(get_db, None)
        api_cache.invalidate_prefix("")
        await test_engine.dispose()
