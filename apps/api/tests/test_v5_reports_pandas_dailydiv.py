from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.market import (
    Asset,
    EquityFinancialPeriod,
    EquityFinancialSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
)
from app.scrapers.financial_reports import parse_company_report_pdf_text
from app.services.pandas_analytics import (
    analyze_financial_statements_pandas,
    compute_cross_sectional_factor_ranks_pandas,
    compute_market_heatmap_pandas,
    compute_seasonality_and_rolling_pandas,
    compute_technical_summary_gauge_pandas,
    financial_periods_to_csv,
)


def test_pucars_annual_report_pdf_parser_and_pandas_financials():
    pages = [
        """
        DIRECTORS' REPORT TO THE SHAREHOLDERS
        Lucky Cement Limited achieved record consolidated revenue of Rs. 210,500 million during FY2025, an increase of 18.4% over FY2024 driven by export dispatches.
        The Board of Directors has recommended a final cash dividend of Rs. 18.00 per share (180%) for the year ended June 30, 2025.
        Capacity expansion of 3.15 million tons per annum was commissioned ahead of schedule with 42% solar renewable power integration.
        """,
        """
        INDEPENDENT AUDITOR'S REPORT
        To the members of Lucky Cement Limited
        Opinion
        In our opinion, the financial statements give a true and fair view of the financial position as at June 30, 2025.
        A.F. Ferguson & Co.
        Chartered Accountants
        """,
        """
        STATEMENT OF PROFIT OR LOSS
        FOR THE YEAR ENDED JUNE 30, 2025
        (Rupees in '000)                           Note        2025            2024
        Net sales                                  24   210,500,000     177,800,000
        Cost of sales                              25   142,000,000     126,500,000
        Gross profit                                     68,500,000      51,300,000
        Operating profit                                 54,200,000      41,100,000
        Finance cost                               28     4,200,000       5,100,000
        Profit before taxation                           50,000,000      36,000,000
        Taxation                                   30    14,000,000      11,000,000
        Profit after tax                                 36,000,000      25,000,000
        Basic and diluted earnings per share (Rs.)           122.87           85.32
        """,
        """
        STATEMENT OF FINANCIAL POSITION
        AS AT JUNE 30, 2025
        (Rupees in '000)                           Note        2025            2024
        Total current assets                            112,000,000      95,000,000
        Cash and bank balances                           42,500,000      31,000,000
        Stock-in-trade                                   24,000,000      21,000,000
        Trade debts                                      14,500,000      12,000,000
        Total assets                                    320,000,000     285,000,000
        Total current liabilities                        52,000,000      48,000,000
        Long-term financing                              30,000,000      35,000,000
        Short-term borrowings                            10,000,000      12,000,000
        Total liabilities                               105,000,000     102,000,000
        Total shareholders' equity                      215,000,000     183,000,000
        """,
        """
        STATEMENT OF CASH FLOWS
        FOR THE YEAR ENDED JUNE 30, 2025
        (Rupees in '000)                           Note        2025            2024
        Net cash generated from operating activities     44,000,000      31,500,000
        Fixed capital expenditure                        11,000,000       9,500,000
        Dividends paid                                    5,200,000       4,000,000
        """,
    ]

    parsed = parse_company_report_pdf_text(
        pages,
        symbol="LUCK",
        sector="Cement",
        filename_hint="Annual_Report_2025.pdf",
        source_label="PUCARS Annual PDF",
    )
    assert len(parsed) == 2
    fy25 = next(p for p in parsed if p["period_label"] == "FY2025")
    fy24 = next(p for p in parsed if p["period_label"] == "FY2024")

    assert fy25["revenue_mn"] == pytest.approx(210500.0, rel=1e-3)
    assert fy25["net_profit_mn"] == pytest.approx(36000.0, rel=1e-3)
    assert fy25["total_assets_mn"] == pytest.approx(320000.0, rel=1e-3)
    assert fy25["shareholders_equity_mn"] == pytest.approx(215000.0, rel=1e-3)
    assert fy25["total_debt_mn"] == pytest.approx(40000.0, rel=1e-3)
    assert fy25["operating_cashflow_mn"] == pytest.approx(44000.0, rel=1e-3)
    assert fy25["free_cashflow_mn"] == pytest.approx(33000.0, rel=1e-3)
    assert fy25["eps"] == pytest.approx(122.87, rel=1e-3)
    assert "A.F. Ferguson" in (fy25["auditor_opinion"] or "")
    assert "Unmodified" in (fy25["auditor_opinion"] or "")
    assert fy25["dividend_bonus_note"] is not None
    assert len(fy25["directors_highlights_json"]) >= 2

    assert fy24["revenue_mn"] == pytest.approx(177800.0, rel=1e-3)
    assert fy24["net_profit_mn"] == pytest.approx(25000.0, rel=1e-3)

    # Feed straight into Pandas 3-Statement Analyzer
    analysis = analyze_financial_statements_pandas(
        [{**p, "period_end_date": p["period_end_date"].isoformat()} for p in parsed],
        is_financial=False,
    )
    assert len(analysis["annual_rows"]) == 2
    row25 = analysis["annual_rows"][-1]
    assert row25["revenue_growth_pct"] == pytest.approx(18.39, rel=1e-2)
    assert row25["net_profit_growth_pct"] == pytest.approx(44.0, rel=1e-2)
    assert row25["gross_margin_pct"] == pytest.approx(32.54, rel=1e-2)
    assert row25["cash_conversion_x"] == pytest.approx(1.22, rel=1e-2)
    assert analysis["earnings_quality"]["quality_verdict"] == "HIGH_CASH_BACKED"
    assert "FY2025" in financial_periods_to_csv(analysis["annual_rows"])


def test_pandas_technical_gauge_seasonality_factors_and_heatmap():
    # Generate 240 uptrend daily OHLCV bars across multiple months/years
    start = date(2025, 1, 2)
    bars = []
    bench = []
    px = 100.0
    bpx = 70000.0
    for i in range(260):
        d = start + timedelta(days=int(i * 1.4))
        px = px * (1.002 + (0.004 if i % 7 < 4 else -0.002))
        bpx = bpx * (1.001 + (0.002 if i % 5 < 3 else -0.001))
        bars.append(
            {
                "bar_time": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                "open": round(px * 0.995, 2),
                "high": round(px * 1.01, 2),
                "low": round(px * 0.99, 2),
                "close": round(px, 2),
                "volume": 500000,
            }
        )
        bench.append({"bar_time": datetime(d.year, d.month, d.day, tzinfo=timezone.utc), "close": round(bpx, 2)})

    gauge = compute_technical_summary_gauge_pandas(bars)
    assert gauge["available"] is True
    assert gauge["overall_signal"] in {"BUY", "STRONG_BUY"}
    assert len(gauge["ma_summary"]["items"]) == 12
    assert len(gauge["oscillator_summary"]["items"]) >= 7

    seas = compute_seasonality_and_rolling_pandas(bars, benchmark_bars=bench)
    assert len(seas["years"]) >= 1
    assert len(seas["monthly_avg"]) == 12
    assert seas["rolling_stats"]["vol_90d_ann_pct"] is not None
    assert seas["rolling_stats"]["beta_vs_benchmark"] is not None

    stocks = [
        {"symbol": "MEBL", "name": "Meezan Bank", "sector": "Commercial Banks", "last_price": 250, "change_pct": 2.4, "volume": 3_000_000, "market_cap_mn": 450000, "pe_ratio": 4.5, "pb_ratio": 1.7, "roe_pct": 38.0, "dividend_yield_pct": 11.5, "piotroski_f_score": 8, "is_shariah_compliant": True},
        {"symbol": "UBL", "name": "United Bank", "sector": "Commercial Banks", "last_price": 320, "change_pct": 1.1, "volume": 1_800_000, "market_cap_mn": 390000, "pe_ratio": 6.2, "pb_ratio": 1.5, "roe_pct": 26.0, "dividend_yield_pct": 12.0, "piotroski_f_score": 7, "is_shariah_compliant": False},
        {"symbol": "LUCK", "name": "Lucky Cement", "sector": "Cement", "last_price": 1000, "change_pct": -1.5, "volume": 500_000, "market_cap_mn": 290000, "pe_ratio": 7.0, "pb_ratio": 1.4, "roe_pct": 21.0, "dividend_yield_pct": 3.0, "piotroski_f_score": 8, "is_shariah_compliant": True},
    ]
    ranks = compute_cross_sectional_factor_ranks_pandas(stocks, "MEBL")
    assert ranks is not None
    assert ranks["sector_rank"] == 1
    assert ranks["sector_total"] == 2
    assert ranks["composite_score"] > 50.0

    hm = compute_market_heatmap_pandas(stocks)
    assert hm["breadth"]["advancers"] == 2
    assert hm["breadth"]["decliners"] == 1
    assert hm["breadth"]["total"] == 3
    assert len(hm["sectors"]) == 2


@pytest.mark.asyncio
async def test_v5_api_reports_heatmap_and_live_daily_dividend_accrual(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    try:
        async with SessionLocal() as db:
            now = datetime.now(timezone.utc)
            luck = Asset(
                symbol="LUCK",
                name="Lucky Cement Limited",
                asset_class="PSX_EQUITY",
                currency="PKR",
                sector_or_category="Cement",
                is_shariah_compliant=True,
                last_price=Decimal("430.00"),
                prev_close=Decimal("420.00"),
                change_pct=Decimal("2.38"),
                volume=Decimal("900000"),
                source="psx_dps",
                updated_at=now,
            )
            kse = Asset(
                symbol="KSE100",
                name="KSE 100 Index",
                asset_class="PSX_INDEX",
                currency="PKR",
                sector_or_category="Index",
                is_shariah_compliant=False,
                last_price=Decimal("115000.00"),
                prev_close=Decimal("114000.00"),
                change_pct=Decimal("0.88"),
                volume=Decimal("0"),
                source="psx_dps",
                updated_at=now,
            )
            mraf = Asset(
                symbol="MRAF",
                name="Meezan Rozana Amdani Fund",
                asset_class="MUTUAL_FUND",
                currency="PKR",
                sector_or_category="Shariah Compliant Money Market (Daily Dividend)",
                is_shariah_compliant=True,
                last_price=Decimal("50.00"),
                prev_close=Decimal("50.00"),
                change_pct=Decimal("0.00"),
                volume=Decimal("0"),
                source="amc_almeezan",
                updated_at=now,
            )
            db.add_all([luck, kse, mraf])
            await db.flush()

            db.add_all(
                [
                    PsxProfile(
                        asset_id=luck.id,
                        market_cap_mn=Decimal("310000"),
                        pe_ratio=Decimal("6.80"),
                        pb_ratio=Decimal("1.40"),
                        eps=Decimal("63.20"),
                        roe_pct=Decimal("21.50"),
                        dividend_yield_pct=Decimal("3.80"),
                        high_52w=Decimal("480"),
                        low_52w=Decimal("320"),
                        is_kse100=True,
                        is_kmi30=True,
                    ),
                    EquityFinancialSnapshot(
                        asset_id=luck.id,
                        fiscal_period="FY2025",
                        total_assets_mn=Decimal("320000"),
                        shareholders_equity_mn=Decimal("215000"),
                        free_cashflow_mn=Decimal("33000"),
                        roe_pct=Decimal("21.50"),
                        piotroski_f_score=8,
                        source="PUCARS PDF",
                        updated_at=now,
                    ),
                    EquityFinancialPeriod(
                        asset_id=luck.id,
                        period_type="ANNUAL",
                        period_label="FY2024",
                        period_end_date=date(2024, 6, 30),
                        fiscal_year=2024,
                        revenue_mn=Decimal("177800"),
                        net_profit_mn=Decimal("25000"),
                        eps=Decimal("85.32"),
                        total_assets_mn=Decimal("285000"),
                        shareholders_equity_mn=Decimal("183000"),
                        operating_cashflow_mn=Decimal("31500"),
                        free_cashflow_mn=Decimal("22000"),
                        source_label="PUCARS Annual PDF",
                    ),
                    EquityFinancialPeriod(
                        asset_id=luck.id,
                        period_type="ANNUAL",
                        period_label="FY2025",
                        period_end_date=date(2025, 6, 30),
                        fiscal_year=2025,
                        revenue_mn=Decimal("210500"),
                        net_profit_mn=Decimal("36000"),
                        eps=Decimal("122.87"),
                        total_assets_mn=Decimal("320000"),
                        shareholders_equity_mn=Decimal("215000"),
                        operating_cashflow_mn=Decimal("44000"),
                        free_cashflow_mn=Decimal("33000"),
                        auditor_opinion="Unmodified / Clean Opinion — A.F. Ferguson & Co. (PwC)",
                        directors_highlights_json=["Record export volume drove 18.4% revenue growth in FY2025."],
                        source_label="PUCARS Annual PDF",
                    ),
                    MutualFundProfile(
                        asset_id=mraf.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Money Market (Daily Dividend)",
                        ter_pct=Decimal("0.85"),
                        front_end_load_pct=Decimal("0.00"),
                        back_end_load_pct=Decimal("0.00"),
                        return_1y_pct=Decimal("10.50"),
                        aum_mn=Decimal("26000"),
                        is_daily_dividend=True,
                        constant_par_nav=Decimal("50.00"),
                        annualized_yield_pct=Decimal("12.00"),
                        default_payout_mode="REINVEST",
                    ),
                    MacroIndicator(
                        code="SBP_POLICY_RATE",
                        name="SBP Policy Rate",
                        category="RATES",
                        value=Decimal("12.00"),
                        unit="%",
                        as_of_date=date(2026, 11, 1),
                        source="sbp",
                    ),
                ]
            )
            # 120 daily bars for LUCK & KSE100 so technical gauge & seasonality fire
            base_d = date.today() - timedelta(days=170)
            for i in range(120):
                d = base_d + timedelta(days=int(i * 1.4))
                p_l = Decimal(str(round(340.0 + i * 0.75, 2)))
                p_k = Decimal(str(round(98000.0 + i * 140.0, 2)))
                db.add_all(
                    [
                        PriceBar(asset_id=luck.id, timeframe="1D", bar_time=datetime(d.year, d.month, d.day, tzinfo=timezone.utc), open=p_l, high=p_l + Decimal("3"), low=p_l - Decimal("3"), close=p_l, volume=Decimal("500000")),
                        PriceBar(asset_id=kse.id, timeframe="1D", bar_time=datetime(d.year, d.month, d.day, tzinfo=timezone.utc), open=p_k, high=p_k, low=p_k, close=p_k, volume=Decimal("0")),
                    ]
                )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Sarmaya-style Market Heatmap & Breadth
            hm = (await client.get("/api/v1/market/heatmap")).json()
            assert hm["breadth"]["advancers"] == 1
            assert hm["sectors"][0]["sector"] == "Cement"

            # 2. Enriched Stock Detail (Gauge + Seasonality + Factor Ranks + Multi-Period Financial Reports)
            st = (await client.get("/api/v1/market/stocks/LUCK")).json()
            assert st["technical_gauge"]["available"] is True
            assert st["seasonality"]["years"]
            assert st["factor_ranks"]["sector_rank"] == 1
            assert len(st["financial_reports"]["annual_rows"]) == 2
            assert st["financial_reports"]["latest_disclosures"]["auditor_opinion"].startswith("Unmodified")

            # 3. Reports CSV Export & PDF Upload
            csv_res = await client.get("/api/v1/market/stocks/LUCK/reports/export")
            assert csv_res.status_code == 200
            assert "FY2025" in csv_res.text

            bad_pdf = await client.post("/api/v1/market/stocks/LUCK/reports/upload", files={"file": ("x.txt", b"notpdf", "text/plain")})
            assert bad_pdf.status_code == 400

            fake_qtr = [
                {
                    "period_type": "QUARTERLY",
                    "period_label": "Q1-2026",
                    "period_end_date": date(2025, 9, 30),
                    "fiscal_year": 2026,
                    "revenue_mn": 56000.0,
                    "net_profit_mn": 9800.0,
                    "eps": 33.45,
                    "source_label": "Uploaded PDF",
                }
            ]
            with patch("app.api.analytics.parse_company_report_pdf_bytes_sync", return_value=fake_qtr):
                ok_pdf = await client.post(
                    "/api/v1/market/stocks/LUCK/reports/upload?period_type=QUARTERLY",
                    files={"file": ("LUCK_Q1_2026.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
                )
            assert ok_pdf.status_code == 200
            assert ok_pdf.json()["periods_extracted"] == 1
            assert len(ok_pdf.json()["report_analysis"]["quarterly_rows"]) == 1

            # 4. Daily Dividend Fund Detail: Compounded Wealth Curve & Income Profile even with 0 raw PriceBars
            mraf_det = (await client.get("/api/v1/market/funds/MRAF/detail")).json()
            ddp = mraf_det["daily_dividend_profile"]
            assert ddp is not None
            assert ddp["effective_annual_yield_pct"] == pytest.approx(12.0, rel=1e-2)
            assert len(ddp["wealth_curve_1y"]) == 13
            assert ddp["wealth_curve_1y"][-1]["net_filer_value"] > 110000.0
            assert len(mraf_det["analytics"]["nav_series"]) >= 20

            # 5. Live Automatic Daily Dividend Income inside Portfolio Summary (ZERO manual click needed!) + Zero Double-Counting
            reg = await client.post(
                "/api/v1/auth/register",
                json={"email": "dailydiv@openkhazana.pk", "password": "StrongPassword123!", "full_name": "Dividend Investor"},
            )
            assert reg.status_code == 201
            headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
            pf = (
                await client.post(
                    "/api/v1/portfolios",
                    json={"name": "Cash Yield Vault", "base_currency": "PKR", "auto_manage_cash": False},
                    headers=headers,
                )
            ).json()
            pid = pf["id"]

            buy_dt = (date.today() - timedelta(days=60)).isoformat()
            r_buy = await client.post(
                f"/api/v1/portfolios/{pid}/transactions",
                json={"asset_symbol": "MRAF", "tx_type": "BUY", "tx_date": buy_dt, "quantity": "10000", "unit_price": "50", "fee_amount": "0"},
                headers=headers,
            )
            assert r_buy.status_code == 201

            # Immediately fetch portfolio summary WITHOUT manually accruing — daily dividend income MUST be reflected!
            s1 = (await client.get(f"/api/v1/portfolios/{pid}/summary", headers=headers)).json()
            assert float(s1["total_net_dividends_pkr"]) > 5000.0
            assert float(s1["daily_dividend_auto_accrued_pkr"]) > 5000.0
            assert float(s1["daily_dividend_est_monthly_pkr"]) > 3000.0
            mraf_pos1 = next(h for h in s1["holdings"] if h["symbol"] == "MRAF")
            assert float(mraf_pos1["market_value_pkr"]) > 505000.0
            assert float(mraf_pos1["est_daily_income_pkr"]) > 100.0

            # Now explicitly post ledger accrual and confirm ZERO double-counting!
            acc = await client.post(
                f"/api/v1/portfolios/{pid}/accrue-daily-funds",
                json={"asset_symbol": "MRAF", "mode": "REINVEST", "rollup": "MONTHLY"},
                headers=headers,
            )
            assert acc.status_code == 200
            s2 = (await client.get(f"/api/v1/portfolios/{pid}/summary", headers=headers)).json()
            assert float(s2["daily_dividend_auto_accrued_pkr"]) == pytest.approx(0.0, abs=1.0)
            mraf_pos2 = next(h for h in s2["holdings"] if h["symbol"] == "MRAF")
            assert float(mraf_pos2["market_value_pkr"]) == pytest.approx(float(mraf_pos1["market_value_pkr"]), rel=0.02)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
