from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

D = Decimal


def _r2(v: float | None) -> float | None:
    if v is None or math.isnan(v) or math.isinf(v):
        return None
    return round(float(v), 2)


def _r4(v: float | None) -> float | None:
    if v is None or math.isnan(v) or math.isinf(v):
        return None
    return round(float(v), 4)


def compute_dcf_intrinsic_value(
    *,
    free_cashflow_mn: float | None,
    shares_mn: float | None,
    total_debt_mn: float = 0.0,
    cash_mn: float = 0.0,
    growth_rate_pct: float = 12.0,
    risk_free_rate_pct: float = 13.0,
    erp_pct: float = 5.5,
) -> float | None:
    """2-stage Free Cash Flow DCF intrinsic value per share with dynamic sovereign WACC."""
    if not free_cashflow_mn or free_cashflow_mn <= 0 or not shares_mn or shares_mn <= 0:
        return None

    wacc = max(0.08, min(0.24, (risk_free_rate_pct + erp_pct) / 100.0))
    g1 = max(-0.05, min(0.25, growth_rate_pct / 100.0))
    g_term = min(0.055 if risk_free_rate_pct > 8.0 else 0.03, wacc - 0.025)

    pv_sum = 0.0
    fcf = float(free_cashflow_mn)
    for yr in range(1, 6):
        fcf = fcf * (1.0 + g1 * (0.92 ** (yr - 1)))
        pv_sum += fcf / ((1.0 + wacc) ** yr)

    term_val = (fcf * (1.0 + g_term)) / max(wacc - g_term, 0.02)
    pv_term = term_val / ((1.0 + wacc) ** 5)
    equity_val_mn = pv_sum + pv_term + max(0.0, float(cash_mn)) - max(0.0, float(total_debt_mn))
    if equity_val_mn <= 0:
        return None
    return _r4(equity_val_mn / float(shares_mn))


def compute_piotroski_f_score(
    *,
    net_income: float | None = None,
    prev_net_income: float | None = None,
    total_assets: float | None = None,
    prev_total_assets: float | None = None,
    operating_cashflow: float | None = None,
    long_term_debt: float | None = None,
    prev_long_term_debt: float | None = None,
    current_assets: float | None = None,
    current_liabilities: float | None = None,
    prev_current_assets: float | None = None,
    prev_current_liabilities: float | None = None,
    net_margin_pct: float | None = None,
    prev_net_margin_pct: float | None = None,
    revenue: float | None = None,
    prev_revenue: float | None = None,
    shares: float | None = None,
    prev_shares: float | None = None,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Compute Piotroski F-Score (0-9) when sufficient Balance Sheet/Cash Flow signals exist."""
    checks: list[dict[str, Any]] = []

    def _add(name: str, passed: bool | None) -> None:
        if passed is not None:
            checks.append({"criterion": name, "passed": bool(passed)})

    roa = (net_income / total_assets) if (net_income is not None and total_assets and total_assets > 0) else None
    prev_roa = (
        (prev_net_income / prev_total_assets)
        if (prev_net_income is not None and prev_total_assets and prev_total_assets > 0)
        else None
    )
    _add("Positive ROA (NI / Assets > 0)", roa > 0 if roa is not None else None)
    _add("Positive Operating Cash Flow", operating_cashflow > 0 if operating_cashflow is not None else None)
    _add("Improving ROA YoY", (roa > prev_roa) if (roa is not None and prev_roa is not None) else None)
    _add(
        "Quality Earnings (OCF > Net Income)",
        (operating_cashflow > net_income) if (operating_cashflow is not None and net_income is not None) else None,
    )

    lev = (
        (long_term_debt / total_assets)
        if (long_term_debt is not None and total_assets and total_assets > 0)
        else None
    )
    prev_lev = (
        (prev_long_term_debt / prev_total_assets)
        if (prev_long_term_debt is not None and prev_total_assets and prev_total_assets > 0)
        else None
    )
    _add("Lower Leverage YoY", (lev <= prev_lev) if (lev is not None and prev_lev is not None) else None)

    cr = (
        (current_assets / current_liabilities)
        if (current_assets is not None and current_liabilities and current_liabilities > 0)
        else None
    )
    prev_cr = (
        (prev_current_assets / prev_current_liabilities)
        if (prev_current_assets is not None and prev_current_liabilities and prev_current_liabilities > 0)
        else None
    )
    _add("Improving Current Ratio", (cr > prev_cr) if (cr is not None and prev_cr is not None) else None)
    _add("No Equity Dilution", (shares <= prev_shares) if (shares and prev_shares) else None)
    _add(
        "Expanding Profit Margin",
        (net_margin_pct > prev_net_margin_pct)
        if (net_margin_pct is not None and prev_net_margin_pct is not None)
        else None,
    )

    at = (revenue / total_assets) if (revenue is not None and total_assets and total_assets > 0) else None
    prev_at = (
        (prev_revenue / prev_total_assets)
        if (prev_revenue is not None and prev_total_assets and prev_total_assets > 0)
        else None
    )
    _add("Higher Asset Turnover", (at > prev_at) if (at is not None and prev_at is not None) else None)

    if len(checks) < 3:
        return None, checks
    raw_pts = sum(1 for c in checks if c["passed"])
    scaled_9 = min(9, round((raw_pts / len(checks)) * 9)) if len(checks) < 9 else raw_pts
    return scaled_9, checks


def compute_altman_z_score(
    *,
    sector: str = "",
    total_assets_mn: float | None = None,
    total_liabilities_mn: float | None = None,
    current_assets_mn: float | None = None,
    current_liabilities_mn: float | None = None,
    retained_earnings_mn: float | None = None,
    ebit_mn: float | None = None,
    market_cap_mn: float | None = None,
    revenue_mn: float | None = None,
) -> tuple[float | None, str]:
    """Compute Altman Z-Score for non-financial companies."""
    sec = sector.lower()
    if any(k in sec for k in ["bank", "insurance", "financial", "modaraba", "leasing", "investment"]):
        return None, "NOT_APPLICABLE_FINANCIAL"
    if not total_assets_mn or total_assets_mn <= 0 or not total_liabilities_mn or total_liabilities_mn <= 0:
        return None, "INSUFFICIENT_DATA"

    wc = (current_assets_mn or 0.0) - (current_liabilities_mn or 0.0)
    x1 = wc / total_assets_mn
    x2 = (retained_earnings_mn or 0.0) / total_assets_mn
    x3 = (ebit_mn or 0.0) / total_assets_mn
    x4 = (market_cap_mn or 0.0) / total_liabilities_mn
    x5 = (revenue_mn or 0.0) / total_assets_mn
    z = (1.2 * x1) + (1.4 * x2) + (3.3 * x3) + (0.6 * x4) + (1.0 * x5)
    zone = "SAFE" if z >= 2.99 else ("GREY" if z >= 1.81 else "DISTRESS")
    return _r2(z), zone


def compute_dupont_roe(
    *,
    net_income_mn: float | None,
    revenue_mn: float | None,
    total_assets_mn: float | None,
    shareholders_equity_mn: float | None,
) -> dict[str, float | None]:
    """3-Factor DuPont ROE decomposition: Net Margin % * Asset Turnover * Equity Multiplier."""
    if (
        net_income_mn is None
        or not revenue_mn
        or revenue_mn <= 0
        or not total_assets_mn
        or total_assets_mn <= 0
        or not shareholders_equity_mn
        or shareholders_equity_mn <= 0
    ):
        return {}
    net_margin = (net_income_mn / revenue_mn) * 100.0
    asset_turnover = revenue_mn / total_assets_mn
    leverage = total_assets_mn / shareholders_equity_mn
    roe_calc = net_margin * asset_turnover * leverage
    return {
        "net_margin_pct": _r2(net_margin),
        "asset_turnover": _r2(asset_turnover),
        "equity_multiplier": _r2(leverage),
        "roe_dupont_pct": _r2(roe_calc),
    }


def build_stock_valuation_lab(
    *,
    asset: dict[str, Any],
    profile: dict[str, Any] | None,
    deep_snapshot: dict[str, Any] | None,
    shariah: dict[str, Any] | None,
    peer_valuation: dict[str, Any] | None,
    dividend_history: dict[str, Any] | None,
    risk_free_rate_pct: float = 12.0,
) -> dict[str, Any]:
    """
    Build an institutional Valuation Lab payload for `/stocks/[symbol]`:
    - Interactive DCF inputs + 5x5 (Growth x WACC) Sensitivity Matrix
    - Full 9-point Piotroski F-Score rule-by-rule checklist
    - 3-Factor DuPont ROE decomposition
    - Automated Bull vs Bear Thesis & Accounting Red-Flag Digest
    """
    def _f(val: Any) -> float | None:
        if val is None:
            return None
        try:
            v = float(val)
            return None if (math.isnan(v) or math.isinf(v)) else v
        except (TypeError, ValueError):
            return None

    px = _f(asset.get("last_price")) or 0.0
    sector = str(asset.get("sector_or_category") or "")
    sec_low = sector.lower()
    is_financial = any(k in sec_low for k in ("bank", "insurance", "financial", "modaraba", "leasing", "investment"))
    is_usd = str(asset.get("currency") or "PKR").upper() == "USD"

    snap = deep_snapshot or {}
    mj = snap.get("metrics_json") if isinstance(snap.get("metrics_json"), dict) else {}
    prof = profile or {}

    fcf_mn = _f(snap.get("free_cashflow_mn"))
    ocf_mn = _f(snap.get("operating_cashflow_mn"))
    tot_assets_mn = _f(snap.get("total_assets_mn"))
    tot_debt_mn = _f(snap.get("total_debt_mn")) or 0.0
    cash_mn = _f(snap.get("cash_equivalents_mn")) or 0.0
    eq_mn = _f(snap.get("shareholders_equity_mn"))
    rev_mn = _f(mj.get("revenue_mn"))
    ni_mn = _f(mj.get("net_income_mn"))
    cur_ratio = _f(snap.get("current_ratio"))
    d_to_e = _f(snap.get("debt_to_equity"))
    roe_pct = _f(snap.get("roe_pct")) if snap.get("roe_pct") is not None else _f(prof.get("roe_pct"))
    roa_pct = _f(snap.get("roa_pct"))
    op_margin = _f(snap.get("operating_margin_pct"))
    net_margin = _f(mj.get("net_margin_pct"))
    altman_z = _f(snap.get("altman_z_score"))
    f_score = snap.get("piotroski_f_score")

    shares_mn = _f(mj.get("shares_outstanding_mn"))
    if not shares_mn:
        mcap_mn = _f(prof.get("market_cap_mn"))
        if mcap_mn and mcap_mn > 0 and px > 0:
            shares_mn = round(mcap_mn / px, 4)

    # 1. DCF Interactive Lab + 5x5 Sensitivity Matrix
    erp = 5.5
    base_wacc = round(max(8.0, min(24.0, risk_free_rate_pct + erp)), 2)
    base_growth = 10.0 if is_usd else 12.0
    g_term = round(min(5.5 if risk_free_rate_pct > 8.0 else 3.0, base_wacc - 2.5), 2)

    dcf_available = (not is_financial) and bool(fcf_mn and fcf_mn > 0 and shares_mn and shares_mn > 0)
    growth_steps = [max(2.0, round(base_growth + d, 1)) for d in (-6.0, -3.0, 0.0, 3.0, 6.0)]
    wacc_steps = [max(7.5, round(base_wacc + d, 1)) for d in (-2.0, -1.0, 0.0, 1.0, 2.0)]
    sensitivity_rows: list[dict[str, Any]] = []
    if dcf_available:
        for g in growth_steps:
            cells: list[dict[str, Any]] = []
            for w in wacc_steps:
                iv = compute_dcf_intrinsic_value(
                    free_cashflow_mn=fcf_mn,
                    shares_mn=shares_mn,
                    total_debt_mn=tot_debt_mn,
                    cash_mn=cash_mn,
                    growth_rate_pct=g,
                    risk_free_rate_pct=max(2.0, w - erp),
                    erp_pct=erp,
                )
                upside = _r2(((iv - px) / px) * 100.0) if (iv and px > 0) else None
                cells.append({"wacc_pct": w, "intrinsic_value": iv, "upside_pct": upside})
            sensitivity_rows.append({"growth_pct": g, "cells": cells})

    dcf_lab = {
        "available": dcf_available,
        "reason_unavailable": (
            "Commercial banks & financial institutions are valued via P/B & Sustainable ROE rather than corporate EV/FCF DCF."
            if is_financial
            else ("Positive Free Cash Flow & share count required for DCF." if not dcf_available else None)
        ),
        "free_cashflow_mn": fcf_mn,
        "shares_mn": shares_mn,
        "total_debt_mn": tot_debt_mn,
        "cash_mn": cash_mn,
        "base_growth_pct": base_growth,
        "base_wacc_pct": base_wacc,
        "terminal_growth_pct": g_term,
        "risk_free_rate_pct": _r2(risk_free_rate_pct),
        "current_price": px,
        "base_intrinsic_value": _f(snap.get("dcf_intrinsic_value")),
        "wacc_steps": wacc_steps,
        "sensitivity_rows": sensitivity_rows,
    }

    # 2. 3-Factor DuPont ROE Decomposition
    dupont = mj.get("dupont_breakdown") if isinstance(mj.get("dupont_breakdown"), dict) and mj.get("dupont_breakdown") else None
    if not dupont:
        dupont = compute_dupont_roe(
            net_income_mn=ni_mn,
            revenue_mn=rev_mn,
            total_assets_mn=tot_assets_mn,
            shareholders_equity_mn=eq_mn,
        )

    # 3. 9-Point Piotroski Accounting Checklist (transparent pass/fail per rule)
    cash_conv = _r2(ocf_mn / ni_mn) if (ocf_mn is not None and ni_mn and ni_mn > 0) else None
    debt_to_assets = _f(mj.get("debt_to_assets_pct"))
    if debt_to_assets is None and tot_assets_mn and tot_assets_mn > 0 and tot_debt_mn > 0:
        debt_to_assets = _r2((tot_debt_mn / tot_assets_mn) * 100.0)
    asset_turnover = _r2(rev_mn / tot_assets_mn) if (rev_mn and tot_assets_mn and tot_assets_mn > 0) else None

    piotroski_rules: list[dict[str, Any]] = [
        {
            "group": "Profitability",
            "criterion": "Positive Return on Assets (ROA > 0%)",
            "passed": (roa_pct > 0) if roa_pct is not None else ((ni_mn > 0) if ni_mn is not None else None),
            "metric": f"ROA {roa_pct:.2f}%" if roa_pct is not None else "N/A",
        },
        {
            "group": "Profitability",
            "criterion": "Positive Operating Cash Flow (OCF > 0)",
            "passed": (ocf_mn > 0) if ocf_mn is not None else None,
            "metric": f"OCF {ocf_mn:,.0f} Mn" if ocf_mn is not None else "N/A",
        },
        {
            "group": "Profitability",
            "criterion": "Quality Earnings (OCF > Net Income)",
            "passed": (ocf_mn > ni_mn) if (ocf_mn is not None and ni_mn is not None) else None,
            "metric": f"OCF/NI {cash_conv:.2f}x" if cash_conv is not None else "N/A",
        },
        {
            "group": "Leverage & Liquidity",
            "criterion": "Healthy Liquidity (Current Ratio >= 1.2x)",
            "passed": (cur_ratio >= 1.2) if (cur_ratio is not None and not is_financial) else None,
            "metric": f"{cur_ratio:.2f}x" if cur_ratio is not None else ("Bank (N/A)" if is_financial else "N/A"),
        },
        {
            "group": "Leverage & Liquidity",
            "criterion": "Conservative Leverage (Debt/Assets < 37%)",
            "passed": (debt_to_assets < 37.0) if (debt_to_assets is not None and not is_financial) else None,
            "metric": f"{debt_to_assets:.1f}%" if debt_to_assets is not None else "N/A",
        },
        {
            "group": "Operating Efficiency",
            "criterion": "Strong Operating Margin (> 10%)",
            "passed": (op_margin >= 10.0) if op_margin is not None else None,
            "metric": f"{op_margin:.1f}%" if op_margin is not None else "N/A",
        },
        {
            "group": "Operating Efficiency",
            "criterion": "Positive Net Profit Margin (> 5%)",
            "passed": (net_margin >= 5.0) if net_margin is not None else None,
            "metric": f"{net_margin:.1f}%" if net_margin is not None else "N/A",
        },
        {
            "group": "Operating Efficiency",
            "criterion": "Healthy Asset Utilization (Turnover > 0.5x)",
            "passed": (asset_turnover >= 0.5) if (asset_turnover is not None and not is_financial) else None,
            "metric": f"{asset_turnover:.2f}x" if asset_turnover is not None else "N/A",
        },
        {
            "group": "Shareholder Return",
            "criterion": "Strong Return on Equity (ROE >= 15%)",
            "passed": (roe_pct >= 15.0) if roe_pct is not None else None,
            "metric": f"ROE {roe_pct:.1f}%" if roe_pct is not None else "N/A",
        },
    ]

    # 4. Automated Bull vs Bear Thesis & Red-Flag Digest
    bull_points: list[str] = []
    bear_points: list[str] = []
    red_flags: list[str] = []

    pe_f = _f(prof.get("pe_ratio"))
    pb_f = _f(prof.get("pb_ratio"))
    div_y = _f(prof.get("dividend_yield_pct")) or 0.0
    pe_vs_sec = _f((peer_valuation or {}).get("pe_vs_sector_pct"))
    sec_med_pe = _f((peer_valuation or {}).get("sector_median_pe"))

    if pe_vs_sec is not None and sec_med_pe is not None and pe_f is not None:
        if pe_vs_sec <= -10.0:
            bull_points.append(f"Trades at {abs(pe_vs_sec):.1f}% P/E discount to sector median ({pe_f:.1f}x vs {sec_med_pe:.1f}x).")
        elif pe_vs_sec >= 25.0:
            bear_points.append(f"Trades at +{pe_vs_sec:.1f}% P/E premium over sector median ({pe_f:.1f}x vs {sec_med_pe:.1f}x).")

    if roe_pct is not None:
        if roe_pct >= 20.0:
            bull_points.append(f"High capital efficiency with {roe_pct:.1f}% Return on Equity.")
        elif roe_pct < 8.0:
            bear_points.append(f"Sub-par ROE ({roe_pct:.1f}%) below Pakistan sovereign T-Bill yield.")

    if f_score is not None:
        if f_score >= 7:
            bull_points.append(f"Institutional accounting strength: Piotroski F-Score {f_score}/9.")
        elif f_score <= 3:
            red_flags.append(f"Weak Piotroski F-Score ({f_score}/9) — review margins, leverage & cash flow.")

    if cash_conv is not None:
        if cash_conv >= 1.0:
            bull_points.append(f"High earnings quality: Operating Cash Flow covers {cash_conv:.2f}x of Net Profit.")
        elif cash_conv < 0.5:
            red_flags.append(f"Low cash conversion ({cash_conv:.2f}x OCF/Net Income) — profits not fully turning into cash.")

    if fcf_mn is not None and fcf_mn < 0 and not is_financial:
        bear_points.append(f"Negative Free Cash Flow ({fcf_mn:,.0f} Mn) due to CapEx or working-capital outflows.")

    base_dcf = _f(snap.get("dcf_intrinsic_value"))
    if dcf_available and base_dcf and px > 0:
        dcf_up = ((base_dcf - px) / px) * 100.0
        if dcf_up >= 15.0:
            bull_points.append(f"Base-case DCF ({base_dcf:,.2f}) implies +{dcf_up:.1f}% intrinsic margin of safety.")
        elif dcf_up <= -25.0:
            bear_points.append(f"Market price sits {abs(dcf_up):.1f}% above conservative FCF DCF ({base_dcf:,.2f}) at {base_wacc:.1f}% WACC.")

    if div_y >= 7.0:
        streak = int((dividend_history or {}).get("streak_years") or 0)
        bull_points.append(
            f"Attractive cash income: {div_y:.2f}% dividend yield" + (f" ({streak}Y consecutive payout streak)." if streak >= 2 else ".")
        )
    payout_r = _f((dividend_history or {}).get("payout_ratio_pct"))
    if payout_r is not None and payout_r > 100.0:
        red_flags.append(f"Dividend payout ratio ({payout_r:.0f}%) exceeds TTM earnings — sustainability risk.")

    if altman_z is not None and not is_financial:
        if altman_z >= 2.99:
            bull_points.append(f"Fortress balance sheet: Altman Z-Score {altman_z:.2f} (Safe Zone).")
        elif altman_z < 1.81:
            red_flags.append(f"Altman Z-Score {altman_z:.2f} sits in Financial Distress Zone (< 1.81).")

    if d_to_e is not None and d_to_e > 1.5 and not is_financial:
        bear_points.append(f"Elevated Debt-to-Equity ratio ({d_to_e:.2f}x) increases sensitivity to SBP KIBOR rates.")

    if is_financial and pb_f is not None and roe_pct is not None:
        if pb_f <= 1.2 and roe_pct >= 18.0:
            bull_points.append(f"Compelling bank valuation: {pb_f:.2f}x P/B backed by {roe_pct:.1f}% ROE.")
        elif pb_f >= 2.8:
            bear_points.append(f"Rich bank P/B multiple ({pb_f:.2f}x) prices in sustained high ROE & spread resilience.")

    sh_headroom: float | None = None
    if shariah and shariah.get("passed"):
        d_a = _f(shariah.get("debt_to_assets_pct"))
        if d_a is not None:
            sh_headroom = _r2(37.0 - d_a)
            if sh_headroom is not None and sh_headroom < 5.0 and not is_financial:
                red_flags.append(f"Tight Shariah KMI Debt/Assets headroom ({d_a:.1f}% vs 37% ceiling).")

    return {
        "dcf_lab": dcf_lab,
        "dupont_roe": dupont or {},
        "piotroski_score": f_score,
        "piotroski_checklist": piotroski_rules,
        "bull_bear_digest": {
            "bull_points": bull_points[:6],
            "bear_points": bear_points[:6],
            "red_flags": red_flags[:5],
            "cash_conversion_ratio": cash_conv,
            "shariah_debt_headroom_pct": sh_headroom,
            "is_financial_sector": is_financial,
        },
    }
