from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_quote, get_cached_intraday_bars, set_cached_intraday_bars
from app.models.market import (
    Asset,
    CorporateAction,
    EquityFinancialSnapshot,
    PriceBar,
    PsxProfile,
    ShariahMetric,
)
from app.scrapers.base import BaseScraper, ScraperParseError
from app.services.deep_fundamentals import (
    compute_altman_z_score,
    compute_dcf_intrinsic_value,
    compute_dupont_roe,
    compute_piotroski_f_score,
)

logger = logging.getLogger("openkhazana.scrapers.intl_stocks")
D = Decimal

KNOWN_SHARIAH_INTL_TICKERS = {
    "SPUS",
    "HLAL",
    "UMMA",
    "SPSK",
    "AMAGX",
    "AMANX",
    "IGDA.L",
    "ISWD.L",
    "HIWS.L",
    "AAPL",
    "NVDA",
    "MSFT",
    "GOOGL",
    "META",
    "TSLA",
    "AMD",
    "AVGO",
    "TSM",
    "ASML",
    "LLY",
    "XOM",
    "CVX",
}

CURATED_INTL_UNIVERSE: list[str] = [
    # Global Benchmark & US Core/Dividend/Treasury ETFs
    "SPX",
    "VOO",
    "QQQ",
    "SCHD",
    "SGOV",
    # US Shariah & Ireland-Domiciled UCITS ETFs (0% US estate tax / 15% Ireland treaty WHT)
    "SPUS",
    "HLAL",
    "VWRA.L",
    "ISWD.L",
    "CSPX.L",
    # Global Mega-Cap Equities
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "BRK-B",
]

NON_HALAL_SECTORS = {
    "financial services",
    "financial",
    "banks",
    "insurance",
    "gambling",
    "tobacco",
    "alcoholic beverages",
}

YF_SYMBOL_OVERRIDES = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
}


def _d(val: Any, default: Decimal | None = Decimal("0")) -> Decimal | None:
    if val is None:
        return default
    try:
        s = str(val).strip()
        if not s or s.lower() in {"nan", "none", "inf", "-inf"}:
            return default
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return default


def _extract_yfinance_sync(symbol: str, days: int = 1825) -> dict[str, Any]:
    """Synchronous worker executed in a thread pool to fetch quote, fundamentals, financial statements, bars, and dividends via `yfinance`."""
    clean_sym = symbol.strip().upper()
    yf_sym = YF_SYMBOL_OVERRIDES.get(clean_sym, clean_sym)
    ticker = yf.Ticker(yf_sym)

    info: dict[str, Any] = {}
    try:
        raw_info = ticker.info
        if isinstance(raw_info, dict):
            info = raw_info
    except Exception as exc:
        logger.debug("yfinance .info fallback for %s: %s", yf_sym, exc)

    if days >= 1500:
        period_str = "5y"
    elif days >= 700:
        period_str = "2y"
    elif days >= 350:
        period_str = "1y"
    else:
        period_str = f"{max(days, 30)}d"

    hist = ticker.history(period=period_str, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise ScraperParseError(f"Yahoo Finance returned no price history for symbol '{clean_sym}'.")

    bars: list[dict[str, Any]] = []
    for idx, row in hist.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        close_px = _d(row.get("Close"), Decimal("0")) or Decimal("0")
        if close_px <= 0:
            continue
        open_px = _d(row.get("Open"), close_px) or close_px
        high_px = _d(row.get("High"), close_px) or close_px
        low_px = _d(row.get("Low"), close_px) or close_px
        vol = _d(row.get("Volume"), Decimal("0")) or Decimal("0")
        bars.append(
            {
                "bar_time": ts.replace(hour=16, minute=0, second=0, microsecond=0),
                "open": open_px.quantize(D("0.0001")),
                "high": max(open_px, high_px, close_px).quantize(D("0.0001")),
                "low": min(open_px, low_px, close_px).quantize(D("0.0001")),
                "close": close_px.quantize(D("0.0001")),
                "volume": max(D("0"), vol).quantize(D("0.01")),
            }
        )

    if not bars:
        raise ScraperParseError(f"Yahoo Finance history contained 0 valid close bars for '{clean_sym}'.")

    last_bar = bars[-1]
    prev_bar = bars[-2] if len(bars) >= 2 else last_bar
    last_price = (_d(info.get("currentPrice") or info.get("regularMarketPrice"), last_bar["close"]) or last_bar["close"]).quantize(D("0.0001"))
    prev_close = (_d(info.get("previousClose") or info.get("regularMarketPreviousClose"), prev_bar["close"]) or prev_bar["close"]).quantize(D("0.0001"))
    change_pct = (((last_price - prev_close) / prev_close) * D("100")).quantize(D("0.0001")) if prev_close > 0 else D("0")

    quote_type = str(info.get("quoteType") or "").upper()
    if clean_sym.startswith("^") or clean_sym in {"SPX", "NDX"}:
        asset_class = "INTL_INDEX"
    elif quote_type in {"ETF", "MUTUALFUND"} or clean_sym in {"VOO", "SPY", "QQQ", "SPUS", "HLAL", "UMMA", "SGOV", "SCHD", "JEPI", "SPSK"}:
        asset_class = "INTL_ETF"
    else:
        asset_class = "INTL_EQUITY"

    name = str(info.get("longName") or info.get("shortName") or ("S&P 500 Index" if clean_sym == "SPX" else clean_sym))[:200]
    currency = str(info.get("currency") or "USD").upper()[:8]
    sector = str(info.get("sector") or info.get("category") or ("Global Index" if asset_class == "INTL_INDEX" else "Global ETF" if asset_class == "INTL_ETF" else "Global Equity"))[:120]
    exchange = str(info.get("exchange") or ("NYSEARCA" if asset_class == "INTL_ETF" else "NASDAQ")).upper()[:32]
    country_raw = str(info.get("country") or ("IE" if clean_sym.endswith(".L") else "US")).upper()
    country = "IE" if "IRELAND" in country_raw or clean_sym.endswith(".L") else "US" if "UNITED STATES" in country_raw or country_raw == "US" else country_raw[:16]

    mcap_raw = _d(info.get("marketCap") or info.get("totalAssets"), D("0")) or D("0")
    market_cap_mn = (mcap_raw / D("1000000")).quantize(D("0.01")) if mcap_raw > 0 else D("0")
    pe_ratio = _d(info.get("trailingPE") or info.get("forwardPE"), None)
    pb_ratio = _d(info.get("priceToBook"), None)
    eps = _d(info.get("trailingEps"), None)
    roe_raw = _d(info.get("returnOnEquity"), None)
    roe_pct = (roe_raw * D("100")).quantize(D("0.01")) if roe_raw is not None else None

    div_rate = _d(info.get("dividendRate") or info.get("trailingAnnualDividendRate"), None)
    trailing_div_yield = _d(info.get("trailingAnnualDividendYield"), None)
    info_div_yield = _d(info.get("dividendYield"), None)
    if div_rate is not None and div_rate > 0 and last_price > 0:
        div_yield_pct = ((div_rate / last_price) * D("100")).quantize(D("0.01"))
    elif trailing_div_yield is not None and trailing_div_yield > 0:
        div_yield_pct = (
            trailing_div_yield * D("100") if trailing_div_yield < D("0.5") else trailing_div_yield
        ).quantize(D("0.01"))
    elif info_div_yield is not None and info_div_yield > 0:
        # yfinance `dividendYield` is already expressed in percent (e.g. 0.38 = 0.38%, 3.45 = 3.45%)
        div_yield_pct = info_div_yield.quantize(D("0.01"))
    else:
        div_yield_pct = D("0.00")

    high_52w = (_d(info.get("fiftyTwoWeekHigh"), max(b["high"] for b in bars)) or last_price).quantize(D("0.0001"))
    low_52w = (_d(info.get("fiftyTwoWeekLow"), min(b["low"] for b in bars)) or last_price).quantize(D("0.0001"))

    total_debt = _d(info.get("totalDebt"), D("0")) or D("0")
    total_cash = _d(info.get("totalCash"), D("0")) or D("0")
    debt_to_mcap_pct = ((total_debt / mcap_raw) * D("100")).quantize(D("0.01")) if mcap_raw > 0 else D("0")
    cash_to_mcap_pct = ((total_cash / mcap_raw) * D("100")).quantize(D("0.01")) if mcap_raw > 0 else D("0")
    sector_clean = sector.lower()
    core_compliant = not any(bad in sector_clean for bad in NON_HALAL_SECTORS)
    is_shariah = clean_sym in KNOWN_SHARIAH_INTL_TICKERS or (
        core_compliant and debt_to_mcap_pct < D("30.0")
    )

    default_wht = D("0.00") if country == "IE" else D("15.00")

    # Extract multi-year financial summary rows from yfinance income statement if available
    financial_summary: list[dict[str, Any]] = []
    if asset_class == "INTL_EQUITY":
        try:
            inc = getattr(ticker, "income_stmt", None)
            if inc is not None and not inc.empty:
                cols_sorted = sorted(list(inc.columns))[-4:]
                for col_dt in cols_sorted:
                    yr_str = str(col_dt.year) if hasattr(col_dt, "year") else str(col_dt)[:4]
                    col_series = inc[col_dt]
                    rev_val = _d(col_series.get("Total Revenue") or col_series.get("Operating Revenue"), D("0")) or D("0")
                    ni_val = _d(col_series.get("Net Income") or col_series.get("Net Income Common Stockholders"), D("0")) or D("0")
                    diluted_eps = _d(col_series.get("Diluted EPS") or col_series.get("Basic EPS"), None)
                    if rev_val > 0 or ni_val != 0:
                        margin = float((ni_val / rev_val) * D("100")) if rev_val > 0 else None
                        financial_summary.append(
                            {
                                "year": yr_str,
                                "revenue_bn": round(float(rev_val / D("1000000000")), 2),
                                "net_profit_bn": round(float(ni_val / D("1000000000")), 2),
                                "eps": round(float(diluted_eps), 2) if diluted_eps is not None else (float(eps) if eps is not None else None),
                                "net_margin_pct": round(margin, 2) if margin is not None else None,
                            }
                        )
        except Exception as exc:
            logger.debug("yfinance income_stmt skipped for %s: %s", clean_sym, exc)

        if not financial_summary and info.get("totalRevenue"):
            rev_ttm = _d(info.get("totalRevenue"), D("0")) or D("0")
            ni_ttm = _d(info.get("netIncomeToCommon"), D("0")) or D("0")
            pm_raw = _d(info.get("profitMargins"), None)
            financial_summary.append(
                {
                    "year": "TTM",
                    "revenue_bn": round(float(rev_ttm / D("1000000000")), 2),
                    "net_profit_bn": round(float(ni_ttm / D("1000000000")), 2),
                    "eps": float(eps) if eps is not None else None,
                    "net_margin_pct": round(float(pm_raw * D("100")), 2) if pm_raw is not None else None,
                }
            )

    # Extract 5Y dividends & stock splits from yfinance for accurate Total Return & payout history
    corp_actions: list[dict[str, Any]] = []
    try:
        divs = ticker.dividends
        if divs is not None and not divs.empty:
            cutoff_dt = date.today() - timedelta(days=365 * 5)
            for idx, val in divs.tail(30).items():
                ex_dt = idx.date() if hasattr(idx, "date") else date.today()
                if ex_dt < cutoff_dt:
                    continue
                amt = _d(val, D("0")) or D("0")
                if amt > 0:
                    corp_actions.append(
                        {
                            "action_type": "CASH_DIVIDEND",
                            "ex_date": ex_dt,
                            "payout_per_share": amt.quantize(D("0.0001")),
                            "ratio_numerator": D("0"),
                            "ratio_denominator": D("100"),
                            "description": f"Dividend {currency} {amt:.4f}/share (yfinance)",
                        }
                    )
    except Exception as exc:
        logger.debug("yfinance dividends fetch skipped for %s: %s", clean_sym, exc)

    try:
        splits = ticker.splits
        if splits is not None and not splits.empty:
            cutoff_dt = date.today() - timedelta(days=365 * 2)
            for idx, ratio in splits.tail(4).items():
                ex_dt = idx.date() if hasattr(idx, "date") else date.today()
                if ex_dt < cutoff_dt:
                    continue
                r = _d(ratio, D("0")) or D("0")
                if r > 1:
                    corp_actions.append(
                        {
                            "action_type": "SPLIT",
                            "ex_date": ex_dt,
                            "payout_per_share": D("0"),
                            "ratio_numerator": (r - D("1")) * D("100"),
                            "ratio_denominator": D("100"),
                            "description": f"{r:g}-for-1 Stock Split (yfinance)",
                        }
                    )
    except Exception as exc:
        logger.debug("yfinance splits fetch skipped for %s: %s", clean_sym, exc)

    # Extract deep Balance Sheet, Cash Flow, Valuation Ratios, Piotroski F-Score, Altman Z & DuPont
    deep_snapshot: dict[str, Any] | None = None
    if asset_class in {"INTL_EQUITY", "INTL_ETF"} and asset_class == "INTL_EQUITY":
        try:
            def _pick(series: Any, *labels: str) -> float | None:
                if series is None:
                    return None
                for lbl in labels:
                    if lbl in series.index:
                        val = _d(series.get(lbl), None)
                        if val is not None:
                            return float(val)
                return None

            def _to_mn(val: float | None) -> float | None:
                return round(val / 1_000_000.0, 2) if val is not None else None

            bs = getattr(ticker, "balance_sheet", None)
            cf = getattr(ticker, "cashflow", None)
            inc = getattr(ticker, "income_stmt", None)

            bs_cols = sorted(list(bs.columns), reverse=True) if bs is not None and not bs.empty else []
            cf_cols = sorted(list(cf.columns), reverse=True) if cf is not None and not cf.empty else []
            inc_cols = sorted(list(inc.columns), reverse=True) if inc is not None and not inc.empty else []

            bs0 = bs[bs_cols[0]] if bs_cols else None
            bs1 = bs[bs_cols[1]] if len(bs_cols) > 1 else None
            cf0 = cf[cf_cols[0]] if cf_cols else None
            inc0 = inc[inc_cols[0]] if inc_cols else None
            inc1 = inc[inc_cols[1]] if len(inc_cols) > 1 else None

            rev_mn = _to_mn(_pick(inc0, "Total Revenue", "Operating Revenue")) or _to_mn(
                float(info.get("totalRevenue")) if info.get("totalRevenue") is not None else None
            )
            prev_rev_mn = _to_mn(_pick(inc1, "Total Revenue", "Operating Revenue"))
            gp_mn = _to_mn(_pick(inc0, "Gross Profit")) or _to_mn(
                float(info.get("grossProfits")) if info.get("grossProfits") is not None else None
            )
            ebit_mn = _to_mn(_pick(inc0, "Operating Income", "EBIT"))
            int_exp_mn = _to_mn(_pick(inc0, "Interest Expense", "Interest Expense Non Operating"))
            ni_mn = _to_mn(_pick(inc0, "Net Income", "Net Income Common Stockholders")) or _to_mn(
                float(info.get("netIncomeToCommon")) if info.get("netIncomeToCommon") is not None else None
            )
            prev_ni_mn = _to_mn(_pick(inc1, "Net Income", "Net Income Common Stockholders"))

            tot_assets_mn = _to_mn(_pick(bs0, "Total Assets"))
            prev_assets_mn = _to_mn(_pick(bs1, "Total Assets"))
            cur_assets_mn = _to_mn(_pick(bs0, "Current Assets", "Total Current Assets"))
            prev_cur_assets_mn = _to_mn(_pick(bs1, "Current Assets", "Total Current Assets"))
            tot_liab_mn = _to_mn(_pick(bs0, "Total Liabilities Net Minority Interest", "Total Liabilities"))
            cur_liab_mn = _to_mn(_pick(bs0, "Current Liabilities", "Total Current Liabilities"))
            prev_cur_liab_mn = _to_mn(_pick(bs1, "Current Liabilities", "Total Current Liabilities"))
            eq_mn = _to_mn(_pick(bs0, "Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"))
            lt_debt_mn = _to_mn(_pick(bs0, "Long Term Debt", "Long Term Debt And Capital Lease Obligation"))
            prev_lt_debt_mn = _to_mn(_pick(bs1, "Long Term Debt", "Long Term Debt And Capital Lease Obligation"))
            tot_debt_mn = _to_mn(_pick(bs0, "Total Debt")) or _to_mn(float(total_debt) if total_debt > 0 else None)
            cash_mn = _to_mn(_pick(bs0, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")) or _to_mn(
                float(total_cash) if total_cash > 0 else None
            )
            retained_mn = _to_mn(_pick(bs0, "Retained Earnings"))

            ocf_mn = _to_mn(_pick(cf0, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")) or _to_mn(
                float(info.get("operatingCashflow")) if info.get("operatingCashflow") is not None else None
            )
            capex_raw = _pick(cf0, "Capital Expenditure", "Purchase Of PPE")
            capex_mn = round(abs(capex_raw) / 1_000_000.0, 2) if capex_raw is not None else None
            fcf_mn = _to_mn(_pick(cf0, "Free Cash Flow")) or _to_mn(
                float(info.get("freeCashflow")) if info.get("freeCashflow") is not None else None
            )
            if fcf_mn is None and ocf_mn is not None and capex_mn is not None:
                fcf_mn = round(ocf_mn - capex_mn, 2)

            shares_raw = _d(info.get("sharesOutstanding"), None)
            shares_mn = round(float(shares_raw) / 1_000_000.0, 4) if shares_raw and shares_raw > 0 else _to_mn(
                _pick(bs0, "Ordinary Shares Number", "Share Issued")
            )
            prev_shares_mn = _to_mn(_pick(bs1, "Ordinary Shares Number", "Share Issued"))
            bvps_val = float(_d(info.get("bookValue"), None)) if info.get("bookValue") is not None else (
                round(eq_mn / shares_mn, 2) if eq_mn is not None and shares_mn and shares_mn > 0 else None
            )

            gm_pct = round((gp_mn / rev_mn) * 100.0, 2) if gp_mn is not None and rev_mn and rev_mn > 0 else (
                round(float(_d(info.get("grossMargins"), D("0")) * D("100")), 2) if info.get("grossMargins") is not None else None
            )
            om_pct = round((ebit_mn / rev_mn) * 100.0, 2) if ebit_mn is not None and rev_mn and rev_mn > 0 else (
                round(float(_d(info.get("operatingMargins"), D("0")) * D("100")), 2) if info.get("operatingMargins") is not None else None
            )
            nm_pct = round((ni_mn / rev_mn) * 100.0, 2) if ni_mn is not None and rev_mn and rev_mn > 0 else (
                round(float(_d(info.get("profitMargins"), D("0")) * D("100")), 2) if info.get("profitMargins") is not None else None
            )
            roe_f = float(roe_pct) if roe_pct is not None else (
                round((ni_mn / eq_mn) * 100.0, 2) if ni_mn is not None and eq_mn and eq_mn > 0 else None
            )
            roa_f = round((ni_mn / tot_assets_mn) * 100.0, 2) if ni_mn is not None and tot_assets_mn and tot_assets_mn > 0 else (
                round(float(_d(info.get("returnOnAssets"), D("0")) * D("100")), 2) if info.get("returnOnAssets") is not None else None
            )
            cur_ratio = round(cur_assets_mn / cur_liab_mn, 2) if cur_assets_mn is not None and cur_liab_mn and cur_liab_mn > 0 else (
                round(float(_d(info.get("currentRatio"), D("0"))), 2) if info.get("currentRatio") is not None else None
            )
            d_to_e = round(tot_debt_mn / eq_mn, 2) if tot_debt_mn is not None and eq_mn and eq_mn > 0 else (
                round(float(_d(info.get("debtToEquity"), D("0"))) / 100.0, 2) if info.get("debtToEquity") is not None else None
            )
            d_to_a = round((tot_debt_mn / tot_assets_mn) * 100.0, 2) if tot_debt_mn is not None and tot_assets_mn and tot_assets_mn > 0 else float(debt_to_mcap_pct)
            int_cov = round(ebit_mn / abs(int_exp_mn), 2) if ebit_mn is not None and int_exp_mn and abs(int_exp_mn) > 0 else None
            fcf_yield = round((fcf_mn / float(market_cap_mn)) * 100.0, 2) if fcf_mn is not None and market_cap_mn > 0 else None
            payout_r = round(float(_d(info.get("payoutRatio"), D("0")) * D("100")), 2) if info.get("payoutRatio") is not None else None
            invested_cap = (eq_mn or 0.0) + (tot_debt_mn or 0.0) - (cash_mn or 0.0)
            roic_f = round(((ebit_mn * 0.78) / invested_cap) * 100.0, 2) if ebit_mn is not None and invested_cap > 0 else None

            dcf_val = compute_dcf_intrinsic_value(
                free_cashflow_mn=fcf_mn,
                shares_mn=shares_mn,
                total_debt_mn=tot_debt_mn or 0.0,
                cash_mn=cash_mn or 0.0,
                growth_rate_pct=10.0,
                risk_free_rate_pct=4.25,
                erp_pct=5.0,
            )
            prev_nm_pct = (
                round((prev_ni_mn / prev_rev_mn) * 100.0, 2)
                if prev_ni_mn is not None and prev_rev_mn and prev_rev_mn > 0
                else None
            )
            f_score, _ = compute_piotroski_f_score(
                net_income=ni_mn,
                prev_net_income=prev_ni_mn,
                operating_cashflow=ocf_mn,
                total_assets=tot_assets_mn,
                prev_total_assets=prev_assets_mn,
                long_term_debt=lt_debt_mn,
                prev_long_term_debt=prev_lt_debt_mn,
                current_assets=cur_assets_mn,
                prev_current_assets=prev_cur_assets_mn,
                current_liabilities=cur_liab_mn,
                prev_current_liabilities=prev_cur_liab_mn,
                net_margin_pct=nm_pct,
                prev_net_margin_pct=prev_nm_pct,
                shares=shares_mn,
                prev_shares=prev_shares_mn,
                revenue=rev_mn,
                prev_revenue=prev_rev_mn,
            )
            z_score, _ = compute_altman_z_score(
                sector=sector,
                current_assets_mn=cur_assets_mn,
                current_liabilities_mn=cur_liab_mn,
                retained_earnings_mn=retained_mn,
                ebit_mn=ebit_mn,
                market_cap_mn=float(market_cap_mn) if market_cap_mn > 0 else None,
                total_liabilities_mn=tot_liab_mn,
                revenue_mn=rev_mn,
                total_assets_mn=tot_assets_mn,
            )
            dupont = compute_dupont_roe(
                net_income_mn=ni_mn,
                revenue_mn=rev_mn,
                total_assets_mn=tot_assets_mn,
                shareholders_equity_mn=eq_mn,
            )
            period_label = (
                str(bs_cols[0].date()) if bs_cols and hasattr(bs_cols[0], "date")
                else (str(inc_cols[0].date()) if inc_cols and hasattr(inc_cols[0], "date") else "TTM")
            )
            deep_snapshot = {
                "source": "yfinance",
                "fiscal_period": period_label,
                "unit_scale": f"{currency}_MN",
                "revenue_mn": rev_mn,
                "gross_profit_mn": gp_mn,
                "operating_income_mn": ebit_mn,
                "net_income_mn": ni_mn,
                "total_assets_mn": tot_assets_mn,
                "current_assets_mn": cur_assets_mn,
                "total_liabilities_mn": tot_liab_mn,
                "current_liabilities_mn": cur_liab_mn,
                "shareholders_equity_mn": eq_mn,
                "total_debt_mn": tot_debt_mn,
                "cash_and_equivalents_mn": cash_mn,
                "operating_cashflow_mn": ocf_mn,
                "capex_mn": capex_mn,
                "free_cashflow_mn": fcf_mn,
                "shares_outstanding_mn": shares_mn,
                "bvps": bvps_val,
                "gross_margin_pct": gm_pct,
                "operating_margin_pct": om_pct,
                "net_margin_pct": nm_pct,
                "roe_pct": roe_f,
                "roa_pct": roa_f,
                "roic_pct": roic_f,
                "current_ratio": cur_ratio,
                "debt_to_equity": d_to_e,
                "debt_to_assets_pct": d_to_a,
                "interest_coverage": int_cov,
                "payout_ratio_pct": payout_r,
                "fcf_yield_pct": fcf_yield,
                "dcf_intrinsic_value": dcf_val,
                "piotroski_f_score": f_score,
                "altman_z_score": z_score,
                "dupont_breakdown": dupont,
                "analyst_target_price": float(_d(info.get("targetMeanPrice"), None)) if info.get("targetMeanPrice") is not None else None,
                "analyst_recommendation": str(info.get("recommendationKey") or "").upper()[:32] or None,
                "extra_metrics": {"target_high": info.get("targetHighPrice"), "target_low": info.get("targetLowPrice")},
            }
        except Exception as exc:
            logger.debug("Deep yfinance extraction skipped for %s: %s", clean_sym, exc)

    return {
        "symbol": clean_sym,
        "name": name,
        "asset_class": asset_class,
        "currency": currency,
        "sector_or_category": sector,
        "exchange": exchange,
        "country": country,
        "default_wht_rate_pct": default_wht,
        "is_shariah_compliant": is_shariah,
        "debt_to_assets_pct": min(D("99.99"), debt_to_mcap_pct),
        "cash_to_assets_pct": min(D("99.99"), cash_to_mcap_pct),
        "last_price": last_price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "volume": last_bar["volume"],
        "market_cap_mn": market_cap_mn,
        "pe_ratio": pe_ratio.quantize(D("0.01")) if pe_ratio is not None else None,
        "pb_ratio": pb_ratio.quantize(D("0.01")) if pb_ratio is not None else None,
        "eps": eps.quantize(D("0.01")) if eps is not None else None,
        "roe_pct": roe_pct,
        "dividend_yield_pct": div_yield_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "financial_summary_json": financial_summary,
        "deep_snapshot": deep_snapshot,
        "bars": bars,
        "corporate_actions": corp_actions,
    }


async def fetch_yfinance_ticker_snapshot(symbol: str, days: int = 180) -> dict[str, Any]:
    """Fetch a global equity/ETF/index snapshot asynchronously using `yfinance`."""
    return await asyncio.to_thread(_extract_yfinance_sync, symbol, days)


async def upsert_yfinance_asset(
    db: AsyncSession,
    symbol: str,
    snapshot: dict[str, Any] | None = None,
    days: int = 180,
) -> Asset:
    """Fetch (if needed) and upsert an international stock/ETF/index and its bars + dividends into DB."""
    data = snapshot or await fetch_yfinance_ticker_snapshot(symbol, days=days)
    sym = data["symbol"]
    now = datetime.now(timezone.utc)

    asset = (
        await db.execute(select(Asset).where(Asset.symbol == sym, Asset.owner_user_id.is_(None)))
    ).scalar_one_or_none()

    if asset is None:
        asset = Asset(
            symbol=sym,
            name=data["name"],
            asset_class=data["asset_class"],
            currency=data["currency"],
            sector_or_category=data["sector_or_category"],
            is_shariah_compliant=data["is_shariah_compliant"],
            last_price=data["last_price"],
            prev_close=data["prev_close"],
            change_pct=data["change_pct"],
            volume=data["volume"],
            source="yfinance",
            updated_at=now,
        )
        db.add(asset)
        await db.flush()
    else:
        asset.name = data["name"]
        asset.asset_class = data["asset_class"]
        asset.currency = data["currency"]
        asset.sector_or_category = data["sector_or_category"]
        asset.is_shariah_compliant = data["is_shariah_compliant"]
        asset.last_price = data["last_price"]
        asset.prev_close = data["prev_close"]
        asset.change_pct = data["change_pct"]
        asset.volume = data["volume"]
        asset.source = "yfinance"
        asset.updated_at = now

    if data["asset_class"] in {"INTL_EQUITY", "INTL_ETF"}:
        profile = (
            await db.execute(select(PsxProfile).where(PsxProfile.asset_id == asset.id))
        ).scalar_one_or_none()
        if profile is None:
            profile = PsxProfile(
                asset_id=asset.id,
                exchange=data["exchange"],
                country=data["country"],
                default_wht_rate_pct=data["default_wht_rate_pct"],
                market_cap_mn=data["market_cap_mn"],
                free_float_pct=D("85.00"),
                pe_ratio=data["pe_ratio"],
                pb_ratio=data["pb_ratio"],
                eps=data["eps"],
                roe_pct=data["roe_pct"],
                dividend_yield_pct=data["dividend_yield_pct"],
                high_52w=data["high_52w"],
                low_52w=data["low_52w"],
                is_kse100=False,
                is_kmi30=False,
                financial_summary_json=data.get("financial_summary_json", []),
            )
            db.add(profile)
        else:
            profile.exchange = data["exchange"]
            profile.country = data["country"]
            profile.default_wht_rate_pct = data["default_wht_rate_pct"]
            profile.market_cap_mn = data["market_cap_mn"]
            profile.pe_ratio = data["pe_ratio"]
            profile.pb_ratio = data["pb_ratio"]
            profile.eps = data["eps"]
            profile.roe_pct = data["roe_pct"]
            profile.dividend_yield_pct = data["dividend_yield_pct"]
            profile.high_52w = data["high_52w"]
            profile.low_52w = data["low_52w"]
            if data.get("financial_summary_json"):
                profile.financial_summary_json = data["financial_summary_json"]

        shariah = (
            await db.execute(select(ShariahMetric).where(ShariahMetric.asset_id == asset.id))
        ).scalar_one_or_none()
        purif_pct = D("1.20") if data["is_shariah_compliant"] and data["asset_class"] == "INTL_EQUITY" else D("0.00")
        if shariah is None:
            db.add(
                ShariahMetric(
                    asset_id=asset.id,
                    fiscal_period="AAOIFI-Global",
                    core_business_compliant=data["is_shariah_compliant"],
                    is_kmi_all_share=False,
                    debt_to_assets_pct=data.get("debt_to_assets_pct", D("10.00")),
                    non_compliant_inv_pct=data.get("cash_to_assets_pct", D("3.50")) if data["is_shariah_compliant"] else D("40.00"),
                    non_compliant_income_pct=purif_pct if data["is_shariah_compliant"] else D("25.00"),
                    purification_rate_pct=purif_pct,
                    illiquid_assets_pct=D("60.00"),
                    passed=data["is_shariah_compliant"],
                )
            )
        else:
            shariah.debt_to_assets_pct = data.get("debt_to_assets_pct", shariah.debt_to_assets_pct)
            shariah.passed = data["is_shariah_compliant"]
            shariah.core_business_compliant = data["is_shariah_compliant"]

    existing_bars = {
        b.bar_time.date(): b
        for b in (
            await db.execute(
                select(PriceBar).where(PriceBar.asset_id == asset.id, PriceBar.timeframe == "1D")
            )
        ).scalars()
    }
    for b in data.get("bars", []):
        b_dt = b["bar_time"].date()
        existing_bar = existing_bars.get(b_dt)
        if existing_bar is None:
            new_bar = PriceBar(
                asset_id=asset.id,
                timeframe="1D",
                bar_time=b["bar_time"],
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume"],
            )
            db.add(new_bar)
            existing_bars[b_dt] = new_bar
        else:
            existing_bar.open = b["open"]
            existing_bar.high = b["high"]
            existing_bar.low = b["low"]
            existing_bar.close = b["close"]
            existing_bar.volume = b["volume"]

    existing_cas = {
        (ca.action_type, ca.ex_date): ca
        for ca in (
            await db.execute(select(CorporateAction).where(CorporateAction.asset_id == asset.id))
        ).scalars()
    }
    for ca_row in data.get("corporate_actions", []):
        key = (ca_row["action_type"], ca_row["ex_date"])
        if key not in existing_cas:
            db.add(
                CorporateAction(
                    asset_id=asset.id,
                    action_type=ca_row["action_type"],
                    ex_date=ca_row["ex_date"],
                    payout_per_share=ca_row["payout_per_share"],
                    ratio_numerator=ca_row["ratio_numerator"],
                    ratio_denominator=ca_row["ratio_denominator"],
                    description=ca_row["description"],
                )
            )

    deep = data.get("deep_snapshot")
    if deep and data.get("asset_class") == "INTL_EQUITY":
        snap = (
            await db.execute(
                select(EquityFinancialSnapshot).where(EquityFinancialSnapshot.asset_id == asset.id)
            )
        ).scalar_one_or_none()
        if snap is None:
            snap = EquityFinancialSnapshot(asset_id=asset.id)
            db.add(snap)

        def _dec_or_none(v: Any) -> Decimal | None:
            return D(str(v)) if v is not None else None

        snap.source = str(deep.get("source") or "yfinance")
        snap.fiscal_period = str(deep.get("fiscal_period") or "TTM")
        snap.bvps = _dec_or_none(deep.get("bvps"))
        snap.total_assets_mn = _dec_or_none(deep.get("total_assets_mn"))
        snap.total_debt_mn = _dec_or_none(deep.get("total_debt_mn"))
        snap.cash_equivalents_mn = _dec_or_none(deep.get("cash_and_equivalents_mn"))
        snap.shareholders_equity_mn = _dec_or_none(deep.get("shareholders_equity_mn"))
        snap.operating_cashflow_mn = _dec_or_none(deep.get("operating_cashflow_mn"))
        snap.capex_mn = _dec_or_none(deep.get("capex_mn"))
        snap.free_cashflow_mn = _dec_or_none(deep.get("free_cashflow_mn"))
        snap.current_ratio = _dec_or_none(deep.get("current_ratio"))
        snap.debt_to_equity = _dec_or_none(deep.get("debt_to_equity"))
        snap.roe_pct = _dec_or_none(deep.get("roe_pct"))
        snap.roa_pct = _dec_or_none(deep.get("roa_pct"))
        snap.roic_pct = _dec_or_none(deep.get("roic_pct"))
        snap.operating_margin_pct = _dec_or_none(deep.get("operating_margin_pct"))
        snap.payout_ratio_pct = _dec_or_none(deep.get("payout_ratio_pct"))
        snap.analyst_target_price = _dec_or_none(deep.get("analyst_target_price"))
        snap.analyst_rating = deep.get("analyst_recommendation")
        snap.piotroski_f_score = deep.get("piotroski_f_score")
        snap.altman_z_score = _dec_or_none(deep.get("altman_z_score"))
        snap.dcf_intrinsic_value = _dec_or_none(deep.get("dcf_intrinsic_value"))
        snap.metrics_json = {
            "unit_scale": deep.get("unit_scale"),
            "revenue_mn": deep.get("revenue_mn"),
            "gross_profit_mn": deep.get("gross_profit_mn"),
            "operating_income_mn": deep.get("operating_income_mn"),
            "net_income_mn": deep.get("net_income_mn"),
            "current_assets_mn": deep.get("current_assets_mn"),
            "total_liabilities_mn": deep.get("total_liabilities_mn"),
            "current_liabilities_mn": deep.get("current_liabilities_mn"),
            "shares_outstanding_mn": deep.get("shares_outstanding_mn"),
            "gross_margin_pct": deep.get("gross_margin_pct"),
            "net_margin_pct": deep.get("net_margin_pct"),
            "debt_to_assets_pct": deep.get("debt_to_assets_pct"),
            "interest_coverage": deep.get("interest_coverage"),
            "fcf_yield_pct": deep.get("fcf_yield_pct"),
            "dupont_breakdown": deep.get("dupont_breakdown"),
            **(deep.get("extra_metrics") or {}),
        }
        snap.updated_at = now

    await cache_quote(
        asset.symbol,
        {
            "symbol": asset.symbol,
            "last_price": str(asset.last_price),
            "currency": asset.currency,
            "updated_at": now.isoformat(),
        },
    )
    await db.commit()
    await db.refresh(asset)
    return asset


async def ensure_yfinance_asset_backfilled(
    db: AsyncSession,
    asset: Asset,
    min_bars: int = 30,
) -> None:
    """On-demand lazy EOD & fundamentals backfill via `yfinance` for any international equity, ETF, or index (`SPX`)."""
    if asset.asset_class not in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"}:
        return
    bar_count = (
        await db.execute(
            select(func.count(PriceBar.id)).where(
                PriceBar.asset_id == asset.id,
                PriceBar.timeframe == "1D",
            )
        )
    ).scalar_one()
    if bar_count >= min_bars and asset.last_price > 0 and asset.source == "yfinance":
        return
    try:
        await upsert_yfinance_asset(db, asset.symbol, days=180)
    except Exception as exc:
        logger.debug("On-demand yfinance backfill skipped for %s: %s", asset.symbol, exc)


async def fetch_lazy_intl_intraday_bars(symbol: str) -> list[dict[str, Any]]:
    """Fetch 5M intraday bars for an international ticker via `yfinance` with 5-minute Redis caching."""
    cached = await get_cached_intraday_bars(symbol)
    if cached:
        return cached

    def _pull_5m() -> list[dict[str, Any]]:
        clean_s = symbol.strip().upper()
        yf_s = YF_SYMBOL_OVERRIDES.get(clean_s, clean_s)
        ticker = yf.Ticker(yf_s)
        df = ticker.history(period="5d", interval="5m", auto_adjust=False)
        if df is None or df.empty:
            return []
        out: list[dict[str, Any]] = []
        for idx, row in df.tail(120).iterrows():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            c = _d(row.get("Close"), D("0")) or D("0")
            if c <= 0:
                continue
            o = _d(row.get("Open"), c) or c
            h = _d(row.get("High"), c) or c
            low_p = _d(row.get("Low"), c) or c
            vol = _d(row.get("Volume"), D("0")) or D("0")
            out.append(
                {
                    "bar_time": ts.isoformat(),
                    "open": str(o.quantize(D("0.0001"))),
                    "high": str(max(o, h, c).quantize(D("0.0001"))),
                    "low": str(min(o, low_p, c).quantize(D("0.0001"))),
                    "close": str(c.quantize(D("0.0001"))),
                    "volume": str(max(D("0"), vol).quantize(D("0.01"))),
                }
            )
        return out

    try:
        bars = await asyncio.to_thread(_pull_5m)
        if bars:
            await set_cached_intraday_bars(symbol, bars, ttl_seconds=300)
        return bars
    except Exception as exc:
        logger.debug("Lazy 5m yfinance fetch failed for %s: %s", symbol, exc)
        return []


async def search_yfinance_tickers(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search global stocks & ETFs (US, Ireland UCITS .L, UK, Europe, GCC) via Yahoo Finance autocomplete."""
    import httpx

    q = query.strip()
    if len(q) < 1:
        return []
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                url,
                params={"q": q, "quotesCount": max(limit, 10), "newsCount": 0},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
            quotes = (resp.json() or {}).get("quotes") or []
            results: list[dict[str, Any]] = []
            for item in quotes:
                sym = str(item.get("symbol") or "").strip().upper()
                qtype = str(item.get("quoteType") or "").upper()
                if not sym or qtype not in {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}:
                    continue
                results.append(
                    {
                        "symbol": sym,
                        "name": str(item.get("longname") or item.get("shortname") or sym)[:180],
                        "exchange": str(item.get("exchDisp") or item.get("exchange") or "")[:40],
                        "quote_type": qtype,
                        "sector": str(item.get("sector") or item.get("typeDisp") or "")[:80],
                        "is_shariah_hint": sym in KNOWN_SHARIAH_INTL_TICKERS,
                    }
                )
                if len(results) >= limit:
                    break
            return results
    except Exception as exc:
        logger.debug("Yahoo Finance ticker search failed for %s: %s", q, exc)
        return []


class IntlStocksScraper(BaseScraper):
    """Refreshes curated & user-imported International Equities, UCITS/Shariah ETFs, and Global Indices with up to 5Y history via `yfinance`."""

    name = "intl_stocks"

    async def run(self, db: AsyncSession) -> int:
        intl_assets = list(
            (
                await db.execute(
                    select(Asset).where(
                        Asset.owner_user_id.is_(None),
                        Asset.asset_class.in_(["INTL_EQUITY", "INTL_ETF", "INTL_INDEX"]),
                    )
                )
            ).scalars()
        )
        bar_counts = {
            row[0]: int(row[1])
            for row in (
                await db.execute(
                    select(PriceBar.asset_id, func.count(PriceBar.id))
                    .where(
                        PriceBar.asset_id.in_([a.id for a in intl_assets]),
                        PriceBar.timeframe == "1D",
                    )
                    .group_by(PriceBar.asset_id)
                )
            ).all()
        } if intl_assets else {}

        existing_by_sym = {a.symbol.upper(): a for a in intl_assets}
        target_symbols = list(dict.fromkeys(list(CURATED_INTL_UNIVERSE) + list(existing_by_sym.keys())))

        upserted = 0
        for sym in target_symbols:
            try:
                existing = existing_by_sym.get(sym.upper())
                cur_bars = bar_counts.get(existing.id, 0) if existing else 0
                fetch_days = 1825 if cur_bars < 500 else 60
                snap = await fetch_yfinance_ticker_snapshot(sym, days=fetch_days)
                await upsert_yfinance_asset(db, sym, snapshot=snap, days=fetch_days)
                upserted += 1
            except Exception as exc:
                logger.warning("IntlStocksScraper skipped %s: %s", sym, exc)

        if upserted == 0:
            raise ScraperParseError("IntlStocksScraper failed to refresh any international tickers.")
        return upserted
