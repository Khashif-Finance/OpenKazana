from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.schemas.calculator import DailyDividendCalculatorRequest
from app.services.calculators import run_daily_dividend_calculator
from app.services.csv_importer import preview_csv
from app.services.pk_tax import calculate_intl_dividend_wht
from app.services.quant import (
    compute_fundamental_analysis,
    compute_portfolio_quant_analytics,
    compute_technical_indicators,
)
from app.workers.seed import seed_if_empty


@pytest_asyncio.fixture
async def api_client(tmp_path, monkeypatch):
    """Isolated async FastAPI TestClient backed by a fresh per-test SQLite database."""
    db_file = tmp_path / "openkhazana_v1_3_test.db"
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


def test_intl_dividend_wht_us_w8ben_and_ireland():
    gross = Decimal("100.00")
    # US security with active W-8BEN (Pakistan-US tax treaty 15%)
    assert calculate_intl_dividend_wht(gross, us_w8ben_active=True, country="US") == Decimal("15.00")
    # US security without W-8BEN (30% NRA default)
    assert calculate_intl_dividend_wht(gross, us_w8ben_active=False, country="US") == Decimal("30.00")
    # Ireland-domiciled UCITS ETF (0% WHT)
    assert calculate_intl_dividend_wht(gross, us_w8ben_active=False, country="IE") == Decimal("0.00")


def test_daily_dividend_fund_calculator_drip_outperforms_cash():
    res_drip = run_daily_dividend_calculator(
        DailyDividendCalculatorRequest(
            principal_pkr=Decimal("1000000"),
            par_nav=Decimal("50.00"),
            annual_yield_pct=Decimal("18.00"),
            ter_pct=Decimal("0.80"),
            days=365,
            filer_status="ACTIVE_FILER",
            mode="REINVEST",
        )
    )
    res_cash = run_daily_dividend_calculator(
        DailyDividendCalculatorRequest(
            principal_pkr=Decimal("1000000"),
            par_nav=Decimal("50.00"),
            annual_yield_pct=Decimal("18.00"),
            ter_pct=Decimal("0.80"),
            days=365,
            filer_status="ACTIVE_FILER",
            mode="CASH",
        )
    )
    assert res_drip.par_nav == Decimal("50.0000")
    assert res_drip.wht_rate_pct == Decimal("15.00")
    assert res_drip.final_value_pkr > res_cash.final_value_pkr
    assert res_drip.compounding_bonus_vs_cash_pkr > Decimal("0")
    assert res_drip.effective_net_yield_pct > res_cash.effective_net_yield_pct
    assert len(res_drip.monthly_schedule) >= 12


def test_technical_indicators_and_markowitz_quant_engine():
    base_d = date(2025, 1, 1)
    bars = []
    px = 100.0
    for i in range(90):
        px = px * (1.004 if i % 3 != 0 else 0.997)
        bars.append(
            {
                "bar_time": (base_d + timedelta(days=i)).isoformat(),
                "open": px * 0.995,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 150000 + i * 500,
            }
        )

    tech = compute_technical_indicators(bars)
    assert tech["overall_signal"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert tech["latest"]["rsi_14"] is not None
    assert tech["latest"]["sma_20"] is not None
    assert tech["latest"]["sma_50"] is not None
    assert tech["latest"]["macd"] is not None
    assert tech["latest"]["bb_upper"] is not None
    assert len(tech["series"]) == 90

    nav_series = [
        {"date": (base_d + timedelta(days=i)).isoformat(), "nav_per_unit": 100.0 * ((1.0025) ** i)}
        for i in range(60)
    ]
    holdings = [
        {"symbol": "MEBL", "name": "Meezan Bank", "asset_class": "PSX_EQUITY", "market_value_pkr": 600000.0},
        {"symbol": "NVDA", "name": "NVIDIA Corp", "asset_class": "INTL_EQUITY", "market_value_pkr": 400000.0},
    ]
    price_history = {
        "MEBL": [(base_d + timedelta(days=i), 200.0 + i * 0.8 + (i % 4) * 1.2) for i in range(60)],
        "NVDA": [(base_d + timedelta(days=i), 120.0 + i * 0.6 - (i % 3) * 0.9) for i in range(60)],
    }
    bench_bars = [(base_d + timedelta(days=i), 100000.0 + i * 250.0) for i in range(60)]

    quant = compute_portfolio_quant_analytics(
        daily_nav_series=nav_series,
        holdings=holdings,
        price_history_by_symbol=price_history,
        benchmark_symbol="KSE100",
        benchmark_bars=bench_bars,
        risk_free_rate_pct=13.5,
    )
    assert quant["metrics"]["annualized_return_pct"] > 0
    assert quant["metrics"]["beta"] is not None
    assert len(quant["correlation_matrix"]["symbols"]) == 2
    assert len(quant["optimization"]) == 2
    total_sharpe_wt = sum(item["max_sharpe_weight_pct"] for item in quant["optimization"])
    assert 99.5 <= total_sharpe_wt <= 100.5


def test_global_broker_csv_presets():
    ibkr_csv = "Symbol,Date/Time,Quantity,T. Price,Comm/Fee,Code\nNVDA,2026-01-15,5,135.50,1.00,O\n"
    prev = preview_csv(ibkr_csv, preset="ibkr")
    assert prev["suggested_mapping"]["symbol"] == "Symbol"
    assert prev["suggested_mapping"]["unit_price"] == "T. Price"


@pytest.mark.asyncio
async def test_v1_3_api_endpoints_daily_div_intl_and_quant(api_client, monkeypatch):
    from sqlalchemy import select
    from app.models.market import Asset, PriceBar

    client, session_factory = api_client

    # 1. Register a real user account (auto-creates Main Portfolio & Core Market Watch)
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"email": "quant_investor@example.com", "password": "StrongPassword123", "full_name": "Quant Investor"},
    )
    assert reg_res.status_code == 201
    assert reg_res.json()["user"]["is_admin"] is True

    # Seed realistic 1D bars for MEBL, NVDA, and SPX in the isolated test DB so technicals & Markowitz run offline
    base_dt = datetime.now(timezone.utc) - timedelta(days=65)
    async with session_factory() as db:
        for sym, base_px in [("MEBL", Decimal("220.00")), ("NVDA", Decimal("130.00")), ("SPX", Decimal("5800.00"))]:
            a_obj = (await db.execute(select(Asset).where(Asset.symbol == sym))).scalar_one()
            a_obj.last_price = base_px * Decimal("1.12")
            a_obj.prev_close = base_px * Decimal("1.10")
            for i in range(45):
                px = base_px + Decimal(str(i * 0.8 if sym != "SPX" else i * 12.0))
                db.add(
                    PriceBar(
                        asset_id=a_obj.id,
                        timeframe="1D",
                        bar_time=base_dt + timedelta(days=i),
                        open=px * Decimal("0.99"),
                        high=px * Decimal("1.01"),
                        low=px * Decimal("0.98"),
                        close=px,
                        volume=Decimal("1500000"),
                    )
                )
        await db.commit()

    # 2. Verify Daily Dividend Funds filter on /api/v1/market/funds
    funds_res = await client.get("/api/v1/market/funds?daily_dividend_only=true")
    assert funds_res.status_code == 200
    funds = funds_res.json()
    symbols = {f["symbol"] for f in funds}
    assert {"MRAF", "NIDDF", "MICF"}.issubset(symbols)
    mraf = next(f for f in funds if f["symbol"] == "MRAF")
    assert mraf["is_daily_dividend"] is True
    assert float(mraf["constant_par_nav"]) == 50.0
    assert float(mraf["annualized_yield_pct"]) > 0

    # 3. Verify Daily Dividend Calculator endpoint
    calc_res = await client.post(
        "/api/v1/calculators/daily-dividend",
        json={
            "principal_pkr": 500000,
            "par_nav": 50.0,
            "annual_yield_pct": 17.85,
            "ter_pct": 0.85,
            "days": 180,
            "filer_status": "ACTIVE_FILER",
            "mode": "REINVEST",
        },
    )
    assert calc_res.status_code == 200
    calc_data = calc_res.json()
    assert float(calc_data["final_value_pkr"]) > 500000.0
    assert float(calc_data["compounding_bonus_vs_cash_pkr"]) > 0.0

    # 4. Verify Technicals & Fundamental Valuation endpoints for NVDA and MEBL
    tech_res = await client.get("/api/v1/market/assets/NVDA/technicals")
    assert tech_res.status_code == 200
    tech_json = tech_res.json()
    assert tech_json["overall_signal"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert "rsi_14" in tech_json["latest"]
    assert "pivot" in tech_json["latest"]
    assert "vwap_20" in tech_json["latest"]

    detail_res = await client.get("/api/v1/market/stocks/MEBL")
    assert detail_res.status_code == 200
    detail_json = detail_res.json()
    assert detail_json["fundamentals"] is not None
    assert 0 <= detail_json["fundamentals"]["quality_score"] <= 100

    # 5. Verify Live Yahoo Finance Importer endpoint (monkeypatched network call)
    from app.scrapers import intl_stocks

    async def fake_fetch_yfinance(symbol: str, days: int = 180):
        now_dt = datetime.now(timezone.utc)
        return {
            "symbol": symbol.upper(),
            "name": "Advanced Micro Devices, Inc.",
            "asset_class": "INTL_EQUITY",
            "currency": "USD",
            "sector_or_category": "Semiconductors",
            "exchange": "NASDAQ",
            "country": "US",
            "default_wht_rate_pct": Decimal("15.00"),
            "is_shariah_compliant": True,
            "debt_to_assets_pct": Decimal("6.50"),
            "last_price": Decimal("158.40"),
            "prev_close": Decimal("155.10"),
            "change_pct": Decimal("2.13"),
            "volume": Decimal("45000000"),
            "market_cap_mn": Decimal("256000.00"),
            "pe_ratio": Decimal("41.20"),
            "pb_ratio": Decimal("4.50"),
            "eps": Decimal("3.84"),
            "roe_pct": Decimal("12.40"),
            "dividend_yield_pct": Decimal("0.00"),
            "high_52w": Decimal("185.00"),
            "low_52w": Decimal("118.00"),
            "bars": [
                {
                    "bar_time": now_dt - timedelta(days=i),
                    "open": Decimal("150.00"),
                    "high": Decimal("160.00"),
                    "low": Decimal("148.00"),
                    "close": Decimal("158.40"),
                    "volume": Decimal("1000000"),
                }
                for i in range(30, 0, -1)
            ],
            "corporate_actions": [],
        }

    monkeypatch.setattr(intl_stocks, "fetch_yfinance_ticker_snapshot", fake_fetch_yfinance)
    imp_res = await client.post("/api/v1/market/intl/import", json={"symbol": "AMD"})
    assert imp_res.status_code == 200
    imp_json = imp_res.json()
    assert imp_json["symbol"] == "AMD"
    assert imp_json["exchange"] == "NASDAQ"
    assert imp_json["currency"] == "USD"

    # 6. Log real portfolio transactions & test Dual USD/PKR Return Decomposition, Daily Fund Auto-Accrual & Quant Analytics
    pfs = (await client.get("/api/v1/portfolios")).json()
    pf_id = pfs[0]["id"]

    tx_date_60d_ago = (date.today() - timedelta(days=60)).isoformat()
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "PKR_CASH", "tx_type": "OPENING_BALANCE", "tx_date": tx_date_60d_ago, "quantity": "5000000", "unit_price": "1"},
    )
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "MEBL", "tx_type": "BUY", "tx_date": tx_date_60d_ago, "quantity": "1000", "unit_price": "220"},
    )
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "NVDA", "tx_type": "BUY", "tx_date": tx_date_60d_ago, "quantity": "10", "unit_price": "118", "fx_rate_to_pkr": "276"},
    )
    await client.post(
        f"/api/v1/portfolios/{pf_id}/transactions",
        json={"asset_symbol": "MRAF", "tx_type": "BUY", "tx_date": tx_date_60d_ago, "quantity": "10000", "unit_price": "50"},
    )

    sum_res = await client.get(f"/api/v1/portfolios/{pf_id}/summary")
    assert sum_res.status_code == 200
    sum_json = sum_res.json()
    holdings = sum_json["holdings"]

    nvda_h = next((h for h in holdings if h["symbol"] == "NVDA"), None)
    assert nvda_h is not None
    assert nvda_h["currency"] == "USD"
    assert float(nvda_h["avg_cost_native"]) == 118.0
    assert float(nvda_h["current_price_native"]) > 118.0
    assert float(nvda_h["native_return_pct"]) > 0
    assert float(nvda_h["fx_return_pct"]) > 0

    mraf_h = next((h for h in holdings if h["symbol"] == "MRAF"), None)
    assert mraf_h is not None
    assert mraf_h["is_daily_dividend_fund"] is True

    # Run Auto-Accrual on MRAF
    acc_res = await client.post(
        f"/api/v1/portfolios/{pf_id}/accrue-daily-funds",
        json={"asset_symbol": "MRAF", "mode": "REINVEST", "rollup": "MONTHLY"},
    )
    assert acc_res.status_code == 200
    acc_json = acc_res.json()
    assert acc_json["accrued_count"] >= 1
    assert acc_json["days_accrued"] >= 30
    assert float(acc_json["total_reinvested_units"]) > 0

    # Verify Portfolio Quant & Risk Lab endpoint + Rebalance Trade Tickets
    quant_res = await client.get(f"/api/v1/portfolios/{pf_id}/quant?benchmark=SPX&risk_free_rate_pct=13.5")
    assert quant_res.status_code == 200
    quant_json = quant_res.json()
    assert quant_json["benchmark_symbol"] == "SPX"
    assert "sharpe_ratio" in quant_json["metrics"]
    assert "hhi_index" in quant_json["metrics"]
    assert len(quant_json["optimization"]) >= 2
    assert "sharpe_action" in quant_json["optimization"][0]
    assert "sharpe_trade_amount_pkr" in quant_json["optimization"][0]
    assert "sharpe_estimated_units" in quant_json["optimization"][0]

    # 7. Verify Tax Profile W-8BEN update
    tax_res = await client.patch(
        "/api/v1/auth/tax-profile",
        json={"us_w8ben_active": False, "foreign_cgt_rate_pct": 17.5},
    )
    assert tax_res.status_code == 200
    assert tax_res.json()["us_w8ben_active"] is False
    assert float(tax_res.json()["foreign_cgt_rate_pct"]) == 17.5

    # 8. Verify bars & technicals endpoints and list-shaped financial_summary_json CAGR parsing
    bars_res = await client.get("/api/v1/market/assets/MEBL/bars?timeframe=1D&limit=30")
    assert bars_res.status_code == 200
    tech_res = await client.get("/api/v1/market/assets/MEBL/technicals")
    assert tech_res.status_code == 200

    list_fund = compute_fundamental_analysis(
        asset={"asset_class": "INTL_EQUITY", "currency": "USD", "last_price": 150.0},
        profile={
            "pe_ratio": 30.0,
            "pb_ratio": 5.0,
            "eps": 5.0,
            "roe_pct": 25.0,
            "financial_summary_json": [
                {"year": "2023", "revenue_bn": 50.0, "net_profit_bn": 10.0, "eps": 2.5, "net_margin_pct": 20.0},
                {"year": "2024", "revenue_bn": 75.0, "net_profit_bn": 18.0, "eps": 4.0, "net_margin_pct": 24.0},
                {"year": "2025", "revenue_bn": 112.5, "net_profit_bn": 30.0, "eps": 5.0, "net_margin_pct": 26.67},
                {"year": "TTM", "revenue_bn": 120.0, "net_profit_bn": 32.0, "eps": 5.0, "net_margin_pct": 26.67},
            ],
        },
        shariah=None,
    )
    assert list_fund["revenue_cagr_pct"] is not None and list_fund["revenue_cagr_pct"] > 45.0
    assert list_fund["eps_cagr_pct"] is not None and list_fund["eps_cagr_pct"] > 35.0
    assert list_fund["net_margin_trend"] == "EXPANDING"
