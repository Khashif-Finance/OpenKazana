# OpenKhazana `v1.0.0`

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-emerald.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg)](./apps/api)
[![Next.js 15](https://img.shields.io/badge/Next.js-15-000000.svg)](./apps/web)
[![Self-Hosted & SaaS Ready](https://img.shields.io/badge/Deploy-Local%20%7C%20VPS%20Caddy%20HTTPS-059669.svg)](./DEPLOY.md)

An open-source (Apache-2.0) **Pakistan Institutional Financial Market Terminal & Multi-Asset Portfolio Platform** — uniting live PSX equities, KSE-100 index point contributors, 5-Brokerage Equity Research Reports, Forex & Remittance corridors, PMEX & Global Commodities, Fixed Income / GoP Ijarah Sukuk / National Savings (NSS), SECP Monthly Fund Manager Reports (FMR), PUCARS Annual & Quarterly PDF report parsing, PBS & SBP EasyData Macro Analytics, Pandas quantitative factor models, daily-dividend mutual fund tracking, 12-month portfolio dividend calendars, FBR tax compliance, and transparent Shariah screening.

---

## System Architecture

```mermaid
flowchart LR
    subgraph Sources["Live Pakistan & Global Data Feeds"]
        PSX["PSX DPS & PUCARS PDFs"]
        MUFAP["MUFAP, AMCs & SECP FMR PDFs"]
        MACRO["PBS SDMX CPI & SBP EasyData"]
        DESKS["5 Brokerages: AHL · SCS · IMS · KASB · Chase"]
        FXPMEX["FX Corridors, Global Futures & CDNS NSS"]
    end

    subgraph Backend["FastAPI + Pandas + ARQ Worker"]
        SCRAPERS["Async Scrapers & PDF Parsers"]
        CACHE["In-Memory SWR Cache + Redis 7"]
        ENGINE["FBR Tax, FIFO Ledger & Quant Lab"]
        API["REST API + Rate Limiter + RBAC"]
    end

    subgraph Storage["Persistence"]
        DB["SQLite (Local) / PostgreSQL 16 (VPS SaaS)"]
    end

    subgraph Web["Next.js 15 Institutional Terminal"]
        UI["24 Pages · TradingView Charts · Print Tear-Sheets"]
    end

    Sources --> SCRAPERS --> DB
    DB --> ENGINE --> API
    CACHE <--> API
    API <--> UI
```

---

## Key Capabilities

### 1. Institutional Pakistan Multi-Asset Terminal (5-Heading Mega-Menu)
- **Clean 5-Heading Navigation (`Markets` · `Stocks` · `Mutual Funds` · `FX & Commodities` · `Portfolio`) + Quick Settings Toolbar (`⚙️`)**: Dropdown subcategories with `<1ms` local `Ctrl+K` / `⌘K` global search across 870+ assets, live PSX market clock, and a toggleable scrolling market tape.
- **KSE-100 / KMI-30 Index Point Contributors & Interactive Market Map (`/`, `/markets/sectors`)**: Live constituent index point attribution (top positive drivers, drags & sector point impact) alongside a market-cap-weighted Treemap heatmap and Pandas Sector & Industry Explorer across all 37 PSX sectors.
- **5-Brokerage Equity Research Hub (`/stocks/research`) & Stock-Page Research Tab**: Aggregates live research notes, valuation matrices, and corporate briefings from **Arif Habib Limited (AHL)**, **Standard Capital Securities (SCS Trade)**, **Intermarket Securities (IMS)**, **KASB / KTrade**, and **Chase Securities** — auto-tagged to PSX tickers & sectors with Stance/Target Price extraction, 1-click PDF downloads, and per-user custom report bookmarking.
- **Forex & Remittance Hub (`/markets/forex`)**: Live PKR ↔ 14 FCY corridors (USD, EUR, GBP, AED, SAR, CAD, AUD, CNY, CHF, JPY, KWD, QAR, OMR, BHD) comparing SBP Interbank vs Open Market Buy/Sell spreads, interactive currency converter, 9 Global Major FX pairs + ICE `DXY`, and 5-year historical PKR charts.
- **PMEX & Global Commodities Hub (`/markets/commodities`)**: Live Gold, Silver, Platinum, Brent, WTI, Natural Gas, Copper, Cotton, Wheat, Corn & Soybeans converted live to PKR, Pakistan Gold Rate Board (24K/22K/21K per Tola, 10g & 1g), interactive **PMEX Contract Notional & Margin Calculator**, 90-day charts, and clickable **PSX Sector & Ticker Impact Map**.
- **Fixed Income, GoP Ijarah Sukuk & National Savings (NSS) Hub (`/markets/fixed-income`)**: Live SBP T-Bills & PIBs, CDNS National Savings Schemes (Behbood, Pensioners, Defence, Regular Income, Special Savings, Sarwa Islamic), PSX-listed GoP Ijarah Sukuk, SBP RDA Naya Pakistan Certificates, and an **Interactive After-Tax Monthly Income & Zakat Comparator** (Filer 15% vs Non-Filer 30% WHT + Behbood/PBA exemptions vs top daily-dividend mutual funds).
- **1-Click Printable Institutional Tear-Sheet PDFs (`/stocks/[symbol]`, `/funds/[symbol]`)**: Clean `@media print` 1-page research memo layout for exporting any stock or SECP FMR mutual fund analysis to PDF.
- **Dual-Mode Self-Hosting & Production SaaS (`AUTH_MODE=local` | `multiuser`)**: Zero-login local workspace out of the box, OR flip to `AUTH_MODE=multiuser` with Caddy automatic Let's Encrypt HTTPS, rate limiting, PostgreSQL Alembic migrations, daily `pg_dump` backups, and an **Admin SaaS Control Panel** (`/status`) with live public-signup toggle & user management. See [DEPLOY.md](./DEPLOY.md).
- **PSX IPOs, Latest Developments & Corporate Calendar (`/markets/ipos`, `/markets/calendar`)**: Tracks PSX Main & GEM Board IPOs with live Post-IPO Return % vs Strike Price, official PSX manuals, PUCARS material developments, and a unified Ex-Dividend / Bonus / Board Meeting / Results calendar filterable to your holdings & watchlist.
- **Pakistan Financial News Hub (`/markets/news`) & Live PBS/SBP Macro Lab (`/analytics`)**: Live RSS aggregation from Business Recorder, Profit (Pakistan Today), Dawn Business, Express Tribune & Investing.com — auto-tagged to 501 PSX tickers and 37 official sectors — alongside live **PBS Monthly CPI Inflation**, **SBP FX Reserves**, **Workers' Remittances**, and **SBP Yield Curves**.
- **Side-by-Side Stock Comparison Matrix (`/stocks/compare`) & Filings Downloads Hub (`/stocks/downloads`)**: Compare 2–5 stocks head-to-head on normalized performance & fundamentals, and 1-click download official PSX PUCARS announcement PDFs, Annual/Quarterly reports, AMC SECP FMR PDFs & 3-statement CSVs.
- **Dual-Mode Open-Source Self-Hosting (`AUTH_MODE=local` | `multiuser`)**: Zero-login local workspace out of the box with 1-click JSON Workspace Backup/Restore & Shariah-first preferences in `/settings`, or switch `AUTH_MODE=multiuser` in `.env` for hardened multi-tenant cloud/VPS hosting.
- **Tabbed Institutional Stock Workbench (`/stocks/[symbol]`)**:
  - **Overview & Chart**: TradingView `lightweight-charts` candlestick/volume/indicator overlays (`PRICE`, `GROSS_TR`, `NET_FILER`, `NET_NON_FILER` + Shariah purification), **20-Indicator Technical Summary Speedometer Gauge**, **Pandas Cross-Sectional Factor Percentile Ranks**, and **Held by Pakistani Mutual Funds (SECP FMR Radar)**.
  - **Financials & Reports**: Multi-period Annual & Quarterly 3-Statement analyzer powered by **Pandas** (YoY growth, common-size margins, 3Y/5Y CAGRs, cash conversion, Sloan accruals, DSO/DIO) with auto-parsed PUCARS PDFs, drag-and-drop PDF upload, Independent Auditor's Opinion, Directors' Highlights, and 1-click CSV/PDF downloads.
  - **Technicals & Seasonality**: Full 12 Moving Averages + 8 Oscillators Buy/Sell table, classic pivots, and **Pandas Monthly Seasonality Return Heatmap (Year × Month)**.
  - **Valuation & Quality Lab**: Interactive 2-stage FCF DCF Growth/WACC sliders + 5×5 Sensitivity Matrix, 3-Factor DuPont ROE, collapsible 9-point Piotroski accounting checklist, sector peer multiples, and automated Bull/Bear & Red-Flag digest.
  - **Dividends & Filings**, **Brokerage Research**, and **News & Sector Coverage**: Multi-year cash dividend streak, downloadable PUCARS PDFs, matched brokerage equity reports, and company-specific + sector news.

### 2. SECP Mutual Fund Fact Sheets (`/funds`, `/funds/[symbol]`) & Live Daily Dividend Engine
- **AMC Leaderboard, After-Tax SIP Comparator & FMR Overlap Lab**: Rank all 20+ Pakistani AMCs by 1Y return, lowest TER, AUM & FMR coverage; compare 2–4 mutual funds head-to-head net of FEL, TER & FBR 15%/30% WHT against NSS; and detect hidden Top-10 stock overlaps across multiple funds using parsed SECP FMR holdings.
- **Automated SECP Monthly FMR PDF Parser**: Discovers and parses official AMC monthly FMR booklets (Al Meezan, HBL Asset, Atlas, JS Investments, ABL Funds) PLUS universal drag-and-drop FMR PDF upload for any AMC — extracting Asset Allocation, Top-10 Equity Holdings (cross-linked to live PSX prices & implied P/E / dividend yield), Sector Allocation, Credit Quality, WAM, Stability Ratings (`AAA(f)` / `AA+(f)`), TER + Govt Levies, and Manager Commentary.
- **Full Live Daily Dividend Funds Engine**:
  - Automatically accrues daily dividends (Gross & Net after 15% Active Filer / 30% Non-Filer FBR WHT, zero double-counting with posted ledger entries) inside Portfolio valuations, PnL, TWRR, and XIRR for constant-NAV funds (`MRAF`, `MDIP1`, `MMMP`, `NIDDF`, `MCBADDUB`, `MCF`, `MICF`, `SGOV`).
  - Interactive **Daily / Monthly / Annual Dividend Income Simulator** + **1-Year Compounded Wealth Curve** comparing Gross TR, Net Filer, Net Non-Filer, and Flat Par NAV.

### 3. Transaction-Ledger Portfolio, 12-Month Dividend Calendar, Quant Lab & X-Ray (`/portfolio`)
- **12-Month Passive Income & Dividend Snowball Calendar**: Projects Jan–Dec monthly cashflow across your PSX stocks (inferred from historical CorporateAction ex-dates) and Daily Dividend / Income Mutual Funds net of FBR WHT & Shariah purification, with Forward Yield & Yield-on-Cost KPIs.
- **Multi-Asset Ledger & Pakistan FBR Tax Engine**: FIFO lot matching with same-day intraday netting, Finance Act 2024 4-regime CGT + Section 37A 3-year loss carryforward, Section 236Z bonus tax, FBR Section 116(2) Tax Year reconciliation export, Shariah dividend purification, dual USD/PKR FX return decomposition, and broker CSV/XLSX import (KTrade, Finqalab, AKD, CDC, IBKR).
- **Look-Through Portfolio X-Ray**: Explodes Mutual Fund & VPS holdings via parsed SECP FMR disclosures to compute true Asset Mix, Combined Sector Concentration, and Direct-vs-Fund Single-Stock Overlaps.
- **Quantitative Risk & Optimization Lab**: SciPy/PyPortfolioOpt Markowitz Max-Sharpe, Min-Vol & HRP weights with actionable trade tickets, Efficient Frontier, 3-Year Monte Carlo wealth fan, and SBP macro stress tests.

---

## Tech Stack

- **Frontend (`apps/web`)**: Next.js 15 (App Router, TypeScript), Tailwind CSS v4, TradingView `lightweight-charts`, Recharts, `technicalindicators`, Lucide icons.
- **Backend (`apps/api`)**: FastAPI, Python 3.12+, SQLAlchemy 2.0 Async, Pydantic v2, Pandas, NumPy, SciPy, `pdfplumber`, `curl_cffi`, `yfinance`, ARQ + Redis (optional for local mode).
- **Database**: SQLite (zero-config local dev) or PostgreSQL 16 + Redis 7 (Docker / production).

---

## Quick Start

### Option A — 1-Click Local Launch (Zero Docker, Zero Login Required)
- **Windows**: Double-click or run `start-local.bat` from the repository root.
- **macOS / Linux**: Run `chmod +x start-local.sh && ./start-local.sh`.

Or start both services manually:

```powershell
# Terminal 1 — Backend API (SQLite local DB, zero-login AUTH_MODE=local)
cd apps/api
$env:DATABASE_URL="sqlite+aiosqlite:///./openkhazana_local.db"
$env:AUTH_MODE="local"
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — Frontend Web App
cd apps/web
npm install
npm run build
npm run start -- -H 127.0.0.1 -p 3000
```

Open **http://localhost:3000** in your browser.

### Option B — Production VPS / Cloud SaaS (PostgreSQL 16 + Redis 7 + Caddy Auto-HTTPS)
See the step-by-step VPS deployment & backup runbook in **[DEPLOY.md](./DEPLOY.md)**:
```bash
cp .env.example .env
# Set APP_DOMAIN, POSTGRES_PASSWORD, SECRET_KEY (>=32 chars), AUTH_MODE=multiuser
docker compose -f docker-compose.prod.yml up -d --build
```

### Option C — Local Docker Compose (Dev)
```bash
cp .env.example .env
docker compose up --build
```

---

## Documentation & Community

- **[DEPLOY.md](./DEPLOY.md)** — Production Ubuntu VPS + Docker + Caddy Automatic HTTPS + PostgreSQL Daily Backups
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** — Local dev workflow, Alembic migrations & PR checklist
- **[SECURITY.md](./SECURITY.md)** — Security controls, SSRF allowlist & vulnerability reporting
- **Interactive OpenAPI / Swagger Docs**: `http://localhost:8000/docs`

---

## Running Tests

```bash
cd apps/api
pytest tests -v
```

```bash
cd apps/web
npm run build
```

---

## Disclaimer

OpenKhazana is open-source software provided for informational, analytical, and personal portfolio tracking purposes only. Always verify FBR tax figures (CGT, Section 236Z, Section 116 wealth schedules, WHT, Zakat), Shariah purification metrics, and company financial statements against official PSX/PUCARS/SECP/NCCPL/FBR notices before filing taxes or making investment decisions.

## Credits & License

Built by **Khashif Sarfraz** with **Gemini**.

Licensed under [Apache-2.0](./LICENSE).
