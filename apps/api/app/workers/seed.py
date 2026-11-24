from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import apply_rls_context
from app.models.market import (
    Announcement,
    Asset,
    AssetSymbolAlias,
    CorporateAction,
    FixedIncomeProfile,
    MutualFundProfile,
    PriceBar,
    PsxProfile,
    ScraperRun,
    ShariahMetric,
)
from app.models.user import User

D = Decimal


async def purge_legacy_demo_data(db: AsyncSession) -> None:
    """
    Remove legacy demo users, dead RUNNING/seed scraper logs, and synthetic `source='seed'`
    bars/announcements/ratios from prior installs so only real live market data remains.
    """
    await apply_rls_context(db, bypass=True)
    await db.execute(
        delete(User).where(
            (User.is_demo.is_(True)) | (User.email.like("demo%@openkhazana.local"))
        )
    )
    await db.execute(
        delete(ScraperRun).where(
            ScraperRun.error_detail.like("%Initial demo seed%")
        )
    )
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
    await db.execute(
        update(ScraperRun)
        .where(ScraperRun.status == "RUNNING", ScraperRun.started_at < stale_cutoff)
        .values(
            status="FAILED",
            finished_at=datetime.now(timezone.utc),
            error_detail="Interrupted prior process before completion.",
        )
    )

    seed_asset_ids = list((await db.execute(select(Asset.id).where(Asset.source == "seed"))).scalars())
    if seed_asset_ids:
        await db.execute(delete(PriceBar).where(PriceBar.asset_id.in_(seed_asset_ids)))
        await db.execute(delete(Announcement).where(Announcement.asset_id.in_(seed_asset_ids)))
        await db.execute(delete(CorporateAction).where(CorporateAction.asset_id.in_(seed_asset_ids)))
        await db.execute(
            update(PsxProfile)
            .where(PsxProfile.asset_id.in_(seed_asset_ids))
            .values(
                pe_ratio=None,
                pb_ratio=None,
                eps=None,
                roe_pct=None,
                financial_summary_json=[],
            )
        )
        await db.execute(
            update(Asset)
            .where(Asset.id.in_(seed_asset_ids))
            .values(source="reference_catalog")
        )

    intl_asset_ids = list(
        (
            await db.execute(
                select(Asset.id).where(Asset.asset_class.in_(["INTL_EQUITY", "INTL_ETF", "INTL_INDEX"]))
            )
        ).scalars()
    )
    if intl_asset_ids:
        await db.execute(
            delete(Announcement).where(
                Announcement.asset_id.in_(intl_asset_ids),
                Announcement.source_url.like("%dps.psx.com.pk%"),
            )
        )

    await db.commit()


async def bootstrap_reference_catalog(db: AsyncSession) -> None:
    """
    Bootstrap the canonical Pakistan & Global instrument catalog (symbols, aliases, exchange/country metadata,
    Constant-NAV Daily Dividend Fund rules, NSS rules, and KMI/AAOIFI Shariah screening metadata).
    Contains ZERO synthetic sine-wave price bars, ZERO fake announcements, and ZERO demo user transactions.
    All live prices, EOD history bars, fundamentals, and corporate actions are populated by live scrapers
    and on-demand backfill.
    """
    await apply_rls_context(db, bypass=True)
    await purge_legacy_demo_data(db)

    asset_count = (await db.execute(select(func.count(Asset.id)))).scalar_one()
    if asset_count > 0:
        return

    now = datetime.now(timezone.utc)

    # 1. Indices, Dual FX (Open & Interbank), Normalized Gold 24K/gram, Crypto, Cash
    macro_specs = [
        ("KSE100", "PSX KSE-100 Index", "PSX_INDEX", "PKR", "Index", False, "177873.87", "177373.76", "0.28", "195449769"),
        ("KMI30", "PSX KMI-30 Islamic Index", "PSX_INDEX", "PKR", "Islamic Index", True, "255042.46", "253872.99", "0.46", "112400000"),
        ("SPX", "S&P 500 Index", "INTL_INDEX", "USD", "Global Index", False, "5985.40", "5942.10", "0.73", "2450000000"),
        ("USD_PKR_OPEN", "USD / PKR (Open Market)", "FX", "PKR", "Currencies", False, "280.40", "280.10", "0.11", "0"),
        ("USD_PKR_INTERBANK", "USD / PKR (SBP Interbank)", "FX", "PKR", "Currencies", False, "278.45", "278.30", "0.05", "0"),
        ("AEDPKR", "UAE Dirham / PKR", "FX", "PKR", "Currencies", False, "76.35", "76.28", "0.09", "0"),
        ("SARPKR", "Saudi Riyal / PKR", "FX", "PKR", "Currencies", False, "74.77", "74.70", "0.09", "0"),
        ("GBPPKR", "British Pound / PKR", "FX", "PKR", "Currencies", False, "356.20", "355.10", "0.31", "0"),
        ("EURPKR", "Euro / PKR", "FX", "PKR", "Currencies", False, "304.80", "304.10", "0.23", "0"),
        ("GOLD_24K_GRAM", "Gold 24K (Per 1 Gram)", "GOLD", "PKR", "Precious Metals", True, "35045.00", "34890.00", "0.44", "0"),
        ("SILVER_GRAM", "Silver 999 (Per 1 Gram)", "GOLD", "PKR", "Precious Metals", True, "438.50", "434.20", "0.99", "0"),
        ("BTC", "Bitcoin", "CRYPTO", "USD", "Digital Assets", False, "66067.00", "69050.00", "-4.32", "0"),
        ("ETH", "Ethereum", "CRYPTO", "USD", "Digital Assets", False, "1857.35", "1959.30", "-5.20", "0"),
        ("USDT", "Tether USD", "CRYPTO", "USD", "Stablecoins", False, "1.00", "1.00", "0.00", "0"),
        ("PKR_CASH", "Pakistani Rupee Cash / Wallet", "CASH", "PKR", "Cash & Savings", True, "1.00", "1.00", "0.00", "0"),
        ("USD_CASH", "US Dollar Savings", "CASH", "USD", "Cash & Savings", False, "1.00", "1.00", "0.00", "0"),
        ("DHA_PLOT", "Custom Real Estate / Plot", "CUSTOM", "PKR", "Real Estate", True, "1.00", "1.00", "0.00", "0"),
    ]

    asset_map: dict[str, Asset] = {}
    for sym, name, cls, ccy, cat, shariah, lp, pc, chg, vol in macro_specs:
        a = Asset(
            symbol=sym,
            name=name,
            asset_class=cls,
            currency=ccy,
            sector_or_category=cat,
            is_shariah_compliant=shariah,
            last_price=D(lp),
            prev_close=D(pc),
            change_pct=D(chg),
            volume=D(vol),
            source="reference_catalog",
            updated_at=now,
        )
        db.add(a)
        asset_map[sym] = a

    await db.flush()
    db.add(AssetSymbolAlias(asset_id=asset_map["USD_PKR_OPEN"].id, old_symbol="USDPKR"))
    db.add(AssetSymbolAlias(asset_id=asset_map["SPX"].id, old_symbol="^GSPC"))

    # 2. Core PSX Blue-Chip Equities & ETFs (enriched and updated live by PsxIntradayScraper & PsxFundamentalsAndPucarsScraper)
    psx_specs = [
        {
            "symbol": "MEBL", "name": "Meezan Bank Limited", "sector": "Commercial Banks", "shariah": True, "kmi30": True, "kse100": True,
            "price": "556.25", "prev": "558.18", "vol": "372147", "mcap": "1005197.40", "pe": "10.43", "pb": None, "eps": "49.54", "roe": None, "dy": "5.39", "h52": "605.00", "l52": "410.00",
            "debt": "0.0", "nci": "2.1", "inc": "0.0", "ill": "42.0", "aliases": [],
        },
        {
            "symbol": "SYS", "name": "Systems Limited", "sector": "Technology & Communication", "shariah": True, "kmi30": True, "kse100": True,
            "price": "114.57", "prev": "115.10", "vol": "980200", "mcap": "164800", "pe": "16.90", "pb": None, "eps": "6.78", "roe": None, "dy": "1.24", "h52": "155.00", "l52": "95.00",
            "debt": "8.4", "nci": "3.2", "inc": "1.1", "ill": "56.0", "aliases": [],
        },
        {
            "symbol": "LUCK", "name": "Lucky Cement Limited", "sector": "Cement", "shariah": True, "kmi30": True, "kse100": True,
            "price": "443.04", "prev": "441.20", "vol": "412500", "mcap": "334600", "pe": "7.95", "pb": None, "eps": "55.70", "roe": None, "dy": "1.31", "h52": "510.00", "l52": "310.00",
            "debt": "14.2", "nci": "5.5", "inc": "1.9", "ill": "74.0", "aliases": [],
        },
        {
            "symbol": "EFERT", "name": "Engro Fertilizers Limited", "sector": "Fertilizer", "shariah": True, "kmi30": True, "kse100": True,
            "price": "214.30", "prev": "215.10", "vol": "1820000", "mcap": "286100", "pe": "9.12", "pb": None, "eps": "23.50", "roe": None, "dy": "12.83", "h52": "228.00", "l52": "132.00",
            "debt": "16.8", "nci": "4.1", "inc": "1.4", "ill": "68.5", "aliases": [],
        },
        {
            "symbol": "FFC", "name": "Fauji Fertilizer Company Limited", "sector": "Fertilizer", "shariah": True, "kmi30": True, "kse100": True,
            "price": "362.40", "prev": "358.10", "vol": "2610000", "mcap": "515800", "pe": "7.80", "pb": None, "eps": "46.45", "roe": None, "dy": "9.65", "h52": "388.00", "l52": "118.00",
            "debt": "11.2", "nci": "6.5", "inc": "1.8", "ill": "54.0", "aliases": ["FFBL"],
        },
        {
            "symbol": "OGDC", "name": "Oil & Gas Development Company Limited", "sector": "Oil & Gas Exploration Companies", "shariah": True, "kmi30": True, "kse100": True,
            "price": "315.87", "prev": "312.50", "vol": "8450000", "mcap": "1358400", "pe": "4.85", "pb": None, "eps": "50.30", "roe": None, "dy": "6.22", "h52": "335.00", "l52": "180.50",
            "debt": "2.4", "nci": "8.9", "inc": "2.8", "ill": "61.0", "aliases": [],
        },
        {
            "symbol": "HUBC", "name": "The Hub Power Company Limited", "sector": "Power Generation & Distribution", "shariah": True, "kmi30": True, "kse100": True,
            "price": "138.40", "prev": "137.00", "vol": "4920000", "mcap": "179500", "pe": "3.42", "pb": "0.94", "eps": "40.45", "roe": "29.10", "dy": "14.45", "h52": "168.00", "l52": "102.00",
            "debt": "24.5", "nci": "3.0", "inc": "1.2", "ill": "78.0", "aliases": [],
        },
        {
            "symbol": "ENGROH", "name": "Engro Holdings Limited", "sector": "Fertilizer", "shariah": True, "kmi30": True, "kse100": True,
            "price": "218.75", "prev": "215.20", "vol": "920000", "mcap": "264700", "pe": "7.20", "pb": "1.18", "eps": "30.38", "roe": "17.40", "dy": "8.46", "h52": "245.00", "l52": "148.00",
            "debt": "22.1", "nci": "9.8", "inc": "2.4", "ill": "64.0", "aliases": ["DAWH", "ENGRO"],
        },
        {
            "symbol": "MCB", "name": "MCB Bank Limited", "sector": "Commercial Banks", "shariah": False, "kmi30": False, "kse100": True,
            "price": "276.40", "prev": "278.00", "vol": "740000", "mcap": "327500", "pe": "5.10", "pb": "1.35", "eps": "54.20", "roe": "27.30", "dy": "13.02", "h52": "292.00", "l52": "179.00",
            "debt": "91.0", "nci": "45.0", "inc": "82.0", "ill": "18.0", "aliases": [],
        },
        {
            "symbol": "TRG", "name": "TRG Pakistan Limited", "sector": "Technology & Communication", "shariah": False, "kmi30": False, "kse100": True,
            "price": "62.40", "prev": "61.10", "vol": "9800000", "mcap": "34050", "pe": "18.40", "pb": "0.88", "eps": "3.39", "roe": "4.80", "dy": "0.00", "h52": "84.00", "l52": "46.50",
            "debt": "12.0", "nci": "36.0", "inc": "12.0", "ill": "22.0", "aliases": [],
        },
        {
            "symbol": "MIIETF", "name": "Mahaana Islamic Index ETF", "sector": "Exchange Traded Funds", "shariah": True, "kmi30": False, "kse100": False,
            "price": "14.25", "prev": "14.12", "vol": "640000", "mcap": "1425", "pe": None, "pb": "1.01", "eps": None, "roe": None, "dy": "3.50", "h52": "15.10", "l52": "10.10",
            "debt": "0.0", "nci": "0.0", "inc": "0.0", "ill": "90.0", "aliases": [],
        },
    ]

    for s in psx_specs:
        lp, pc = D(s["price"]), D(s["prev"])
        chg = ((lp - pc) / pc * D("100")).quantize(D("0.0001"))
        asset = Asset(
            symbol=s["symbol"],
            name=s["name"],
            asset_class="PSX_ETF" if s["symbol"].endswith("ETF") else "PSX_EQUITY",
            currency="PKR",
            sector_or_category=s["sector"],
            is_shariah_compliant=s["shariah"],
            last_price=lp,
            prev_close=pc,
            change_pct=chg,
            volume=D(s["vol"]),
            source="reference_catalog",
            updated_at=now,
        )
        db.add(asset)
        await db.flush()
        asset_map[s["symbol"]] = asset

        for old_sym in s["aliases"]:
            db.add(AssetSymbolAlias(asset_id=asset.id, old_symbol=old_sym))

        db.add(
            PsxProfile(
                asset_id=asset.id,
                market_cap_mn=D(s["mcap"]),
                free_float_pct=D("25.00"),
                pe_ratio=D(s["pe"]) if s["pe"] else None,
                pb_ratio=D(s["pb"]) if s["pb"] else None,
                eps=D(s["eps"]) if s["eps"] else None,
                roe_pct=D(s["roe"]) if s["roe"] else None,
                dividend_yield_pct=D(s["dy"]),
                high_52w=D(s["h52"]),
                low_52w=D(s["l52"]),
                is_kse100=s["kse100"],
                is_kmi30=s["kmi30"],
                financial_summary_json=[],
            )
        )
        db.add(
            ShariahMetric(
                asset_id=asset.id,
                fiscal_period="KMI-AllShare",
                core_business_compliant=s["shariah"],
                is_kmi_all_share=s["shariah"],
                debt_to_assets_pct=D(s["debt"]),
                non_compliant_inv_pct=D(s["nci"]),
                non_compliant_income_pct=D(s["inc"]),
                purification_rate_pct=D(s["inc"]),
                illiquid_assets_pct=D(s["ill"]),
                passed=s["shariah"],
            )
        )

    # 2b. International Stocks & Global ETFs (refreshed live via `yfinance` IntlStocksScraper & on-demand backfill)
    intl_specs = [
        {
            "symbol": "NVDA", "name": "NVIDIA Corporation", "cls": "INTL_EQUITY", "exchange": "NASDAQ", "sector": "Semiconductors & AI", "shariah": True,
            "price": "138.60", "prev": "135.40", "vol": "48500000", "mcap": "3395000", "pe": "52.40", "pb": "44.10", "eps": "2.64", "roe": "115.20", "dy": "0.03", "h52": "149.76", "l52": "75.60",
            "debt": "4.2", "nci": "6.5", "inc": "0.8", "ill": "48.0",
        },
        {
            "symbol": "AAPL", "name": "Apple Inc.", "cls": "INTL_EQUITY", "exchange": "NASDAQ", "sector": "Consumer Technology", "shariah": True,
            "price": "228.50", "prev": "226.80", "vol": "39200000", "mcap": "3460000", "pe": "34.80", "pb": "51.20", "eps": "6.57", "roe": "147.40", "dy": "0.44", "h52": "237.23", "l52": "164.08",
            "debt": "28.5", "nci": "9.4", "inc": "1.2", "ill": "52.0",
        },
        {
            "symbol": "MSFT", "name": "Microsoft Corporation", "cls": "INTL_EQUITY", "exchange": "NASDAQ", "sector": "Cloud & Enterprise Software", "shariah": True,
            "price": "426.90", "prev": "423.10", "vol": "18400000", "mcap": "3170000", "pe": "35.10", "pb": "11.40", "eps": "12.16", "roe": "36.20", "dy": "0.78", "h52": "468.35", "l52": "385.58",
            "debt": "18.1", "nci": "11.2", "inc": "1.6", "ill": "58.0",
        },
        {
            "symbol": "SPUS", "name": "SP Funds S&P 500 Sharia Industry Exclusions ETF", "cls": "INTL_ETF", "exchange": "NYSE", "sector": "Global Shariah Equity ETF", "shariah": True,
            "price": "43.15", "prev": "42.82", "vol": "320000", "mcap": "945", "pe": "29.40", "pb": "7.80", "eps": "1.47", "roe": "28.50", "dy": "0.82", "h52": "44.10", "l52": "34.20",
            "debt": "0.0", "nci": "0.0", "inc": "0.4", "ill": "88.0",
        },
        {
            "symbol": "HLAL", "name": "Wahed FTSE USA Shariah ETF", "cls": "INTL_ETF", "exchange": "NASDAQ", "sector": "Global Shariah Equity ETF", "shariah": True,
            "price": "53.40", "prev": "53.05", "vol": "195000", "mcap": "620", "pe": "28.10", "pb": "7.20", "eps": "1.90", "roe": "27.10", "dy": "0.68", "h52": "54.80", "l52": "43.10",
            "debt": "0.0", "nci": "0.0", "inc": "0.3", "ill": "89.0",
        },
        {
            "symbol": "VOO", "name": "Vanguard S&P 500 ETF", "cls": "INTL_ETF", "exchange": "NYSE", "sector": "US Broad Index ETF", "shariah": False,
            "price": "548.20", "prev": "544.10", "vol": "4950000", "mcap": "512000", "pe": "27.50", "pb": "4.95", "eps": "19.93", "roe": "24.00", "dy": "1.24", "h52": "555.40", "l52": "442.10",
            "debt": "42.0", "nci": "25.0", "inc": "8.5", "ill": "60.0",
        },
        {
            "symbol": "SCHD", "name": "Schwab U.S. Dividend Equity ETF", "cls": "INTL_ETF", "exchange": "NYSE", "sector": "US Dividend Equity ETF", "shariah": False,
            "price": "28.85", "prev": "28.72", "vol": "12400000", "mcap": "64800", "pe": "17.40", "pb": "3.20", "eps": "1.66", "roe": "22.50", "dy": "3.48", "h52": "29.45", "l52": "24.10",
            "debt": "38.0", "nci": "18.0", "inc": "6.2", "ill": "65.0",
        },
        {
            "symbol": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF", "cls": "INTL_ETF", "exchange": "NYSE", "sector": "USD Daily Yield Treasury ETF", "shariah": False,
            "price": "100.42", "prev": "100.40", "vol": "8200000", "mcap": "28400", "pe": None, "pb": "1.00", "eps": None, "roe": None, "dy": "5.12", "h52": "100.74", "l52": "100.04",
            "debt": "100.0", "nci": "100.0", "inc": "100.0", "ill": "0.0",
        },
    ]

    for s in intl_specs:
        lp, pc = D(s["price"]), D(s["prev"])
        chg = ((lp - pc) / pc * D("100")).quantize(D("0.0001"))
        asset = Asset(
            symbol=s["symbol"],
            name=s["name"],
            asset_class=s["cls"],
            currency="USD",
            sector_or_category=s["sector"],
            is_shariah_compliant=s["shariah"],
            last_price=lp,
            prev_close=pc,
            change_pct=chg,
            volume=D(s["vol"]),
            source="reference_catalog",
            updated_at=now,
        )
        db.add(asset)
        await db.flush()
        asset_map[s["symbol"]] = asset

        db.add(
            PsxProfile(
                asset_id=asset.id,
                exchange=s["exchange"],
                country="US",
                default_wht_rate_pct=D("15.00"),
                market_cap_mn=D(s["mcap"]),
                free_float_pct=D("85.00"),
                pe_ratio=D(s["pe"]) if s["pe"] else None,
                pb_ratio=D(s["pb"]) if s["pb"] else None,
                eps=D(s["eps"]) if s["eps"] else None,
                roe_pct=D(s["roe"]) if s["roe"] else None,
                dividend_yield_pct=D(s["dy"]),
                high_52w=D(s["h52"]),
                low_52w=D(s["l52"]),
                is_kse100=False,
                is_kmi30=False,
                financial_summary_json=[],
            )
        )
        db.add(
            ShariahMetric(
                asset_id=asset.id,
                fiscal_period="AAOIFI-Global",
                core_business_compliant=s["shariah"],
                is_kmi_all_share=False,
                debt_to_assets_pct=D(s["debt"]),
                non_compliant_inv_pct=D(s["nci"]),
                non_compliant_income_pct=D(s["inc"]),
                purification_rate_pct=D(s["inc"]),
                illiquid_assets_pct=D(s["ill"]),
                passed=s["shariah"],
            )
        )

    # 3. Pakistan Mutual Funds, Constant-NAV Daily Dividend Funds & VPS Pension Funds
    fund_specs = [
        ("MRAF", "Meezan Rozana Amdani Fund", "MUTUAL_FUND", "Al Meezan Investment Management", "Shariah Compliant Money Market (Daily Dividend)", "Low", True, "50.0000", "50.0000", "0.78", "0.45", "0.00", "0.00", "146800", "1.412000", "0.0245", "10.27", "10.21", "9.91", "57.80", "PKISRV Avg / 3M Islamic Deposit", True, "50.0000", "10.27"),
        ("NIDDF", "NBP Islamic Daily Dividend Fund", "MUTUAL_FUND", "NBP Fund Management", "Shariah Compliant Money Market (Daily Dividend)", "Low", True, "10.0000", "10.0000", "0.82", "0.50", "0.00", "0.00", "64200", "1.396000", "0.0048", "10.15", "10.10", "10.15", "56.40", "3M Islamic Bank Deposit", True, "10.0000", "10.15"),
        ("MICF", "Mahaana Islamic Cash Fund", "MUTUAL_FUND", "Mahaana Wealth Limited", "Shariah Compliant Money Market (Daily Accrual)", "Low", True, "102.4500", "102.4000", "0.55", "0.35", "0.00", "0.00", "18500", "1.385000", "0.0496", "10.45", "10.35", "10.50", "58.90", "PKISRV Avg", True, "100.0000", "10.45"),
        ("MIF", "Meezan Islamic Fund", "MUTUAL_FUND", "Al Meezan Investment Management", "Shariah Compliant Equity", "High", True, "163.9115", "162.8000", "2.68", "2.00", "2.00", "0.00", "41250", "1.124000", "4.50", "7.05", "-3.91", "33.02", "128.40", "KMI-30", False, None, None),
        ("MCF", "Meezan Cash Fund", "MUTUAL_FUND", "Al Meezan Investment Management", "Shariah Compliant Money Market", "Low", True, "53.5609", "53.5100", "0.95", "0.55", "0.00", "0.00", "128400", "1.348000", "0.75", "10.61", "10.17", "9.88", "56.10", "PKISRV Avg", True, "50.0000", "10.61"),
        ("NBPISF", "NBP Islamic Sarmaya Izafa Fund", "MUTUAL_FUND", "NBP Fund Management", "Shariah Compliant Asset Allocation", "Medium-High", True, "34.6591", "34.4200", "2.85", "1.80", "3.00", "0.00", "9800", "1.085000", "1.20", "3.20", "17.10", "44.20", "94.50", "KMI-30 / 6M KIBOR", False, None, None),
        ("UBLSF", "UBL Stock Advantage Fund", "MUTUAL_FUND", "UBL Fund Managers", "Equity", "High", False, "132.8000", "131.9000", "2.94", "2.00", "2.50", "0.00", "14600", "1.142000", "5.00", "4.90", "21.80", "55.40", "119.20", "KSE-100", False, None, None),
        ("AKDIF", "AKD Index Tracker Fund", "MUTUAL_FUND", "AKD Investment Management", "Index Tracker", "High", False, "24.1800", "24.0200", "1.35", "0.75", "1.00", "0.00", "2150", "1.050000", "0.80", "4.40", "20.90", "53.10", "114.80", "KSE-100", False, None, None),
        ("MCBMF", "MCB Cash Management Optimizer", "MUTUAL_FUND", "MCB Investment Management", "Money Market", "Low", False, "106.4200", "106.3600", "0.88", "0.50", "0.00", "0.00", "86500", "1.362000", "1.45", "10.35", "10.20", "10.40", "58.40", "70% 3M PKRV + 30% Deposit", True, "100.0000", "10.35"),
        ("MTPF_EQ", "Meezan Tahaffuz Pension Fund - Equity", "VPS_PENSION", "Al Meezan Investment Management", "VPS Shariah Equity", "High", True, "1817.3552", "1804.5000", "1.95", "1.50", "1.50", "0.00", "7420", "1.000000", "0.00", "7.07", "-2.35", "30.36", "134.90", "KMI-30", False, None, None),
    ]

    for sym, name, cls, amc, cat, risk, shariah, nav, prev, ter, mgmt, fel, bel, aum, adj, ldist, r1m, r6m, r1y, r3y, bench, is_daily_div, par_nav, ann_yield in fund_specs:
        lp, pc = D(nav), D(prev)
        chg = ((lp - pc) / pc * D("100")).quantize(D("0.0001")) if pc > 0 else D("0")
        a = Asset(
            symbol=sym,
            name=name,
            asset_class=cls,
            currency="PKR",
            sector_or_category=cat,
            is_shariah_compliant=shariah,
            last_price=lp,
            prev_close=pc,
            change_pct=chg,
            source="reference_catalog",
            updated_at=now,
        )
        db.add(a)
        await db.flush()
        asset_map[sym] = a
        db.add(
            MutualFundProfile(
                asset_id=a.id,
                amc_name=amc,
                fund_category=cat,
                risk_profile=risk,
                ter_pct=D(ter),
                management_fee_pct=D(mgmt),
                front_end_load_pct=D(fel),
                back_end_load_pct=D(bel),
                aum_mn=D(aum),
                adjusted_nav_multiplier=D(adj),
                last_distribution_pkr=D(ldist),
                return_1m_pct=D(r1m),
                return_6m_pct=D(r6m),
                return_1y_pct=D(r1y),
                return_3y_pct=D(r3y),
                benchmark_name=bench,
                is_daily_dividend=is_daily_div,
                constant_par_nav=D(par_nav) if par_nav else None,
                annualized_yield_pct=D(ann_yield) if ann_yield else None,
                default_payout_mode="REINVEST",
            )
        )

    # 4. National Savings (NSS) Fixed-Income Accrual Instruments
    nss_specs = [
        ("BEHBOOD_NSS", "Behbood Savings Certificates (NSS)", "NSS", "BEHBOOD", "13.68", "MONTHLY", True),
        ("SARWA_ISLAMIC_NSS", "Sarwa Islamic Term Account (NSS)", "NSS", "SARWA_ISLAMIC", "11.40", "MONTHLY", False),
    ]
    for sym, name, cls, itype, rate, freq, wht_ex in nss_specs:
        a = Asset(
            symbol=sym,
            name=name,
            asset_class=cls,
            currency="PKR",
            sector_or_category="National Savings",
            is_shariah_compliant=(itype == "SARWA_ISLAMIC"),
            last_price=D("1.00"),
            prev_close=D("1.00"),
            change_pct=D("0.00"),
            source="reference_catalog",
            updated_at=now,
        )
        db.add(a)
        await db.flush()
        asset_map[sym] = a
        db.add(
            FixedIncomeProfile(
                asset_id=a.id,
                instrument_type=itype,
                annual_profit_rate_pct=D(rate),
                payout_frequency=freq,
                is_wht_exempt=wht_ex,
            )
        )

    await db.commit()


# Keep backward-compatible function name used by startup lifespan & tests
seed_if_empty = bootstrap_reference_catalog
