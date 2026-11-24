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
    EquityFinancialSnapshot,
    FundFmrSnapshot,
    MacroIndicator,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
    ShariahMetric,
)
from app.scrapers.fmr_parser import (
    _build_psx_company_matcher,
    _pick_latest_fmr_pdf_urls,
    parse_secp_fmr_page_text,
)
from app.services.deep_fundamentals import build_stock_valuation_lab


def test_secp_fmr_page_parser_equity_and_money_market():
    psx_matchers = _build_psx_company_matcher(
        [
            {"symbol": "MEBL", "name": "Meezan Bank Limited", "sector_or_category": "Commercial Banks"},
            {"symbol": "LUCK", "name": "Lucky Cement Limited", "sector_or_category": "Cement"},
            {"symbol": "OGDC", "name": "Oil & Gas Development Company Limited", "sector_or_category": "Oil & Gas Exploration"},
            {"symbol": "FFC", "name": "Fauji Fertilizer Company Limited", "sector_or_category": "Fertilizer"},
        ]
    )

    equity_page = """
    Meezan Islamic Fund (MIF)
    October 2026
    Net Assets as of October 31, 2026 stood at Rs. 59.12 billion.
    Fund Manager: Muhammad Asad
    Risk Profile: High
    Benchmark: KMI-30 Index
    Management Fee: 2.00%
    Total Expense Ratio (MTD): 3.45% (including 0.42% government levies)
    Total Expense Ratio (YTD): 3.38% (including 0.40% government levies)

    Fund Manager's Comments
    The fund remained 96.20% invested in Shariah compliant equities during October 2026 with overweight in banks and cement.

    Asset Allocation
    Equities 96.20%
    Cash & Bank Balances 3.10%
    Others including receivables 0.70%

    Sector Allocation
    Commercial Banks 24.50%
    Cement 19.30%
    Oil & Gas Exploration 18.10%
    Fertilizer 14.20%

    Top Ten Equity Holdings (% of Total Assets)
    Meezan Bank Limited 11.28%
    Lucky Cement Limited 9.85%
    Oil & Gas Development Company Limited 8.40%
    Fauji Fertilizer Company Limited 7.65%
    """

    eq_parsed = parse_secp_fmr_page_text(
        {"full": equity_page, "left": equity_page, "right": equity_page, "col1": "", "col2": "", "col3": ""},
        fund_info={"asset_id": "a-mif", "symbol": "MIF", "name": "Meezan Islamic Fund", "amc_name": "Al Meezan"},
        report_month="2026-10",
        report_label="October 2026",
        psx_matchers=psx_matchers,
    )
    assert eq_parsed is not None
    assert eq_parsed["net_assets_mn"] == pytest.approx(59120.0, rel=1e-3)
    assert eq_parsed["ter_mtd_pct"] == pytest.approx(3.45, rel=1e-2)
    assert eq_parsed["govt_levy_pct"] == pytest.approx(0.42, rel=1e-2)
    alloc_dict = {a["label"]: a["pct"] for a in eq_parsed["asset_allocation_json"]}
    assert alloc_dict["Equities"] == pytest.approx(96.2, rel=1e-2)
    assert alloc_dict["Cash & Bank"] == pytest.approx(3.1, rel=1e-2)
    held_syms = {h["symbol"]: h["weight_pct"] for h in eq_parsed["top_holdings_json"]}
    assert held_syms["MEBL"] == pytest.approx(11.28, rel=1e-2)
    assert held_syms["LUCK"] == pytest.approx(9.85, rel=1e-2)
    assert held_syms["OGDC"] == pytest.approx(8.40, rel=1e-2)
    sec_dict = {s["sector"]: s["pct"] for s in eq_parsed["sector_allocation_json"]}
    assert sec_dict["Commercial Banks"] == pytest.approx(24.5, rel=1e-2)
    assert sec_dict["Cement"] == pytest.approx(19.3, rel=1e-2)

    mm_page = """
    HBL Cash Fund (HBLCF)
    October 2026
    Net Assets (PKR Mn): 42,500.50
    Fund Stability Rating: AA+(f) (VIS)
    Weighted Average Maturity (Days): 48 Days
    Yield to Maturity: 11.75%
    Risk Profile: Low

    Asset Allocation
    T-Bills 58.40%
    Cash & Bank 28.10%
    GoP Ijarah Sukuk 13.50%

    Credit Quality
    Govt Securities 71.90%
    AAA 21.60%
    AA+ 6.50%
    """
    mm_parsed = parse_secp_fmr_page_text(
        {"full": mm_page, "left": mm_page, "right": mm_page, "col1": "", "col2": "", "col3": ""},
        fund_info={"asset_id": "a-hblcf", "symbol": "HBLCF", "name": "HBL Cash Fund", "amc_name": "HBL Asset Management"},
        report_month="2026-10",
        report_label="October 2026",
        psx_matchers=psx_matchers,
    )
    assert mm_parsed is not None
    assert mm_parsed["net_assets_mn"] == pytest.approx(42500.5, rel=1e-3)
    assert "AA+(f)" in (mm_parsed["stability_rating"] or "")
    assert mm_parsed["wam_days"] == 48
    mm_alloc = {a["label"]: a["pct"] for a in mm_parsed["asset_allocation_json"]}
    assert mm_alloc["T-Bills"] == pytest.approx(58.4, rel=1e-2)
    assert mm_alloc["Cash & Bank"] == pytest.approx(28.1, rel=1e-2)
    cq = {c["rating"]: c["pct"] for c in mm_parsed["credit_quality_json"]}
    assert cq["AAA"] == pytest.approx(21.6, rel=1e-2)

    # URL picker picks latest month + handles Shariah + Conventional dual booklets
    atlas_html = """
    <a href="/wp-content/uploads/fmr_sep-26-shariah.pdf">Sep Shariah</a>
    <a href="/wp-content/uploads/fmr_oct-26-shariah.pdf">Oct Shariah</a>
    <a href="/wp-content/uploads/fmr_oct-26-conventional.pdf">Oct Conv</a>
    """
    picked = _pick_latest_fmr_pdf_urls(atlas_html, "https://www.atlasfunds.com.pk/downloads/", max_pdfs=2)
    assert len(picked) == 2
    assert all("oct-26" in u for u in picked)


def test_stock_valuation_lab_corporate_and_bank_modes():
    corp_lab = build_stock_valuation_lab(
        asset={"symbol": "LUCK", "last_price": 1200.0, "sector_or_category": "Cement", "currency": "PKR"},
        profile={"market_cap_mn": 350000.0, "pe_ratio": 7.5, "pb_ratio": 1.6, "roe_pct": 22.5, "dividend_yield_pct": 3.5},
        deep_snapshot={
            "free_cashflow_mn": 38000.0,
            "operating_cashflow_mn": 52000.0,
            "total_assets_mn": 310000.0,
            "total_debt_mn": 45000.0,
            "cash_equivalents_mn": 60000.0,
            "shareholders_equity_mn": 215000.0,
            "current_ratio": 2.1,
            "debt_to_equity": 0.21,
            "roe_pct": 22.5,
            "roa_pct": 15.6,
            "operating_margin_pct": 26.4,
            "altman_z_score": 4.2,
            "piotroski_f_score": 8,
            "dcf_intrinsic_value": 1520.0,
            "metrics_json": {"revenue_mn": 210000.0, "net_income_mn": 48300.0, "net_margin_pct": 23.0},
        },
        shariah={"passed": True, "debt_to_assets_pct": 14.5},
        peer_valuation={"sector_median_pe": 9.5, "pe_vs_sector_pct": -21.1},
        dividend_history={"streak_years": 5, "payout_ratio_pct": 28.0},
        risk_free_rate_pct=11.5,
    )
    assert corp_lab["dcf_lab"]["available"] is True
    assert len(corp_lab["dcf_lab"]["sensitivity_rows"]) == 5
    assert all(len(r["cells"]) == 5 for r in corp_lab["dcf_lab"]["sensitivity_rows"])
    assert corp_lab["dupont_roe"]["roe_dupont_pct"] > 20.0
    assert len(corp_lab["piotroski_checklist"]) == 9
    assert corp_lab["bull_bear_digest"]["cash_conversion_ratio"] > 1.0
    assert corp_lab["bull_bear_digest"]["shariah_debt_headroom_pct"] == pytest.approx(22.5, rel=1e-2)
    assert len(corp_lab["bull_bear_digest"]["bull_points"]) >= 3

    bank_lab = build_stock_valuation_lab(
        asset={"symbol": "MEBL", "last_price": 240.0, "sector_or_category": "Commercial Banks", "currency": "PKR"},
        profile={"market_cap_mn": 430000.0, "pe_ratio": 4.5, "pb_ratio": 1.15, "roe_pct": 38.0, "dividend_yield_pct": 11.5},
        deep_snapshot={"roe_pct": 38.0, "roa_pct": 2.9, "piotroski_f_score": 7, "metrics_json": {}},
        shariah={"passed": True, "debt_to_assets_pct": 0.0},
        peer_valuation={"sector_median_pe": 5.2, "pe_vs_sector_pct": -13.5},
        dividend_history={"streak_years": 6, "payout_ratio_pct": 48.0},
        risk_free_rate_pct=11.5,
    )
    assert bank_lab["dcf_lab"]["available"] is False
    assert "P/B" in (bank_lab["dcf_lab"]["reason_unavailable"] or "")
    assert bank_lab["bull_bear_digest"]["is_financial_sector"] is True
    assert any("bank valuation" in b.lower() for b in bank_lab["bull_bear_digest"]["bull_points"])


@pytest.mark.asyncio
async def test_v4_api_fund_detail_stock_valuation_and_portfolio_xray(monkeypatch):
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
            mebl = Asset(
                symbol="MEBL",
                name="Meezan Bank Limited",
                asset_class="PSX_EQUITY",
                currency="PKR",
                sector_or_category="Commercial Banks",
                is_shariah_compliant=True,
                last_price=Decimal("250.00"),
                prev_close=Decimal("246.00"),
                change_pct=Decimal("1.63"),
                volume=Decimal("2500000"),
                source="psx_dps",
                updated_at=now,
            )
            luck = Asset(
                symbol="LUCK",
                name="Lucky Cement Limited",
                asset_class="PSX_EQUITY",
                currency="PKR",
                sector_or_category="Cement",
                is_shariah_compliant=True,
                last_price=Decimal("1000.00"),
                prev_close=Decimal("990.00"),
                change_pct=Decimal("1.01"),
                volume=Decimal("400000"),
                source="psx_dps",
                updated_at=now,
            )
            mif = Asset(
                symbol="MIF",
                name="Meezan Islamic Fund",
                asset_class="MUTUAL_FUND",
                currency="PKR",
                sector_or_category="Shariah Compliant Equity",
                is_shariah_compliant=True,
                last_price=Decimal("100.00"),
                prev_close=Decimal("99.50"),
                change_pct=Decimal("0.50"),
                volume=Decimal("0"),
                source="amc_almeezan",
                updated_at=now,
            )
            mraf = Asset(
                symbol="MRAF",
                name="Meezan Rozana Amdani Fund",
                asset_class="MUTUAL_FUND",
                currency="PKR",
                sector_or_category="Shariah Compliant Money Market",
                is_shariah_compliant=True,
                last_price=Decimal("50.00"),
                prev_close=Decimal("50.00"),
                change_pct=Decimal("0.00"),
                volume=Decimal("0"),
                source="amc_almeezan",
                updated_at=now,
            )
            db.add_all([mebl, luck, mif, mraf])
            await db.flush()

            db.add_all(
                [
                    PsxProfile(
                        asset_id=mebl.id,
                        market_cap_mn=Decimal("448000"),
                        pe_ratio=Decimal("4.60"),
                        pb_ratio=Decimal("1.80"),
                        eps=Decimal("54.35"),
                        roe_pct=Decimal("39.50"),
                        dividend_yield_pct=Decimal("11.20"),
                        high_52w=Decimal("270"),
                        low_52w=Decimal("180"),
                        is_kse100=True,
                        is_kmi30=True,
                    ),
                    PsxProfile(
                        asset_id=luck.id,
                        market_cap_mn=Decimal("293000"),
                        pe_ratio=Decimal("6.80"),
                        pb_ratio=Decimal("1.45"),
                        eps=Decimal("147.00"),
                        roe_pct=Decimal("21.40"),
                        dividend_yield_pct=Decimal("2.50"),
                        high_52w=Decimal("1150"),
                        low_52w=Decimal("780"),
                        is_kse100=True,
                        is_kmi30=True,
                    ),
                    EquityFinancialSnapshot(
                        asset_id=luck.id,
                        fiscal_period="FY2025",
                        bvps=Decimal("690.00"),
                        total_assets_mn=Decimal("280000"),
                        total_debt_mn=Decimal("40000"),
                        cash_equivalents_mn=Decimal("55000"),
                        shareholders_equity_mn=Decimal("202000"),
                        operating_cashflow_mn=Decimal("48000"),
                        capex_mn=Decimal("10000"),
                        free_cashflow_mn=Decimal("38000"),
                        current_ratio=Decimal("2.20"),
                        debt_to_equity=Decimal("0.20"),
                        roe_pct=Decimal("21.40"),
                        roa_pct=Decimal("15.20"),
                        operating_margin_pct=Decimal("24.50"),
                        piotroski_f_score=8,
                        altman_z_score=Decimal("4.10"),
                        dcf_intrinsic_value=Decimal("1340.00"),
                        metrics_json={"revenue_mn": 195000.0, "net_income_mn": 43000.0, "net_margin_pct": 22.05},
                        source="PUCARS PDF + PSX DPS",
                        updated_at=now,
                    ),
                    ShariahMetric(
                        asset_id=luck.id,
                        debt_to_assets_pct=Decimal("14.29"),
                        non_compliant_income_pct=Decimal("1.20"),
                        illiquid_assets_pct=Decimal("62.00"),
                        purification_rate_pct=Decimal("1.20"),
                        passed=True,
                    ),
                    MutualFundProfile(
                        asset_id=mif.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Equity",
                        ter_pct=Decimal("3.40"),
                        front_end_load_pct=Decimal("2.00"),
                        back_end_load_pct=Decimal("0.00"),
                        return_1y_pct=Decimal("42.50"),
                        aum_mn=Decimal("59120.00"),
                        is_daily_dividend=False,
                    ),
                    MutualFundProfile(
                        asset_id=mraf.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Money Market",
                        ter_pct=Decimal("0.85"),
                        front_end_load_pct=Decimal("0.00"),
                        back_end_load_pct=Decimal("0.00"),
                        return_1y_pct=Decimal("12.40"),
                        aum_mn=Decimal("82000.00"),
                        is_daily_dividend=True,
                        constant_par_nav=Decimal("50.00"),
                        annualized_yield_pct=Decimal("12.10"),
                    ),
                    FundFmrSnapshot(
                        asset_id=mif.id,
                        report_month="2026-10",
                        report_label="October 2026",
                        amc_name="Al Meezan Investment Management",
                        fund_manager="Muhammad Asad",
                        risk_profile="High",
                        benchmark_name="KMI-30 Index",
                        net_assets_mn=Decimal("59120.00"),
                        ter_mtd_pct=Decimal("3.45"),
                        ter_ytd_pct=Decimal("3.38"),
                        govt_levy_pct=Decimal("0.42"),
                        mgmt_fee_pct=Decimal("2.00"),
                        asset_allocation_json=[
                            {"label": "Equities", "pct": 95.0},
                            {"label": "Cash & Bank", "pct": 5.0},
                        ],
                        sector_allocation_json=[
                            {"sector": "Commercial Banks", "pct": 25.0},
                            {"sector": "Cement", "pct": 20.0},
                        ],
                        top_holdings_json=[
                            {"symbol": "MEBL", "name": "Meezan Bank Limited", "sector": "Commercial Banks", "weight_pct": 15.0},
                            {"symbol": "LUCK", "name": "Lucky Cement Limited", "sector": "Cement", "weight_pct": 12.0},
                        ],
                        credit_quality_json=[],
                        returns_table_json={"FYTD": 18.4, "1Y": 42.5},
                        manager_commentary="MIF maintained 95% equity allocation led by Islamic banks and cement.",
                        source_label="Al Meezan Official FMR PDF",
                        updated_at=now,
                    ),
                    FundFmrSnapshot(
                        asset_id=mraf.id,
                        report_month="2026-10",
                        report_label="October 2026",
                        amc_name="Al Meezan Investment Management",
                        stability_rating="AA+(f)",
                        risk_profile="Low",
                        net_assets_mn=Decimal("82000.00"),
                        wam_days=41,
                        ytm_pct=Decimal("11.80"),
                        ter_mtd_pct=Decimal("0.85"),
                        asset_allocation_json=[
                            {"label": "Cash & Bank", "pct": 60.0},
                            {"label": "GoP Sukuk / PIBs", "pct": 40.0},
                        ],
                        sector_allocation_json=[],
                        top_holdings_json=[],
                        credit_quality_json=[
                            {"rating": "AAA", "pct": 70.0},
                            {"rating": "AA+", "pct": 30.0},
                        ],
                        returns_table_json={"1Y": 12.4},
                        source_label="Al Meezan Official FMR PDF",
                        updated_at=now,
                    ),
                    MacroIndicator(
                        code="SBP_POLICY_RATE",
                        name="SBP Policy Rate",
                        category="RATES",
                        value=Decimal("11.50"),
                        unit="%",
                        as_of_date=date(2026, 10, 31),
                        source="sbp_official",
                    ),
                ]
            )
            # Add 35 daily bars for MIF so fund analytics works cleanly
            for i in range(35):
                d = date(2026, 9, 25) + timedelta(days=i)
                nav_v = Decimal(str(round(92.0 + i * 0.23, 4)))
                db.add(
                    PriceBar(
                        asset_id=mif.id,
                        timeframe="1D",
                        bar_time=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                        open=nav_v,
                        high=nav_v,
                        low=nav_v,
                        close=nav_v,
                        volume=Decimal("0"),
                    )
                )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Coverage counts FMRs
            cov = (await client.get("/api/v1/market/coverage")).json()
            assert cov["funds_with_fmr"] == 2

            # 2. Funds directory exposes FMR preview fields
            funds = (await client.get("/api/v1/market/funds")).json()
            mif_row = next(f for f in funds if f["symbol"] == "MIF")
            assert mif_row["has_fmr"] is True
            assert mif_row["fmr_month"] == "October 2026"
            assert mif_row["top_holding_symbols"] == ["MEBL", "LUCK"]
            mraf_row = next(f for f in funds if f["symbol"] == "MRAF")
            assert mraf_row["stability_rating"] == "AA+(f)"
            assert float(mraf_row["wam_days"]) == pytest.approx(41.0, rel=1e-2)

            # 3. Dedicated Fund Fact Sheet endpoint hydrates live PSX prices & implied P/E + yield
            f_det = (await client.get("/api/v1/market/funds/MIF/detail")).json()
            assert f_det["fund"]["symbol"] == "MIF"
            assert f_det["fmr"]["report_label"] == "October 2026"
            assert len(f_det["fmr"]["top_holdings"]) == 2
            mebl_h = next(h for h in f_det["fmr"]["top_holdings"] if h["symbol"] == "MEBL")
            assert mebl_h["last_price"] == pytest.approx(250.0, rel=1e-3)
            assert f_det["fmr"]["implied_portfolio_pe"] is not None
            assert f_det["fmr"]["implied_portfolio_div_yield_pct"] is not None

            # 4. Stock detail includes Valuation Lab & Mutual Fund Institutional Holders radar
            luck_det = (await client.get("/api/v1/market/stocks/LUCK")).json()
            assert luck_det["valuation_lab"]["dcf_lab"]["available"] is True
            assert len(luck_det["valuation_lab"]["dcf_lab"]["sensitivity_rows"]) == 5
            assert len(luck_det["valuation_lab"]["piotroski_checklist"]) == 9
            assert luck_det["valuation_lab"]["dupont_roe"]["roe_dupont_pct"] > 15.0
            assert len(luck_det["institutional_holders"]) == 1
            assert luck_det["institutional_holders"][0]["fund_symbol"] == "MIF"
            assert luck_det["institutional_holders"][0]["weight_pct"] == pytest.approx(12.0, rel=1e-2)
            assert luck_det["institutional_holders"][0]["est_value_mn"] == pytest.approx(7094.4, rel=1e-2)

            mebl_det = (await client.get("/api/v1/market/stocks/MEBL")).json()
            assert mebl_det["valuation_lab"]["dcf_lab"]["available"] is False
            assert mebl_det["institutional_holders"][0]["fund_symbol"] == "MIF"

            # 5. Auth + Portfolio Look-Through X-Ray overlap test
            reg = await client.post(
                "/api/v1/auth/register",
                json={"email": "xray@openkhazana.pk", "password": "StrongPassword123!", "full_name": "XRay Investor"},
            )
            assert reg.status_code == 201
            token = reg.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            pf = (
                await client.post(
                    "/api/v1/portfolios",
                    json={"name": "Core Hybrid", "base_currency": "PKR", "auto_manage_cash": False},
                    headers=headers,
                )
            ).json()
            pid = pf["id"]

            # Buy 100 LUCK @ 1000 = 100,000 PKR direct
            # Buy 2000 MIF @ 100 = 200,000 PKR (FMR holds 12% LUCK = 24,000 PKR, 15% MEBL = 30,000 PKR)
            # Buy 2000 MRAF @ 50 = 100,000 PKR (FMR 60% Cash, 40% GoP Sukuk)
            # Total = 400,000 PKR -> LUCK combined = 124,000 (31.0%), is_overlap = True!
            for sym, qty, px in [("LUCK", "100", "1000"), ("MIF", "2000", "100"), ("MRAF", "2000", "50")]:
                r_tx = await client.post(
                    f"/api/v1/portfolios/{pid}/transactions",
                    json={
                        "asset_symbol": sym,
                        "tx_type": "BUY",
                        "tx_date": "2026-10-15",
                        "quantity": qty,
                        "unit_price": px,
                        "fee_amount": "0",
                    },
                    headers=headers,
                )
                assert r_tx.status_code == 201

            xray_res = await client.get(f"/api/v1/portfolios/{pid}/xray", headers=headers)
            assert xray_res.status_code == 200
            xray = xray_res.json()
            assert xray["total_value_pkr"] == pytest.approx(400000.0, rel=1e-2)
            assert xray["mutual_fund_value_pkr"] == pytest.approx(300000.0, rel=1e-2)
            assert xray["fmr_covered_fund_pct"] == pytest.approx(100.0, rel=1e-2)

            mix = {m["label"]: m["value_pkr"] for m in xray["look_through_asset_mix"]}
            # Equities = 100,000 direct + 95% of 200,000 MIF = 290,000
            assert mix["Equities"] == pytest.approx(290000.0, rel=1e-2)
            # Cash & Bank = 5% of 200,000 MIF (10,000) + 60% of 100,000 MRAF (60,000) = 70,000
            assert mix["Cash & Bank"] == pytest.approx(70000.0, rel=1e-2)
            # GoP Sukuk = 40% of 100,000 MRAF = 40,000
            assert mix["GoP Sukuk / PIBs"] == pytest.approx(40000.0, rel=1e-2)

            luck_ov = next(s for s in xray["stock_overlap"] if s["symbol"] == "LUCK")
            assert luck_ov["is_overlap"] is True
            assert luck_ov["direct_pkr"] == pytest.approx(100000.0, rel=1e-2)
            assert luck_ov["via_funds_pkr"] == pytest.approx(24000.0, rel=1e-2)
            assert luck_ov["total_weight_pct"] == pytest.approx(31.0, rel=1e-2)
            assert luck_ov["contributing_funds"][0]["fund_symbol"] == "MIF"

            # 6. Manual FMR upload validation (reject non-PDF, accept valid PDF via mocked parser)
            bad_up = await client.post(
                "/api/v1/market/funds/fmr/upload",
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
            assert bad_up.status_code == 400

            fake_parsed = [
                {
                    "asset_id": mif.id,
                    "symbol": "MIF",
                    "name": "Meezan Islamic Fund",
                    "amc_name": "Al Meezan Investment Management",
                    "report_month": "2026-11",
                    "report_label": "November 2026",
                    "asset_allocation_json": [{"label": "Equities", "pct": 94.0}, {"label": "Cash & Bank", "pct": 6.0}],
                    "sector_allocation_json": [{"sector": "Commercial Banks", "pct": 26.0}],
                    "top_holdings_json": [{"symbol": "MEBL", "name": "Meezan Bank Limited", "sector": "Commercial Banks", "weight_pct": 14.5}],
                    "credit_quality_json": [],
                    "returns_table_json": {"1Y": 44.0},
                    "net_assets_mn": 60250.0,
                    "fund_manager": "Muhammad Asad",
                    "stability_rating": None,
                    "amc_rating": None,
                    "risk_profile": "High",
                    "launch_date": None,
                    "benchmark_name": "KMI-30 Index",
                    "wam_days": None,
                    "duration_days": None,
                    "ytm_pct": None,
                    "ter_mtd_pct": 3.4,
                    "ter_ytd_pct": 3.3,
                    "govt_levy_pct": 0.4,
                    "mgmt_fee_pct": 2.0,
                    "manager_commentary": "Updated Nov FMR.",
                }
            ]
            with patch("app.scrapers.fmr_parser.parse_secp_fmr_pdf_bytes_sync", return_value=fake_parsed):
                ok_up = await client.post(
                    "/api/v1/market/funds/fmr/upload?symbol=MIF",
                    files={"file": ("FMR-November-2026.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
                )
            assert ok_up.status_code == 200
            up_body = ok_up.json()
            assert up_body["matched_funds"] == 1
            assert up_body["report_month"] == "2026-11"
            assert up_body["funds"][0]["symbol"] == "MIF"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
