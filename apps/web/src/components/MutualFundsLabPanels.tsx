"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Calculator, CheckCircle2, Layers, ShieldCheck, Trophy } from "lucide-react";
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
import { TableSkeleton } from "@/components/Skeletons";
import { apiFetchCached } from "@/lib/api";

export interface FundDirectoryLite {
  symbol: string;
  name: string;
  amc_name: string;
  fund_category: string;
  is_shariah_compliant: boolean;
  ter_pct: string;
  front_end_load_pct: string;
  return_1y_pct: string | null;
  annualized_yield_pct?: string | null;
  is_daily_dividend?: boolean;
}

interface AmcLeaderboardRow {
  amc_name: string;
  fund_count: number;
  shariah_count: number;
  daily_div_count: number;
  fmr_count: number;
  avg_return_1y_pct: number | null;
  best_return_1y_pct: number | null;
  best_fund_symbol: string;
  best_fund_name: string;
  avg_ter_pct: number;
  lowest_ter_pct: number;
  total_aum_mn: number;
}

interface CategoryChampion {
  bucket: string;
  total_funds: number;
  avg_ter_pct: number;
  leaders: Array<{
    symbol: string;
    name: string;
    amc_name: string;
    return_1y_pct: number;
    ter_pct: number;
    fel_pct: number;
    is_daily_dividend: boolean;
    has_fmr: boolean;
  }>;
}

interface LeaderboardPayload {
  amc_leaderboard: AmcLeaderboardRow[];
  category_champions: CategoryChampion[];
}

export function AmcLeaderboardPanel({ onPickAmc }: { onPickAmc?: (amcName: string) => void }) {
  const [data, setData] = useState<LeaderboardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [sortCol, setSortCol] = useState<"avg_return_1y_pct" | "avg_ter_pct" | "total_aum_mn" | "fund_count" | "fmr_count">(
    "avg_return_1y_pct",
  );

  useEffect(() => {
    apiFetchCached<LeaderboardPayload>("/api/v1/market/funds/leaderboard", 180_000, { onUpdate: setData })
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const sortedAmcs = useMemo(() => {
    const rows = [...(data?.amc_leaderboard || [])];
    rows.sort((a, b) => {
      if (sortCol === "avg_ter_pct") return (a.avg_ter_pct || 99) - (b.avg_ter_pct || 99);
      if (sortCol === "total_aum_mn") return (b.total_aum_mn || 0) - (a.total_aum_mn || 0);
      if (sortCol === "fund_count") return b.fund_count - a.fund_count;
      if (sortCol === "fmr_count") return b.fmr_count - a.fmr_count;
      return (b.avg_return_1y_pct ?? -999) - (a.avg_return_1y_pct ?? -999);
    });
    return rows;
  }, [data, sortCol]);

  if (loading && !data) {
    return <TableSkeleton rows={8} cols={7} />;
  }

  return (
    <div className="space-y-6">
      {/* Category Champions Grid */}
      <div className="space-y-3">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Trophy className="w-4 h-4 text-amber-400" />
            Category Champions — Top 3 Pakistani Mutual Funds by Investor Bucket
          </h2>
          <p className="text-xs okz-muted">
            Ranked across 330+ MUFAP &amp; VPS funds by 1-Year / Annualized Return (tie-broken by lowest TER).
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(data?.category_champions || []).map((cat) => (
            <div key={cat.bucket} className="okz-card p-4 space-y-2.5">
              <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-2">
                <div>
                  <div className="text-xs font-bold">{cat.bucket}</div>
                  <div className="text-[10px] okz-muted">
                    {cat.total_funds} funds &middot; Avg TER {cat.avg_ter_pct}%
                  </div>
                </div>
                {cat.bucket.startsWith("Shariah") && <ShieldCheck className="w-4 h-4 text-emerald-500 shrink-0" />}
              </div>
              <div className="space-y-2">
                {cat.leaders.map((f, idx) => (
                  <div key={f.symbol} className="flex items-start justify-between gap-2 text-xs">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`text-[10px] font-black w-4 text-center ${
                            idx === 0 ? "text-amber-400" : "okz-muted"
                          }`}
                        >
                          #{idx + 1}
                        </span>
                        <Link
                          href={`/funds/${encodeURIComponent(f.symbol)}`}
                          className="font-semibold hover:text-emerald-400 truncate"
                        >
                          {f.name}
                        </Link>
                      </div>
                      <div className="text-[10px] okz-muted pl-5 truncate">
                        {f.amc_name} &middot; TER {f.ter_pct}% &middot; FEL {f.fel_pct}%
                      </div>
                    </div>
                    <span className="font-mono font-bold text-emerald-400 shrink-0">
                      {f.return_1y_pct >= 0 ? "+" : ""}
                      {f.return_1y_pct.toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Full AMC Scorecard */}
      <div className="okz-card p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold">Pakistan AMC Institutional Scorecard ({sortedAmcs.length} AMCs)</h2>
            <p className="text-xs okz-muted">
              Compare Asset Management Companies on average 1Y performance, fee discipline (TER), SECP FMR transparency &amp; AUM.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="okz-muted">Sort by:</span>
            <select
              value={sortCol}
              onChange={(e) => setSortCol(e.target.value as typeof sortCol)}
              className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] font-semibold"
            >
              <option value="avg_return_1y_pct">Highest Avg 1Y Return</option>
              <option value="avg_ter_pct">Lowest Avg TER %</option>
              <option value="total_aum_mn">Largest Total AUM</option>
              <option value="fund_count">Most Funds</option>
              <option value="fmr_count">Most FMR Reports Parsed</option>
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--border)] okz-muted text-left">
              <tr>
                <th className="py-2.5 px-3">#</th>
                <th className="py-2.5 px-3">Asset Management Company (AMC)</th>
                <th className="py-2.5 px-3 text-right">Funds (Shariah / FMR)</th>
                <th className="py-2.5 px-3 text-right">Avg 1Y Return</th>
                <th className="py-2.5 px-3 text-right">Avg / Min TER</th>
                <th className="py-2.5 px-3">Top Performing Fund</th>
                <th className="py-2.5 px-3 text-right">Est. AUM (Mn)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {sortedAmcs.map((row, idx) => (
                <tr key={row.amc_name} className="hover:bg-[var(--card-muted)]/50">
                  <td className="py-2.5 px-3 font-mono okz-muted">{idx + 1}</td>
                  <td className="py-2.5 px-3">
                    {onPickAmc ? (
                      <button
                        type="button"
                        onClick={() => onPickAmc(row.amc_name)}
                        className="font-bold hover:text-emerald-400 text-left"
                      >
                        {row.amc_name}
                      </button>
                    ) : (
                      <span className="font-bold">{row.amc_name}</span>
                    )}
                    {row.daily_div_count > 0 && (
                      <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px] font-semibold">
                        {row.daily_div_count} Daily Div
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    <strong>{row.fund_count}</strong>{" "}
                    <span className="okz-muted">
                      ({row.shariah_count} / {row.fmr_count})
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono font-bold text-emerald-400">
                    {row.avg_return_1y_pct != null ? `${row.avg_return_1y_pct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    {row.avg_ter_pct.toFixed(2)}%{" "}
                    <span className="okz-muted">/ {row.lowest_ter_pct.toFixed(2)}%</span>
                  </td>
                  <td className="py-2.5 px-3">
                    <Link
                      href={`/funds/${encodeURIComponent(row.best_fund_symbol)}`}
                      className="text-sky-400 hover:underline font-semibold"
                    >
                      {row.best_fund_symbol}
                    </Link>{" "}
                    <span className="okz-muted">
                      ({row.best_return_1y_pct != null ? `${row.best_return_1y_pct.toFixed(1)}%` : "—"})
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    {row.total_aum_mn > 0 ? row.total_aum_mn.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const LINE_COLORS = ["#10b981", "#38bdf8", "#f59e0b", "#a855f7", "#f43f5e"];

export function AfterTaxSipComparatorPanel({
  allFunds,
  initialSymbols,
}: {
  allFunds: FundDirectoryLite[];
  initialSymbols?: string[];
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [lumpSum, setLumpSum] = useState<number>(500_000);
  const [monthlySip, setMonthlySip] = useState<number>(25_000);
  const [years, setYears] = useState<number>(5);
  const [isFiler, setIsFiler] = useState<boolean>(true);
  const [includeNss, setIncludeNss] = useState<boolean>(true);

  useEffect(() => {
    if (allFunds.length === 0) return;
    if (initialSymbols && initialSymbols.length >= 2) {
      setPicked(initialSymbols.slice(0, 4));
      return;
    }
    if (picked.length === 0) {
      const defaults = allFunds
        .filter((f) => Number(f.return_1y_pct || f.annualized_yield_pct || 0) > 8)
        .sort((a, b) => Number(b.return_1y_pct || b.annualized_yield_pct || 0) - Number(a.return_1y_pct || a.annualized_yield_pct || 0))
        .slice(0, 3)
        .map((f) => f.symbol);
      if (defaults.length > 0) setPicked(defaults);
    }
  }, [allFunds, initialSymbols, picked.length]);

  const whtRate = isFiler ? 0.15 : 0.30;

  const selectedFunds = useMemo(
    () => picked.map((s) => allFunds.find((f) => f.symbol === s)).filter(Boolean) as FundDirectoryLite[],
    [picked, allFunds],
  );

  const simulation = useMemo(() => {
    const safeYears = Math.max(1, Math.min(20, years || 5));
    const safeLump = Math.max(0, lumpSum || 0);
    const safeSip = Math.max(0, monthlySip || 0);
    const totalInvested = safeLump + safeSip * 12 * safeYears;

    type TrackCfg = {
      key: string;
      label: string;
      grossRatePct: number;
      terPct: number;
      felPct: number;
      taxExempt: boolean;
    };

    const tracks: TrackCfg[] = selectedFunds.map((f) => {
      const rawR = Number(f.return_1y_pct ?? f.annualized_yield_pct ?? 14);
      // Cap extreme 1-year equity spikes at a realistic forward expectation of 30% for multi-year projections
      const cappedR = Math.max(5, Math.min(30, Number.isFinite(rawR) ? rawR : 14));
      return {
        key: f.symbol,
        label: f.symbol,
        grossRatePct: cappedR,
        terPct: Math.max(0, Number(f.ter_pct || 0)),
        felPct: Math.max(0, Number(f.front_end_load_pct || 0)),
        taxExempt: false,
      };
    });

    if (includeNss) {
      tracks.push({
        key: "NSS_RIC",
        label: "CDNS RIC (11.76%)",
        grossRatePct: 11.76,
        terPct: 0,
        felPct: 0,
        taxExempt: false,
      });
    }

    const yearlyPoints: Array<Record<string, number | string>> = [];
    const summaries: Array<{
      key: string;
      label: string;
      grossRatePct: number;
      terPct: number;
      felPct: number;
      finalValue: number;
      netGain: number;
      feesPaid: number;
      taxPaid: number;
      netMultiple: number;
    }> = [];

    const state = tracks.map((t) => {
      const fel0 = safeLump * (t.felPct / 100);
      return {
        cfg: t,
        bal: safeLump - fel0,
        fees: fel0,
        tax: 0,
      };
    });

    const p0: Record<string, number | string> = { year: "Yr 0", Invested: Math.round(totalInvested > 0 ? safeLump : 0) };
    for (const st of state) p0[st.cfg.key] = Math.round(st.bal);
    yearlyPoints.push(p0);

    for (let yr = 1; yr <= safeYears; yr++) {
      const pt: Record<string, number | string> = {
        year: `Yr ${yr}`,
        Invested: Math.round(safeLump + safeSip * 12 * yr),
      };
      for (const st of state) {
        const monthlyGross = st.cfg.grossRatePct / 100 / 12;
        const monthlyTer = st.cfg.terPct / 100 / 12;
        const taxMult = st.cfg.taxExempt ? 0 : whtRate;
        for (let m = 0; m < 12; m++) {
          const felM = safeSip * (st.cfg.felPct / 100);
          st.fees += felM;
          st.bal += safeSip - felM;
          const grossProfit = st.bal * monthlyGross;
          const terDrag = st.bal * monthlyTer;
          st.fees += terDrag;
          const taxableGain = Math.max(0, grossProfit - terDrag);
          const taxM = taxableGain * taxMult;
          st.tax += taxM;
          st.bal += grossProfit - terDrag - taxM;
        }
        pt[st.cfg.key] = Math.round(st.bal);
      }
      yearlyPoints.push(pt);
    }

    for (const st of state) {
      summaries.push({
        key: st.cfg.key,
        label: st.cfg.label,
        grossRatePct: st.cfg.grossRatePct,
        terPct: st.cfg.terPct,
        felPct: st.cfg.felPct,
        finalValue: Math.round(st.bal),
        netGain: Math.round(st.bal - totalInvested),
        feesPaid: Math.round(st.fees),
        taxPaid: Math.round(st.tax),
        netMultiple: totalInvested > 0 ? Number((st.bal / totalInvested).toFixed(2)) : 0,
      });
    }
    summaries.sort((a, b) => b.finalValue - a.finalValue);
    return { tracks, yearlyPoints, summaries, totalInvested };
  }, [selectedFunds, lumpSum, monthlySip, years, whtRate, includeNss]);

  const addFund = (sym: string) => {
    if (!sym || picked.includes(sym) || picked.length >= 4) return;
    setPicked((prev) => [...prev, sym]);
  };

  return (
    <div className="space-y-6">
      <div className="okz-card p-5 space-y-4">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Calculator className="w-4 h-4 text-emerald-500" />
            After-Tax, FEL &amp; TER Mutual Fund SIP Head-to-Head Comparator
          </h2>
          <p className="text-xs okz-muted">
            Simulates real Pakistani investor outcomes after Front-End Load (FEL), annual TER fee drag, and FBR{" "}
            {isFiler ? "15% Active Filer" : "30% Non-Filer"} WHT (returns capped at 30% p.a. for realistic multi-year compounding).
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs items-end">
          <div>
            <label className="block okz-muted mb-1">Initial Lump Sum (PKR)</label>
            <input
              type="number"
              min={0}
              step={50000}
              value={lumpSum}
              onChange={(e) => setLumpSum(Number(e.target.value) || 0)}
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono font-bold"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">Monthly SIP (PKR)</label>
            <input
              type="number"
              min={0}
              step={5000}
              value={monthlySip}
              onChange={(e) => setMonthlySip(Number(e.target.value) || 0)}
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono font-bold"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">Horizon (Years): {years}Y</label>
            <input
              type="range"
              min={1}
              max={15}
              value={years}
              onChange={(e) => setYears(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
          </div>
          <div>
            <label className="block okz-muted mb-1">FBR Tax Status</label>
            <div className="grid grid-cols-2 gap-1">
              <button
                type="button"
                onClick={() => setIsFiler(true)}
                className={`py-2 rounded font-semibold border ${
                  isFiler ? "bg-emerald-600 text-white border-emerald-600" : "border-[var(--border)] okz-muted"
                }`}
              >
                Filer 15%
              </button>
              <button
                type="button"
                onClick={() => setIsFiler(false)}
                className={`py-2 rounded font-semibold border ${
                  !isFiler ? "bg-rose-600 text-white border-rose-600" : "border-[var(--border)] okz-muted"
                }`}
              >
                Non-Filer 30%
              </button>
            </div>
          </div>
          <div>
            <label className="block okz-muted mb-1">Add Fund (Max 4)</label>
            <select
              value=""
              onChange={(e) => addFund(e.target.value)}
              disabled={picked.length >= 4}
              className="w-full px-2.5 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)]"
            >
              <option value="">+ Select a mutual fund...</option>
              {allFunds
                .filter((f) => !picked.includes(f.symbol))
                .slice(0, 150)
                .map((f) => (
                  <option key={f.symbol} value={f.symbol}>
                    {f.symbol} — {f.name} ({f.ter_pct}% TER)
                  </option>
                ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div className="flex flex-wrap gap-1.5">
            {selectedFunds.map((f, i) => (
              <span
                key={f.symbol}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-[var(--border)] bg-[var(--bg-elevated)] text-xs font-semibold"
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: LINE_COLORS[i % LINE_COLORS.length] }} />
                <span>{f.symbol}</span>
                <span className="okz-muted font-normal">({f.ter_pct}% TER)</span>
                <button
                  type="button"
                  onClick={() => setPicked((prev) => prev.filter((s) => s !== f.symbol))}
                  className="text-rose-400 hover:text-rose-300 ml-0.5"
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
          <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer okz-muted">
            <input type="checkbox" checked={includeNss} onChange={(e) => setIncludeNss(e.target.checked)} />
            <span>Overlay CDNS Regular Income (11.76% benchmark)</span>
          </label>
        </div>
      </div>

      {simulation.yearlyPoints.length > 1 && (
        <div className="okz-card p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-bold">Projected Net-of-Fee &amp; After-Tax Wealth Growth (PKR)</h3>
            <span className="text-xs font-mono okz-muted">
              Total Capital Invested: <strong className="text-[var(--text)]">Rs {simulation.totalInvested.toLocaleString()}</strong>
            </span>
          </div>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={simulation.yearlyPoints} margin={{ top: 8, right: 16, left: 10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="year" tick={{ fontSize: 11, fill: "#94a3b8" }} />
                <YAxis
                  tick={{ fontSize: 10, fill: "#94a3b8" }}
                  tickFormatter={(v) => `Rs ${(Number(v) / 1_000_000).toFixed(1)}M`}
                  width={62}
                />
                <RechartsTooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    borderColor: "var(--border)",
                    fontSize: "12px",
                    borderRadius: "8px",
                  }}
                  formatter={(v) => [`Rs ${Number(v).toLocaleString()}`, ""]}
                />
                <Legend wrapperStyle={{ fontSize: "11px" }} />
                <Line type="monotone" dataKey="Invested" name="Capital Invested" stroke="#94a3b8" strokeDasharray="4 4" dot={false} />
                {simulation.tracks.map((t, i) => (
                  <Line
                    key={t.key}
                    type="monotone"
                    dataKey={t.key}
                    name={t.label}
                    stroke={LINE_COLORS[i % LINE_COLORS.length]}
                    strokeWidth={2.5}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="overflow-x-auto pt-2">
            <table className="w-full text-xs">
              <thead className="border-b border-[var(--border)] okz-muted text-left">
                <tr>
                  <th className="py-2 px-3">Fund / Benchmark</th>
                  <th className="py-2 px-3 text-right">Proj. Return %</th>
                  <th className="py-2 px-3 text-right">TER / FEL</th>
                  <th className="py-2 px-3 text-right">Est. Fees Paid</th>
                  <th className="py-2 px-3 text-right">Est. WHT Tax Paid</th>
                  <th className="py-2 px-3 text-right">Final Net Wealth</th>
                  <th className="py-2 px-3 text-right">Net Gain (Multiple)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {simulation.summaries.map((s) => (
                  <tr key={s.key} className="hover:bg-[var(--card-muted)]/50">
                    <td className="py-2 px-3 font-bold">{s.label}</td>
                    <td className="py-2 px-3 text-right font-mono">{s.grossRatePct.toFixed(2)}%</td>
                    <td className="py-2 px-3 text-right font-mono okz-muted">
                      {s.terPct.toFixed(2)}% / {s.felPct.toFixed(2)}%
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-amber-400">
                      Rs {s.feesPaid.toLocaleString()}
                    </td>
                    <td className="py-2 px-3 text-right font-mono text-rose-400">
                      Rs {s.taxPaid.toLocaleString()}
                    </td>
                    <td className="py-2 px-3 text-right font-mono font-black text-emerald-400">
                      Rs {s.finalValue.toLocaleString()}
                    </td>
                    <td className="py-2 px-3 text-right font-mono">
                      +Rs {s.netGain.toLocaleString()} ({s.netMultiple}x)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

interface OverlapPayload {
  selected_funds: Array<{
    symbol: string;
    name: string;
    amc_name: string;
    ter_pct: number;
    fel_pct: number;
    return_1y_pct: number | null;
    report_label: string;
    holdings_count: number;
  }>;
  pairwise_overlap: Array<{
    fund_a: string;
    fund_b: string;
    common_stocks_count: number;
    common_symbols: string[];
    weighted_overlap_pct: number;
    jaccard_overlap_pct: number;
    verdict: string;
  }>;
  shared_holdings: Array<{
    symbol: string;
    name: string;
    fund_count: number;
    avg_weight_pct: number;
    weights: Record<string, number>;
  }>;
  available_fmr_funds: Array<{
    symbol: string;
    name: string;
    amc_name: string;
    ter_pct: number;
    holdings_count: number;
    report_label: string;
  }>;
}

export function FmrHoldingsOverlapPanel() {
  const [selectedSyms, setSelectedSyms] = useState<string[]>([]);
  const [data, setData] = useState<OverlapPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const query = selectedSyms.length >= 2 ? `?symbols=${encodeURIComponent(selectedSyms.join(","))}` : "";
    setLoading(true);
    apiFetchCached<OverlapPayload>(`/api/v1/market/funds/overlap${query}`, 120_000, { onUpdate: setData })
      .then((res) => {
        setData(res);
        if (selectedSyms.length === 0 && res.selected_funds.length >= 2) {
          setSelectedSyms(res.selected_funds.map((f) => f.symbol));
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedSyms]);

  const toggleFund = (sym: string) => {
    setSelectedSyms((prev) => {
      if (prev.includes(sym)) {
        return prev.length > 2 ? prev.filter((s) => s !== sym) : prev;
      }
      return prev.length < 4 ? [...prev, sym] : [...prev.slice(1), sym];
    });
  };

  if (loading && !data) {
    return <TableSkeleton rows={6} cols={5} />;
  }

  return (
    <div className="space-y-6">
      <div className="okz-card p-5 space-y-3">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-1.5">
            <Layers className="w-4 h-4 text-emerald-500" />
            SECP FMR Top-10 Portfolio Overlap &amp; Redundant Fee Detector
          </h2>
          <p className="text-xs okz-muted">
            Select 2 to 4 mutual funds with parsed SECP Monthly Fund Manager Reports to uncover hidden duplicate PSX stock
            holdings — avoiding paying 3%+ TER twice for the exact same stocks.
          </p>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {(data?.available_fmr_funds || []).map((f) => {
            const active = selectedSyms.includes(f.symbol);
            return (
              <button
                key={f.symbol}
                type="button"
                onClick={() => toggleFund(f.symbol)}
                className={`px-2.5 py-1.5 rounded-lg border text-xs font-semibold transition ${
                  active
                    ? "bg-emerald-600 text-white border-emerald-600"
                    : "border-[var(--border)] bg-[var(--bg-elevated)] okz-muted hover:border-emerald-500/60"
                }`}
              >
                <span className="font-mono">{f.symbol}</span>{" "}
                <span className="opacity-80 font-normal">
                  ({f.ter_pct}% TER · {f.holdings_count} stocks)
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Pairwise Overlap Verdict Cards */}
      {(data?.pairwise_overlap || []).length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {data!.pairwise_overlap.map((pair) => {
            const high = pair.verdict === "HIGH_OVERLAP";
            const mod = pair.verdict === "MODERATE_OVERLAP";
            return (
              <div
                key={`${pair.fund_a}-${pair.fund_b}`}
                className={`okz-card p-4 space-y-2 ${
                  high
                    ? "border-rose-500/40 bg-rose-500/5"
                    : mod
                      ? "border-amber-500/40 bg-amber-500/5"
                      : "border-emerald-500/40 bg-emerald-500/5"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-bold text-sm">
                    {pair.fund_a} &harr; {pair.fund_b}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${
                      high
                        ? "bg-rose-500/20 text-rose-400"
                        : mod
                          ? "bg-amber-500/20 text-amber-400"
                          : "bg-emerald-500/20 text-emerald-400"
                    }`}
                  >
                    {high ? <AlertTriangle className="w-3 h-3" /> : <CheckCircle2 className="w-3 h-3" />}
                    {pair.verdict.replace("_", " ")}
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <div>
                    <div className="text-[10px] okz-muted">Weighted Top-10 Overlap</div>
                    <div className="text-2xl font-black font-mono">{pair.weighted_overlap_pct.toFixed(1)}%</div>
                  </div>
                  <div className="text-right text-xs okz-muted">
                    <div>
                      <strong className="text-[var(--text)]">{pair.common_stocks_count}</strong> shared stocks
                    </div>
                    <div>Jaccard: {pair.jaccard_overlap_pct}%</div>
                  </div>
                </div>
                {pair.common_symbols.length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {pair.common_symbols.map((sym) => (
                      <Link
                        key={sym}
                        href={`/stocks/${encodeURIComponent(sym)}`}
                        className="px-1.5 py-0.5 rounded bg-[var(--card)] border border-[var(--border)] font-mono text-[10px] font-bold hover:text-emerald-400"
                      >
                        {sym}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Shared Holdings Matrix */}
      {(data?.shared_holdings || []).length > 0 && (
        <div className="okz-card p-5 space-y-3">
          <h3 className="text-sm font-bold">Cross-Fund Top-10 Equity Holdings Matrix</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b border-[var(--border)] okz-muted text-left">
                <tr>
                  <th className="py-2 px-3">PSX Stock</th>
                  <th className="py-2 px-3 text-center">Held By</th>
                  {(data?.selected_funds || []).map((f) => (
                    <th key={f.symbol} className="py-2 px-3 text-right font-mono">
                      {f.symbol} (%)
                    </th>
                  ))}
                  <th className="py-2 px-3 text-right">Avg Weight</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {data!.shared_holdings.map((row) => (
                  <tr key={row.symbol} className="hover:bg-[var(--card-muted)]/50">
                    <td className="py-2 px-3">
                      <Link
                        href={`/stocks/${encodeURIComponent(row.symbol)}`}
                        className="font-mono font-bold text-emerald-400 hover:underline"
                      >
                        {row.symbol}
                      </Link>
                      {row.name !== row.symbol && <span className="ml-2 okz-muted">{row.name}</span>}
                    </td>
                    <td className="py-2 px-3 text-center">
                      <span
                        className={`px-2 py-0.5 rounded font-mono text-[10px] font-bold ${
                          row.fund_count >= 2 ? "bg-amber-500/20 text-amber-400" : "bg-[var(--card-muted)] okz-muted"
                        }`}
                      >
                        {row.fund_count} / {data!.selected_funds.length}
                      </span>
                    </td>
                    {data!.selected_funds.map((f) => {
                      const w = row.weights[f.symbol] || 0;
                      return (
                        <td
                          key={f.symbol}
                          className={`py-2 px-3 text-right font-mono ${w > 0 ? "font-semibold" : "okz-muted opacity-40"}`}
                        >
                          {w > 0 ? `${w.toFixed(2)}%` : "—"}
                        </td>
                      );
                    })}
                    <td className="py-2 px-3 text-right font-mono font-bold">{row.avg_weight_pct.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
