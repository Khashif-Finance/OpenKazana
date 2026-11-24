from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import ta

from app.services.total_return import build_adjusted_bars

TRADING_DAYS_PER_YEAR = 252.0


def run_strategy_backtest(
    legs_data: list[dict[str, Any]],
    *,
    strategy: str = "BUY_AND_HOLD",
    benchmark_bars: list[dict[str, Any]] | None = None,
    benchmark_symbol: str = "KSE100",
    initial_capital_pkr: float = 1_000_000.0,
    monthly_contribution_pkr: float = 0.0,
    return_mode: str = "NET_FILER",
    real_return_mode: str = "NOMINAL_PKR",
    apply_shariah_purification: bool = False,
    broker_bps: int = 15,
    risk_free_rate_pct: float = 13.0,
    usd_to_pkr: float = 278.0,
    usd_pkr_bars: list[dict[str, Any]] | None = None,
    cpi_annual_pct: float = 8.0,
) -> dict[str, Any]:
    """
    Backtest a multi-asset portfolio or single-asset tactical strategy using dividend/bonus/daily-payout
    adjusted Total Return series with Pakistan brokerage/load & FBR tax drag support.
    """
    if not legs_data:
        return {"error": "At least one asset leg is required.", "equity_curve": [], "summary": {}}

    norm_weights = np.array([max(float(leg["weight_pct"]), 0.0) for leg in legs_data], dtype=np.float64)
    w_sum = float(np.sum(norm_weights))
    if w_sum <= 0:
        norm_weights = np.ones(len(legs_data), dtype=np.float64) / len(legs_data)
    else:
        norm_weights = norm_weights / w_sum

    series_map: dict[str, pd.Series] = {}
    fee_rate_by_sym: dict[str, float] = {}
    for leg in legs_data:
        sym = str(leg["symbol"]).upper()
        if leg.get("adj_bars"):
            adj = list(leg["adj_bars"])
        else:
            bars = leg.get("bars") or []
            if len(bars) < 2:
                continue
            adj = build_adjusted_bars(
                bars,
                leg.get("corporate_actions") or [],
                mode=return_mode,
                is_daily_dividend=bool(leg.get("is_daily_dividend", False)),
                annualized_yield_pct=float(leg.get("annualized_yield_pct") or 0.0),
                is_intl=str(leg.get("asset_class") or "").startswith("INTL"),
                country=str(leg.get("country") or "PK"),
                us_w8ben_active=bool(leg.get("us_w8ben_active", True)),
                purification_rate_pct=float(leg.get("purification_rate_pct") or 0.0),
                apply_shariah_purification=apply_shariah_purification,
            )
        if len(adj) < 2:
            continue
        ccy_mult = usd_to_pkr if str(leg.get("currency") or "PKR").upper() == "USD" else 1.0
        s = pd.Series(
            {pd.to_datetime(str(b["bar_time"])[:10]): float(b["close"]) * ccy_mult for b in adj if float(b["close"]) > 0},
            name=sym,
        ).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if len(s) >= 2:
            series_map[sym] = s
            fel_pct = float(leg.get("front_end_load_pct") or 0.0)
            fee_rate_by_sym[sym] = (fel_pct / 100.0) if fel_pct > 0 else (broker_bps / 10000.0) * 1.13

    if not series_map:
        return {
            "error": "Insufficient historical bars for selected assets.",
            "equity_curve": [],
            "summary": {},
        }

    px_df = pd.concat(list(series_map.values()), axis=1).sort_index().ffill().dropna()
    if len(px_df) < 3:
        return {
            "error": "Overlapping date range across selected assets has fewer than 3 sessions.",
            "equity_curve": [],
            "summary": {},
        }

    # Re-align weights strictly to surviving symbols
    active_syms = list(px_df.columns)
    active_w = np.array(
        [next(float(leg["weight_pct"]) for leg in legs_data if str(leg["symbol"]).upper() == s) for s in active_syms],
        dtype=np.float64,
    )
    active_w = active_w / max(float(np.sum(active_w)), 1e-9)

    bench_series: pd.Series | None = None
    if benchmark_bars:
        bs = pd.Series(
            {pd.to_datetime(str(b["bar_time"])[:10]): float(b["close"]) for b in benchmark_bars if float(b["close"]) > 0},
            name=benchmark_symbol,
        ).sort_index()
        bs = bs[~bs.index.duplicated(keep="last")]
        if len(bs) >= 2:
            bench_series = bs.reindex(px_df.index).ffill().bfill()

    # Precompute tactical indicator masks per leg if needed
    strat = strategy.upper()
    in_market_mask = pd.DataFrame(True, index=px_df.index, columns=active_syms)
    if strat == "SMA_CROSSOVER":
        for sym in active_syms:
            col = px_df[sym]
            sma_fast = col.rolling(min(20, max(3, len(col) // 4))).mean()
            sma_slow = col.rolling(min(50, max(5, len(col) // 2))).mean()
            in_market_mask[sym] = (sma_fast >= sma_slow).fillna(True)
    elif strat == "RSI_MEAN_REVERSION":
        for sym in active_syms:
            col = px_df[sym]
            if len(col) >= 15:
                rsi_s = ta.momentum.RSIIndicator(close=col, window=14).rsi().fillna(50.0)
                active_flag = True
                flags: list[bool] = []
                for r_val in rsi_s.to_numpy():
                    if r_val <= 35.0:
                        active_flag = True
                    elif r_val >= 75.0:
                        active_flag = False
                    flags.append(active_flag)
                in_market_mask[sym] = flags

    dates = list(px_df.index)
    n_days = len(dates)
    rf_daily = (risk_free_rate_pct / 100.0) / 365.0

    # Per-leg dollar allocations & T-bill sleeve (for tactical out-of-market moves)
    leg_values = np.zeros(len(active_syms), dtype=np.float64)
    cash_sleeves = np.zeros(len(active_syms), dtype=np.float64)
    avg_entry_fee = sum(fee_rate_by_sym.get(s, 0.0017) * active_w[i] for i, s in enumerate(active_syms))
    total_fees_pkr = initial_capital_pkr * avg_entry_fee if return_mode != "PRICE" else 0.0
    net_start = max(0.0, initial_capital_pkr - total_fees_pkr)

    for i, sym in enumerate(active_syms):
        if bool(in_market_mask[sym].iloc[0]):
            leg_values[i] = net_start * active_w[i]
        else:
            cash_sleeves[i] = net_start * active_w[i]

    invested_total = initial_capital_pkr
    bench_units = (
        (initial_capital_pkr / float(bench_series.iloc[0]))
        if bench_series is not None and float(bench_series.iloc[0]) > 0
        else 0.0
    )
    trade_count = len(active_syms)
    prev_month = dates[0].month

    equity_points: list[dict[str, Any]] = []
    strat_vals: list[float] = []
    bench_vals: list[float] = []
    daily_strat_rets: list[float] = []

    for d_idx in range(n_days):
        dt = dates[d_idx]
        if d_idx > 0:
            gap_days = max(1, (dt - dates[d_idx - 1]).days)
            prev_total = float(np.sum(leg_values) + np.sum(cash_sleeves))

            for i, sym in enumerate(active_syms):
                px_prev = float(px_df[sym].iloc[d_idx - 1])
                px_curr = float(px_df[sym].iloc[d_idx])
                if px_prev > 0 and leg_values[i] > 0:
                    leg_values[i] *= px_curr / px_prev
                if cash_sleeves[i] > 0:
                    cash_sleeves[i] *= (1.0 + rf_daily) ** gap_days

                # Tactical entry/exit check
                if strat in {"SMA_CROSSOVER", "RSI_MEAN_REVERSION"}:
                    want_in = bool(in_market_mask[sym].iloc[d_idx])
                    fee_r = fee_rate_by_sym.get(sym, 0.0017) if return_mode != "PRICE" else 0.0
                    if want_in and cash_sleeves[i] > 1.0 and leg_values[i] <= 1.0:
                        fee = cash_sleeves[i] * fee_r
                        total_fees_pkr += fee
                        leg_values[i] += max(0.0, cash_sleeves[i] - fee)
                        cash_sleeves[i] = 0.0
                        trade_count += 1
                    elif not want_in and leg_values[i] > 1.0:
                        fee = leg_values[i] * fee_r
                        total_fees_pkr += fee
                        cash_sleeves[i] += max(0.0, leg_values[i] - fee)
                        leg_values[i] = 0.0
                        trade_count += 1

            # Benchmark unit growth before same-day contribution
            post_move_total = float(np.sum(leg_values) + np.sum(cash_sleeves))
            if prev_total > 0:
                daily_strat_rets.append((post_move_total / prev_total) - 1.0)

            # Monthly SIP contribution + optional Rebalance on month turn
            if dt.month != prev_month:
                prev_month = dt.month
                if monthly_contribution_pkr > 0:
                    invested_total += monthly_contribution_pkr
                    fee = monthly_contribution_pkr * avg_entry_fee if return_mode != "PRICE" else 0.0
                    total_fees_pkr += fee
                    net_add = max(0.0, monthly_contribution_pkr - fee)
                    for i, sym in enumerate(active_syms):
                        if bool(in_market_mask[sym].iloc[d_idx]):
                            leg_values[i] += net_add * active_w[i]
                        else:
                            cash_sleeves[i] += net_add * active_w[i]
                    if bench_series is not None and float(bench_series.iloc[d_idx]) > 0:
                        bench_units += monthly_contribution_pkr / float(bench_series.iloc[d_idx])
                    trade_count += 1

                if strat == "MONTHLY_SIP_REBALANCE":
                    pool = float(np.sum(leg_values) + np.sum(cash_sleeves))
                    for i, sym in enumerate(active_syms):
                        target_v = pool * active_w[i]
                        turnover = abs(target_v - leg_values[i])
                        if turnover > pool * 0.01:
                            fee = turnover * (fee_rate_by_sym.get(sym, 0.0017) if return_mode != "PRICE" else 0.0)
                            total_fees_pkr += fee
                            leg_values[i] = max(0.0, target_v - fee)
                            cash_sleeves[i] = 0.0
                            trade_count += 1

        total_now = float(np.sum(leg_values) + np.sum(cash_sleeves))
        bench_now = (
            float(bench_units * float(bench_series.iloc[d_idx]))
            if bench_series is not None
            else total_now
        )
        strat_vals.append(total_now)
        bench_vals.append(bench_now)

        equity_points.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "strategy_value_pkr": round(total_now, 2),
                "benchmark_value_pkr": round(bench_now, 2),
                "invested_capital_pkr": round(invested_total, 2),
                "strategy_return_pct": round(((total_now / max(invested_total, 1.0)) - 1.0) * 100.0, 2),
                "benchmark_return_pct": round(((bench_now / max(invested_total, 1.0)) - 1.0) * 100.0, 2),
            }
        )

    # Optional Real-Return deflator: REAL_USD (historical USD/PKR) or REAL_CPI_PKR (Pakistan CPI inflation)
    real_mode = (real_return_mode or "NOMINAL_PKR").upper()
    fx_series: pd.Series | None = None
    if usd_pkr_bars:
        raw_fx = pd.Series(
            {pd.to_datetime(str(b["bar_time"])[:10]): float(b["close"]) for b in usd_pkr_bars if float(b["close"]) > 0}
        ).sort_index()
        raw_fx = raw_fx[~raw_fx.index.duplicated(keep="last")]
        if not raw_fx.empty:
            fx_series = raw_fx.reindex(px_df.index, method="ffill").bfill()

    display_currency = "USD" if real_mode == "REAL_USD" else "PKR"
    if real_mode in {"REAL_USD", "REAL_CPI_PKR"} and equity_points:
        start_dt = dates[0]
        base_fx = float(fx_series.iloc[0]) if fx_series is not None and float(fx_series.iloc[0]) > 0 else usd_to_pkr
        adj_strat_vals: list[float] = []
        adj_bench_vals: list[float] = []
        adj_daily_rets: list[float] = []
        for idx_i, (dt, pt) in enumerate(zip(dates, equity_points, strict=False)):
            if real_mode == "REAL_USD":
                cur_fx = float(fx_series.iloc[idx_i]) if fx_series is not None and float(fx_series.iloc[idx_i]) > 0 else usd_to_pkr
                s_val = float(strat_vals[idx_i]) / max(cur_fx, 1.0)
                b_val = float(bench_vals[idx_i]) / max(cur_fx, 1.0)
                inv_val = float(pt["invested_capital_pkr"]) / max(base_fx, 1.0)
            else:
                yrs_elapsed = max(0.0, (dt - start_dt).days / 365.25)
                deflator = (1.0 + max(0.0, float(cpi_annual_pct)) / 100.0) ** yrs_elapsed
                s_val = float(strat_vals[idx_i]) / deflator
                b_val = float(bench_vals[idx_i]) / deflator
                inv_val = float(pt["invested_capital_pkr"])
            if idx_i > 0 and adj_strat_vals[-1] > 0:
                adj_daily_rets.append((s_val / adj_strat_vals[-1]) - 1.0)
            adj_strat_vals.append(s_val)
            adj_bench_vals.append(b_val)
            pt["strategy_value_pkr"] = round(s_val, 2)
            pt["benchmark_value_pkr"] = round(b_val, 2)
            pt["invested_capital_pkr"] = round(inv_val, 2)
            pt["strategy_return_pct"] = round(((s_val / max(inv_val, 1.0)) - 1.0) * 100.0, 2)
            pt["benchmark_return_pct"] = round(((b_val / max(inv_val, 1.0)) - 1.0) * 100.0, 2)
        strat_vals = adj_strat_vals
        bench_vals = adj_bench_vals
        if adj_daily_rets:
            daily_strat_rets = adj_daily_rets
        invested_total = float(equity_points[-1]["invested_capital_pkr"])

    # Underwater drawdown curve
    vals_arr = np.array(strat_vals, dtype=np.float64)
    peaks = np.maximum.accumulate(vals_arr)
    dd_arr = ((vals_arr - peaks) / np.maximum(peaks, 1e-6)) * 100.0
    for idx, pt in enumerate(equity_points):
        pt["drawdown_pct"] = round(float(dd_arr[idx]), 2)

    rets_arr = np.array(daily_strat_rets, dtype=np.float64) if daily_strat_rets else np.array([0.0])
    mean_d = float(np.mean(rets_arr))
    std_d = float(np.std(rets_arr, ddof=1)) if len(rets_arr) > 1 else 0.0
    eff_rf = 4.5 if real_mode == "REAL_USD" else (max(0.0, risk_free_rate_pct - cpi_annual_pct) if real_mode == "REAL_CPI_PKR" else risk_free_rate_pct)
    rf_trading = (eff_rf / 100.0) / TRADING_DAYS_PER_YEAR
    ann_vol = std_d * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
    sharpe = float(((mean_d - rf_trading) / std_d) * math.sqrt(TRADING_DAYS_PER_YEAR)) if std_d > 1e-8 else 0.0
    downside = rets_arr[rets_arr < rf_trading] - rf_trading
    down_std = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) > 0 else 0.0
    sortino = float(((mean_d - rf_trading) / down_std) * math.sqrt(TRADING_DAYS_PER_YEAR)) if down_std > 1e-8 else 0.0

    total_days = max(1, (dates[-1] - dates[0]).days)
    final_val = strat_vals[-1]
    final_bench = bench_vals[-1]
    total_ret_pct = ((final_val / max(invested_total, 1.0)) - 1.0) * 100.0
    bench_ret_pct = ((final_bench / max(invested_total, 1.0)) - 1.0) * 100.0
    cagr_pct = (((final_val / max(invested_total, 1.0)) ** (365.0 / max(total_days, 30))) - 1.0) * 100.0
    bench_cagr = (((final_bench / max(invested_total, 1.0)) ** (365.0 / max(total_days, 30))) - 1.0) * 100.0

    return {
        "strategy": strategy,
        "return_mode": return_mode,
        "real_return_mode": real_mode,
        "display_currency": display_currency,
        "benchmark_symbol": benchmark_symbol,
        "symbols_used": active_syms,
        "equity_curve": equity_points[-260:],  # Keep chart responsive
        "summary": {
            "final_value_pkr": round(final_val, 2),
            "total_invested_pkr": round(invested_total, 2),
            "display_currency": display_currency,
            "real_return_mode": real_mode,
            "total_return_pct": round(total_ret_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "benchmark_final_value_pkr": round(final_bench, 2),
            "benchmark_return_pct": round(bench_ret_pct, 2),
            "benchmark_cagr_pct": round(bench_cagr, 2),
            "excess_return_pct": round(total_ret_pct - bench_ret_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(float(np.min(dd_arr)), 2),
            "annualized_volatility_pct": round(ann_vol, 2),
            "win_rate_pct": round(float(np.mean(rets_arr > 0) * 100.0), 2),
            "estimated_fees_pkr": round(total_fees_pkr, 2),
            "trade_count": trade_count,
            "days_analyzed": total_days,
        },
    }


def compute_monte_carlo_and_stress(
    holdings: list[dict[str, Any]],
    *,
    portfolio_value_pkr: float,
    ann_return_pct: float = 16.0,
    ann_vol_pct: float = 18.0,
    horizon_years: int = 3,
    simulations: int = 500,
    beta: float | None = None,
    sbp_policy_rate_pct: float = 13.0,
) -> dict[str, Any]:
    """
    Generate P10 / P25 / P50 / P75 / P90 geometric Brownian motion Monte Carlo wealth fans
    plus Pakistan-specific Macro Stress Test scenarios (SBP rate shocks, USD/PKR move, PSX drawdown).
    """
    v0 = max(float(portfolio_value_pkr), 1000.0)
    mu = float(ann_return_pct) / 100.0
    sigma = max(float(ann_vol_pct) / 100.0, 0.04)
    months = max(6, min(60, horizon_years * 12))
    dt = 1.0 / 12.0

    rng = np.random.default_rng(seed=42)
    z = rng.standard_normal((simulations, months))
    drift = (mu - 0.5 * sigma * sigma) * dt
    diffusion = sigma * math.sqrt(dt) * z
    log_paths = np.cumsum(drift + diffusion, axis=1)
    wealth_paths = v0 * np.exp(log_paths)

    mc_points: list[dict[str, Any]] = [
        {
            "month": 0,
            "p10_pkr": round(v0, 0),
            "p25_pkr": round(v0, 0),
            "p50_pkr": round(v0, 0),
            "p75_pkr": round(v0, 0),
            "p90_pkr": round(v0, 0),
        }
    ]
    for m_idx in range(months):
        col = wealth_paths[:, m_idx]
        mc_points.append(
            {
                "month": m_idx + 1,
                "p10_pkr": round(float(np.percentile(col, 10)), 0),
                "p25_pkr": round(float(np.percentile(col, 25)), 0),
                "p50_pkr": round(float(np.percentile(col, 50)), 0),
                "p75_pkr": round(float(np.percentile(col, 75)), 0),
                "p90_pkr": round(float(np.percentile(col, 90)), 0),
            }
        )

    # Exposure breakdown across holdings for macro stress-test sensitivity
    psx_eq_mv = 0.0
    intl_usd_mv = 0.0
    fixed_cash_mv = 0.0
    gold_mv = 0.0
    crypto_mv = 0.0
    for h in holdings:
        mv = float(h.get("market_value_pkr") or 0.0)
        if mv <= 0:
            continue
        cls = str(h.get("asset_class") or "")
        ccy = str(h.get("currency") or "PKR").upper()
        if cls in {"PSX_EQUITY", "PSX_ETF", "PSX_INDEX"}:
            psx_eq_mv += mv
        elif cls in {"INTL_EQUITY", "INTL_ETF", "INTL_INDEX"} or ccy == "USD":
            intl_usd_mv += mv
        elif cls in {"GOLD"}:
            gold_mv += mv
        elif cls in {"CRYPTO"}:
            crypto_mv += mv
        else:
            fixed_cash_mv += mv

    port_beta = float(beta) if beta is not None else 0.95
    scenarios_raw = [
        (
            "KSE-100 -20% Correction",
            "Broad domestic equity drawdown weighted by portfolio Beta",
            (-0.20 * port_beta * psx_eq_mv) + (-0.06 * intl_usd_mv) + (0.015 * fixed_cash_mv) - (0.15 * crypto_mv),
        ),
        (
            "USD/PKR +15% Depreciation",
            "PKR weakens 15% vs USD (helps US equities, USD cash & local gold; pressures imported inputs)",
            (-0.04 * psx_eq_mv) + (0.15 * intl_usd_mv) + (0.14 * gold_mv) + (0.15 * crypto_mv),
        ),
        (
            f"SBP Policy Rate +200 bps (to {sbp_policy_rate_pct + 2:.1f}%)",
            "Monetary tightening boosts Daily Dividend & T-Bill income but compresses equity P/E multiples",
            (-0.09 * psx_eq_mv) + (0.02 * fixed_cash_mv),
        ),
        (
            f"SBP Monetary Easing -300 bps (to {max(4.0, sbp_policy_rate_pct - 3):.1f}%)",
            "Rate cuts trigger multiple re-rating in leveraged/cyclical PSX sectors & lower money market yields",
            (0.16 * psx_eq_mv) + (-0.015 * fixed_cash_mv),
        ),
        (
            "US Tech -15% Pullback",
            "Global risk-off tech drawdown across NASDAQ/S&P & crypto holdings",
            (-0.04 * psx_eq_mv) + (-0.15 * intl_usd_mv) + (-0.25 * crypto_mv) + (0.04 * gold_mv),
        ),
    ]

    stress_tests = []
    for title, rationale, impact_pkr in scenarios_raw:
        stressed_val = max(0.0, v0 + impact_pkr)
        impact_pct = (impact_pkr / v0) * 100.0
        stress_tests.append(
            {
                "scenario": title,
                "rationale": rationale,
                "estimated_impact_pkr": round(impact_pkr, 2),
                "estimated_impact_pct": round(impact_pct, 2),
                "projected_value_pkr": round(stressed_val, 2),
            }
        )

    return {
        "monte_carlo": mc_points,
        "stress_tests": stress_tests,
        "exposures_pkr": {
            "psx_equity": round(psx_eq_mv, 2),
            "intl_usd": round(intl_usd_mv, 2),
            "fixed_income_and_cash": round(fixed_cash_mv, 2),
            "gold": round(gold_mv, 2),
            "crypto": round(crypto_mv, 2),
        },
    }
