"""
Pakistan & Global Financial News Aggregator (Open RSS Feeds + Auto Ticker/Sector Tagger).

Aggregates live business, market, and corporate headlines from open RSS feeds:
  - Business Recorder (Latest News & Markets)
  - Profit by Pakistan Today (via open Google News RSS publisher query)
  - Dawn Business
  - Express Tribune Business
  - Investing.com Markets

Automatically matches every article against all PSX equity tickers, company names/aliases,
and official PSX sectors so stock detail pages (`/stocks/[symbol]`), sector pages, and the
News Hub (`/markets/news`) surface relevant stock-specific & industry-specific coverage.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Asset, NewsArticle
from app.scrapers.base import BaseScraper

logger = logging.getLogger("openkhazana.scrapers.pk_news")

NEWS_FEEDS: list[dict[str, str]] = [
    {
        "source_key": "brecorder",
        "source_name": "Business Recorder",
        "url": "https://www.brecorder.com/feeds/markets",
        "default_category": "Markets",
    },
    {
        "source_key": "brecorder",
        "source_name": "Business Recorder",
        "url": "https://www.brecorder.com/feeds/latest-news",
        "default_category": "Economy",
    },
    {
        "source_key": "profit",
        "source_name": "Profit (Pakistan Today)",
        "url": "https://news.google.com/rss/search?q=site:profit.pakistantoday.com.pk&hl=en-PK&gl=PK&ceid=PK:en",
        "default_category": "Corporate",
    },
    {
        "source_key": "dawn",
        "source_name": "Dawn Business",
        "url": "https://www.dawn.com/feeds/business",
        "default_category": "Economy",
    },
    {
        "source_key": "tribune",
        "source_name": "Express Tribune",
        "url": "https://tribune.com.pk/feed/business",
        "default_category": "Economy",
    },
    {
        "source_key": "investing",
        "source_name": "Investing.com",
        "url": "https://www.investing.com/rss/news_25.rss",
        "default_category": "Markets",
    },
]

# Common English words / short tickers excluded from bare uppercase symbol regex matching
STOPWORD_SYMBOLS = {
    "ALL", "AND", "AIR", "ARE", "ART", "ASK", "AUTO", "BIG", "CAP", "CAR", "CASH",
    "DAY", "END", "FAR", "FAST", "FOR", "GAS", "GET", "GOLD", "HAS", "HIGH", "HOLD",
    "ICE", "IMAGE", "KEY", "LAW", "LOW", "MAN", "MAX", "MAY", "MET", "NEW", "NET",
    "NOW", "OIL", "ONE", "OUT", "PAK", "PAY", "PEN", "PER", "PIN", "PRO", "RED",
    "RUN", "SEA", "SET", "SKY", "SUN", "TAX", "THE", "TOP", "TWO", "USE", "WIN",
    "YES", "YOU", "PACE", "LOADS", "UNITY", "PREMA", "WAVES", "TREAT", "SHIELD",
    "SERVICE", "SINGER", "POWER", "SUGAR", "STEEL", "FIRST", "TRUST", "PRIME",
}

# Authoritative Pakistani company alias dictionary -> canonical PSX ticker
COMPANY_ALIASES: dict[str, str] = {
    "meezan bank": "MEBL",
    "lucky cement": "LUCK",
    "oil and gas development": "OGDC",
    "oil & gas development": "OGDC",
    "ogdcl": "OGDC",
    "pakistan petroleum": "PPL",
    "mari energies": "MARI",
    "mari petroleum": "MARI",
    "pakistan oilfields": "POL",
    "pakistan state oil": "PSO",
    "fauji fertilizer": "FFC",
    "engro fertilizers": "EFERT",
    "engro holdings": "ENGROH",
    "engro corporation": "ENGROH",
    "engro polymer": "EPCL",
    "hub power": "HUBC",
    "hubco": "HUBC",
    "k-electric": "KEL",
    "karachi electric": "KEL",
    "systems limited": "SYS",
    "interloop": "ILP",
    "indus motor": "INDU",
    "toyota indus": "INDU",
    "honda atlas cars": "HCAR",
    "pak suzuki": "PSMC",
    "millat tractors": "MTL",
    "sazgar engineering": "SAZEW",
    "sazgar": "SAZEW",
    "maple leaf cement": "MLCF",
    "fauji cement": "FCCL",
    "cherat cement": "CHCC",
    "d.g. khan cement": "DGKC",
    "dg khan cement": "DGKC",
    "bestway cement": "BWCL",
    "pioneer cement": "PIOC",
    "kohat cement": "KOHC",
    "habib bank": "HBL",
    "united bank limited": "UBL",
    "mcb bank": "MCB",
    "bank al habib": "BAHL",
    "bank alfalah": "BAFL",
    "national bank of pakistan": "NBP",
    "faysal bank": "FABL",
    "bank of punjab": "BOP",
    "askari bank": "AKBL",
    "habib metropolitan": "HMB",
    "bankislami": "BIPL",
    "soneri bank": "SNBL",
    "standard chartered pakistan": "SCBPL",
    "attock refinery": "ATRL",
    "pakistan refinery": "PRL",
    "national refinery": "NRL",
    "cynergico": "CNERGY",
    "sui northern": "SNGP",
    "sngpl": "SNGP",
    "sui southern": "SSGC",
    "ssgcl": "SSGC",
    "air link communication": "AIRLINK",
    "ptcl": "PTC",
    "pakistan telecommunication": "PTC",
    "nishat mills": "NML",
    "nishat chunian": "NCL",
    "gul ahmed": "GATM",
    "glaxosmithkline": "GLAXO",
    "gsk pakistan": "GLAXO",
    "the searle company": "SEARL",
    "searle": "SEARL",
    "abbott laboratories": "ABOT",
    "haleon pakistan": "HALEON",
    "highnoon laboratories": "HINOON",
    "nestle pakistan": "NESTLE",
    "national foods": "NATF",
    "unilever pakistan": "UPFL",
    "colgate-palmolive": "COLG",
    "colgate pakistan": "COLG",
    "frieslandcampina": "FCEPL",
    "fauji foods": "FFL",
    "ghani glass": "GHGL",
    "tariq glass": "TGL",
    "packages limited": "PKGS",
    "international steels": "ISL",
    "mughal iron": "MUGHAL",
    "amreli steels": "ASTL",
    "pak elektron": "PAEL",
    "netsol technologies": "NETSOL",
    "avanceon": "AVN",
    "octopus digital": "OCTOPUS",
    "trg pakistan": "TRG",
    "kot addu power": "KAPCO",
    "pakistan tobacco": "PAKT",
    "fauji bin qasim": "FFC",
    "fatima fertilizer": "FATIMA",
    "lucky core industries": "LCI",
    "fast cables": "FCL",
    "bf biosciences": "BFBIO",
    "secure logistics": "SLGL",
    "barkat frisian": "BFAGRO",
    "zarea limited": "ZAL",
    "dolmen city reit": "DCR",
    "tpl reit": "TPLRF1",
    "kse-100": "KSE100",
    "kse 100": "KSE100",
    "pakistan stock exchange": "KSE100",
}

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "COMMERCIAL BANKS": ["bank", "banking", "islamic bank", "sbp", "policy rate", "monetary policy", "adr ", "deposit", "kibor"],
    "CEMENT": ["cement", "clinker", "dispatches", "apcma", "construction"],
    "OIL & GAS EXPLORATION COMPANIES": ["oil and gas", "oil & gas", "crude oil", "exploration", "gas discovery", "drilling", "hydrocarbon", "e&p", "brent", "wti"],
    "OIL & GAS MARKETING COMPANIES": ["petrol price", "diesel price", "fuel price", "ogra", "omc", "petroleum sales", "pso", "lng"],
    "REFINERY": ["refinery", "refineries", "refining policy", "upgrade agreement"],
    "FERTILIZER": ["fertilizer", "urea", "dap ", "nfml", "feedstock gas"],
    "POWER GENERATION & DISTRIBUTION": ["power sector", "ipp", "ipps", "nepra", "electricity", "tariff", "circular debt", "disco", "discos", "solar", "net metering", "grid"],
    "TECHNOLOGY & COMMUNICATION": ["it exports", "software exports", "tech sector", "telecom", "5g", "pta ", "freelancer", "fintech", "startup"],
    "AUTOMOBILE ASSEMBLER": ["auto sales", "pama", "car sales", "electric vehicle", "ev ", "tractor", "motorcycle", "vehicle"],
    "PHARMACEUTICALS": ["pharma", "pharmaceutical", "drap", "medicine", "drug price", "healthcare"],
    "TEXTILE COMPOSITE": ["textile", "aptma", "garment", "apparel", "cotton", "spinning", "knitwear"],
    "FOOD & PERSONAL CARE PRODUCTS": ["fmcg", "dairy", "food inflation", "beverage", "consumer goods"],
    "CHEMICAL": ["chemical", "pvc", "caustic soda", "petrochemical", "soda ash"],
    "INSURANCE": ["insurance", "takaful", "reinsurance", "secp"],
    "STEEL & ALLIED PRODUCTS": ["steel", "rebar", "crc", "hrc", "iron ore"],
    "REAL ESTATE INVESTMENT TRUST": ["reit", "real estate", "property market"],
}


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_pub_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    s = raw.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _classify_news_category(title: str, summary: str, default_category: str) -> str:
    blob = f"{title} {summary}".lower()
    if any(k in blob for k in ("ipo", "initial public offering", "book building", "new listing", "gem board", "prospectus")):
        return "IPO"
    if any(k in blob for k in ("sbp", "state bank", "imf", "policy rate", "cpi", "inflation", "gdp", "current account", "remittance", "t-bill", "pib", "rupee", "fx reserve")):
        return "Macro"
    if any(k in blob for k in ("gold", "silver", "crude oil", "brent", "wti", "commodity", "wheat", "cotton")):
        return "Commodities"
    if any(k in blob for k in ("profit", "earnings", "dividend", "quarter", "annual report", "board meeting", "acquisition", "merger", "subsidiary")):
        return "Corporate"
    if any(k in blob for k in ("psx", "kse-100", "kse 100", "stocks", "equities", "shares", "rally", "bullish", "bearish")):
        return "Markets"
    return default_category


def parse_rss_xml(xml_text: str, source_key: str, source_name: str, default_category: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom XML text into normalized article dicts."""
    if not xml_text or len(xml_text.strip()) < 60:
        return []
    # Strip invalid XML control characters cleanly
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return []

    # RSS 2.0 <item>
    nodes = root.findall(".//item")
    is_atom = False
    if not nodes:
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        is_atom = True

    for node in nodes[:40]:
        if is_atom:
            title_el = node.find("{http://www.w3.org/2005/Atom}title")
            link_el = node.find("{http://www.w3.org/2005/Atom}link")
            desc_el = node.find("{http://www.w3.org/2005/Atom}summary")
            pub_el = node.find("{http://www.w3.org/2005/Atom}updated") or node.find("{http://www.w3.org/2005/Atom}published")
            link = (link_el.attrib.get("href") if link_el is not None else "") or ""
        else:
            title_el = node.find("title")
            link_el = node.find("link")
            desc_el = node.find("description")
            pub_el = node.find("pubDate")
            link = (link_el.text or "").strip() if link_el is not None else ""

        title = _strip_html(title_el.text if title_el is not None else "")
        # Clean Google News trailing " - Profit by Pakistan Today" suffix
        title = re.sub(r"\s+-\s+Profit by Pakistan Today\s*$", "", title, flags=re.I).strip()
        summary = _strip_html(desc_el.text if desc_el is not None else "")
        if summary.lower().startswith(title.lower()[:40]):
            summary = summary[len(title):].strip(" -|:")
        if not title or not link or not link.startswith("http"):
            continue
        pub_dt = _parse_pub_date(pub_el.text if pub_el is not None else None)
        items.append(
            {
                "title": title[:390],
                "summary": summary[:750],
                "url": link[:690],
                "source_key": source_key,
                "source_name": source_name,
                "category": _classify_news_category(title, summary, default_category),
                "published_at": pub_dt,
            }
        )
    return items


def build_symbol_sector_matcher(
    assets: list[Asset],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """
    Build fast lookup maps:
      - sym_to_sector: {SYMBOL: official_sector}
      - alias_to_sym: {lowercase_name_or_alias: SYMBOL}
      - upper_sym_set: {distinctive ticker symbols safe for exact uppercase matching}
    """
    sym_to_sector: dict[str, str] = {}
    alias_to_sym: dict[str, str] = dict(COMPANY_ALIASES)
    upper_sym_set: dict[str, str] = {}

    for a in assets:
        sym = a.symbol.upper()
        sec = (a.sector_or_category or "").strip()
        sym_to_sector[sym] = sec
        if a.asset_class in {"PSX_EQUITY", "PSX_ETF"}:
            if 3 <= len(sym) <= 10 and sym.isalpha() and sym not in STOPWORD_SYMBOLS:
                upper_sym_set[sym] = sec
            # Add cleaned multi-word company stem (e.g. "Lucky Cement Limited" -> "lucky cement")
            clean_name = re.sub(
                r"\b(limited|ltd\.?|pakistan|company|co\.?|corporation|corp\.?|industries|mills|holdings|group)\b",
                "",
                a.name.lower(),
            )
            clean_name = re.sub(r"[^a-z0-9&\s-]", " ", clean_name)
            clean_name = re.sub(r"\s+", " ", clean_name).strip()
            if len(clean_name) >= 8 and " " in clean_name:
                alias_to_sym.setdefault(clean_name, sym)
    return sym_to_sector, alias_to_sym, upper_sym_set


def tag_article_symbols_and_sectors(
    title: str,
    summary: str,
    sym_to_sector: dict[str, str],
    alias_to_sym: dict[str, str],
    upper_sym_set: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Identify matching PSX ticker symbols and official PSX sectors from article text."""
    raw_blob = f"{title} {summary}"
    low_blob = f" {raw_blob.lower()} "

    matched_syms: set[str] = set()
    matched_sectors: set[str] = set()

    # 1. Exact company alias / full name match
    for alias, sym in alias_to_sym.items():
        if alias in low_blob:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", low_blob):
                matched_syms.add(sym)
                sec = sym_to_sector.get(sym)
                if sec and sec not in {"General", "PSX Index"}:
                    matched_sectors.add(sec)

    # 2. Exact ALL-CAPS PSX ticker token match (e.g. "(MEBL)", "OGDC", "LUCK")
    for tok in re.findall(r"\b([A-Z]{3,10})\b", raw_blob):
        if tok in upper_sym_set:
            matched_syms.add(tok)
            sec = upper_sym_set[tok]
            if sec and sec not in {"General", "PSX Index"}:
                matched_sectors.add(sec)

    # 3. Official PSX sector keyword match
    available_sectors = {s.upper(): s for s in sym_to_sector.values() if s}
    for sec_key, kw_list in SECTOR_KEYWORDS.items():
        if any(kw in low_blob for kw in kw_list):
            canonical_sec = available_sectors.get(sec_key, sec_key)
            matched_sectors.add(canonical_sec)

    return sorted(matched_syms)[:10], sorted(matched_sectors)[:6]


class PakistanNewsScraper(BaseScraper):
    """Fetches live financial news RSS feeds from Pakistan's top financial publications and auto-tags stocks/sectors."""

    name = "pk_news"

    async def run(self, db: AsyncSession) -> int:
        assets = (
            await db.execute(
                select(Asset).where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF", "PSX_INDEX", "INTL_EQUITY"]),
                )
            )
        ).scalars().all()
        sym_to_sector, alias_to_sym, upper_sym_set = build_symbol_sector_matcher(assets)

        async def _fetch_feed(client: httpx.AsyncClient, feed: dict[str, str]) -> list[dict[str, Any]]:
            try:
                resp = await client.get(feed["url"])
                if resp.status_code != 200:
                    logger.debug("News feed %s returned HTTP %s", feed["url"], resp.status_code)
                    return []
                return parse_rss_xml(
                    resp.text,
                    source_key=feed["source_key"],
                    source_name=feed["source_name"],
                    default_category=feed["default_category"],
                )
            except Exception as exc:
                logger.debug("News feed fetch error (%s): %s", feed["url"], exc)
                return []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            results = await asyncio.gather(*[_fetch_feed(client, f) for f in NEWS_FEEDS])

        raw_articles = [item for batch in results for item in batch]
        if not raw_articles:
            return 0

        existing_hashes = {
            row[0]
            for row in (await db.execute(select(NewsArticle.url_hash))).all()
        }
        upserted = 0
        seen_batch: set[str] = set()

        for item in raw_articles:
            u_hash = hashlib.sha256(item["url"].encode("utf-8")).hexdigest()
            if u_hash in existing_hashes or u_hash in seen_batch:
                continue
            seen_batch.add(u_hash)
            syms, secs = tag_article_symbols_and_sectors(
                item["title"],
                item["summary"],
                sym_to_sector,
                alias_to_sym,
                upper_sym_set,
            )
            db.add(
                NewsArticle(
                    url_hash=u_hash,
                    title=item["title"],
                    summary=item["summary"],
                    url=item["url"],
                    source_key=item["source_key"],
                    source_name=item["source_name"],
                    category=item["category"],
                    matched_symbols_json=syms,
                    matched_sectors_json=secs,
                    published_at=item["published_at"],
                )
            )
            upserted += 1

        if upserted:
            await db.commit()
        return upserted
