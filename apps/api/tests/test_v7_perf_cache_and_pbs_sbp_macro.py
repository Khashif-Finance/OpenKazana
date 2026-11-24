"""
v7 Verification Suite:
  - PBS IMF-SDMX Monthly CPI & 12-Group COICOP Inflation Basket XML Parser
  - SBP EasyData / EcoData Excel Parsers (FX Reserves footnote safety, Remittances, Current Account Balance)
  - MemoryTTLCache Single-Flight Coalescing & Prefix Invalidation
  - True Monthly PBS CPI vs SBP Policy Rate Regime Alignment (`/api/v1/market/macro/regime`)
  - Compact Instant-Search Catalog Endpoint (`/api/v1/market/assets/index`) & Fast Screener/Sectors/Funds
"""

from __future__ import annotations

import asyncio
import io
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import openpyxl
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cache import MemoryTTLCache
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.market import Asset, MacroIndicator
from app.scrapers.sbp_macro import (
    parse_pbs_sdmx_cpi_xml,
    parse_sbp_bop_xlsx,
    parse_sbp_forex_reserves_xlsx,
    parse_sbp_remittances_xlsx,
)


def _build_sample_pbs_sdmx_xml() -> str:
    # 14 months of headline CPI + 2 COICOP groups + weights
    months = [
        ("2025-09", 275.0, 290.0, 240.0),
        ("2025-10", 280.0, 300.0, 250.0),
        ("2025-11", 281.0, 301.0, 251.0),
        ("2025-12", 282.0, 302.0, 252.0),
        ("2026-01", 285.0, 305.0, 255.0),
        ("2026-02", 287.0, 306.0, 256.0),
        ("2026-03", 289.0, 307.0, 257.0),
        ("2026-04", 291.0, 308.0, 258.0),
        ("2026-05", 293.0, 309.0, 259.0),
        ("2026-06", 295.0, 310.0, 260.0),
        ("2026-07", 298.0, 315.0, 262.0),
        ("2026-08", 301.0, 320.0, 264.0),
        ("2026-09", 304.0, 324.0, 266.0),
        ("2026-10", 308.0, 330.0, 270.0),
    ]
    obs_h = "".join(f'<Obs TIME_PERIOD="{m}" OBS_VALUE="{h}"/>' for m, h, _, _ in months)
    obs_f = "".join(f'<Obs TIME_PERIOD="{m}" OBS_VALUE="{f}"/>' for m, _, f, _ in months)
    obs_u = "".join(f'<Obs TIME_PERIOD="{m}" OBS_VALUE="{u}"/>' for m, _, _, u in months)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<message:StructureSpecificData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
  <message:DataSet>
    <Series INDICATOR="PCPI_IX">{obs_h}</Series>
    <Series INDICATOR="PCPI_CP_01_IX">{obs_f}</Series>
    <Series INDICATOR="PCPI_CP_01_WT"><Obs TIME_PERIOD="2026-10" OBS_VALUE="34.58"/></Series>
    <Series INDICATOR="PCPI_CP_04_IX">{obs_u}</Series>
    <Series INDICATOR="PCPI_CP_04_WT"><Obs TIME_PERIOD="2026-10" OBS_VALUE="23.63"/></Series>
  </message:DataSet>
</message:StructureSpecificData>"""


def test_parse_pbs_sdmx_cpi_xml_computes_yoy_mom_and_coicop_basket():
    items = parse_pbs_sdmx_cpi_xml(_build_sample_pbs_sdmx_xml())
    by_key = {x["series_key"]: x for x in items}
    assert "PK_CPI_YOY" in by_key
    assert "PK_CPI_MOM" in by_key
    assert "PK_CPI_BASKET" in by_key

    # 308 / 280 - 1 = +10.00% YoY
    assert float(by_key["PK_CPI_YOY"]["value"]) == pytest.approx(10.0, abs=0.02)
    assert by_key["PK_CPI_YOY"]["as_of_date"] == date(2026, 10, 1)
    assert by_key["PK_CPI_YOY"]["tenor"] == "MONTHLY"
    assert len(by_key["PK_CPI_YOY"]["history_points"]) == 2  # 2026-09 and 2026-10 have prior-year obs

    # 308 / 304 - 1 = +1.32% MoM
    assert float(by_key["PK_CPI_MOM"]["value"]) == pytest.approx(1.32, abs=0.02)

    # 2 COICOP groups sorted by weight descending
    basket = by_key["PK_CPI_BASKET"]["history_points"]
    assert len(basket) == 2
    assert basket[0]["code"] == "PCPI_CP_01"
    assert basket[0]["weight_pct"] == pytest.approx(34.58, abs=0.01)
    assert basket[0]["yoy_pct"] == pytest.approx(10.0, abs=0.02)
    assert basket[1]["code"] == "PCPI_CP_04"
    assert basket[1]["yoy_pct"] == pytest.approx(8.0, abs=0.02)


def test_parse_sbp_forex_reserves_skips_footnotes_and_future_dates():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Week-end"
    ws.append(["No", "Date", "Net SBP", "Banks", "Total"])
    # Footnote-suffixed date string that previously misparsed as year 2316
    ws.append([1, "20-Apr-23 16/", 4460.0, 5560.0, 10020.0])
    # Real 2026 weekly reserve row
    ws.append([2, datetime(2026, 11, 13), 21590.0, 5180.0, 26770.0])
    # Future bogus date row should be ignored
    ws.append([3, datetime(2099, 1, 1), 9999.0, 1000.0, 10999.0])

    buf = io.BytesIO()
    wb.save(buf)
    items = parse_sbp_forex_reserves_xlsx(buf.getvalue())
    by_key = {x["series_key"]: x for x in items}
    assert by_key["SBP_FX_RESERVES_BN"]["as_of_date"] == date(2026, 11, 13)
    assert float(by_key["SBP_FX_RESERVES_BN"]["value"]) == pytest.approx(21.59, abs=0.01)
    assert float(by_key["PK_TOTAL_FX_RESERVES_BN"]["value"]) == pytest.approx(26.77, abs=0.01)


def test_parse_sbp_remittances_and_bop_xlsx():
    # 1. Remittances workbook
    wb_r = openpyxl.Workbook()
    ws_r = wb_r.active
    ws_r.title = "July 2018 onwards "
    headers = ["Country"] + [f"{m}-26" for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]] + ["Nov-25", "Dec-25"]
    ws_r.append(headers)
    ws_r.append(["Cash", 3100, 3200, 3300, 3400, 3500, 3600, 3700, 3750, 3810, 3891.9, 2900, 3000])
    ws_r.append(["Saudi Arabia", 700, 720, 740, 760, 780, 800, 820, 850, 870, 910.5, 650, 680])
    buf_r = io.BytesIO()
    wb_r.save(buf_r)

    rem_items = parse_sbp_remittances_xlsx(buf_r.getvalue())
    assert len(rem_items) == 1
    assert rem_items[0]["series_key"] == "SBP_REMITTANCES_MN"
    assert float(rem_items[0]["value"]) == pytest.approx(3891.9, abs=0.1)
    assert rem_items[0]["as_of_date"] == date(2026, 10, 1)

    # 2. BOP Current Account workbook (including empty future FY27 placeholder row)
    wb_b = openpyxl.Workbook()
    ws_b = wb_b.active
    ws_b.title = "1.Summary BOP"
    # Future placeholder row with 0 credits/debits -> must be skipped
    ws_b.append([datetime(2027, 6, 30), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    # Real Oct 2026 surplus row
    ws_b.append([datetime(2026, 10, 31), 6500.0, 6461.0, 0, 0, 0, 0, 0, 0, 0, 0, 39.0])
    # Earlier Sep 2026 deficit row
    ws_b.append([datetime(2026, 9, 30), 6100.0, 6173.0, 0, 0, 0, 0, 0, 0, 0, 0, -73.0])
    buf_b = io.BytesIO()
    wb_b.save(buf_b)

    bop_items = parse_sbp_bop_xlsx(buf_b.getvalue())
    assert len(bop_items) == 1
    assert bop_items[0]["series_key"] == "SBP_CURRENT_ACCOUNT_MN"
    assert bop_items[0]["as_of_date"] == date(2026, 10, 31)
    assert float(bop_items[0]["value"]) == pytest.approx(39.0, abs=0.1)


@pytest.mark.asyncio
async def test_memory_ttl_cache_single_flight_and_invalidation():
    cache = MemoryTTLCache(max_entries=16)
    cache._is_enabled = lambda: True  # enable explicitly under pytest

    calls = 0

    async def slow_loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return {"value": calls}

    # 5 concurrent callers share ONE execution via per-key lock
    results = await asyncio.gather(*[cache.get_or_set("k:shared", 30.0, slow_loader) for _ in range(5)])
    assert calls == 1
    assert all(r == {"value": 1} for r in results)

    # Prefix invalidation clears matching keys only
    cache.set("other:key", 42, 30.0)
    removed = cache.invalidate_prefix("k:")
    assert removed == 1
    assert cache.get("k:shared") == (False, None)
    assert cache.get("other:key") == (True, 42)


@pytest.mark.asyncio
async def test_assets_index_and_monthly_macro_regime_endpoints(monkeypatch):
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
        async with SessionLocal() as db_session:
            now = datetime.now(timezone.utc)
            for sym, name, cls in [
                ("MEBL", "Meezan Bank Limited", "PSX_EQUITY"),
                ("LUCK", "Lucky Cement Limited", "PSX_EQUITY"),
                ("OGDC", "Oil & Gas Development Company", "PSX_EQUITY"),
                ("MIF", "Meezan Islamic Fund", "MUTUAL_FUND"),
                ("KSE100", "KSE 100 Index", "PSX_INDEX"),
            ]:
                db_session.add(
                    Asset(
                        symbol=sym,
                        name=name,
                        asset_class=cls,
                        currency="PKR",
                        sector_or_category="Commercial Banks" if sym == "MEBL" else "Cement",
                        is_shariah_compliant=sym in {"MEBL", "LUCK", "MIF"},
                        last_price=Decimal("250.00"),
                        prev_close=Decimal("245.00"),
                        change_pct=Decimal("2.04"),
                        volume=Decimal("500000"),
                        source="psx_dps",
                        updated_at=now,
                    )
                )

            monthly_cpi = [
                {"date": f"2025-{m:02d}-01", "month": f"2025-{m:02d}", "value": round(15.0 - m * 0.5, 2)}
                for m in range(1, 13)
            ] + [
                {"date": "2026-01-01", "month": "2026-01", "value": 8.4},
                {"date": "2026-02-01", "month": "2026-02", "value": 9.1},
            ]
            db_session.add(
                MacroIndicator(
                    code="PK_CPI_YOY",
                    name="Pakistan National CPI Inflation (YoY)",
                    country="PK",
                    category="INFLATION",
                    value=Decimal("9.1000"),
                    unit="%",
                    as_of_date=date(2026, 2, 1),
                    source="PBS SDMX (Monthly National CPI)",
                    history_json=monthly_cpi,
                    updated_at=now,
                )
            )
            db_session.add(
                MacroIndicator(
                    code="SBP_POLICY_RATE",
                    name="SBP Monetary Policy Rate",
                    country="PK",
                    category="POLICY",
                    value=Decimal("12.0000"),
                    unit="%",
                    as_of_date=date(2026, 2, 1),
                    source="State Bank of Pakistan",
                    history_json=[
                        {"date": "2025-01-27", "value": 12.0},
                        {"date": "2025-05-05", "value": 11.0},
                        {"date": "2026-01-26", "value": 12.0},
                    ],
                    updated_at=now,
                )
            )
            await db_session.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            r_reg = await client.get("/api/v1/market/macro/regime")
            assert r_reg.status_code == 200
            body = r_reg.json()
            assert body["cpi_frequency"] == "MONTHLY"
            assert body["latest_cpi_yoy_pct"] == pytest.approx(9.1, abs=0.01)
            assert body["latest_real_rate_pct"] == pytest.approx(2.9, abs=0.01)
            by_month = {pt["month"]: pt for pt in body["series"]}
            assert by_month["2025-01"]["cpi_yoy_pct"] == pytest.approx(14.5, abs=0.01)
            assert by_month["2025-06"]["cpi_yoy_pct"] == pytest.approx(12.0, abs=0.01)
            assert by_month["2026-02"]["cpi_yoy_pct"] == pytest.approx(9.1, abs=0.01)
            assert by_month["2026-02"]["real_rate_pct"] == pytest.approx(2.9, abs=0.01)

            # Compact /assets/index catalog endpoint
            r_idx = await client.get("/api/v1/market/assets/index")
            assert r_idx.status_code == 200
            idx_items = r_idx.json()
            assert len(idx_items) == 5
            sample = idx_items[0]
            for field in (
                "symbol",
                "name",
                "asset_class",
                "currency",
                "last_price",
                "change_pct",
                "is_shariah_compliant",
            ):
                assert field in sample

            # Screener scan works cleanly with refactored universe helper
            r_scr = await client.get("/api/v1/market/screener/scan?market=PSX&limit=20")
            assert r_scr.status_code == 200
            assert len(r_scr.json()) == 3
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
