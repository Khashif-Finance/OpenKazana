"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Award, GitCompareArrows, Plus, ShieldCheck, X } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiFetch } from "@/lib/api";
import { formatNumber, formatPct, pctColorClass } from "@/lib/format";

interface CompareStockRow {
  symbol: string;
  name: string;
  sector: string;
  currency: string;
  last_price: number;
  change_pct: number;
  market_cap_mn: number;
  pe_ratio: number | null;
  pb_ratio: number | null;
  eps: number | null;
  roe_pct: number | null;
  net_margin_pct: number | null;
  operating_margin_pct: number | null;
  dividend_yield_pct: number | null;
  div_streak_years: number;
  revenue_cagr_3y_pct: number | null;
  net_profit_cagr_3y_pct: number | null;
  eps_cagr_3y_pct: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  piotroski_f_score: number | null;
  altman_z_score: number | null;
  dcf_intrinsic_value: number | null;
  return_1m_pct: number | null;
  return_3m_pct: number | null;
  return_6m_pct: number | null;
  return_1y_pct: number | null;
  volatility_pct: number | null;
  max_drawdown_pct: number | null;
  technical_signal: string | null;
  is_shariah_compliant: boolean;
  purification_rate_pct: number | null;
}

interface ComparePayload {
  symbols: string[];
  days: number;
  stocks: CompareStockRow[];
  indexed_series: Array<Record<string, string | number | null>>;
  leaders: Record<string, string | null>;
}

const LINE_COLORS = ["#10b981", "#38bdf8", "#f59e0b", "#a855f7", "#f43f5e"];

const PRESETS: Array<{ label: string; symbols: string[] }> = [
  { label: "Big 4 Banks", symbols: ["MEBL", "UBL", "MCB", "HBL"] },
  { label: "Cement Leaders", symbols: ["LUCK", "MLCF", "FCCL", "CHCC"] },
  { label: "E&P Oil & Gas", symbols: ["OGDC", "PPL", "MARI", "POL"] },
  { label: "Fertilizer", symbols: ["FFC", "EFERT", "FATIMA"] },
  { label: "Tech & Export", symbols: ["SYS", "AIRLINK", "ILP", "TRG"] },
];

export default function CompareStocksPage() {
  const [symbols, setSymbols] = useState<string[]>(["MEBL", "UBL", "MCB", "HBL"]);
  const [inputSym, setInputSym] = useState("");
  const [days, setDays] = useState<number>(252);
  const [data, setData] = useState<ComparePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const raw = sp.get("symbols");
      if (raw) {
        const parsed = raw
          .split(",")
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean)
          .slice(0, 5);
        if (parsed.length === 1) {
          const peer = parsed[0] === "MEBL" ? "UBL" : "MEBL";
          setSymbols([parsed[0], peer]);
        } else if (parsed.length >= 2) {
          setSymbols(parsed);
        }
      }
    }
  }, []);

  useEffect(() => {
    if (symbols.length < 2) return;
    setLoading(true);
    setError(null);
    apiFetch<ComparePayload>(`/api/v1/market/compare?symbols=${encodeURIComponent(symbols.join(","))}&days=${days}`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Comparison failed"))
      .finally(() => setLoading(false));
  }, [symbols, days]);

  const addSymbol = (e: React.FormEvent) => {
    e.preventDefault();
    const s = inputSym.trim().toUpperCase();
    if (!s || symbols.includes(s) || symbols.length >= 5) return;
    setSymbols((prev) => [...prev, s]);
    setInputSym("");
  };

  const removeSymbol = (sym: string) => {
    if (symbols.length <= 2) return;
    setSymbols((prev) => prev.filter((x) => x !== sym));
  };

  const metrics: Array<{
    label: string;
    leaderKey?: string;
    render: (s: CompareStockRow) => string;
    colorFn?: (s: CompareStockRow) => string;
  }> = [
    { label: "Last Price", render: (s) => `${s.currency} ${formatNumber(s.last_price, 2)}` },
    { label: "1D Change", render: (s) => formatPct(s.change_pct), colorFn: (s) => pctColorClass(s.change_pct) },
    { label: "Market Cap (Mn)", render: (s) => formatNumber(s.market_cap_mn, 0) },
    { label: "1M Return", render: (s) => (s.return_1m_pct != null ? formatPct(s.return_1m_pct) : "—"), colorFn: (s) => pctColorClass(s.return_1m_pct) },
    { label: "3M Return", render: (s) => (s.return_3m_pct != null ? formatPct(s.return_3m_pct) : "—"), colorFn: (s) => pctColorClass(s.return_3m_pct) },
    { label: "1Y Return", leaderKey: "highest_return_1y", render: (s) => (s.return_1y_pct != null ? formatPct(s.return_1y_pct) : "—"), colorFn: (s) => pctColorClass(s.return_1y_pct) },
    { label: "Annualized Volatility", leaderKey: "lowest_volatility", render: (s) => (s.volatility_pct != null ? `${s.volatility_pct.toFixed(1)}%` : "—") },
    { label: "Max Drawdown", render: (s) => (s.max_drawdown_pct != null ? `${s.max_drawdown_pct.toFixed(1)}%` : "—"), colorFn: () => "text-rose-400" },
    { label: "P/E Ratio", leaderKey: "lowest_pe", render: (s) => (s.pe_ratio != null ? `${s.pe_ratio.toFixed(2)}x` : "—") },
    { label: "P/B Ratio", leaderKey: "lowest_pb", render: (s) => (s.pb_ratio != null ? `${s.pb_ratio.toFixed(2)}x` : "—") },
    { label: "EPS (TTM)", render: (s) => (s.eps != null ? s.eps.toFixed(2) : "—") },
    { label: "ROE %", leaderKey: "highest_roe", render: (s) => (s.roe_pct != null ? `${s.roe_pct.toFixed(1)}%` : "—") },
    { label: "Net Margin %", render: (s) => (s.net_margin_pct != null ? `${s.net_margin_pct.toFixed(1)}%` : "—") },
    { label: "3Y Revenue CAGR", render: (s) => (s.revenue_cagr_3y_pct != null ? formatPct(s.revenue_cagr_3y_pct) : "—"), colorFn: (s) => pctColorClass(s.revenue_cagr_3y_pct) },
    { label: "3Y Net Profit CAGR", leaderKey: "highest_pat_cagr", render: (s) => (s.net_profit_cagr_3y_pct != null ? formatPct(s.net_profit_cagr_3y_pct) : "—"), colorFn: (s) => pctColorClass(s.net_profit_cagr_3y_pct) },
    { label: "Dividend Yield %", leaderKey: "highest_div_yield", render: (s) => (s.dividend_yield_pct ? `${s.dividend_yield_pct.toFixed(2)}%` : "—"), colorFn: () => "text-emerald-400" },
    { label: "Dividend Streak", render: (s) => (s.div_streak_years > 0 ? `${s.div_streak_years} Yrs` : "—") },
    { label: "Piotroski F-Score", leaderKey: "highest_f_score", render: (s) => (s.piotroski_f_score != null ? `${s.piotroski_f_score} / 9` : "—") },
    { label: "Altman Z-Score", render: (s) => (s.altman_z_score != null ? s.altman_z_score.toFixed(2) : "—") },
    { label: "Debt / Equity", render: (s) => (s.debt_to_equity != null ? `${s.debt_to_equity.toFixed(2)}x` : "—") },
    { label: "Technical Gauge", render: (s) => s.technical_signal || "—" },
    { label: "Shariah Compliant", render: (s) => (s.is_shariah_compliant ? "Yes (KMI)" : "No") },
  ];

  return (
    <div className="space-y-6">
      <div className="okz-card p-6 bg-gradient-to-br from-sky-500/10 via-transparent to-emerald-500/10 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              <GitCompareArrows className="w-6 h-6 text-sky-400" />
              Side-by-Side Stock Comparison Matrix
            </h1>
            <p className="text-sm okz-muted">
              Compare 2 to 5 PSX or Global stocks head-to-head on indexed total performance, valuation multiples, 3-year growth CAGRs, Piotroski F-Score &amp; Shariah compliance.
            </p>
          </div>
          <div className="flex items-center gap-1 text-xs">
            {[
              [90, "3M"],
              [180, "6M"],
              [252, "1Y"],
              [500, "2Y"],
            ].map(([dVal, lbl]) => (
              <button
                key={lbl}
                type="button"
                onClick={() => setDays(Number(dVal))}
                className={`px-2.5 py-1 rounded-md font-semibold ${
                  days === dVal ? "bg-emerald-600 text-white" : "bg-[var(--card)] border border-[var(--border)] okz-muted"
                }`}
              >
                {lbl}
              </button>
            ))}
          </div>
        </div>

        {/* Selected Ticker Pills + Add Form + Quick Presets */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {symbols.map((sym, idx) => (
            <span
              key={sym}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--card)] border border-[var(--border)] font-mono font-bold"
            >
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: LINE_COLORS[idx % LINE_COLORS.length] }} />
              {sym}
              {symbols.length > 2 && (
                <button type="button" onClick={() => removeSymbol(sym)} className="okz-muted hover:text-rose-400">
                  <X className="w-3 h-3" />
                </button>
              )}
            </span>
          ))}
          {symbols.length < 5 && (
            <form onSubmit={addSymbol} className="inline-flex items-center gap-1">
              <input
                value={inputSym}
                onChange={(e) => setInputSym(e.target.value.toUpperCase())}
                placeholder="Add ticker..."
                className="w-28 px-2.5 py-1 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono uppercase text-xs"
              />
              <button
                type="submit"
                className="px-2.5 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-semibold inline-flex items-center gap-1"
              >
                <Plus className="w-3 h-3" /> Add
              </button>
            </form>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="okz-muted mr-1">PSX Presets:</span>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => setSymbols(p.symbols)}
              className="px-2.5 py-1 rounded-md bg-[var(--card)] border border-[var(--border)] hover:border-emerald-500 font-medium"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="okz-card p-4 text-xs text-rose-400 border-rose-500/40 bg-rose-500/5">{error}</div>
      )}

      {loading && !data ? (
        <div className="okz-card p-8 text-center text-sm okz-muted">Computing Pandas multi-stock comparison...</div>
      ) : data && (
        <>
          {/* Normalized Base=100 Overlay Chart */}
          {data.indexed_series.length >= 2 && (
            <div className="okz-card p-5 space-y-3">
              <div>
                <h2 className="text-sm font-semibold">Normalized Price Performance (Indexed Base = 100)</h2>
                <p className="text-xs okz-muted">
                  Shows relative cumulative growth of Rs 100 invested at the start of the selected window across all compared tickers.
                </p>
              </div>
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.indexed_series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={30} />
                    <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} width={46} />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: "var(--card)",
                        borderColor: "var(--border)",
                        fontSize: "11px",
                        borderRadius: "8px",
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: "11px" }} />
                    {data.symbols.map((sym, idx) => (
                      <Line
                        key={sym}
                        type="monotone"
                        dataKey={sym}
                        stroke={LINE_COLORS[idx % LINE_COLORS.length]}
                        strokeWidth={2.2}
                        dot={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Side-by-Side Comparison Matrix */}
          <div className="okz-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-[var(--bg-elevated)] border-b border-[var(--border)]">
                  <tr>
                    <th className="py-3 px-4 text-left font-semibold okz-muted w-48">Metric</th>
                    {data.stocks.map((st, idx) => (
                      <th key={st.symbol} className="py-3 px-4 text-right min-w-36">
                        <Link
                          href={`/stocks/${encodeURIComponent(st.symbol)}`}
                          className="font-mono font-bold text-sm hover:underline inline-flex items-center gap-1.5"
                          style={{ color: LINE_COLORS[idx % LINE_COLORS.length] }}
                        >
                          {st.symbol}
                          {st.is_shariah_compliant && <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />}
                        </Link>
                        <div className="text-[10px] font-normal okz-muted truncate max-w-36 ml-auto">{st.name}</div>
                        <div className="text-[10px] font-normal okz-muted">{st.sector}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border)]">
                  {metrics.map((m) => {
                    const bestSym = m.leaderKey ? data.leaders[m.leaderKey] : null;
                    return (
                      <tr key={m.label} className="hover:bg-[var(--card-muted)]/35">
                        <td className="py-2.5 px-4 font-medium okz-muted">{m.label}</td>
                        {data.stocks.map((st) => {
                          const isLeader = bestSym === st.symbol;
                          return (
                            <td
                              key={st.symbol}
                              className={`py-2.5 px-4 text-right font-mono ${isLeader ? "bg-emerald-500/10 font-bold" : ""} ${
                                m.colorFn ? m.colorFn(st) : ""
                              }`}
                            >
                              <span className="inline-flex items-center gap-1 justify-end">
                                {isLeader && <Award className="w-3 h-3 text-emerald-400 shrink-0" />}
                                {m.render(st)}
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
