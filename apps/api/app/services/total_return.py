from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.pk_tax import calculate_dividend_wht, calculate_intl_dividend_wht, normalize_filer_status

D = Decimal


def _to_date(val: Any) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val)[:10]
    return date.fromisoformat(s)


def _dividend_keep_ratio(
    mode: str,
    *,
    is_intl: bool = False,
    country: str = "PK",
    us_w8ben_active: bool = True,
    purification_rate_pct: float = 0.0,
    apply_shariah_purification: bool = False,
) -> float:
    m = mode.upper()
    if m == "GROSS_TR":
        keep = 1.0
    elif m in {"NET_FILER", "NET_NON_FILER"}:
        filer = "ACTIVE_FILER" if m == "NET_FILER" else "NON_FILER"
        sample = D("100.00")
        if is_intl:
            wht = calculate_intl_dividend_wht(
                sample,
                us_w8ben_active=us_w8ben_active if m == "NET_FILER" else False,
                country=country,
            )
        else:
            wht = calculate_dividend_wht(sample, filer_status=normalize_filer_status(filer))
        keep = max(0.0, 1.0 - float(wht) / 100.0)
    else:
        return 1.0

    if apply_shariah_purification and purification_rate_pct > 0:
        keep = max(0.0, keep - (purification_rate_pct / 100.0))
    return keep


def build_adjusted_bars(
    bars: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    *,
    mode: str = "GROSS_TR",
    is_daily_dividend: bool = False,
    annualized_yield_pct: float = 0.0,
    is_intl: bool = False,
    country: str = "PK",
    us_w8ben_active: bool = True,
    purification_rate_pct: float = 0.0,
    apply_shariah_purification: bool = False,
    base_wealth: float = 100.0,
) -> list[dict[str, Any]]:
    """
    Build Dividend/Bonus/Split & Constant-NAV Daily-Dividend adjusted OHLCV + Base-100 Wealth Series.

    Modes:
      - `PRICE`: raw unadjusted OHLCV bars (plus wealth normalized to initial close)
      - `GROSS_TR`: backward total-return adjusted (cash dividends + splits/bonuses reinvested gross)
      - `NET_FILER`: deducts FBR Active Filer WHT (15% PK / 15% US W-8BEN / 0% IE), 10% Sec 236Z bonus tax, & optional Shariah purification
      - `NET_NON_FILER`: deducts Non-Filer WHT (30% PK / 30% US) & 20% Sec 236Z bonus tax
    """
    if not bars:
        return []

    sorted_bars = sorted(bars, key=lambda b: str(b["bar_time"]))
    m = mode.upper()
    if m == "PRICE":
        first_c = max(float(sorted_bars[0]["close"]), 1e-9)
        return [
            {
                **b,
                "raw_close": round(float(b["close"]), 4),
                "wealth_index": round((float(b["close"]) / first_c) * base_wealth, 4),
            }
            for b in sorted_bars
        ]

    keep_div = _dividend_keep_ratio(
        m,
        is_intl=is_intl,
        country=country,
        us_w8ben_active=us_w8ben_active,
        purification_rate_pct=purification_rate_pct,
        apply_shariah_purification=apply_shariah_purification,
    )
    bonus_keep = 0.90 if m == "NET_FILER" else (0.80 if m == "NET_NON_FILER" else 1.0)

    n = len(sorted_bars)
    bar_dates = [_to_date(b["bar_time"]) for b in sorted_bars]
    raw_closes = [max(float(b["close"]), 1e-9) for b in sorted_bars]

    # Constant-NAV Daily Dividend Funds compound continuously via yield on constant par NAV
    if is_daily_dividend and annualized_yield_pct > 0:
        eff_rate = (annualized_yield_pct / 100.0) * keep_div
        factors = [1.0] * n
        for idx in range(n - 1):
            days_gap = max(0, (bar_dates[idx + 1] - bar_dates[idx]).days)
            growth = (1.0 + eff_rate / 365.0) ** days_gap if days_gap > 0 else 1.0
            # Anchor latest price to current NAV while scaling history down by compounding growth
            for j in range(idx + 1):
                factors[j] /= growth
        first_adj = max(raw_closes[0] * factors[0], 1e-9)
        out: list[dict[str, Any]] = []
        for idx, b in enumerate(sorted_bars):
            f = factors[idx]
            adj_c = raw_closes[idx] * f
            out.append(
                {
                    **b,
                    "open": round(float(b["open"]) * f, 4),
                    "high": round(float(b["high"]) * f, 4),
                    "low": round(float(b["low"]) * f, 4),
                    "close": round(adj_c, 4),
                    "raw_close": round(raw_closes[idx], 4),
                    "wealth_index": round((adj_c / first_adj) * base_wealth, 4),
                }
            )
        return out

    # Stepwise multipliers applied to all bars strictly prior to each corporate action ex-date
    mult = [1.0] * n
    for act in actions or []:
        ex_dt_raw = act.get("ex_date")
        if not ex_dt_raw:
            continue
        ex_dt = _to_date(ex_dt_raw)
        # First bar on or after ex-date
        ex_idx = next((i for i, d in enumerate(bar_dates) if d >= ex_dt), None)
        if ex_idx is None or ex_idx == 0:
            continue

        prev_c = raw_closes[ex_idx - 1]
        ex_c = raw_closes[ex_idx]
        act_type = str(act.get("action_type") or "").upper()
        payout = float(act.get("payout_per_share") or 0.0)
        ratio_num = float(act.get("ratio_numerator") or 0.0)
        ratio_den = max(float(act.get("ratio_denominator") or 100.0), 1e-6)

        step_factor = 1.0
        if act_type == "CASH_DIVIDEND" and payout > 0 and prev_c > 0:
            net_div = payout * keep_div
            # Standard CRSP total-return backward adjustment
            step_factor = max(0.05, min(1.0, (prev_c - net_div) / prev_c))
        elif act_type in {"BONUS_ISSUE", "SPLIT"} and ratio_num > 0:
            k = 1.0 + (ratio_num / ratio_den)
            # Only apply backward split/bonus adjustment if raw history shows an unadjusted drop near ex-date
            if prev_c > ex_c * (0.65 * k):
                if act_type == "BONUS_ISSUE":
                    eff_k = 1.0 + ((k - 1.0) * bonus_keep)
                else:
                    eff_k = k
                step_factor = 1.0 / max(eff_k, 1.0)

        if abs(step_factor - 1.0) > 1e-6:
            for j in range(ex_idx):
                mult[j] *= step_factor

    first_adj = max(raw_closes[0] * mult[0], 1e-9)
    out = []
    for idx, b in enumerate(sorted_bars):
        f = mult[idx]
        adj_c = raw_closes[idx] * f
        out.append(
            {
                **b,
                "open": round(float(b["open"]) * f, 4),
                "high": round(float(b["high"]) * f, 4),
                "low": round(float(b["low"]) * f, 4),
                "close": round(adj_c, 4),
                "raw_close": round(raw_closes[idx], 4),
                "wealth_index": round((adj_c / first_adj) * base_wealth, 4),
            }
        )
    return out
