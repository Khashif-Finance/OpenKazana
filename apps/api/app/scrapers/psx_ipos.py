"""
PSX IPOs, GEM Board Offerings, and Official Listing Registry Scraper.

Combines:
  1. Official PSX IPO reference registry & verified recent PSX Main/GEM Board listings (2023-2026)
     cross-linked against live PSX market prices in `assets` to calculate live Post-IPO Return %.
  2. Live scraping of `psx.com.pk` official IPO guides/resources + PUCARS announcements tagged
     with IPO / Book Building / Listing / Prospectus notices so new upcoming IPOs auto-surface.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Announcement, Asset, IpoListing
from app.scrapers.base import BaseScraper

logger = logging.getLogger("openkhazana.scrapers.psx_ipos")
D = Decimal

PSX_IPO_PAGE_URL = "https://www.psx.com.pk/psx/product-and-services/products/initial-public-offerings-ipos"

# Official PSX IPO educational/regulatory PDFs hosted directly on psx.com.pk
PSX_IPO_GUIDES: list[dict[str, str]] = [
    {
        "title": "PSX Book Building System Manual (v1.2)",
        "category": "Regulatory Manual",
        "url": "https://www.psx.com.pk/psx/themes/psx/uploads/PSX-Book-Building-System-Manual-Version-1.2.pdf",
    },
    {
        "title": "PSX Listing Guide (Updated Feb 2025)",
        "category": "Issuer Guide",
        "url": "https://www.psx.com.pk/psx/themes/psx/uploads/Listing-guide-02-2025.pdf",
    },
    {
        "title": "Consultant-wise PSX IPO Summary (2021–2025)",
        "category": "Market Statistics",
        "url": "https://www.psx.com.pk/psx/themes/psx/uploads/Consultant-wise-IPO-Summary-2021-2025.pdf",
    },
    {
        "title": "PSX IPO Market Overview & Regulatory Initiatives",
        "category": "Market Overview",
        "url": "https://www.psx.com.pk/psx/themes/psx/uploads/Initial_Public_Offering-_Market_Overview_and_Regulatory_Initiatives.pdf",
    },
    {
        "title": "List of Consultants to the Issue (SECP / PSX)",
        "category": "Consultants Directory",
        "url": "https://www.psx.com.pk/psx/themes/psx/uploads/List-of-Consultant-to-Issue-as-of-June-30-2025.pdf",
    },
]

# Factual PSX Main & GEM Board IPOs (2023-2026) — linked dynamically with live PSX prices in `assets`
VERIFIED_PSX_IPOS: list[dict[str, Any]] = [
    {
        "slug": "barkat-frisian-agro-2025",
        "company_name": "Barkat Frisian Agro Limited",
        "symbol": "BFAGRO",
        "sector": "FOOD & PERSONAL CARE PRODUCTS",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Book Building + General Public",
        "floor_price": D("13.00"),
        "strike_price": D("18.20"),
        "issue_size_mn": D("1232.69"),
        "subscription_multiple": D("16.25"),
        "book_building_date": date(2025, 2, 17),
        "listing_date": date(2025, 3, 17),
        "lead_manager": "Arif Habib Limited",
        "source_url": "https://dps.psx.com.pk/company/BFAGRO",
        "summary": "Pakistan's only pasteurized egg products producer listed on PSX Main Board; oversubscribed 16.25x at 40% ceiling strike price of Rs 18.20/share.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "zarea-limited-2025",
        "company_name": "Zarea Limited",
        "symbol": "ZAL",
        "sector": "TECHNOLOGY & COMMUNICATION",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Book Building + General Public",
        "floor_price": D("16.00"),
        "strike_price": D("16.50"),
        "issue_size_mn": D("1031.25"),
        "subscription_multiple": D("1.90"),
        "book_building_date": date(2025, 2, 10),
        "listing_date": date(2025, 2, 28),
        "lead_manager": "Topline Securities & Growth Securities",
        "source_url": "https://dps.psx.com.pk/company/ZAL",
        "summary": "Pakistan's B2B commodities digital marketplace connecting cement, steel, agri & industrial raw material buyers and suppliers across Pakistan.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "bf-biosciences-2024",
        "company_name": "BF Biosciences Limited",
        "symbol": "BFBIO",
        "sector": "PHARMACEUTICALS",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Book Building + General Public",
        "floor_price": D("55.00"),
        "strike_price": D("77.00"),
        "issue_size_mn": D("1925.00"),
        "subscription_multiple": D("3.40"),
        "book_building_date": date(2024, 9, 25),
        "listing_date": date(2024, 10, 21),
        "lead_manager": "Arif Habib Limited",
        "source_url": "https://dps.psx.com.pk/company/BFBIO",
        "summary": "Ferozsons Laboratories biotech joint venture manufacturing biological & sterile injectable pharmaceuticals; hit 40% max strike price of Rs 77.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "fast-cables-2024",
        "company_name": "Fast Cables Limited",
        "symbol": "FCL",
        "sector": "CABLE & ELECTRICAL GOODS",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Book Building + General Public",
        "floor_price": D("23.50"),
        "strike_price": D("24.45"),
        "issue_size_mn": D("3129.60"),
        "subscription_multiple": D("1.57"),
        "book_building_date": date(2024, 5, 15),
        "listing_date": date(2024, 6, 10),
        "lead_manager": "AKD Securities Limited",
        "source_url": "https://dps.psx.com.pk/company/FCL",
        "summary": "One of Pakistan's largest electrical cable and conductor manufacturers; raised Rs 3.13 Bn for capacity expansion and debt retirement.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "tpl-reit-fund-i-2024",
        "company_name": "TPL REIT Fund I",
        "symbol": "TPLRF1",
        "sector": "REAL ESTATE INVESTMENT TRUST",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Offer for Sale (Fixed Price)",
        "floor_price": D("17.59"),
        "strike_price": D("17.59"),
        "issue_size_mn": D("402.81"),
        "subscription_multiple": D("1.10"),
        "book_building_date": date(2024, 5, 2),
        "listing_date": date(2024, 5, 29),
        "lead_manager": "Next Capital Limited",
        "source_url": "https://dps.psx.com.pk/company/TPLRF1",
        "summary": "Pakistan's first Shariah-compliant hybrid developmental REIT fund spanning Mangrove, One Hoshang, and Technology Park Karachi.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "secure-logistics-group-2024",
        "company_name": "Secure Logistics Group Limited",
        "symbol": "SLGL",
        "sector": "TRANSPORT",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Fixed Price IPO",
        "floor_price": D("12.00"),
        "strike_price": D("12.00"),
        "issue_size_mn": D("600.00"),
        "subscription_multiple": D("1.16"),
        "book_building_date": date(2024, 3, 27),
        "listing_date": date(2024, 4, 22),
        "lead_manager": "Arif Habib Limited",
        "source_url": "https://dps.psx.com.pk/company/SLGL",
        "summary": "Integrated B2B logistics, long-haul fleet, asset tracking, and security services provider listed on PSX Main Board.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "stylish-garments-gem-2024",
        "company_name": "Stylers International Limited",
        "symbol": "STYLERS",
        "sector": "TEXTILE COMPOSITE",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Scheme of Arrangement / Direct Listing",
        "floor_price": D("40.00"),
        "strike_price": D("40.00"),
        "issue_size_mn": None,
        "subscription_multiple": None,
        "book_building_date": None,
        "listing_date": date(2024, 1, 15),
        "lead_manager": "PSX Direct Listing",
        "source_url": "https://dps.psx.com.pk/company/STYLERS",
        "summary": "Export-oriented denim apparel manufacturer listed on PSX following corporate restructuring.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "symmetry-group-2023",
        "company_name": "Symmetry Group Limited",
        "symbol": "SYM",
        "sector": "TECHNOLOGY & COMMUNICATION",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "Book Building + General Public",
        "floor_price": D("4.25"),
        "strike_price": D("4.30"),
        "issue_size_mn": D("435.38"),
        "subscription_multiple": D("1.46"),
        "book_building_date": date(2023, 8, 8),
        "listing_date": date(2023, 9, 1),
        "lead_manager": "Topline Securities Limited",
        "source_url": "https://dps.psx.com.pk/company/SYM",
        "summary": "Digital transformation, analytics, and interactive marketing technology group listed on PSX Main Board.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "image-reit-2024",
        "company_name": "Image REIT",
        "symbol": "IMAGER",
        "sector": "REAL ESTATE INVESTMENT TRUST",
        "board": "MAIN",
        "status": "LISTED",
        "issue_type": "REIT Listing",
        "floor_price": D("10.00"),
        "strike_price": D("10.00"),
        "issue_size_mn": None,
        "subscription_multiple": None,
        "book_building_date": None,
        "listing_date": date(2024, 7, 5),
        "lead_manager": "Sinclair / Image Pakistan",
        "source_url": "https://dps.psx.com.pk/company/IMAGER",
        "summary": "Shariah-compliant rental & real-estate investment trust sponsored by Image Pakistan.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "pak-agro-packaging-gem",
        "company_name": "Pak Agro Packaging Limited (GEM)",
        "symbol": "GEMPAPL",
        "sector": "PAPER, BOARD & PACKAGING",
        "board": "GEM",
        "status": "LISTED",
        "issue_type": "GEM Board Accredited Investors Offer",
        "floor_price": D("24.75"),
        "strike_price": D("24.75"),
        "issue_size_mn": D("198.00"),
        "subscription_multiple": D("1.66"),
        "book_building_date": date(2021, 11, 24),
        "listing_date": date(2021, 12, 6),
        "lead_manager": "AKD Securities Limited",
        "source_url": "https://dps.psx.com.pk/company/GEMPAPL",
        "summary": "First-ever company listed on PSX Growth Enterprise Market (GEM) Board manufacturing agricultural packaging fabrics.",
        "is_shariah_compliant": True,
    },
    {
        "slug": "blue-ex-universal-network-gem",
        "company_name": "Universal Network Systems Limited (Blue-EX)",
        "symbol": "GEMUNSL",
        "sector": "TRANSPORT",
        "board": "GEM",
        "status": "LISTED",
        "issue_type": "GEM Board Book Building",
        "floor_price": D("65.00"),
        "strike_price": D("65.00"),
        "issue_size_mn": D("445.71"),
        "subscription_multiple": D("1.52"),
        "book_building_date": date(2021, 11, 26),
        "listing_date": date(2021, 12, 6),
        "lead_manager": "Topline Securities Limited",
        "source_url": "https://dps.psx.com.pk/company/GEMUNSL",
        "summary": "E-commerce logistics and courier network (Blue-EX) listed on PSX Growth Enterprise Market (GEM) Board.",
        "is_shariah_compliant": True,
    },
]


class PsxIpoAndListingsScraper(BaseScraper):
    """Hydrates official PSX Main & GEM Board IPOs and auto-discovers new IPO/Listing notices from PUCARS & psx.com.pk."""

    name = "psx_ipos"

    async def run(self, db: AsyncSession) -> int:
        existing_map = {
            row.slug: row
            for row in (await db.execute(select(IpoListing))).scalars().all()
        }
        upserted = 0

        # 1. Upsert verified PSX Main & GEM Board IPO registry
        for rec in VERIFIED_PSX_IPOS:
            obj = existing_map.get(rec["slug"])
            if obj is None:
                obj = IpoListing(**rec)
                db.add(obj)
                existing_map[rec["slug"]] = obj
                upserted += 1
            else:
                # Keep factual metrics synced without duplicating
                obj.status = rec["status"]
                obj.strike_price = rec["strike_price"]
                obj.floor_price = rec["floor_price"]
                obj.subscription_multiple = rec["subscription_multiple"]
                obj.summary = rec["summary"]

        # 2. Auto-detect any additional GEM-board assets in `assets` not yet listed
        gem_assets = (
            await db.execute(
                select(Asset).where(
                    Asset.owner_user_id.is_(None),
                    Asset.symbol.ilike("GEM%"),
                )
            )
        ).scalars().all()
        existing_syms = {row.symbol.upper() for row in existing_map.values() if row.symbol}
        for ga in gem_assets:
            sym = ga.symbol.upper()
            if sym in existing_syms:
                continue
            slug = f"psx-gem-{sym.lower()}"
            if slug in existing_map:
                continue
            db.add(
                IpoListing(
                    slug=slug,
                    company_name=ga.name,
                    symbol=sym,
                    sector=ga.sector_or_category or "GEM Board",
                    board="GEM",
                    status="LISTED",
                    issue_type="GEM Board Listing",
                    source_url=f"https://dps.psx.com.pk/company/{sym}",
                    summary=f"Listed on PSX Growth Enterprise Market (GEM) Board in {ga.sector_or_category}.",
                    is_shariah_compliant=ga.is_shariah_compliant,
                )
            )
            existing_syms.add(sym)
            upserted += 1

        # 3. Scan recent PUCARS announcements for newly announced IPOs / Book Building / Prospectus filings
        ipo_anns = (
            await db.execute(
                select(Announcement, Asset)
                .join(Asset, Announcement.asset_id == Asset.id)
                .where(
                    (Announcement.title.ilike("%initial public offering%"))
                    | (Announcement.title.ilike("%book building%"))
                    | (Announcement.title.ilike("%prospectus%"))
                    | (Announcement.title.ilike("%new listing%"))
                )
                .order_by(Announcement.published_at.desc())
                .limit(20)
            )
        ).all()
        for ann, asset in ipo_anns:
            sym = asset.symbol.upper()
            if sym in existing_syms:
                # Attach prospectus link if missing
                for item in existing_map.values():
                    if item.symbol and item.symbol.upper() == sym and not item.prospectus_url and ann.source_url:
                        item.prospectus_url = ann.source_url
                continue
            slug = f"pucars-ipo-{sym.lower()}"
            if slug in existing_map:
                continue
            db.add(
                IpoListing(
                    slug=slug,
                    company_name=asset.name,
                    symbol=sym,
                    sector=asset.sector_or_category or "General",
                    board="GEM" if sym.startswith("GEM") else "MAIN",
                    status="BOOK_BUILDING" if "book building" in ann.title.lower() else "UPCOMING",
                    issue_type="PUCARS IPO / Prospectus Notice",
                    prospectus_url=ann.source_url,
                    source_url=f"https://dps.psx.com.pk/company/{sym}",
                    summary=ann.title[:300],
                    is_shariah_compliant=asset.is_shariah_compliant,
                )
            )
            existing_syms.add(sym)
            upserted += 1

        # 4. Lightweight probe of PSX IPO page for any newly posted prospectus PDF links (best-effort)
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(PSX_IPO_PAGE_URL)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    for a_tag in soup.find_all("a", href=True):
                        href = str(a_tag.get("href") or "")
                        txt = a_tag.get_text(" ", strip=True)
                        if ".pdf" in href.lower() and "prospectus" in (href + " " + txt).lower():
                            clean_title = re.sub(r"\s+", " ", txt).strip()[:200]
                            if not clean_title:
                                continue
                            slug = "psx-pdf-" + re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")[:90]
                            if slug not in existing_map:
                                full_url = href if href.startswith("http") else f"https://www.psx.com.pk{href}"
                                db.add(
                                    IpoListing(
                                        slug=slug,
                                        company_name=clean_title,
                                        symbol=None,
                                        sector="PSX IPO Filing",
                                        board="MAIN",
                                        status="UPCOMING",
                                        issue_type="Draft / Final Prospectus",
                                        prospectus_url=full_url[:590],
                                        source_url=PSX_IPO_PAGE_URL,
                                        summary=f"Official prospectus filing detected on PSX IPO portal: {clean_title}.",
                                    )
                                )
                                existing_map[slug] = True  # type: ignore[assignment]
                                upserted += 1
        except Exception as exc:
            logger.debug("PSX IPO page live check skipped: %s", exc)

        await db.commit()
        return upserted
