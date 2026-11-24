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
from app.models.market import Asset, EquityFinancialSnapshot, MacroIndicator, PriceBar
from app.scrapers.amc_navs import (
    parse_abl_nav_html,
    parse_hbl_nav_html,
    parse_jsil_nav_html,
    parse_mcb_nav_html,
    parse_ubl_nav_html,
)
from app.scrapers.psx_deep import parse_pucars_financial_pdf_text
from app.scrapers.sbp_macro import (
    get_live_risk_free_rate_pct,
    parse_sbp_cpi_html,
    parse_sbp_kibor_pkrv_html,
    parse_sbp_policy_rate_html,
)
from app.services.backtester import compute_monte_carlo_and_stress, run_strategy_backtest
from app.services.deep_fundamentals import (
    compute_altman_z_score,
    compute_dcf_intrinsic_value,
    compute_dupont_roe,
    compute_piotroski_f_score,
)
from app.services.quant import compute_fund_risk_analytics
from app.services.total_return import build_adjusted_bars
from app.workers.seed import seed_if_empty


@pytest.mark.asyncio
async def test_sbp_macro_parsers_and_rf_lookup(tmp_path):
    policy_html = """
    <html><body>
      <div>MPC decided to keep SBP Policy Rate at 12.0% w.e.f. Jan 28, 2025.</div>
    </body></html>
    """
    kibor_html = """
    <html><body>
      <p>Rates as on Feb 14, 2025</p>
      <table>
        <tr><th>Tenor</th><th>Bid</th><th>Offer</th></tr>
        <tr><td>3-M</td><td>11.65</td><td>11.90</td></tr>
        <tr><td>6-M</td><td>11.72</td><td>11.97</td></tr>
        <tr><td>12-M</td><td>11.80</td><td>12.05</td></tr>
      </table>
      <table>
        <tr><th>Tenor</th><th>Yield</th></tr>
        <tr><td>3-M</td><td>11.78</td></tr>
        <tr><td>6-M</td><td>11.82</td></tr>
        <tr><td>12-M</td><td>11.95</td></tr>
      </table>
      <table>
        <tr><th>Tenor</th><th>Yield</th></tr>
        <tr><td>3-Y</td><td>12.10</td></tr>
        <tr><td>5-Y</td><td>12.35</td></tr>
        <tr><td>10-Y</td><td>12.60</td></tr>
      </table>
    </body></html>
    """
    cpi_html = "<html><body><div>Pakistan Headline CPI Inflation (YoY) stood at 4.1% last month.</div></body></html>"

    p_rows = parse_sbp_policy_rate_html(policy_html)
    assert len(p_rows) == 1
    assert p_rows[0]["series_key"] == "SBP_POLICY_RATE"
    assert p_rows[0]["value"] == Decimal("12.0000")

    k_rows = parse_sbp_kibor_pkrv_html(kibor_html)
    keys = {r["series_key"]: r["value"] for r in k_rows}
    assert keys["KIBOR_6M"] == Decimal("11.9700")
    assert keys["TBILL_6M"] == Decimal("11.8200")
    assert keys["PIB_10Y"] == Decimal("12.6000")

    c_rows = parse_sbp_cpi_html(cpi_html)
    assert len(c_rows) == 1
    assert c_rows[0]["series_key"] == "PK_CPI_YOY"
    assert c_rows[0]["value"] == Decimal("4.1000")

    # Verify live DB risk-free lookup for PKR & USD
    db_file = tmp_path / "macro_lookup.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as db:
        db.add_all(
            [
                MacroIndicator(
                    code="TBILL_6M",
                    name="6M T-Bill",
                    country="PK",
                    category="TBILL",
                    value=Decimal("11.8200"),
                    unit="%",
                    as_of_date=date.today(),
                    source="SBP",
                ),
                MacroIndicator(
                    code="US_10Y_TREASURY",
                    name="US 10Y Treasury",
                    country="US",
                    category="GLOBAL",
                    value=Decimal("4.3800"),
                    unit="%",
                    as_of_date=date.today(),
                    source="Yahoo Finance",
                ),
            ]
        )
        await db.commit()
        assert await get_live_risk_free_rate_pct(db, "PKR") == Decimal("11.8200")
        assert await get_live_risk_free_rate_pct(db, "USD") == Decimal("4.3800")
    await engine.dispose()


def test_amc_nav_parsers():
    ubl_html = """
    <table>
      <tr><td colspan="9">Shariah Compliant Funds</td></tr>
      <tr><th>Fund</th><th>Date</th><th>Offer</th><th>Redemption</th><th>TER</th><th>5</th><th>6</th><th>FEL</th><th>BEL</th></tr>
      <tr><td>Al-Ameen Islamic Cash Fund</td><td>15-Feb-2025</td><td>101.2500</td><td>101.2500</td><td>0.75</td><td>-</td><td>-</td><td>0.00</td><td>0.00</td></tr>
      <tr><td>UBL Stock Advantage Fund</td><td>15-Feb-2025</td><td>138.4000</td><td>135.1000</td><td>2.65</td><td>-</td><td>-</td><td>2.50</td><td>0.00</td></tr>
    </table>
    """
    ubl_rows = parse_ubl_nav_html(ubl_html)
    assert len(ubl_rows) == 2
    assert ubl_rows[0]["amc_name"] == "Al-Ameen Islamic (UBL)"
    assert ubl_rows[0]["is_shariah_compliant"] is True
    assert ubl_rows[0]["nav"] == Decimal("101.2500")
    assert ubl_rows[1]["ter_pct"] == Decimal("2.65")

    mcb_html = """
    <table>
      <tr><th>Fund Name</th><th>Date</th><th>NAV</th><th>Offer</th><th>Redemption</th><th>5</th><th>Unit</th><th>FEL</th><th>BEL</th></tr>
      <tr><td>Alhamra Daily Dividend Fund</td><td>15-Feb-2025</td><td>100.0000</td><td>100.0000</td><td>100.0000</td><td>-</td><td>Daily Dividend</td><td>0.00</td><td>0.00</td></tr>
    </table>
    """
    mcb_rows = parse_mcb_nav_html(mcb_html)
    assert len(mcb_rows) == 1
    assert mcb_rows[0]["amc_name"] == "Alhamra Islamic (MCB)"
    assert mcb_rows[0]["is_daily_dividend"] is True
    assert mcb_rows[0]["is_shariah_compliant"] is True

    hbl_html = """
    <table>
      <tr><th>Fund Name</th><th>Offer</th><th>Redemption</th><th>Date</th></tr>
      <tr><td>HBL Islamic Money Market Fund</td><td>102.50</td><td>102.50</td><td>15-Feb-2025</td></tr>
    </table>
    """
    hbl_rows = parse_hbl_nav_html(hbl_html)
    assert len(hbl_rows) == 1
    assert hbl_rows[0]["amc_name"] == "HBL Asset Management"
    assert hbl_rows[0]["nav"] == Decimal("102.5000")

    abl_html = """
    <table>
      <tr><th>Funds</th><th>NAV</th><th>Offer</th><th>Repurchase</th><th>Date</th></tr>
      <tr><td>ABL Islamic Stock Fund</td><td>22.1450</td><td>22.5879</td><td>22.1450</td><td>15-Feb-2025</td></tr>
    </table>
    """
    abl_rows = parse_abl_nav_html(abl_html)
    assert len(abl_rows) == 1
    assert abl_rows[0]["amc_name"] == "ABL Asset Management"
    assert abl_rows[0]["is_shariah_compliant"] is True

    jsil_html = """
    <table>
      <tr><th>Fund</th><th>Date</th><th>Offer</th><th>Redemption</th><th>NAV</th></tr>
      <tr><td>JS Islamic Hybrid Fund of Funds</td><td>15-Feb-2025</td><td>115.20</td><td>112.00</td><td>112.00</td></tr>
    </table>
    """
    jsil_rows = parse_jsil_nav_html(jsil_html)
    assert len(jsil_rows) == 1
    assert jsil_rows[0]["amc_name"] == "JS Investments"
    assert jsil_rows[0]["nav"] == Decimal("112.0000")


@pytest.mark.asyncio
async def test_amc_nav_upsert_persists_profiles_and_daily_bars(tmp_path):
    from app.scrapers.amc_navs import upsert_mutual_fund_rows

    db_file = tmp_path / "amc_upsert.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    rows = parse_ubl_nav_html(
        """
        <table>
          <tr><th>Fund</th><th>Date</th><th>Offer</th><th>Redemption</th><th>TER</th><th>5</th><th>6</th><th>FEL</th><th>BEL</th></tr>
          <tr><td>Al-Ameen Islamic Cash Fund</td><td>15-Feb-2025</td><td>101.2500</td><td>101.2500</td><td>0.75</td><td>-</td><td>-</td><td>0.00</td><td>0.00</td></tr>
        </table>
        """
    )
    async with sf() as db:
        cnt = await upsert_mutual_fund_rows(db, rows)
        assert cnt == 1
    await engine.dispose()


def test_pucars_pdf_balance_sheet_parser_and_deep_math():
    pages = [
        """
        STATEMENT OF FINANCIAL POSITION
        As at December 31, 2024 (Rupees in '000)
        Issued, subscribed and paid-up share capital   5,000,000   5,000,000
        Unappropriated profit                         35,000,000  28,000,000
        Total equity                                  40,000,000  33,000,000
        Long-term financing                            4,000,000   6,000,000
        Short-term borrowings                          1,000,000   1,500,000
        Total current liabilities                     15,000,000  14,000,000
        Total liabilities                             20,000,000  20,000,000
        Cash and bank balances                        12,000,000   9,000,000
        Total current assets                          32,000,000  27,000,000
        Total assets                                  60,000,000  53,000,000
        """,
        """
        STATEMENT OF PROFIT AND LOSS
        For the year ended December 31, 2024 (Rupees in '000)
        Sales - net                                   85,000,000  72,000,000
        Gross profit                                  24,000,000  19,000,000
        Operating profit                              18,000,000  14,000,000
        Finance cost                                     600,000     700,000
        Profit after taxation                         12,000,000   9,000,000
        """,
        """
        STATEMENT OF CASH FLOWS
        For the year ended December 31, 2024 (Rupees in '000)
        Net cash generated from operating activities  16,000,000  11,000,000
        Additions to property, plant and equipment    (3,000,000) (2,500,000)
        """,
    ]
    res = parse_pucars_financial_pdf_text(
        pages,
        "LUCKY_SIM",
        sector="Cement",
        last_price=240.0,
        default_shares_mn=500.0,
        market_cap_mn=120_000.0,
        risk_free_rate_pct=12.0,
    )
    assert res is not None
    assert res["total_assets_mn"] == pytest.approx(60_000.0)
    assert res["shareholders_equity_mn"] == pytest.approx(40_000.0)
    assert res["bvps"] == pytest.approx(80.0)
    assert res["roe_pct"] == pytest.approx(30.0)
    assert res["free_cashflow_mn"] == pytest.approx(13_000.0)
    assert res["dcf_intrinsic_value"] is not None and res["dcf_intrinsic_value"] > 0
    assert res["piotroski_f_score"] is not None and res["piotroski_f_score"] >= 7
    assert res["altman_z_score"] is not None and res["altman_z_score"] > 2.99
    assert res["dupont_breakdown"]["roe_dupont_pct"] == pytest.approx(30.0, abs=0.5)

    # Direct checks on standalone deep_fundamentals helpers
    dcf = compute_dcf_intrinsic_value(free_cashflow_mn=1000.0, shares_mn=100.0, cash_mn=500.0, total_debt_mn=200.0)
    assert dcf is not None and dcf > 50.0
    f_pts, f_list = compute_piotroski_f_score(
        net_income=120, prev_net_income=90, total_assets=600, prev_total_assets=580,
        operating_cashflow=150, long_term_debt=40, prev_long_term_debt=60,
        current_assets=200, current_liabilities=100, prev_current_assets=170, prev_current_liabilities=110,
        net_margin_pct=15.0, prev_net_margin_pct=12.5, revenue=800, prev_revenue=720, shares=100, prev_shares=100,
    )
    assert f_pts == 9 and len(f_list) == 9
    z, zone = compute_altman_z_score(
        sector="Commercial Banks", total_assets_mn=1000, total_liabilities_mn=800
    )
    assert z is None and zone == "NOT_APPLICABLE_FINANCIAL"
    dp = compute_dupont_roe(net_income_mn=12, revenue_mn=100, total_assets_mn=200, shareholders_equity_mn=80)
    assert dp["roe_dupont_pct"] == pytest.approx(15.0)

    # Bank report with Directors' Review ("Rupees in millions" + 12% change col) vs statements in '000
    bank_pages = [
        "DIRECTORS' REVIEW\nBalance Sheet summary Rupees in millions\nTotal Assets 5,397,591 4,806,561 12%",
        "STATEMENT OF FINANCIAL POSITION\nAS AT SEPTEMBER 30, 2026\nASSETS Rupees in 000\nTotal Assets 5,397,590,990 4,806,560,919\nNet assets 260,000,000 230,000,000",
        "STATEMENT OF PROFIT AND LOSS ACCOUNT\nRupees in '000\nTotal income 229,001,391 210,000,000\nProfit after taxation 74,533,718 67,225,794",
        "STATEMENT OF CASH FLOWS\nRupees in '000\nNet cash generated from operating activities 572,053,597 120,000,000",
    ]
    bank_res = parse_pucars_financial_pdf_text(
        bank_pages, "MEBL_SIM", sector="Commercial Banks", last_price=556.25, default_shares_mn=1807.1
    )
    assert bank_res is not None
    assert bank_res["total_assets_mn"] == pytest.approx(5397590.99)
    assert bank_res["net_income_mn"] == pytest.approx(74533.72)
    assert bank_res["roa_pct"] == pytest.approx(1.38)
    assert bank_res["dcf_intrinsic_value"] is None

    # Raw Rupees statement headers + Total Equity and Liabilities exclusion
    raw_pages = [
        "STATEMENT OF FINANCIAL POSITION\nRupees   Rupees\nTOTAL ASSETS 73,473,668,779 52,040,557,875\nTOTAL EQUITY AND LIABILITIES 73,473,668,779 52,040,557,875",
        "STATEMENT OF PROFIT OR LOSS\nRupees   Rupees\nRevenue 13,494,787,140 12,000,000,000\nProfit after taxation 4,993,691,925 4,000,000,000",
        "STATEMENT OF CASH FLOWS\nRupees   Rupees\nNet cash (used in) / generated from Operating Activities 2,837,352,626 1,598,181,927",
    ]
    raw_res = parse_pucars_financial_pdf_text(raw_pages, "SYS_SIM", sector="Technology", last_price=114.57)
    assert raw_res is not None
    assert raw_res["total_assets_mn"] == pytest.approx(73473.67)
    assert raw_res["shareholders_equity_mn"] is None
    assert raw_res["operating_cashflow_mn"] == pytest.approx(2837.35)


def test_total_return_adjustments_and_daily_div():
    base_dt = date(2025, 1, 1)
    bars = [
        {"bar_time": datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0}
        for i in range(60)
    ]
    actions = [
        {"action_type": "CASH_DIVIDEND", "ex_date": base_dt + timedelta(days=30), "payout_per_share": 10.0},
    ]

    price_only = build_adjusted_bars(bars, actions=actions, mode="PRICE")
    gross_tr = build_adjusted_bars(bars, actions=actions, mode="GROSS_TR")
    net_filer = build_adjusted_bars(bars, actions=actions, mode="NET_FILER")
    net_non_filer = build_adjusted_bars(bars, actions=actions, mode="NET_NON_FILER")
    purified = build_adjusted_bars(
        bars,
        actions=actions,
        mode="NET_FILER",
        purification_rate_pct=4.5,
        apply_shariah_purification=True,
    )

    assert price_only[-1]["wealth_index"] == pytest.approx(100.0)
    # Reinvesting Rs 10 dividend into a Rs 100 stock yields higher cumulative wealth than price alone,
    # and Gross > Net Filer (15% WHT) > Net Filer + Purification > Net Non-Filer (30% WHT)
    assert gross_tr[-1]["wealth_index"] > net_filer[-1]["wealth_index"]
    assert net_filer[-1]["wealth_index"] > purified[-1]["wealth_index"]
    assert net_filer[-1]["wealth_index"] > net_non_filer[-1]["wealth_index"]

    # Constant-NAV Daily Dividend compounding (MRAF/MCF style at Rs 100 par NAV, 14% annualized yield)
    dd_gross = build_adjusted_bars(
        bars,
        mode="GROSS_TR",
        is_daily_dividend=True,
        annualized_yield_pct=14.0,
    )
    dd_net = build_adjusted_bars(
        bars,
        mode="NET_FILER",
        is_daily_dividend=True,
        annualized_yield_pct=14.0,
    )
    assert dd_gross[-1]["wealth_index"] > dd_net[-1]["wealth_index"] > 101.0


def test_backtester_and_fund_risk_analytics():
    start = date(2024, 6, 1)
    n_days = 120
    bars_a = [
        {
            "bar_time": (start + timedelta(days=i)).isoformat(),
            "open": 100 + i * 0.25,
            "high": 101 + i * 0.25,
            "low": 99 + i * 0.25,
            "close": 100 + i * 0.25 + (2.0 if i % 5 == 0 else -0.5),
        }
        for i in range(n_days)
    ]
    bars_b = [
        {
            "bar_time": (start + timedelta(days=i)).isoformat(),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0 * ((1.0 + 0.14 / 365.0) ** i),
        }
        for i in range(n_days)
    ]
    bench = [
        {
            "bar_time": (start + timedelta(days=i)).isoformat(),
            "close": 70000 + i * 60.0,
        }
        for i in range(n_days)
    ]

    legs = [
        {"symbol": "MEBL", "asset_class": "PSX_EQUITY", "currency": "PKR", "weight_pct": 60.0, "adj_bars": bars_a},
        {"symbol": "MCF", "asset_class": "MUTUAL_FUND", "currency": "PKR", "weight_pct": 40.0, "adj_bars": bars_b},
    ]

    for strat in ("BUY_AND_HOLD", "MONTHLY_SIP_REBALANCE", "SMA_CROSSOVER"):
        bt = run_strategy_backtest(
            legs,
            strategy=strat,
            benchmark_bars=bench,
            benchmark_symbol="KSE100",
            initial_capital_pkr=1_000_000.0,
            monthly_contribution_pkr=50_000.0 if strat == "MONTHLY_SIP_REBALANCE" else 0.0,
            return_mode="NET_FILER",
            risk_free_rate_pct=12.0,
        )
        assert bt["summary"]["final_value_pkr"] > 0
        assert len(bt["equity_curve"]) == n_days

    mc_stress = compute_monte_carlo_and_stress(
        [{"symbol": "MEBL", "asset_class": "PSX_EQUITY", "currency": "PKR", "market_value_pkr": 1_000_000.0}],
        portfolio_value_pkr=1_000_000.0,
        ann_return_pct=18.0,
        ann_vol_pct=20.0,
        horizon_years=3,
        simulations=500,
        sbp_policy_rate_pct=12.0,
    )
    last_mc = mc_stress["monte_carlo"][-1]
    assert last_mc["month"] == 36
    assert last_mc["p10_pkr"] < last_mc["p50_pkr"] < last_mc["p90_pkr"]
    assert len(mc_stress["stress_tests"]) == 5

    # Mutual fund risk analytics
    fund_risk = compute_fund_risk_analytics(
        build_adjusted_bars(bars_b, mode="GROSS_TR"),
        benchmark_bars=bench,
        risk_free_rate_pct=12.0,
        ter_pct=1.0,
        front_end_load_pct=0.5,
    )
    assert fund_risk["sharpe_ratio"] is not None
    assert fund_risk["fee_drag_5y_pct"] is not None and fund_risk["fee_drag_5y_pct"] > 4.0
    assert len(fund_risk["nav_series"]) == n_days


@pytest_asyncio.fixture
async def v2_api_client(tmp_path, monkeypatch):
    db_file = tmp_path / "openkhazana_v2_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        await seed_if_empty(db)
        db.add(
            MacroIndicator(
                code="TBILL_6M",
                name="PK 6-Month T-Bill",
                country="PK",
                category="TBILL",
                value=Decimal("11.7500"),
                unit="%",
                as_of_date=date.today(),
                source="SBP",
            )
        )
        mebl = (await db.execute(
            Asset.__table__.select().where(Asset.symbol == "MEBL")
        )).mappings().first()
        if mebl:
            db.add(
                EquityFinancialSnapshot(
                    asset_id=mebl["id"],
                    source="PUCARS PDF + PSX DPS",
                    fiscal_period="FY24",
                    bvps=Decimal("95.50"),
                    roe_pct=Decimal("42.00"),
                    roa_pct=Decimal("2.90"),
                    piotroski_f_score=8,
                    dcf_intrinsic_value=Decimal("315.2500"),
                )
            )
        # Seed 45 realistic daily bars for MEBL, MRAF & KSE100 so screener/TR/backtest have history
        now_dt = datetime.now(timezone.utc)
        for sym_bar, base_px in [("MEBL", 220.0), ("MRAF", 100.0), ("KSE100", 78000.0)]:
            row_a = (
                await db.execute(Asset.__table__.select().where(Asset.symbol == sym_bar))
            ).mappings().first()
            if row_a:
                for d_back in range(45, 0, -1):
                    px = Decimal(str(round(base_px * (1.0 + 0.002 * (45 - d_back)), 4)))
                    db.add(
                        PriceBar(
                            asset_id=row_a["id"],
                            timeframe="1D",
                            bar_time=now_dt - timedelta(days=d_back),
                            open=px,
                            high=px * Decimal("1.01"),
                            low=px * Decimal("0.99"),
                            close=px,
                            volume=Decimal("500000"),
                        )
                    )
        await db.commit()

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
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_v2_api_endpoints_end_to_end(v2_api_client: AsyncClient):
    # 1. Macro & Coverage
    macro_res = await v2_api_client.get("/api/v1/market/macro")
    assert macro_res.status_code == 200
    assert any(m["code"] == "TBILL_6M" for m in macro_res.json())

    cov_res = await v2_api_client.get("/api/v1/market/coverage")
    assert cov_res.status_code == 200
    cov = cov_res.json()
    assert cov["psx_total"] >= 1
    assert cov["funds_total"] >= 1
    assert cov["risk_free_rate_pkr_pct"] == pytest.approx(11.75)

    # 2. Screener scan with Piotroski filter
    scan_res = await v2_api_client.get("/api/v1/market/screener/scan?market=PSX&min_piotroski=7")
    assert scan_res.status_code == 200
    rows = scan_res.json()
    assert any(r["symbol"] == "MEBL" for r in rows)

    # 3. Stock detail deep_snapshot + live SBP Risk-Free Rate
    mebl_res = await v2_api_client.get("/api/v1/market/stocks/MEBL")
    assert mebl_res.status_code == 200
    mebl_json = mebl_res.json()
    assert mebl_json["deep_snapshot"] is not None
    assert mebl_json["deep_snapshot"]["piotroski_f_score"] == 8
    assert mebl_json["fundamentals"]["risk_free_rate_pct"] == pytest.approx(11.75)

    # 4. Total-Return Adjusted Bars endpoint
    tr_res = await v2_api_client.get("/api/v1/market/assets/MEBL/bars?timeframe=1D&limit=120&return_mode=NET_FILER")
    assert tr_res.status_code == 200
    assert len(tr_res.json()) > 0

    # 5. Fund Analytics endpoint
    funds_list = (await v2_api_client.get("/api/v1/market/funds")).json()
    assert len(funds_list) > 0
    sym = funds_list[0]["symbol"]
    fund_an_res = await v2_api_client.get(f"/api/v1/market/funds/{sym}/analytics")
    assert fund_an_res.status_code == 200
    fund_an = fund_an_res.json()
    assert fund_an["fee_drag_5y_pct"] is not None
    assert "nav_series" in fund_an
    assert "amc_peers" in fund_an

    # 6. Multi-Asset Strategy Backtest endpoint
    bt_res = await v2_api_client.post(
        "/api/v1/market/backtest",
        json={
            "legs": [{"symbol": "MEBL", "weight_pct": 60}, {"symbol": "MRAF", "weight_pct": 40}],
            "strategy": "BUY_AND_HOLD",
            "benchmark_symbol": "KSE100",
            "initial_capital_pkr": 1000000,
            "monthly_contribution_pkr": 25000,
            "lookback_days": 180,
            "return_mode": "NET_FILER",
        },
    )
    assert bt_res.status_code == 200
    bt_json = bt_res.json()
    assert bt_json["summary"]["final_value_pkr"] > 0
    assert len(bt_json["monte_carlo"]) == 37
    assert len(bt_json["stress_tests"]) == 5
