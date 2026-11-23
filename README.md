# OpenKhazana

**Open-source Pakistan & global financial market terminal, PDF report analyzer, and multi-asset portfolio tracker.**

OpenKhazana brings together live **Pakistan Stock Exchange (PSX)** equities, **330+ Pakistani Mutual Funds & VPS schemes** across 13 AMCs, **SECP monthly Fund Manager Reports (FMRs)**, **PUCARS financial filings**, **SBP macro rates**, international stocks/ETFs, USD/PKR forex, local gold/silver, and crypto—paired with **Pakistan FBR tax accounting**, **Shariah compliance & purification**, and **Pandas/SciPy quantitative analytics**.

> **Zero Demo Data** — All quotes, historical bars, financial statements, mutual fund NAVs, FMR compositions, and macro rates are sourced from live public feeds or user-uploaded official PDFs/spreadsheets.

---

## Core Capabilities

### Market Intelligence & Screener
- **Unified Market Hub**: Live **KSE-100**, **KMI-30**, **USD/PKR** (Interbank & Open Market), **24K/22K Gold & Silver** (Tola / 10g / 1g converter), **Crypto** (`BTC`, `ETH`, `USDT`), PSX sector movers, top dividend yields, market breadth, and PUCARS corporate announcements.
- **PSX & Global Equities (`/stocks/[symbol]`)**:
  - **TradingView Lightweight Charts** (`1D` Daily & `5M` Intraday) with SMA/EMA/Bollinger overlays, RSI/MACD oscillators, and Price vs. Gross/Net Total-Return toggles.
  - **Interactive Valuation Lab**: Live 5Y FCF Growth & Sovereign WACC sliders + **5×5 DCF Sensitivity Matrix** (with automatic Bank P/B & ROE valuation lens for financial institutions).
  - **Accounting & Quality Diagnostics**: 9-point **Piotroski F-Score** rule checklist, **Altman Z-Score**, **3-Factor DuPont ROE decomposition**, automated **Bull vs. Bear Thesis & Red-Flag Digest**, peer sector valuation comparison, and dividend streak radar.
  - **Multi-Period Annual & Quarterly PDF Report Analyzer**: Parses PUCARS and user-uploaded company Annual/Quarterly PDF reports (`pdfplumber`) into multi-period 3-statement data (Income Statement, Balance Sheet, Cash Flow), auditor opinions, and Directors' Report highlights with **Pandas** growth/margin/CAGR modeling and CSV export.
  - **Mutual Fund Institutional Ownership Radar**: Cross-links parsed SECP FMR Top-10 equity disclosures to show which Pakistani mutual funds hold a given PSX stock.
  - **Transparent Shariah Screening**: KMI / AAOIFI debt-to-assets, non-compliant income, illiquid asset ratios, and exact **Dividend Purification Rate %**.

### Pakistani Mutual Funds, VPS & SECP FMR Engine
- **Multi-AMC Directory (`/funds`)**: Covers 330+ funds across 13 Pakistani AMCs (Al Meezan, HBL, ABL, JS Investments, NBP, UBL/Al-Ameen, MCB/Alhamra, Lakson, Mahaana, AKD, etc.) with Shariah, Daily Dividend, Category, AMC, and TER filters, Risk-Return scatter plots, and side-by-side comparison.
- **Dedicated Mutual Fund Fact Sheets (`/funds/[symbol]`)**:
  - **SECP Monthly FMR Parser**: Automatically ingests official monthly FMR PDF booklets (Al Meezan, HBL, Atlas, JSIL, ABL) plus **drag-and-drop FMR PDF upload** for any Pakistani AMC.
  - **Full Composition Breakdown**: Asset Allocation, Top-10 Equity Holdings (linked to live PSX quotes with **Implied Portfolio P/E & Dividend Yield**), Sector Allocation, Credit Quality (`AAA`/`AA+`/`Govt`), WAM/Duration, Stability Rating, Fee Anatomy (TER MTD/YTD + Govt Levies + Management Fee), and Manager Commentary.
  - **Daily Dividend Funds Support**: Handles constant-NAV daily-dividend funds (`MRAF`, `MDIP1`, `NIDDF`, `MCF`, `MICF`, etc.) with compounded total-return wealth curves, Filer/Non-Filer WHT modeling, and daily/monthly/annual income calculators.
  - **Historical NAV Import**: Built-in CSV/XLSX NAV history uploader for AMCs without public history APIs.

### Portfolio Ledger, Look-Through X-Ray & Pakistan Tax Engine
- **Multi-Asset Transaction Ledger (`/portfolio`)**: Supports `BUY`, `SELL`, `OPENING_BALANCE`, `DIVIDEND`, `REINVEST_DIVIDEND` (DRIP), `BONUS`, `RIGHTS`, `SPLIT`, `FEE`, `DEPOSIT`, `WITHDRAWAL`, optional **Auto-Managed PKR Cash Wallet**, **National Savings (NSS)** coupon accrual, and **Daily Dividend Fund** accrual.
- **Look-Through Portfolio X-Ray**: Explodes Mutual Fund & VPS positions using parsed SECP FMR compositions to reveal true combined Asset Mix, Sector Concentration, and **Direct-vs-Fund Single-Stock Overlaps**.
- **FBR Tax & Compliance Engine (Finance Act 2024)**:
  - `ACTIVE_FILER`, `LATE_FILER`, and `NON_FILER` WHT & CGT schedules across all 4 FBR acquisition-date regimes (`< Jul 2013`, `2013–2022`, `2022–2024`, `>= Jul 2024`) plus US W-8BEN / Ireland UCITS dividend WHT.
  - **Same-Day Intraday Netting** before strict FIFO lot matching and **Section 236Z** deemed-cost bonus share accounting.
  - **Section 37A Capital Loss Carryforward** (3-year FIFO netting) and **1-Click FBR Section 116(2)** fiscal-year (July 1 – June 30) wealth statement export (JSON/CSV).
  - **Shariah Dividend Purification**, **XIRR**, **Unitized Virtual-NAV TWRR**, and **CSV/Excel Broker Importer** (CDC, KTrade, Finqalab, AKD, IBKR, AMC exports).
- **Quant Risk & Strategy Backtester (`/analytics` & `/portfolio`)**:
  - NumPy, Pandas, SciPy SLSQP & **PyPortfolioOpt** engine: Sharpe, Sortino, Calmar, Max Drawdown, Historical 95% VaR/CVaR, Beta/Alpha, Pairwise Correlation Matrix, Markowitz Max-Sharpe / Min-Vol / HRP rebalancing tickets, Efficient Frontier, 3-Year Monte Carlo Wealth Fan, Macro Stress Tests, and Multi-Asset Strategy Backtesting (Nominal PKR, Real USD, Real CPI-Adjusted).
- **Watchlists, Alerts & Calculators**: Price & announcement alerts, SIP (FEL & TER), PSX Brokerage/CGT Breakeven, and Zakat Nisab calculators.

---

## Tech Stack

| Layer | Technologies |
|---|---|
| **Frontend (`apps/web`)** | Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS v4, TradingView `lightweight-charts`, Recharts, Lucide Icons |
| **Backend (`apps/api`)** | FastAPI, Python 3.11+, SQLAlchemy 2.0 (async), Pydantic v2, Alembic |
| **Analytics & PDFs** | Pandas, NumPy, SciPy, PyPortfolioOpt, Statsmodels, `ta`, `pdfplumber`, `openpyxl` |
| **Data Ingestion** | ARQ + Redis 7 (optional for Docker), `curl_cffi`, `httpx`, `BeautifulSoup4`, `yfinance` |
| **Storage** | SQLite (`aiosqlite`) for zero-config local runs; PostgreSQL 16 (partitioned `price_bars` + RLS) + Redis 7 for Docker/production |

---

## Quick Start

### Option 1: One-Click Local Run on Windows (SQLite, No Docker Required)
Double-click or run from the project root:
```bat
start-local.bat
```
Starts the FastAPI backend on `http://127.0.0.1:8000` and Next.js frontend on `http://127.0.0.1:3000`.

### Option 2: Manual Local Development

**Backend (`apps/api`)**
```bash
cd apps/api
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# Local SQLite quickstart:
export DATABASE_URL="sqlite+aiosqlite:///./openkhazana_local.db"  # Windows PowerShell: $env:DATABASE_URL="sqlite+aiosqlite:///./openkhazana_local.db"
uvicorn app.main:app --reload --port 8000
```

**Frontend (`apps/web`)**
```bash
cd apps/web
npm install
npm run dev
```
Open `http://localhost:3000` in your browser.

### Option 3: Docker Compose (PostgreSQL 16 + Redis 7 + ARQ Worker)
```bash
cp .env.example .env
docker compose up --build
```

---

## Testing & Linting

```bash
# Backend unit & API integration tests
cd apps/api
pytest -v

# Frontend production build & type-check
cd apps/web
npm run build
```

---

## Project Structure

```text
OpenKazana/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/          # FastAPI routers (market, analytics, portfolio, auth, watchlists)
│   │   │   ├── models/       # SQLAlchemy 2.0 ORM models (Assets, FMRs, Financial Periods, Ledger)
│   │   │   ├── scrapers/     # PSX DPS, PUCARS PDFs, SECP FMRs, Multi-AMC NAVs, SBP Macro, FX/Gold, Crypto, Intl
│   │   │   ├── services/     # Accounting/FIFO, FBR Tax, Pandas Analytics, Deep Fundamentals, Quant, Backtester
│   │   │   └── workers/      # ARQ scheduled worker & scraper registry
│   │   └── tests/            # Pytest unit & async integration test suite
│   └── web/
│       └── src/
│           ├── app/          # Next.js pages (/, /stocks/[symbol], /funds/[symbol], /portfolio, /analytics, /status...)
│           └── components/   # Charts, AppShell, Modals, Theme & Currency context
├── docker-compose.yml
├── start-local.bat
├── .env.example
└── LICENSE
```

---

## Disclaimer

OpenKhazana is open-source software for informational, research, and personal portfolio tracking purposes only. It is **not** financial, investment, tax, or Shariah advisory software. Always verify FBR tax figures (CGT, Section 236Z bonus tax, Section 116 wealth schedules, WHT), Zakat calculations, financial filings, and Shariah compliance/purification metrics against official PSX/NCCPL/SECP/FBR disclosures and qualified advisors before making decisions or filing taxes.

## License

[MIT](./LICENSE)
