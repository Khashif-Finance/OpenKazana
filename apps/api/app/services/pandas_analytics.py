"""
Full Institutional Pandas Analytics Engine for OpenKhazana v5.

Powers:
  1. Multi-Period Annual & Quarterly 3-Statement Financial Modeling (YoY/QoQ growth, Common-Size %,
     3Y/5Y CAGRs, Rolling TTM, Cash Conversion, Working Capital DSO/DIO, CSV export)
  2. Investing.com-style Technical Summary Consensus Gauge (12 Moving Averages + 8 Oscillators ->
     Strong Buy / Buy / Neutral / Sell / Strong Sell)
  3. Monthly Seasonality Return Heatmap (Year x Month Pandas pivot matrix) & Rolling 90D Vol/Sharpe/Beta
  4. Cross-Sectional PSX Sector Factor Percentile Ranks (Value, Quality, Income, Size, Composite 0-100)
  5. Sarmaya.pk-style PSX Market Treemap Heatmap & Market Breadth (Advancers/Decliners + Sector Leaders)
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import pandas as pd


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _r2(v: Any) -> float | None:
    f = _safe_float(v)
    return round(f, 2) if f is not None else None


def _r4(v: Any) -> float | None:
    f = _safe_float(v)
    return round(f, 4) if f is not None else None


def _clean_str(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    return s if s else None


def _cagr(series: pd.Series, years: int) -> float | None:
    clean = series.dropna()
    if len(clean) < 2:
        return None
    end_val = float(clean.iloc[-1])
    span = min(years, len(clean) - 1)
    start_val = float(clean.iloc[-(span + 1)])
    if start_val <= 0 or end_val <= 0 or span <= 0:
        return None
    return round(((end_val / start_val) ** (1.0 / span) - 1.0) * 100.0, 2)


# ---------------------------------------------------------------------------
# 1. Multi-Period Annual & Quarterly 3-Statement Financial Analysis (Pandas)
# ---------------------------------------------------------------------------
def analyze_financial_statements_pandas(
    periods: list[dict[str, Any]],
    *,
    is_financial: bool = False,
) -> dict[str, Any]:
    """Build full Pandas multi-period financial statement analytics across Annual & Quarterly reports."""
    if not periods:
        return {
            "annual_rows": [],
            "quarterly_rows": [],
            "cagrs": {},
            "ttm_summary": {},
            "latest_disclosures": {},
            "earnings_quality": {},
        }

    df_all = pd.DataFrame(periods)
    df_all["period_end_dt"] = pd.to_datetime(df_all["period_end_date"], errors="coerce")
    df_all = df_all.sort_values(["period_end_dt", "fiscal_year", "period_label"]).reset_index(drop=True)

    num_cols = [
        "revenue_mn", "cost_of_sales_mn", "gross_profit_mn", "operating_profit_mn",
        "finance_cost_mn", "profit_before_tax_mn", "taxation_mn", "net_profit_mn",
        "eps", "dps", "total_assets_mn", "current_assets_mn", "cash_equivalents_mn",
        "inventory_mn", "receivables_mn", "total_liabilities_mn", "current_liabilities_mn",
        "total_debt_mn", "shareholders_equity_mn", "operating_cashflow_mn",
        "capex_mn", "free_cashflow_mn", "dividends_paid_mn",
    ]
    for c in num_cols:
        if c not in df_all.columns:
            df_all[c] = np.nan
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

    def _enrich_frame(sub: pd.DataFrame) -> list[dict[str, Any]]:
        if sub.empty:
            return []
        s = sub.sort_values(["period_end_dt", "fiscal_year", "period_label"]).copy()
        rev = s["revenue_mn"].replace(0, np.nan)
        pbt = s["profit_before_tax_mn"].replace(0, np.nan)
        pat = s["net_profit_mn"].replace(0, np.nan)
        ta = s["total_assets_mn"].replace(0, np.nan)
        eq = s["shareholders_equity_mn"].replace(0, np.nan)
        cl = s["current_liabilities_mn"].replace(0, np.nan)
        cos = s["cost_of_sales_mn"].replace(0, np.nan)

        # Common-Size Vertical Ratios (% of Revenue / Assets)
        s["gross_margin_pct"] = (s["gross_profit_mn"] / rev) * 100.0
        s["operating_margin_pct"] = (s["operating_profit_mn"] / rev) * 100.0
        s["net_margin_pct"] = (s["net_profit_mn"] / rev) * 100.0
        s["effective_tax_rate_pct"] = (s["taxation_mn"] / pbt) * 100.0
        s["fcf_margin_pct"] = (s["free_cashflow_mn"] / rev) * 100.0

        # Profitability, Solvency & Working Capital Efficiency
        s["roe_pct"] = (s["net_profit_mn"] / eq) * 100.0
        s["roa_pct"] = (s["net_profit_mn"] / ta) * 100.0
        s["debt_to_equity"] = s["total_debt_mn"] / eq
        s["debt_to_assets_pct"] = (s["total_debt_mn"] / ta) * 100.0
        s["current_ratio"] = s["current_assets_mn"] / cl
        s["cash_conversion_x"] = s["operating_cashflow_mn"] / pat
        if not is_financial:
            s["dso_days"] = (s["receivables_mn"] / rev) * 365.0
            s["dio_days"] = (s["inventory_mn"] / cos) * 365.0
        else:
            s["dso_days"] = np.nan
            s["dio_days"] = np.nan

        # Vectorized Period-over-Period Growth %
        for col, out_col in [
            ("revenue_mn", "revenue_growth_pct"),
            ("net_profit_mn", "net_profit_growth_pct"),
            ("eps", "eps_growth_pct"),
            ("shareholders_equity_mn", "equity_growth_pct"),
        ]:
            prev = s[col].shift(1)
            valid = prev.abs() > 1e-6
            s[out_col] = np.where(valid, ((s[col] - prev) / prev.abs()) * 100.0, np.nan)

        records: list[dict[str, Any]] = []
        for _, row in s.iterrows():
            rec: dict[str, Any] = {
                "period_type": str(row.get("period_type") or "ANNUAL"),
                "period_label": str(row.get("period_label") or ""),
                "period_end_date": str(row.get("period_end_date") or "")[:10],
                "fiscal_year": int(row.get("fiscal_year") or 0),
                "source_label": _clean_str(row.get("source_label")) or "",
                "source_pdf_url": _clean_str(row.get("source_pdf_url")),
                "auditor_opinion": _clean_str(row.get("auditor_opinion")),
                "dividend_bonus_note": _clean_str(row.get("dividend_bonus_note")),
            }
            for col in num_cols + [
                "gross_margin_pct", "operating_margin_pct", "net_margin_pct",
                "effective_tax_rate_pct", "fcf_margin_pct", "roe_pct", "roa_pct",
                "debt_to_equity", "debt_to_assets_pct", "current_ratio",
                "cash_conversion_x", "dso_days", "dio_days",
                "revenue_growth_pct", "net_profit_growth_pct", "eps_growth_pct", "equity_growth_pct",
            ]:
                rec[col] = _r4(row.get(col)) if col in {"eps", "dps"} else _r2(row.get(col))
            records.append(rec)
        return records

    ann_df = df_all[df_all["period_type"] == "ANNUAL"].copy()
    qtr_df = df_all[df_all["period_type"] == "QUARTERLY"].copy()

    annual_rows = _enrich_frame(ann_df)
    quarterly_rows = _enrich_frame(qtr_df)

    # Multi-Year CAGRs (3Y & 5Y)
    cagrs: dict[str, float | None] = {}
    if len(ann_df) >= 2:
        ann_s = ann_df.sort_values(["period_end_dt", "fiscal_year"])
        cagrs = {
            "revenue_cagr_3y_pct": _cagr(ann_s["revenue_mn"], 3),
            "revenue_cagr_5y_pct": _cagr(ann_s["revenue_mn"], 5),
            "net_profit_cagr_3y_pct": _cagr(ann_s["net_profit_mn"], 3),
            "net_profit_cagr_5y_pct": _cagr(ann_s["net_profit_mn"], 5),
            "eps_cagr_3y_pct": _cagr(ann_s["eps"], 3),
            "equity_cagr_3y_pct": _cagr(ann_s["shareholders_equity_mn"], 3),
        }

    # Latest Disclosures (preferring latest row that has auditor/directors notes)
    latest_disc: dict[str, Any] = {
        "auditor_opinion": None,
        "directors_highlights": [],
        "dividend_bonus_note": None,
        "segment_notes": [],
        "source_pdf_url": None,
        "period_label": None,
    }
    for _, row in df_all.iloc[::-1].iterrows():
        ao = _clean_str(row.get("auditor_opinion"))
        if not latest_disc["auditor_opinion"] and ao:
            latest_disc["auditor_opinion"] = ao
        hl = row.get("directors_highlights_json")
        if not latest_disc["directors_highlights"] and isinstance(hl, list) and hl:
            latest_disc["directors_highlights"] = hl[:6]
            latest_disc["period_label"] = _clean_str(row.get("period_label")) or ""
        dn = _clean_str(row.get("dividend_bonus_note"))
        if not latest_disc["dividend_bonus_note"] and dn:
            latest_disc["dividend_bonus_note"] = dn
        segs = row.get("segment_notes_json")
        if not latest_disc["segment_notes"] and isinstance(segs, list) and segs:
            latest_disc["segment_notes"] = segs[:6]
        pu = _clean_str(row.get("source_pdf_url"))
        if not latest_disc["source_pdf_url"] and pu:
            latest_disc["source_pdf_url"] = pu

    # TTM & Earnings Quality Summary
    latest_ann = annual_rows[-1] if annual_rows else {}
    prev_ann = annual_rows[-2] if len(annual_rows) >= 2 else {}
    ttm_summary = {
        "latest_annual_period": latest_ann.get("period_label"),
        "latest_quarterly_period": quarterly_rows[-1]["period_label"] if quarterly_rows else None,
        "latest_revenue_mn": latest_ann.get("revenue_mn"),
        "latest_net_profit_mn": latest_ann.get("net_profit_mn"),
        "latest_eps": latest_ann.get("eps"),
        "latest_ocf_mn": latest_ann.get("operating_cashflow_mn"),
        "latest_fcf_mn": latest_ann.get("free_cashflow_mn"),
        "latest_net_margin_pct": latest_ann.get("net_margin_pct"),
        "margin_change_bps": (
            round((latest_ann["net_margin_pct"] - prev_ann["net_margin_pct"]) * 100.0, 0)
            if latest_ann.get("net_margin_pct") is not None and prev_ann.get("net_margin_pct") is not None
            else None
        ),
    }

    # Earnings Quality (Accruals Ratio = (PAT - OCF) / Total Assets)
    accruals_pct = None
    if (
        latest_ann.get("net_profit_mn") is not None
        and latest_ann.get("operating_cashflow_mn") is not None
        and latest_ann.get("total_assets_mn")
        and latest_ann["total_assets_mn"] > 0
    ):
        accruals_pct = round(
            ((latest_ann["net_profit_mn"] - latest_ann["operating_cashflow_mn"]) / latest_ann["total_assets_mn"]) * 100.0,
            2,
        )
    earnings_quality = {
        "cash_conversion_x": latest_ann.get("cash_conversion_x"),
        "sloan_accruals_pct": accruals_pct,
        "quality_verdict": (
            "HIGH_CASH_BACKED"
            if (latest_ann.get("cash_conversion_x") or 0) >= 1.0
            else ("MODERATE" if (latest_ann.get("cash_conversion_x") or 0) >= 0.6 else ("ACCRUAL_HEAVY" if latest_ann.get("cash_conversion_x") is not None else "INSUFFICIENT_DATA"))
        ),
    }

    return {
        "annual_rows": annual_rows,
        "quarterly_rows": quarterly_rows,
        "cagrs": cagrs,
        "ttm_summary": ttm_summary,
        "latest_disclosures": latest_disc,
        "earnings_quality": earnings_quality,
    }


def financial_periods_to_csv(periods: list[dict[str, Any]]) -> str:
    """Export multi-period Annual & Quarterly financial rows to clean CSV via Pandas."""
    if not periods:
        return "period_type,period_label,period_end_date,revenue_mn,net_profit_mn,eps\n"
    cols = [
        "period_type", "period_label", "period_end_date", "fiscal_year",
        "revenue_mn", "cost_of_sales_mn", "gross_profit_mn", "operating_profit_mn",
        "finance_cost_mn", "profit_before_tax_mn", "taxation_mn", "net_profit_mn", "eps",
        "total_assets_mn", "current_assets_mn", "cash_equivalents_mn", "total_debt_mn",
        "total_liabilities_mn", "shareholders_equity_mn",
        "operating_cashflow_mn", "capex_mn", "free_cashflow_mn", "dividends_paid_mn",
        "source_label",
    ]
    df = pd.DataFrame(periods)
    keep = [c for c in cols if c in df.columns]
    buf = io.StringIO()
    df[keep].to_csv(buf, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 2. Investing.com-Style Technical Summary Consensus Gauge (Pandas)
# ---------------------------------------------------------------------------
def _signal_from_counts(buy: int, sell: int, neutral: int) -> tuple[str, float]:
    tot = buy + sell + neutral
    if tot == 0:
        return "NEUTRAL", 0.0
    score = ((buy - sell) / tot) * 100.0
    if score >= 55:
        return "STRONG_BUY", round(score, 1)
    if score >= 15:
        return "BUY", round(score, 1)
    if score <= -55:
        return "STRONG_SELL", round(score, 1)
    if score <= -15:
        return "SELL", round(score, 1)
    return "NEUTRAL", round(score, 1)


def compute_technical_summary_gauge_pandas(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute Investing.com-style Technical Summary Gauge via Pandas:
      - 12 Moving Averages: SMA & EMA (5, 10, 20, 50, 100, 200)
      - 8 Oscillators: RSI(14), MACD(12,26,9), Stochastic %K(14), Williams %R(14),
        CCI(20), ROC(12), Momentum(10), Bollinger %B(20,2)
    """
    if len(bars) < 20:
        return {
            "available": False,
            "overall_signal": "NEUTRAL",
            "gauge_score": 0.0,
            "buy_count": 0,
            "sell_count": 0,
            "neutral_count": 0,
            "ma_summary": {"signal": "NEUTRAL", "buy": 0, "sell": 0, "neutral": 0, "items": []},
            "oscillator_summary": {"signal": "NEUTRAL", "buy": 0, "sell": 0, "neutral": 0, "items": []},
        }

    df = pd.DataFrame(bars)
    df["bar_time"] = pd.to_datetime(df["bar_time"], errors="coerce")
    df = df.dropna(subset=["bar_time"]).sort_values("bar_time").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    if len(df) < 20:
        return {
            "available": False,
            "overall_signal": "NEUTRAL",
            "gauge_score": 0.0,
            "buy_count": 0,
            "sell_count": 0,
            "neutral_count": 0,
            "ma_summary": {"signal": "NEUTRAL", "buy": 0, "sell": 0, "neutral": 0, "items": []},
            "oscillator_summary": {"signal": "NEUTRAL", "buy": 0, "sell": 0, "neutral": 0, "items": []},
        }

    close = df["close"]
    high = df["high"].fillna(close)
    low = df["low"].fillna(close)
    last_px = float(close.iloc[-1])

    # --- 12 Moving Averages ---
    ma_items: list[dict[str, Any]] = []
    ma_buy = ma_sell = ma_neu = 0
    for period in (5, 10, 20, 50, 100, 200):
        if len(close) >= period:
            sma_v = float(close.rolling(period).mean().iloc[-1])
            ema_v = float(close.ewm(span=period, adjust=False).mean().iloc[-1])
            for kind, val in ((f"SMA {period}", sma_v), (f"EMA {period}", ema_v)):
                diff_pct = ((last_px - val) / val) * 100.0 if val > 0 else 0.0
                if diff_pct > 0.15:
                    sig = "BUY"
                    ma_buy += 1
                elif diff_pct < -0.15:
                    sig = "SELL"
                    ma_sell += 1
                else:
                    sig = "NEUTRAL"
                    ma_neu += 1
                ma_items.append({"name": kind, "value": round(val, 2), "signal": sig})

    # --- 8 Oscillators ---
    osc_items: list[dict[str, Any]] = []
    osc_buy = osc_sell = osc_neu = 0

    def _add_osc(name: str, val: float | None, sig: str) -> None:
        nonlocal osc_buy, osc_sell, osc_neu
        if val is None or math.isnan(val) or math.isinf(val):
            return
        if sig == "BUY":
            osc_buy += 1
        elif sig == "SELL":
            osc_sell += 1
        else:
            osc_neu += 1
        osc_items.append({"name": name, "value": round(val, 2), "signal": sig})

    # 1. RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = float((100.0 - (100.0 / (1.0 + rs))).iloc[-1]) if len(close) >= 15 else None
    if rsi14 is not None and not math.isnan(rsi14):
        sig = "BUY" if rsi14 < 35 else ("SELL" if rsi14 > 68 else ("BUY" if rsi14 > 53 else ("SELL" if rsi14 < 47 else "NEUTRAL")))
        _add_osc("RSI (14)", rsi14, sig)

    # 2. MACD (12, 26, 9)
    if len(close) >= 35:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = float((macd_line - sig_line).iloc[-1])
        _add_osc("MACD (12,26)", float(macd_line.iloc[-1]), "BUY" if hist > 0 else ("SELL" if hist < 0 else "NEUTRAL"))

    # 3. Stochastic %K (14)
    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    denom = (high14 - low14).replace(0, np.nan)
    stoch_k = float((((close - low14) / denom) * 100.0).iloc[-1])
    if not math.isnan(stoch_k):
        sig = "BUY" if stoch_k < 25 else ("SELL" if stoch_k > 80 else "NEUTRAL")
        _add_osc("Stoch %K (14)", stoch_k, sig)

    # 4. Williams %R (14)
    will_r = float((((high14 - close) / denom) * -100.0).iloc[-1])
    if not math.isnan(will_r):
        sig = "BUY" if will_r < -80 else ("SELL" if will_r > -20 else "NEUTRAL")
        _add_osc("Williams %R (14)", will_r, sig)

    # 5. CCI (20)
    tp = (high + low + close) / 3.0
    tp_sma = tp.rolling(20).mean()
    tp_mad = tp.rolling(20).apply(lambda x: float(np.mean(np.abs(x - np.mean(x)))), raw=True).replace(0, np.nan)
    cci20 = float(((tp - tp_sma) / (0.015 * tp_mad)).iloc[-1])
    if not math.isnan(cci20):
        sig = "BUY" if cci20 > 100 else ("SELL" if cci20 < -100 else "NEUTRAL")
        _add_osc("CCI (20)", cci20, sig)

    # 6. ROC (12)
    roc12 = float((((close / close.shift(12)) - 1.0) * 100.0).iloc[-1])
    if not math.isnan(roc12):
        _add_osc("ROC (12)", roc12, "BUY" if roc12 > 0.5 else ("SELL" if roc12 < -0.5 else "NEUTRAL"))

    # 7. Momentum (10)
    mom10 = float((close - close.shift(10)).iloc[-1])
    if not math.isnan(mom10):
        _add_osc("Momentum (10)", mom10, "BUY" if mom10 > 0 else ("SELL" if mom10 < 0 else "NEUTRAL"))

    # 8. Bollinger %B (20, 2)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_up = bb_mid + 2.0 * bb_std
    bb_dn = bb_mid - 2.0 * bb_std
    bb_span = (bb_up - bb_dn).replace(0, np.nan)
    bb_pct_b = float(((close - bb_dn) / bb_span).iloc[-1])
    if not math.isnan(bb_pct_b):
        sig = "BUY" if bb_pct_b < 0.2 else ("SELL" if bb_pct_b > 0.9 else ("BUY" if bb_pct_b > 0.55 else "NEUTRAL"))
        _add_osc("Bollinger %B", bb_pct_b, sig)

    ma_sig, _ = _signal_from_counts(ma_buy, ma_sell, ma_neu)
    osc_sig, _ = _signal_from_counts(osc_buy, osc_sell, osc_neu)
    tot_buy = ma_buy + osc_buy
    tot_sell = ma_sell + osc_sell
    tot_neu = ma_neu + osc_neu
    overall_sig, gauge_score = _signal_from_counts(tot_buy, tot_sell, tot_neu)

    return {
        "available": True,
        "overall_signal": overall_sig,
        "gauge_score": gauge_score,
        "buy_count": tot_buy,
        "sell_count": tot_sell,
        "neutral_count": tot_neu,
        "ma_summary": {"signal": ma_sig, "buy": ma_buy, "sell": ma_sell, "neutral": ma_neu, "items": ma_items},
        "oscillator_summary": {"signal": osc_sig, "buy": osc_buy, "sell": osc_sell, "neutral": osc_neu, "items": osc_items},
    }


# ---------------------------------------------------------------------------
# 3. Monthly Seasonality Return Heatmap & Rolling Beta/Vol (Pandas)
# ---------------------------------------------------------------------------
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def compute_seasonality_and_rolling_pandas(
    bars: list[dict[str, Any]],
    benchmark_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Year x Month seasonality return pivot table and rolling 90D vol/beta/Sharpe."""
    if len(bars) < 25:
        return {"years": [], "monthly_avg": [], "rolling_stats": {}}

    df = pd.DataFrame(bars)
    df["dt"] = pd.to_datetime(df["bar_time"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["dt", "close"]).sort_values("dt").drop_duplicates(subset=["dt"], keep="last")
    if len(df) < 25:
        return {"years": [], "monthly_avg": [], "rolling_stats": {}}

    df = df.set_index("dt")
    # Drop timezone cleanly for resampling
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)

    monthly_close = df["close"].resample("ME").last().dropna()
    monthly_ret = monthly_close.pct_change().dropna() * 100.0

    years_payload: list[dict[str, Any]] = []
    if not monthly_ret.empty:
        m_df = pd.DataFrame({"ret": monthly_ret, "year": monthly_ret.index.year, "month": monthly_ret.index.month})
        pivot = m_df.pivot_table(index="year", columns="month", values="ret", aggfunc="last")
        for yr in sorted(pivot.index.tolist())[-6:]:
            months_dict: dict[str, float | None] = {}
            comp = 1.0
            has_any = False
            for m_idx, m_lbl in enumerate(MONTH_LABELS, start=1):
                val = pivot.loc[yr, m_idx] if m_idx in pivot.columns else np.nan
                fv = _r2(val)
                months_dict[m_lbl] = fv
                if fv is not None:
                    comp *= 1.0 + (fv / 100.0)
                    has_any = True
            years_payload.append(
                {
                    "year": int(yr),
                    "months": months_dict,
                    "ytd_pct": round((comp - 1.0) * 100.0, 2) if has_any else None,
                }
            )
        monthly_avg = [
            {
                "month": m_lbl,
                "avg_return_pct": _r2(pivot[m_idx].mean()) if m_idx in pivot.columns else None,
                "win_rate_pct": _r2((pivot[m_idx].dropna() > 0).mean() * 100.0)
                if m_idx in pivot.columns and len(pivot[m_idx].dropna()) > 0
                else None,
            }
            for m_idx, m_lbl in enumerate(MONTH_LABELS, start=1)
        ]
    else:
        monthly_avg = []

    daily_ret = df["close"].pct_change().dropna()
    rolling_90_vol = _r2(float(daily_ret.tail(90).std() * np.sqrt(252) * 100.0)) if len(daily_ret) >= 20 else None
    rolling_252_ret = _r2(float(((df["close"].iloc[-1] / df["close"].iloc[-min(len(df), 252)]) - 1.0) * 100.0))

    beta_90 = None
    corr_90 = None
    if benchmark_bars and len(benchmark_bars) >= 30:
        bdf = pd.DataFrame(benchmark_bars)
        bdf["dt"] = pd.to_datetime(bdf["bar_time"], errors="coerce")
        bdf["bclose"] = pd.to_numeric(bdf["close"], errors="coerce")
        bdf = bdf.dropna(subset=["dt", "bclose"]).sort_values("dt").drop_duplicates(subset=["dt"], keep="last").set_index("dt")
        if bdf.index.tz is not None:
            bdf.index = bdf.index.tz_convert(None)
        joined = pd.DataFrame({"a": df["close"].pct_change(), "b": bdf["bclose"].pct_change()}).dropna().tail(120)
        if len(joined) >= 20 and float(joined["b"].var()) > 1e-10:
            cov_ab = float(joined["a"].cov(joined["b"]))
            var_b = float(joined["b"].var())
            beta_90 = _r2(cov_ab / var_b)
            corr_90 = _r2(float(joined["a"].corr(joined["b"])))

    return {
        "years": years_payload,
        "monthly_avg": monthly_avg,
        "rolling_stats": {
            "vol_90d_ann_pct": rolling_90_vol,
            "return_1y_pct": rolling_252_ret,
            "beta_vs_benchmark": beta_90,
            "corr_vs_benchmark": corr_90,
        },
    }


# ---------------------------------------------------------------------------
# 4. Cross-Sectional PSX Sector Factor Percentile Ranks (Pandas)
# ---------------------------------------------------------------------------
def compute_cross_sectional_factor_ranks_pandas(
    all_stocks: list[dict[str, Any]],
    target_symbol: str,
) -> dict[str, Any] | None:
    """Rank target stock across Value, Quality, Income & Size vs its PSX Sector & Overall PSX."""
    if not all_stocks:
        return None
    df = pd.DataFrame(all_stocks)
    sym_up = target_symbol.upper()
    if sym_up not in set(df["symbol"].astype(str).str.upper()):
        return None
    df["symbol_up"] = df["symbol"].astype(str).str.upper()

    for col in ("pe_ratio", "pb_ratio", "roe_pct", "dividend_yield_pct", "market_cap_mn", "piotroski_f_score"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Sanitize extreme/negative multiples before percentile ranking
    df["pe_clean"] = df["pe_ratio"].where((df["pe_ratio"] > 0) & (df["pe_ratio"] < 120))
    df["pb_clean"] = df["pb_ratio"].where((df["pb_ratio"] > 0) & (df["pb_ratio"] < 40))
    df["roe_clean"] = df["roe_pct"].where((df["roe_pct"] > -80) & (df["roe_pct"] < 200))
    df["dy_clean"] = df["dividend_yield_pct"].where((df["dividend_yield_pct"] >= 0) & (df["dividend_yield_pct"] < 45))

    # Market-wide percentiles (0-100, higher is better)
    pe_pct = (1.0 - df["pe_clean"].rank(pct=True)) * 100.0
    pb_pct = (1.0 - df["pb_clean"].rank(pct=True)) * 100.0
    df["value_pct"] = pd.concat([pe_pct, pb_pct], axis=1).mean(axis=1)

    roe_pct_r = df["roe_clean"].rank(pct=True) * 100.0
    f_pct_r = df["piotroski_f_score"].rank(pct=True) * 100.0
    df["quality_pct"] = pd.concat([roe_pct_r, f_pct_r], axis=1).mean(axis=1)

    df["income_pct"] = df["dy_clean"].rank(pct=True) * 100.0
    df["size_pct"] = df["market_cap_mn"].where(df["market_cap_mn"] > 0).rank(pct=True) * 100.0
    df["composite_score"] = (
        df["value_pct"].fillna(50.0) * 0.35
        + df["quality_pct"].fillna(50.0) * 0.35
        + df["income_pct"].fillna(50.0) * 0.20
        + df["size_pct"].fillna(50.0) * 0.10
    )

    row = df[df["symbol_up"] == sym_up].iloc[0]
    sector = str(row.get("sector") or "")
    sec_df = df[df["sector"] == sector].sort_values("composite_score", ascending=False).reset_index(drop=True)
    sec_rank = int(sec_df.index[sec_df["symbol_up"] == sym_up][0]) + 1 if not sec_df.empty else 1

    return {
        "symbol": sym_up,
        "sector": sector,
        "sector_rank": sec_rank,
        "sector_total": int(len(sec_df)),
        "market_total": int(len(df)),
        "value_percentile": _r2(row["value_pct"]),
        "quality_percentile": _r2(row["quality_pct"]),
        "income_percentile": _r2(row["income_pct"]),
        "size_percentile": _r2(row["size_pct"]),
        "composite_score": _r2(row["composite_score"]),
    }


# ---------------------------------------------------------------------------
# 5. Sarmaya.pk-Style PSX Market Treemap Heatmap & Market Breadth (Pandas)
# ---------------------------------------------------------------------------
def compute_market_heatmap_pandas(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute PSX Market Breadth + Market-Cap-Weighted Sector Treemap cells via Pandas."""
    if not stocks:
        return {"breadth": {}, "sectors": []}

    df = pd.DataFrame(stocks)
    for col in ("last_price", "change_pct", "volume", "market_cap_mn"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    # Guard against unscaled PSX '000 market caps (> Rs 2.5 Trillion for a single PSX equity)
    df["market_cap_mn"] = np.where(df["market_cap_mn"] > 2_500_000.0, df["market_cap_mn"] / 1000.0, df["market_cap_mn"])
    df["sector"] = df["sector"].fillna("Other").astype(str).str.strip().replace("", "Other")
    df["weight"] = np.where(df["market_cap_mn"] > 0, df["market_cap_mn"], np.maximum(df["last_price"] * df["volume"] / 1_000_000.0, 10.0))

    adv = int((df["change_pct"] > 0.01).sum())
    dec = int((df["change_pct"] < -0.01).sum())
    unch = int(len(df) - adv - dec)
    tot_mcap_bn = round(float(df["market_cap_mn"].sum()) / 1000.0, 1)
    tot_vol = int(df["volume"].sum())

    sectors_out: list[dict[str, Any]] = []
    for sec_name, grp in df.groupby("sector"):
        w_sum = float(grp["weight"].sum())
        mcap_sum = float(grp["market_cap_mn"].sum())
        w_chg = float((grp["change_pct"] * grp["weight"]).sum() / w_sum) if w_sum > 0 else float(grp["change_pct"].mean())
        top_stocks = grp.sort_values("weight", ascending=False).head(8)
        children = [
            {
                "symbol": str(r["symbol"]),
                "name": str(r["name"]),
                "sector": str(sec_name),
                "last_price": round(float(r["last_price"]), 2),
                "change_pct": round(float(r["change_pct"]), 2),
                "market_cap_mn": round(float(r["market_cap_mn"]), 1),
                "size": round(max(float(r["weight"]), 50.0), 1),
                "volume": int(r["volume"]),
                "is_shariah_compliant": bool(r.get("is_shariah_compliant")),
            }
            for _, r in top_stocks.iterrows()
        ]
        sectors_out.append(
            {
                "sector": str(sec_name),
                "market_cap_mn": round(mcap_sum, 1),
                "weight_sum": round(w_sum, 1),
                "change_pct": round(w_chg, 2),
                "stock_count": int(len(grp)),
                "advancers": int((grp["change_pct"] > 0.01).sum()),
                "decliners": int((grp["change_pct"] < -0.01).sum()),
                "children": children,
            }
        )

    sectors_out.sort(key=lambda x: x["weight_sum"], reverse=True)
    return {
        "breadth": {
            "total": int(len(df)),
            "advancers": adv,
            "decliners": dec,
            "unchanged": unch,
            "advance_decline_ratio": round(adv / max(dec, 1), 2),
            "total_market_cap_bn": tot_mcap_bn,
            "total_volume": tot_vol,
        },
        "sectors": sectors_out[:14],
    }
