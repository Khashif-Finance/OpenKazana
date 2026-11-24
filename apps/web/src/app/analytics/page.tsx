"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Globe2, Landmark, Play, Plus, RefreshCw, Trash2, TrendingUp } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAppContext } from "@/components/CurrencyContext";
import { ChartSkeleton, KpiGridSkeleton } from "@/components/Skeletons";
import { TickerAutocomplete } from "@/components/TickerAutocomplete";
import { apiFetch, apiFetchCached, invalidateApiCache } from "@/lib/api";
import { formatMoney, formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface CpiBasketItem {
  code: string;
  group: string;
  weight_pct: number;
  index: number;
  yoy_pct: number;
  mom_pct: number | null;
  contribution_pct: number;
}

interface MacroItem {
  code: string;
  name: string;
  country: string;
  category: string;
  value: string;
  unit: string;
  as_of_date: string | null;
  source: string;
  history?: Array<Record<string, unknown>>;
  updated_at: string;
}

interface BacktestLeg {
  symbol: string;
  weight_pct: number;
}

interface BacktestResult {
  strategy: string;
  return_mode: string;
  real_return_mode?: string;
  display_currency?: string;
  benchmark_symbol: string;
  symbols_used: string[];
  equity_curve: Array<{
    date: string;
    strategy_value_pkr: number;
    benchmark_value_pkr: number;
    invested_capital_pkr: number;
    strategy_return_pct: number;
    benchmark_return_pct: number;
    drawdown_pct: number;
  }>;
  summary: {
    final_value_pkr: number;
    total_invested_pkr: number;
    display_currency?: string;
    real_return_mode?: string;
    total_return_pct: number;
    cagr_pct: number;
    benchmark_final_value_pkr: number;
    benchmark_return_pct: number;
    benchmark_cagr_pct: number;
    excess_return_pct: number;
    sharpe_ratio: number;
    sortino_ratio: number;
    max_drawdown_pct: number;
    annualized_volatility_pct: number;
    win_rate_pct: number;
    estimated_fees_pkr: number;
    trade_count: number;
    days_analyzed: number;
  };
  monte_carlo: Array<{
    month: number;
    p10_pkr: number;
    p25_pkr: number;
    p50_pkr: number;
    p75_pkr: number;
    p90_pkr: number;
  }>;
  stress_tests: Array<{
    scenario: string;
    rationale: string;
    estimated_impact_pkr: number;
    estimated_impact_pct: number;
    projected_value_pkr: number;
  }>;
}

const TENOR_ORDER = ["1M", "3M", "6M", "12M", "1Y", "2Y", "3Y", "5Y", "10Y"];

interface MacroRegimePoint {
  month: string;
  policy_rate_pct: number | null;
  cpi_yoy_pct: number | null;
  real_rate_pct: number | null;
  us_10y_pct: number | null;
  usd_pkr: number | null;
  gold_10g_pkr: number | null;
}

interface MacroRegimePayload {
  latest_policy_rate_pct: number | null;
  policy_rate_as_of_date?: string | null;
  latest_cpi_yoy_pct: number | null;
  latest_cpi_mom_pct?: number | null;
  cpi_as_of_date?: string | null;
  cpi_source?: string | null;
  cpi_frequency?: string | null;
  latest_real_rate_pct: number | null;
  sbp_fx_reserves_bn?: number | null;
  remittances_mn?: number | null;
  current_account_mn?: number | null;
  series: MacroRegimePoint[];
}

export default function QuantAndMacroAnalyticsPage() {
  const { currency, usdToPkr } = useAppContext();
  const [macro, setMacro] = useState<MacroItem[]>([]);
  const [regime, setRegime] = useState<MacroRegimePayload | null>(null);
  const [macroLoading, setMacroLoading] = useState(true);
  const [refreshingMacro, setRefreshingMacro] = useState(false);
  const [macroNotice, setMacroNotice] = useState<string | null>(null);

  const [legs, setLegs] = useState<BacktestLeg[]>([
    { symbol: "MEBL", weight_pct: 40 },
    { symbol: "LUCK", weight_pct: 30 },
    { symbol: "MCF", weight_pct: 30 },
  ]);
  const [newSym, setNewSym] = useState("");
  const [strategy, setStrategy] = useState<
    "BUY_AND_HOLD" | "MONTHLY_SIP_REBALANCE" | "RSI_MEAN_REVERSION" | "SMA_CROSSOVER"
  >("MONTHLY_SIP_REBALANCE");
  const [benchmark, setBenchmark] = useState("KSE100");
  const [initialCapital, setInitialCapital] = useState(1000000);
  const [monthlySip, setMonthlySip] = useState(25000);
  const [lookbackDays, setLookbackDays] = useState(365);
  const [returnMode, setReturnMode] = useState<"PRICE" | "GROSS_TR" | "NET_FILER" | "NET_NON_FILER">("NET_FILER");
  const [realReturnMode, setRealReturnMode] = useState<"NOMINAL_PKR" | "REAL_USD" | "REAL_CPI_PKR">("NOMINAL_PKR");
  const [shariahPurify, setShariahPurify] = useState(false);

  const [btResult, setBtResult] = useState<BacktestResult | null>(null);
  const [btLoading, setBtLoading] = useState(false);
  const [btError, setBtError] = useState<string | null>(null);

  const loadMacroData = useCallback((forceRefresh = false) => {
    setMacroLoading(true);
    Promise.all([
      apiFetchCached<MacroItem[]>("/api/v1/market/macro", 120_000, { forceRefresh, onUpdate: setMacro }),
      apiFetchCached<MacroRegimePayload>("/api/v1/market/macro/regime", 120_000, { forceRefresh, onUpdate: setRegime }),
    ])
      .then(([mRows, regData]) => {
        setMacro(mRows);
        setRegime(regData);
      })
      .catch(() => {})
      .finally(() => setMacroLoading(false));
  }, []);

  useEffect(() => {
    loadMacroData(false);
  }, [loadMacroData]);

  const handleRefreshMacro = async () => {
    setRefreshingMacro(true);
    setMacroNotice(null);
    try {
      const res = await apiFetch<{ ok: boolean; indicators_updated: number }>("/api/v1/market/macro/refresh", {
        method: "POST",
      });
      invalidateApiCache("/api/v1/market/macro");
      invalidateApiCache("/api/v1/market/overview");
      loadMacroData(true);
      setMacroNotice(`Synced ${res.indicators_updated} live PBS SDMX CPI & SBP EasyData indicators.`);
    } catch (err) {
      setMacroNotice(err instanceof Error ? err.message : "Failed to refresh SBP/PBS macro indicators.");
    } finally {
      setRefreshingMacro(false);
    }
  };

  const runBacktest = async (overrideLegs?: BacktestLeg[], overrideBench?: string) => {
    const activeLegs = (overrideLegs || legs).filter((l) => l.symbol.trim() && l.weight_pct > 0);
    if (activeLegs.length === 0) return;
    setBtLoading(true);
    setBtError(null);
    try {
      const res = await apiFetch<BacktestResult>("/api/v1/market/backtest", {
        method: "POST",
        body: JSON.stringify({
          legs: activeLegs,
          strategy,
          benchmark_symbol: overrideBench || benchmark,
          initial_capital_pkr: initialCapital,
          monthly_contribution_pkr: monthlySip,
          lookback_days: lookbackDays,
          return_mode: returnMode,
          real_return_mode: realReturnMode,
          apply_shariah_purification: shariahPurify,
          broker_bps: 15,
        }),
      });
      setBtResult(res);
    } catch (err) {
      setBtError(err instanceof Error ? err.message : "Backtest failed. Check symbols have daily bars.");
    } finally {
      setBtLoading(false);
    }
  };

  useEffect(() => {
    runBacktest().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const yieldCurveData = useMemo(() => {
    const mapTenor = (code: string, prefix: string): string | null => {
      if (!code.startsWith(prefix)) return null;
      return code.replace(prefix, "").replace("12M", "1Y");
    };
    const byTenor: Record<string, { tenor: string; kibor?: number; tbill?: number; pib?: number }> = {};
    for (const m of macro) {
      const val = Number(m.value);
      if (!Number.isFinite(val)) continue;
      const kt = mapTenor(m.code, "KIBOR_");
      const tt = mapTenor(m.code, "TBILL_");
      const pt = mapTenor(m.code, "PIB_");
      if (kt) {
        byTenor[kt] = { ...(byTenor[kt] || { tenor: kt }), kibor: val };
      } else if (tt) {
        byTenor[tt] = { ...(byTenor[tt] || { tenor: tt }), tbill: val };
      } else if (pt) {
        byTenor[pt] = { ...(byTenor[pt] || { tenor: pt }), pib: val };
      }
    }
    return TENOR_ORDER.filter((t) => byTenor[t]).map((t) => byTenor[t]);
  }, [macro]);

  const macroByCode = useMemo(() => {
    const map: Record<string, MacroItem> = {};
    for (const m of macro) map[m.code] = m;
    return map;
  }, [macro]);

  const cpiBasket = useMemo<CpiBasketItem[]>(() => {
    const basket = macroByCode["PK_CPI_BASKET"];
    if (!basket?.history || !Array.isArray(basket.history)) return [];
    return (basket.history as unknown as CpiBasketItem[])
      .filter((x) => x && typeof x.group === "string" && typeof x.yoy_pct === "number")
      .sort((a, b) => b.yoy_pct - a.yoy_pct);
  }, [macroByCode]);

  const highlightCodes = [
    "SBP_POLICY_RATE",
    "PK_CPI_YOY",
    "TBILL_6M",
    "KIBOR_6M",
    "SBP_FX_RESERVES_BN",
    "SBP_REMITTANCES_MN",
    "SBP_CURRENT_ACCOUNT_MN",
    "US_10Y_TREASURY",
  ];
  const keyCards = highlightCodes.map((c) => macroByCode[c]).filter(Boolean);
  const tableIndicators = macro.filter((m) => m.code !== "PK_CPI_BASKET");

  const addLeg = (explicitSym?: string) => {
    const sym = (explicitSym ?? newSym).trim().toUpperCase();
    if (!sym || legs.some((l) => l.symbol === sym)) return;
    setLegs((prev) => [...prev, { symbol: sym, weight_pct: 20 }]);
    setNewSym("");
  };

  const applyPreset = (preset: "SHARIAH_PK" | "GLOBAL_BARBELL" | "INCOME_MIX") => {
    if (preset === "SHARIAH_PK") {
      const next = [
        { symbol: "MEBL", weight_pct: 40 },
        { symbol: "ENGROH", weight_pct: 30 },
        { symbol: "MCF", weight_pct: 30 },
      ];
      setLegs(next);
      setBenchmark("KMI30");
      runBacktest(next, "KMI30");
    } else if (preset === "GLOBAL_BARBELL") {
      const next = [
        { symbol: "AAPL", weight_pct: 25 },
        { symbol: "MSFT", weight_pct: 25 },
        { symbol: "MEBL", weight_pct: 25 },
        { symbol: "GOLD_24K_TOLA", weight_pct: 25 },
      ];
      setLegs(next);
      setBenchmark("KSE100");
      runBacktest(next, "KSE100");
    } else {
      const next = [
        { symbol: "EFERT", weight_pct: 40 },
        { symbol: "OGDC", weight_pct: 30 },
        { symbol: "MRAF", weight_pct: 30 },
      ];
      setLegs(next);
      setBenchmark("KSE100");
      runBacktest(next, "KSE100");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp className="w-6 h-6 text-emerald-500" />
            Quant Strategy Backtester, SBP EasyData &amp; PBS Macro Lab
          </h1>
          <p className="text-sm okz-muted">
            Authoritative PBS Monthly CPI (12 COICOP groups) &amp; SBP EasyData Policy Rate, KIBOR, T-Bills, PIBs, FX
            Reserves, Remittances &amp; Current Account paired with a Multi-Asset Total-Return Strategy Backtester.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRefreshMacro}
          disabled={refreshingMacro}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shrink-0 self-start disabled:opacity-60"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshingMacro ? "animate-spin" : ""}`} />
          {refreshingMacro ? "Syncing SBP & PBS..." : "Refresh SBP / PBS Macro"}
        </button>
      </div>

      {macroNotice && (
        <div className="px-4 py-2.5 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
          {macroNotice}
        </div>
      )}

      {/* Macro KPIs */}
      {macroLoading && macro.length === 0 ? (
        <KpiGridSkeleton count={8} cols="grid-cols-2 sm:grid-cols-4" />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {(keyCards.length > 0 ? keyCards : tableIndicators.slice(0, 8)).map((m) => {
            const valNum = Number(m.value);
            const isExternalSigned = m.code === "SBP_CURRENT_ACCOUNT_MN";
            const momSub =
              m.code === "PK_CPI_YOY" && macroByCode["PK_CPI_MOM"]
                ? `MoM: ${Number(macroByCode["PK_CPI_MOM"].value) >= 0 ? "+" : ""}${formatNumber(macroByCode["PK_CPI_MOM"].value, 2)}%`
                : m.code === "SBP_FX_RESERVES_BN" && macroByCode["PK_TOTAL_FX_RESERVES_BN"]
                  ? `Total w/ Banks: $${formatNumber(macroByCode["PK_TOTAL_FX_RESERVES_BN"].value, 2)} Bn`
                  : null;
            return (
              <div key={m.code} className="okz-card p-3.5 space-y-1">
                <div className="flex items-center justify-between text-[11px] okz-muted">
                  <span className="truncate font-medium">{m.name}</span>
                  {m.country === "US" ? (
                    <Globe2 className="w-3.5 h-3.5 text-sky-400 shrink-0" />
                  ) : (
                    <Landmark className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                  )}
                </div>
                <div
                  className={`text-xl font-bold font-mono ${
                    isExternalSigned ? (valNum >= 0 ? "text-emerald-400" : "text-rose-400") : ""
                  }`}
                >
                  {isExternalSigned && valNum > 0 ? "+" : ""}
                  {formatNumber(m.value, m.unit.includes("Mn") ? 0 : 2)}
                  <span className="text-xs font-normal ml-1 okz-muted">{m.unit}</span>
                </div>
                {momSub && <div className="text-[11px] text-sky-400 font-medium">{momSub}</div>}
                <div className="text-[10px] okz-muted truncate">
                  {m.source} {m.as_of_date ? `• ${m.as_of_date}` : ""}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Yield Curve + Complete Rate Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {macroLoading && macro.length === 0 ? (
          <div className="lg:col-span-2">
            <ChartSkeleton heightClass="h-60" />
          </div>
        ) : (
          <div className="okz-card p-5 lg:col-span-2 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Pakistan KIBOR &amp; Sovereign PKRV Yield Curve (T-Bills &amp; PIBs)</h2>
              <span className="text-xs okz-muted">Source: State Bank of Pakistan</span>
            </div>
            {yieldCurveData.length === 0 ? (
              <div className="h-56 flex items-center justify-center text-xs okz-muted">
                No SBP yield curve data synced yet. Click &ldquo;Refresh SBP / PBS Macro&rdquo; above.
              </div>
            ) : (
              <div className="h-60 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={yieldCurveData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="tenor" tick={{ fontSize: 11, fill: "#94a3b8" }} />
                    <YAxis domain={["auto", "auto"]} unit="%" tick={{ fontSize: 11, fill: "#94a3b8" }} width={44} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "var(--card)",
                        borderColor: "var(--border)",
                        fontSize: "12px",
                        borderRadius: "8px",
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: "11px" }} />
                    <Line type="monotone" dataKey="kibor" name="KIBOR %" stroke="#f59e0b" strokeWidth={2.5} dot />
                    <Line type="monotone" dataKey="tbill" name="T-Bill %" stroke="#10b981" strokeWidth={2.5} dot />
                    <Line type="monotone" dataKey="pib" name="PIB Bond %" stroke="#38bdf8" strokeWidth={2.5} dot />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        )}

        <div className="okz-card p-5 space-y-2.5 overflow-y-auto max-h-80">
          <h2 className="text-sm font-semibold">All Synced SBP &amp; PBS Benchmarks ({tableIndicators.length})</h2>
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--border)] okz-muted text-left">
              <tr>
                <th className="py-1.5">Indicator</th>
                <th className="py-1.5 text-right">Value</th>
                <th className="py-1.5 text-right">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {tableIndicators.map((m) => (
                <tr key={m.code}>
                  <td className="py-1.5 pr-2">
                    <div className="font-medium">{m.name}</div>
                    <div className="text-[10px] okz-muted font-mono">{m.code}</div>
                  </td>
                  <td className="py-1.5 text-right font-mono font-bold text-emerald-500 whitespace-nowrap">
                    {formatNumber(m.value, m.unit.includes("Mn") ? 0 : 2)} {m.unit}
                  </td>
                  <td className="py-1.5 text-right okz-muted whitespace-nowrap">{m.as_of_date || "Live"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 5-Year Historical Pakistan Macro Regime Chart + PBS 12-Group Inflation Basket */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {regime && regime.series.length > 0 && (
          <div className={`okz-card p-5 space-y-3 ${cpiBasket.length > 0 ? "lg:col-span-7" : "lg:col-span-12"}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">
                  5-Year Macro Regime: SBP Policy Rate vs {regime.cpi_frequency === "MONTHLY" ? "Monthly PBS CPI YoY" : "CPI YoY"} &amp; USD/PKR
                </h2>
                <p className="text-xs okz-muted">
                  True real interest rate spread (SBP Policy Rate minus CPI) · Source: {regime.cpi_source || "PBS / SBP"}
                  {regime.cpi_as_of_date ? ` (as of ${regime.cpi_as_of_date.slice(0, 7)})` : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs">
                {regime.latest_policy_rate_pct != null && (
                  <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-bold">
                    Policy: {regime.latest_policy_rate_pct.toFixed(2)}%
                  </span>
                )}
                {regime.latest_cpi_yoy_pct != null && (
                  <span className="px-2 py-0.5 rounded bg-rose-500/15 text-rose-400 font-bold">
                    CPI: {regime.latest_cpi_yoy_pct.toFixed(2)}%
                  </span>
                )}
                {regime.latest_real_rate_pct != null && (
                  <span className="px-2 py-0.5 rounded bg-sky-500/15 text-sky-400 font-bold">
                    Real: {regime.latest_real_rate_pct > 0 ? "+" : ""}
                    {regime.latest_real_rate_pct.toFixed(2)}%
                  </span>
                )}
              </div>
            </div>
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={regime.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="month" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
                  <YAxis yAxisId="left" unit="%" tick={{ fontSize: 11, fill: "#94a3b8" }} width={44} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: "#94a3b8" }} width={48} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--card)",
                      borderColor: "var(--border)",
                      fontSize: "12px",
                      borderRadius: "8px",
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: "11px" }} />
                  <Line yAxisId="left" type="stepAfter" dataKey="policy_rate_pct" name="SBP Policy Rate %" stroke="#10b981" strokeWidth={2.5} dot={false} />
                  <Line yAxisId="left" type="monotone" dataKey="cpi_yoy_pct" name="PBS CPI YoY %" stroke="#f43f5e" strokeWidth={2.2} dot={false} />
                  <Line yAxisId="left" type="monotone" dataKey="real_rate_pct" name="Real Interest Rate %" stroke="#38bdf8" strokeWidth={1.8} strokeDasharray="4 4" dot={false} />
                  <Line yAxisId="right" type="monotone" dataKey="usd_pkr" name="USD / PKR" stroke="#f59e0b" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {cpiBasket.length > 0 && (
          <div className="okz-card p-5 space-y-3 lg:col-span-5">
            <div>
              <h2 className="text-sm font-semibold">PBS 12-Group National CPI Inflation Breakdown (YoY %)</h2>
              <p className="text-xs okz-muted">
                Official COICOP categories &amp; weights from Pakistan Bureau of Statistics ({macroByCode["PK_CPI_BASKET"]?.as_of_date?.slice(0, 7)})
              </p>
            </div>
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cpiBasket} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" unit="%" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                  <YAxis
                    type="category"
                    dataKey="group"
                    width={138}
                    tick={{ fontSize: 10, fill: "#94a3b8" }}
                    tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)}
                  />
                  <ReferenceLine x={0} stroke="var(--border)" />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const g = payload[0].payload as CpiBasketItem;
                      return (
                        <div className="p-2.5 rounded-lg border border-[var(--border)] bg-[var(--card)] text-xs shadow-lg space-y-0.5">
                          <div className="font-bold">{g.group}</div>
                          <div>
                            YoY Inflation:{" "}
                            <strong className={g.yoy_pct > 10 ? "text-rose-400" : g.yoy_pct < 0 ? "text-emerald-400" : "text-amber-400"}>
                              {g.yoy_pct > 0 ? "+" : ""}
                              {g.yoy_pct.toFixed(2)}%
                            </strong>
                          </div>
                          {g.mom_pct != null && (
                            <div>
                              MoM Change: <strong>{g.mom_pct > 0 ? "+" : ""}{g.mom_pct.toFixed(2)}%</strong>
                            </div>
                          )}
                          <div className="okz-muted">
                            CPI Weight: <strong>{g.weight_pct.toFixed(2)}%</strong> · Headline Contrib:{" "}
                            <strong>{g.contribution_pct >= 0 ? "+" : ""}{g.contribution_pct.toFixed(2)} pts</strong>
                          </div>
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="yoy_pct" name="YoY %" radius={[0, 4, 4, 0]}>
                    {cpiBasket.map((entry) => (
                      <Cell
                        key={entry.code}
                        fill={entry.yoy_pct >= 12 ? "#f43f5e" : entry.yoy_pct >= 6 ? "#f59e0b" : entry.yoy_pct >= 0 ? "#38bdf8" : "#10b981"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>

      {/* Multi-Asset Strategy Backtester */}
      <div className="okz-card p-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[var(--border)] pb-4">
          <div>
            <h2 className="text-base font-bold flex items-center gap-2">
              <Activity className="w-4 h-4 text-emerald-500" />
              Multi-Asset Total-Return Strategy Backtester (PK + Global + Daily Dividend)
            </h2>
            <p className="text-xs okz-muted">
              Simulates PSX, US Stocks, Gold, and Constant-NAV Daily Dividend funds with optional FBR WHT, Sec 236Z
              bonus drag, and Shariah purification.
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            <button
              type="button"
              onClick={() => applyPreset("SHARIAH_PK")}
              className="px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500"
            >
              PSX Shariah Core
            </button>
            <button
              type="button"
              onClick={() => applyPreset("GLOBAL_BARBELL")}
              className="px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500"
            >
              US Tech + PSX + Gold
            </button>
            <button
              type="button"
              onClick={() => applyPreset("INCOME_MIX")}
              className="px-2.5 py-1.5 rounded-md border border-[var(--border)] hover:border-emerald-500"
            >
              High-Dividend + MRAF
            </button>
          </div>
        </div>

        {/* Config Row */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 text-xs">
          {/* Legs */}
          <div className="lg:col-span-5 space-y-2">
            <div className="font-semibold okz-muted">Portfolio Allocation Legs</div>
            <div className="space-y-2">
              {legs.map((leg, i) => (
                <div key={leg.symbol} className="flex items-center gap-2">
                  <span className="font-mono font-bold w-28 truncate">{leg.symbol}</span>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={leg.weight_pct}
                    onChange={(e) => {
                      const val = Number(e.target.value);
                      setLegs((prev) => prev.map((x, idx) => (idx === i ? { ...x, weight_pct: val } : x)));
                    }}
                    className="w-20 px-2 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] font-mono text-right"
                  />
                  <span className="okz-muted">%</span>
                  <button
                    type="button"
                    onClick={() => setLegs((prev) => prev.filter((_, idx) => idx !== i))}
                    className="p-1 text-rose-400 hover:bg-rose-500/10 rounded"
                    aria-label="Remove leg"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <div className="flex items-center gap-2 pt-1">
                <TickerAutocomplete
                  value={newSym}
                  onChange={setNewSym}
                  onSelect={(item) => addLeg(item.symbol)}
                  clearOnSelect
                  placeholder="Type ticker or name (e.g. Meezan, OGDC, AAPL, MRAF)..."
                  className="flex-1"
                />
                <button
                  type="button"
                  onClick={() => addLeg()}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-[var(--border)] hover:border-emerald-500 font-semibold shrink-0"
                >
                  <Plus className="w-3.5 h-3.5" /> Add
                </button>
              </div>
            </div>
          </div>

          {/* Strategy & Tax Controls */}
          <div className="lg:col-span-7 grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div>
              <label className="block okz-muted mb-1">Strategy Engine</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value as typeof strategy)}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="BUY_AND_HOLD">Buy &amp; Hold</option>
                <option value="MONTHLY_SIP_REBALANCE">Monthly SIP &amp; Rebalance</option>
                <option value="SMA_CROSSOVER">SMA 20/50 Trend Follow</option>
                <option value="RSI_MEAN_REVERSION">RSI(14) Mean Reversion</option>
              </select>
            </div>
            <div>
              <label className="block okz-muted mb-1">Return &amp; Tax Mode</label>
              <select
                value={returnMode}
                onChange={(e) => setReturnMode(e.target.value as typeof returnMode)}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="NET_FILER">Net Total Return (Filer 15% WHT)</option>
                <option value="NET_NON_FILER">Net Total Return (Non-Filer 30%)</option>
                <option value="GROSS_TR">Gross Total Return</option>
                <option value="PRICE">Raw Price Return Only</option>
              </select>
            </div>
            <div>
              <label className="block okz-muted mb-1">Benchmark</label>
              <select
                value={benchmark}
                onChange={(e) => setBenchmark(e.target.value)}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="KSE100">KSE100</option>
                <option value="KMI30">KMI30 (Islamic)</option>
                <option value="^GSPC">S&amp;P 500 (^GSPC)</option>
              </select>
            </div>
            <div>
              <label className="block okz-muted mb-1">Initial Capital (PKR)</label>
              <input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(Number(e.target.value))}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)] font-mono"
              />
            </div>
            <div>
              <label className="block okz-muted mb-1">Monthly SIP (PKR)</label>
              <input
                type="number"
                value={monthlySip}
                onChange={(e) => setMonthlySip(Number(e.target.value))}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)] font-mono"
              />
            </div>
            <div>
              <label className="block okz-muted mb-1">Lookback Horizon</label>
              <select
                value={lookbackDays}
                onChange={(e) => setLookbackDays(Number(e.target.value))}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value={180}>6 Months</option>
                <option value={365}>1 Year</option>
                <option value={730}>2 Years</option>
                <option value={1095}>3 Years</option>
                <option value={1825}>5 Years</option>
              </select>
            </div>
            <div>
              <label className="block okz-muted mb-1">Purchasing Power Basis</label>
              <select
                value={realReturnMode}
                onChange={(e) => setRealReturnMode(e.target.value as typeof realReturnMode)}
                className="w-full px-2.5 py-2 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <option value="NOMINAL_PKR">Nominal PKR</option>
                <option value="REAL_USD">Real USD (FX-Adjusted)</option>
                <option value="REAL_CPI_PKR">Real PKR (CPI-Deflated)</option>
              </select>
            </div>
            <div className="col-span-2 sm:col-span-3 flex flex-wrap items-center justify-between gap-3 pt-1">
              <label className="inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={shariahPurify}
                  onChange={(e) => setShariahPurify(e.target.checked)}
                />
                <span>Deduct Shariah Dividend Purification Drag</span>
              </label>
              <button
                type="button"
                onClick={() => runBacktest()}
                disabled={btLoading}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold disabled:opacity-60"
              >
                <Play className="w-3.5 h-3.5" />
                {btLoading ? "Running Simulation..." : "Run Backtest & Monte Carlo"}
              </button>
            </div>
          </div>
        </div>

        {btError && (
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-xs text-rose-400 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{btError}</span>
          </div>
        )}

        {btResult && (
          <div className="space-y-6 pt-2">
            {/* Backtest Summary KPIs */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">
                  Ending Value ({btResult.display_currency === "USD" ? "Real USD" : btResult.real_return_mode === "REAL_CPI_PKR" ? "Real PKR" : "Nominal"})
                </div>
                <div className="text-base font-bold mt-0.5 text-emerald-500">
                  {btResult.display_currency === "USD"
                    ? `$${formatNumber(btResult.summary.final_value_pkr, 0)}`
                    : formatMoney(btResult.summary.final_value_pkr, currency, usdToPkr, true)}
                </div>
                <div className="text-[10px] okz-muted">
                  Invested:{" "}
                  {btResult.display_currency === "USD"
                    ? `$${formatNumber(btResult.summary.total_invested_pkr, 0)}`
                    : formatMoney(btResult.summary.total_invested_pkr, currency, usdToPkr, true)}
                </div>
              </div>
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">CAGR / Total Return</div>
                <div className={`text-base font-bold mt-0.5 ${pctColorClass(btResult.summary.cagr_pct)}`}>
                  {formatPct(btResult.summary.cagr_pct)}
                </div>
                <div className="text-[10px] okz-muted">Total: {formatPct(btResult.summary.total_return_pct)}</div>
              </div>
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">vs {btResult.benchmark_symbol} Excess</div>
                <div className={`text-base font-bold mt-0.5 ${pctColorClass(btResult.summary.excess_return_pct)}`}>
                  {formatPct(btResult.summary.excess_return_pct)}
                </div>
                <div className="text-[10px] okz-muted">
                  Bench CAGR: {formatPct(btResult.summary.benchmark_cagr_pct)}
                </div>
              </div>
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">Sharpe / Sortino</div>
                <div className="text-base font-bold mt-0.5">
                  {btResult.summary.sharpe_ratio.toFixed(2)} / {btResult.summary.sortino_ratio.toFixed(2)}
                </div>
                <div className="text-[10px] okz-muted">
                  Vol: {btResult.summary.annualized_volatility_pct.toFixed(1)}%
                </div>
              </div>
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">Max Drawdown</div>
                <div className="text-base font-bold mt-0.5 text-rose-400">
                  {btResult.summary.max_drawdown_pct.toFixed(2)}%
                </div>
                <div className="text-[10px] okz-muted">Win Days: {btResult.summary.win_rate_pct.toFixed(1)}%</div>
              </div>
              <div className="p-3 rounded-lg bg-[var(--card-muted)]">
                <div className="okz-muted">Est. Brokerage &amp; Taxes</div>
                <div className="text-base font-bold mt-0.5">
                  {formatMoney(btResult.summary.estimated_fees_pkr, currency, usdToPkr, true)}
                </div>
                <div className="text-[10px] okz-muted">
                  {btResult.summary.trade_count} trades &bull; {btResult.summary.days_analyzed}d
                </div>
              </div>
            </div>

            {/* Equity Curve + Monte Carlo Fan */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                  Cumulative Strategy Equity vs {btResult.benchmark_symbol} ({btResult.return_mode})
                </h3>
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={btResult.equity_curve} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
                      <YAxis
                        tickFormatter={(v) => `${(Number(v) / 100000).toFixed(1)}L`}
                        tick={{ fontSize: 10, fill: "#94a3b8" }}
                        width={46}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "var(--card)",
                          borderColor: "var(--border)",
                          fontSize: "11px",
                          borderRadius: "8px",
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: "11px" }} />
                      <Area
                        type="monotone"
                        dataKey="strategy_value_pkr"
                        name="Strategy Value (PKR)"
                        stroke="#10b981"
                        fill="rgba(16, 185, 129, 0.2)"
                        strokeWidth={2}
                      />
                      <Line
                        type="monotone"
                        dataKey="benchmark_value_pkr"
                        name={`${btResult.benchmark_symbol} Benchmark`}
                        stroke="#38bdf8"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="invested_capital_pkr"
                        name="Invested Principal"
                        stroke="#94a3b8"
                        strokeDasharray="4 4"
                        dot={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                  3-Year Forward Monte Carlo Fan (500 GBM Paths: P10 / P50 / P90)
                </h3>
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={btResult.monte_carlo} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="month" tickFormatter={(m) => `M${m}`} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                      <YAxis
                        tickFormatter={(v) => `${(Number(v) / 100000).toFixed(1)}L`}
                        tick={{ fontSize: 10, fill: "#94a3b8" }}
                        width={46}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "var(--card)",
                          borderColor: "var(--border)",
                          fontSize: "11px",
                          borderRadius: "8px",
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: "11px" }} />
                      <Area
                        type="monotone"
                        dataKey="p90_pkr"
                        name="P90 Bull Case"
                        stroke="#38bdf8"
                        fill="rgba(56, 189, 248, 0.14)"
                      />
                      <Area
                        type="monotone"
                        dataKey="p50_pkr"
                        name="P50 Median"
                        stroke="#10b981"
                        fill="rgba(16, 185, 129, 0.22)"
                        strokeWidth={2}
                      />
                      <Line
                        type="monotone"
                        dataKey="p10_pkr"
                        name="P10 Bear Case"
                        stroke="#f43f5e"
                        strokeWidth={2}
                        dot={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            {/* Stress Tests Table */}
            <div className="space-y-2">
              <h3 className="text-xs font-bold uppercase tracking-wider okz-muted">
                Pakistan &amp; Global Macro Stress Test Matrix
              </h3>
              <div className="overflow-x-auto border border-[var(--border)] rounded-lg">
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted text-left">
                    <tr>
                      <th className="py-2.5 px-3">Macro Shock Scenario</th>
                      <th className="py-2.5 px-3">Transmission Mechanism</th>
                      <th className="py-2.5 px-3 text-right">Impact %</th>
                      <th className="py-2.5 px-3 text-right">Estimated PnL</th>
                      <th className="py-2.5 px-3 text-right">Projected Value</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {btResult.stress_tests.map((st) => (
                      <tr key={st.scenario}>
                        <td className="py-2.5 px-3 font-semibold">{st.scenario}</td>
                        <td className="py-2.5 px-3 okz-muted">{st.rationale}</td>
                        <td className={`py-2.5 px-3 text-right font-bold ${pctColorClass(st.estimated_impact_pct)}`}>
                          {formatPct(st.estimated_impact_pct)}
                        </td>
                        <td className={`py-2.5 px-3 text-right font-mono ${pctColorClass(st.estimated_impact_pkr)}`}>
                          {st.estimated_impact_pkr >= 0 ? "+" : ""}
                          {formatMoney(st.estimated_impact_pkr, currency, usdToPkr, true)}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono font-semibold">
                          {formatMoney(st.projected_value_pkr, currency, usdToPkr, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
