from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import openpyxl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.market import (
    Asset,
    CorporateAction,
    EquityFinancialSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
)
from app.scrapers.amc_navs import (
    _parse_date,
    parse_atlas_history_table,
    parse_lakson_nav_html,
    parse_nav_history_upload_bytes,
)
from app.scrapers.psx_deep import _merge_pucars_with_ka_fallback
from app.scrapers.sbp_macro import parse_worldbank_cpi_json
from app.services.backtester import run_strategy_backtest


def test_lakson_atlas_date_and_nav_upload_parsers():
    assert _parse_date("19 November, 2026") == date(2026, 11, 19)
    assert _parse_date("2026-11-20 00:00:00") == date(2026, 11, 20)

    lakson_html = """
    <table>
      <tr><th>0</th><th>Fund</th><th>Offer</th><th>Redemption</th><th>Applicable Date</th><th>ter</th></tr>
      <tr><td>519</td><td>Lakson Money Market Fund (LMMF)</td><td>107.8393</td><td>106.6132</td><td>19-Nov-2026</td><td>0.78</td></tr>
      <tr><td>520</td><td>Lakson Islamic Tactical Fund</td><td>125.1000</td><td>122.0000</td><td>19-Nov-2026</td><td>2.15</td></tr>
    </table>
    """
    lrows = parse_lakson_nav_html(lakson_html)
    assert len(lrows) == 2
    assert lrows[0]["amc_name"] == "Lakson Investments"
    assert lrows[0]["nav"] == Decimal("106.6132")
    assert lrows[0]["ter_pct"] == Decimal("0.78")
    assert lrows[1]["is_shariah_compliant"] is True

    atlas_html = """
    <table>
      <tr><th>APPLICABLE DATE</th><th>OFFER</th><th>REDEMPTION</th><th>NAV</th><th>TER-WL</th></tr>
      <tr><td>31-Dec-2025</td><td>2,177.9978</td><td>2,177.9978</td><td>2,177.9978</td><td>4.26</td></tr>
      <tr><td>31-Mar-2026</td><td>1,835.2654</td><td>1,835.2654</td><td>1,835.2654</td><td>4.18</td></tr>
    </table>
    """
    apts = parse_atlas_history_table(atlas_html)
    assert len(apts) == 2
    assert apts[0][0] == date(2025, 12, 31)
    assert apts[0][1] == Decimal("2177.9978")
    assert apts[1][2] == Decimal("4.18")

    # CSV upload parser
    csv_bytes = b"Date,NAV\n2026-01-02,100.12\n2026-01-03,100.45\n2026-01-04,100.89\n"
    c_pts = parse_nav_history_upload_bytes(csv_bytes, "meezan.csv")
    assert len(c_pts) == 3
    assert c_pts[-1] == (date(2026, 1, 4), Decimal("100.8900"))

    # XLSX upload parser
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Applicable Date", "Repurchase Price"])
    ws.append(["15-Jan-2026", 51.25])
    ws.append(["16-Jan-2026", 51.50])
    buf = io.BytesIO()
    wb.save(buf)
    x_pts = parse_nav_history_upload_bytes(buf.getvalue(), "meezan.xlsx")
    assert len(x_pts) == 2
    assert x_pts[0] == (date(2026, 1, 15), Decimal("51.2500"))


def test_worldbank_cpi_and_ka_fallback_merge():
    payload = [
        {"page": 1, "total": 2},
        [
            {"date": "2025", "value": 3.55},
            {"date": "2024", "value": 12.63},
            {"date": "2023", "value": 30.77},
        ],
    ]
    cpi = parse_worldbank_cpi_json(payload)
    assert cpi is not None
    assert cpi["series_key"] == "PK_CPI_YOY"
    assert cpi["value"] == Decimal("3.5500")
    assert len(cpi["history_points"]) == 3

    pucars = {
        "source": "PUCARS PDF + PSX DPS",
        "total_assets_mn": 50000.0,
        "shareholders_equity_mn": None,
        "roe_pct": None,
        "extra_metrics": {"scale_detected": "PKR_000"},
    }
    ka = {
        "source": "PSX DPS + Yahoo Finance (.KA)",
        "total_assets_mn": 49500.0,
        "shareholders_equity_mn": 22000.0,
        "roe_pct": 18.5,
        "piotroski_f_score": 7,
    }
    merged = _merge_pucars_with_ka_fallback(pucars, ka)
    assert merged is not None
    # Official PUCARS value stays primary, missing fields filled from .KA & transparently tagged
    assert merged["total_assets_mn"] == 50000.0
    assert merged["shareholders_equity_mn"] == 22000.0
    assert merged["roe_pct"] == 18.5
    assert merged["source"] == "PUCARS PDF + Yahoo Finance (.KA)"
    assert _merge_pucars_with_ka_fallback(None, ka)["source"] == "PSX DPS + Yahoo Finance (.KA)"


def test_backtester_real_usd_and_real_cpi_deflators():
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # Asset grows +30% in PKR over 250 days while USD/PKR weakens from 250 -> 300 (+20% FX depreciation)
    asset_bars = [
        {"bar_time": base + timedelta(days=i), "open": 100 + i * 0.12, "high": 100 + i * 0.12, "low": 100 + i * 0.12, "close": 100 + i * 0.12}
        for i in range(251)
    ]
    fx_bars = [
        {"bar_time": base + timedelta(days=i), "close": 250.0 + (i / 250.0) * 50.0}
        for i in range(251)
    ]
    legs = [{"symbol": "MEBL", "weight_pct": 100.0, "currency": "PKR", "adj_bars": asset_bars}]

    nom = run_strategy_backtest(legs, initial_capital_pkr=1_000_000.0, real_return_mode="NOMINAL_PKR", return_mode="PRICE")
    real_usd = run_strategy_backtest(
        legs, initial_capital_pkr=1_000_000.0, real_return_mode="REAL_USD", return_mode="PRICE", usd_pkr_bars=fx_bars
    )
    real_cpi = run_strategy_backtest(
        legs, initial_capital_pkr=1_000_000.0, real_return_mode="REAL_CPI_PKR", return_mode="PRICE", cpi_annual_pct=12.0
    )

    assert nom["display_currency"] == "PKR"
    assert real_usd["display_currency"] == "USD"
    # +30% nominal PKR with +20% USD/PKR depreciation -> ~+8.3% real USD return
    assert nom["summary"]["total_return_pct"] > 28.0
    assert 6.0 < real_usd["summary"]["total_return_pct"] < 11.0
    assert real_cpi["summary"]["total_return_pct"] < nom["summary"]["total_return_pct"]


@pytest.mark.asyncio
async def test_v3_endpoints_peer_valuation_div_radar_scatter_import_and_regime(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()
    db_file = tmp_path / "v3_api.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with sf() as db:
        mebl = Asset(
            symbol="MEBL",
            name="Meezan Bank Limited",
            asset_class="PSX_EQUITY",
            currency="PKR",
            sector_or_category="Commercial Banks",
            is_shariah_compliant=True,
            last_price=Decimal("250.00"),
            prev_close=Decimal("248.00"),
            change_pct=Decimal("0.81"),
            volume=Decimal("2000000"),
        )
        ubl = Asset(
            symbol="UBL",
            name="United Bank Limited",
            asset_class="PSX_EQUITY",
            currency="PKR",
            sector_or_category="Commercial Banks",
            is_shariah_compliant=False,
            last_price=Decimal("320.00"),
            prev_close=Decimal("318.00"),
            change_pct=Decimal("0.63"),
            volume=Decimal("1500000"),
        )
        mcb = Asset(
            symbol="MCB",
            name="MCB Bank Limited",
            asset_class="PSX_EQUITY",
            currency="PKR",
            sector_or_category="Commercial Banks",
            is_shariah_compliant=False,
            last_price=Decimal("240.00"),
            prev_close=Decimal("239.00"),
            change_pct=Decimal("0.42"),
            volume=Decimal("900000"),
        )
        mif = Asset(
            symbol="MIF",
            name="Meezan Islamic Fund",
            asset_class="MUTUAL_FUND",
            currency="PKR",
            sector_or_category="Shariah Equity",
            is_shariah_compliant=True,
            last_price=Decimal("150.00"),
            prev_close=Decimal("149.00"),
            change_pct=Decimal("0.67"),
            volume=Decimal("0"),
        )
        fx = Asset(
            symbol="USD_PKR_INTERBANK",
            name="USD/PKR",
            asset_class="FX",
            currency="PKR",
            sector_or_category="Currencies",
            last_price=Decimal("278.00"),
            prev_close=Decimal("278.00"),
        )
        db.add_all([mebl, ubl, mcb, mif, fx])
        await db.flush()

        db.add_all(
            [
                PsxProfile(asset_id=mebl.id, market_cap_mn=Decimal("450000"), pe_ratio=Decimal("6.50"), pb_ratio=Decimal("2.10"), eps=Decimal("38.46"), roe_pct=Decimal("32.00"), dividend_yield_pct=Decimal("9.50"), is_kse100=True, is_kmi30=True, financial_summary_json=[{"year": "2024", "revenue": 200000, "profit_after_tax": 70000}]),
                PsxProfile(asset_id=ubl.id, market_cap_mn=Decimal("390000"), pe_ratio=Decimal("9.00"), pb_ratio=Decimal("1.90"), eps=Decimal("35.50"), roe_pct=Decimal("21.00"), dividend_yield_pct=Decimal("11.00"), is_kse100=True, is_kmi30=False),
                PsxProfile(asset_id=mcb.id, market_cap_mn=Decimal("285000"), pe_ratio=Decimal("8.50"), pb_ratio=Decimal("1.70"), eps=Decimal("28.20"), roe_pct=Decimal("20.00"), dividend_yield_pct=Decimal("10.00"), is_kse100=True, is_kmi30=False),
                EquityFinancialSnapshot(asset_id=mebl.id, total_assets_mn=Decimal("3800000"), shareholders_equity_mn=Decimal("220000"), roe_pct=Decimal("32.00"), piotroski_f_score=7, source="PUCARS PDF + PSX DPS"),
                MutualFundProfile(asset_id=mif.id, amc_name="Al Meezan", fund_category="Shariah Equity", ter_pct=Decimal("2.25"), front_end_load_pct=Decimal("2.00"), is_daily_dividend=False),
                MacroIndicator(code="SBP_POLICY_RATE", name="SBP Policy Rate", country="PK", category="POLICY", value=Decimal("12.00"), unit="%", as_of_date=date(2026, 10, 27), source="SBP", history_json=[{"date": "2024-06-10", "value": 20.5}, {"date": "2026-10-27", "value": 12.0}]),
                MacroIndicator(code="PK_CPI_YOY", name="Pakistan CPI YoY", country="PK", category="INFLATION", value=Decimal("4.50"), unit="%", as_of_date=date(2026, 6, 30), source="World Bank", history_json=[{"date": "2024-06-30", "value": 12.6}, {"date": "2025-06-30", "value": 4.5}]),
            ]
        )

        today_d = date.today()
        for yr_off in (0, 1, 2):
            db.add(
                CorporateAction(
                    asset_id=mebl.id,
                    action_type="CASH_DIVIDEND",
                    ex_date=date(today_d.year - yr_off, 3, 15),
                    payout_per_share=Decimal("8.00"),
                    description="Cash Dividend 80%",
                )
            )

        base_dt = datetime.now(timezone.utc) - timedelta(days=60)
        for i in range(60):
            dt = base_dt + timedelta(days=i)
            db.add(PriceBar(asset_id=mebl.id, timeframe="1D", bar_time=dt, open=Decimal(str(220 + i * 0.5)), high=Decimal(str(222 + i * 0.5)), low=Decimal(str(219 + i * 0.5)), close=Decimal(str(220 + i * 0.5)), volume=Decimal("100000")))
            db.add(PriceBar(asset_id=fx.id, timeframe="1D", bar_time=dt, open=Decimal("275"), high=Decimal("278"), low=Decimal("275"), close=Decimal(str(275 + i * 0.05)), volume=Decimal("0")))
        await db.commit()

    async def _override_db():
        async with sf() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Stock detail returns peer_valuation & dividend_history
            r_stock = await client.get("/api/v1/market/stocks/MEBL")
            assert r_stock.status_code == 200
            sd = r_stock.json()
            assert sd["peer_valuation"]["sector_median_pe"] == pytest.approx(8.5)
            assert sd["peer_valuation"]["pe_vs_sector_pct"] < 0
            assert sd["dividend_history"]["streak_years"] == 3

            # 2. Screener index_only + below_sector_pe + min_div_streak
            r_scr = await client.get("/api/v1/market/screener/scan?market=PSX&index_only=KSE100&below_sector_pe=true&min_div_streak=3")
            assert r_scr.status_code == 200
            rows = r_scr.json()
            assert [r["symbol"] for r in rows] == ["MEBL"]
            assert rows[0]["div_streak_years"] == 3
            assert rows[0]["pe_vs_sector_pct"] < 0

            # 3. Upload CSV NAV history for MIF, then check /funds/scatter
            csv_lines = ["Date,NAV"] + [
                f"{(date.today() - timedelta(days=40 - i)).isoformat()},{130.0 + i * 0.5:.4f}"
                for i in range(35)
            ]
            r_up = await client.post(
                "/api/v1/market/funds/MIF/import-history",
                files={"file": ("mif_nav.csv", "\n".join(csv_lines).encode("utf-8"), "text/csv")},
            )
            assert r_up.status_code == 200
            assert r_up.json()["bars_inserted"] == 35

            r_scat = await client.get("/api/v1/market/funds/scatter")
            assert r_scat.status_code == 200
            assert any(p["symbol"] == "MIF" for p in r_scat.json()["points"])

            # 4. Macro Regime endpoint
            r_reg = await client.get("/api/v1/market/macro/regime")
            assert r_reg.status_code == 200
            assert r_reg.json()["latest_real_rate_pct"] == pytest.approx(7.5)
            assert len(r_reg.json()["series"]) >= 12

            # 5. Expanded Coverage endpoint
            r_cov = await client.get("/api/v1/market/coverage")
            assert r_cov.status_code == 200
            cov = r_cov.json()
            assert cov["kse100_total"] == 3
            assert cov["kse100_deep_fundamentals"] == 1
            assert cov["amcs_with_history"] == 1

            # 6. Backtest REAL_USD mode
            r_bt = await client.post(
                "/api/v1/market/backtest",
                json={"legs": [{"symbol": "MEBL", "weight_pct": 100}], "real_return_mode": "REAL_USD", "return_mode": "PRICE"},
            )
            assert r_bt.status_code == 200
            assert r_bt.json()["display_currency"] == "USD"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
