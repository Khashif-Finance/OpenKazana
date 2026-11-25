"""
OpenKhazana v8 test suite:
  - 5-Brokerage Research Report parsers (AHL, SCS Trade, KASB, Chase) & Stance/TP extractor
  - Custom Research Bookmark CRUD + `/stocks/{symbol}/research` ticker/sector tagging
  - Forex & Remittance Hub (`/api/v1/market/forex`)
  - PMEX & Global Commodities Hub (`/api/v1/market/commodities`)
  - Fixed Income, GoP Ijarah Sukuk & CDNS National Savings Hub (`/api/v1/market/fixed-income`)
  - Mutual Funds AMC Leaderboard (`/api/v1/market/funds/leaderboard`) & FMR Holdings Overlap (`/api/v1/market/funds/overlap`)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.markets_v8 import parse_cdns_nss_rates_html
from app.core.cache import api_cache
from app.core.database import Base, get_db
from app.main import app
from app.models.market import (
    Asset,
    FundFmrSnapshot,
    MacroIndicator,
    MutualFundProfile,
    ResearchReport,
)
from app.scrapers.brokerage_research import (
    BROKERAGE_PORTALS,
    extract_stance_and_tp,
    parse_ahl_research_html,
    parse_chase_wp_posts,
    parse_kasb_reports_html,
    parse_scs_research_html,
)


def test_extract_stance_and_tp():
    stance, tp = extract_stance_and_tp("MEBL 3QCY26 Result Review — Maintain BUY with Target Price Rs. 345.50")
    assert stance == "BUY"
    assert tp == Decimal("345.50")

    stance2, tp2 = extract_stance_and_tp("Cement Sector Note", "We rate LUCK Overweight with fair value PKR 1,420")
    assert stance2 == "OVERWEIGHT"
    assert tp2 == Decimal("1420.00")

    stance3, tp3 = extract_stance_and_tp("PSX Weekly Wrap — Macro Outlook")
    assert stance3 is None
    assert tp3 is None


def test_brokerage_parsers_all_four_live_desks():
    assert len(BROKERAGE_PORTALS) == 5
    keys = {p["broker_key"] for p in BROKERAGE_PORTALS}
    assert keys == {"ahl", "scs", "ims", "kasb", "chase"}

    chase_items = parse_chase_wp_posts(
        [
            {
                "title": {"rendered": "Corporate Briefing Note &#8211; Nishat Power Limited (NPL)"},
                "link": "https://chasesecurities.com/corporate-briefing-note-npl/",
                "date_gmt": "2026-11-25T09:00:00",
                "excerpt": {"rendered": "<p>NPL management highlighted strong payout capacity. BUY TP Rs 48.</p>"},
                "content": {"rendered": '<p><a href="https://chasesecurities.com/wp-content/uploads/2026/11/npl.pdf">PDF</a></p>'},
            }
        ]
    )
    assert len(chase_items) == 1
    assert chase_items[0]["broker_key"] == "chase"
    assert chase_items[0]["report_type"] == "CORPORATE_BRIEFING"
    assert chase_items[0]["stance"] == "BUY"
    assert chase_items[0]["target_price"] == Decimal("48.00")
    assert chase_items[0]["pdf_url"] == "https://chasesecurities.com/wp-content/uploads/2026/11/npl.pdf"

    kasb_html = """
    <div class="card">
      <h3>FY27 Budget Impact &amp; PMEX Commodities Outlook</h3>
      <a href="/wp-content/uploads/2026/11/FY27-Budget-PMEX-Outlook.pdf">Download PDF</a>
    </div>
    """
    kasb_items = parse_kasb_reports_html(kasb_html)
    assert len(kasb_items) == 1
    assert kasb_items[0]["broker_key"] == "kasb"
    assert "PMEX" in kasb_items[0]["title"]
    assert kasb_items[0]["pdf_url"].endswith(".pdf")

    ahl_html = """
    <div><a href="/downloads/RecommendationSummary.pdf">Click Report</a></div>
    <div><a href="/downloads/LuckyCementLimited.pdf">Click</a></div>
    """
    ahl_items = parse_ahl_research_html(ahl_html)
    assert len(ahl_items) == 2
    assert any("Recommendation Summary" in it["title"] for it in ahl_items)
    assert any("Lucky Cement Limited" in it["title"] for it in ahl_items)

    scs_html = """
    <a href="/Research/Valuation Matrix.pdf">Valuation Matrix</a>
    <a href="/Research/PSX Company Dividend Schedule.pdf">PSX Company Dividend Schedule</a>
    """
    scs_items = parse_scs_research_html(scs_html)
    assert len(scs_items) == 2
    assert scs_items[0]["broker_key"] == "scs"
    assert "%20" in scs_items[0]["pdf_url"]


def test_parse_cdns_nss_rates_html():
    sample = """
    <html><body>
      <p>Behbood Savings Certificates latest profit rate is 13.44% per annum.</p>
      <p>Pensioners' Benefit Account profit rate: 13.44% p.a.</p>
      <p>Defence Savings Certificates average rate: 12.12% per annum.</p>
      <p>Regular Income Certificates return: 11.52%</p>
      <p>Special Savings Certificates rate: 11.40%</p>
      <p>Sarwa Islamic Term Account 1-Year rate 10.95%</p>
    </body></html>
    """
    rates = parse_cdns_nss_rates_html(sample)
    assert rates["NSS_BSC"] == 13.44
    assert rates["NSS_PBA"] == 13.44
    assert rates["NSS_DSC"] == 12.12
    assert rates["NSS_RIC"] == 11.52
    assert rates["NSS_SSC"] == 11.40
    assert rates["NSS_SARWA_1Y"] == 10.95


@pytest.mark.asyncio
async def test_v8_endpoints_end_to_end():
    api_cache.invalidate_prefix("")
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
            # Seed PSX equity, FX, Macro, 2 Mutual Funds with overlapping SECP FMRs & 1 ResearchReport
            mebl = Asset(
                symbol="MEBL",
                name="Meezan Bank Limited",
                asset_class="PSX_EQUITY",
                sector_or_category="Commercial Banks",
                currency="PKR",
                last_price=Decimal("245.50"),
                prev_close=Decimal("240.00"),
                change_pct=Decimal("2.29"),
                is_shariah_compliant=True,
            )
            usd = Asset(
                symbol="USD_PKR_INTERBANK",
                name="USD/PKR Interbank",
                asset_class="FX",
                sector_or_category="FX",
                currency="PKR",
                last_price=Decimal("278.50"),
                prev_close=Decimal("278.20"),
                change_pct=Decimal("0.11"),
            )
            f1 = Asset(
                symbol="MIF",
                name="Meezan Islamic Fund",
                asset_class="MUTUAL_FUND",
                sector_or_category="Shariah Compliant Equity",
                currency="PKR",
                last_price=Decimal("105.00"),
                prev_close=Decimal("104.00"),
                change_pct=Decimal("0.96"),
                is_shariah_compliant=True,
            )
            f2 = Asset(
                symbol="ASSF",
                name="Al-Ameen Shariah Stock Fund",
                asset_class="MUTUAL_FUND",
                sector_or_category="Shariah Compliant Equity",
                currency="PKR",
                last_price=Decimal("220.00"),
                prev_close=Decimal("218.00"),
                change_pct=Decimal("0.92"),
                is_shariah_compliant=True,
            )
            f3 = Asset(
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
            db.add_all([mebl, usd, f1, f2, f3])
            await db.flush()

            db.add_all(
                [
                    MutualFundProfile(
                        asset_id=f1.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Equity",
                        risk_profile="High",
                        ter_pct=Decimal("3.20"),
                        front_end_load_pct=Decimal("2.00"),
                        aum_mn=Decimal("42000"),
                        return_1y_pct=Decimal("48.50"),
                    ),
                    MutualFundProfile(
                        asset_id=f2.id,
                        amc_name="UBL Fund Managers",
                        fund_category="Shariah Compliant Equity",
                        risk_profile="High",
                        ter_pct=Decimal("3.50"),
                        front_end_load_pct=Decimal("2.50"),
                        aum_mn=Decimal("18500"),
                        return_1y_pct=Decimal("44.20"),
                    ),
                    MutualFundProfile(
                        asset_id=f3.id,
                        amc_name="Al Meezan Investment Management",
                        fund_category="Shariah Compliant Money Market",
                        risk_profile="Low",
                        ter_pct=Decimal("1.10"),
                        front_end_load_pct=Decimal("0.00"),
                        aum_mn=Decimal("88000"),
                        is_daily_dividend=True,
                        annualized_yield_pct=Decimal("12.85"),
                        return_1y_pct=Decimal("12.85"),
                    ),
                    FundFmrSnapshot(
                        asset_id=f1.id,
                        report_month="2026-10",
                        report_label="Oct 2026",
                        top_holdings_json=[
                            {"symbol": "MEBL", "name": "Meezan Bank", "weight_pct": 12.0},
                            {"symbol": "OGDC", "name": "Oil & Gas Dev", "weight_pct": 11.0},
                            {"symbol": "LUCK", "name": "Lucky Cement", "weight_pct": 10.0},
                            {"symbol": "MARI", "name": "Mari Energies", "weight_pct": 9.0},
                        ],
                    ),
                    FundFmrSnapshot(
                        asset_id=f2.id,
                        report_month="2026-10",
                        report_label="Oct 2026",
                        top_holdings_json=[
                            {"symbol": "MEBL", "name": "Meezan Bank", "weight_pct": 11.5},
                            {"symbol": "OGDC", "name": "Oil & Gas Dev", "weight_pct": 10.5},
                            {"symbol": "LUCK", "name": "Lucky Cement", "weight_pct": 8.0},
                            {"symbol": "PPL", "name": "Pakistan Petroleum", "weight_pct": 7.0},
                        ],
                    ),
                    MacroIndicator(
                        code="SBP_POLICY_RATE",
                        name="SBP Policy Rate",
                        value=Decimal("11.00"),
                        unit="%",
                        as_of_date=date(2026, 11, 1),
                    ),
                    MacroIndicator(
                        code="PK_CPI_YOY",
                        name="PBS Monthly CPI YoY",
                        value=Decimal("5.40"),
                        unit="%",
                        as_of_date=date(2026, 11, 1),
                    ),
                    MacroIndicator(
                        code="TBILL_12M",
                        name="12M Market T-Bill",
                        value=Decimal("11.45"),
                        unit="%",
                        as_of_date=date(2026, 11, 1),
                    ),
                    MacroIndicator(
                        code="PIB_5Y",
                        name="5Y PIB Yield",
                        value=Decimal("12.10"),
                        unit="%",
                        as_of_date=date(2026, 11, 1),
                    ),
                    ResearchReport(
                        url_hash="seed-ahl-mebl-1",
                        broker_key="ahl",
                        broker_name="Arif Habib Limited (AHL)",
                        title="AHL Banking Sector — MEBL Strong ROE Continues (BUY, TP Rs 310)",
                        summary="Meezan Bank remains our top Islamic banking pick.",
                        url="https://www.arifhabibltd.com/downloads/mebl.pdf",
                        pdf_url="https://www.arifhabibltd.com/downloads/mebl.pdf",
                        report_type="COMPANY",
                        stance="BUY",
                        target_price=Decimal("310.00"),
                        matched_symbols_json=["MEBL"],
                        matched_sectors_json=["Commercial Banks"],
                        published_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await db.commit()

        mock_fx = (
            {"USD": 1.0, "PKR": 278.5, "EUR": 0.92, "GBP": 0.78, "AED": 3.6725, "SAR": 3.75, "JPY": 152.0, "CHF": 0.88, "CAD": 1.38, "AUD": 1.52, "CNY": 7.22, "KWD": 0.307, "QAR": 3.64, "OMR": 0.385, "BHD": 0.376},
            "2026-11-25T12:00:00Z",
        )
        mock_yf = {
            "GC=F": {"price": 2700.0, "change_1d_pct": 0.85, "return_1m_pct": 3.2, "return_1y_pct": 28.4, "high_52w": 2790.0, "low_52w": 2020.0, "series": [{"date": "2026-11-24", "price": 2677.0}, {"date": "2026-11-25", "price": 2700.0}]},
            "BZ=F": {"price": 74.5, "change_1d_pct": -0.4, "return_1m_pct": -2.1, "return_1y_pct": -6.5, "high_52w": 90.0, "low_52w": 69.0, "series": [{"date": "2026-11-24", "price": 74.8}, {"date": "2026-11-25", "price": 74.5}]},
            "DX-Y.NYB": {"price": 106.2, "change_1d_pct": 0.15, "return_1m_pct": 1.8, "return_1y_pct": 2.9, "high_52w": 107.1, "low_52w": 100.2, "series": []},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Brokerage research listing & stock-specific tagging + authenticated bookmark CRUD
            r_reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "researcher@example.com",
                    "password": "StrongPassword123!",
                    "full_name": "Research Analyst",
                    "filer_status": "ACTIVE_FILER",
                },
            )
            assert r_reg.status_code == 201

            r_list = await client.get("/api/v1/market/research")
            assert r_list.status_code == 200
            list_body = r_list.json()
            assert list_body["total"] == 1
            assert len(list_body["portals"]) == 5

            r_bm = await client.post(
                "/api/v1/market/research/bookmark",
                json={
                    "title": "IMS Note on MEBL — BUY TP Rs 325",
                    "url": "https://imsecurities.com.pk/reports/mebl-note.pdf",
                    "broker_name": "Intermarket Securities (IMS)",
                    "broker_key": "ims",
                    "symbols": ["MEBL"],
                    "sectors": ["Commercial Banks"],
                },
            )
            assert r_bm.status_code == 201
            bm = r_bm.json()
            assert bm["stance"] == "BUY"
            assert bm["target_price"] == 325.0
            assert bm["is_user_bookmark"] is True

            r_stk = await client.get("/api/v1/market/stocks/MEBL/research")
            assert r_stk.status_code == 200
            stk_body = r_stk.json()
            assert len(stk_body["stock_reports"]) == 2
            assert len(stk_body["portals"]) == 5

            r_del = await client.delete(f"/api/v1/market/research/bookmark/{bm['id']}")
            assert r_del.status_code == 200

            # 2. Forex & Remittance Hub
            with (
                patch("app.api.markets_v8._fetch_live_fx_rates", new=AsyncMock(return_value=mock_fx)),
                patch("app.api.markets_v8._fetch_commodities_yfinance_sync", return_value=mock_yf),
            ):
                r_fx = await client.get("/api/v1/market/forex")
                assert r_fx.status_code == 200
                fx_body = r_fx.json()
                assert fx_body["usd_pkr_interbank"] == 278.5
                assert len(fx_body["corridors"]) == 14
                assert len(fx_body["global_majors"]) == 9
                assert fx_body["dxy"]["price"] == 106.2

                # 3. PMEX & Global Commodities Hub
                r_cmd = await client.get("/api/v1/market/commodities")
                assert r_cmd.status_code == 200
                cmd_body = r_cmd.json()
                assert len(cmd_body["commodities"]) == 11
                assert cmd_body["gold_pkr_breakdown"]["gold_24k_tola_pkr"] > 200000
                assert len(cmd_body["pmex_resources"]) == 3

            # 4. Fixed Income, GoP Ijarah Sukuk & CDNS NSS Hub
            with patch("app.api.markets_v8._fetch_live_cdns_nss_overrides", new=AsyncMock(return_value={})):
                r_fi = await client.get("/api/v1/market/fixed-income")
            assert r_fi.status_code == 200
            fi_body = r_fi.json()
            assert fi_body["policy_rate_pct"] == 11.0
            assert fi_body["cpi_yoy_pct"] == 5.4
            assert len(fi_body["nss"]) == 8
            assert any(s["code"] == "NSS_BSC" and s["wht_exempt"] and s["zakat_exempt"] for s in fi_body["nss"])
            assert len(fi_body["sukuk"]) == 4
            assert len(fi_body["sovereign"]) == 2
            assert len(fi_body["top_money_market_funds"]) == 1

            # 5. Mutual Funds AMC Leaderboard & Category Champions
            r_lb = await client.get("/api/v1/market/funds/leaderboard")
            assert r_lb.status_code == 200
            lb_body = r_lb.json()
            assert len(lb_body["amc_leaderboard"]) == 2
            assert len(lb_body["category_champions"]) >= 2

            # 6. SECP FMR Top-10 Holdings Overlap Detector
            r_ov = await client.get("/api/v1/market/funds/overlap?symbols=MIF,ASSF")
            assert r_ov.status_code == 200
            ov_body = r_ov.json()
            assert len(ov_body["selected_funds"]) == 2
            assert len(ov_body["pairwise_overlap"]) == 1
            pair = ov_body["pairwise_overlap"][0]
            assert pair["common_stocks_count"] == 3
            assert pair["weighted_overlap_pct"] > 70.0
            assert pair["verdict"] == "HIGH_OVERLAP"
    finally:
        app.dependency_overrides.pop(get_db, None)
        api_cache.invalidate_prefix("")
        await test_engine.dispose()
