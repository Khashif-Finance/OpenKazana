# OpenKhazana

An open-source (MIT) **Pakistan Institutional Financial Market Terminal & Multi-Asset Portfolio Platform** — combining live PSX equities, SECP Monthly Fund Manager Reports (FMR), PUCARS Annual & Quarterly PDF report parsing, Pandas quantitative analytics, daily-dividend mutual fund tracking, global stocks/ETFs, gold, FX, crypto, FBR tax compliance, and transparent Shariah screening.

---

## Key Capabilities

### 1. Investing.com + Sarmaya.pk Hybrid Market Terminal (5-Heading Mega-Menu)
- **Clean 5-Heading Navigation (`Markets` · `Stocks` · `Mutual Funds` · `Portfolio` · `Settings`)**: Dropdown subcategories with instant `Ctrl+K` / `⌘K` global search and a toggleable scrolling market tape.
- **Sarmaya.pk-Style PSX Market Map & Sector Explorer (`/markets/sectors`)**: Market-cap-weighted interactive Treemap heatmap + Pandas Sector & Industry Explorer across all 37 PSX sectors with expandable stock accordions, median P/E, ROE, Dividend Yield & Shariah ratios.
- **PSX IPOs, Latest Developments & Corporate Calendar (`/markets/ipos`, `/markets/calendar`)**: Tracks PSX Main & GEM Board IPOs with live Post-IPO Return % vs Strike Price, official PSX manuals, PUCARS material developments, and a unified Ex-Dividend / Bonus / Board Meeting / Results calendar filterable to your holdings & watchlist.
- **Pakistan Financial News Hub (`/markets/news`)**: Live RSS aggregation from Business Recorder, Profit (Pakistan Today), Dawn Business, Express Tribune & Investing.com — auto-tagged to 501 PSX tickers and 37 official sectors, surfaced both globally and inside every `/stocks/[symbol]` page.
- **Side-by-Side Stock Comparison Matrix (`/stocks/compare`) & Filings Downloads Hub (`/stocks/downloads`)**: Compare 2–5 stocks head-to-head on normalized performance & fundamentals, and 1-click download official PSX PUCARS announcement PDFs, Annual/Quarterly reports, AMC SECP FMR PDFs & 3-statement CSVs.
- **Dual-Mode Open-Source Self-Hosting (`AUTH_MODE=local` | `multiuser`)**: Zero-login local workspace out of the box with 1-click JSON Workspace Backup/Restore & Shariah-first preferences in `/settings`, or switch `AUTH_MODE=multiuser` in `.env` for hardened multi-tenant cloud/VPS hosting.
- **Tabbed Institutional Stock Workbench (`/stocks/[symbol]`)**:
  - **Overview & Chart**: TradingView `lightweight-charts` candlestick/volume/indicator overlays (`PRICE`, `GROSS_TR`, `NET_FILER`, `NET_NON_FILER` + Shariah purification), **Investing.com-style 20-Indicator Technical Summary Speedometer Gauge**, **Pandas Cross-Sectional Factor Percentile Ranks**, and **Held by Pakistani Mutual Funds (SECP FMR Radar)**.
  - **Financials & Reports**: Multi-period Annual & Quarterly 3-Statement analyzer powered by **Pandas** (YoY growth, common-size margins, 3Y/5Y CAGRs, cash conversion, Sloan accruals, DSO/DIO) with auto-parsed PUCARS PDFs, drag-and-drop PDF upload, Independent Auditor's Opinion, Directors' Highlights, and 1-click CSV/PDF downloads.
  - **Technicals & Seasonality**: Full 12 Moving Averages + 8 Oscillators Buy/Sell table, classic pivots, and **Pandas Monthly Seasonality Return Heatmap (Year × Month)**.
  - **Valuation & Quality Lab**: Interactive 2-stage FCF DCF Growth/WACC sliders + 5×5 Sensitivity Matrix, 3-Factor DuPont ROE, collapsible 9-point Piotroski accounting checklist, sector peer multiples, and automated Bull/Bear & Red-Flag digest.
  - **Dividends & Filings** + **News & Sector Coverage**: Multi-year cash dividend streak, downloadable PUCARS PDFs, and company-specific + sector news.

### 2. SECP Mutual Fund Fact Sheets (`/funds/[symbol]`) & Live Daily Dividend Engine
- **Automated SECP Monthly FMR PDF Parser**: Discovers and parses official AMC monthly FMR booklets (Al Meezan, HBL Asset, Atlas, JS Investments, ABL Funds) PLUS universal drag-and-drop FMR PDF upload for any AMC — extracting Asset Allocation, Top-10 Equity Holdings (cross-linked to live PSX prices & implied P/E / dividend yield), Sector Allocation, Credit Quality, WAM, Stability Ratings (`AAA(f)` / `AA+(f)`), TER + Govt Levies, and Manager Commentary.
- **Full Live Daily Dividend Funds Engine**:
  - Automatically accrues daily dividends (Gross & Net after 15% Active Filer / 30% Non-Filer FBR WHT, zero double-counting with posted ledger entries) inside Portfolio valuations, PnL, TWRR, and XIRR for constant-NAV funds (`MRAF`, `MDIP1`, `MMMP`, `NIDDF`, `MCBADDUB`, `MCF`, `MICF`, `SGOV`).
  - Interactive **Daily / Monthly / Annual Dividend Income Simulator** + **1-Year Compounded Wealth Curve** comparing Gross TR, Net Filer, Net Non-Filer, and Flat Par NAV.

### 3. Transaction-Ledger Portfolio, Quant Lab & Look-Through X-Ray (`/portfolio`)
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

### Option B — Docker Compose (PostgreSQL 16 + Redis 7 + ARQ Worker)
```bash
cp .env.example .env
# Set AUTH_MODE=multiuser and a 32+ char SECRET_KEY in .env for cloud/VPS multi-user hosting
docker compose up --build
```

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

## License

[Apache-2.0](./LICENSE)
