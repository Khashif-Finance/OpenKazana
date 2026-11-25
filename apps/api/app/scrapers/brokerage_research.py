"""
Pakistan Brokerage Equity Research Aggregator & Auto-Tagger.

Covers Pakistan's leading institutional research desks:
  1. Arif Habib Limited (AHL)        — https://www.arifhabibltd.com/services/research
  2. Standard Capital (SCS Trade)    — https://www.scstrade.com/research/RE_Reports.aspx
  3. Intermarket Securities (IMS)    — https://imsecurities.com.pk/services/research-reports
  4. KASB / KTrade Securities        — https://www.kasb.com/reports/
  5. Chase Securities                — https://chasesecurities.com/category/monthly/

Live-fetches open research listings, WordPress JSON feeds, and direct PDF catalogs,
extracts analyst stance (`BUY` / `HOLD` / `SELL` / `OVERWEIGHT`) & Target Price when
mentioned, and auto-tags every report to exact PSX ticker symbols & official PSX sectors.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import api_cache
from app.models.market import Asset, ResearchReport
from app.scrapers.base import BaseScraper
from app.scrapers.pk_news import build_symbol_sector_matcher, tag_article_symbols_and_sectors

logger = logging.getLogger("openkhazana.scrapers.brokerage_research")

BROKERAGE_PORTALS: list[dict[str, Any]] = [
    {
        "broker_key": "ahl",
        "broker_name": "Arif Habib Limited (AHL)",
        "short_name": "AHL",
        "url": "https://www.arifhabibltd.com/services/research",
        "specialty": "PSX Strategy, Daily Calls, Sector Deep Dives & Stock Recommendation Summary",
        "feed_mode": "LIVE_HTML_PDF",
    },
    {
        "broker_key": "scs",
        "broker_name": "Standard Capital Securities (SCS Trade)",
        "short_name": "SCS Trade",
        "url": "https://www.scstrade.com/research/RE_Reports.aspx",
        "specialty": "Company Reports, Valuation Matrix, Dividend Schedules, Technical & MTS Reports",
        "feed_mode": "LIVE_HTML_PDF",
    },
    {
        "broker_key": "ims",
        "broker_name": "Intermarket Securities (IMS)",
        "short_name": "IMS",
        "url": "https://imsecurities.com.pk/services/research-reports",
        "specialty": "Institutional Macro, Equity Coverage & Sector Initiation Reports",
        "feed_mode": "PORTAL_DIRECTORY",
    },
    {
        "broker_key": "kasb",
        "broker_name": "KASB / KTrade Securities",
        "short_name": "KASB / KTrade",
        "url": "https://www.kasb.com/reports/",
        "specialty": "Pakistan Macro, Budget Impact, PMEX Commodities, IPO Notes & Equity Research",
        "feed_mode": "LIVE_WP_AND_PDF",
    },
    {
        "broker_key": "chase",
        "broker_name": "Chase Securities",
        "short_name": "Chase Securities",
        "url": "https://chasesecurities.com/category/monthly/",
        "specialty": "Corporate Briefing Session (CBS) Notes, Monthly Strategy & PSX Company Updates",
        "feed_mode": "LIVE_WP_JSON",
    },
]

# Extra Pakistani company aliases common in Corporate Briefing Notes & Brokerage Reports
EXTRA_RESEARCH_ALIASES: dict[str, str] = {
    "nishat power": "NPL",
    "nishat chunian power": "NCPL",
    "baluchistan glass": "BGL",
    "fazal cloth": "FZCM",
    "aisha steel": "ASL",
    "attock petroleum": "APL",
    "attock cement": "ACPL",
    "fauji fertilizer bin qasim": "FFC",
    "pakistan cables": "PCAL",
    "honda atlas": "HCAR",
    "al-ghazi tractors": "AGTL",
    "ghandhara automobiles": "GAL",
    "ghandhara industries": "GHNI",
    "pak electron": "PAEL",
    "service industries": "SRVI",
    "service global footwear": "SGF",
    "tcf": "TCF",
    "image pakistan": "IMAGE",
    "unity foods": "UNITY",
    "tplo": "TPLP",
    "tpl properties": "TPLP",
    "hum network": "HUMNL",
    "symmetry group": "SYM",
    "cnergyico": "CNERGY",
    "hascol": "HASCOL",
    "shell pakistan": "WAFI",
    "wafi energy": "WAFI",
    "lalpir power": "LPL",
    "pakgen power": "PKGP",
    "saif power": "SPWL",
    "hub power company": "HUBC",
    "kohinoor textile": "KTML",
    "crescent steel": "CSAP",
    "international industries": "INIL",
    "agp limited": "AGP",
    "ferozsons": "FEROZ",
    "citi pharma": "CPHL",
    "highnoon": "HINOON",
    "matco foods": "MFL",
    "al shaheer": "ASC",
    "thall limited": "THALL",
    "thal limited": "THALL",
    "shahtaj sugar": "SHJS",
    "shahmurad sugar": "SHSML",
    "century paper": "CEPB",
    "cherat packaging": "CPPL",
    "sitara chemical": "SITC",
    "lotchem": "LOTCHEM",
    "lotte chemical": "LOTCHEM",
    "ghani chemical": "GCIL",
    "descon oxychem": "DOL",
    "bata pakistan": "BATA",
    "panther tyres": "PTL",
    "general tyre": "GTYR",
    "gull ahmed": "GATM",
    "adamjee insurance": "AICL",
    "jubilee life": "JLICL",
    "efu general": "EFUG",
    "pak re": "PAKRI",
    "pakistan stock exchange limited": "PSX",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
}


def _clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    s = html_lib.unescape(raw)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_stance_and_tp(title: str, summary: str = "") -> tuple[str | None, Decimal | None]:
    """Extract optional brokerage rating (`BUY`/`HOLD`/`SELL`/`OVERWEIGHT`/`UNDERWEIGHT`) & Target Price."""
    blob = f"{title} {summary}"
    stance: str | None = None
    for pat, label in (
        (r"\b(STRONG\s+BUY|BUY|ACCUMULATE)\b", "BUY"),
        (r"\b(OVERWEIGHT|OUTPERFORM)\b", "OVERWEIGHT"),
        (r"\b(HOLD|NEUTRAL|MARKET\s+PERFORM)\b", "HOLD"),
        (r"\b(UNDERWEIGHT|UNDERPERFORM)\b", "UNDERWEIGHT"),
        (r"\b(SELL|REDUCE)\b", "SELL"),
    ):
        if re.search(pat, blob, re.IGNORECASE):
            stance = label
            break

    tp: Decimal | None = None
    m_tp = re.search(
        r"(?:target\s*price|TP|fair\s*value)\s*(?:of|:|at|-)?\s*(?:PKR|Rs\.?)?\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)",
        blob,
        re.IGNORECASE,
    )
    if m_tp:
        try:
            v = Decimal(m_tp.group(1).replace(",", ""))
            if Decimal("1") <= v <= Decimal("50000"):
                tp = v.quantize(Decimal("0.01"))
        except Exception:
            tp = None
    return stance, tp


def _classify_report_type(title: str, summary: str = "", default_type: str = "COMPANY") -> str:
    low = f"{title} {summary}".lower()
    if any(k in low for k in ("briefing", "cbs", "analyst briefing")):
        return "CORPORATE_BRIEFING"
    if any(k in low for k in ("strategy", "outlook", "budget", "monthly", "annual review", "kse-100")):
        return "STRATEGY"
    if any(k in low for k in ("inflation", "cpi", "sbp", "policy rate", "monetary", "imf", "current account", "remittance", "tariff", "economy", "macro")):
        return "MACRO"
    if any(k in low for k in ("technical", "chart", "momentum", "mts")):
        return "TECHNICAL"
    if any(k in low for k in ("sector", "cement", "fertilizer", "banking", "textile", "oil", "power", "refinery", "auto", "pmex", "commodit")):
        return "SECTOR"
    if any(k in low for k in ("valuation matrix", "recommendation summary", "dividend schedule", "payout")):
        return "VALUATION"
    return default_type


def _parse_iso_dt(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _date_from_url_or_text(text: str) -> datetime:
    # YYYY-MM-DD
    m1 = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m1:
        try:
            return datetime(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)), 12, 0, tzinfo=timezone.utc)
        except ValueError:
            pass
    # /uploads/YYYY/MM/
    m2 = re.search(r"/(20\d{2})/(\d{2})/", text)
    if m2:
        try:
            return datetime(int(m2.group(1)), int(m2.group(2)), 15, 12, 0, tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def parse_chase_wp_posts(posts: Any) -> list[dict[str, Any]]:
    """Parse Chase Securities WordPress JSON posts (Corporate Briefing Notes, Monthly & Market Research)."""
    if not isinstance(posts, list):
        return []
    out: list[dict[str, Any]] = []
    for p in posts[:45]:
        if not isinstance(p, dict):
            continue
        title = _clean_text((p.get("title") or {}).get("rendered"))
        link = str(p.get("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        content_html = str((p.get("content") or {}).get("rendered") or "")
        excerpt_html = str((p.get("excerpt") or {}).get("rendered") or "")
        summary = _clean_text(excerpt_html) or _clean_text(content_html)[:420]
        pdfs = re.findall(r'https?://[^\s"\'<>]+\.pdf', content_html, re.IGNORECASE)
        pub = _parse_iso_dt(p.get("date_gmt") or p.get("date"))
        stance, tp = extract_stance_and_tp(title, summary)
        out.append(
            {
                "broker_key": "chase",
                "broker_name": "Chase Securities",
                "title": title[:390],
                "summary": summary[:700],
                "url": link[:690],
                "pdf_url": pdfs[0][:690] if pdfs else None,
                "report_type": _classify_report_type(title, summary, default_type="CORPORATE_BRIEFING"),
                "stance": stance,
                "target_price": tp,
                "published_at": pub,
            }
        )
    return out


def parse_kasb_reports_html(html_text: str) -> list[dict[str, Any]]:
    """Parse KASB / KTrade `https://www.kasb.com/reports/` PDF download listings."""
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" not in href.lower():
            continue
        full_url = urljoin("https://www.kasb.com/", href)
        if full_url in seen:
            continue
        seen.add(full_url)
        # Derive clean title from card heading or PDF filename slug
        slug_name = full_url.split("/")[-1]
        slug_clean = re.sub(r"\.pdf$", "", slug_name, flags=re.I)
        slug_clean = re.sub(r"[-_]+", " ", slug_clean).strip()
        slug_clean = re.sub(r"\s+(vF|compressed|\d{8}|\d+)$", "", slug_clean, flags=re.I).strip()

        card_heading = ""
        parent = a.parent
        for _ in range(3):
            if parent is None:
                break
            h = parent.find(["h1", "h2", "h3", "h4", "h5"])
            if h:
                cand = _clean_text(h.get_text(" ", strip=True))
                if cand and cand.lower() not in {"reports", "download", "research"}:
                    card_heading = cand
                    break
            parent = parent.parent

        title = card_heading if len(card_heading) >= 8 else slug_clean
        if len(title) < 6:
            continue
        pub = _date_from_url_or_text(full_url)
        stance, tp = extract_stance_and_tp(title)
        out.append(
            {
                "broker_key": "kasb",
                "broker_name": "KASB / KTrade Securities",
                "title": title[:390],
                "summary": "Official KASB / KTrade Securities Pakistan research PDF publication.",
                "url": full_url[:690],
                "pdf_url": full_url[:690],
                "report_type": _classify_report_type(title, "", default_type="MACRO"),
                "stance": stance,
                "target_price": tp,
                "published_at": pub,
            }
        )
    return out[:30]


def parse_kasb_wp_posts(posts: Any) -> list[dict[str, Any]]:
    """Parse KASB / KTrade WordPress research articles."""
    if not isinstance(posts, list):
        return []
    out: list[dict[str, Any]] = []
    for p in posts[:25]:
        if not isinstance(p, dict):
            continue
        title = _clean_text((p.get("title") or {}).get("rendered"))
        link = str(p.get("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        excerpt = _clean_text((p.get("excerpt") or {}).get("rendered"))
        content_html = str((p.get("content") or {}).get("rendered") or "")
        pdfs = re.findall(r'https?://[^\s"\'<>]+\.pdf', content_html, re.IGNORECASE)
        pub = _parse_iso_dt(p.get("date_gmt") or p.get("date"))
        stance, tp = extract_stance_and_tp(title, excerpt)
        out.append(
            {
                "broker_key": "kasb",
                "broker_name": "KASB / KTrade Securities",
                "title": title[:390],
                "summary": (excerpt or _clean_text(content_html))[:700],
                "url": link[:690],
                "pdf_url": pdfs[0][:690] if pdfs else None,
                "report_type": _classify_report_type(title, excerpt, default_type="STRATEGY"),
                "stance": stance,
                "target_price": tp,
                "published_at": pub,
            }
        )
    return out


def parse_ahl_research_html(html_text: str) -> list[dict[str, Any]]:
    """Parse Arif Habib Limited (AHL) `https://www.arifhabibltd.com/services/research` PDF reports."""
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    skip_slugs = ("faq", "awareness", "brochure", "account")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" not in href.lower():
            continue
        if any(s in href.lower() for s in skip_slugs):
            continue
        full_url = urljoin("https://www.arifhabibltd.com/", href)
        if full_url in seen:
            continue
        seen.add(full_url)

        # Look for nearby title text
        raw_ctx = ""
        if a.parent:
            raw_ctx = _clean_text(a.parent.get_text(" ", strip=True))
            raw_ctx = re.sub(r"\bClick\s*Report\b|\bClick\b", "", raw_ctx, flags=re.I).strip()
        slug = re.sub(r"\.pdf$", "", full_url.split("/")[-1], flags=re.I)
        slug_title = re.sub(r"[-_]+", " ", slug).strip()
        # Split CamelCase in slugs like LuckyCementLimited -> Lucky Cement Limited
        slug_title = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug_title)
        if re.match(r"^\d{4}\s+\d{2}\s+\d{2}\s+pdf\s+\d+$", slug_title, re.I):
            slug_title = ""

        title = raw_ctx if (8 <= len(raw_ctx) <= 180) else (slug_title or "Arif Habib Research Report")
        if "RecommendationSummary" in full_url:
            title = "AHL Market Review & PSX Stock Recommendation Summary"
        pub = _date_from_url_or_text(full_url)
        stance, tp = extract_stance_and_tp(title)
        out.append(
            {
                "broker_key": "ahl",
                "broker_name": "Arif Habib Limited (AHL)",
                "title": title[:390],
                "summary": "Arif Habib Limited (AHL) Institutional Research PDF report.",
                "url": full_url[:690],
                "pdf_url": full_url[:690],
                "report_type": _classify_report_type(title, full_url, default_type="COMPANY"),
                "stance": stance,
                "target_price": tp,
                "published_at": pub,
            }
        )
    return out[:25]


def parse_scs_research_html(html_text: str) -> list[dict[str, Any]]:
    """Parse Standard Capital Securities (SCS Trade) research PDFs & live analytical sheets."""
    if not html_text:
        return []
    soup = BeautifulSoup(html_text, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/research/" not in href.lower() or ".pdf" not in href.lower():
            continue
        full_url = urljoin("https://www.scstrade.com/", href).replace("http://", "https://").replace(" ", "%20")
        if full_url in seen:
            continue
        seen.add(full_url)
        label = _clean_text(a.get_text(" ", strip=True))
        if not label or len(label) < 3:
            label = re.sub(r"\.pdf$", "", full_url.split("/")[-1], flags=re.I).replace("%20", " ")
        title = f"SCS Trade Research — {label}"
        pub = _date_from_url_or_text(full_url)
        stance, tp = extract_stance_and_tp(title)
        out.append(
            {
                "broker_key": "scs",
                "broker_name": "Standard Capital Securities (SCS Trade)",
                "title": title[:390],
                "summary": "Standard Capital Securities (SCS Trade) PSX Equity Research & Valuation Sheet.",
                "url": full_url[:690],
                "pdf_url": full_url[:690],
                "report_type": _classify_report_type(title, "", default_type="VALUATION"),
                "stance": stance,
                "target_price": tp,
                "published_at": pub,
            }
        )
    return out[:25]


class BrokerageResearchScraper(BaseScraper):
    """Fetches open research reports from Pakistan's top 5 brokerages and auto-tags PSX tickers & sectors."""

    name = "brokerage_research"

    async def run(self, db: AsyncSession) -> int:
        assets = (
            await db.execute(
                select(Asset).where(
                    Asset.owner_user_id.is_(None),
                    Asset.asset_class.in_(["PSX_EQUITY", "PSX_ETF", "PSX_INDEX"]),
                )
            )
        ).scalars().all()
        sym_to_sector, alias_to_sym, upper_sym_set = build_symbol_sector_matcher(assets)
        alias_to_sym.update(EXTRA_RESEARCH_ALIASES)

        collected: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, verify=False, headers=HEADERS) as client:
            async def _fetch_chase() -> list[dict[str, Any]]:
                try:
                    r = await client.get("https://chasesecurities.com/wp-json/wp/v2/posts?per_page=35")
                    if r.status_code == 200:
                        return parse_chase_wp_posts(r.json())
                except Exception as exc:
                    logger.debug("Chase research fetch warning: %s", exc)
                return []

            async def _fetch_kasb() -> list[dict[str, Any]]:
                items: list[dict[str, Any]] = []
                try:
                    r1 = await client.get("https://www.kasb.com/reports/")
                    if r1.status_code == 200:
                        items.extend(parse_kasb_reports_html(r1.text))
                except Exception as exc:
                    logger.debug("KASB /reports/ fetch warning: %s", exc)
                try:
                    r2 = await client.get("https://www.kasb.com/wp-json/wp/v2/posts?per_page=20")
                    if r2.status_code == 200:
                        items.extend(parse_kasb_wp_posts(r2.json()))
                except Exception as exc:
                    logger.debug("KASB WP fetch warning: %s", exc)
                return items

            async def _fetch_ahl() -> list[dict[str, Any]]:
                try:
                    r = await client.get("https://www.arifhabibltd.com/services/research")
                    if r.status_code == 200:
                        return parse_ahl_research_html(r.text)
                except Exception as exc:
                    logger.debug("AHL research fetch warning: %s", exc)
                return []

            async def _fetch_scs() -> list[dict[str, Any]]:
                try:
                    r = await client.get("https://www.scstrade.com/research/RE_Reports.aspx")
                    if r.status_code == 200:
                        return parse_scs_research_html(r.text)
                except Exception as exc:
                    logger.debug("SCS research fetch warning: %s", exc)
                return []

            batches = await asyncio.gather(_fetch_chase(), _fetch_kasb(), _fetch_ahl(), _fetch_scs())
            for b in batches:
                collected.extend(b)

        if not collected:
            return 0

        existing_hashes = {row[0] for row in (await db.execute(select(ResearchReport.url_hash))).all()}
        upserted = 0
        seen_batch: set[str] = set()

        for item in collected:
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
                ResearchReport(
                    url_hash=u_hash,
                    broker_key=item["broker_key"],
                    broker_name=item["broker_name"],
                    title=item["title"],
                    summary=item["summary"],
                    url=item["url"],
                    pdf_url=item.get("pdf_url"),
                    report_type=item["report_type"],
                    stance=item.get("stance"),
                    target_price=item.get("target_price"),
                    matched_symbols_json=syms,
                    matched_sectors_json=secs,
                    published_at=item["published_at"],
                    is_user_bookmark=False,
                )
            )
            upserted += 1

        if upserted:
            await db.commit()
            api_cache.invalidate_prefix("research:")
        return upserted
