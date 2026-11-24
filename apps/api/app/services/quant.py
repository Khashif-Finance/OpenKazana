from __future__ import annotations

import math
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize, stats
import ta

D = Decimal
TRADING_DAYS_PER_YEAR = 252.0


def _r2(val: float | Decimal | None) -> float | None:
    if val is None:
        return None
    f = float(val)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 2)


def _r4(val: float | Decimal | None) -> float | None:
    if val is None:
        return None
    f = float(val)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 4)


def compute_xirr_scipy(cashflows: list[tuple[date, float]], guess: float = 0.15) -> Decimal | None:
    """
    Compute annualized Money-Weighted IRR (XIRR) using `numpy` + `scipy.optimize`
    (Newton-Raphson with Brent's root-finding method `scipy.optimize.brentq` fallback).
    """
    if len(cashflows) < 2:
        return None
    amounts = np.array([float(amt) for _, amt in cashflows], dtype=np.float64)
    if not (np.any(amounts > 0) and np.any(amounts < 0)):
        return None

    d0 = min(d for d, _ in cashflows)
    years = np.array([(d - d0).days / 365.0 for d, _ in cashflows], dtype=np.float64)
    if np.max(years) < (1.0 / 365.0):
        return None

    def npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 1e-9:
            return 1e18
        return float(np.sum(amounts / np.power(base, years)))

    def d_npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 1e-9:
            return 0.0
        mask = years > 0
        if not np.any(mask):
            return 0.0
        return float(-np.sum(years[mask] * amounts[mask] / np.power(base, years[mask] + 1.0)))

    try:
        root = float(optimize.newton(npv, x0=guess, fprime=d_npv, tol=1e-7, maxiter=80))
        if -0.99 < root < 50.0 and abs(npv(root)) < 1e-3:
            return Decimal(str(root * 100.0)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        pass

    try:
        root = float(optimize.brentq(npv, -0.95, 10.0, xtol=1e-7, maxiter=120))
        return Decimal(str(root * 100.0)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _ema_np(arr: np.ndarray, span: int) -> np.ndarray:
    """Fast pure-NumPy exponential moving average (adjust=False)."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = arr[0]
    one_m = 1.0 - alpha
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + one_m * out[i - 1]
    return out


def compute_screener_technicals_fast(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Ultra-fast pure-NumPy technical scanner helper (~0.25 ms/stock vs ~50 ms in `ta`).
    Computes exact RSI(14), SMA 20/50/200, Golden Cross, MACD Hist, ADX(14), Supertrend(10,3),
    trailing 1M/3M/1Y returns, and overall BULLISH/BEARISH/NEUTRAL consensus for `/screener/scan`.
    """
    if not bars or len(bars) < 5:
        return {"overall_signal": "NEUTRAL", "latest": {}}

    n_raw = len(bars)
    close_raw = np.fromiter((float(b["close"]) for b in bars), dtype=np.float64, count=n_raw)
    mask = close_raw > 0
    if not np.all(mask):
        bars = [b for b, keep in zip(bars, mask, strict=True) if keep]
        close = close_raw[mask]
    else:
        close = close_raw
    n = len(close)
    if n < 5:
        return {"overall_signal": "NEUTRAL", "latest": {}}

    high = np.fromiter((float(b["high"]) for b in bars), dtype=np.float64, count=n)
    low = np.fromiter((float(b["low"]) for b in bars), dtype=np.float64, count=n)
    last_close = float(close[-1])

    # SMAs
    sma20 = float(np.mean(close[-min(20, n) :]))
    sma50 = float(np.mean(close[-min(50, n) :]))
    sma200 = float(np.mean(close[-min(200, n) :])) if n >= 60 else None
    golden_cross = bool(sma200 is not None and sma50 > sma200)

    # Wilder RSI(14)
    rsi_win = min(14, max(2, n - 1))
    deltas = np.diff(close)
    gains = np.where(deltas > 0.0, deltas, 0.0)
    losses = np.where(deltas < 0.0, -deltas, 0.0)
    avg_g = float(np.mean(gains[:rsi_win]))
    avg_l = float(np.mean(losses[:rsi_win]))
    for i in range(rsi_win, len(deltas)):
        avg_g = (avg_g * (rsi_win - 1) + gains[i]) / rsi_win
        avg_l = (avg_l * (rsi_win - 1) + losses[i]) / rsi_win
    if avg_l <= 1e-12:
        rsi_val = 100.0 if avg_g > 0 else 50.0
    else:
        rs = avg_g / avg_l
        rsi_val = 100.0 - (100.0 / (1.0 + rs))
    rsi_val = round(rsi_val, 2)

    # MACD(12, 26, 9) histogram
    macd_hist_val: float | None = None
    if n >= 26:
        ema12 = _ema_np(close, 12)
        ema26 = _ema_np(close, 26)
        macd_line = ema12 - ema26
        sig_line = _ema_np(macd_line, 9)
        macd_hist_val = _r4(float(macd_line[-1] - sig_line[-1]))

    # True Range (used by both Supertrend and ADX)
    prev_close = close[:-1]
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )

    # Supertrend(10, 3)
    supertrend_sig: str | None = None
    if n >= 12:
        atr_w = min(10, n - 1)
        atr10 = np.zeros(n, dtype=np.float64)
        atr10[atr_w - 1] = float(np.mean(tr[:atr_w]))
        for i in range(atr_w, n):
            atr10[i] = (atr10[i - 1] * (atr_w - 1) + tr[i]) / atr_w
        hl2 = (high + low) * 0.5
        upper = hl2 + 3.0 * atr10
        lower = hl2 - 3.0 * atr10
        final_up = upper.copy()
        final_lo = lower.copy()
        st_dir = 1
        for i in range(1, n):
            if not (upper[i] < final_up[i - 1] or close[i - 1] > final_up[i - 1]):
                final_up[i] = final_up[i - 1]
            if not (lower[i] > final_lo[i - 1] or close[i - 1] < final_lo[i - 1]):
                final_lo[i] = final_lo[i - 1]
            if st_dir == 1:
                if close[i] < final_lo[i]:
                    st_dir = -1
            else:
                if close[i] > final_up[i]:
                    st_dir = 1
        supertrend_sig = "BULL" if st_dir == 1 else "BEAR"

    # Wilder ADX(14)
    adx_val: float | None = None
    if n >= 28:
        w = 14
        up_move = high[1:] - high[:-1]
        dn_move = low[:-1] - low[1:]
        pdm = np.where((up_move > dn_move) & (up_move > 0.0), up_move, 0.0)
        ndm = np.where((dn_move > up_move) & (dn_move > 0.0), dn_move, 0.0)
        tr_s = float(np.sum(tr[1 : w + 1]))
        p_s = float(np.sum(pdm[:w]))
        n_s = float(np.sum(ndm[:w]))
        dx_vals: list[float] = []
        for i in range(w, len(pdm)):
            if i > w:
                tr_s = tr_s - (tr_s / w) + tr[i + 1]
                p_s = p_s - (p_s / w) + pdm[i]
                n_s = n_s - (n_s / w) + ndm[i]
            if tr_s > 1e-12:
                pdi = 100.0 * p_s / tr_s
                ndi = 100.0 * n_s / tr_s
                denom = pdi + ndi
                dx_vals.append(100.0 * abs(pdi - ndi) / denom if denom > 1e-12 else 0.0)
        if dx_vals:
            adx_v = float(np.mean(dx_vals[:w])) if len(dx_vals) >= w else float(np.mean(dx_vals))
            for d in dx_vals[w:]:
                adx_v = (adx_v * (w - 1) + d) / w
            adx_val = round(adx_v, 2)

    def _ret(sessions: int) -> float | None:
        if n <= sessions:
            return None
        prev_p = float(close[-(sessions + 1)])
        return round(((last_close / prev_p) - 1.0) * 100.0, 2) if prev_p > 0 else None

    bull = 0
    bear = 0
    if rsi_val < 30:
        bull += 2
    elif rsi_val > 70:
        bear += 2
    elif rsi_val >= 52:
        bull += 1
    if last_close > sma20 and sma20 >= sma50:
        bull += 2
    elif last_close < sma20 and sma20 < sma50:
        bear += 2
    if macd_hist_val is not None:
        if macd_hist_val > 0:
            bull += 1
        elif macd_hist_val < 0:
            bear += 1
    if supertrend_sig == "BULL":
        bull += 1
    elif supertrend_sig == "BEAR":
        bear += 1
    if golden_cross:
        bull += 1

    overall = "BULLISH" if bull >= bear + 2 else ("BEARISH" if bear >= bull + 2 else "NEUTRAL")
    return {
        "overall_signal": overall,
        "latest": {
            "rsi_14": rsi_val,
            "sma_20": round(sma20, 4),
            "sma_50": round(sma50, 4),
            "sma_200": round(sma200, 4) if sma200 is not None else None,
            "golden_cross": golden_cross,
            "macd_hist": macd_hist_val,
            "adx_14": adx_val,
            "supertrend_signal": supertrend_sig,
            "return_1m_pct": _ret(21),
            "return_3m_pct": _ret(63),
            "return_1y_pct": _ret(min(251, n - 1)) if n >= 120 else None,
        },
    }


def compute_technical_indicators(
    bars: list[dict[str, Any]],
    symbol: str | None = None,
    current_price: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """
    Compute institutional technical analysis indicators (`RSI`, `MACD`, `SMA 20/50`, `EMA 20`,
    `Bollinger Bands`, `ATR`) using the `ta` and `pandas` libraries.
    """
    if not bars or len(bars) < 5:
        return {
            "overall_signal": "NEUTRAL",
            "signals": ["Insufficient historical bars for technical indicators."],
            "latest": {},
            "series": [],
        }

    df = pd.DataFrame(bars).copy()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df = df[df["close"] > 0].reset_index(drop=True)
    if len(df) < 5:
        return {
            "overall_signal": "NEUTRAL",
            "signals": ["Insufficient valid closing prices."],
            "latest": {},
            "series": [],
        }

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["sma_20"] = ta.trend.SMAIndicator(close=close, window=min(20, len(df))).sma_indicator()
    df["sma_50"] = ta.trend.SMAIndicator(close=close, window=min(50, len(df))).sma_indicator()
    df["ema_20"] = ta.trend.EMAIndicator(close=close, window=min(20, len(df))).ema_indicator()

    rsi_win = min(14, max(2, len(df) - 1))
    df["rsi_14"] = ta.momentum.RSIIndicator(close=close, window=rsi_win).rsi()

    macd_ind = ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd_ind.macd()
    df["macd_signal"] = macd_ind.macd_signal()
    df["macd_hist"] = macd_ind.macd_diff()

    bb_win = min(20, max(2, len(df)))
    bb_ind = ta.volatility.BollingerBands(close=close, window=bb_win, window_dev=2)
    df["bb_upper"] = bb_ind.bollinger_hband()
    df["bb_middle"] = bb_ind.bollinger_mavg()
    df["bb_lower"] = bb_ind.bollinger_lband()

    atr_win = min(14, max(2, len(df) - 1))
    df["atr_14"] = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=atr_win).average_true_range()
    df["sma_200"] = ta.trend.SMAIndicator(close=close, window=min(200, len(df))).sma_indicator() if len(df) >= 20 else np.nan

    # Volume & Momentum Oscillators: OBV, MFI(14), Williams %R(14), CCI(20), Supertrend(10, 3)
    obv_val: float | None = None
    mfi_val: float | None = None
    willr_val: float | None = None
    cci_val: float | None = None
    supertrend_sig = "NEUTRAL"
    supertrend_series: list[float | None] = [None] * len(df)

    try:
        if float(df["volume"].sum()) > 0:
            obv_val = _r2(ta.volume.OnBalanceVolumeIndicator(close=close, volume=df["volume"]).on_balance_volume().iloc[-1])
            if len(df) >= 15:
                mfi_val = _r2(
                    ta.volume.MFIIndicator(high=high, low=low, close=close, volume=df["volume"], window=14)
                    .money_flow_index()
                    .iloc[-1]
                )
    except Exception:
        pass

    try:
        if len(df) >= 15:
            willr_val = _r2(ta.momentum.WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r().iloc[-1])
        if len(df) >= 21:
            cci_val = _r2(ta.trend.CCIIndicator(high=high, low=low, close=close, window=20).cci().iloc[-1])
    except Exception:
        pass

    try:
        if len(df) >= 12:
            atr10 = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=min(10, len(df) - 1)).average_true_range().fillna(0.0)
            hl2 = (high + low) / 2.0
            mult = 3.0
            upper = (hl2 + mult * atr10).to_numpy(dtype=np.float64)
            lower = (hl2 - mult * atr10).to_numpy(dtype=np.float64)
            c_arr = close.to_numpy(dtype=np.float64)
            st_dir = np.ones(len(df), dtype=int)
            final_up = upper.copy()
            final_lo = lower.copy()
            st_line = np.zeros(len(df), dtype=np.float64)
            for idx in range(1, len(df)):
                final_up[idx] = upper[idx] if (upper[idx] < final_up[idx - 1] or c_arr[idx - 1] > final_up[idx - 1]) else final_up[idx - 1]
                final_lo[idx] = lower[idx] if (lower[idx] > final_lo[idx - 1] or c_arr[idx - 1] < final_lo[idx - 1]) else final_lo[idx - 1]
                if st_dir[idx - 1] == 1:
                    st_dir[idx] = -1 if c_arr[idx] < final_lo[idx] else 1
                else:
                    st_dir[idx] = 1 if c_arr[idx] > final_up[idx] else -1
                st_line[idx] = final_lo[idx] if st_dir[idx] == 1 else final_up[idx]
            supertrend_sig = "BULLISH" if st_dir[-1] == 1 else "BEARISH"
            supertrend_series = [_r4(float(v)) if v > 0 else None for v in st_line]
    except Exception:
        supertrend_sig = "NEUTRAL"

    df["supertrend"] = supertrend_series

    # ADX(14), Stochastic RSI, and 20D VWAP
    adx_val: float | None = None
    if len(df) >= 15:
        try:
            adx_ind = ta.trend.ADXIndicator(high=high, low=low, close=close, window=14)
            adx_val = _r2(adx_ind.adx().iloc[-1])
        except Exception:
            adx_val = None

    stoch_k_val: float | None = None
    stoch_d_val: float | None = None
    if len(df) >= 15:
        try:
            stoch_ind = ta.momentum.StochRSIIndicator(close=close, window=14, smooth1=3, smooth2=3)
            stoch_k_val = _r2(stoch_ind.stochrsi_k().iloc[-1] * 100.0)
            stoch_d_val = _r2(stoch_ind.stochrsi_d().iloc[-1] * 100.0)
        except Exception:
            stoch_k_val = None
            stoch_d_val = None

    vwap_win = min(20, len(df))
    typical_px = (high + low + close) / 3.0
    vol_series = df["volume"]
    if float(vol_series.tail(vwap_win).sum()) > 0:
        vwap_20_val = _r4(
            float((typical_px.tail(vwap_win) * vol_series.tail(vwap_win)).sum() / vol_series.tail(vwap_win).sum())
        )
    else:
        vwap_20_val = _r4(float(close.tail(vwap_win).mean()))

    # Support & Resistance Pivot Levels (based on trailing 20-session range)
    pivot_slice = df.tail(min(20, len(df)))
    p_high = float(pivot_slice["high"].max())
    p_low = float(pivot_slice["low"].min())
    p_close = float(df["close"].iloc[-1])
    p_range = max(0.0, p_high - p_low)
    pivot_pt = (p_high + p_low + p_close) / 3.0
    r1_lvl = (2.0 * pivot_pt) - p_low
    s1_lvl = (2.0 * pivot_pt) - p_high
    r2_lvl = pivot_pt + p_range
    s2_lvl = pivot_pt - p_range
    fib_r1 = pivot_pt + 0.382 * p_range
    fib_r2 = pivot_pt + 0.618 * p_range
    fib_s1 = pivot_pt - 0.382 * p_range
    fib_s2 = pivot_pt - 0.618 * p_range

    # 52-Week (up to 252 trading days) range position, realized volatility & max drawdown
    w52_slice = df.tail(min(252, len(df)))
    high_52w = float(w52_slice["high"].max())
    low_52w = float(w52_slice["low"].min())
    range_52w_pct = (
        _r2(((p_close - low_52w) / (high_52w - low_52w)) * 100.0)
        if high_52w > low_52w
        else 50.0
    )
    daily_rets = close.pct_change().dropna()
    ann_vol_pct = (
        _r2(float(daily_rets.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0)
        if len(daily_rets) > 2
        else 0.0
    )
    run_max = close.cummax()
    dd_pct_series = ((close - run_max) / run_max.replace(0, np.nan)) * 100.0
    max_dd_pct = _r2(float(dd_pct_series.min())) if len(dd_pct_series) > 0 else 0.0

    last = df.iloc[-1]
    last_close = float(last["close"])
    rsi_val = _r2(last.get("rsi_14"))
    sma20_val = _r4(last.get("sma_20"))
    sma50_val = _r4(last.get("sma_50"))
    sma200_val = _r4(last.get("sma_200")) if len(df) >= 60 else None
    ema20_val = _r4(last.get("ema_20"))
    macd_val = _r4(last.get("macd"))
    macd_sig_val = _r4(last.get("macd_signal"))
    macd_hist_val = _r4(last.get("macd_hist"))
    bb_up_val = _r4(last.get("bb_upper"))
    bb_low_val = _r4(last.get("bb_lower"))
    atr_val = _r4(last.get("atr_14"))
    golden_cross = bool(sma50_val is not None and sma200_val is not None and sma50_val > sma200_val)

    def _trailing_ret(sessions: int) -> float | None:
        if len(close) <= sessions:
            return None
        prev_p = float(close.iloc[-(sessions + 1)])
        return _r2(((last_close / prev_p) - 1.0) * 100.0) if prev_p > 0 else None

    ret_1m = _trailing_ret(21)
    ret_3m = _trailing_ret(63)
    ret_1y = _trailing_ret(min(251, len(close) - 1)) if len(close) >= 120 else None

    bull_score = 0
    bear_score = 0
    signals: list[str] = []

    if rsi_val is not None:
        if rsi_val < 30:
            bull_score += 2
            signals.append(f"RSI(14) is {rsi_val} — Oversold zone (<30), potential rebound.")
        elif rsi_val > 70:
            bear_score += 2
            signals.append(f"RSI(14) is {rsi_val} — Overbought zone (>70), elevated pullback risk.")
        elif rsi_val >= 52:
            bull_score += 1
            signals.append(f"RSI(14) at {rsi_val} shows positive momentum.")
        else:
            signals.append(f"RSI(14) is neutral at {rsi_val}.")

    if sma20_val is not None and sma50_val is not None:
        if last_close > sma20_val and sma20_val >= sma50_val:
            bull_score += 2
            signals.append("Price trading above rising 20D & 50D SMA (Bullish Trend Alignment).")
        elif last_close < sma20_val and sma20_val < sma50_val:
            bear_score += 2
            signals.append("Price trading below 20D & 50D SMA (Bearish Trend Alignment).")

    if macd_hist_val is not None:
        if macd_hist_val > 0:
            bull_score += 1
            signals.append(f"MACD Histogram positive (+{macd_hist_val}) above signal line.")
        elif macd_hist_val < 0:
            bear_score += 1
            signals.append(f"MACD Histogram negative ({macd_hist_val}) below signal line.")

    if vwap_20_val is not None:
        if last_close > vwap_20_val:
            bull_score += 1
            signals.append(f"Price ({_r2(last_close)}) trading above 20D VWAP ({_r2(vwap_20_val)}) — institutional accumulation.")
        elif last_close < vwap_20_val:
            bear_score += 1
            signals.append(f"Price ({_r2(last_close)}) below 20D VWAP ({_r2(vwap_20_val)}) — overhead supply.")

    if adx_val is not None and adx_val >= 25.0:
        signals.append(f"ADX(14) at {adx_val} confirms a strong directional trend (>25).")

    if bb_up_val is not None and bb_low_val is not None and bb_up_val > bb_low_val:
        if last_close >= bb_up_val:
            signals.append("Testing Upper Bollinger Band (2σ breakout / stretched).")
        elif last_close <= bb_low_val:
            signals.append("Testing Lower Bollinger Band (2σ support / oversold).")

    if supertrend_sig == "BULLISH":
        bull_score += 1
        signals.append("Supertrend(10,3) is in a confirmed BULLISH uptrend.")
    elif supertrend_sig == "BEARISH":
        bear_score += 1
        signals.append("Supertrend(10,3) indicates BEARISH trailing resistance.")

    if golden_cross:
        bull_score += 1
        signals.append("Golden Cross active (50D SMA above 200D SMA).")

    if bull_score >= bear_score + 2:
        overall = "BULLISH"
    elif bear_score >= bull_score + 2:
        overall = "BEARISH"
    else:
        overall = "NEUTRAL"

    series_out: list[dict[str, Any]] = []
    for _, r in df.tail(120).iterrows():
        bt = r["bar_time"]
        bt_str = bt.isoformat() if hasattr(bt, "isoformat") else str(bt)
        series_out.append(
            {
                "bar_time": bt_str,
                "close": _r4(r["close"]),
                "sma_20": _r4(r.get("sma_20")),
                "sma_50": _r4(r.get("sma_50")),
                "sma_200": _r4(r.get("sma_200")),
                "ema_20": _r4(r.get("ema_20")),
                "supertrend": _r4(r.get("supertrend")),
                "bb_upper": _r4(r.get("bb_upper")),
                "bb_lower": _r4(r.get("bb_lower")),
                "rsi_14": _r2(r.get("rsi_14")),
                "macd": _r4(r.get("macd")),
                "macd_signal": _r4(r.get("macd_signal")),
                "macd_hist": _r4(r.get("macd_hist")),
            }
        )

    return {
        "overall_signal": overall,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "signals": signals,
        "latest": {
            "close": _r4(last_close),
            "rsi_14": rsi_val,
            "sma_20": sma20_val,
            "sma_50": sma50_val,
            "sma_200": sma200_val,
            "ema_20": ema20_val,
            "macd": macd_val,
            "macd_signal": macd_sig_val,
            "macd_hist": macd_hist_val,
            "bb_upper": bb_up_val,
            "bb_middle": _r4(last.get("bb_middle")),
            "bb_lower": bb_low_val,
            "atr_14": atr_val,
            "adx_14": adx_val,
            "stoch_rsi_k": stoch_k_val,
            "stoch_rsi_d": stoch_d_val,
            "vwap_20": vwap_20_val,
            "obv": obv_val,
            "mfi_14": mfi_val,
            "williams_r": willr_val,
            "cci_20": cci_val,
            "supertrend_signal": supertrend_sig,
            "golden_cross": golden_cross,
            "return_1m_pct": ret_1m,
            "return_3m_pct": ret_3m,
            "return_1y_pct": ret_1y,
            "pivot": _r4(pivot_pt),
            "r1": _r4(r1_lvl),
            "r2": _r4(r2_lvl),
            "s1": _r4(s1_lvl),
            "s2": _r4(s2_lvl),
            "fib_r1": _r4(fib_r1),
            "fib_r2": _r4(fib_r2),
            "fib_s1": _r4(fib_s1),
            "fib_s2": _r4(fib_s2),
            "high_52w": _r4(high_52w),
            "low_52w": _r4(low_52w),
            "range_52w_pct": range_52w_pct,
            "annualized_volatility_pct": ann_vol_pct,
            "max_drawdown_pct": max_dd_pct,
        },
        "series": series_out,
    }


def compute_fundamental_analysis(
    asset: dict[str, Any],
    profile: dict[str, Any] | None,
    shariah: dict[str, Any] | None = None,
    risk_free_rate_pct: float | None = None,
    deep_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute Fundamental Valuation & Quality Analysis from real company financial metrics:
    - Benjamin Graham Intrinsic Value (`sqrt(22.5 * EPS * BVPS)`) & Margin of Safety %
    - Earnings Yield (`E/P`) vs Sovereign Risk-Free Rate (PKR 13.5% / USD 4.3%)
    - Multi-Year Revenue & EPS CAGR + Net Profit Margin trend from scraped financial statements
    - 0-100 Fundamental Quality Score across Valuation (25), Profitability (25), Growth (25), Solvency (25)
    """
    currency = str(asset.get("currency") or "PKR").upper()
    rf_rate = float(risk_free_rate_pct) if risk_free_rate_pct is not None else (4.3 if currency == "USD" else 13.5)
    last_price = float(asset.get("last_price") or 0.0)
    asset_class = str(asset.get("asset_class") or "PSX_EQUITY")

    if not profile:
        return {
            "quality_score": 50,
            "valuation_score": 12,
            "profitability_score": 13,
            "growth_score": 12,
            "solvency_score": 13,
            "valuation_verdict": "FAIR_VALUE",
            "graham_intrinsic_value": None,
            "margin_of_safety_pct": None,
            "implied_bvps": None,
            "earnings_yield_pct": None,
            "risk_free_rate_pct": _r2(rf_rate),
            "equity_risk_premium_pct": None,
            "revenue_cagr_pct": None,
            "eps_cagr_pct": None,
            "latest_net_margin_pct": None,
            "net_margin_trend": "STABLE",
            "annual_financials": [],
            "signals": ["Fundamental profile metrics will populate upon company financials sync."],
        }

    pe = float(profile["pe_ratio"]) if profile.get("pe_ratio") not in (None, 0) else None
    pb = float(profile["pb_ratio"]) if profile.get("pb_ratio") not in (None, 0) else None
    eps = float(profile["eps"]) if profile.get("eps") not in (None, 0) else None
    roe = float(profile["roe_pct"]) if profile.get("roe_pct") not in (None, 0) else None
    if roe is None and deep_snapshot and deep_snapshot.get("roe_pct") not in (None, 0):
        roe = float(deep_snapshot["roe_pct"])
    div_yield = float(profile.get("dividend_yield_pct") or 0.0)
    free_float = float(profile["free_float_pct"]) if profile.get("free_float_pct") not in (None, 0) else None

    # Infer missing EPS or BVPS/P/B from fundamental accounting identities:
    # EPS = Price / (P/E), BVPS = Price / (P/B) = EPS / (ROE / 100)
    if eps is None and pe is not None and pe > 0 and last_price > 0:
        eps = last_price / pe
    if pe is None and eps is not None and eps > 0 and last_price > 0:
        pe = last_price / eps

    bvps: float | None = None
    if pb is not None and pb > 0 and last_price > 0:
        bvps = last_price / pb
    elif deep_snapshot and deep_snapshot.get("bvps") not in (None, 0) and float(deep_snapshot["bvps"]) > 0:
        bvps = float(deep_snapshot["bvps"])
        if last_price > 0:
            pb = last_price / bvps
    elif eps is not None and eps > 0 and roe is not None and roe > 0:
        bvps = eps / (roe / 100.0)
        if last_price > 0 and bvps > 0:
            pb = last_price / bvps

    # Parse multi-year financial statements from financial_summary_json (list OR {"annual_periods": [...]})
    fin_summary = profile.get("financial_summary_json") or {}
    if isinstance(fin_summary, list):
        raw_periods = fin_summary
    elif isinstance(fin_summary, dict):
        raw_periods = fin_summary.get("annual_periods") or []
    else:
        raw_periods = []

    annual_financials: list[dict[str, Any]] = []
    if isinstance(raw_periods, list):
        for item in raw_periods:
            if not isinstance(item, dict):
                continue
            p_label = item.get("period") or item.get("year")
            if p_label:
                normalized = dict(item)
                normalized["period"] = str(p_label)
                annual_financials.append(normalized)

    # Sort chronologically by 4-digit year if present ("TTM" placed last)
    def _extract_yr(p_str: str) -> int:
        if str(p_str).strip().upper() == "TTM":
            return 9999
        for tok in str(p_str).replace("-", " ").split():
            if tok.isdigit() and len(tok) == 4:
                return int(tok)
        return 0

    annual_financials_chrono = sorted(annual_financials, key=lambda x: _extract_yr(str(x.get("period", ""))))

    # If EPS still missing, check latest annual period EPS
    if eps is None and annual_financials_chrono:
        for item in reversed(annual_financials_chrono):
            if item.get("eps") and float(item["eps"]) > 0:
                eps = float(item["eps"])
                if pe is None and last_price > 0:
                    pe = last_price / eps
                break

    # Multi-year Revenue & EPS CAGR + Net Margin trend (exclude synthetic "TTM" when >=2 fiscal years exist)
    cagr_rows = [p for p in annual_financials_chrono if 1900 <= _extract_yr(str(p.get("period", ""))) < 9999]
    if len(cagr_rows) < 2:
        cagr_rows = annual_financials_chrono

    rev_cagr: float | None = None
    eps_cagr: float | None = None
    latest_margin: float | None = None
    margin_trend = "STABLE"

    rev_pts: list[tuple[int, float]] = []
    for key in ("revenue", "revenue_mn", "revenue_bn"):
        cand_pts = [
            (_extract_yr(str(p.get("period"))), float(p[key]))
            for p in cagr_rows
            if p.get(key) not in (None, 0) and float(p[key]) > 0
        ]
        if len(cand_pts) >= 2:
            rev_pts = cand_pts
            break
    if len(rev_pts) >= 2:
        y0, r0 = rev_pts[0]
        y1, r1 = rev_pts[-1]
        n_yrs = max(1, y1 - y0) if (1900 <= y0 < y1 < 9999) else max(1, len(rev_pts) - 1)
        if r0 > 0 and r1 > 0:
            rev_cagr = ((r1 / r0) ** (1.0 / n_yrs) - 1.0) * 100.0

    eps_pts = [
        (_extract_yr(str(p.get("period"))), float(p["eps"]))
        for p in cagr_rows
        if p.get("eps") not in (None, 0) and float(p["eps"]) > 0
    ]
    if len(eps_pts) >= 2:
        y0, e0 = eps_pts[0]
        y1, e1 = eps_pts[-1]
        n_yrs = max(1, y1 - y0) if (1900 <= y0 < y1 < 9999) else max(1, len(eps_pts) - 1)
        if e0 > 0 and e1 > 0:
            eps_cagr = ((e1 / e0) ** (1.0 / n_yrs) - 1.0) * 100.0

    margin_pts = [
        float(p["net_margin_pct"])
        for p in annual_financials_chrono
        if p.get("net_margin_pct") is not None
    ]
    if margin_pts:
        latest_margin = margin_pts[-1]
        if len(margin_pts) >= 2:
            diff_m = margin_pts[-1] - margin_pts[0]
            if diff_m >= 1.5:
                margin_trend = "EXPANDING"
            elif diff_m <= -1.5:
                margin_trend = "COMPRESSING"

    # Graham Intrinsic Value = sqrt(22.5 * EPS * BVPS)
    graham_val: float | None = None
    margin_of_safety: float | None = None
    if eps is not None and eps > 0 and bvps is not None and bvps > 0:
        graham_val = math.sqrt(22.5 * eps * bvps)
        if graham_val > 0 and last_price > 0:
            margin_of_safety = ((graham_val - last_price) / graham_val) * 100.0

    # Earnings Yield (E/P) vs Risk-Free Rate
    earnings_yield: float | None = None
    erp_spread: float | None = None
    if pe is not None and pe > 0:
        earnings_yield = 100.0 / pe
        erp_spread = earnings_yield - rf_rate

    # 1. Valuation Score (0 - 25)
    val_score = 12
    signals: list[str] = []
    is_intl = currency == "USD" or asset_class.startswith("INTL")
    fair_pe_benchmark = 22.0 if is_intl else 8.5

    if pe is not None and pe > 0:
        if pe <= fair_pe_benchmark * 0.7:
            val_score += 7
            signals.append(f"Attractive P/E multiple ({_r2(pe)}x vs {_r2(fair_pe_benchmark)}x market benchmark).")
        elif pe <= fair_pe_benchmark * 1.1:
            val_score += 4
        elif pe > fair_pe_benchmark * 1.6:
            val_score -= 4
            signals.append(f"Premium P/E valuation ({_r2(pe)}x) requires sustained high earnings growth.")

    if div_yield >= (6.0 if not is_intl else 2.0):
        val_score += 4
        signals.append(f"Strong cash dividend yield of {_r2(div_yield)}%.")
    elif div_yield > 0:
        val_score += 2

    if margin_of_safety is not None:
        if margin_of_safety >= 15.0:
            val_score += 4
            signals.append(
                f"Benjamin Graham Intrinsic Value ({currency} {_r2(graham_val)}) implies +{_r2(margin_of_safety)}% Margin of Safety."
            )
        elif margin_of_safety >= -10.0:
            val_score += 2
        else:
            val_score -= 2
    val_score = max(0, min(25, val_score))

    # 2. Profitability Score (0 - 25)
    prof_score = 12
    if roe is not None:
        if roe >= 20.0:
            prof_score += 8
            signals.append(f"High Return on Equity (ROE: {_r2(roe)}%) demonstrates strong capital efficiency.")
        elif roe >= 12.0:
            prof_score += 5
        elif roe < 5.0:
            prof_score -= 4
    if latest_margin is not None:
        if latest_margin >= 15.0:
            prof_score += 5
            signals.append(f"Healthy Net Profit Margin ({_r2(latest_margin)}%, {margin_trend.lower()}).")
        elif latest_margin >= 8.0:
            prof_score += 3
        elif latest_margin < 0:
            prof_score -= 5
    elif eps is not None and eps > 0:
        prof_score += 4
    prof_score = max(0, min(25, prof_score))

    # 3. Growth Score (0 - 25)
    growth_score = 12
    if eps_cagr is not None:
        if eps_cagr >= 15.0:
            growth_score += 8
            signals.append(f"Multi-year EPS CAGR of +{_r2(eps_cagr)}% across reported annual financials.")
        elif eps_cagr >= 5.0:
            growth_score += 4
        elif eps_cagr < 0:
            growth_score -= 4
    if rev_cagr is not None:
        if rev_cagr >= 12.0:
            growth_score += 5
            signals.append(f"Top-line Revenue compounding at +{_r2(rev_cagr)}% CAGR.")
        elif rev_cagr >= 4.0:
            growth_score += 3
        elif rev_cagr < 0:
            growth_score -= 3
    growth_score = max(0, min(25, growth_score))

    # 4. Solvency & Governance Score (0 - 25)
    solv_score = 15
    if shariah:
        debt_ratio = float(shariah.get("debt_to_assets_pct") or 0.0)
        if shariah.get("passed"):
            solv_score += 6
            signals.append(f"KMI Shariah compliant balance sheet (Debt/Assets: {_r2(debt_ratio)}% < 37% cap).")
        elif debt_ratio > 37.0:
            solv_score -= 4
    if free_float is not None:
        if free_float >= 25.0:
            solv_score += 4
        elif free_float < 10.0:
            solv_score -= 2
    solv_score = max(0, min(25, solv_score))

    if erp_spread is not None:
        if erp_spread >= 2.0:
            signals.append(
                f"Earnings Yield ({_r2(earnings_yield)}%) beats Risk-Free Rate ({_r2(rf_rate)}%) by +{_r2(erp_spread)}%."
            )
        elif erp_spread < -2.0 and not is_intl:
            signals.append(
                f"Earnings Yield ({_r2(earnings_yield)}%) trails sovereign T-Bill rate ({_r2(rf_rate)}%) by {_r2(abs(erp_spread))}%."
            )

    dcf_val = (
        float(deep_snapshot["dcf_intrinsic_value"])
        if deep_snapshot and deep_snapshot.get("dcf_intrinsic_value") not in (None, 0)
        else None
    )
    f_score = (
        int(deep_snapshot["piotroski_f_score"])
        if deep_snapshot and deep_snapshot.get("piotroski_f_score") is not None
        else None
    )
    z_score = (
        float(deep_snapshot["altman_z_score"])
        if deep_snapshot and deep_snapshot.get("altman_z_score") is not None
        else None
    )
    if f_score is not None:
        if f_score >= 7:
            prof_score += 3
            solv_score += 3
            signals.append(f"Strong Piotroski F-Score ({f_score}/9) confirms improving profitability & balance sheet.")
        elif f_score <= 3:
            solv_score -= 3
            signals.append(f"Weak Piotroski F-Score ({f_score}/9) flags deteriorating financial momentum.")
    if dcf_val is not None and last_price > 0:
        dcf_upside = ((dcf_val - last_price) / last_price) * 100.0
        if dcf_upside >= 15.0:
            val_score = min(25, val_score + 3)
            signals.append(f"2-Stage FCF DCF model ({currency} {_r2(dcf_val)}) suggests +{_r2(dcf_upside)}% intrinsic upside.")
        elif dcf_upside <= -20.0:
            signals.append(f"Trading above DCF intrinsic value ({currency} {_r2(dcf_val)}, {_r2(dcf_upside)}%).")
    if z_score is not None and z_score < 1.81:
        solv_score = max(0, solv_score - 3)
        signals.append(f"Altman Z-Score ({_r2(z_score)}) sits in financial distress zone (<1.81).")

    prof_score = max(0, min(25, prof_score))
    solv_score = max(0, min(25, solv_score))
    quality_score = int(max(0, min(100, val_score + prof_score + growth_score + solv_score)))

    if asset_class in {"PSX_ETF", "INTL_ETF", "PSX_INDEX", "INTL_INDEX"} and pe is None:
        verdict = "INDEX_OR_FUND"
    elif (margin_of_safety is not None and margin_of_safety >= 12.0) or (pe is not None and 0 < pe <= fair_pe_benchmark * 0.78):
        verdict = "UNDERVALUED"
    elif (margin_of_safety is not None and margin_of_safety <= -25.0 and (pe is None or pe > fair_pe_benchmark * 1.4)) or (
        pe is not None and pe > fair_pe_benchmark * 1.65
    ):
        verdict = "OVERVALUED"
    else:
        verdict = "FAIR_VALUE"

    return {
        "quality_score": quality_score,
        "valuation_score": val_score,
        "profitability_score": prof_score,
        "growth_score": growth_score,
        "solvency_score": solv_score,
        "valuation_verdict": verdict,
        "graham_intrinsic_value": _r2(graham_val),
        "margin_of_safety_pct": _r2(margin_of_safety),
        "implied_bvps": _r2(bvps),
        "implied_pe": _r2(pe),
        "implied_pb": _r2(pb),
        "earnings_yield_pct": _r2(earnings_yield),
        "risk_free_rate_pct": _r2(rf_rate),
        "equity_risk_premium_pct": _r2(erp_spread),
        "revenue_cagr_pct": _r2(rev_cagr),
        "eps_cagr_pct": _r2(eps_cagr),
        "latest_net_margin_pct": _r2(latest_margin),
        "net_margin_trend": margin_trend,
        "annual_financials": list(reversed(annual_financials_chrono)),
        "signals": signals,
    }


def compute_portfolio_quant_analytics(
    daily_nav_series: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    price_history_by_symbol: dict[str, Any],
    benchmark_symbol: str = "KSE100",
    benchmark_history: list[tuple[date, float]] | None = None,
    benchmark_bars: list[tuple[date, float]] | None = None,
    risk_free_rate_pct: float = 12.0,
    portfolio_id: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """
    Compute institutional portfolio quant metrics (Sharpe, Sortino, Calmar, Max Drawdown,
    Historical & Parametric 95% VaR/CVaR, Beta/Alpha vs Benchmark, Correlation Matrix,
    and Markowitz Mean-Variance Optimal Weights via `scipy.optimize.minimize`).
    """
    if benchmark_history is None and benchmark_bars is not None:
        benchmark_history = benchmark_bars

    # If ledger has fewer than 3 snapshots (e.g. newly entered positions today), synthesize backtested NAV series
    # from the real historical EOD price series of current holdings so risk & Markowitz analytics work immediately.
    if len(daily_nav_series) < 3 and holdings and price_history_by_symbol:
        synth_frames: list[pd.Series] = []
        synth_weights: list[float] = []
        for h in holdings:
            if h.get("asset_class") == "CASH" or float(h.get("market_value_pkr", 0)) <= 0:
                continue
            sym = str(h["symbol"])
            raw_pts = price_history_by_symbol.get(sym, [])
            pts = list(raw_pts.items()) if isinstance(raw_pts, dict) else list(raw_pts)
            if len(pts) >= 5:
                s = pd.Series({pd.to_datetime(dt): float(px) for dt, px in pts if float(px) > 0}, name=sym)
                if len(s) >= 5:
                    synth_frames.append(s)
                    synth_weights.append(float(h.get("market_value_pkr", 0.0)))
        if synth_frames and sum(synth_weights) > 0:
            s_df = pd.concat(synth_frames, axis=1).sort_index().ffill().dropna()
            if len(s_df) >= 3:
                w_arr = np.array(synth_weights, dtype=np.float64)
                w_arr = w_arr / np.sum(w_arr)
                norm_df = s_df / s_df.iloc[0]
                synth_nav = (norm_df.to_numpy(dtype=np.float64) @ w_arr) * 100.0
                daily_nav_series = [
                    {"date": dt.strftime("%Y-%m-%d"), "nav_per_unit": float(nav_v)}
                    for dt, nav_v in zip(s_df.index, synth_nav, strict=False)
                ]

    if len(daily_nav_series) < 3:
        return {
            "benchmark_symbol": benchmark_symbol,
            "risk_free_rate_pct": risk_free_rate_pct,
            "metrics": {
                "annualized_return_pct": 0.0,
                "annualized_volatility_pct": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "current_drawdown_pct": 0.0,
                "var_95_daily_pct": 0.0,
                "cvar_95_daily_pct": 0.0,
                "win_rate_pct": 0.0,
                "best_day_pct": 0.0,
                "worst_day_pct": 0.0,
                "beta": None,
                "alpha_annualized_pct": None,
                "r_squared": None,
                "hhi_index": 0.0,
                "effective_holdings_count": 0.0,
                "top_holding_weight_pct": 0.0,
                "diversification_rating": "CONCENTRATED",
            },
            "drawdown_series": [],
            "monthly_returns": [],
            "correlation_matrix": {"symbols": [], "matrix": []},
            "optimization": [],
        }

    nav_df = pd.DataFrame(daily_nav_series).copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df = nav_df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    nav_df["nav"] = pd.to_numeric(nav_df["nav_per_unit"], errors="coerce").fillna(100.0)
    nav_df["ret"] = nav_df["nav"].pct_change().dropna()

    rets = nav_df["ret"].dropna().to_numpy(dtype=np.float64)
    if len(rets) < 2:
        rets = np.array([0.0, 0.0], dtype=np.float64)

    rf_daily = (risk_free_rate_pct / 100.0) / TRADING_DAYS_PER_YEAR
    mean_daily = float(np.mean(rets))
    std_daily = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0

    total_days = max(1, (nav_df["date"].iloc[-1] - nav_df["date"].iloc[0]).days)
    total_growth = float(nav_df["nav"].iloc[-1] / max(nav_df["nav"].iloc[0], 1e-6))
    ann_ret = (total_growth ** (365.0 / max(total_days, 30)) - 1.0) * 100.0
    ann_vol = std_daily * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0

    excess_daily = rets - rf_daily
    sharpe = (
        float(np.mean(excess_daily) / std_daily * math.sqrt(TRADING_DAYS_PER_YEAR))
        if std_daily > 1e-8
        else 0.0
    )

    downside = rets[rets < rf_daily] - rf_daily
    downside_std = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) > 0 else 0.0
    sortino = (
        float(np.mean(excess_daily) / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))
        if downside_std > 1e-8
        else 0.0
    )

    # Running peak & underwater drawdown
    nav_vals = nav_df["nav"].to_numpy(dtype=np.float64)
    running_max = np.maximum.accumulate(nav_vals)
    dd_series = ((nav_vals - running_max) / np.maximum(running_max, 1e-8)) * 100.0
    max_dd = float(np.min(dd_series)) if len(dd_series) > 0 else 0.0
    curr_dd = float(dd_series[-1]) if len(dd_series) > 0 else 0.0
    calmar = float(ann_ret / abs(max_dd)) if abs(max_dd) > 1e-4 else 0.0

    # Historical 95% Daily VaR & CVaR (Expected Shortfall)
    var_95 = float(np.percentile(rets, 5) * 100.0)
    tail_losses = rets[rets <= np.percentile(rets, 5)]
    cvar_95 = float(np.mean(tail_losses) * 100.0) if len(tail_losses) > 0 else var_95

    win_rate = float(np.mean(rets > 0) * 100.0) if len(rets) > 0 else 0.0
    best_day = float(np.max(rets) * 100.0) if len(rets) > 0 else 0.0
    worst_day = float(np.min(rets) * 100.0) if len(rets) > 0 else 0.0

    # Benchmark Beta / Alpha / R-squared via scipy.stats.linregress
    beta_val: float | None = None
    alpha_val: float | None = None
    r_sq_val: float | None = None
    if benchmark_history and len(benchmark_history) >= 5:
        bdf = pd.DataFrame(benchmark_history, columns=["date", "bench_px"])
        bdf["date"] = pd.to_datetime(bdf["date"])
        bdf = bdf.sort_values("date").drop_duplicates("date")
        bdf["bench_ret"] = bdf["bench_px"].pct_change()
        merged = pd.merge(nav_df[["date", "ret"]], bdf[["date", "bench_ret"]], on="date", how="inner").dropna()
        if len(merged) >= 5 and merged["bench_ret"].std() > 1e-8:
            reg = stats.linregress(merged["bench_ret"].to_numpy(), merged["ret"].to_numpy())
            beta_val = _r2(reg.slope)
            alpha_daily = float(reg.intercept) - rf_daily * (1.0 - float(reg.slope))
            alpha_val = _r2(alpha_daily * TRADING_DAYS_PER_YEAR * 100.0)
            r_sq_val = _r2(float(reg.rvalue) ** 2)

    drawdown_points = [
        {"date": d.strftime("%Y-%m-%d"), "drawdown_pct": _r2(dd) or 0.0}
        for d, dd in zip(nav_df["date"].iloc[-120:], dd_series[-120:], strict=False)
    ]

    # Monthly returns table
    nav_df["ym"] = nav_df["date"].dt.strftime("%Y-%m")
    monthly_rows: list[dict[str, Any]] = []
    for ym, grp in nav_df.groupby("ym"):
        first_nav = float(grp["nav"].iloc[0])
        last_nav = float(grp["nav"].iloc[-1])
        m_ret = ((last_nav / first_nav) - 1.0) * 100.0 if first_nav > 0 else 0.0
        monthly_rows.append({"month": str(ym), "return_pct": _r2(m_ret) or 0.0})

    # Correlation Matrix & Markowitz Mean-Variance Optimization across non-cash holdings
    non_cash = [
        h for h in holdings
        if h.get("asset_class") != "CASH" and float(h.get("market_value_pkr", 0)) > 0
    ]
    non_cash.sort(key=lambda x: float(x.get("market_value_pkr", 0)), reverse=True)
    top_holdings = non_cash[:8]

    # Concentration & Diversification (HHI Index & Effective Number of Holdings)
    all_non_cash_mv = np.array([float(h.get("market_value_pkr", 0.0)) for h in non_cash], dtype=np.float64)
    total_non_cash_mv = float(np.sum(all_non_cash_mv)) if len(all_non_cash_mv) > 0 else 0.0
    if total_non_cash_mv > 0:
        w_all = all_non_cash_mv / total_non_cash_mv
        sum_sq = float(np.sum(np.square(w_all)))
        hhi_index = _r2(sum_sq * 10000.0) or 10000.0
        eff_holdings = _r2(1.0 / max(sum_sq, 1e-6)) or 1.0
        top_weight_pct = _r2(float(np.max(w_all)) * 100.0) or 100.0
    else:
        hhi_index = 0.0
        eff_holdings = 0.0
        top_weight_pct = 0.0

    if eff_holdings >= 4.0 and hhi_index <= 2500.0:
        div_rating = "HIGH"
    elif eff_holdings >= 2.2 and hhi_index <= 4500.0:
        div_rating = "MODERATE"
    else:
        div_rating = "CONCENTRATED"

    price_frames: list[pd.Series] = []
    valid_holdings: list[dict[str, Any]] = []
    for h in top_holdings:
        sym = str(h["symbol"])
        raw_pts = price_history_by_symbol.get(sym, [])
        if isinstance(raw_pts, dict):
            series_pts = list(raw_pts.items())
        else:
            series_pts = list(raw_pts)
        if len(series_pts) >= 5:
            s = pd.Series(
                {pd.to_datetime(dt): float(px) for dt, px in series_pts if float(px) > 0},
                name=sym,
            )
            if len(s) >= 5:
                price_frames.append(s)
                valid_holdings.append(h)

    corr_symbols: list[str] = []
    corr_matrix_out: list[list[float]] = []
    optimization_out: list[dict[str, Any]] = []
    frontier_points: list[dict[str, float]] = []

    if len(price_frames) >= 2:
        px_df = pd.concat(price_frames, axis=1).sort_index().ffill().dropna()
        ret_df = px_df.pct_change().dropna()
        if len(ret_df) >= 4:
            corr_df = ret_df.corr().fillna(0.0)
            corr_symbols = list(corr_df.columns)
            corr_matrix_out = [
                [_r2(val) or 0.0 for val in row]
                for row in corr_df.to_numpy()
            ]

            n_assets = len(corr_symbols)
            mean_rets_ann = ret_df.mean().to_numpy(dtype=np.float64) * TRADING_DAYS_PER_YEAR
            cov_ann = ret_df.cov().to_numpy(dtype=np.float64) * TRADING_DAYS_PER_YEAR
            rf_dec = risk_free_rate_pct / 100.0

            # Ledoit-Wolf shrinkage covariance + HRP weights via PyPortfolioOpt (graceful SciPy fallback)
            w_hrp = np.ones(n_assets) / n_assets
            try:
                from pypfopt import EfficientFrontier, HRPOpt, expected_returns, risk_models

                mu_s = expected_returns.mean_historical_return(px_df)
                S_mat = risk_models.CovarianceShrinkage(px_df).ledoit_wolf()
                hrp = HRPOpt(returns=ret_df)
                hrp_dict = hrp.optimize()
                w_hrp = np.array([float(hrp_dict.get(sym, 0.0)) for sym in corr_symbols], dtype=np.float64)

                max_w_pfo = 0.65 if n_assets == 2 else 0.45
                ef_max = EfficientFrontier(mu_s, S_mat, weight_bounds=(0.02, max_w_pfo))
                ef_max.max_sharpe(risk_free_rate=rf_dec)
                w_pfo_s = ef_max.clean_weights()
                w_sharpe = np.array([float(w_pfo_s.get(sym, 0.0)) for sym in corr_symbols], dtype=np.float64)

                ef_min = EfficientFrontier(mu_s, S_mat, weight_bounds=(0.02, max_w_pfo))
                ef_min.min_volatility()
                w_pfo_m = ef_min.clean_weights()
                w_minvol = np.array([float(w_pfo_m.get(sym, 0.0)) for sym in corr_symbols], dtype=np.float64)

                # Sample 10-point Efficient Frontier Curve
                min_ret = float(np.dot(w_minvol, mu_s.to_numpy(dtype=np.float64)))
                max_ret = max(min_ret + 0.01, float(mu_s.max()) * 0.92)
                for tgt_r in np.linspace(min_ret, max_ret, 10):
                    try:
                        ef_pt = EfficientFrontier(mu_s, S_mat, weight_bounds=(0.02, max_w_pfo))
                        ef_pt.efficient_return(target_return=float(tgt_r))
                        r_pt, v_pt, s_pt = ef_pt.portfolio_performance(risk_free_rate=rf_dec)
                        frontier_points.append(
                            {
                                "return_pct": round(float(r_pt) * 100.0, 2),
                                "volatility_pct": round(float(v_pt) * 100.0, 2),
                                "sharpe_ratio": round(float(s_pt), 2),
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                pass

            raw_mv = np.array([float(h.get("market_value_pkr", 0.0)) for h in valid_holdings], dtype=np.float64)
            opt_pool_mv = float(np.sum(raw_mv))
            curr_w = raw_mv / opt_pool_mv if opt_pool_mv > 0 else np.ones(n_assets) / n_assets

            # Bound single-asset weight between 2% and 45% (or 65% if n=2) for realistic diversification
            max_w = 0.65 if n_assets == 2 else 0.45
            bounds = tuple((0.02, max_w) for _ in range(n_assets))
            constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
            init_w = np.ones(n_assets) / n_assets

            def neg_sharpe(w: np.ndarray) -> float:
                p_ret = float(np.dot(w, mean_rets_ann))
                p_vol = float(np.sqrt(max(1e-10, np.dot(w.T, np.dot(cov_ann, w)))))
                return -(p_ret - rf_dec) / p_vol

            def port_vol(w: np.ndarray) -> float:
                return float(np.sqrt(max(1e-10, np.dot(w.T, np.dot(cov_ann, w)))))

            if "w_sharpe" not in locals() or np.sum(w_sharpe) <= 0:
                try:
                    res_sharpe = optimize.minimize(neg_sharpe, init_w, method="SLSQP", bounds=bounds, constraints=constraints)
                    w_sharpe = res_sharpe.x if res_sharpe.success else init_w
                except Exception:
                    w_sharpe = init_w

            if "w_minvol" not in locals() or np.sum(w_minvol) <= 0:
                try:
                    res_minvol = optimize.minimize(port_vol, init_w, method="SLSQP", bounds=bounds, constraints=constraints)
                    w_minvol = res_minvol.x if res_minvol.success else init_w
                except Exception:
                    w_minvol = init_w

            if not frontier_points:
                for alpha_mix in np.linspace(0.0, 1.0, 8):
                    w_mix = (1.0 - alpha_mix) * w_minvol + alpha_mix * w_sharpe
                    r_m = float(np.dot(w_mix, mean_rets_ann))
                    v_m = port_vol(w_mix)
                    frontier_points.append(
                        {
                            "return_pct": round(r_m * 100.0, 2),
                            "volatility_pct": round(v_m * 100.0, 2),
                            "sharpe_ratio": round((r_m - rf_dec) / max(v_m, 1e-6), 2),
                        }
                    )

            for idx, h in enumerate(valid_holdings):
                sym = corr_symbols[idx]
                c_w_pct = float(curr_w[idx] * 100.0)
                s_w_pct = float(w_sharpe[idx] * 100.0)
                mv_w_pct = float(w_minvol[idx] * 100.0)
                hrp_w_pct = float(w_hrp[idx] * 100.0)
                cur_mv_pkr = float(h.get("market_value_pkr", 0.0))
                unit_px_pkr = float(h.get("current_price_pkr") or 0.0)
                if unit_px_pkr <= 0 and price_frames[idx].size > 0:
                    unit_px_pkr = float(price_frames[idx].iloc[-1])

                target_sharpe_mv = opt_pool_mv * float(w_sharpe[idx])
                sharpe_diff_pkr = target_sharpe_mv - cur_mv_pkr
                sharpe_delta_pct = s_w_pct - c_w_pct
                sharpe_action = "BUY" if sharpe_delta_pct >= 1.0 else ("SELL" if sharpe_delta_pct <= -1.0 else "HOLD")
                sharpe_units = (abs(sharpe_diff_pkr) / unit_px_pkr) if unit_px_pkr > 0 else 0.0

                target_minvol_mv = opt_pool_mv * float(w_minvol[idx])
                minvol_diff_pkr = target_minvol_mv - cur_mv_pkr
                minvol_delta_pct = mv_w_pct - c_w_pct
                minvol_action = "BUY" if minvol_delta_pct >= 1.0 else ("SELL" if minvol_delta_pct <= -1.0 else "HOLD")
                minvol_units = (abs(minvol_diff_pkr) / unit_px_pkr) if unit_px_pkr > 0 else 0.0

                optimization_out.append(
                    {
                        "symbol": sym,
                        "name": h.get("name", sym),
                        "asset_class": h.get("asset_class", ""),
                        "current_weight_pct": _r2(c_w_pct) or 0.0,
                        "max_sharpe_weight_pct": _r2(s_w_pct) or 0.0,
                        "min_vol_weight_pct": _r2(mv_w_pct) or 0.0,
                        "hrp_weight_pct": _r2(hrp_w_pct) or 0.0,
                        "expected_annual_return_pct": _r2(mean_rets_ann[idx] * 100.0) or 0.0,
                        "annual_volatility_pct": _r2(math.sqrt(max(0.0, cov_ann[idx, idx])) * 100.0) or 0.0,
                        "current_market_value_pkr": _r2(cur_mv_pkr) or 0.0,
                        "current_price_pkr": _r2(unit_px_pkr) or 0.0,
                        "sharpe_delta_weight_pct": _r2(sharpe_delta_pct) or 0.0,
                        "sharpe_action": sharpe_action,
                        "sharpe_trade_amount_pkr": _r2(abs(sharpe_diff_pkr)) or 0.0,
                        "sharpe_estimated_units": _r4(sharpe_units) or 0.0,
                        "min_vol_delta_weight_pct": _r2(minvol_delta_pct) or 0.0,
                        "min_vol_action": minvol_action,
                        "min_vol_trade_amount_pkr": _r2(abs(minvol_diff_pkr)) or 0.0,
                        "min_vol_estimated_units": _r4(minvol_units) or 0.0,
                    }
                )

    from app.services.backtester import compute_monte_carlo_and_stress

    total_mv_pkr = sum(max(0.0, float(h.get("market_value_pkr") or 0.0)) for h in holdings)
    mc_stress = (
        compute_monte_carlo_and_stress(
            holdings,
            portfolio_value_pkr=total_mv_pkr,
            ann_return_pct=ann_ret if abs(ann_ret) > 0.1 else 16.0,
            ann_vol_pct=ann_vol if ann_vol > 1.0 else 16.0,
            beta=beta_val,
            sbp_policy_rate_pct=risk_free_rate_pct,
        )
        if total_mv_pkr > 0
        else {"monte_carlo": [], "stress_tests": [], "exposures_pkr": {}}
    )

    return {
        "benchmark_symbol": benchmark_symbol,
        "risk_free_rate_pct": risk_free_rate_pct,
        "metrics": {
            "annualized_return_pct": _r2(ann_ret) or 0.0,
            "annualized_volatility_pct": _r2(ann_vol) or 0.0,
            "sharpe_ratio": _r2(sharpe) or 0.0,
            "sortino_ratio": _r2(sortino) or 0.0,
            "calmar_ratio": _r2(calmar) or 0.0,
            "max_drawdown_pct": _r2(max_dd) or 0.0,
            "current_drawdown_pct": _r2(curr_dd) or 0.0,
            "var_95_daily_pct": _r2(var_95) or 0.0,
            "cvar_95_daily_pct": _r2(cvar_95) or 0.0,
            "win_rate_pct": _r2(win_rate) or 0.0,
            "best_day_pct": _r2(best_day) or 0.0,
            "worst_day_pct": _r2(worst_day) or 0.0,
            "beta": beta_val,
            "alpha_annualized_pct": alpha_val,
            "r_squared": r_sq_val,
            "hhi_index": hhi_index,
            "effective_holdings_count": eff_holdings,
            "top_holding_weight_pct": top_weight_pct,
            "diversification_rating": div_rating,
        },
        "drawdown_series": drawdown_points,
        "monthly_returns": monthly_rows,
        "correlation_matrix": {
            "symbols": corr_symbols,
            "matrix": corr_matrix_out,
        },
        "optimization": optimization_out,
        "efficient_frontier": frontier_points,
        "monte_carlo": mc_stress["monte_carlo"],
        "stress_tests": mc_stress["stress_tests"],
    }


def compute_fund_risk_analytics(
    adj_bars: list[dict[str, Any]],
    *,
    benchmark_bars: list[dict[str, Any]] | None = None,
    risk_free_rate_pct: float = 13.0,
    ter_pct: float = 1.0,
    front_end_load_pct: float = 0.0,
) -> dict[str, Any]:
    """Compute mutual fund risk-adjusted metrics (Sharpe, Sortino, Alpha, Beta, Tracking Error, 5Y fee drag, NAV curve)."""
    if len(adj_bars) < 3:
        fel = float(front_end_load_pct or 0.0)
        ter = float(ter_pct or 0.0)
        fee_5y = (1.0 - ((1.0 - fel / 100.0) * ((1.0 - ter / 100.0) ** 5))) * 100.0
        return {
            "cagr_pct": None,
            "annualized_volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
            "max_drawdown_pct": None,
            "alpha_annualized_pct": None,
            "beta": None,
            "tracking_error_pct": None,
            "information_ratio": None,
            "total_return_1m_pct": None,
            "total_return_6m_pct": None,
            "total_return_1y_pct": None,
            "fee_drag_5y_pct": _r2(fee_5y),
            "nav_series": [
                {
                    "date": str(b["bar_time"])[:10],
                    "nav": round(float(b["close"]), 4),
                    "wealth_index": round(float(b.get("wealth_index") or 100.0), 2),
                }
                for b in adj_bars
            ],
        }

    fdf = pd.DataFrame(
        {
            "date": [pd.to_datetime(str(b["bar_time"])[:10]) for b in adj_bars],
            "close": [float(b["close"]) for b in adj_bars],
            "wealth": [float(b.get("wealth_index") or b["close"]) for b in adj_bars],
        }
    ).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    fdf["ret"] = fdf["close"].pct_change()
    rets = fdf["ret"].dropna().to_numpy(dtype=np.float64)
    total_days = max(1, (fdf["date"].iloc[-1] - fdf["date"].iloc[0]).days)
    growth = float(fdf["close"].iloc[-1] / max(fdf["close"].iloc[0], 1e-8))
    cagr = ((growth ** (365.0 / max(total_days, 14))) - 1.0) * 100.0 if growth > 0 else 0.0

    # Infer observations/year so weekly vs daily NAV histories annualize accurately
    avg_step_days = max(1.0, total_days / max(len(fdf) - 1, 1))
    obs_per_yr = min(252.0, 365.0 / avg_step_days)
    std_obs = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    ann_vol = std_obs * math.sqrt(obs_per_yr) * 100.0
    rf_obs = (risk_free_rate_pct / 100.0) / obs_per_yr
    excess = rets - rf_obs
    sharpe = (float(np.mean(excess)) / std_obs) * math.sqrt(obs_per_yr) if std_obs > 1e-8 else 0.0
    downside = excess[excess < 0]
    down_std = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) > 0 else 0.0
    sortino = (float(np.mean(excess)) / down_std) * math.sqrt(obs_per_yr) if down_std > 1e-8 else 0.0

    vals = fdf["close"].to_numpy(dtype=np.float64)
    peaks = np.maximum.accumulate(vals)
    dd = ((vals - peaks) / np.maximum(peaks, 1e-8)) * 100.0
    max_dd = float(np.min(dd)) if len(dd) else 0.0
    calmar = (cagr / abs(max_dd)) if abs(max_dd) > 0.05 else None

    def _window_ret(days_back: int) -> float | None:
        cutoff = fdf["date"].iloc[-1] - pd.Timedelta(days=days_back)
        if fdf["date"].iloc[0] > cutoff + pd.Timedelta(days=7):
            return None
        past_rows = fdf[fdf["date"] <= cutoff]
        ref_row = past_rows.iloc[-1] if not past_rows.empty else fdf.iloc[0]
        p0 = float(ref_row["close"])
        return _r2(((float(fdf["close"].iloc[-1]) / p0) - 1.0) * 100.0) if p0 > 0 else None

    beta_v = alpha_v = te_v = ir_v = None
    bench_map: dict[str, float] = {}
    if benchmark_bars and len(benchmark_bars) >= 3:
        bdf = pd.DataFrame(
            {
                "date": [pd.to_datetime(str(b["bar_time"])[:10]) for b in benchmark_bars],
                "b_close": [float(b["close"]) for b in benchmark_bars],
            }
        ).sort_values("date").drop_duplicates("date")
        b_aligned = bdf.set_index("date")["b_close"].reindex(fdf["date"]).ffill().bfill()
        if not b_aligned.isna().all() and float(b_aligned.iloc[0]) > 0:
            b_wealth = (b_aligned / float(b_aligned.iloc[0])) * 100.0
            for dt_v, bw in zip(fdf["date"], b_wealth, strict=False):
                bench_map[dt_v.strftime("%Y-%m-%d")] = round(float(bw), 2)
            fdf["b_ret"] = b_aligned.pct_change().to_numpy()
            merged = fdf.dropna(subset=["ret", "b_ret"])
            if len(merged) >= 5 and merged["b_ret"].std() > 1e-8:
                reg = stats.linregress(merged["b_ret"].to_numpy(), merged["ret"].to_numpy())
                beta_v = _r2(reg.slope)
                alpha_v = _r2((float(reg.intercept) - rf_obs * (1.0 - float(reg.slope))) * obs_per_yr * 100.0)
                active_rets = merged["ret"].to_numpy() - merged["b_ret"].to_numpy()
                te_dec = float(np.std(active_rets, ddof=1)) * math.sqrt(obs_per_yr)
                te_v = _r2(te_dec * 100.0)
                if te_dec > 1e-6:
                    ir_v = _r2((float(np.mean(active_rets)) * obs_per_yr) / te_dec)

    fel = float(front_end_load_pct or 0.0)
    ter = float(ter_pct or 0.0)
    fee_5y = (1.0 - ((1.0 - fel / 100.0) * ((1.0 - ter / 100.0) ** 5))) * 100.0

    nav_series = []
    for _, row in fdf.tail(180).iterrows():
        ds = row["date"].strftime("%Y-%m-%d")
        nav_series.append(
            {
                "date": ds,
                "nav": round(float(row["close"]), 4),
                "wealth_index": round(float(row["wealth"]), 2),
                "benchmark_index": bench_map.get(ds),
            }
        )

    return {
        "cagr_pct": _r2(cagr),
        "annualized_volatility_pct": _r2(ann_vol),
        "sharpe_ratio": _r2(sharpe),
        "sortino_ratio": _r2(sortino),
        "calmar_ratio": _r2(calmar) if calmar is not None else None,
        "max_drawdown_pct": _r2(max_dd),
        "alpha_annualized_pct": alpha_v,
        "beta": beta_v,
        "tracking_error_pct": te_v,
        "information_ratio": ir_v,
        "total_return_1m_pct": _window_ret(30),
        "total_return_6m_pct": _window_ret(182),
        "total_return_1y_pct": _window_ret(365),
        "fee_drag_5y_pct": _r2(fee_5y),
        "nav_series": nav_series,
    }
